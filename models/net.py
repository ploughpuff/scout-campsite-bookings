"""
net.py - The rules for talking to anything outside this container.

Two jobs, both learned from the production log.

First, every outbound call gets a deadline. Only Xero ever set one; Google and
SMTP were built with no timeout at all, so a black-holed socket blocked its
caller indefinitely. When that caller is the single scheduler thread, every
background job stops for good - the thread is still alive, so start() will not
replace it, and the only symptom is a quietly ageing "last pulled" time.

Second, a shared vocabulary for what went wrong. The app used to collapse "we
never reached them", "they answered and refused" and "there was nothing to do"
into one return value. That is how a failed calendar delete came to look exactly
like a successful one, and left six orphaned events on the calendar with nothing
left pointing at them. Retryable and Permanent keep those cases apart so callers
- and the outbox - can act on the difference.
"""

import http.client
import logging
import smtplib
import socket
import ssl

import google.auth.exceptions
import google_auth_httplib2
import httplib2
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger("app_logger")

#
## Deadlines. Generous enough that a slow-but-working call still succeeds, short
## enough that a dead one frees the thread long before the next scheduler tick.
GOOGLE_TIMEOUT = 30
SMTP_TIMEOUT = 30

#
## Backstop for any socket we do not own - a library that grows a new call, or
## one that ignores the timeout we passed it. Deliberately longer than the
## deadlines above so it never pre-empts a call that set its own.
DEFAULT_SOCKET_TIMEOUT = 60

#
## HTTP statuses worth another go. 408 and 429 are the server asking us to wait;
## 5xx is the server having a bad day. Everything else in 4xx is a refusal that
## will refuse again just as fast next time.
RETRYABLE_STATUSES = {408, 429}


class Retryable(Exception):
    """We never got a usable answer, so the work may or may not have landed.

    Anything raised as Retryable must be safe to attempt again - see the
    idempotency notes on the outbox handlers.
    """


class Permanent(Exception):
    """The far end answered and refused. Trying again will not change that."""


#
## Transport-level failures, in the order they bite: DNS, connect, TLS, read.
## httplib2 raises ServerNotFoundError for DNS rather than socket.gaierror, and
## google.auth raises TransportError from the token exchange that happens lazily
## inside the first .execute() - neither is an HttpError, which is why catching
## HttpError alone never caught an outage.
_TRANSPORT_ERRORS = (
    socket.gaierror,
    socket.timeout,
    TimeoutError,
    ConnectionError,
    ssl.SSLError,
    http.client.HTTPException,
    httplib2.HttpLib2Error,
    google.auth.exceptions.TransportError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


def status_of(exc) -> int | None:
    """The HTTP status behind an exception, if it has one."""
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status

    resp = getattr(exc, "resp", None)
    if resp is not None and getattr(resp, "status", None) is not None:
        return resp.status

    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) is not None:
        return resp.status_code

    return None


def _is_retryable(exc: BaseException) -> bool:
    """Whether another attempt could plausibly do better."""
    #
    ## A refresh that fails is usually the token endpoint having trouble rather
    ## than a bad service-account key. Treated as retryable so a blip recovers
    ## on its own; a genuinely broken key simply runs out of attempts and lands
    ## in the outbox as blocked, which is where a human needs to see it anyway.
    if isinstance(exc, google.auth.exceptions.RefreshError):
        return True

    #
    ## Transport first, so a requests ConnectionError is judged on having had no
    ## answer rather than on the status it does not carry.
    ## OSError covers what is left once gaierror, timeout and the
    ## ConnectionError family are matched: a local problem (no route,
    ## permission, too many open files) that may well clear.
    if isinstance(exc, _TRANSPORT_ERRORS + (OSError,)):
        return True

    if isinstance(exc, (HttpError, requests.exceptions.RequestException)):
        status = status_of(exc)
        return status is None or status >= 500 or status in RETRYABLE_STATUSES

    return False


def classify(exc: BaseException) -> Exception:
    """Sort an exception into Retryable or Permanent.

    Returns the exception to raise rather than raising it, so callers can add
    their own context: `raise classify(exc) from exc`.
    """
    if _is_retryable(exc):
        return Retryable(str(exc))

    #
    ## Anything we did not recognise is Permanent on purpose: retrying an
    ## unknown failure forever hides it, and blocked work is visible on /admin.
    if isinstance(exc, (HttpError, requests.exceptions.RequestException)):
        return Permanent(str(exc))

    return Permanent(f"{type(exc).__name__}: {exc}")


def classify_smtp(exc: BaseException) -> Exception:
    """Sort an SMTP failure into Retryable or Permanent.

    SMTP has its own conventions, so the HTTP rules do not carry over: a 4xx
    reply means "busy, try later" and a 5xx means "no, and don't ask again" -
    the opposite way round from the retryable statuses above.
    """
    #
    ## A refused recipient or sender is about the address, not the connection.
    ## Retrying delivers it to the same wrong place just as unsuccessfully.
    if isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
        return Permanent(str(exc))

    #
    ## A rejected credential needs someone to go and fix it. Blocking makes it
    ## visible on the Admin page instead of retrying quietly for two days.
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return Permanent(str(exc))

    if isinstance(exc, smtplib.SMTPResponseException):
        return Retryable(str(exc)) if 400 <= exc.smtp_code < 500 else Permanent(str(exc))

    #
    ## Connect failures, a dropped session, DNS, TLS: never reached the server.
    if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, OSError)):
        return Retryable(str(exc))

    return classify(exc)


def install_default_socket_timeout() -> None:
    """Stop any socket blocking forever, whoever opened it."""
    socket.setdefaulttimeout(DEFAULT_SOCKET_TIMEOUT)
    logger.info("Default socket timeout set to %ss", DEFAULT_SOCKET_TIMEOUT)


def google_service(api: str, version: str, scopes: list[str], service_account_path):
    """Build a Google API client whose sockets give up.

    googleapiclient only honours a timeout when it is handed a transport, so the
    credentials are wrapped in an httplib2.Http carrying one. Passing `http=`
    means `credentials=` must be left off - the transport already holds them.
    """
    creds = service_account.Credentials.from_service_account_file(
        service_account_path, scopes=scopes
    )
    authed = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=GOOGLE_TIMEOUT))
    return build(api, version, http=authed, cache_discovery=False)
