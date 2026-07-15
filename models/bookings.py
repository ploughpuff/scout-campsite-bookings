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
from collections import defaultdict, Counter

from flask import flash
from pydantic import BaseModel, ValidationError

from config import (
    ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS,
    ARCHIVE_FILE_PATH,
    DATA_FILE_PATH,
    FIELD_MAPPINGS_DICT,
    UK_TZ,
)
from models.calendar import (
    del_cal_event,
    delete_calendar_entry,
    get_cal_events,
    update_calendar_entry,
)
from models import xero
from models.json_utils import load_json, save_json
from models.mailer import (
    send_confirm_numbers_email,
    send_email_notification,
    send_invoice_email,
)
from models.schemas import ArchiveData, BookingData, LeaderData, LiveBooking, LiveData, TrackingData
from models.utils import (
    SortedFacilities,
    estimate_cost,
    get_booking_prefix,
    get_event_type,
    get_timestamp_for_notes,
    is_email_enabled,
    is_xero_enabled,
    now_uk,
    secs_to_hr,
    sort_facilities,
)

#
## Display order on the All Bookings page: Invoice first so bookings
## awaiting an invoice are dealt with promptly
status_options = ["Invoice", "New", "Pending", "Confirmed", "Completed", "Archived", "Cancelled"]

#
## Valid transitions to control buttons on html, and filter user input
status_transitions = {
    "New": ["Pending", "Confirmed", "Cancelled"],
    "Pending": ["Confirmed", "Cancelled"],
    "Confirmed": ["Cancelled"],
    "Invoice": ["Completed"],
    "Completed": [],
    "Archived": [],
    "Cancelled": ["New"],
}


