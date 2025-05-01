"""
calendar.py - Hanle all calendar related operations.
"""

import logging
import textwrap

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import CALENDAR_ID, SERVICE_ACCOUNT_PATH, UK_TZ

logger = logging.getLogger("app_logger")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# https://developers.google.com/workspace/calendar/api/v3/reference


def get_cal_events():
    """Return a list of all calendar events, or between two dates"""
    try:
        service = _build_service()

        # pylint: disable=no-member
        event_resource = service.events().list(calendarId=CALENDAR_ID).execute()

        return event_resource

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


def update_calendar_entry(booking):
    """Adds new, modifies existing, or deleted cal entry"""

    #
    ## New, Pending, Invoice, Completed, Archived - No action
    ## Confirmed - Add or modify cal entry
    ## Cancelled - Delete cal entry
    status = booking.get("Status")

    if not status:
        logger.error("Unable to add event.  Status not found: %s", booking.get("id"))

    elif status == "Confirmed":
        booking["google_calendar_id"] = _add_or_mod_event(booking)

    elif status == "Cancelled":
        booking["google_calendar_id"] = _del_event(booking)

    else:
        logger.debug("No calendar changes for status: %s", status)


def _build_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_PATH, scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds)


def _build_event(booking, extra_text=None):
    summary = "EVE: Scouts"

    extra_text = extra_text or ""

    description = textwrap.dedent(
        f"""
        {booking.get("id")}
        Number of people: {booking.get('Number', 'N/A')}
        {extra_text}
    """
    ).strip()

    arriving = booking.get("Arriving")
    departing = booking.get("Departing")

    return {
        "summary": summary,
        "description": description,
        "start": {"dateTime": arriving.isoformat() if arriving else None, "timeZone": UK_TZ.key},
        "end": {"dateTime": departing.isoformat() if departing else None, "timeZone": UK_TZ.key},
        "extendedProperties": {"private": {"booking_id": booking.get("id")}},
    }


def _add_or_mod_event(booking):
    """Add a new calendar event."""

    try:
        service = _build_service()
        event = _build_event(booking.get("id"), booking)
        google_calendar_id = booking.get("google_calendar_id")

        # pylint: disable=no-member
        if not google_calendar_id:
            event_resource = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
            logger.info(
                "Calendar event created: %s: %s", booking.get("id"), event_resource.get("htmlLink")
            )
        else:
            event_resource = (
                service.events()
                .update(calendarId=CALENDAR_ID, eventId=google_calendar_id, body=event)
                .execute()
            )
            logger.info(
                "Calendar event modified: %s: %s", booking.get("id"), event_resource.get("htmlLink")
            )

        return event_resource["id"]

    except HttpError as e:
        logger.error("Failed to create or mod event: %s", str(e))
        return None


def _del_event(booking):
    """Delete an event from the calendar

    Args:
        google_calendar_id (str): String from the booking record corresponding to the calendar ID.

    Returns:
        None on success, or google_calendar_id on error
    """
    try:
        service = _build_service()
        google_calendar_id = booking.get("google_calendar_id")

        if not google_calendar_id:
            logger.info("Unable to delete calendar event as no ID available: %s", booking.get("id"))
            return google_calendar_id

        # pylint: disable=no-member
        service.events().delete(calendarId=CALENDAR_ID, eventId=google_calendar_id).execute()
        return None
    except HttpError as e:
        logger.error("Failed to delete event: %s", str(e))
        return google_calendar_id
