"""
Bookings.py - Manage the bookings data file and provide access functions.
"""

import copy
import hashlib
import json
import logging
from datetime import datetime, timedelta

from flask import flash

from config import (
    ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS,
    ARCHIVE_FILE_PATH,
    DATA_FILE_PATH,
    MAX_BACKUPS_TO_KEEP,
    UK_TZ,
)
from models.booking_types import gen_next_booking_id
from models.json_utils import load_json, save_json
from models.mailer import send_email_notification
from models.utils import (
    get_timestamp_for_notes,
    now_uk,
    parse_iso_datetime,
    secs_to_hr,
)

status_options = ["New", "Pending", "Confirmed", "Invoice", "Completed", "Archived", "Cancelled"]

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
    "Archived": [],
    "Cancelled": ["New"],
}


class Bookings:
    """Class for managing the booking data"""

    def __init__(self, calendar=None):
        self.calendar = calendar  # GoogleCalendar instance
        self.logger = logging.getLogger("app_logger")
        self.data = load_json(DATA_FILE_PATH)
        self.archive = load_json(ARCHIVE_FILE_PATH)

    def _save(self):
        save_json(self.data, DATA_FILE_PATH, MAX_BACKUPS_TO_KEEP)

    def load(self):
        """Reload the bookings json file from disk"""
        self.data = load_json(DATA_FILE_PATH, use_checksum=False)

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
        try:
            bookings.sort(
                key=lambda b: (status_options.index(b["Status"]), b.get("Arriving") or "")
            )
        except TypeError as e:
            msg = f"Corrupt datetime in one of the booking entries! {str(e)}"
            flash(msg, "danger")
            self.logger.warning(msg)

        return bookings

    def _can_transition(self, from_status, to_status):
        return to_status in status_transitions.get(from_status, [])

    def change_status(self, booking_id, new_status, description=None):
        """Change the status of a single booking.

        Args:
            booking_id (str): The booking ID to modify
            new_status (str): Proposed new status value
            description (str, optional): cancel_reason or pending question text. Defaults to None.

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

            booking["email_confirmation_sent"] = False  # Clear so we send again if resurected
            field = "cancel_reason" if new_status == "Cancelled" else "pend_question"
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
            self._add_to_notes(booking, f"Status changed [{old_status}] > [{new_status}]")
            self._save()  # Only save if the state transition is valid
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
                old_value_str = get_timestamp_for_notes(old_value)
                new_value_str = get_timestamp_for_notes(new_value)
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

        timestamp = get_timestamp_for_notes(include_seconds=True)
        new_note_entry = f"[{timestamp}]: {new_note}"

        old_value = booking.get("Notes", "")
        booking["Notes"] = new_note_entry + ("\n" + old_value if old_value else "")

    def auto_update_statuses(self):
        """Look for bookings to automatically change the status of"""

        for booking_id, booking in self.data.get("bookings", {}).items():
            status = booking.get("Status")
            departing = booking.get("Departing")
            invoice = booking.get("invoice")

            #
            ## Move confirmed bookings to completed/invoice once departure dates has passed
            if departing.tzinfo is None or departing.tzinfo.utcoffset(departing) is None:
                self.logger.warning(
                    "Departing time for %s is offset-naive [%s]", booking_id, departing
                )
                continue

            if status == "Confirmed" and departing < now_uk():
                new_status = "Invoice" if invoice else "Completed"
                booking["Status"] = new_status
                self._add_to_notes(booking, f"Auto Status Change: [{status}] > [{new_status}]")
                self._save()
                flash(
                    f"{booking_id} automatically change from {status} "
                    f"to {new_status} now booking has passed",
                    "warning",
                )

    def archive_old_bookings(self):
        """Move bookings with status 'Archived' to archive.json and remove from bookings.json."""
        to_archive = []

        for booking_id, booking in list(self.data.get("bookings", {}).items()):
            departing = booking.get("Departing")
            archive_date = departing + timedelta(days=ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS)

            if booking.get("Status") == "Completed" and archive_date < now_uk():

                # Take a deep copy of this booking and remove from main bookings
                archive_copy = copy.deepcopy(booking)
                self.data["bookings"].pop(booking_id)

                # Remove all GDPR data
                archive_copy.pop("original_sheet_data", None)
                archive_copy.pop("Leader", None)
                archive_copy["Status"] = "Archived"
                self._add_to_notes(
                    archive_copy, f"Auto Status Change: [{booking.get('Status')}] > [Archived]"
                )
                self.logger.info("%s archived", booking_id)
                to_archive.append(archive_copy)

        if not to_archive:
            self.logger.info("No bookings to archive.")
            return False

        # Handle archive file
        if ARCHIVE_FILE_PATH.exists():
            archived = load_json(ARCHIVE_FILE_PATH)
            archived.extend(to_archive)
            save_json(archived, ARCHIVE_FILE_PATH)
        else:
            save_json(to_archive, ARCHIVE_FILE_PATH)

        # Save main data after removing archived bookings
        self._save()

        self.logger.info("Archived %d bookings", len(to_archive))
        return True

    def _md5_of_dict(self, data):
        # Ensure consistent ordering to get a consistent hash
        # Convert dict into a string of bytes for use with hashlib
        encoded = json.dumps(data, sort_keys=True).encode()
        return hashlib.md5(encoded).hexdigest()

    def _find_booking_by_md5(self, target_md5):
        for booking in self.data.get("bookings", {}).values():
            if isinstance(booking, dict) and booking.get("original_sheet_md5") == target_md5:
                return True

        return False

    def add_new_data(self, all_sheets):
        """Function to load a sheet of data in dict format into our booking structure

        Args:
            sheet_bookings (list): list of dict from sheets
            booking_type (class BookingType ENUM): What the booking type is

        Returns:
            int: number of bookings added
        """

        bookings_added = 0

        if "timestamp" in all_sheets and all_sheets["timestamp"]:

            # Sheets records timestamp in ISO format.  Convert to dt object
            self.data["timestamp"] = parse_iso_datetime(all_sheets["timestamp"])

            #
            ## Need to normalise the new data from Sheet to our structure
            for single_sheet in all_sheets["data"]:

                booking_type = single_sheet.get("type")

                for sb in single_sheet.get("sheet_data"):

                    #
                    ## Create MD5 of sheet line item so we can track if its new or seen before
                    new_booking_md5 = self._md5_of_dict(sb)

                    if not self._find_booking_by_md5(new_booking_md5):

                        start_dt = datetime.strptime(sb["arrival_date_time"], "%d/%m/%Y %H:%M:%S")
                        start_dt = start_dt.replace(tzinfo=UK_TZ)

                        # Parse the departure time and replace the time part of arrival
                        dep_time = datetime.strptime(sb["departure_time"], "%H:%M:%S").time()
                        end_dt = start_dt.replace(
                            hour=dep_time.hour, minute=dep_time.minute, second=0
                        )
                        end_dt = start_dt.replace(tzinfo=UK_TZ)

                        existing_ids = list(self.data["bookings"].keys())
                        new_booking_id = gen_next_booking_id(
                            existing_ids, booking_type, start_dt.year
                        )

                        new_booking = {
                            new_booking_id: {
                                "original_sheet_md5": new_booking_md5,
                                "original_sheet_data": sb,
                                "booking_type": booking_type,
                                "Group": sb["chelmsford_scout_group"],
                                "Leader": sb["name_of_lead_person"],
                                "Arriving": start_dt,
                                "Departing": end_dt,
                                "Number": sb["number_of_people"],
                                "Status": "New",
                                "invoice": False,
                                "confirmation_email_sent": False,
                                "google_calendar_id": None,
                                "Notes": "",
                            }
                        }

                        self._add_to_notes(new_booking.get(new_booking_id), "Pulled from sheets")
                        self.data["bookings"].update(new_booking)
                        bookings_added += 1

            self._save()

        return bookings_added
