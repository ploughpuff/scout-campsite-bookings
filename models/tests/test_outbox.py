"""
test_outbox.py - Work that has to survive the network being down.

The queue exists because six calendar events were orphaned when a delete failed
during an outage and the booking was archived anyway, taking the event id with
it. These tests are mostly about that: does the work still exist afterwards.
"""

# pylint: disable=all
from datetime import timedelta

import pytest

from models import outbox
from models.net import Permanent, Retryable
from models.schemas import OutboxData, OutboxItem
from models.utils import now_uk


@pytest.fixture(autouse=True)
def isolated_outbox(tmp_path, monkeypatch):
    """A queue of its own, on disk, so durability can actually be tested."""
    monkeypatch.setattr(outbox, "OUTBOX_FILE_PATH", tmp_path / "outbox.json")
    monkeypatch.setattr(outbox, "OUTBOX_PAYLOAD_DIR", tmp_path / "payloads")
    monkeypatch.setattr(outbox, "_handlers", {})
    outbox.reset_for_test()
    yield tmp_path
    outbox.reset_for_test()


def _handler(kind, func):
    outbox._handlers[kind] = func


#
## The happy path
def test_work_that_succeeds_leaves_the_queue():
    done = []
    _handler("cal_delete", lambda item: done.append(item.payload["event_id"]))

    outbox.enqueue("cal_delete", {"event_id": "abc"}, booking_id="CDS-2026-0117")

    assert done == ["abc"]
    assert outbox.pending_count() == 0


def test_enqueue_can_hold_off_sending():
    """The scheduler path queues without attempting, so a drain does the work."""
    tried = []
    _handler("cal_delete", lambda item: tried.append(item.id))

    outbox.enqueue("cal_delete", {"event_id": "abc"}, send_now=False)

    assert tried == []
    assert outbox.pending_count() == 1


#
## The failure that started all this
def test_an_outage_leaves_the_work_queued():
    def dns_is_down(item):
        raise Retryable("[Errno -3] Temporary failure in name resolution")

    _handler("cal_delete", dns_is_down)

    outbox.enqueue("cal_delete", {"event_id": "abc"}, booking_id="CDS-2026-0117")

    assert outbox.pending_count() == 1
    assert "name resolution" in outbox.items()[0].last_error


def test_the_event_id_survives_a_restart(isolated_outbox):
    """The whole point. The id used to live only on a record that got archived."""

    def dns_is_down(item):
        raise Retryable("dns")

    _handler("cal_delete", dns_is_down)
    outbox.enqueue("cal_delete", {"event_id": "j6eo3ork9bno"}, booking_id="CDS-2026-0117")

    #
    ## Forget everything we know and read the file back, as a restart would.
    outbox._data = None

    survivor = outbox.items()[0]
    assert survivor.payload["event_id"] == "j6eo3ork9bno"
    assert survivor.booking_id == "CDS-2026-0117"


def test_work_resumes_when_the_network_comes_back():
    attempts = []

    def flaky(item):
        attempts.append(1)
        if len(attempts) == 1:
            raise Retryable("dns")

    _handler("cal_delete", flaky)
    outbox.enqueue("cal_delete", {"event_id": "abc"})

    #
    ## Backoff means it is not due yet; the scheduler would skip it.
    assert outbox.drain() == {"sent": 0, "retry": 0, "blocked": 0}

    outbox.items()[0].next_attempt_at = now_uk() - timedelta(seconds=1)

    assert outbox.drain()["sent"] == 1
    assert outbox.pending_count() == 0


#
## Backing off, and knowing when to stop
def test_each_failure_waits_longer_than_the_last():
    _handler("cal_delete", lambda item: (_ for _ in ()).throw(Retryable("dns")))
    outbox.enqueue("cal_delete", {"event_id": "abc"})
    item = outbox.items()[0]

    waits = []
    for _ in range(4):
        before = now_uk()
        item.next_attempt_at = before - timedelta(seconds=1)
        outbox.drain()
        waits.append(item.next_attempt_at - before)

    assert waits == sorted(waits)
    assert waits[-1] > waits[0]


