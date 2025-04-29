"""
Bookings.py - Manage the bookings data file and provide access functions.
"""

import copy
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from flask import flash

from config import (
    ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS,
    ARCHIVE_FILE_PATH,
    DATA_FILE_PATH,
    MAX_BACKUPS_TO_KEEP,
    UK_TZ,
)
from models.booking_types import gen_next_booking_id
from models.calendar import update_calendar_entry
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

    def __init__(self):
        self.logger = logging.getLogger("app_logger")
        self.data = {}
        self.data["bookings"] = {}
        self.archive = []
        self.load(use_checksum=True)

    def _save(self):
        save_json(self.data, DATA_FILE_PATH, MAX_BACKUPS_TO_KEEP)

    def load(self, use_checksum=False):
        """Reload the bookings json file from disk. Create empty structure if file not found"""
        self.data = load_json(DATA_FILE_PATH, use_checksum)
        if not self.data:
            self.data = {}
            self.data["bookings"] = {}

        self.archive = load_json(ARCHIVE_FILE_PATH, use_checksum)
        if not self.archive:
            self.archive = []

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

    def _can_transition(self, from_status, to_status):
        return to_status in status_transitions.get(from_status, [])

    def get_bookings_list(
        self,
        date_range: Optional[Tuple[datetime, datetime]] = None,
        booking_id: Optional[str] = None,
        booking_state: Optional[str] = None,
    ) -> List[dict]:
        """
        Return a filtered and sorted list of bookings.

        - Filter by:
            - booking_id: return only the booking with that ID
            - booking_state: return bookings matching that status
            - date_range: return bookings overlapping with the date range
        - Otherwise, return all bookings.

        The result is sorted by (status index, arrival datetime).
        """
        results = []

        for b_id, booking in self.data["bookings"].items():
            if booking_id and b_id != booking_id:
                continue
            if booking_state and booking.get("Status") != booking_state:
                continue
            if date_range:
                arriving = booking.get("Arriving")
                departing = booking.get("Departing")
                if not arriving or not departing:
                    continue
                start, end = date_range
                if not (start < departing and end > arriving):
                    continue
            booking_copy = dict(booking)
            booking_copy["id"] = b_id
            results.append(booking_copy)

        # Sort by status index then arrival datetime
        results.sort(key=lambda b: (status_options.index(b["Status"]), b.get("Arriving") or ""))
        return results

    def get_archive_list(self):
        """Returns the list of archived bookings"""
        return self.archive

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
        if not booking:
            self.logger.warning("Booking ID return no records: [%s]", booking_id)
            return False

        old_status = booking.get("Status")
        if not old_status:
            self.logger.warning("No status for that booking ID: [%s]", booking_id)
            return False

        if not self._apply_status_change(booking_id, new_status):
            return False

        if new_status in ("Cancelled", "Pending"):
            if not description:
                msg = (
                    "Cancellation reason is required."
                    if new_status == "Cancelled"
                    else "Reason for pending or question to requester is required."
                )
                flash(msg, "danger")
                return False

            field = "cancel_reason" if new_status == "Cancelled" else "pend_question"
            self._add_to_notes(booking, f"{field}: {description}")
            booking[field] = description

        send_email_notification(booking_id, booking)
        update_calendar_entry(booking_id, booking)
        self._add_to_notes(booking, f"Status changed [{old_status}] > [{new_status}]")
        self._save()  # Only save if the state transition is valid
        return True

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
                archive_copy["id"] = booking_id
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
            self.archive = archived
        else:
            self.archive = to_archive

        # Save the modified data files to json
        self._save()
        save_json(self.archive, ARCHIVE_FILE_PATH)
        return True

    def _md5_of_dict(self, data):
        # Ensure consistent ordering to get a consistent hash
        # Convert dict into a string of bytes for use with hashlib
        encoded = json.dumps(data, sort_keys=True).encode()
        return hashlib.md5(encoded).hexdigest()

    def _find_booking_by_md5(self, target_md5):
        # Look in main bookings
        for booking in self.data.get("bookings", {}).values():
            if isinstance(booking, dict) and booking.get("original_sheet_md5") == target_md5:
                return True

        # Now look in archive
        for booking in self.archive:
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

                        start_dt = datetime.strptime(
                            sb["arrival_date_time"], "%d/%m/%Y %H:%M:%S"
                        ).replace(tzinfo=UK_TZ)
                        dep_time = datetime.strptime(sb["departure_time"], "%H:%M:%S").time()
                        end_dt = datetime.combine(start_dt.date(), dep_time).replace(tzinfo=UK_TZ)

                        existing_ids = list(self.data.get("bookings", {}).keys())

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
                                "Notes": "",
                            }
                        }

                        self._add_to_notes(new_booking.get(new_booking_id), "Pulled from sheets")
                        self.data["bookings"].update(new_booking)
                        bookings_added += 1

            self._save()

        return bookings_added
