"""
test_bookings.py
"""

# pylint: disable=all
from datetime import datetime, timedelta, timezone
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


def test_change_status_without_reason_leaves_status_untouched(setup_bookings, monkeypatch):
    """Regression: a rejected Cancel/Pend (missing reason) must not mutate the
    in-memory status - a later save would silently persist it with no history."""
    import models.bookings as bookings_module

    manager = setup_bookings
    monkeypatch.setattr(bookings_module, "flash", lambda *a, **k: None)

    def fail(*a, **k):
        raise AssertionError("no save/email/calendar expected for a rejected change")

    monkeypatch.setattr(bookings_module, "save_json", fail)
    monkeypatch.setattr(bookings_module, "send_email_notification", fail)
    monkeypatch.setattr(bookings_module, "update_calendar_entry", fail)

    rec = manager.live.items[0]
    assert rec.tracking.status == "Pending"

    result = manager.change_status("frozen123", "Cancelled", description=None)

    assert result is False
    assert rec.tracking.status == "Pending"  # unchanged
    assert "Status changed" not in rec.tracking.notes


def _mk_booking(**overrides):
    """Minimal valid BookingData with overridable fields for stats tests"""
    defaults = dict(
        id="TST-2025-0001",
        original_sheet_md5="md5-default",
        group_type="Other Scout Group",
        group_name="Test Group",
        group_size=10,
        event_type="overnight",
        submitted="2025-05-01T09:00:00",
        arriving="2025-06-06T18:00:00",
        departing="2025-06-09T10:00:00",
        facilities=[],
    )
    defaults.update(overrides)
    return BookingData(**defaults)


@pytest.fixture
def stats_manager(leader_data):
    """One archived overnight, one live Completed day booking, one Confirmed
    (open), one Cancelled (ignored)."""

    archived = _mk_booking(
        original_sheet_md5="md5-archived",
        group_name="Camp Group",
        facilities=["Roxby Hut"],
        # 3 nights, size 10 -> 30 person-nights; June; lead 36 days
    )

    live_day = LiveBooking(
        booking=_mk_booking(
            id="TST-2025-0002",
            original_sheet_md5="md5-day",
            group_name="Day Group",
            group_size=20,
            event_type="day",
            submitted="2025-03-05T09:00:00",
            arriving="2025-03-15T09:00:00",
            departing="2025-03-15T15:00:00",
            facilities=["Campfire Circle"],
        ),
        leader=leader_data,
        tracking=TrackingData(status="Completed", cost_estimate=500, notes=""),
    )

    live_open = LiveBooking(
        booking=_mk_booking(
            id="TST-2026-0003",
            original_sheet_md5="md5-open",
            arriving="2026-08-01T18:00:00",
            departing="2026-08-03T10:00:00",
        ),
        leader=leader_data,
        tracking=TrackingData(status="Confirmed", cost_estimate=1000, notes=""),
    )

    live_cancelled = LiveBooking(
        booking=_mk_booking(
            id="TST-2025-0004",
            original_sheet_md5="md5-cancelled",
            arriving="2025-09-01T18:00:00",
            departing="2025-09-02T10:00:00",
        ),
        leader=leader_data,
        tracking=TrackingData(status="Cancelled", cost_estimate=9999, notes=""),
    )

    manager = Bookings()
    manager.live = LiveData(items=[live_day, live_open, live_cancelled])
    manager.archive = ArchiveData(items=[archived])
    return manager


def test_get_yearly_stats_rich(stats_manager):
    stats = stats_manager.get_yearly_stats()

    assert stats["open_bookings"] == 1
    assert stats["open_by_status"] == {"New": 0, "Pending": 0, "Confirmed": 1}

    # Only 2025 has counted bookings (open 2026 excluded, cancelled ignored)
    assert [y["year"] for y in stats["years"]] == [2025]
    y = stats["years"][0]

    assert y["bookings_total"] == 2
    assert y["day_bookings"] == 1 and y["ovr_bookings"] == 1 and y["eve_bookings"] == 0
    assert y["day_total_visitors"] == 20
    assert y["ovr_total_campers"] == 10
    assert y["total_people"] == 30
    assert y["unique_groups"] == 2
    assert y["person_nights"] == 30  # 3 nights x 10 people

    # Live booking contributes its stored cost; archived cost is re-derived
    # (0 under the dummy test config) and flags the total as estimated
    assert y["income_day_p"] == 500
    assert y["income_estimated"] is True
    assert y["est_income_p"] == 500 + y["income_ovr_p"]

    # Monthly buckets: March (day) and June (overnight)
    assert y["monthly_people"][2] == 20
    assert y["monthly_people"][5] == 10
    assert sum(y["monthly_bookings"]) == 2

    assert y["avg_group_size"] == 15.0
    assert y["avg_stay_nights"] == 3.0
    assert y["median_lead_days"] == 23  # median of 10 and 36
    assert y["busiest_month"] == "March"
    assert y["busiest_night"]["people"] == 10
    assert "2025" in y["busiest_night"]["date"]

    assert y["facility_counts"] == [("Campfire Circle", 1), ("Roxby Hut", 1)]
    assert ["Camp Group", 1, 10] in y["top_groups"]
    assert ["Day Group", 1, 20] in y["top_groups"]

    # Oldest year has no previous year to compare against
    assert all(v is None for v in y["deltas"].values())