def test_backoff_is_capped():
    """A fortnight offline must not schedule the next try for next year."""
    assert outbox._backoff(50) <= outbox.MAX_BACKOFF * (1 + outbox.JITTER)


def test_an_item_that_never_gets_through_ends_up_blocked():
    _handler("cal_delete", lambda item: (_ for _ in ()).throw(Retryable("dns")))
    outbox.enqueue("cal_delete", {"event_id": "abc"})

    item = outbox.items()[0]
    item.created_at = now_uk() - outbox.GIVE_UP_AFTER - timedelta(minutes=1)
    item.next_attempt_at = now_uk() - timedelta(seconds=1)

    assert outbox.drain()["blocked"] == 1
    assert outbox.items()[0].state == "blocked"
    assert outbox.pending_count() == 0
    assert outbox.blocked_count() == 1


def test_a_blocked_item_is_kept_not_dropped():
    """Losing it silently is the failure mode we are trying to end."""
    _handler("email", lambda item: (_ for _ in ()).throw(Permanent("mailbox does not exist")))

    outbox.enqueue("email", {"to": "nobody@example.com"}, booking_id="CDS-2026-0117")

    assert outbox.blocked_count() == 1
    assert "mailbox does not exist" in outbox.items()[0].last_error


def test_a_refusal_is_not_retried():
    calls = []

    def refused(item):
        calls.append(1)
        raise Permanent("403 forbidden")

    _handler("cal_delete", refused)
    outbox.enqueue("cal_delete", {"event_id": "abc"})
    outbox.drain()  # blocked items are not due, and never become due on their own

    assert calls == [1]


#
## Idempotency and ordering
def test_a_handler_can_say_there_was_nothing_left_to_do():
    """An event already gone is a success, not a failure to retry forever."""

    def already_gone(item):
        raise outbox.Skip("410 Gone")

    _handler("cal_delete", already_gone)
    outbox.enqueue("cal_delete", {"event_id": "abc"})

    assert outbox.pending_count() == 0
    assert outbox.items() == []


def test_one_bookings_work_stays_in_order():
    """A delete must never overtake the create that is stuck in front of it."""
    order = []

    def only_deletes_work(item):
        order.append(item.kind)
        if item.kind == "cal_upsert":
            raise Retryable("dns")

    _handler("cal_upsert", only_deletes_work)
    _handler("cal_delete", only_deletes_work)

    outbox.enqueue("cal_upsert", {}, booking_id="CDS-1", send_now=False)
    outbox.enqueue("cal_delete", {"event_id": "x"}, booking_id="CDS-1", send_now=False)

    outbox.drain()

    assert order == ["cal_upsert"]  # the delete was held back
    assert outbox.pending_count() == 2


def test_a_stuck_booking_does_not_hold_up_anyone_else():
    done = []

    def selective(item):
        if item.booking_id == "CDS-1":
            raise Retryable("dns")
        done.append(item.booking_id)

    _handler("cal_delete", selective)
    outbox.enqueue("cal_delete", {}, booking_id="CDS-1", send_now=False)
    outbox.enqueue("cal_delete", {}, booking_id="CDS-2", send_now=False)

    outbox.drain()

    assert done == ["CDS-2"]


#
## Follow-on work, for anything that may only happen once something else has
## been confirmed. Telling Xero an invoice was sent is why this exists.
def _with_follow_on(payload=None):
    return {
        **(payload or {}),
        "then": [{"kind": "xero_email", "payload": {"invoice_id": "inv-guid"}}],
    }


def test_a_follow_on_is_queued_once_the_work_it_waited_on_succeeds():
    _handler("email", lambda item: None)

    outbox.enqueue("email", _with_follow_on({"recipient": "jane@example.com"}),
                   booking_id="CDS-1")

    waiting = outbox.items()
    assert [i.kind for i in waiting] == ["xero_email"]
    assert waiting[0].payload == {"invoice_id": "inv-guid"}


