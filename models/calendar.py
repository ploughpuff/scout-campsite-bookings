"""
calendar.py - Hanle all calendar related operations.
"""

import logging
import textwrap
from typing import Callable, Optional

from googleapiclient.errors import HttpError

from config import CALENDAR_ID, SERVICE_ACCOUNT_PATH
from models import outbox
from models.net import classify, google_service, status_of
from models.pricing import bookable_facilities
from models.schemas import BookingData, LiveBooking

logger = logging.getLogger("app_logger")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

#
## Set by the booking store at start-up; see set_event_id_recorder().
_event_id_recorder: Optional[Callable] = None

# https://developers.google.com/workspace/calendar/api/v3/reference


def get_cal_events() -> list:
    """Query google cal for all events.

    Raises:
        Retryable or Permanent: the listing did not happen. It used to return
        None on an HttpError, which the only caller fed straight into a set
        comprehension - so a Calendar outage turned /admin/list_cal_events into
        an unexplained 500 rather than saying what was wrong.
    """
    events = []
    page_token = None

    try:
        service = _build_service()

        while True:
            # pylint: disable=no-member
            response = service.events().list(calendarId=CALENDAR_ID, pageToken=page_token).execute()

            events.extend(response.get("items", []))

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return events

    except Exception as e:  # pylint: disable=broad-except
        error = classify(e)
        logger.error("Failed to list events: %s", error)
        raise error from e


def update_calendar_entry(rec: LiveBooking):
    """Queue the calendar up to match this booking's status.

    Queued rather than done here: the caller has usually already emailed the
    leader and saved the record, and a calendar outage must not undo either.
    The work is attempted immediately, so in the normal case the event really
    has changed by the time the page reloads - it just survives if it has not.
    """
    #
    ## Confirmed, Completed, Invoice - the booking is real and belongs on the
    ## calendar. Cancelled and Archived - it does not. Everything else (New,
    ## Pending) is not settled enough to show anyone.
    if not rec.tracking.status:
        logger.error("Unable to add event.  Status not found: %s", rec.booking.id)

    elif rec.tracking.status in ["Confirmed", "Completed", "Invoice"]:
        outbox.enqueue("cal_upsert", _upsert_payload(rec), booking_id=rec.booking.id)

    elif rec.tracking.status in ["Cancelled", "Archived"]:
        delete_calendar_entry(rec)

    else:
        logger.debug(
            "Not updating calendar for booking %s as status is %s",
            rec.booking.id,
            rec.tracking.status,
        )


def delete_calendar_entry(rec: LiveBooking):
    """Queue the removal of this booking's calendar event.

    The payload carries the event id *and* the booking id. That redundancy is
    the fix for the orphans: the archive sweep drops the whole tracking block
    the moment it archives a record, so by the time a retry comes round the id
    may be the only trace left - and if even that is missing, the booking id is
    still stamped on the event itself.
    """
    outbox.enqueue(
        "cal_delete",
        {"event_id": rec.tracking.google_calendar_id, "booking_id": rec.booking.id},
        booking_id=rec.booking.id,
    )


def _build_service():
    return google_service("calendar", "v3", SCOPES, SERVICE_ACCOUNT_PATH)


def create_calendar_title(b: BookingData) -> str:
    """Create Google Calendar event title using only bookable facilities."""
    facilities = bookable_facilities()
    selected = [part.strip() for part in b.facilities if part in facilities]
    return f"{b.event_type.upper()}: " + " + ".join(selected)


def _build_event(rec: LiveBooking, extra_text: str = None) -> dict:
    extra_text = extra_text or ""

    description = textwrap.dedent(
        f"""
        {rec.booking.id}
        {rec.booking.group_size} - {rec.booking.group_name}
        {extra_text}
    """
    ).strip()

    return {
        "summary": create_calendar_title(rec.booking),
        "description": description,
        "start": {"dateTime": rec.booking.arriving.isoformat()},
        "end": {"dateTime": rec.booking.departing.isoformat()},
        "extendedProperties": {"private": {"booking_id": rec.booking.id}},
    }


