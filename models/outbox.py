"""
outbox.py - Work that has to reach the outside world, kept until it does.

Every integration used to be "try once, log it, carry on". That is how six
calendar events ended up orphaned: the archive sweep asked Google to delete an
event, DNS was down, the delete quietly failed, and the booking was archived
anyway - taking the only copy of the event id with it. Nothing was left that
knew the event existed.

So outbound work goes in here first and only leaves once it has been confirmed
done. Two rules make that work:

  - A payload is self-contained. It carries everything needed to carry the
    action out, with no lookup back into a live booking, because the record may
    be edited or archived long before the queue drains. A cal_delete carries the
    event id; an email carries the message, already rendered.

  - A handler is idempotent wherever the remote API allows it. A Retryable
    failure means we never got an answer, so the work may or may not have
    landed, and the retry has to cope with either.

Storage is deliberately atomic_write_json rather than save_json: the queue
changes far too often for save_json's fifty-backup rotation, which would flush
the bookings backup history in a day. Durability here comes from the atomic
replace, not from backups.
"""

import json
import logging
import random
import threading
import uuid
from datetime import timedelta

from pydantic import ValidationError

from config import OUTBOX_FILE_PATH, OUTBOX_PAYLOAD_DIR
from models.json_utils import atomic_write_json
from models.net import Permanent, Retryable
from models.schemas import OutboxData, OutboxItem
from models.utils import now_uk

logger = logging.getLogger("app_logger")

#
## Backoff. A blip clears on the first retry a minute later; a real outage backs
## off to six-hourly so a fortnight offline is a handful of attempts, not
## thousands. Jitter keeps a queue full of items from hammering in lockstep.
BASE_BACKOFF = timedelta(minutes=1)
MAX_BACKOFF = timedelta(hours=6)
JITTER = 0.1

#
## Long enough to sit out a weekend outage, short enough that nobody discovers a
## stuck email a month later. After this an item is blocked, not dropped.
GIVE_UP_AFTER = timedelta(hours=48)

_lock = threading.RLock()
_data: OutboxData | None = None
_handlers: dict = {}
_handlers_loaded: bool = False


class Skip(Exception):
    """The handler decided there was nothing left to do.

    Not a failure: the far end already reflects what we wanted, so the item has
    done its job and leaves the queue. Raised by the idempotency checks - an
    event that is already gone, an invoice that already exists.
    """


def handler(kind: str):
    """Register the function that carries out one kind of work."""

    def register(func):
        _handlers[kind] = func
        return func

    return register


def _ensure_handlers() -> None:
    """Import the modules that register handlers.

    Done here rather than at the top of the file because those modules import
    this one to enqueue - taking the import late breaks the cycle without
    anybody having to remember to import them in the right order.
    """
    global _handlers_loaded  # pylint: disable=global-statement
    if _handlers_loaded:
        return

    # pylint: disable=import-outside-toplevel,unused-import,cyclic-import
    from models import calendar, mailer  # noqa: F401

    _handlers_loaded = True


def _load() -> OutboxData:
    """The queue, read from disk once and then held in memory."""
    global _data  # pylint: disable=global-statement
    if _data is not None:
        return _data

    try:
        #
        ## Read directly rather than through load_json: that path runs the
        ## booking schema migrations, so a bookings version bump would make the
        ## queue unreadable. The queue carries work, not records, and its own
        ## version moves only when its own shape does.
        if OUTBOX_FILE_PATH.exists():
            raw = json.loads(OUTBOX_FILE_PATH.read_text(encoding="utf-8"))
            _data = OutboxData.model_validate(raw)
        else:
            _data = OutboxData()
    except (OSError, ValueError, ValidationError) as exc:
        #
        ## Starting empty loses queued work, which is bad - but refusing to
        ## start loses the whole app, which is worse. Say so loudly.
        logger.error(
            "Could not read %s (%s) - starting with an empty outbox", OUTBOX_FILE_PATH, exc
        )
        _data = OutboxData()

    return _data


def _save() -> None:
    OUTBOX_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_load().model_dump(mode="json"), OUTBOX_FILE_PATH)


def reset_for_test(data: OutboxData | None = None) -> None:
    """Drop the in-memory queue. Tests only."""
    global _data, _handlers_loaded  # pylint: disable=global-statement
    _data = data if data is not None else OutboxData()
    _handlers_loaded = True


#
## Payload files. A rendered email with a PDF attached is far too big to sit in
## the queue file, which is rewritten on every single state change.
def payload_path(item_id: str):
    """Where the bulky half of an item lives."""
    return OUTBOX_PAYLOAD_DIR / f"{item_id}.eml"


def write_payload(item_id: str, blob: bytes) -> None:
    """Store an item's payload file, before the item itself is queued."""
    OUTBOX_PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    payload_path(item_id).write_bytes(blob)


def read_payload(item_id: str) -> bytes:
    """Read an item's payload file back."""
    return payload_path(item_id).read_bytes()


def _discard_payload(item_id: str) -> None:
    payload_path(item_id).unlink(missing_ok=True)


def _backoff(attempts: int) -> timedelta:
    """How long to wait before attempt number `attempts` + 1."""
    #
    ## The doubling is capped before it is applied, not after: timedelta refuses
    ## to multiply by a number that large, so a long-stuck item would raise
    ## rather than back off. MAX_BACKOFF is reached long before the cap anyway.
    doublings = min(max(attempts - 1, 0), 20)
    delay = min(BASE_BACKOFF * (2**doublings), MAX_BACKOFF)
    return delay * (1 + random.uniform(0, JITTER))


