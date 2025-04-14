
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

