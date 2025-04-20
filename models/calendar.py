from google.oauth2 import service_account
from googleapiclient.discovery import build
import datetime
import logging
import os

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

#booking["google_calendar_event_id"] = handle_calendar_entry(booking_id, booking)

class GoogleCalendar:
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

    def AddEvent(self, booking):
        try:
            start = datetime.datetime.fromtimestamp(int(booking["Arriving"]))
            end = datetime.datetime.fromtimestamp(int(booking.get("Departing", 0))) if booking.get("Departing") else start + datetime.timedelta(hours=2)

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

            created_event = self.service.events().insert(calendarId=self.calendar_id, body=event).execute()
            logger.info(f"Event created: {created_event.get('htmlLink')}")
            return created_event["id"]
        except Exception as e:
            logger.error(f"Failed to create event: {e}")
            return None

    def UpdateEvent(self, event_id, booking):
        try:
            start = datetime.datetime.fromtimestamp(int(booking["Arriving"]))
            end = datetime.datetime.fromtimestamp(int(booking.get("Departing", 0))) if booking.get("Departing") else start + datetime.timedelta(hours=2)

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

            self.service.events().update(
                calendarId=self.calendar_id, eventId=event_id, body=updated_event
            ).execute()

            logger.info(f"Event updated: {event_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update event {event_id}: {e}")
            return False

    def DeleteEvent(self, event_id):
        try:
            self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
            logger.info(f"Event deleted: {event_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete event {event_id}: {e}")
            return False