def enqueue(
    kind: str,
    payload: dict,
    booking_id: str = None,
    send_now: bool = True,
    blob: bytes = None,
) -> str:
    """Add work to the queue and, by default, try it straight away.

    `blob` is for anything too big to sit in the queue file - a rendered email,
    PDF attachment and all. It is written before the item is recorded, so an
    item that exists always has its payload file beside it.

    The immediate attempt is what keeps the common case feeling synchronous -
    the leader's email really has gone by the time the page reloads. When it
    fails the item simply stays queued, which is the whole point: the caller
    gets on with its work either way.
    """
    with _lock:
        item = OutboxItem(id=uuid.uuid4().hex, kind=kind, payload=payload, booking_id=booking_id)
        if blob is not None:
            write_payload(item.id, blob)
        _load().items.append(item)
        _save()
        logger.info("Queued %s for %s (%s)", kind, booking_id or "-", item.id)

    if send_now:
        _attempt(item)

    return item.id


def pending_count() -> int:
    """How much work is waiting."""
    with _lock:
        return sum(1 for item in _load().items if item.state == "pending")


def blocked_count() -> int:
    """How much work has given up and needs a human."""
    with _lock:
        return sum(1 for item in _load().items if item.state == "blocked")


def items() -> list:
    """Everything in the queue, oldest first."""
    with _lock:
        return sorted(_load().items, key=lambda i: i.created_at)


def oldest_pending():
    """The item that has been waiting longest, or None."""
    waiting = [i for i in items() if i.state == "pending"]
    return waiting[0] if waiting else None


def discard(item_id: str) -> bool:
    """Drop an item without carrying it out. An admin decision, never automatic."""
    with _lock:
        data = _load()
        keep = [i for i in data.items if i.id != item_id]
        if len(keep) == len(data.items):
            return False

        data.items = keep
        _save()
        _discard_payload(item_id)
        logger.info("Discarded outbox item %s", item_id)
        return True


def retry_now(item_id: str) -> bool:
    """Put a blocked or waiting item back at the front of the queue."""
    with _lock:
        for item in _load().items:
            if item.id == item_id:
                item.state = "pending"
                item.next_attempt_at = now_uk()
                _save()
                return True
    return False


def _remove(item: OutboxItem) -> None:
    data = _load()
    data.items = [i for i in data.items if i.id != item.id]
    _save()
    _discard_payload(item.id)


def _attempt(item: OutboxItem) -> str:
    """Carry out one item. Returns "sent", "retry" or "blocked"."""
    _ensure_handlers()

    with _lock:
        #
        ## Count the attempt and push the next one out *before* trying, then
        ## save. If the process dies mid-call we come back to an item scheduled
        ## for later rather than one that retries instantly forever - and for an
        ## email, that bounded window is the difference between a possible
        ## duplicate and an unbounded stream of them.
        item.attempts += 1
        item.next_attempt_at = now_uk() + _backoff(item.attempts)
        _save()

    func = _handlers.get(item.kind)
    if func is None:
        return _block(item, f"No handler registered for {item.kind}")

    try:
        func(item)
    except Skip as exc:
        logger.info("%s for %s already done: %s", item.kind, item.booking_id or "-", exc)
        with _lock:
            _remove(item)
        return "sent"
    except Permanent as exc:
        return _block(item, str(exc))
    except Retryable as exc:
        return _defer(item, str(exc))
    except Exception as exc:  # pylint: disable=broad-except
        #
        ## A handler that throws something unclassified is a bug in us, not an
        ## outage. Block it so it is seen rather than retried out of sight.
        logger.exception("Outbox handler for %s failed unexpectedly", item.kind)
        return _block(item, f"{type(exc).__name__}: {exc}")

    with _lock:
        _remove(item)
    logger.info("Sent %s for %s", item.kind, item.booking_id or "-")
    return "sent"


def _defer(item: OutboxItem, error: str) -> str:
    """Leave an item queued for another go, unless it has run out of road."""
    with _lock:
        item.last_error = error
        if now_uk() - item.created_at > GIVE_UP_AFTER:
            return _block(item, f"Gave up after {item.attempts} attempts: {error}")

        _save()

    logger.warning(
        "%s for %s did not get through (attempt %d): %s",
        item.kind,
        item.booking_id or "-",
        item.attempts,
        error,
    )
    return "retry"


def _block(item: OutboxItem, error: str) -> str:
    """Stop trying and put it in front of a human."""
    with _lock:
        item.state = "blocked"
        item.last_error = error
        _save()

    logger.error("%s for %s needs attention: %s", item.kind, item.booking_id or "-", error)
    return "blocked"


def drain(limit: int = None) -> dict:
    """Try everything that is due. Returns a tally of what happened."""
    _ensure_handlers()
    now = now_uk()

    with _lock:
        due = [
            item
            for item in sorted(_load().items, key=lambda i: i.created_at)
            if item.state == "pending" and item.next_attempt_at <= now
        ]

    tally = {"sent": 0, "retry": 0, "blocked": 0}
    held_up = set()

    for item in due:
        #
        ## One booking's work stays in order. Letting a later cal_delete overtake
        ## the cal_upsert that is stuck in front of it would delete an event and
        ## then recreate it.
        if item.booking_id is not None and item.booking_id in held_up:
            continue

        outcome = _attempt(item)
        tally[outcome] += 1

        if outcome != "sent" and item.booking_id is not None:
            held_up.add(item.booking_id)

        if limit is not None and sum(tally.values()) >= limit:
            break

    return tally
