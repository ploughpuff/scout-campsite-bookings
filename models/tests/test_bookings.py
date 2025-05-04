"""
test_bookings.py
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from models.booking_types import BookingType
from models.bookings import Bookings, SiteData

# pylint: disable=all


@pytest.fixture
def test_booking_manager():
    # Create test data
    test_bookings = {
        "bookings": [
            {"id": 1, "original_sheet_md5": "abc123"},
            {"id": 2, "original_sheet_md5": "def456"},
        ]
    }
    test_archive = {"bookings": [{"id": 3, "original_sheet_md5": "ghi789"}]}

    # Create instance and inject test data
    manager = Bookings()
    manager.set_test_data(test_bookings, test_archive)
    return manager


# pylint: disable=protected-access


def test_find_md5_in_data(test_booking_manager):
    assert test_booking_manager._find_booking_by_md5("abc123") is True
    assert test_booking_manager._find_booking_by_md5("def456") is True


def test_find_md5_in_archive(test_booking_manager):
    assert test_booking_manager._find_booking_by_md5("ghi789") is True


def test_find_md5_not_found(test_booking_manager):
    assert test_booking_manager._find_booking_by_md5("notfound") is False


def test_create_new_booking():
    # Create instance of Bookings (or mock if needed)
    bookings = Bookings()

    # Fake input row from Google Sheets
    row = {
        "timestamp": "03/01/2025 20:35:03",
        "email_address": "chris_harpley@yahoo.co.uk",
        "name_of_lead_person": "John Doe",
        "mobile_Number_for_lead_person": "01234567889",
        "chelmsford_scout_group": "1st Town",
        "number_of_people": "23",
        "arrival_date_time": "05/04/2025 08:30:00",
        "departure_time": "10:30:00",
    }

    booking_type = BookingType.DISTRICT_DAY_VISIT

    booking = bookings.create_rec_from_sheet_row(row, booking_type)

    assert isinstance(booking, SiteData)
    assert booking.group_name == "1st Town"
    assert booking.leader_name == "John Doe"
    assert booking.booking_type == booking_type
    assert booking.group_size == 23
    assert booking.status == "New"
    assert booking.invoice is False
    assert isinstance(booking.arriving, datetime)
    assert isinstance(booking.departing, datetime)
