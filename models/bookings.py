"""
Bookings.py - Manage the bookings data file and provide access functions.
"""

import copy
import functools
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, get_args

from flask import flash
from pydantic import BaseModel, ValidationError

from config import (
    ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS,
    ARCHIVE_FILE_PATH,
    DATA_FILE_PATH,
    UK_TZ,
)
from models.booking_types import BookingType
from models.calendar import update_calendar_entry
from models.json_utils import load_json, save_json
from models.mailer import send_email_notification

# from models.booking_types import
from models.schemas import ArchiveData, LeaderData, LiveData, SiteData, SitePlusLeader
from models.utils import (
    get_timestamp_for_notes,
    now_uk,
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


def test_only(func):
    """Decorator to stop test functions being available in production"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        print(f"test_only wrapper triggered for: {func.__name__}")
        if os.getenv("APP_ENV") != "test":
            raise RuntimeError("test_only function called outside of test environment")
        return func(*args, **kwargs)

    return wrapper


def integrity_check(method):
    """Decorator function to perform integrity check on data"""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        result = method(self, *args, **kwargs)

        try:
            self.check_integrity()
        except RuntimeError as e:
            raise RuntimeError(f"Failed after {method.__name__}(): {e}") from e

        return result

    return wrapper


class Bookings:
    """Class for managing the booking data"""

    def __init__(self):
        self.logger = logging.getLogger("app_logger")

        self.live = self._load_or_initialize(DATA_FILE_PATH, LiveData)
        self.archive = self._load_or_initialize(ARCHIVE_FILE_PATH, ArchiveData)

    def _load_or_initialize(self, path: Path, model: BaseModel) -> BaseModel:
        if path.exists():
            instance = load_json(path, model)
            if instance:
                return instance
            self.logger.warning("Failed to load or validate [%s], initializing new.", path)
        else:
            self.logger.info("[%s] not found, initializing new.", path)

        # If load failed or file not found, initialize and save new
        instance = model()
        save_json(instance, path)
        return instance

    def check_integrity(self):
        """Helper function via decorator to check the integrity of booking data"""
        problems = []

        for raw_booking in self.live.bookings + self.archive.bookings:
            entry = SiteData(**raw_booking)
            if not entry.is_valid():
                problems.append(
                    {
                        "booking": raw_booking.id,
                        "errors": entry.get_problematic_data(),
                    }
                )

        if problems:
            raise RuntimeError(f"Integrity check failed with problems: {problems}")

    @test_only
    def set_test_data(self, bookings, archive):
        """Setter method to overwrite data for testing purposes"""
        self.live = bookings
        self.archive = archive

    # @integrity_check
    def load(self, use_checksum=False):
        """Reload the bookings json file from disk. Create empty structure if file not found"""
        self.live = load_json(DATA_FILE_PATH, LiveData, use_checksum)
        self.archive = load_json(ARCHIVE_FILE_PATH, ArchiveData, use_checksum)

    def _get_booking_by_id(self, booking_id: str) -> SitePlusLeader:
        """Return booking with the matching booking id"""
        return next((b for b in self.live.bookings if b.site.id == booking_id), None)

    def get_states(self):
        """Reveal the various status names and their valid transitions.

        Returns:
            dict: "name" of states, and "transition" list of valid transitions.
        """
        # Programmatically extract the options from a Literal field in a Pydantic model
        # using the __annotations__ and typing.get_args
        status_type = SiteData.__annotations__["status"]

        return {"names": get_args(status_type), "transitions": status_transitions}

    def age(self):
        """
        String showing the age of the bookings or when they were last retrieved from sheets

        Returns:
            str: Either 'NEVER' if not data exists, or string like '1d 5h 35m 17s'
        """
        return secs_to_hr((now_uk() - self.live.updated).total_seconds())

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

        for booking in self.live.bookings:
            if booking_id and booking.site.id != booking_id:
                continue
            if booking_state and booking.site.status != booking_state:
                continue
            if date_range:
                arriving = booking.site.arriving
                departing = booking.site.departing
                if not arriving or not departing:
                    continue
                start, end = date_range
                if not (start < departing and end > arriving):
                    continue
            booking_copy = copy.deepcopy(booking)
            results.append(booking_copy)

        # Sort by status index then arrival datetime
        results.sort(
            key=lambda b: (
                status_options.index(b.site.status),
                b.site.arriving or datetime.min,
            )
        )

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
        booking = self._get_booking_by_id(booking_id)

        if not booking:
            self.logger.warning("Booking ID return no records: [%s]", booking_id)
            return False

        old_status = booking.site.status

        if not self._apply_status_change(booking, new_status):
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

            if new_status == "Cancelled":
                booking.site.cancel_reason = description
                self._add_to_notes(booking, f"Cancel Reason: {description}")
            else:
                booking.site.pend_question = description
                self._add_to_notes(booking, f"Pend Question: {description}")

        send_email_notification(booking)
        update_calendar_entry(booking)
        self._add_to_notes(booking, f"Status changed [{old_status}] > [{new_status}]")
        save_json(self.live, DATA_FILE_PATH)
        return True

    def modify_fields(self, booking_id, update_data: dict) -> bool:
        """Modify fields in the booking from the html page.

        Args:
            booking_id (str): The booking ID to modify fields on.
            updates (dict): Dictionary of kv pair to modify.

        Returns:
            bool: True is successful
        """

        booking = self._get_booking_by_id(booking_id)

        if not booking:
            flash(
                f"Cannot update booking {booking_id} as not found in database.",
                "danger",
            )
            return False

        for section, fields in update_data.items():
            if not hasattr(booking, section):
                self.logger.warning("Unknown section '%s' in update data", section)
                continue

            original = getattr(booking, section)

            try:
                # Do a partial update on a copy
                updated = original.model_copy(update=fields)

                # Create new BaseModel with the updated fields
                updated = original.__class__.model_validate(updated.model_dump())

            except ValidationError as e:
                self.logger.warning("Validation failed for %s update: %s", section, e)
                continue

            changes = False
            for key in original.model_fields:
                if key == "notes":
                    continue
                old_value = getattr(original, key)
                new_value = getattr(updated, key)
                if old_value != new_value:
                    changes = True
                    setattr(original, key, new_value)

                    # Optionally format datetime changes nicely
                    if isinstance(new_value, datetime):
                        old_value = get_timestamp_for_notes(old_value)
                        new_value = get_timestamp_for_notes(new_value)

                    self.logger.info("Updated booking %s: %s = %s", booking_id, key, new_value)

                    self._add_to_notes(
                        booking, f"{key} changed from [{old_value}] to [{new_value}]"
                    )

        if changes:
            save_json(self.live, DATA_FILE_PATH)

        return changes

    def _apply_status_change(self, booking, to_status):

        if not booking:
            return False

        from_status = booking.site.status

        if not self._can_transition(from_status, to_status):
            msg = f"Invalid transition for {booking.site.id}: {from_status} > {to_status}"
            flash(msg, "danger")
            self.logger.warning(msg)
            return False

        #
        ## Before blindly transitioning to the new state, if its Cancel>New check the arrival time
        ## is still in the future.  We don't want to resurrest past bookings
        if from_status == "Cancelled" and to_status == "New":
            if booking.site.arriving < now_uk():
                msg = f"Unable to resurrect booking {booking.site.id}: arrival date is in the past!"
                flash(msg, "warning")
                return False

        booking.site.status = to_status

        save_json(self.live, DATA_FILE_PATH)
        return True

    def _add_to_notes(self, booking, new_note):

        timestamp = get_timestamp_for_notes(include_seconds=True)
        new_note_entry = f"[{timestamp}]: {new_note}"

        old_value = booking.site.notes
        booking.site.notes = new_note_entry + ("\n" + old_value if old_value else "")

    def auto_update_statuses(self):
        """Look for bookings to automatically change the status of"""

        for booking in self.live.bookings:
            #
            ## Move confirmed bookings to completed/invoice once departure dates has passed
            if (
                booking.site.departing.tzinfo is None
                or booking.site.departing.tzinfo.utcoffset(booking.site.departing) is None
            ):
                self.logger.warning(
                    "Departing time for %s is offset-naive [%s]",
                    booking.site.id,
                    booking.site.departing,
                )
                continue

            if booking.site.status == "Confirmed" and booking.site.departing < now_uk():
                new_status = "Invoice" if booking.site.invoice else "Completed"
                booking.site.status = new_status
                self._add_to_notes(
                    booking, f"Auto Status Change: [{booking.site.status}] > [{new_status}]"
                )
                save_json(self.live, DATA_FILE_PATH)
                flash(
                    f"{booking.site.id} automatically change from {booking.site.status} "
                    f"to {new_status} now booking has passed",
                    "warning",
                )

    def archive_old_bookings(self):
        """Move bookings with status 'Archived' to archive.json and remove from bookings.json."""
        to_archive = []

        for booking in self.live.bookings:
            archive_date = booking.site.departing + timedelta(
                days=ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS
            )

            if booking.site.status == "Completed" and archive_date < now_uk():

                # Take a deep copy of this booking and remove all GDPR data
                archive_copy = copy.deepcopy(booking)
                archive_copy.pop("original_sheet_data", None)
                archive_copy.pop("Leader", None)
                archive_copy["Status"] = "Archived"
                self._add_to_notes(
                    archive_copy, f"Auto Status Change: [{booking.site.status}] > [Archived]"
                )
                to_archive.append(archive_copy)

                # Remove this booking from the main data table
                self.live.site.bookings = [
                    b for b in self.live.site.bookings if b.site.id != booking.site.id
                ]

                self.logger.info("%s archived", booking.site.id)

        if not to_archive:
            self.logger.info("No bookings to archive.")
            return False

        # Handle archive file
        if ARCHIVE_FILE_PATH.exists():
            self.archive.bookings.extend(to_archive)
        else:
            self.archive.bookings = to_archive

        # Save the modified data files to json
        save_json(self.live, DATA_FILE_PATH)
        save_json(self.archive, ARCHIVE_FILE_PATH)
        return True

    def _md5_of_dict(self, data):
        # Ensure consistent ordering to get a consistent hash
        # Convert dict into a string of bytes for use with hashlib
        encoded = json.dumps(data, sort_keys=True).encode()
        return hashlib.md5(encoded).hexdigest()

    def _find_booking_by_md5(self, target_md5: str) -> bool:
        """Look in main table and archive for matching md5"""
        if any(b.site.original_sheet_md5 == target_md5 for b in self.live.bookings):
            return True
        if any(b.original_sheet_md5 == target_md5 for b in self.archive.bookings):
            return True
        return False

    def add_new_data(self, all_sheets) -> int:
        """Function to load a sheet of data in dict format into our booking structure

        Args:
            sheet_bookings (list): list of dict from sheets
            booking_type (class BookingType ENUM): What the booking type is

        Returns:
            int: number of bookings added
        """
        added = 0
        if "updated" in all_sheets and all_sheets["updated"]:

            # Sheets records timestamp in ISO format.  Convert to dt object
            self.live.updated = all_sheets["updated"]

            #
            ## Need to normalise the new data from Sheet to our structure
            for single_sheet in all_sheets["data"]:

                booking_type = single_sheet["booking_type"]

                for row in single_sheet["sheet_data"]:

                    #
                    ## Create MD5 of sheet line item so we can track if its new or seen before
                    new_booking_md5 = self._md5_of_dict(row)

                    if not self._find_booking_by_md5(new_booking_md5):

                        new_booking = self.create_rec_from_sheet_row(row, booking_type)

                        self._add_to_notes(new_booking, "Pulled from sheets")
                        self.live.bookings.append(new_booking)
                        self.live.next_idx += 1
                        self.logger.info("New booking added: %s", new_booking.site.id)
                        added += 1

            save_json(self.live, DATA_FILE_PATH)
        return added

    def create_rec_from_sheet_row(self, row: dict, booking_type: BookingType) -> SiteData:
        """Create a booking record from a row of data from Google sheet using field mappings
        from JSON file"""

        field_map = booking_type.field_map

        submitted_dt = datetime.strptime(row["timestamp"], "%d/%m/%Y %H:%M:%S").replace(
            tzinfo=UK_TZ
        )

        start_dt = datetime.strptime(row["arrival_date_time"], "%d/%m/%Y %H:%M:%S").replace(
            tzinfo=UK_TZ
        )

        dep_time = datetime.strptime(row["departure_time"], "%H:%M:%S").time()
        end_dt = datetime.combine(start_dt.date(), dep_time).replace(tzinfo=UK_TZ)

        #
        ## Map the google sheet fields to the Bookings class keys in one hit
        leader_fields = {key: row[src_field] for key, src_field in field_map["leader"].items()}
        site_fields = {key: row[src_field] for key, src_field in field_map["site"].items()}

        # Construct SiteData and LeaderData separately
        site_data = {
            **site_fields,
            "idx": self.live.next_idx,
            "id": f"{booking_type.prefix}-{start_dt.year}-{self.live.next_idx:04d}",
            "original_sheet_md5": self._md5_of_dict(row),
            "booking_type": booking_type,
            "status": "New",
            "invoice": False,
            "notes": "",
            "google_calendar_id": "",
            "submitted": submitted_dt,
            "arriving": start_dt,
            "departing": end_dt,
        }

        # Now build full SitePlusLeader record
        try:
            site_entry = SiteData.model_validate(site_data)
            leader_data = LeaderData.model_validate(leader_fields)
            return SitePlusLeader(site=site_entry, leader=leader_data)
        except ValidationError as e:
            self.logger.error("Validation failed for booking data: %s", e.json())
            self.logger.debug(e.json())  # Or use e.errors() for structured error list
            return None
