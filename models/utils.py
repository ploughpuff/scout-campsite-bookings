"""
utils.py - Utility functions for use in Scout Campsite Booking.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime

from config import DATE_FORMAT, DATE_FORMAT_WITH_SECONDS, UK_TZ


def now_uk() -> datetime:
    """Returns the current datetime in UK local time."""
    return datetime.now(UK_TZ)


def now_uk_iso() -> str:
    """Returns the current datetime in ISO format: '2025-06-01T14:30:00+01:00'
    Not to be used with HTML input labels.  Use strftime('%Y-%m-%dT%H:%M')"""
    return datetime.now(UK_TZ).isoformat()


def parse_iso_datetime(iso_str: str) -> datetime | str:
    """
    Attempt to parse an ISO 8601 string into a timezone-aware datetime.
    If parsing fails, return the original string untouched.

    Args:
        iso_str (str): ISO 8601 datetime string.

    Returns:
        datetime | str: Parsed datetime if successful, otherwise the original string.
    """
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UK_TZ)
        return dt
    except (ValueError, TypeError):
        return iso_str


def datetime_to_iso_uk(value: datetime) -> str:
    """
    Convert a datetime object to an ISO 8601 string in UK local time.
    If the datetime is naive, assumes UK timezone.

    Args:
        value (datetime): The datetime object to convert.

    Returns:
        str: ISO 8601 formatted string with UK timezone.
    """
    if not isinstance(value, datetime):
        raise TypeError("Expected a datetime object")

    if value.tzinfo is None:
        value = value.replace(tzinfo=UK_TZ)
    else:
        value = value.astimezone(UK_TZ)

    return value.isoformat()


def secs_to_hr(seconds):
    """
    Convert int number of seconds into human readable string.

    Args:
        seconds (int): Number of seconds to use in the conversion

    Returns:
        str: A sring like '1d 3h 45m 59s'
    """
    seconds = int(seconds)

    if not isinstance(seconds, int) or seconds < 0:
        raise ValueError("Seconds must be a non-negative integer")

    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)

    parts = []
    if d:
        parts.append(f"{d}d")
    if h or parts:
        parts.append(f"{h}h")
    if m or parts:
        parts.append(f"{m}m")
    parts.append(f"{s}s")

    return " ".join(parts)


def get_timestamp_for_notes(value=None, include_seconds=False):
    """
    Create a pretty date string from either dt object or epoch int.

    If no input value given, uses current time.
    Default is Hours:Mins, but Secs can be added if required.

    Returns:
        str: A pretty date string following the format defined in config.

    """

    if isinstance(value, datetime):
        dt = value

    elif value is None:
        dt = now_uk()

    elif value and isinstance(value, (int, float)) and value > 1_000_000_000:
        dt = datetime.fromtimestamp(value, UK_TZ)

    else:
        return value

    try:
        fmt = DATE_FORMAT_WITH_SECONDS if include_seconds else DATE_FORMAT
        return dt.strftime(fmt)
    except (TypeError, ValueError) as e:
        logger = logging.getLogger("app_logger")
        logger.warning("Failed to format epoch timestamp: %s", e)
        return value


def get_pretty_date_str(dt):
    """Return pretty date string from passed dt object"""
    try:
        day = dt.day
        suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        month = dt.strftime("%B")
        time_part = dt.strftime("%H:%M")
        year = dt.year
        current_year = now_uk().year

        date_str = f"{day}{suffix} {month}"
        if year != current_year:
            date_str += f" {year}"

        return f"{date_str} {time_part}"
    except (TypeError, ValueError) as e:
        logger = logging.getLogger("app_logger")
        logger.warning("Failed to create pretty date string %s", e)
        return None


def normalize_key(key: str) -> str:
    """
    Convert a string like 'Email address' or 'Arrival Date / Time'
    into a safe, lowercase, underscore-separated key for Python/Jinja.

    Args:
        key (str): The original key string.

    Returns:
        str: Normalized key in snake_case format.
    """
    key = key.strip()
    key = re.sub(r"[^\w\s]", "", key)  # Remove punctuation
    key = re.sub(r"\s+", "_", key)  # Replace spaces with underscores
    return key.lower()


def write_checksum(json_path):
    """Create and write a checksum to file.

    Args:
        json_path (str): Path to JSON file to checksum.
    """
    content = json_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    json_path.with_suffix(".sha256").write_text(digest, encoding="utf-8")


def verify_checksum(json_path):
    """Compare checksum to real file

    Args:
        json_path (str): Path to JSON file.

    Returns:
        Boolean: True if file checksum matches value stored in checksum file, else False.
    """
    if not json_path.with_suffix(".sha256").exists():
        return True

    try:
        content = json_path.read_text(encoding="utf-8")
        stored = json_path.with_suffix(".sha256").read_text(encoding="utf-8").strip()
        return hashlib.sha256(content.encode("utf-8")).hexdigest() == stored
    except (TypeError, ValueError) as e:
        logger = logging.getLogger("app_logger")
        logger.warning(
            "Problem creating digest for comparison against stored [%s] [%s]: %s",
            json_path,
            stored,
            e,
        )
        return False


def backup_with_rotation(file_path, max_backups=5):
    """Backup booking JSON file and purge old backups.

    Args:
        file_path (str): Path to booking JSON file
        max_backups (int, optional): Max backups.  See config. Defaults to 5.
    """
    if not file_path.exists():
        return

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = file_path.with_name(f"{file_path.stem}-{timestamp}{file_path.suffix}")
    shutil.copy2(file_path, backup_path)

    # Cleanup old backups
    backups = sorted(
        file_path.parent.glob(f"{file_path.stem}-*{file_path.suffix}"),
        key=os.path.getmtime,
        reverse=True,
    )
    for old in backups[max_backups:]:
        old.unlink(missing_ok=True)


def atomic_write_json(data, target_path):
    """Atomic save to avoid half-saved and corrupt files.

    Args:
        data (list): Seralised booking data
        target_path (str): Path to save JSON dump
    """
    with tempfile.NamedTemporaryFile(
        "w", dir=target_path.parent, delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(data, tmp, indent=2)
        temp_path = tmp.name

    os.replace(temp_path, target_path)
