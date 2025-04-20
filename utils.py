"""
utils.py - Utility functions for use in Scout Campsite Booking.
"""

import time
import logging
from datetime import datetime
from config import DATE_FORMAT, DATE_FORMAT_WITH_SECONDS, UK_TZ


def secs_to_hr(seconds):
    """
    Convert int number of seconds into human readable string.

    Args:
        seconds (int): Number of seconds to use in the conversion

    Returns:
        str: A sring like '1d 3h 45m 59s'
    """
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


def get_pretty_datetime_str(epoch_time=None, include_seconds=False):
    """
    Create a pretty date string from epoch int.

    If not epoch given, uses current time.
    Default is Hours:Mins, but Secs can be added if required.

    Returns:
        str: A pretty date string following the format defined in config.

    """

    if epoch_time is None:
        epoch_time = int(time.time())

    if (
        epoch_time
        and isinstance(epoch_time, (int, float))
        and epoch_time < 1_000_000_000
    ):
        # Not a valid epoch time, return as-is
        return str(epoch_time)

    try:
        dt = datetime.fromtimestamp(epoch_time, UK_TZ)
        fmt = DATE_FORMAT_WITH_SECONDS if include_seconds else DATE_FORMAT
        return dt.strftime(fmt)
    except (TypeError, ValueError) as e:
        logger = logging.getLogger("app_logger")
        logger.warning("Failed to format epoch timestamp: %s", e)
        return str(epoch_time)
