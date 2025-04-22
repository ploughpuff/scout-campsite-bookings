"""
calendar.py - Hanle all calendar related operations.
"""

import datetime
import logging

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# booking["google_calendar_event_id"] = handle_calendar_entry(booking_id, booking)


class GoogleCalendar:
    """class for Google calendar inerfacing."""

    def __init__(self, service_account_file, calendar_id, timezone="Europe/London"):
        self.service_account_file = service_account_file
        self.calendar_id = calendar_id
        self.timezone = timezone
        self.service = self._build_service()

    def _build_service(self):
        creds = service_account.Credentials.from_service_account_file(
            self.service_account_file, scopes=SCOPES
        )
        return build("calendar", "v3", credentials=creds)

    def add_event(self, booking):
        """Add a new calendar event."""
        try:
            start = datetime.datetime.fromtimestamp(int(booking["Arriving"]))
            end = (
                datetime.datetime.fromtimestamp(int(booking.get("Departing", 0)))
                if booking.get("Departing")
                else start + datetime.timedelta(hours=2)
            )

            event = {
                "summary": f"{booking.get('Group', 'Group')} - {booking.get('Leader', 'Leader')}",
                "description": f"""
Booking confirmed for campsite: {booking.get('Campsite', 'N/A')}
Number of people: {booking.get('Number', 'N/A')}
Status: {booking.get('Status', 'N/A')}
                """.strip(),
                "start": {"dateTime": start.isoformat(), "timeZone": self.timezone},
                "end": {"dateTime": end.isoformat(), "timeZone": self.timezone},
            }

            # pylint: disable=no-member
            created_event = (
                self.service.events().insert(calendarId=self.calendar_id, body=event).execute()
            )

            logger.info("Event created: %s", created_event.get("htmlLink"))
            return created_event["id"]
        except HttpError as e:
            logger.error("Failed to create event: %s", e)
            return None

    def update_event(self, event_id, booking):
        """Update existing calendar entry."""
        try:
            start = datetime.datetime.fromtimestamp(int(booking["Arriving"]))
            end = (
                datetime.datetime.fromtimestamp(int(booking.get("Departing", 0)))
                if booking.get("Departing")
                else start + datetime.timedelta(hours=2)
            )

            updated_event = {
                "summary": f"{booking.get('Group', 'Group')} - {booking.get('Leader', 'Leader')}",
                "description": f"""
Booking updated for campsite: {booking.get('Campsite', 'N/A')}
Number of people: {booking.get('Number', 'N/A')}
Status: {booking.get('Status', 'N/A')}
                """.strip(),
                "start": {"dateTime": start.isoformat(), "timeZone": self.timezone},
                "end": {"dateTime": end.isoformat(), "timeZone": self.timezone},
            }

            # pylint: disable=no-member
            self.service.events().update(
                calendarId=self.calendar_id, eventId=event_id, body=updated_event
            ).execute()

            logger.info("Event updated: %s", event_id)
            return True
        except HttpError as e:
            logger.error("Failed to update event %s: %s", event_id, e)
            return False

    def delete_event(self, event_id):
        """Delete an event from the calendar

        Args:
            event_id (str): String from the booking record corresponding to the calendar ID.

        Returns:
            Boolean: True removed ok, otherwise False
        """
        try:
            # pylint: disable=no-member
            self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
            logger.info("Event deleted: %s", event_id)
            return True
        except HttpError as e:
            logger.error("Failed to delete event %s: %s", event_id, e)
            return False
