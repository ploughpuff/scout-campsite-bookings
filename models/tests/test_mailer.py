"""
test_mailer.py - Queuing the leader's email, and the difference between "sent",
"not sent" and "switched off".
"""

# pylint: disable=all
import smtplib
from email.message import EmailMessage
from unittest.mock import MagicMock

import pytest

import config
from models import mailer, outbox
from models.net import Permanent, Retryable


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(outbox, "OUTBOX_FILE_PATH", tmp_path / "outbox.json")
    monkeypatch.setattr(outbox, "OUTBOX_PAYLOAD_DIR", tmp_path / "payloads")
    monkeypatch.setattr(outbox, "_handlers", {})
    monkeypatch.setattr(mailer, "flash", lambda msg, cat=None: None)
    monkeypatch.setattr(mailer, "is_email_enabled", lambda: True)
    outbox.reset_for_test()
    outbox._handlers["email"] = mailer._handle_email
    yield
    outbox.reset_for_test()


def _msg(subject="Booking CDS-1 CONFIRMED", to="leader@example.com"):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "site@example.com"
    msg["To"] = to
    msg.set_content("plain")
    msg.add_alternative("<p>html</p>", subtype="html")
    return msg


def _smtp(monkeypatch, **behaviour):
    """A stand-in SMTP server. Returns the mock so calls can be inspected."""
    server = MagicMock()
    if "send_raises" in behaviour:
        server.send_message.side_effect = behaviour["send_raises"]
    context = MagicMock()
    context.__enter__.return_value = server
    context.__exit__.side_effect = behaviour.get("exit_raises")
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: context)
    return server


#
## The bug that reported a delivered email as failed
def test_a_bad_goodbye_does_not_turn_a_sent_email_into_a_failure(monkeypatch, caplog):
    """Office365 answers 250 to QUIT; smtplib's context manager raises on any
    reply but 221. The leader got the mail, so retrying would send it twice."""
    server = _smtp(
        monkeypatch,
        exit_raises=smtplib.SMTPResponseException(250, b"2.0.0 OK"),
    )

    mailer._send_email(_msg(), "leader@example.com", booking_id="CDS-1")

    assert server.send_message.called  # it really did go out
    assert "session ended badly" in caplog.text  # and the raise really happened
    assert outbox.items() == []  # finished, not queued for another go


def test_a_send_that_really_failed_stays_queued(monkeypatch):
    _smtp(monkeypatch, send_raises=OSError(99, "Cannot assign requested address"))

    mailer._send_email(_msg(), "leader@example.com", booking_id="CDS-1")

    assert outbox.pending_count() == 1


#
## Telling the SMTP failure modes apart
def test_a_network_failure_is_retried(monkeypatch):
    _smtp(monkeypatch, send_raises=smtplib.SMTPConnectError(421, "busy"))

    mailer._send_email(_msg(), "leader@example.com")

    assert outbox.pending_count() == 1


def test_a_bad_address_is_not_retried(monkeypatch):
    """Delivering it again sends it to the same wrong place."""
    _smtp(
        monkeypatch,
        send_raises=smtplib.SMTPRecipientsRefused({"nope@example.com": (550, b"no such user")}),
    )

    mailer._send_email(_msg(), "nope@example.com")

    assert outbox.blocked_count() == 1


def test_a_rejected_login_is_put_in_front_of_a_human(monkeypatch):
    _smtp(monkeypatch, send_raises=smtplib.SMTPAuthenticationError(535, b"bad password"))

    mailer._send_email(_msg(), "leader@example.com")

    assert outbox.blocked_count() == 1


#
## Switched off is not the same as sent
def test_email_switched_off_reports_that_nothing_was_sent(monkeypatch):
    """This used to return True, so callers journalled "Email Sent" against
    mail that was never going anywhere."""
    monkeypatch.setattr(mailer, "is_email_enabled", lambda: False)

    assert mailer._send_email(_msg(), "leader@example.com") is False
    assert outbox.items() == []


#
## What actually gets queued
def test_the_message_is_frozen_at_the_moment_it_is_queued(monkeypatch):
    """A booking edited while the queue is backed up must not change what the
    leader is told - so the rendered bytes are stored, not the record."""
    sent = []
    _smtp(monkeypatch)
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: _capture(sent))

    mailer._send_email(_msg(subject="Original subject"), "leader@example.com", booking_id="CDS-1")

    assert sent[0]["Subject"] == "Original subject"


def _capture(sink):
    server = MagicMock()
    server.send_message.side_effect = lambda msg, **kw: sink.append(msg)
    context = MagicMock()
    context.__enter__.return_value = server
    context.__exit__.return_value = False
    return context


def test_work_waiting_on_the_email_only_runs_once_it_has_gone(monkeypatch):
    """Telling Xero an invoice was sent has to follow the sending, not precede it."""
    marked = []
    outbox._handlers["xero_email"] = lambda item: marked.append(item.payload["invoice_id"])
    then = [{"kind": "xero_email", "payload": {"invoice_id": "inv-guid"}}]

    _smtp(monkeypatch, send_raises=smtplib.SMTPServerDisconnected("dropped"))
    mailer._send_email(_msg(), "leader@example.com", booking_id="CDS-1", then=then)
    assert marked == []
    assert [i.kind for i in outbox.items()] == ["email"]

    _smtp(monkeypatch)
    outbox.retry_now(outbox.items()[0].id)  # past the backoff, as "Try now" does
    outbox.drain()  # the email gets through, which queues the follow-on
    outbox.drain()  # and the next tick carries it out

    assert marked == ["inv-guid"]
    assert outbox.items() == []


def test_the_payload_file_goes_when_the_email_does(monkeypatch):
    _smtp(monkeypatch)

    mailer._send_email(_msg(), "leader@example.com", booking_id="CDS-1")

    assert list((outbox.OUTBOX_PAYLOAD_DIR).glob("*.eml")) == []


def test_a_queued_email_survives_a_restart(monkeypatch):
    _smtp(monkeypatch, send_raises=OSError(-3, "Temporary failure in name resolution"))
    mailer._send_email(_msg(subject="Keep me"), "leader@example.com", booking_id="CDS-1")

    outbox._data = None  # as a restart would

    item = outbox.items()[0]
    assert item.payload["recipient"] == "leader@example.com"
    assert b"Keep me" in outbox.read_payload(item.id)
