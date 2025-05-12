"""
test_schemas.py
"""

# pylint: disable=all
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from config import UK_TZ
from models.schemas import ArchiveData, BookingData, LeaderData, LiveBooking, LiveData, TrackingData
from models.utils import now_uk


def test_valid_leader_data():
    data = LeaderData(name="Alice Smith", email="alice@example.com", phone="0123456789")
    assert data.name == "Alice Smith"
    assert data.email == "alice@example.com"
    assert data.phone == "0123456789"


def test_missing_name():
    with pytest.raises(ValidationError) as exc_info:
        LeaderData(email="alice@example.com", phone="0123456789")
    assert "name" in str(exc_info.value)


def test_missing_email():
    with pytest.raises(ValidationError) as exc_info:
        LeaderData(name="Alice", phone="0123456789")
    assert "email" in str(exc_info.value)


def test_missing_phone():
    with pytest.raises(ValidationError) as exc_info:
        LeaderData(name="Alice", email="alice@example.com")
    assert "phone" in str(exc_info.value)


def test_wrong_type_phone():
    with pytest.raises(ValidationError) as exc_info:
        LeaderData(name="Alice", email="alice@example.com", phone=1234567890)
    assert "phone" in str(exc_info.value)


def test_valid_booking_data():
    booking = BookingData(
        id="abc123",
        original_sheet_md5="deadbeef",
        group_type="Scouts",
        group_name="1st London",
        group_size=25,
        submitted="2025-05-01T10:00:00",
        arriving="2025-05-10T15:00:00",
        departing="2025-05-12T11:00:00",
    )
    assert booking.id == "abc123"
    assert booking.group_size == 25
    assert booking.submitted.tzinfo is not None


def test_invalid_group_size():
    with pytest.raises(ValidationError) as exc_info:
        BookingData(
            id="abc123",
            original_sheet_md5="deadbeef",
            group_type="Scouts",
            group_name="1st London",
            group_size="twenty",  # invalid type
            submitted="2025-05-01T10:00:00",
            arriving="2025-05-10T15:00:00",
            departing="2025-05-12T11:00:00",
        )
    assert "group_size" in str(exc_info.value)


def test_missing_required_field():
    with pytest.raises(ValidationError) as exc_info:
        BookingData(
            id="abc123",
            original_sheet_md5="deadbeef",
            group_type="Scouts",
            group_name="1st London",
            group_size=25,
            submitted="2025-05-01T10:00:00",
            arriving="2025-05-10T15:00:00",
            # missing 'departing'
        )
    assert "departing" in str(exc_info.value)


def test_timezone_added():
    booking = BookingData(
        id="xyz789",
        original_sheet_md5="feedface",
        group_type="Guides",
        group_name="2nd Manchester",
        group_size=18,
        submitted=datetime(2025, 5, 1, 10, 0),  # naive datetime
        arriving=datetime(2025, 5, 10, 14, 0),
        departing=datetime(2025, 5, 12, 11, 0),
    )
    assert booking.submitted.tzinfo is not None
    assert booking.arriving.tzinfo is not None
    assert booking.departing.tzinfo is not None


def test_frozen_fields():
    booking = BookingData(
        id="frozen123",
        original_sheet_md5="abc123def456",
        group_type="Explorers",
        group_name="3rd Sheffield",
        group_size=12,
        submitted="2025-05-02T09:30:00",
        arriving="2025-05-10T16:00:00",
        departing="2025-05-12T10:00:00",
    )

    # Test original_sheet_md5 is frozen
    with pytest.raises(ValidationError) as exc_md5:
        booking.original_sheet_md5 = "newmd5value"
    assert "Field is frozen" in str(exc_md5.value)
    assert "original_sheet_md5" in str(exc_md5.value)

    # Test submitted is frozen
    with pytest.raises(ValidationError) as exc_sub:
        booking.submitted = "2025-05-03T10:00:00"
    assert "Field is frozen" in str(exc_sub.value)
    assert "submitted" in str(exc_sub.value)

    # Test id is frozen
    with pytest.raises(ValidationError) as exc_id:
        booking.id = "newid456"
    assert "Field is frozen" in str(exc_id.value)
    assert "id" in str(exc_id.value)


def test_valid_tracking_data():
    data = TrackingData(
        status="Confirmed",
        invoice=True,
        notes="Booking confirmed.",
        google_calendar_id="abc123",
        pending_email_sent="2025-05-01T09:00:00",
        confirm_email_sent="2025-05-02T10:00:00",
        cancel_email_sent=None,
        pend_question=None,
        cancel_reason=None,
    )

    assert data.status == "Confirmed"
    assert data.invoice is True
    assert data.pending_email_sent.tzinfo == UK_TZ
    assert data.confirm_email_sent.tzinfo == UK_TZ
    assert data.cancel_email_sent is None


def test_invalid_status():
    with pytest.raises(ValidationError) as exc_info:
        TrackingData(status="InvalidStatus", invoice=True, notes="Bad status")
    assert "status" in str(exc_info.value)


