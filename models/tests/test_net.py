"""
test_net.py - Telling "we never reached them" apart from "they said no".

The app used to collapse the two, which is how a Sheets outage came to be
recorded as a successful pull of zero bookings, and how a failed calendar
delete came to look exactly like a successful one.
"""

# pylint: disable=all
import socket
import ssl
from unittest.mock import MagicMock

import httplib2
import pytest
import requests
from googleapiclient.errors import HttpError

import models.bookings as bookings_module
import models.calendar as calendar_module
import models.sheets as sheets_module
from models.bookings import Bookings
from models.net import Permanent, Retryable, classify, status_of
from models.run_state import RunState
from models.schemas import ArchiveData, LiveData


def _http_error(status: int) -> HttpError:
    """An HttpError shaped the way googleapiclient really builds them."""
    resp = MagicMock()
    resp.status = status
    resp.reason = "because"
    return HttpError(resp, b'{"error": {"message": "nope"}}')


#
## classify - the taxonomy itself
@pytest.mark.parametrize(
    "exc",
    [
        #
        ## The exact shape the NAS produced every time it orphaned an event.
        socket.gaierror(-3, "Temporary failure in name resolution"),
        httplib2.ServerNotFoundError("Unable to find the server"),
        TimeoutError("The read operation timed out"),
        ssl.SSLError("_ssl.c:1015: The handshake operation timed out"),
        ConnectionResetError("Connection reset by peer"),
        ConnectionRefusedError("Connection refused"),
        OSError(99, "Cannot assign requested address"),
        requests.exceptions.ConnectionError("dns went away"),
        requests.exceptions.Timeout("too slow"),
        _http_error(500),
        _http_error(503),
        _http_error(429),
        _http_error(408),
    ],
)
def test_no_usable_answer_is_retryable(exc):
    assert isinstance(classify(exc), Retryable)


@pytest.mark.parametrize(
    "exc",
    [
        _http_error(400),
        _http_error(401),
        _http_error(403),
        _http_error(404),
        ValueError("that is not json"),
        KeyError("address"),
    ],
)
def test_a_refusal_is_permanent(exc):
    assert isinstance(classify(exc), Permanent)


def test_classify_keeps_the_original_message():
    """The admin page shows this text, so it has to say what actually happened."""
    error = classify(socket.gaierror(-3, "Temporary failure in name resolution"))

    assert "Temporary failure in name resolution" in str(error)


def test_status_of_reads_googles_shape_and_requests_shape():
    assert status_of(_http_error(404)) == 404

    resp = MagicMock()
    resp.status_code = 503
    assert status_of(requests.exceptions.HTTPError(response=resp)) == 503

    assert status_of(ValueError("no status here")) is None


#
## The regression this was all for: a Sheets outage must not read as success
def _sheets_that_answer(monkeypatch, **execute):
    """Point models.sheets at a Google client with a canned execute()."""
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.get.return_value.execute.configure_mock(
        **execute
    )
    monkeypatch.setattr(sheets_module, "google_service", lambda *a, **k: service)


def _fetch(monkeypatch, **execute):
    """One sheet fetch. Called directly rather than through get_sheet_data(),
    because the checked-in dev field_mappings has every sheet turned off."""
    _sheets_that_answer(monkeypatch, **execute)
    return sheets_module._fetch_google_sheets_data("sheet-id", "2026!A:E")


def test_a_sheets_outage_is_not_an_empty_sheet(monkeypatch):
    """_fetch_google_sheets_data returned [] for both, so a 503 looked like a
    sheet with no new bookings on it."""
    with pytest.raises(Retryable):
        _fetch(monkeypatch, side_effect=_http_error(503))


def test_a_sheets_dns_failure_is_retryable(monkeypatch):
    """The failure that actually happened, and it is not an HttpError."""
    with pytest.raises(Retryable):
        _fetch(monkeypatch, side_effect=socket.gaierror(-3, "Temporary failure"))


def test_a_sheets_refusal_is_permanent(monkeypatch):
    """A revoked share (403) will refuse again just as fast next time."""
    with pytest.raises(Permanent):
        _fetch(monkeypatch, side_effect=_http_error(403))


def test_an_empty_sheet_is_still_an_empty_sheet(monkeypatch):
    """The other half of the distinction: no rows must stay a quiet success."""
    assert _fetch(monkeypatch, return_value={"values": []}) == []


def test_rows_come_back_keyed_by_the_header_row(monkeypatch):
    rows = _fetch(
        monkeypatch,
        return_value={"values": [["name", "size"], ["1st Danbury", "34"]]},
    )

    assert rows == [{"name": "1st Danbury", "size": "34"}]


#
## Calendar: listing used to return None on an HttpError, and the only caller
## fed that straight into a set comprehension
def _calendar_that_raises(monkeypatch, exc):
    service = MagicMock()
    service.events.return_value.list.return_value.execute.side_effect = exc
    monkeypatch.setattr(calendar_module, "google_service", lambda *a, **k: service)


def test_listing_events_raises_rather_than_returning_none(monkeypatch):
    """Returning None turned a Calendar outage into an unexplained TypeError."""
    _calendar_that_raises(monkeypatch, _http_error(503))

    with pytest.raises(Retryable):
        calendar_module.get_cal_events()


def test_listing_events_survives_dns_going_away(monkeypatch):
    _calendar_that_raises(monkeypatch, socket.gaierror(-3, "Temporary failure"))

    with pytest.raises(Retryable):
        calendar_module.get_cal_events()


def test_listing_events_pages_through_every_result(monkeypatch):
    """Paging is the reason the caller gets one flat list; keep it working."""
    service = MagicMock()
    service.events.return_value.list.return_value.execute.side_effect = [
        {"items": [{"id": "a"}], "nextPageToken": "more"},
        {"items": [{"id": "b"}]},
    ]
    monkeypatch.setattr(calendar_module, "google_service", lambda *a, **k: service)

    assert [e["id"] for e in calendar_module.get_cal_events()] == ["a", "b"]


def test_a_failed_sheets_pull_is_recorded_as_failed(monkeypatch):
    """The whole chain: a 503 from Google has to reach run_state as pull_ok=False,
    or /admin shows a green tick while booking forms stop arriving."""
    saved, states = [], []
    monkeypatch.setattr(bookings_module, "save_json", lambda data, path: saved.append(path.name))
    monkeypatch.setattr(bookings_module, "save_run_state", lambda state: states.append(state))

    manager = Bookings()
    manager.live = LiveData(items=[])
    manager.archive = ArchiveData(items=[])
    manager.run_state = RunState()

    def outage():
        raise classify(_http_error(503))

    monkeypatch.setattr(bookings_module, "get_sheet_data", outage)

    with pytest.raises(Retryable):
        manager.pull_from_sheets()

    assert manager.run_state.pull_ok is False
    assert "503" in manager.run_state.pull_error
    assert saved == []  # nothing pulled, so nothing written