def test_only(func):
    """Decorator to stop test functions being available in production"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if os.getenv("APP_ENV") != "test":
            raise RuntimeError("test_only function called outside of test environment")
        return func(*args, **kwargs)

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

    @test_only
    def set_test_data(self, bookings, archive):
        """Setter method to overwrite data for testing purposes"""
        self.live = bookings
        self.archive = archive

    def load(self, use_checksum: bool = False):
        """Reload the bookings json file from disk. Create empty structure if file not found"""
        self.live = load_json(DATA_FILE_PATH, LiveData, use_checksum)
        self.archive = load_json(ARCHIVE_FILE_PATH, ArchiveData, use_checksum)

    def _get_booking_by_id(self, booking_id: str) -> LiveBooking:
        """Return booking with the matching booking id"""
        return next((rec for rec in self.live.items if rec.booking.id == booking_id), None)

    def get_states(self):
        """Reveal the various status names and their valid transitions.

        Returns:
            dict: "name" of states, and "transition" list of valid transitions.
        """
        # Programmatically extract the options from a Literal field in a Pydantic model
        # using the __annotations__ and typing.get_args
        status_type = TrackingData.__annotations__["status"]

        return {"names": get_args(status_type), "transitions": status_transitions}

    def age(self):
        """
        String showing the age of the bookings or when they were last retrieved from sheets

        Returns:
            str: Either 'NEVER' if not data exists, or string like '1d 5h 35m 17s'
        """
        return secs_to_hr((now_uk() - self.live.updated).total_seconds())

    def _estimate_cost(self, b: BookingData) -> int:
        """Estimate the cost of a booking in pence"""
        return estimate_cost(
            b.event_type,
            b.num_overnights(),
            b.group_type,
            b.group_size,
            b.facilities,
            nightly_sizes=b.nightly_size_list() if b.nightly_group_sizes else None,
        )

    def _can_transition(self, from_status, to_status):
        return to_status in status_transitions.get(from_status, [])

    def get_yearly_stats(self) -> dict:
        """
        Build statistics for every year present in the booking data.
        Returns a list of dicts sorted by year descending.
        """

        open_statuses = {
            "New",
            "Pending",
            "Confirmed",
        }
        day_events = {"day", "eve"}

        years = defaultdict(
            lambda: {
                "day_total_visitors": 0,
                "day_group_counter": Counter(),
                "ovr_total_campers": 0,
                "ovr_group_counter": Counter(),
            }
        )

        open_bookings = 0

        for rec in self.live.items:

            # Ignore cancelled bookings
            if rec.tracking.status == "Cancelled":
                continue

            # Count open bookings
            if rec.tracking.status in open_statuses:
                open_bookings += 1
                continue

            year = rec.booking.arriving.year
            if not year:
                continue

            if rec.booking.event_type in day_events:
                years[year]["day_total_visitors"] += rec.booking.group_size
                years[year]["day_group_counter"][rec.booking.group_name] += 1
            else:
                years[year]["ovr_total_campers"] += rec.booking.group_size
                years[year]["ovr_group_counter"][rec.booking.group_name] += 1

        for rec in self.archive.items:

            # Now the archive bookings
            year = rec.arriving.year
            if not year:
                continue

            if rec.event_type in day_events:
                years[year]["day_total_visitors"] += rec.group_size
                years[year]["day_group_counter"][rec.group_name] += 1
            else:
                years[year]["ovr_total_campers"] += rec.group_size
                years[year]["ovr_group_counter"][rec.group_name] += 1

        # ---- Convert to sorted output ----
        output = []

        for year in sorted(years.keys(), reverse=True):
            data = years[year]

            output.append(
                {
                    "year": year,
                    "day_total_visitors": data["day_total_visitors"],
                    "day_total_groups": len(data["day_group_counter"]),
                    "ovr_total_campers": data["ovr_total_campers"],
                    "ovr_total_groups": len(data["ovr_group_counter"]),
                    "day_groups": data["day_group_counter"].most_common(),
                    "ovr_groups": data["ovr_group_counter"].most_common(),
                }
            )

        return {"open_bookings": open_bookings, "years": output}

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

        for rec in self.live.items:
            if booking_id and rec.booking.id != booking_id:
                continue
            if booking_state and rec.tracking.status != booking_state:
                continue
            if date_range:
                arriving = rec.booking.arriving
                departing = rec.booking.departing
                if not arriving or not departing:
                    continue
                start, end = date_range
                if not (start < departing and end > arriving):
                    continue
            rec_copy = copy.deepcopy(rec)
            results.append(rec_copy)

        # Sort by status index then arrival datetime
        results.sort(
            key=lambda rec: (
                status_options.index(rec.tracking.status),
                rec.booking.arriving or datetime.min,
            )
        )

        return results

    def get_archive_list(self) -> List[BookingData]:
        """Returns the list of archived bookings"""
        return self.archive.items

    def change_status(self, booking_id: str, new_status: str, description: str = None):
        """Change the status of a single booking.

        Args:
            booking_id (str): The booking ID to modify
            new_status (str): Proposed new status value
            description (str, optional): cancel_reason or pending question text. Defaults to None.

        Returns:
            Boolean: True if change made, else False
        """
        rec = self._get_booking_by_id(booking_id)

        if not rec:
            self.logger.warning("Booking ID return no records: [%s]", booking_id)
            return False

        old_status = rec.tracking.status

        #
        ## Validate BEFORE mutating the status: bailing out after the mutation
        ## would leave an unsaved, unnoted status change in memory that the
        ## next save of any booking silently persists (no history trail)
        if new_status in ("Cancelled", "Pending") and not description:
            msg = (
                "Cancellation reason is required."
                if new_status == "Cancelled"
                else "Reason for pending or question to requester is required."
            )
            flash(msg, "danger")
            return False

        if not self._apply_status_change(rec, new_status):
            return False

        if new_status == "Cancelled":
            rec.tracking.cancel_reason = description
            self._add_to_notes(rec.tracking, f"Cancel Reason: {description}")
        elif new_status == "Pending":
            rec.tracking.pend_question = description
            self._add_to_notes(rec.tracking, f"Pend Question: {description}")

        self._add_to_notes(rec.tracking, f"Status changed [{old_status}] > [{new_status}]")
        if send_email_notification(rec):
            self._add_to_notes(rec.tracking, f"Email Sent: change_status: {rec.leader.email}")
        update_calendar_entry(rec)
        save_json(self.live, DATA_FILE_PATH)
        return True

    def resend_email(self, booking_id):
        """Resend the last type of email again"""
        rec = self._get_booking_by_id(booking_id)
        if send_email_notification(rec, "RESEND"):
            self._add_to_notes(rec.tracking, f"Email Sent: resend_email: {rec.leader.email}")
            save_json(self.live, DATA_FILE_PATH)

    def request_confirm_numbers(self, booking_id):
        """Email the leader asking them to confirm final attendance numbers.

        Only valid while awaiting an invoice; the leader replies with the
        confirmed per-day numbers so staff can price the invoice accurately.
        """
        rec = self._get_booking_by_id(booking_id)

        if not rec or rec.tracking.status != "Invoice":
            reason = "not found" if not rec else f"not awaiting an invoice ({rec.tracking.status})"
            flash(f"Booking {booking_id} is {reason}", "danger")
            return

        if send_confirm_numbers_email(rec):
            rec.tracking.numbers_email_sent = now_uk()
            self._add_to_notes(
                rec.tracking, f"Email Sent: confirm numbers request: {rec.leader.email}"
            )
            save_json(self.live, DATA_FILE_PATH)

    def raise_xero_invoice(
        self,
        booking_id: str,
        contact_id: str = None,
        contact_name: str = None,
        create_contact: bool = False,
    ) -> dict:
        """Raise an AUTHORISED invoice in Xero for a booking and mark it Completed.

        contact_id/contact_name or create_contact come from the confirmation page
        when the group could not be matched to a Xero contact automatically.

        Returns a dict: {"ok": bool} normally, or
        {"ok": False, "needs_contact": True, "candidates": [...]} when the caller
        must ask the user to pick or create the Xero contact.
        """
        rec = self._get_booking_by_id(booking_id)

        if not rec or rec.tracking.status != "Invoice":
            reason = "not found" if not rec else f"not awaiting an invoice ({rec.tracking.status})"
            flash(f"Booking {booking_id} is {reason}", "danger")
            return {"ok": False}

        #
        ## Already raised (e.g. crash before completing, or double-click): don't
        ## create a second invoice, just finish the status change
        if rec.booking.xero_invoice_id:
            flash(
                f"Xero invoice {rec.booking.xero_invoice_number} was already raised "
                f"for {booking_id} - marking booking Completed",
                "info",
            )
            self._complete_after_invoice(rec)
            return {"ok": True}

        #
        ## Dry run: unlike email, nothing at all may change - the status change
        ## IS "invoice raised", so it must wait for a real run
        if not is_xero_enabled():
            flash(
                f"[XERO DISABLED] Would raise invoice for {booking_id} "
                f"(£{rec.tracking.cost_estimate / 100:.2f}) "
                f"to Xero contact for [{rec.booking.group_name}]",
                "info",
            )
            return {"ok": False}

        try:
            contact_id = self._resolve_xero_contact(rec, contact_id, contact_name, create_contact)
            if not contact_id:
                # First encounter for this group - always ask the user to confirm
                return {
                    "ok": False,
                    "needs_contact": True,
                    "candidates": self._xero_contact_candidates(rec.booking.group_name),
                }

            # Belt-and-braces: adopt an invoice already carrying our reference
            # (covers a crash between Xero creating it and us saving the ID)
            inv = xero.find_invoice_by_reference(rec.booking.id)
            if inv:
                self._add_to_notes(
                    rec.tracking, f"Existing Xero invoice {inv['invoice_number']} adopted"
                )
            else:
                inv = xero.create_invoice(rec, contact_id)
                self._add_to_notes(rec.tracking, f"Xero invoice {inv['invoice_number']} raised")

            rec.booking.xero_invoice_id = inv["invoice_id"]
            rec.booking.xero_invoice_number = inv["invoice_number"]
            # Persist the invoice ID before emailing so a retry can never double-invoice
            save_json(self.live, DATA_FILE_PATH)

        except xero.XeroError as e:
            self.logger.error("Xero invoice for %s failed: %s", booking_id, e)
            flash(str(e), "danger")
            return {"ok": False}

        # The invoice is now live in Xero, so emailing is best-effort: a failure
        # here must never strand the booking in Invoice status (it would leave a
        # raised invoice the app thinks is still pending). Always complete.
        try:
            self._email_xero_invoice(rec, inv)
        except Exception:  # pylint: disable=broad-except
            self.logger.exception("Emailing invoice for %s failed", booking_id)
            self._add_to_notes(
                rec.tracking,
                f"Invoice {rec.booking.xero_invoice_number} email errored - send it manually",
            )
        self._complete_after_invoice(rec)
        return {"ok": True}

    def _email_xero_invoice(self, rec: LiveBooking, inv: dict, action: str = "raised"):
        """Email the invoice (PDF + pay link) to the booking's leader from the app"""
        number = rec.booking.xero_invoice_number

        if not is_email_enabled():
            self._add_to_notes(rec.tracking, f"Invoice {number} email SKIPPED (email disabled)")
            flash(f"Invoice {number} {action}. Email disabled - send it manually.", "warning")
            return

        pdf_bytes = None
        try:
            pdf_bytes = xero.get_invoice_pdf(rec.booking.xero_invoice_id)
        except xero.XeroError as e:
            self.logger.error("Invoice PDF fetch for %s failed: %s", rec.booking.id, e)

        online_url = xero.get_online_invoice_url(rec.booking.xero_invoice_id)

        if send_invoice_email(rec, number, online_url, pdf_bytes, inv.get("due_date")):
            self._add_to_notes(
                rec.tracking, f"Invoice {number} emailed to leader: {rec.leader.email}"
            )
            flash(f"Invoice {number} {action} and emailed to {rec.leader.email}", "success")
        else:
            self._add_to_notes(rec.tracking, f"Invoice {number} email FAILED: {rec.leader.email}")
            flash(
                f"Invoice {number} {action} but emailing it failed - send it from Xero.", "warning"
            )

    def resend_invoice_email(self, booking_id) -> bool:
        """Re-email an already-raised Xero invoice to the leader.

        For when the original send failed (e.g. no connectivity). Only the email
        is re-sent; the Xero invoice itself is not touched.
        """
        rec = self._get_booking_by_id(booking_id)

        if not rec or rec.tracking.status != "Completed" or not rec.booking.xero_invoice_id:
            reason = (
                "not found"
                if not rec
                else f"not an invoiced Completed booking ({rec.tracking.status})"
            )
            flash(f"Cannot resend invoice: booking {booking_id} is {reason}", "danger")
            return False

        if not is_xero_enabled():
            flash(
                f"[XERO DISABLED] Would resend invoice {rec.booking.xero_invoice_number} "
                f"email for {booking_id}",
                "info",
            )
            return False

        # Refetch so the email carries the current due date and to confirm the
        # invoice is still live (not voided) before emailing.
        inv = xero.find_invoice_by_reference(rec.booking.id)
        if not inv:
            flash(f"No live Xero invoice found for {booking_id} to resend", "danger")
            return False

        self._email_xero_invoice(rec, inv, action="resent")
        save_json(self.live, DATA_FILE_PATH)
        return True

    def amend_xero_invoice(
        self,
        booking_id: str,
        nightly_sizes: dict[str, int] | None = None,
        group_size: int | None = None,
    ) -> bool:
        """Correct the people numbers on an invoiced booking and update its Xero
        invoice in place (same invoice number), then re-email it.

        The booking record is only mutated after Xero accepts the update, so a
        refused amendment (paid/voided invoice, API error) changes nothing.
        """
        rec = self._get_booking_by_id(booking_id)

        if not rec or rec.tracking.status != "Completed" or not rec.booking.xero_invoice_id:
            reason = (
                "not found"
                if not rec
                else f"not an invoiced Completed booking ({rec.tracking.status})"
            )
            flash(f"Cannot amend invoice: booking {booking_id} is {reason}", "danger")
            return False

        # Apply the new sizes to a deep copy so a Xero failure leaves the record untouched
        candidate = rec.model_copy(deep=True)
        b = candidate.booking

        if nightly_sizes:
            values = list(nightly_sizes.values())
            # A uniform headcount needs no per-night breakdown
            b.nightly_group_sizes = None if len(set(values)) == 1 else dict(nightly_sizes)
            b.group_size = max(values)
        elif group_size:
            b.nightly_group_sizes = None
            b.group_size = group_size
        else:
            flash("No people numbers supplied", "danger")
            return False

        candidate.tracking.cost_estimate = self._estimate_cost(b)

        if (
            b.group_size == rec.booking.group_size
            and b.nightly_group_sizes == rec.booking.nightly_group_sizes
            and candidate.tracking.cost_estimate == rec.tracking.cost_estimate
        ):
            flash(f"No changes to booking {booking_id} - invoice left as-is", "info")
            return False

        #
        ## Dry run: nothing at all may change - the Xero update IS the amendment
        if not is_xero_enabled():
            flash(
                f"[XERO DISABLED] Would amend invoice {rec.booking.xero_invoice_number} "
                f"for {booking_id} to £{candidate.tracking.cost_estimate / 100:.2f}",
                "info",
            )
            return False

        try:
            inv = xero.update_invoice(candidate, rec.booking.xero_invoice_id)
        except xero.XeroError as e:
            self.logger.error("Xero invoice amend for %s failed: %s", booking_id, e)
            flash(str(e), "danger")
            return False

        for key in ("group_size", "nightly_group_sizes"):
            old_value, new_value = getattr(rec.booking, key), getattr(b, key)
            if old_value != new_value:
                setattr(rec.booking, key, new_value)
                self._add_to_notes(rec.tracking, f"{key} changed from [{old_value}] to [{new_value}]")
        if rec.tracking.cost_estimate != candidate.tracking.cost_estimate:
            self._add_to_notes(
                rec.tracking,
                f"Cost estimate changed from [{rec.tracking.cost_estimate}] "
                + f"to [{candidate.tracking.cost_estimate}]",
            )
            rec.tracking.cost_estimate = candidate.tracking.cost_estimate
        self._add_to_notes(
            rec.tracking, f"Xero invoice {inv['invoice_number']} amended (line items replaced)"
        )

        # Xero already holds the amendment, so a calendar failure must not lose it
        try:
            update_calendar_entry(rec)
        except Exception:  # pylint: disable=broad-exception-caught
            self.logger.exception("Calendar update failed for %s after invoice amend", booking_id)

        # Persist the amendment before emailing so an email failure can't lose it
        save_json(self.live, DATA_FILE_PATH)

        self._email_xero_invoice(rec, inv, action="amended")
        return True

    def get_xero_link(self, group_name: str) -> dict:
        """The saved Xero contact link for a group, or None. File read only."""
        return xero.get_contact_mapping(group_name)

    def get_xero_contact_urls(self, recs) -> dict:
        """Xero contact page URL per group name, for the bookings given. File read only."""
        return xero.get_contact_urls({rec.booking.group_name for rec in recs})

    def link_xero_contact(
        self,
        booking_id: str,
        contact_id: str = None,
        contact_name: str = None,
        create_contact: bool = False,
    ) -> dict:
        """Link a booking's group to a Xero contact ahead of invoicing.

        Xero is the source of truth for group names: choosing an existing
        contact renames the booking's group_name to the contact's name
        (recorded in the history), fixing typos from the submission form.

        Returns {"ok": bool} or {"ok": False, "needs_contact": True,
        "candidates": [...]} when the caller must show the picker.
        """
        rec = self._get_booking_by_id(booking_id)

        if not rec or rec.tracking.status in ("Completed", "Archived", "Cancelled"):
            reason = "not found" if not rec else f"already {rec.tracking.status}"
            flash(f"Cannot link Xero contact: booking {booking_id} is {reason}", "danger")
            return {"ok": False}

        group_name = rec.booking.group_name

        if xero.get_mapped_contact(group_name):
            flash(f"[{group_name}] is already linked to a Xero contact", "info")
            return {"ok": True}

        try:
            if create_contact:
                contact = xero.create_contact(group_name, rec.leader)
                contact_id = contact["ContactID"]
                contact_name = contact["Name"]
                self._add_to_notes(rec.tracking, f"Xero contact created: [{contact_name}]")
            elif contact_id:
                self._add_to_notes(
                    rec.tracking, f"Xero contact linked: [{contact_name or contact_id}]"
                )
            else:
                return {
                    "ok": False,
                    "needs_contact": True,
                    "candidates": self._xero_contact_candidates(group_name),
                }
        except xero.XeroError as e:
            self.logger.error("Xero contact link for %s failed: %s", booking_id, e)
            flash(str(e), "danger")
            return {"ok": False}

        # Key the mapping by the Xero name - after the rename below, future
        # bookings under this (correct) name bind without asking
        xero.save_contact_mapping(contact_name or group_name, contact_id, contact_name or "")

        if contact_name and contact_name.strip() != group_name:
            self._add_to_notes(
                rec.tracking,
                f"group_name changed from [{group_name}] to [{contact_name.strip()}] "
                "(matched to Xero contact)",
            )
            rec.booking.group_name = contact_name.strip()
            update_calendar_entry(rec)

        save_json(self.live, DATA_FILE_PATH)
        flash(f"[{rec.booking.group_name}] linked to Xero contact", "success")
        return {"ok": True}

    def _resolve_xero_contact(
        self, rec: LiveBooking, contact_id: str, contact_name: str, create_contact: bool
    ) -> str:
        """Work out which Xero contact to invoice, remembering the group mapping.

        Returns the ContactID, or None when no match was found and the user
        must confirm the contact first.
        """
        group_name = rec.booking.group_name

        if contact_id:
            # User picked an existing Xero contact on the confirmation page
            xero.save_contact_mapping(group_name, contact_id, contact_name or "")
            self._add_to_notes(
                rec.tracking, f"Xero contact confirmed: [{contact_name or contact_id}]"
            )
            return contact_id

        if create_contact:
            # User confirmed creating a brand-new Xero contact
            contact = xero.create_contact(group_name, rec.leader)
            xero.save_contact_mapping(group_name, contact["ContactID"], contact["Name"])
            self._add_to_notes(rec.tracking, f"Xero contact created: [{contact['Name']}]")
            return contact["ContactID"]

        # Only a previously confirmed mapping binds silently - a group's first
        # invoice always goes through the confirmation page, even on exact match
        return xero.get_mapped_contact(group_name)

    def _xero_contact_candidates(self, group_name: str) -> list:
        """Candidate Xero contacts for the confirmation page, exact name match first"""
        candidates = xero.search_contacts(group_name)

        exact = xero.find_contact_by_name(group_name)
        if exact:
            entry = {
                "contact_id": exact["ContactID"],
                "name": exact["Name"],
                "email": exact.get("EmailAddress", ""),
            }
            candidates = [entry] + [c for c in candidates if c["contact_id"] != entry["contact_id"]]

        return candidates

    def _complete_after_invoice(self, rec: LiveBooking):
        """Move an invoiced booking to Completed and persist"""
        old_status = rec.tracking.status
        if self._apply_status_change(rec, "Completed"):
            self._add_to_notes(rec.tracking, f"Status changed [{old_status}] > [Completed]")
            update_calendar_entry(rec)
        save_json(self.live, DATA_FILE_PATH)

    def _update_cost_estimate(self, rec: LiveData):
        #
        ## Recalculate the cost estimate, but only if its non-zero
        ## so that manually setting a booking to FREE is not lost
        if rec.tracking.cost_estimate > 0:
            cost_estimate = self._estimate_cost(rec.booking)

            if rec.tracking.cost_estimate != cost_estimate:
                self._add_to_notes(
                    rec.tracking,
                    f"Cost estimate changed from [{rec.tracking.cost_estimate}] "
                    + f"to [{cost_estimate}]",
                )
                rec.tracking.cost_estimate = cost_estimate

    def modify_fields(self, booking_id, update_data: dict) -> bool:
        """Modify fields in the booking from the html page.

        Args:
            booking_id (str): The booking ID to modify fields on.
            updates (dict): Dictionary of kv pair to modify.

        Returns:
            bool: True is successful
        """

        rec = self._get_booking_by_id(booking_id)

        if not rec:
            flash(
                f"Cannot update booking {booking_id} as not found in database.",
                "danger",
            )
            return False

        changed_keys = []

        for section, fields in update_data.items():
            if not hasattr(rec, section):
                self.logger.warning("Unknown section '%s' in update data", section)
                continue

            original = getattr(rec, section)

            try:
                # Merge and re-validate in one go
                merged = {**original.model_dump(mode="python"), **fields}
                updated = original.__class__.model_validate(merged)

            except ValidationError as e:
                self.logger.warning("Validation failed for %s update: %s", section, e)
                continue

            for key in original.model_fields:
                if key == "notes":
                    continue
                old_value = getattr(original, key)
                new_value = getattr(updated, key)
                if old_value != new_value:
                    changed_keys.append(key)
                    setattr(original, key, new_value)

                    # Optionally format datetime changes nicely
                    if isinstance(new_value, datetime):
                        old_value = get_timestamp_for_notes(old_value)
                        new_value = get_timestamp_for_notes(new_value)

                    self.logger.info("Updated booking %s: %s = %s", booking_id, key, new_value)

                    self._add_to_notes(
                        rec.tracking, f"{key} changed from [{old_value}] to [{new_value}]"
                    )

        if changed_keys:
            # Redo the event type as a time change may have altered it
            rec.booking.event_type = get_event_type(rec.booking.arriving, rec.booking.departing)

            # Per-night sizes are keyed by date, so a date change invalidates them
            if (
                rec.booking.nightly_group_sizes
                and "nightly_group_sizes" not in changed_keys
                and any(key in ("arriving", "departing") for key in changed_keys)
            ):
                rec.booking.nightly_group_sizes = None
                self._add_to_notes(rec.tracking, "nightly group sizes cleared - dates changed")

            # Only update the cost estimate if it was not in the update payload otherwise
            # the manual cost estimate is lost
            if "cost_estimate" not in changed_keys:
                self._update_cost_estimate(rec)

            # Only send modified emails if the booking is confirmed.
            # This avoids sending emails for pending, and then another staight after for comfirmed
            if (
                any(
                    key
                    in ["arriving", "departing", "group_size", "nightly_group_sizes", "facilities"]
                    for key in changed_keys
                )
                and rec.tracking.status == "Confirmed"
            ):
                if send_email_notification(rec, "MODIFIED"):
                    self._add_to_notes(
                        rec.tracking, f"Email Sent: modified_fields: {rec.leader.email}"
                    )
            update_calendar_entry(rec)
            save_json(self.live, DATA_FILE_PATH)

        return bool(changed_keys)

    def _apply_status_change(self, rec: LiveBooking, to_status: str):

        if not rec:
            return False

        from_status = rec.tracking.status

        if not self._can_transition(from_status, to_status):
            msg = f"Invalid transition for {rec.booking.id}: {from_status} > {to_status}"
            flash(msg, "danger")
            self.logger.warning(msg)
            return False

        #
        ## Before blindly transitioning to the new state, if its Cancel>New check the arrival time
        ## is still in the future.  We don't want to resurrest past bookings
        if from_status == "Cancelled" and to_status == "New":
            if rec.booking.arriving < now_uk():
                msg = f"Unable to resurrect booking {rec.booking.id}: arrival date is in the past!"
                flash(msg, "warning")
                return False

        rec.tracking.status = to_status
        return True

    def _add_to_notes(self, tracking: TrackingData, new_note: str):

        timestamp = get_timestamp_for_notes(include_seconds=True)
        new_note_entry = f"[{timestamp}]: {new_note}"

        old_value = tracking.notes
        tracking.notes = new_note_entry + ("\n" + old_value if old_value else "")

    def auto_update_statuses(self):
        """Look for bookings to automatically change the status of"""

        for rec in self.live.items:
            #
            ## Move confirmed bookings to completed/invoice once departure dates has passed
            if (
                rec.booking.departing.tzinfo is None
                or rec.booking.departing.tzinfo.utcoffset(rec.booking.departing) is None
            ):
                self.logger.warning(
                    "Departing time for %s is offset-naive [%s]",
                    rec.booking.id,
                    rec.booking.departing,
                )
                continue

            if rec.tracking.status == "Confirmed" and rec.booking.departing < now_uk():
                new_status = "Invoice" if rec.tracking.cost_estimate > 0 else "Completed"
                self._add_to_notes(
                    rec.tracking, f"Auto Status Change: [{rec.tracking.status}] > [{new_status}]"
                )
                rec.tracking.status = new_status
                save_json(self.live, DATA_FILE_PATH)
                flash(
                    f"{rec.booking.id} Auto Status Change: From: [Comfirmed] "
                    f"To: [{new_status}] now booking has passed",
                    "warning",
                )

    def fix_cal_events(self, dry_run: bool = True) -> dict:
        """Attempt to fix the calendar entries using latest live data"""

        cal_events = get_cal_events()
        event_ids = set(event["id"] for event in cal_events)
        good, missing, delete, extra = [], [], [], []
        now = now_uk()

        def should_have_event(rec):
            if rec.tracking.status in ["Confirmed", "Invoice"]:
                return True
            if rec.tracking.status == "Completed":
                archive_date = rec.booking.departing + timedelta(
                    days=ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS
                )
                return archive_date > now
            return False

        def should_delete_event(rec):
            if rec.tracking.status in ["New", "Pending", "Archived", "Cancelled"]:
                return True
            if rec.tracking.status == "Completed":
                archive_date = rec.booking.departing + timedelta(
                    days=ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS
                )
                return archive_date <= now
            return False

        for rec in self.live.items:
            cal_id = rec.tracking.google_calendar_id
            has_event = cal_id in event_ids

            if should_have_event(rec):
                if has_event:
                    good.append(rec)
                    event_ids.remove(cal_id)
                else:
                    if dry_run:
                        missing.append(rec)
                    else:
                        rec.tracking.google_calendar_id = ""
                        update_calendar_entry(rec)
                        save_json(self.live, DATA_FILE_PATH)

            elif should_delete_event(rec):
                if has_event:
                    event_ids.remove(cal_id)

                    if dry_run:
                        delete.append(rec)
                    else:
                        delete_calendar_entry(rec)
                        rec.tracking.google_calendar_id = ""
                        save_json(self.live, DATA_FILE_PATH)

        # Remaining event_ids are "extra"
        for event_id in event_ids:
            if dry_run:
                extra.append(event_id)
            else:
                del_cal_event(event_id, f"Raw id {event_id}")

        return {"good": good, "missing": missing, "delete": delete, "extra": extra}

    def archive_old_bookings(self) -> bool:
        """
        Archive or delete old bookings:
        - 'Completed' bookings 90+ days after departure are archived.
        - 'Cancelled' bookings 90+ days after departure are deleted.
        """
        now = now_uk()
        to_archive = []
        remaining_live = []

        for rec in self.live.items:
            archive_date = rec.booking.departing + timedelta(
                days=ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS
            )

            # Keep if not yet due for archiving/deletion
            if archive_date > now:
                remaining_live.append(rec)
                continue

            if rec.tracking.status == "Completed":
                delete_calendar_entry(rec)

                # Deep copy only the booking part (exclude GDPR-related data)
                archive_copy = copy.deepcopy(rec.booking)
                to_archive.append(archive_copy)

                self.logger.info("%s archived", rec.booking.id)

            elif rec.tracking.status == "Cancelled":
                self.logger.info("%s cancelled booking deleted (not archived)", rec.booking.id)

            else:
                # Keep all other statuses
                remaining_live.append(rec)

        # Update live items
        self.live.items = remaining_live

        if not to_archive:
            self.logger.info("No bookings to archive.")
            return False

        # Append to archive (create new list if first time)
        if ARCHIVE_FILE_PATH.exists():
            self.archive.items.extend(to_archive)
        else:
            self.archive.items = to_archive

        # Save updated files
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
        if any(rec.booking.original_sheet_md5 == target_md5 for rec in self.live.items):
            return True
        if any(rec.original_sheet_md5 == target_md5 for rec in self.archive.items):
            return True
        return False

    def add_new_data(self, all_sheets) -> int:
        """Function to load a sheet of data in dict format into our booking structure

        Args:
            sheet_bookings (list): list of dict from sheets

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

                for row in single_sheet["sheet_data"]:

                    #
                    ## Create MD5 of sheet line item so we can track if its new or seen before
                    new_booking_md5 = self._md5_of_dict(row)

                    if not self._find_booking_by_md5(new_booking_md5):

                        rec = self.create_rec_from_sheet_row(
                            row,
                            new_booking_md5,
                            single_sheet.get("group_type"),
                            single_sheet.get("contains"),
                        )

                        self._add_to_notes(rec.tracking, "Pulled from sheets")
                        self.live.items.append(rec)
                        self.live.next_idx += 1
                        self.logger.info("New booking added: %s", rec.booking.id)
                        added += 1

            save_json(self.live, DATA_FILE_PATH)
        return added

    def create_rec_from_sheet_row(
        self, row: dict, original_sheet_md5: str, group_type: str, contains: str
    ) -> LiveBooking:
        """Create a booking record from a row of data from Google sheet using field mappings
        from JSON file"""

        submitted_dt = datetime.strptime(row["timestamp"], "%d/%m/%Y %H:%M:%S").replace(
            tzinfo=UK_TZ
        )

        # Arrival date/time is common
        start_dt = datetime.strptime(row["arrival_date_time"], "%d/%m/%Y %H:%M:%S").replace(
            tzinfo=UK_TZ
        )

        # Depart time is not common
        if contains == "day_visits":
            dep_time = datetime.strptime(row["departure_time"], "%H:%M:%S").time()
            end_dt = datetime.combine(start_dt.date(), dep_time).replace(tzinfo=UK_TZ)

        else:
            end_dt = datetime.strptime(row["departure_date_time"], "%d/%m/%Y %H:%M:%S").replace(
                tzinfo=UK_TZ
            )

        facilities: SortedFacilities = sort_facilities(row.get("facilities", "").split(","))

        #
        ## Map the google sheet fields to the Bookings class keys in one hit
        leader_fields = {
            key: row[src_field].strip()
            for key, src_field in FIELD_MAPPINGS_DICT.get("key_mapping").get("leader").items()
        }
        booking_fields = {
            key: row[src_field].strip()
            for key, src_field in FIELD_MAPPINGS_DICT.get("key_mapping").get("booking").items()
        }

        # Overide the global group_type if available from the row, or default to passed in type
        # which comes from the field mappings file
        group_type = row.get("group_type") or group_type

        # Construct BookingData, LeaderData and TrackingData separately
        booking_data = {
            **booking_fields,
            "id": f"{get_booking_prefix(group_type)}-{start_dt.year}-{self.live.next_idx:04d}",
            "original_sheet_md5": original_sheet_md5,
            "group_type": group_type,
            "event_type": get_event_type(start_dt, end_dt),
            "submitted": submitted_dt,
            "arriving": start_dt,
            "departing": end_dt,
            "facilities": facilities.valid,
        }

        tracking_data = {
            "status": "New",
            "cost_estimate": self._estimate_cost(BookingData.model_validate(booking_data)),
            "notes": "",
            "bookers_comment": ", ".join(facilities.extra),
            "google_calendar_id": "",
        }

        # Now build full SitePlusLeader record
        try:
            return LiveBooking(
                booking=BookingData.model_validate(booking_data),
                leader=LeaderData.model_validate(leader_fields),
                tracking=TrackingData.model_validate(tracking_data),
            )
        except ValidationError as e:
            self.logger.error("Validation failed for booking data: %s", e.json())
            self.logger.debug(e.json())  # Or use e.errors() for structured error list
            return None
