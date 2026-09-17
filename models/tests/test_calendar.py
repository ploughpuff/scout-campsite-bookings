"""
test_calendar.py - The calendar handlers, and the reconciliation that finds
events nothing points at any more.
"""

# pylint: disable=all
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

import models.bookings as bookings_module
from models import calendar as calendar_module
from models import outbox
from models.bookings import Bookings
from models.net import Retryable
from models.run_state import RunState
from models.schemas import (
    ArchiveData,
    BookingData,
    LeaderData,
    LiveBooking,
    LiveData,
    TrackingData,
)
from models.utils import now_uk


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(outbox, "OUTBOX_FILE_PATH", tmp_path / "outbox.json")
    monkeypatch.setattr(outbox, "OUTBOX_PAYLOAD_DIR", tmp_path / "payloads")
    monkeypatch.setattr(outbox, "_handlers", {})
    monkeypatch.setattr(calendar_module, "_event_id_recorder", None)
    outbox.reset_for_test()
    outbox._handlers["cal_upsert"] = calendar_module._handle_upsert
    outbox._handlers["cal_delete"] = calendar_module._handle_delete
    yield
    outbox.reset_for_test()


def _google(monkeypatch, **behaviour):
    """A stand-in Calendar client. Returns the mock so calls can be inspected."""
    service = MagicMock()
    events = service.events.return_value
    events.list.return_value.execute.return_value = behaviour.get("list", {"items": []})
    events.update.return_value.execute.return_value = {"id": "evt", "htmlLink": "http://g/u"}
    events.insert.return_value.execute.return_value = {"id": "new-evt", "htmlLink": "http://g/i"}
    if "delete_raises" in behaviour:
        events.delete.return_value.execute.side_effect = behaviour["delete_raises"]
    monkeypatch.setattr(calendar_module, "_build_service", lambda: service)
    return service


def _http_error(status):
    resp = MagicMock()
    resp.status = status
    resp.reason = "because"
    return HttpError(resp, b"nope")


#
## The upsert has to be safe to repeat: a Retryable failure means we never
## learned whether the write landed.
def test_an_upsert_updates_the_event_it_may_have_just_created(monkeypatch):
    service = _google(monkeypatch, list={"items": [{"id": "existing-evt"}]})

    outbox.enqueue("cal_upsert", {"booking_id": "CDS-1", "event": {"summary": "x"}})

    assert service.events.return_value.insert.called is False
    assert service.events.return_value.update.called is True
    assert outbox.items() == []


def test_an_upsert_creates_the_event_when_there_is_none(monkeypatch):
    service = _google(monkeypatch, list={"items": []})

    outbox.enqueue("cal_upsert", {"booking_id": "CDS-1", "event": {"summary": "x"}})

    assert service.events.return_value.insert.called is True
    assert outbox.items() == []


def test_the_event_is_found_by_booking_id_not_by_a_stored_id(monkeypatch):
    """The stored id is a cache the archive sweep can destroy; the property on
    the event itself is the thing that survives."""
    service = _google(monkeypatch, list={"items": [{"id": "existing-evt"}]})

    outbox.enqueue("cal_upsert", {"booking_id": "CDS-2026-0117", "event": {}})

    _, kwargs = service.events.return_value.list.call_args
    assert kwargs["privateExtendedProperty"] == "booking_id=CDS-2026-0117"


def test_a_confirmed_event_id_is_handed_back_to_the_booking(monkeypatch):
    _google(monkeypatch, list={"items": [{"id": "existing-evt"}]})
    recorded = []
    calendar_module.set_event_id_recorder(lambda bid, eid: recorded.append((bid, eid)))

    outbox.enqueue("cal_upsert", {"booking_id": "CDS-1", "event": {}})

    #
    ## The id comes from Google's reply, not from the lookup: the reply is
    ## what actually happened.
    assert recorded == [("CDS-1", "evt")]


def test_an_outage_during_an_upsert_leaves_it_queued(monkeypatch):
    monkeypatch.setattr(
        calendar_module,
        "_build_service",
        lambda: (_ for _ in ()).throw(OSError(-3, "Temporary failure in name resolution")),
    )

    outbox.enqueue("cal_upsert", {"booking_id": "CDS-1", "event": {}})

    assert outbox.pending_count() == 1


#
## Deleting
def test_a_delete_falls_back_to_the_booking_id(monkeypatch):
    """No stored id - which is exactly the state the orphans were left in."""
    service = _google(monkeypatch, list={"items": [{"id": "found-by-property"}]})

    outbox.enqueue("cal_delete", {"event_id": None, "booking_id": "CDS-2026-0117"})

    _, kwargs = service.events.return_value.delete.call_args
    assert kwargs["eventId"] == "found-by-property"
    assert outbox.items() == []


@pytest.mark.parametrize("status", [404, 410])
def test_an_event_already_gone_counts_as_done(monkeypatch, status):
    """Otherwise the queue would retry a delete that can never succeed."""
    _google(monkeypatch, delete_raises=_http_error(status))

    outbox.enqueue("cal_delete", {"event_id": "abc", "booking_id": "CDS-1"})

    assert outbox.items() == []


def test_nothing_on_the_calendar_at_all_counts_as_done(monkeypatch):
    _google(monkeypatch, list={"items": []})

    outbox.enqueue("cal_delete", {"event_id": None, "booking_id": "CDS-1"})

    assert outbox.items() == []


def test_a_delete_refused_by_google_is_blocked_not_retried(monkeypatch):
    _google(monkeypatch, delete_raises=_http_error(403))

    outbox.enqueue("cal_delete", {"event_id": "abc", "booking_id": "CDS-1"})

    assert outbox.blocked_count() == 1


