"""import_historical_bookings.py - one-off backfill of pre-web-app bookings.

The app only started capturing bookings in May 2025. Everything before that
lives on the 'archive' worksheet of the two Google Form response spreadsheets.
This reads both and appends them to the app's archive so the statistics pages
have real history.

Rows arriving on or after the archive's first real entry are skipped: those
were pulled into the app before being moved to the spreadsheet's archive tab,
so importing them would double-count.

Safe to re-run - every row carries a deterministic original_sheet_md5 and rows
already present are skipped.

Usage:
    python scripts/import_historical_bookings.py                  # dry run
    python scripts/import_historical_bookings.py --write
    python scripts/import_historical_bookings.py --write --archive-path X
"""

import argparse
import hashlib
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from config import (  # noqa: E402
    ARCHIVE_FILE_PATH,
    DATA_FILE_PATH,
    FIELD_MAPPINGS_DICT,
    UK_TZ,
)
from models.json_utils import load_json, save_json  # noqa: E402
from models.schemas import ArchiveData, BookingData, LiveData  # noqa: E402
from models.utils import estimate_cost, get_event_type, sort_facilities  # noqa: E402

CDS = "Chelmsford District Scouts"
OSG = "Other Scout Group"
SCH = "School or Other Youth Organisation"

# Arrivals from here on are already in the app - see module docstring
OVERLAP_FROM = datetime(2025, 5, 15)

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Excel counts days from this epoch (the 1900 leap-year bug is baked in)
EXCEL_EPOCH = datetime(1899, 12, 30)

# Facilities were numbered differently over the years ("4) Squirrels" became
# "5) Squirrels"), so the number is stripped and only the name is matched.
FACILITY_PREFIX = re.compile(r"^\s*\w+\)\s*")


# --------------------------------------------------------------------------
# Minimal .xlsx reader
#
# openpyxl and pandas are not installed, and adding either to requirements.txt
# would ship a dependency into the production image for a throwaway job. An
# .xlsx is a zip of XML, and all this needs is cell values as strings.
# --------------------------------------------------------------------------
def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """The workbook's shared string table, which most text cells index into."""
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{NS}t")) for si in root.findall(f"{NS}si")]


def _cell_text(node, shared: list[str]):
    """Value of one <c> element, resolving shared and inline strings."""
    kind = node.get("t")
    value = node.find(f"{NS}v")
    if kind == "s" and value is not None:
        return shared[int(value.text)]
    if kind == "inlineStr":
        return "".join(t.text or "" for t in node.iter(f"{NS}t"))
    return value.text if value is not None else None


def read_sheet(path: Path, sheet_name: str) -> list[dict]:
    """Return the named worksheet as a list of {column letter: value} dicts."""
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [s.get("name") for s in workbook.iter(f"{NS}sheet")]
        if sheet_name not in names:
            raise ValueError(f"{path.name} has no '{sheet_name}' sheet (found {names})")
        target = f"xl/worksheets/sheet{names.index(sheet_name) + 1}.xml"

        rows = []
        for row in ET.fromstring(archive.read(target)).iter(f"{NS}row"):
            rows.append(
                {
                    re.match(r"[A-Z]+", node.get("r")).group(): _cell_text(node, shared)
                    for node in row.findall(f"{NS}c")
                }
            )
    return rows


def cell(row: dict, col: str) -> str:
    """Trimmed string value of a cell, empty string when missing."""
    return (row.get(col) or "").strip()


def to_datetime(serial: str):
    """Excel serial -> naive datetime, or None when it is not a number."""
    try:
        return EXCEL_EPOCH + timedelta(days=float(serial))
    except (TypeError, ValueError):
        return None