def test_get_year_report(stats_manager):
    assert stats_manager.get_year_report(2025)["year"] == 2025
    assert stats_manager.get_year_report(1999) is None


@pytest.fixture
def archive_manager(leader_data, monkeypatch):
    """A manager holding one of each case the archive sweep has to decide on,
    with saves/calendar/flash captured rather than performed."""
    import models.bookings as bookings_module

    from models.utils import now_uk

    now = now_uk()

    def _live(booking_id, status, days_past_departure):
        departing = now - timedelta(days=days_past_departure)
        return LiveBooking(
            booking=_mk_booking(
                id=booking_id,
                original_sheet_md5=f"md5-{booking_id}",
                arriving=(departing - timedelta(days=2)).isoformat(),
                departing=departing.isoformat(),
            ),
            leader=leader_data,
            tracking=TrackingData(status=status, cost_estimate=100, notes="private"),
        )

    manager = Bookings()
    manager.live = LiveData(
        items=[
            _live("OLD-COMPLETED", "Completed", 91),
            _live("OLD-CANCELLED", "Cancelled", 91),
            _live("NEW-COMPLETED", "Completed", 10),
            _live("OLD-INVOICE", "Invoice", 91),
        ]
    )
    manager.archive = ArchiveData(items=[])

    saved = []
    flashed = []
    monkeypatch.setattr(bookings_module, "save_json", lambda data, path: saved.append(path.name))
    monkeypatch.setattr(bookings_module, "delete_calendar_entry", lambda rec: None)
    monkeypatch.setattr(bookings_module, "flash", lambda msg, cat=None: flashed.append(msg))

    return manager, saved, flashed


def test_archive_old_bookings_moves_completed_and_deletes_cancelled(archive_manager):
    manager, saved, _ = archive_manager

    result = manager.archive_old_bookings()

    assert result == {"archived": 1, "deleted": 1}

    # Only the two in-date/other-status bookings stay live
    assert [rec.booking.id for rec in manager.live.items] == ["NEW-COMPLETED", "OLD-INVOICE"]

    # The archived copy keeps the booking and nothing else (GDPR)
    assert [b.id for b in manager.archive.items] == ["OLD-COMPLETED"]
    assert isinstance(manager.archive.items[0], BookingData)
    assert not hasattr(manager.archive.items[0], "leader")
    assert not hasattr(manager.archive.items[0], "tracking")

    assert set(saved) == {"bookings.json", "archive.json"}


def test_archive_old_bookings_persists_cancelled_only_run(archive_manager):
    """Regression: a sweep that only deletes cancelled bookings must still save
    the live file, or the deletions come back on the next restart."""
    manager, saved, _ = archive_manager
    manager.live.items = [rec for rec in manager.live.items if rec.booking.id == "OLD-CANCELLED"]

    result = manager.archive_old_bookings()

    assert result == {"archived": 0, "deleted": 1}
    assert manager.live.items == []
    assert saved == ["bookings.json"]  # live saved, archive untouched


def test_archive_old_bookings_no_op_saves_nothing(archive_manager):
    manager, saved, _ = archive_manager
    manager.live.items = [rec for rec in manager.live.items if rec.booking.id == "NEW-COMPLETED"]

    assert manager.archive_old_bookings() == {"archived": 0, "deleted": 0}
    assert saved == []


def test_auto_archive_runs_once_a_day(archive_manager):
    manager, saved, flashed = archive_manager

    manager.auto_archive_old_bookings()
    assert len(flashed) == 1
    assert "1 booking(s) archived" in flashed[0]

    # Second call the same day does nothing at all
    saved.clear()
    manager.auto_archive_old_bookings()
    assert saved == []
    assert len(flashed) == 1


def test_auto_archive_stays_quiet_when_nothing_to_do(archive_manager):
    manager, _, flashed = archive_manager
    manager.live.items = [rec for rec in manager.live.items if rec.booking.id == "NEW-COMPLETED"]

    manager.auto_archive_old_bookings()
    assert flashed == []


def test_get_archive_list_filters_by_year_newest_first(setup_bookings):
    manager = setup_bookings
    manager.archive = ArchiveData(
        items=[
            _mk_booking(id="A-2024", arriving="2024-06-01T18:00:00", departing="2024-06-02T10:00:00"),
            _mk_booking(id="B-2025", arriving="2025-03-01T18:00:00", departing="2025-03-02T10:00:00"),
            _mk_booking(id="C-2025", arriving="2025-09-01T18:00:00", departing="2025-09-02T10:00:00"),
        ]
    )

    assert [b.id for b in manager.get_archive_list()] == ["C-2025", "B-2025", "A-2024"]
    assert [b.id for b in manager.get_archive_list(year=2025)] == ["C-2025", "B-2025"]
    assert manager.get_archive_list(year=1999) == []
    assert manager.get_archive_year_counts() == {2025: 2, 2024: 1}
