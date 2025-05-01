"""
test_bookings.py
"""

import pytest

from models.bookings import Bookings

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
