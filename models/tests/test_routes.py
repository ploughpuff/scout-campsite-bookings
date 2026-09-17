"""
test_routes.py - The admin routes added for the outbox, and the ones that must
not do damage on a GET.
"""

# pylint: disable=all
import os

# Set before importing app: app.py decides at import time whether to start the
# background scheduler, and a test run must not reach out to Google Sheets.
os.environ["APP_ENV"] = "test"

import pytest  # pylint: disable=wrong-import-position

import app as flask_app  # pylint: disable=wrong-import-position
from models import outbox  # pylint: disable=wrong-import-position
from models.net import Retryable  # pylint: disable=wrong-import-position


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(outbox, "OUTBOX_FILE_PATH", tmp_path / "outbox.json")
    monkeypatch.setattr(outbox, "OUTBOX_PAYLOAD_DIR", tmp_path / "payloads")
    monkeypatch.setattr(outbox, "_handlers", {})
    outbox.reset_for_test()
    yield
    outbox.reset_for_test()


@pytest.fixture
def client():
    with flask_app.app.test_client() as test_client:
        yield test_client


#
## Fix-It rewrites bookings.json and deletes calendar events, so a browser
## prefetch or a revisited history entry must never be able to trigger it.
def test_fix_cal_events_refuses_a_get(client):
    assert client.get("/admin/fix_cal_events").status_code == 405


def test_listing_cal_events_is_safe_to_load(client, monkeypatch):
    """The read-only view must not queue anything, however it is reached."""
    monkeypatch.setattr(
        flask_app.bookings,
        "fix_cal_events",
        lambda dry_run=True: {"good": [], "missing": [], "delete": [], "extra": []},
    )

    assert client.get("/admin/list_cal_events").status_code == 200
    assert outbox.items() == []


def test_a_calendar_outage_redirects_instead_of_500ing(client, monkeypatch):
    """It used to feed None into a set comprehension and die on the 500 page."""

    def unreachable(dry_run=True):
        raise Retryable("[Errno -3] Temporary failure in name resolution")

    monkeypatch.setattr(flask_app.bookings, "fix_cal_events", unreachable)

    response = client.get("/admin/list_cal_events")

    assert response.status_code == 302
    assert "/admin" in response.headers["Location"]


#
## Managing stuck work
def test_discarding_needs_a_post(client):
    assert client.get("/admin/outbox/discard/anything").status_code == 405


def test_discarding_removes_the_item(client):
    outbox._handlers["cal_delete"] = lambda item: (_ for _ in ()).throw(Retryable("dns"))
    outbox.enqueue("cal_delete", {"event_id": "abc"}, booking_id="CDS-1")
    item_id = outbox.items()[0].id

    response = client.post(f"/admin/outbox/discard/{item_id}")

    assert response.status_code == 302
    assert outbox.items() == []


def test_discarding_something_already_gone_is_harmless(client):
    response = client.post("/admin/outbox/discard/not-a-real-id")

    assert response.status_code == 302


def test_retry_now_sends_an_item_that_can_get_through(client):
    attempts = []

    def works_second_time(item):
        attempts.append(1)
        if len(attempts) == 1:
            raise Retryable("dns")

    outbox._handlers["cal_delete"] = works_second_time
    outbox.enqueue("cal_delete", {"event_id": "abc"}, booking_id="CDS-1")
    item_id = outbox.items()[0].id

    response = client.post(f"/admin/outbox/retry/{item_id}")

    assert response.status_code == 302
    assert outbox.items() == []


def test_retry_now_leaves_a_still_broken_item_queued(client):
    outbox._handlers["cal_delete"] = lambda item: (_ for _ in ()).throw(Retryable("still down"))
    outbox.enqueue("cal_delete", {"event_id": "abc"}, booking_id="CDS-1")
    item_id = outbox.items()[0].id

    client.post(f"/admin/outbox/retry/{item_id}")

    assert outbox.pending_count() == 1


def test_the_admin_page_shows_outstanding_work(client):
    """Regression: the summary key was called "items", which Jinja resolved to
    the dict's own .items method instead of the list."""
    outbox._handlers["email"] = lambda item: (_ for _ in ()).throw(Retryable("dns"))
    outbox.enqueue("email", {"recipient": "leader@example.com"}, booking_id="CDS-2026-0250")

    body = client.get("/admin").get_data(as_text=True)

    assert "CDS-2026-0250" in body
    assert "Outbox" in body
