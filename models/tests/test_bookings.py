"""
test_bookings.py
"""

# pylint: disable=all
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from models.bookings import Bookings
from models.schemas import ArchiveData, BookingData, LeaderData, LiveBooking, LiveData, TrackingData


# Sample data for testing
@pytest.fixture
def live_booking_data():
    return BookingData(
        id="frozen123",
        original_sheet_md5="abc123def456",
        group_type="Explorers",
        group_name="3rd Sheffield",
        group_size=12,
        event_type="eve",
        submitted="2025-05-02T09:30:00",
        arriving="2025-05-10T16:00:00",
        departing="2025-05-12T10:00:00",
        facilities=["Scouts"],
    )


@pytest.fixture
def archive_booking_data():
    return BookingData(
        id="frozen123",
        original_sheet_md5="iminarchive",
        group_type="Explorers",
        group_name="3rd Sheffield",
        group_size=12,
        event_type="eve",
        submitted="2025-05-02T09:30:00",
        arriving="2025-05-10T16:00:00",
        departing="2025-05-12T10:00:00",
        facilities=["Scouts"],
    )


@pytest.fixture
def leader_data():
    return LeaderData(name="John Doe", email="john.doe@example.com", phone="1234567890")


@pytest.fixture
def tracking_data():
    return TrackingData(
        status="Pending",
        cost_estimate=100,
        notes="Test booking",
        google_calendar_id="test-id-123",
        pending_email_sent="2025-05-01T09:30:00",
        confirm_email_sent="2025-05-01T10:00:00",
        cancel_email_sent="2025-05-01T11:00:00",
        pend_question="Pending question",
        cancel_reason="No reason",
    )


@pytest.fixture
def live_booking(live_booking_data, leader_data, tracking_data):
    return LiveBooking(booking=live_booking_data, leader=leader_data, tracking=tracking_data)


@pytest.fixture
def setup_bookings(live_booking, archive_booking_data):

    # Create LiveData and ArchiveData objects and populate them with the booking instances
    live_data = LiveData(items=[live_booking])
    archive_data = ArchiveData(items=[archive_booking_data])

    # Instantiate the BookingManager
    manager = Bookings()
    manager.live = live_data
    manager.archive = archive_data

    return manager


def test_find_booking_by_md5_in_live(setup_bookings):
    manager = setup_bookings
    # Test with a value that exists in live bookings
    result = manager._find_booking_by_md5("abc123def456")
    assert result is True  # Should return True since md5_live_1 is in live bookings


def test_find_booking_by_md5_in_archive(setup_bookings):
    manager = setup_bookings
    # Test with a value that exists in archived bookings
    result = manager._find_booking_by_md5("iminarchive")
    assert result is True  # Should return True since md5_archive_2 is in archived bookings


def test_find_booking_by_md5_not_found(setup_bookings):
    manager = setup_bookings
    # Test with a value that does not exist in either live or archive bookings
    result = manager._find_booking_by_md5("md5_not_found")
    assert result is False  # Should return False since md5_not_found is not found in any booking