def test_a_follow_on_inherits_the_booking_it_belongs_to():
    """It has to, or drain() cannot keep it behind that booking's other work."""
    _handler("email", lambda item: None)

    outbox.enqueue("email", _with_follow_on(), booking_id="CDS-1")

    assert outbox.items()[0].booking_id == "CDS-1"


def test_an_outage_queues_no_follow_on():
    """The email is still owed, so nothing may act as though it had gone."""

    def no_answer(item):
        raise Retryable("dns")

    _handler("email", no_answer)
    outbox.enqueue("email", _with_follow_on(), booking_id="CDS-1")

    assert [i.kind for i in outbox.items()] == ["email"]


def test_a_refused_email_queues_no_follow_on():
    """A bad address must never leave Xero reminding someone who got nothing."""

    def refused(item):
        raise Permanent("550 no such mailbox")

    _handler("email", refused)
    outbox.enqueue("email", _with_follow_on(), booking_id="CDS-1")

    remaining = outbox.items()
    assert [i.kind for i in remaining] == ["email"]
    assert remaining[0].state == "blocked"


def test_work_that_was_already_done_still_queues_its_follow_on():
    """Skip means the far end already reflects what we wanted - so it happened."""

    def already_gone(item):
        raise outbox.Skip("nothing left to do")

    _handler("email", already_gone)
    outbox.enqueue("email", _with_follow_on(), booking_id="CDS-1")

    assert [i.kind for i in outbox.items()] == ["xero_email"]


def test_a_follow_on_waits_for_the_next_drain():
    """Queued, not run from inside the attempt that spawned it."""
    done = []
    _handler("email", lambda item: None)
    _handler("xero_email", lambda item: done.append(item.payload["invoice_id"]))

    outbox.enqueue("email", _with_follow_on(), booking_id="CDS-1", send_now=False)

    outbox.drain()
    assert done == []

    outbox.drain()
    assert done == ["inv-guid"]
    assert outbox.items() == []


#
## Counting the attempt before making it, so a crash cannot loop
def test_the_attempt_is_counted_before_it_is_made(isolated_outbox):
    """A crash mid-send must come back to an item scheduled for later, not one
    that retries instantly and forever."""

    def crash(item):
        #
        ## What the file looks like at the moment of the crash.
        import json

        stored = json.loads((isolated_outbox / "outbox.json").read_text())
        assert stored["items"][0]["attempts"] == 1
        assert stored["items"][0]["next_attempt_at"] > now_uk().isoformat()
        raise Retryable("died here")

    _handler("cal_delete", crash)
    outbox.enqueue("cal_delete", {"event_id": "abc"})

    assert outbox.items()[0].attempts == 1


#
## Payload files, for anything too big to sit in a queue rewritten this often
def test_a_payload_file_round_trips_and_is_cleaned_up(isolated_outbox):
    sent = []

    def send(item):
        sent.append(outbox.read_payload(item.id))

    _handler("email", send)

    item_id = "abc123"
    outbox.write_payload(item_id, b"Subject: hello\n\nbody")
    assert outbox.payload_path(item_id).exists()

    outbox.enqueue("email", {"to": "a@b.c"}, send_now=False)
    queued = outbox.items()[0]
    outbox.write_payload(queued.id, b"Subject: real\n\nbody")

    outbox.drain()

    assert sent == [b"Subject: real\n\nbody"]
    assert not outbox.payload_path(queued.id).exists()  # gone with the item


#
## Admin actions
def test_discarding_removes_the_item_and_its_payload():
    _handler("email", lambda item: (_ for _ in ()).throw(Permanent("no")))
    outbox.enqueue("email", {"to": "a@b.c"})
    item = outbox.items()[0]
    outbox.write_payload(item.id, b"body")

    assert outbox.discard(item.id) is True
    assert outbox.items() == []
    assert not outbox.payload_path(item.id).exists()
    assert outbox.discard("not-a-real-id") is False


def test_retry_now_puts_a_blocked_item_back_in_the_queue():
    calls = []

    def refused_then_fine(item):
        calls.append(1)
        if len(calls) == 1:
            raise Permanent("403")

    _handler("cal_delete", refused_then_fine)
    outbox.enqueue("cal_delete", {"event_id": "abc"})
    item = outbox.items()[0]
    assert item.state == "blocked"

    assert outbox.retry_now(item.id) is True
    assert outbox.drain()["sent"] == 1


