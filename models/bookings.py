"""
Bookings.py - Manage the bookings data file and provide access functions.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from flask import flash

from utils import (
    now_uk,
    secs_to_hr,
    get_pretty_datetime_str,
    parse_iso_datetime,
    datetime_to_iso_uk,
)
from config import CACHE_DIR, ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS
from models.mailer import send_email_notification
from models.booking_types import BookingType, gen_next_booking_id, parse_booking_type


status_options = ["New", "Pending", "Confirmed", "Invoice", "Completed", "Cancelled"]

#
## Valid transitions to control buttons on html, and filter user input
status_transitions = {
    "New": ["Pending", "Confirmed", "Cancelled"],
    "Pending": ["Confirmed", "Cancelled"],
    "Confirmed": ["Cancelled"],
    "Invoice": [
        "Completed",
    ],
    "Completed": [],
    "Cancelled": ["New"],
}


class Bookings:
    """Class for managing the booking data"""

    def __init__(self, calendar=None):
        self.calendar = calendar  # GoogleCalendar instance
        self.logger = logging.getLogger("app_logger")
        self.json_path = Path(CACHE_DIR, "bookings.json")
        self.data = {}
        self._load()

    def get_states(self):
        """Reveal the various status names and their valid transitions.

        Returns:
            dict: "name" of states, and "transition" list of valid transitions.
        """
        return {"names": status_options, "transitions": status_transitions}

    def age(self):
        """
        String showing the age of the bookings or when they were last retrieved from sheets

        Returns:
            str: Either 'NEVER' if not data exists, or string like '1d 5h 35m 17s'
        """
        if "timestamp" in self.data:
            return secs_to_hr((now_uk() - self.data["timestamp"]).total_seconds())

        return "NEVER!"

    def get_booking(self, booking_id=None):
        """Gets specificed booking or list of all of them.

        Args:
            booking_id (str, optional): The bookinging ID to return. Defaults to None.

        Returns:
            dict: Dictionary of values for display purposes.
        """

        self._load()

        # If booking_id is provided, return only that booking
        if booking_id:
            # Fetch the specific booking data by booking_id
            booking_data = self.data["bookings"].get(booking_id)
            if booking_data:
                return booking_data  # Return as a list to keep consistency

        # If no booking_id provided, return the full list of bookings
        bookings = []
        for b_id, b in self.data["bookings"].items():
            simplified = {
                "ID": b_id,
                "Group": b.get("Group"),
                "Leader": b.get("Leader"),
                "Arriving": b.get("Arriving"),
                "Departing": b.get("Departing"),
                "Number": b.get("Number"),
                "Status": b.get("Status"),
            }
            bookings.append(simplified)

        # Optional: sort by status options order
        bookings.sort(key=lambda b: status_options.index(b["Status"]))

        return bookings

    def _can_transition(self, from_status, to_status):
        return to_status in status_transitions.get(from_status, [])

    def change_status(self, booking_id, new_status, description=None):
        """Change the status of a single booking.

        Args:
            booking_id (str): The booking ID to modify
            new_status (str): Proposed new status value
            description (str, optional): Cancel reason or pending question text. Defaults to None.

        Returns:
            Boolean: True if change made, else False
        """

        booking = self.data.get("bookings", {}).get(booking_id)

        if new_status in ("Cancelled", "Pending"):
            if not description:
                msg = (
                    "Cancellation reason is required."
                    if new_status == "Cancelled"
                    else "Reason for pending or question to requester is required."
                )
                flash(msg, "danger")
                return False

            field = "Cancel Reason" if new_status == "Cancelled" else "pend_question"
            self._add_to_notes(booking, f"{field}: {description}")
            booking[field] = description

        elif description:
            self.logger.warning(
                "Unexpected description for state %s (%s): %s",
                new_status,
                booking_id,
                description,
            )
            return False

        old_status = booking.get("Status")

        if self._apply_status_change(booking_id, new_status):
            send_email_notification(booking_id, booking)
            # handle_calendar_entry(booking_id, booking)
            self._add_to_notes(
                booking, f"Status changed [{old_status}] > [{new_status}]"
            )

            self._save()
            return True

        return False

    def modify_fields(self, booking_id, updates: dict) -> bool:
        """Modify fields in the booking from the html page.

        Args:
            booking_id (str): The booking ID to modify fields on.
            updates (dict): Dictionary of kv pair to modify.

        Returns:
            bool: True is successful
        """

        editable_fields = {"Number", "Arriving", "Departing"}

        self._load()  # Ensure fresh data

        booking = self.data.get("bookings", {}).get(booking_id)
        if not booking:
            flash(
                f"Cannot update booking {booking_id} as not found in database.",
                "danger",
            )
            return False

        for field, new_value in updates.items():
            if field not in editable_fields:
                self.logger.warning(
                    "Bookings/Update tried to edit field %s which is not in my list: %s",
                    field,
                    booking_id,
                )
                continue

            old_value = booking.get(field)

            if old_value == new_value:
                continue  # No change, skip

            booking[field] = new_value

            if field in ("Arriving", "Departing"):
                old_value_str = get_pretty_datetime_str(old_value)
                new_value_str = get_pretty_datetime_str(new_value)
            else:
                old_value_str = old_value
                new_value_str = new_value

            self._add_to_notes(
                booking, f"{field} changed from [{old_value_str}] to [{new_value_str}]"
            )

        self._save()
        return True

    def _apply_status_change(self, booking_id, to_status):

        booking = self.data.get("bookings", {}).get(booking_id)

        if not booking:
            flash(
                f"Cannot update booking {booking_id} as not found in database.",
                "danger",
            )
            return False

        from_status = booking.get("Status")

        if not self._can_transition(from_status, to_status):
            msg = f"Invalid transition for {booking_id}: {from_status} > {to_status}"
            flash(msg, "danger")
            self.logger.warning(msg)
            return False

        #
        ## Before blindly transitioning to the new state, if its Cancel>New check the arrival time
        ## is still in the future.  We don't want to resurrest past bookings
        if from_status == "Cancelled" and to_status == "New":
            arriving = booking.get("Arriving")
            if arriving is not None and arriving < now_uk():
                msg = f"Unable to resurrect booking {booking_id}: arrival date is in the past!"
                flash(msg, "warning")
                return False

        booking["Status"] = to_status

        self._save()
        return True

    def _add_to_notes(self, booking, new_note):

        timestamp = get_pretty_datetime_str(include_seconds=True)
        new_note_entry = f"[{timestamp}]: {new_note}"

        old_value = booking.get("Notes", "")
        booking["Notes"] = new_note_entry + ("\n" + old_value if old_value else "")
        self._save()

    def _save(self):
        """Searilise fields before storing in JSON format."""

        # Make a shallow copy to avoid mutating in-memory data
        data_to_save = {
            "timestamp": datetime_to_iso_uk(self.data.get("timestamp")),
            "bookings": {},
        }

        for booking_id, booking in self.data.get("bookings", {}).items():
            serialized = {}
            for key, value in booking.items():
                if isinstance(value, datetime):
                    serialized[key] = datetime_to_iso_uk(value)
                elif isinstance(value, BookingType):
                    serialized[key] = value.name
                else:
                    serialized[key] = value

            data_to_save["bookings"][booking_id] = serialized

        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, indent=2)

    def _load(self):
        if self.json_path.exists():
            with open(self.json_path, "r", encoding="utf-8") as f:
                self.data = json.load(f)

                # Deseralise timestamp
                self.data["timestamp"] = parse_iso_datetime(self.data.get("timestamp"))

                for booking in self.data.get("bookings", {}).values():
                    for key, value in booking.items():
                        # Deserialize datetime strings
                        booking[key] = parse_iso_datetime(value)

                    # Deserialize BookingType enum
                    bt_raw = booking.get("booking_type")
                    parsed_bt = parse_booking_type(bt_raw)

                    if parsed_bt:
                        booking["booking_type"] = parsed_bt
                    else:
                        self.logger.warning(
                            "Unknown or missing booking_type: %s", bt_raw
                        )

                self._auto_update_statuses()

        else:
            self.data = {"timestamp": now_uk(), "bookings": {}}

    def _auto_update_statuses(self):

        for _, booking in self.data.get("bookings", {}).items():
            status = booking.get("Status")
            departing = booking.get("Departing")
            invoice = booking.get("invoice")

            archive_date = departing + timedelta(
                days=ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS
            )

            #
            ## Move confirmed bookings to completed/invoice once departure dates has passed
            if status == "Confirmed" and departing < now_uk():
                new_status = "Completed" if invoice else "Invoice"
                self._add_to_notes(
                    booking, f"Auto Status Change: [{status}] > [{new_status}]"
                )
                booking["Status"] = new_status
                self._save()

            elif status == "Completed" and archive_date < now_uk():
                new_status = "Archived"
                self._add_to_notes(
                    booking, f"Auto Status Change: [{status}] > [{new_status}]"
                )
                booking["Status"] = new_status
                self._save()

    def _md5_of_dict(self, data):
        # Ensure consistent ordering to get a consistent hash
        # Convert dict into a string of bytes for use with hashlib
        encoded = json.dumps(data, sort_keys=True).encode()
        return hashlib.md5(encoded).hexdigest()

    def _find_booking_by_md5(self, target_md5):
        for booking in self.data.get("bookings", {}).values():
            if (
                isinstance(booking, dict)
                and booking.get("original_sheet_md5") == target_md5
            ):
                return True

        return False

    def add_new_data(self, sheet_bookings, booking_type):
        """Function to load a sheet of data in dict format into our booking structure

        Args:
            sheet_bookings (list): list of dict from sheets
            booking_type (class BookingType ENUM): What the booking type is

        Returns:
            int: number of bookings added
        """

        bookings_added = 0

        if "timestamp" in sheet_bookings and sheet_bookings["timestamp"]:

            # Sheets records timestamp in ISO format.  Convert to dt object
            self.data["timestamp"] = parse_iso_datetime(sheet_bookings["timestamp"])

            #
            ## Need to normalise the new data from Sheet to our structure
            for sb in sheet_bookings["sheet_data"]:

                #
                ## Create MD5 of sheet line item so we can track if its new or seen before
                new_booking_md5 = self._md5_of_dict(sb)

                if not self._find_booking_by_md5(new_booking_md5):

                    start_dt = datetime.strptime(
                        sb["Arrival Date / Time"], "%d/%m/%Y %H:%M:%S"
                    )

                    # Parse the departure time and replace the time part of arrival
                    dep_time = datetime.strptime(
                        sb["Departure Time"], "%H:%M:%S"
                    ).time()
                    end_dt = start_dt.replace(
                        hour=dep_time.hour, minute=dep_time.minute, second=0
                    )

                    existing_ids = list(self.data["bookings"].keys())
                    new_booking_id = gen_next_booking_id(
                        existing_ids, booking_type, start_dt.year
                    )

                    new_booking = {
                        new_booking_id: {
                            "original_sheet_md5": new_booking_md5,
                            "original_sheet_data": sb,
                            "booking_type": booking_type.name,
                            "Group": sb["Chelmsford Scout Group"],
                            "Leader": sb["Name of Lead Person"],
                            "Arriving": start_dt,
                            "Departing": end_dt,
                            "Number": sb["Number of people"],
                            "Status": "New",
                            "invoice": False,
                            "confirmation_email_sent": False,
                            "google_calendar_id": None,
                            "Notes": "",
                        }
                    }

                    self._add_to_notes(
                        new_booking.get(new_booking_id), "Pulled from sheets"
                    )
                    self.data["bookings"].update(new_booking)
                    bookings_added += 1

            self._save()

        return bookings_added
