"""
pricing.py - The single source of truth for what a booking costs.

Every charge - the event itself and each chargeable facility - is priced by one
rule built from two independent flags:

    per_person   multiply the rate by the headcount
    per_night    multiply the rate by the number of nights

    cost = rate x (headcount if per_person) x (nights if per_night)

All four combinations are expressible, which the old single "unit" field could
not manage: it offered only per_person (which silently meant *per person per
night*, so day visits priced at zero) and per_group (a one-off, so a nightly
hut charge was billed once however long the stay).

Both the cost estimate and the Xero invoice are built from charge_lines(), so
the two can never disagree about a price.

The pricing config is validated at import. A typo in a rate is a billing error,
so it fails loudly on startup rather than quietly invoicing the wrong amount.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import NamedTuple, Optional

from config import FIELD_MAPPINGS_DICT, PRICING_DICT
from models.schemas import BookingData

logger = logging.getLogger("app_logger")

PRICING_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class ChargeLine:
    """One priced component of a booking.

    quantity x unit_pence is the amount. night is the date the line relates to,
    and per_night records whether quantity is a count of nights (so the invoice
    can say "4 nights" rather than repeating an identical line four times).
    """

    label: str
    quantity: int
    unit_pence: int
    night: date
    per_night: bool = False

    @property
    def total_pence(self) -> int:
        """Amount for this line in pence"""
        return self.quantity * self.unit_pence


def _events() -> dict:
    return PRICING_DICT.get("events") or {}


def _facilities() -> dict:
    return PRICING_DICT.get("facilities") or {}


def bookable_facilities() -> list[str]:
    """Facility names, in config order - the list the booking form offers."""
    return list(_facilities().keys())


class SortedFacilities(NamedTuple):
    """Class to hold valid and extra facilitiey requests"""

    valid: list[str]
    extra: list[str]


def sort_facilities(requested_facilities_list: list) -> SortedFacilities:
    """From a list of strings, compare against bookable facilities and sort into valid and extra"""
    known = bookable_facilities()
    rc = SortedFacilities(valid=[], extra=[])
    for f in requested_facilities_list:
        f = f.strip()
        if f in known:
            rc.valid.append(f)
        else:
            rc.extra.append(f)
    return rc


def _lines_for(spec: dict, label: str, b: BookingData) -> list[ChargeLine]:
    """Price one charge (an event or a facility) into invoice-ready lines."""
    rate = (spec.get("rates") or {}).get(b.group_type)
    if rate is None:
        # validate_pricing() rules this out in production; a monkeypatched or
        # hand-edited config could still reach here.
        logger.warning("No '%s' rate for group_type '%s'; not charging.", label, b.group_type)
        return []

    # A day or evening booking spans no nights but still uses the site once, so
    # a nightly rate is charged for that single occasion.
    nights = max(b.num_overnights(), 1)
    arriving = b.arriving.date()
    per_person = bool(spec.get("per_person"))
    per_night = bool(spec.get("per_night"))

    if per_person and per_night:
        # One line per night: the headcount can differ from night to night.
        lines = []
        for i in range(nights):
            night = arriving + timedelta(days=i)
            lines.append(ChargeLine(label, b.size_for_night(night), rate, night))
        return lines

    if per_person:
        return [ChargeLine(label, b.group_size, rate, arriving)]

    if per_night:
        return [ChargeLine(label, nights, rate, arriving, per_night=True)]

    return [ChargeLine(label, 1, rate, arriving)]


def charge_lines(b: BookingData) -> list[ChargeLine]:
    """Full priced breakdown of a booking: the event, then each chargeable facility."""
    event = _events().get(b.event_type)
    if event is None:
        logger.warning("Unknown event_type '%s' in pricing config; not charging.", b.event_type)
        lines = []
    else:
        lines = _lines_for(event, event.get("label", b.event_type.capitalize()), b)

    facilities = _facilities()
    for facility in b.facilities:
        spec = facilities.get(facility) or {}
        if not spec.get("rates"):
            continue  # facility carries no surcharge
        lines += _lines_for(spec, facility, b)

    return lines


def estimate_cost(b: BookingData) -> int:
    """Total cost of a booking in pence."""
    return sum(line.total_pence for line in charge_lines(b))


def validate_pricing(pricing: dict, group_types: Optional[list] = None) -> None:
    """Raise RuntimeError if the pricing config could misprice a booking.

    Cross-checks every rate table against the configured group types so a
    renamed or misspelt group cannot silently fall through to no charge.
    """
    version = pricing.get("schema_version")
    if version != PRICING_SCHEMA_VERSION:
        raise RuntimeError(
            f"pricing.json schema_version is {version!r}, expected {PRICING_SCHEMA_VERSION}"
        )

    if not pricing.get("events"):
        raise RuntimeError("pricing.json defines no events")

    if group_types is None:
        group_types = FIELD_MAPPINGS_DICT.get("group_types") or []
    # group_types is a list of {description, prefix}; skip the cross-check if
    # it is absent (test stubs) rather than inventing a constraint.
    expected = {g["description"] for g in group_types if isinstance(g, dict) and "description" in g}

    charges = [(f"event '{k}'", v) for k, v in pricing["events"].items()]
    charges += [
        (f"facility '{k}'", v)
        for k, v in (pricing.get("facilities") or {}).items()
        if v.get("rates") or v.get("per_person") or v.get("per_night")
    ]

    problems = []
    for what, spec in charges:
        problems += _charge_problems(what, spec, expected)

    if problems:
        raise RuntimeError("Invalid pricing config:\n  " + "\n  ".join(problems))


def _charge_problems(what: str, spec: dict, expected: set) -> list[str]:
    """Everything wrong with one charge's config, as human-readable strings."""
    problems = [
        f"{what}: {flag} must be true or false"
        for flag in ("per_person", "per_night")
        if not isinstance(spec.get(flag, False), bool)
    ]

    rates = spec.get("rates")
    if not isinstance(rates, dict) or not rates:
        return problems + [f"{what}: no rates"]

    problems += [
        f"{what}: rate for '{group}' must be a whole number of pence"
        for group, rate in rates.items()
        if not isinstance(rate, int) or isinstance(rate, bool) or rate < 0
    ]

    if expected:
        problems += [
            f"{what}: no rate for group type '{missing}'"
            for missing in sorted(expected - set(rates))
        ]
        problems += [
            f"{what}: rate for unknown group type '{unknown}'"
            for unknown in sorted(set(rates) - expected)
        ]

    return problems


validate_pricing(PRICING_DICT)