def test_oldest_pending_is_what_the_admin_page_shows():
    _handler("cal_delete", lambda item: (_ for _ in ()).throw(Retryable("dns")))
    outbox.enqueue("cal_delete", {}, booking_id="first")
    outbox.enqueue("cal_delete", {}, booking_id="second")

    assert outbox.oldest_pending().booking_id == "first"


def test_an_empty_queue_has_no_oldest():
    assert outbox.oldest_pending() is None


#
## The regression this whole thing exists for
def _completed_long_ago(event_id):
    """A booking due for the 90-day sweep, with an event on the calendar."""
    from models.schemas import BookingData, LeaderData, LiveBooking, TrackingData

    departing = now_uk() - timedelta(days=91)
    return LiveBooking(
        booking=BookingData(
            id="CDS-2026-0117",
            original_sheet_md5="md5-0117",
            group_type="Other Scout Group",
            group_name="Bicknacre Priory Scout Group",
            group_size=15,
            event_type="eve",
            submitted=(departing - timedelta(days=30)).isoformat(),
            arriving=(departing - timedelta(hours=4)).isoformat(),
            departing=departing.isoformat(),
            facilities=[],
        ),
        leader=LeaderData(name="Sarah Ryan", email="s@example.com", phone="0", address="hut"),
        tracking=TrackingData(
            status="Completed", cost_estimate=0, notes="", google_calendar_id=event_id
        ),
    )


def test_archiving_during_an_outage_keeps_the_calendar_delete(monkeypatch):
    """June 2026, six times over: the sweep asked Google to delete the event, DNS
    was down, the delete quietly failed, and the booking was archived anyway -
    taking the only copy of the event id with it. The delete must outlive the
    record it came from."""
    import models.bookings as bookings_module
    from models import calendar as calendar_module
    from models.bookings import Bookings
    from models.schemas import ArchiveData, LiveData
    from models.run_state import RunState

    #
    ## Google is unreachable, exactly as it was on the night.
    def dns_is_down(*args, **kwargs):
        raise OSError(-3, "Temporary failure in name resolution")

    monkeypatch.setattr(calendar_module, "_build_service", dns_is_down)
    monkeypatch.setattr(bookings_module, "save_json", lambda data, path: None)
    monkeypatch.setattr(bookings_module, "save_run_state", lambda state: None)
    monkeypatch.setattr(bookings_module, "flash", lambda msg, cat=None: None)
    outbox._handlers["cal_delete"] = calendar_module._handle_delete

    manager = Bookings()
    manager.live = LiveData(items=[_completed_long_ago("j6eo3ork9bno7bqp2epqndu30s")])
    manager.archive = ArchiveData(items=[])
    manager.run_state = RunState()

    result = manager.archive_old_bookings()

    #
    ## Personal data still stripped on time - that part was never in question.
    assert result["archived"] == 1
    assert manager.live.items == []
    assert [b.id for b in manager.archive.items] == ["CDS-2026-0117"]

    #
    ## ...and the delete is still outstanding, with everything needed to finish
    ## it, even though the record that knew the event id has gone.
    queued = outbox.items()
    assert len(queued) == 1
    assert queued[0].kind == "cal_delete"
    assert queued[0].payload["event_id"] == "j6eo3ork9bno7bqp2epqndu30s"
    assert queued[0].payload["booking_id"] == "CDS-2026-0117"
    assert queued[0].state == "pending"


#
## Robustness of the queue file itself
def test_an_unreadable_queue_does_not_stop_the_app(isolated_outbox):
    (isolated_outbox / "outbox.json").write_text("{ not json", encoding="utf-8")
    outbox._data = None

    assert outbox.items() == []  # logged loudly, but the app still starts


def test_work_with_no_handler_is_blocked_rather_than_lost():
    outbox.enqueue("cal_delete", {"event_id": "abc"})

    assert outbox.blocked_count() == 1
    assert "No handler" in outbox.items()[0].last_error
