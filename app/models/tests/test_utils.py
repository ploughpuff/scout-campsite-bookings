# pylint: skip-file
import pytest

from app.models.utils import normalize_key, secs_to_hr


# Test cases for different scenarios
def test_secs_to_hr():
    # Test for seconds only (less than a minute)
    assert secs_to_hr(45) == "45s"

    # Test for minutes only
    assert secs_to_hr(90) == "1m 30s"

    # Test for hours and seconds
    assert secs_to_hr(3665) == "1h 1m 5s"

    # Test for hours and minutes
    assert secs_to_hr(7200) == "2h 0m 0s"

    # Test for days, hours, minutes, and seconds
    assert secs_to_hr(90061) == "1d 1h 1m 1s"

    # Test for exactly 1 day (86,400 seconds)
    assert secs_to_hr(86400) == "1d 0h 0m 0s"

    # Test for exactly 1 minute (60 seconds)
    assert secs_to_hr(60) == "1m 0s"

    # Test for exactly 1 hour (3,600 seconds)
    assert secs_to_hr(3600) == "1h 0m 0s"

    # Test for large number of seconds (more than 1 day)
    assert secs_to_hr(100000) == "1d 3h 46m 40s"

    # Test it can handle floats
    assert secs_to_hr(60.999) == "1m 0s"


# Optional: Test for invalid inputs
def test_invalid_secs_to_hr():
    with pytest.raises(ValueError):
        secs_to_hr(-1)  # Negative seconds should raise an exception (if that's desired behavior)

    with pytest.raises(ValueError):
        secs_to_hr("abc")  # Non-integer input should raise an exception


def test_normalise_key():
    assert normalize_key("Arrival Date / Time") == "arrival_date_time"
    assert normalize_key("Email Address") == "email_address"
