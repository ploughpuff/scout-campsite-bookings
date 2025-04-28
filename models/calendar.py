"""
calendar.py - Hanle all calendar related operations.
"""

import logging
import textwrap
import re
from datetime import datetime, timezone

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import CALENDAR_ID, SERVICE_ACCOUNT_PATH, UK_TZ

logger = logging.getLogger("app_logger")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# booking["google_calendar_event_id"] = handle_calendar_entry(booking_id, booking)
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


def get_clash_booking_ids(start: datetime, end: datetime) -> list:
    """Return a list of events that clash between the two dates, else an empty list."""
    try:
        service = _build_service()

        params = {
            "calendarId": CALENDAR_ID,
            "singleEvents": True,
            "orderBy": "startTime",  # optional, but usually helpful
        }

        if start:
            params["timeMin"] = start.astimezone(timezone.utc).isoformat()
        if end:
            params["timeMax"] = end.astimezone(timezone.utc).isoformat()

        # pylint: disable=no-member
        event_resource = service.events().list(**params).execute()

        clash_events = []

        if "items" in event_resource and event_resource["items"]:
            # Loop through all events to find those with the CDS pattern in the extended properties
            for event in event_resource["items"]:
                event_id = event.get("id")

                # Check if 'extendedProperties' exists and has the 'booking_id' field
                extended_properties = event.get("extendedProperties", {}).get("private", {})
                booking_id = extended_properties.get("booking_id", None)

                if booking_id:  # If there's a booking ID, this event clashes
                    clash_events.append(booking_id)

        return clash_events

    except HttpError as e:
        logger.error("Failed to list events: %s", str(e))
        return []


def update_calendar_entry(booking_id, booking):
    """Adds new, modifies existing, or deleted cal entry"""

    #
    ## New, Pending, Invoice, Completed, Archived - No action
    ## Confirmed - Add or modify cal entry
    ## Cancelled - Delete cal entry
    status = booking.get("Status")

    if not status:
        logger.error("Unable to add event.  Status not found: %s", booking_id)

    elif status == "Confirmed":
        booking["google_calendar_id"] = _add_or_mod_event(booking_id, booking)

    elif status == "Cancelled":
        booking["google_calendar_id"] = _del_event(booking_id, booking)

    else:
        logger.debug("No calendar changes for status: %s", status)


def _build_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_PATH, scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds)


def _build_event(booking_id, booking, extra_text=None):
    summary = "EVE: Scouts"

    extra_text = extra_text or ""

    description = textwrap.dedent(
        f"""
        {booking_id}
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
        "extendedProperties": {"private": {"booking_id": booking_id}},
    }


def _add_or_mod_event(booking_id, booking):
    """Add a new calendar event."""

    try:
        service = _build_service()
        event = _build_event(booking_id, booking)
        google_calendar_id = booking.get("google_calendar_id")

        # pylint: disable=no-member
        if not google_calendar_id:
            event_resource = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
            logger.info(
                "Calendar event created: %s: %s", booking_id, event_resource.get("htmlLink")
            )
        else:
            event_resource = (
                service.events()
                .update(calendarId=CALENDAR_ID, eventId=google_calendar_id, body=event)
                .execute()
            )
            logger.info(
                "Calendar event modified: %s: %s", booking_id, event_resource.get("htmlLink")
            )

        return event_resource["id"]

    except HttpError as e:
        logger.error("Failed to create or mod event: %s", str(e))
        return None


def _del_event(booking_id, booking):
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
            logger.info("Unable to delete calendar event as no ID available: %s", booking_id)
            return google_calendar_id

        # pylint: disable=no-member
        service.events().delete(calendarId=CALENDAR_ID, eventId=google_calendar_id).execute()
        return None
    except HttpError as e:
        logger.error("Failed to delete event: %s", str(e))
        return google_calendar_id
