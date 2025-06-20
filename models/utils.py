"""
utils.py - Utility functions for use in Scout Campsite Booking.
"""

import logging
import re
from datetime import datetime, time

from flask import current_app, session

from config import DATE_FORMAT, DATE_FORMAT_WITH_SECONDS, FIELD_MAPPINGS_DICT, UK_TZ


def is_email_enabled():
    """Checks session if email is enabled, or falls back to .env var"""
    return session.get("email_enabled", current_app.config["EMAIL_ENABLED"])


def now_uk() -> datetime:
    """Returns the current datetime in UK local time."""
    return datetime.now(tz=UK_TZ)


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


def get_pretty_date_str(dt, inc_time=False, full_month=False):
    """Return pretty date string from passed dt object"""
    try:
        # https://strftime.org/
        day_name = dt.strftime("%A")
        day = dt.day
        suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        month = dt.strftime("%B") if full_month else dt.strftime("%b")
        time_part = dt.strftime("%H:%M")
        year = dt.year
        current_year = now_uk().year

        date_str = f"{day_name}, {day}{suffix} {month}"

        if year != current_year:
            date_str += f" {year}"

        if inc_time:
            date_str += f" @ {time_part}"

        return date_str

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


def get_booking_prefix(description: str) -> str:
    """Return prefix for this group description"""
    try:
        return next(
            item["prefix"]
            for item in FIELD_MAPPINGS_DICT.get("group_types")
            if item["description"] == description
        )
    except StopIteration as exc:
        raise ValueError(f"Group description '{description}' not found.") from exc


def get_event_type(start_dt: datetime, end_dt: datetime) -> str:
    """Generate a facilities prefix (EVE, OVERNIGHT) from two dates"""
    if start_dt.date() != end_dt.date():
        rc = "overnight"
    elif end_dt.time() < time(16, 5):
        # Booking which end before 16:05 we class as DAY
        rc = "day"
    else:
        rc = "eve"

    return rc


def estimate_cost(event_type: str, group_type: str, group_size: int, facilities: list) -> int:
    """ "Estimate cost of booking based on group type and size"""
    # People costs are based on event_type (day, eve, overnight)
    # and group_type (cds, ods, sch)
    unit = FIELD_MAPPINGS_DICT.get("charges").get(event_type).get("unit")
    rate = FIELD_MAPPINGS_DICT.get("charges").get(event_type).get("rates").get(group_type)

    if rate is None:
        logger = logging.getLogger("app_logger")
        logger.warning("Failed to find cost rate from field file for group [%s]", group_type)
        cost = 0
    elif unit == "per_person":
        cost = rate * group_size
    elif unit == "per_group":
        cost = rate
    else:
        cost = 0

    # Building cost
    if "Roxby Hut" in facilities:
        cost += FIELD_MAPPINGS_DICT.get("charges").get("roxby_hut").get("rates").get(group_type)

    return cost