def _upsert_payload(rec: LiveBooking) -> dict:
    """Everything the upsert handler needs, with no lookup back into the record.

    The event body is rendered now rather than at send time on purpose: it must
    describe the booking as it was when the change was made, even if the record
    is edited again before the queue drains.
    """
    return {"booking_id": rec.booking.id, "event": _build_event(rec)}


def find_event_id(service, booking_id: str) -> str | None:
    """The id of the event belonging to this booking, if it has one.

    Every event we create carries its booking id in a private extended
    property, so the calendar itself is the authority on which event belongs to
    which booking. Asking Google beats trusting a stored id that a later
    archive may have thrown away.
    """
    # pylint: disable=no-member
    found = (
        service.events()
        .list(calendarId=CALENDAR_ID, privateExtendedProperty=f"booking_id={booking_id}")
        .execute()
    )
    items = found.get("items", [])
    return items[0]["id"] if items else None


@outbox.handler("cal_upsert")
def _handle_upsert(item) -> None:
    """Put the booking on the calendar, whether or not it is there already.

    Idempotent by construction: a retry after a failure we could not read finds
    the event it may have just created and updates it, rather than adding a
    second one.
    """
    booking_id = item.payload["booking_id"]
    event = item.payload["event"]

    try:
        service = _build_service()
        existing = find_event_id(service, booking_id)

        # pylint: disable=no-member
        if existing:
            resource = (
                service.events()
                .update(calendarId=CALENDAR_ID, eventId=existing, body=event)
                .execute()
            )
            logger.info("Calendar event modified: %s: %s", booking_id, resource.get("htmlLink"))
        else:
            resource = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
            logger.info("Calendar event created: %s: %s", booking_id, resource.get("htmlLink"))

    except Exception as e:  # pylint: disable=broad-except
        raise classify(e) from e

    _record_event_id(booking_id, resource["id"])


@outbox.handler("cal_delete")
def _handle_delete(item) -> None:
    """Take the booking off the calendar.

    Works from the booking id when the stored event id is missing or stale,
    which is what makes a delete survive the record being archived underneath
    it. An event that is already gone is a success, not something to retry.
    """
    booking_id = item.payload.get("booking_id")
    event_id = item.payload.get("event_id")

    try:
        service = _build_service()

        if not event_id and booking_id:
            event_id = find_event_id(service, booking_id)

        if not event_id:
            raise outbox.Skip(f"no event on the calendar for {booking_id or 'unknown booking'}")

        # pylint: disable=no-member
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()

    except HttpError as e:
        #
        ## 410 Gone and 404 Not Found both mean the calendar already looks the
        ## way we wanted it to. Nothing left to do, so the item is finished.
        if status_of(e) in (404, 410):
            raise outbox.Skip(f"event {event_id} already deleted") from e
        raise classify(e) from e

    except outbox.Skip:
        raise

    except Exception as e:  # pylint: disable=broad-except
        raise classify(e) from e

    logger.info("Calendar event deleted: %s (%s)", booking_id or "-", event_id)
    _record_event_id(booking_id, None)


def set_event_id_recorder(func) -> None:
    """Register how a confirmed event id gets written back to the booking.

    Kept as a callback so this module never has to reach into the booking
    store. The id is only a cache for the admin listing - the calendar itself
    is the authority - so a booking that has since been archived simply ignores
    the update.
    """
    global _event_id_recorder  # pylint: disable=global-statement
    _event_id_recorder = func


def _record_event_id(booking_id: str, event_id: str | None) -> None:
    if _event_id_recorder is None or not booking_id:
        return

    try:
        _event_id_recorder(booking_id, event_id)
    except Exception:  # pylint: disable=broad-except
        #
        ## Caching the id is a convenience. Failing to do so must not turn a
        ## calendar write that actually worked into a retry.
        logger.exception("Could not record calendar id for %s", booking_id)