def test_invalid_datetime_format():
    with pytest.raises(ValidationError) as exc_info:
        TrackingData(
            status="Pending",
            invoice=False,
            notes="Invalid datetime",
            pending_email_sent="not-a-date",
        )
    assert "pending_email_sent" in str(exc_info.value)
    assert "Invalid isoformat string" in str(exc_info.value)


def test_optional_fields_can_be_none():
    data = TrackingData(status="New", invoice=False, notes="Some optional fields are missing")
    assert data.google_calendar_id is None
    assert data.pending_email_sent is None


# Sample data for testing
@pytest.fixture
def booking_data():
    return BookingData(
        id="frozen123",
        original_sheet_md5="abc123def456",
        group_type="Explorers",
        group_name="3rd Sheffield",
        group_size=12,
        submitted="2025-05-02T09:30:00",
        arriving="2025-05-10T16:00:00",
        departing="2025-05-12T10:00:00",
    )


@pytest.fixture
def leader_data():
    return LeaderData(name="John Doe", email="john.doe@example.com", phone="1234567890")


@pytest.fixture
def tracking_data():
    return TrackingData(
        status="Pending",
        invoice=False,
        notes="Test booking",
        google_calendar_id="test-id-123",
        pending_email_sent="2025-05-01T09:30:00",
        confirm_email_sent="2025-05-01T10:00:00",
        cancel_email_sent="2025-05-01T11:00:00",
        pend_question="Pending question",
        cancel_reason="No reason",
    )


@pytest.fixture
def live_booking(booking_data, leader_data, tracking_data):
    return LiveBooking(booking=booking_data, leader=leader_data, tracking=tracking_data)


# Test for LiveBooking
def test_live_booking(live_booking):

    # Assertions to check if data is correctly assigned
    assert live_booking.booking.id == "frozen123"
    assert live_booking.leader.name == "John Doe"
    assert live_booking.tracking.status == "Pending"
    assert live_booking.booking.group_name == "3rd Sheffield"
    assert live_booking.leader.email == "john.doe@example.com"

    # Check if datetime fields are correctly handled
    assert live_booking.booking.submitted == datetime.fromisoformat("2025-05-02T09:30:00").replace(
        tzinfo=UK_TZ
    )
    assert live_booking.tracking.pending_email_sent == datetime.fromisoformat(
        "2025-05-01T09:30:00"
    ).replace(tzinfo=UK_TZ)

    assert live_booking.is_valid()
    assert live_booking.get_problematic_data() is None


# Test invalid LiveBooking (missing data for example)
def test_invalid_live_booking():
    with pytest.raises(ValueError):
        # Missing leader data
        live_booking = LiveBooking(
            booking=BookingData(
                id="frozen123",
                original_sheet_md5="abc123def456",
                group_type="Explorers",
                group_name="3rd Sheffield",
                group_size=12,
                submitted="2025-05-02T09:30:00",
                arriving="2025-05-10T16:00:00",
                departing="2025-05-12T10:00:00",
            ),
            leader=None,  # Invalid leader data
            tracking=TrackingData(
                status="Pending",
                invoice=False,
                notes="Test booking",
                google_calendar_id="test-id-123",
                pending_email_sent="2025-05-01T09:30:00",
                confirm_email_sent="2025-05-01T10:00:00",
                cancel_email_sent="2025-05-01T11:00:00",
                pend_question="Pending question",
                cancel_reason="No reason",
            ),
        )


from unittest.mock import patch


# Test for LiveData class
def test_live_data_default_values(live_booking):
    # Create the LiveData instance
    live_data = LiveData(items=[live_booking])

    # Check if updated field is correctly initialized to the current UK time
    assert now_uk() - timedelta(seconds=5) <= live_data.updated <= now_uk()

    # Check if next_idx is initialized to 1 by default
    assert live_data.next_idx == 1

    # Check if items is a list containing the live booking data
    assert isinstance(live_data.items, list)
    assert len(live_data.items) == 1
    assert live_data.items[0] == live_booking


# Test that the items field is an empty list if no items are provided
def test_live_data_with_empty_items():
    live_data = LiveData(items=[])

    assert isinstance(live_data.items, list)
    assert len(live_data.items) == 0


# Sample data for the BookingData object
@pytest.fixture
def booking_data():
    return BookingData(
        id="frozen123",
        original_sheet_md5="abc123def456",
        group_type="Explorers",
        group_name="3rd Sheffield",
        group_size=12,
        submitted="2025-05-02T09:30:00",
        arriving="2025-05-10T16:00:00",
        departing="2025-05-12T10:00:00",
    )


# Test for ArchiveData class
def test_archive_data_default_values(booking_data):
    # Create the ArchiveData instance
    archive_data = ArchiveData(items=[booking_data])

    # Check if updated field is correctly initialized to the current UK time
    assert now_uk() - timedelta(seconds=5) <= archive_data.updated <= now_uk()

    # Check if items is a list containing the booking data
    assert isinstance(archive_data.items, list)
    assert len(archive_data.items) == 1
    assert archive_data.items[0] == booking_data


# Test that the items field is an empty list if no items are provided
def test_archive_data_with_empty_items():
    archive_data = ArchiveData(items=[])

    assert isinstance(archive_data.items, list)
    assert len(archive_data.items) == 0
