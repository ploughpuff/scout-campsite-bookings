
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



import time
from datetime import datetime
from config import DATE_FORMAT, DATE_FORMAT_WITH_SECONDS, UK_TZ

def get_pretty_datetime_str(epoch_time=None, include_seconds=False):

    if epoch_time is None:
        epoch_time = int(time.time())
            
    if epoch_time and isinstance(epoch_time, (int, float)) and epoch_time < 1_000_000_000:
        # Not a valid epoch time, return as-is
        return str(epoch_time)

    try:
        dt = datetime.fromtimestamp(epoch_time, UK_TZ)
        fmt = DATE_FORMAT_WITH_SECONDS if include_seconds else DATE_FORMAT
        return dt.strftime(fmt)
    except Exception:
        return str(epoch_time)