def test_a_confirmed_delete_clears_the_cached_id(monkeypatch):
    _google(monkeypatch)
    recorded = []
    calendar_module.set_event_id_recorder(lambda bid, eid: recorded.append((bid, eid)))

    outbox.enqueue("cal_delete", {"event_id": "abc", "booking_id": "CDS-1"})

    assert recorded == [("CDS-1", None)]


def test_a_broken_recorder_does_not_undo_a_write_that_worked(monkeypatch):
    """Caching the id is a convenience; failing at it must not cause a retry."""
    _google(monkeypatch, list={"items": [{"id": "evt"}]})
    calendar_module.set_event_id_recorder(
        lambda bid, eid: (_ for _ in ()).throw(RuntimeError("store is busy"))
    )

    outbox.enqueue("cal_upsert", {"booking_id": "CDS-1", "event": {}})

    assert outbox.items() == []


#
## Reconciliation
def _rec(booking_id, status, cal_id=""):
    departing = now_uk() - timedelta(days=5)
    return LiveBooking(
        booking=BookingData(
            id=booking_id,
            original_sheet_md5=f"md5-{booking_id}",
            group_type="Other Scout Group",
            group_name="G",
            group_size=5,
            event_type="eve",
            submitted=(departing - timedelta(days=30)).isoformat(),
            arriving=(departing - timedelta(hours=3)).isoformat(),
            departing=departing.isoformat(),
            facilities=[],
        ),
        leader=LeaderData(name="n", email="e@x.com", phone="1", address="a"),
        tracking=TrackingData(
            status=status, cost_estimate=0, notes="", google_calendar_id=cal_id
        ),
    )


def _stamped(event_id, booking_id):
    return {
        "id": event_id,
        "summary": "EVE: Campfire Circle",
        "htmlLink": f"http://g/{event_id}",
        "extendedProperties": {"private": {"booking_id": booking_id}},
    }


@pytest.fixture
def reconciler(monkeypatch):
    monkeypatch.setattr(bookings_module, "save_json", lambda data, path: None)
    manager = Bookings()
    manager.live = LiveData(
        items=[
            _rec("CDS-0250", "Confirmed", "evt-250"),
            _rec("CDS-0251", "Confirmed", ""),
            _rec("CDS-0252", "Cancelled", "evt-252"),
        ]
    )
    manager.archive = ArchiveData(items=[])
    manager.run_state = RunState()
    return manager


def _calendar_is(monkeypatch, events):
    monkeypatch.setattr(bookings_module, "get_cal_events", lambda: events)


def test_reconciliation_sorts_every_case(reconciler, monkeypatch):
    _calendar_is(
        monkeypatch,
        [
            _stamped("evt-250", "CDS-0250"),
            _stamped("evt-252", "CDS-0252"),
            {"id": "nobody-knows", "summary": "EVE", "htmlLink": "http://g/x"},
        ],
    )

    result = reconciler.fix_cal_events(dry_run=True)

    assert [r.booking.id for r in result["good"]] == ["CDS-0250"]
    assert [r.booking.id for r in result["missing"]] == ["CDS-0251"]
    assert [r.booking.id for r in result["delete"]] == ["CDS-0252"]
    assert [e["event_id"] for e in result["extra"]] == ["nobody-knows"]


def test_a_dry_run_changes_nothing(reconciler, monkeypatch):
    """It is reached by a plain page load, so it must never queue work."""
    _calendar_is(monkeypatch, [{"id": "nobody-knows", "summary": "EVE"}])

    reconciler.fix_cal_events(dry_run=True)

    assert outbox.items() == []


def test_an_extra_event_says_which_booking_it_came_from(reconciler, monkeypatch):
    """The six orphans showed as bare ids with an empty link. Anything the app
    created is stamped, so it can name itself even once its record has gone."""
    _calendar_is(monkeypatch, [_stamped("j6eo3ork9bno", "CDS-2026-0117")])

    extra = reconciler.fix_cal_events(dry_run=True)["extra"]

    assert extra[0]["booking_id"] == "CDS-2026-0117"
    assert extra[0]["html_link"] == "http://g/j6eo3ork9bno"


def test_fixing_queues_the_work_rather_than_doing_it_inline(reconciler, monkeypatch):
    _google(monkeypatch, list={"items": []})
    _calendar_is(
        monkeypatch,
        [_stamped("evt-252", "CDS-0252"), {"id": "nobody-knows", "summary": "EVE"}],
    )

    result = reconciler.fix_cal_events(dry_run=False)

    #
    ## Reported either way, so the admin flash can say how much it queued
    ## instead of always claiming zero. Both Confirmed bookings are missing
    ## here: the calendar only holds the stale Cancelled one.
    assert len(result["missing"]) == 2
    assert len(result["delete"]) == 1
    assert len(result["extra"]) == 1


def test_fixing_during_an_outage_keeps_every_change(reconciler, monkeypatch):
    """Pressing Fix-It while the network is down used to manufacture new
    orphans: it wiped the stored id whether or not the delete had worked."""
    monkeypatch.setattr(
        calendar_module,
        "_build_service",
        lambda: (_ for _ in ()).throw(OSError(-3, "Temporary failure in name resolution")),
    )
    _calendar_is(monkeypatch, [_stamped("evt-252", "CDS-0252")])

    reconciler.fix_cal_events(dry_run=False)

    cancelled = [r for r in reconciler.live.items if r.booking.id == "CDS-0252"][0]
    assert cancelled.tracking.google_calendar_id == "evt-252"  # not wiped
    #
    ## Two upserts for the Confirmed bookings with no event, plus the delete.
    assert outbox.pending_count() == 3