def to_int(value: str):
    """'20.0' -> 20, or None when it is not a number."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Group type
# --------------------------------------------------------------------------
# Ordered, first match wins. Order matters: the Boys' Brigade and cadet units
# use the same "2nd Chelmsford" ordinal naming as Scout groups, so they have to
# be claimed before the ordinal rule below.
NAME_RULES = [
    (SCH, "youth org", r"boys' ?brigade|\bbb\b|\bacf\b|\batc\b|cadet|woodcraft"),
    (
        SCH,
        "school",
        r"school|college|academy|academies|primary|junior|nursery|sixth form"
        r"|kegs|fitzwimarc|abbs ?cross|eastbury|royal liberty|st benedicts|havering",
    ),
    (SCH, "girlguiding", r"guide|brownie|rainbow|girlguiding"),
    (OSG, "scouting", r"scout|cub|beaver|explorer|\besu\b|jamboree|\bwsj\b|network|troop|\bsg\b"),
    # "1st Broomfield", "5th/9th Chelmsford" - universally a Scout group
    (OSG, "ordinal name", r"^\d+(st|nd|rd|th)\b|\d+(st|nd|rd|th)/\d+(st|nd|rd|th)"),
    (SCH, "DofE", r"dofe|d of e|duke of edinburgh"),
    (SCH, "commercial", r"\bcic\b|c\.i\.c|expedition|outdoors|adventure|maps|larp|science"),
]

# Where the booker typed their own name into the group field, the email domain
# is the better signal.
DOMAIN_RULES = {
    "hpscouts.org": OSG,
    "fitzwimarc.com": SCH,
    "royalliberty.co.uk": SCH,
    "lambourne-end.org.uk": SCH,
    "eastcoastoutdoors.co.uk": SCH,
    "letsgetout.co.uk": SCH,
}


def classify(group_name: str, email: str, known: dict) -> tuple[str, str]:
    """Resolve a group type for an out-of-district booking, with the reason."""
    lowered = group_name.lower()
    for group_type, reason, pattern in NAME_RULES:
        if re.search(pattern, lowered):
            return group_type, reason

    domain = email.split("@")[-1].lower()
    for suffix, group_type in DOMAIN_RULES.items():
        if domain.endswith(suffix):
            return group_type, f"domain {suffix}"

    # Whatever the app already decided for this group keeps history consistent
    if lowered in known:
        return known[lowered], "existing app data"

    # Private and family hires - the catch-all non-Scout bucket
    return SCH, "unresolved default"


def known_group_types(archive: ArchiveData, live_path: Path) -> dict:
    """group_name (lowered) -> group_type, from the data the app already holds."""
    known = {}
    for item in archive.items:
        known.setdefault(item.group_name.strip().lower(), item.group_type)
    live = load_json(live_path, LiveData, use_checksum=False)
    for rec in live.items if live else []:
        known.setdefault(rec.booking.group_name.strip().lower(), rec.booking.group_type)
    return known


# --------------------------------------------------------------------------
# Row -> BookingData
# --------------------------------------------------------------------------
def parse_facilities(raw: str) -> list[str]:
    """Split a facilities cell and keep only the ones the site actually books."""
    bookable = FIELD_MAPPINGS_DICT.get("bookable_facilities", [])
    names = []
    for part in raw.split(","):
        cleaned = FACILITY_PREFIX.sub("", part).strip()
        #
        ## "Roxby Hut (Charge may apply)", "Roxby Hut?" and "Roxby Hut + Toilet
        ## Access" are all just Roxby Hut wearing a note, and it is the one
        ## chargeable facility - dropping them would lose real income.
        match = next((f for f in bookable if cleaned.lower().startswith(f.lower())), None)
        names.append(match or cleaned)
    # Dedupe, or a facility named twice gets charged twice
    return list(dict.fromkeys(sort_facilities(names).valid))


def convert(row: dict, sheet: str, row_no: int, known: dict) -> tuple:
    """Return (BookingData, reason) - exactly one of the two is set."""
    # Every early return is one reason a row cannot be imported, and each is
    # reported separately, so collapsing them would only hide why rows dropped.
    # pylint: disable=too-many-locals,too-many-return-statements,too-many-branches
    if sheet == "CD":
        email, group_name, size_col, arrive_col = "B", "E", "F", "G"
    else:
        email, group_name, size_col, arrive_col = "B", "C", "I", "L"

    name = cell(row, group_name)
    arriving = to_datetime(cell(row, arrive_col))
    size = to_int(cell(row, size_col))

    if sheet == "OD" and (size is None or size < 1):
        #
        ## A run of rows left "Number of people" at zero and put the whole
        ## party in the leaders/family columns instead. Without this they are
        ## dropped as sizeless, losing the booking entirely.
        size = (to_int(cell(row, "J")) or 0) + (to_int(cell(row, "K")) or 0)

    # Column drift: a few rows are shifted, putting a phone number where the
    # lead name goes or a bare number in the group name. Their real values
    # cannot be recovered without guessing, so they are reported instead.
    if arriving is None:
        return None, "unparseable arrival date (column drift)"
    if not name or to_int(name) is not None:
        return None, "group name missing or numeric (column drift)"
    if size is None or size < 1:
        return None, f"unusable group size {cell(row, size_col)!r}"

    if any("cancel" in (v or "").lower() for v in row.values()):
        return None, "flagged cancelled"

    if arriving >= OVERLAP_FROM:
        return None, "arrives after the app went live (already imported)"

    if sheet == "CD":
        # Departure is a time of day only, so it pairs with the arrival date -
        # the same thing create_rec_from_sheet_row does for day visits.
        fraction = to_datetime(cell(row, "H"))
        if fraction is None:
            return None, "unparseable departure time"
        departing = datetime.combine(arriving.date(), fraction.time())
        if departing < arriving:
            #
            ## The cell holds a time of day with no date, so a departure
            ## earlier than the arrival can only mean the next morning - an
            ## evening arrival with a sleepover. Not a guess: it is the only
            ## reading a time-only field allows.
            departing += timedelta(days=1)
        group_type, reason = CDS, "district form"
        # The older form had a facilities column; the current one does not and
        # the sheet config injects Campfire Circle for every row.
        facilities = parse_facilities(cell(row, "I")) or ["Campfire Circle"]
    else:
        departing = to_datetime(cell(row, "M"))
        if departing is None:
            return None, "unparseable departure date"
        group_type, reason = classify(name, cell(row, email), known)
        facilities = parse_facilities(cell(row, "N"))

    if departing < arriving:
        #
        ## Out-of-district only, and always a corrupt departure MONTH while the
        ## day-of-month is right (wrong month picked in the date picker).
        ## Repairing it means guessing at person-nights, so these are reported
        ## rather than invented.
        return None, "departs before it arrives (corrupt departure month)"

    submitted = to_datetime(cell(row, "A")) or arriving

    booking = BookingData(
        id=f"Pre-Web-App #{sheet}{row_no}",
        original_sheet_md5=hashlib.md5(
            f"pre-web-app:{sheet}:{row_no}".encode()
        ).hexdigest(),
        group_type=group_type,
        group_name=name,
        group_size=size,
        event_type=get_event_type(
            arriving.replace(tzinfo=UK_TZ), departing.replace(tzinfo=UK_TZ)
        ),
        submitted=submitted,
        arriving=arriving,
        departing=departing,
        facilities=facilities,
    )
    return booking, reason


def collect(sources: dict, known: dict, existing_md5s: set) -> tuple[list, dict]:
    """Convert every row of both sheets, returning the bookings and tallies."""
    imported = []
    tally = {
        "skipped": Counter(),
        "examples": defaultdict(list),
        "type_reasons": Counter(),
        "by_year": Counter(),
        "facility_misses": Counter(),
    }

    for sheet, path in sources.items():
        rows = read_sheet(path, "archive")
        print(f"{sheet}: {len(rows)} rows from {path.name}")
        for row_no, row in enumerate(rows, start=1):
            booking, reason = convert(row, sheet, row_no, known)
            if booking is None:
                tally["skipped"][reason] += 1
                tally["examples"][reason].append(f"{sheet}{row_no} {cell(row, 'C')[:28]}")
                continue
            if booking.original_sheet_md5 in existing_md5s:
                tally["skipped"]["already imported"] += 1
                continue

            imported.append(booking)
            tally["type_reasons"][f"{booking.group_type} ({reason})"] += 1
            tally["by_year"][booking.arriving.year] += 1
            for part in cell(row, "I" if sheet == "CD" else "N").split(","):
                # Dropped means this fragment mapped to no bookable facility
                if part.strip() and not parse_facilities(part):
                    tally["facility_misses"][FACILITY_PREFIX.sub("", part).strip()] += 1

    return imported, tally


def report(imported: list, tally: dict, archive_count: int) -> None:
    """Print the summary that decides whether this import is safe to apply."""
    skipped = tally["skipped"]
    print(f"\n{'=' * 70}\nCONVERTED {len(imported)}   SKIPPED {sum(skipped.values())}\n{'=' * 70}")

    print("\nSkipped by reason:")
    for reason, count in skipped.most_common():
        print(f"  {count:>4}  {reason}")
        for example in tally["examples"][reason][:3]:
            print(f"          e.g. {example}")

    print("\nArrivals by year:")
    for year in sorted(tally["by_year"]):
        print(f"  {year}  {tally['by_year'][year]:>4}")

    print("\nGroup type (and the rule that fired):")
    for label, count in tally["type_reasons"].most_common():
        print(f"  {count:>4}  {label}")

    print("\nEvent type:", dict(Counter(b.event_type for b in imported)))

    print("\nFacilities kept:")
    for name, count in Counter(f for b in imported for f in b.facilities).most_common():
        print(f"  {count:>4}  {name}")

    if tally["facility_misses"]:
        print("\nFacility names dropped (not bookable):")
        for name, count in tally["facility_misses"].most_common(10):
            print(f"  {count:>4}  {name!r}")

    income = sum(
        estimate_cost(b.event_type, b.num_overnights(), b.group_type, b.group_size, b.facilities)
        for b in imported
    )
    print(f"\nEstimated income added (at current rates): £{income / 100:,.2f}")
    print(f"Archive would go from {archive_count} to {archive_count + len(imported)}")


# --------------------------------------------------------------------------
def main():
    """Read both spreadsheets, convert, report, and optionally write."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--downloads",
        type=Path,
        default=Path("/mnt/c/Users/Chris/Downloads"),
        help="folder holding the two exported .xlsx files",
    )
    parser.add_argument("--archive-path", type=Path, default=ARCHIVE_FILE_PATH)
    parser.add_argument(
        "--live-path",
        type=Path,
        default=DATA_FILE_PATH,
        help="live bookings file, read only to learn existing group types",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="actually write; without this it is a dry run",
    )
    args = parser.parse_args()

    sources = {
        "CD": args.downloads / "Booking Form - Chelmsford District (Responses).xlsx",
        "OD": args.downloads / "Booking Form - Out of District (Responses).xlsx",
    }
    for sheet, path in sources.items():
        if not path.exists():
            parser.error(f"{sheet}: no such file {path}")

    archive = load_json(args.archive_path, ArchiveData, use_checksum=False)
    if archive is None:
        parser.error(f"no archive at {args.archive_path}")
    known = known_group_types(archive, args.live_path)
    existing_md5s = {item.original_sheet_md5 for item in archive.items}
    print(f"Archive: {args.archive_path} ({len(archive.items)} items)\n")

    imported, tally = collect(sources, known, existing_md5s)
    report(imported, tally, len(archive.items))

    if not args.write:
        print("\nDRY RUN - nothing written. Re-run with --write to apply.")
        return

    archive.items.extend(imported)
    save_json(archive, args.archive_path)
    print(f"\nWritten. Archive now holds {len(archive.items)} items.")


if __name__ == "__main__":
    main()
