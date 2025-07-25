"""
calendar.py - Hanle all calendar related operations.
"""

import logging
import textwrap

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import CALENDAR_ID, FIELD_MAPPINGS_DICT, SERVICE_ACCOUNT_PATH
from models.schemas import BookingData, LiveBooking

logger = logging.getLogger("app_logger")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# https://developers.google.com/workspace/calendar/api/v3/reference


def get_cal_events() -> list:
    """Query google cal for all events"""
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

    except HttpError as e:
        logger.error("Failed to list events: %s", str(e))
        return None


def del_cal_events(event_resource):
    """Delete all events from the calendar"""

    events = event_resource.get("items", [])

    if not events:
        return

    for event in events:
        event_id = event["id"]
        try:
            service = _build_service()

            # pylint: disable=no-member
            service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
            print(f"Deleted event: {event_id}")
        except HttpError as e:
            print(f"Failed to delete {event_id}: {e}")


def update_calendar_entry(rec: LiveBooking):
    """Adds new, modifies existing, or deleted cal entry"""

    #
    ## New, Pending, Invoice, Completed, Archived - No action
    ## Confirmed - Add or modify cal entry
    ## Cancelled - Delete cal entry
    if not rec.tracking.status:
        logger.error("Unable to add event.  Status not found: %s", rec.booking.id)

    elif rec.tracking.status in ["Confirmed", "Completed", "Invoiced"]:
        rec.tracking.google_calendar_id = _add_or_mod_event(rec)

    elif rec.tracking.status in ["Cancelled", "Archived"]:
        rec.tracking.google_calendar_id = _del_from_rec(rec)

    else:
        logger.debug(
            "Not updating calendar for booking %s as status is %s",
            rec.booking.id,
            rec.tracking.status,
        )


def delete_calendar_entry(rec: LiveBooking):
    """Delete the google calendar event for the supplied rec"""
    rec.tracking.google_calendar_id = _del_from_rec(rec)


def _build_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_PATH, scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds)


def create_calendar_title(b: BookingData) -> str:
    """Create Google Calendar event title using only bookable facilities."""
    facilities = FIELD_MAPPINGS_DICT.get("bookable_facilities", [])
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


def _add_or_mod_event(rec: LiveBooking):
    """Add a new calendar event."""

    try:
        service = _build_service()
        event = _build_event(rec)

        # pylint: disable=no-member
        if not rec.tracking.google_calendar_id:

            event_resource = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
            logger.info(
                "Calendar event created: %s: %s", rec.booking.id, event_resource.get("htmlLink")
            )
        else:
            event_resource = (
                service.events()
                .update(calendarId=CALENDAR_ID, eventId=rec.tracking.google_calendar_id, body=event)
                .execute()
            )
            logger.info(
                "Calendar event modified: %s: %s", rec.booking.id, event_resource.get("htmlLink")
            )

        return event_resource["id"]

    except HttpError as e:
        logger.error("Failed to create or mod event: %s", str(e))
        return None


def _del_from_rec(rec: LiveBooking):
    return del_cal_event(rec.tracking.google_calendar_id, rec.booking.id)


def del_cal_event(google_calendar_id: str, booking_id: str):
    """Delete an event from the calendar

    Args:
        google_calendar_id (str): String from the booking record corresponding to the calendar ID.

    Returns:
        None on success, or google_calendar_id on error
    """
    try:
        service = _build_service()

        if not google_calendar_id:
            logger.info("Unable to delete calendar event as no ID available: %s", booking_id)
            return google_calendar_id

        # pylint: disable=no-member
        service.events().delete(calendarId=CALENDAR_ID, eventId=google_calendar_id).execute()
        return None
    except HttpError as e:
        logger.error("Failed to delete calendar event for booking: %s %s", booking_id, str(e))
        return google_calendar_id
