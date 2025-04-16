from flask import flash, jsonify
import json
from pathlib import Path
import logging
import hashlib
import json
import time
from datetime import datetime
from utils import secs_to_hr
from config import CACHE_DIR
from models.Mailer import send_booking_confirmation
from models.booking_types import BookingType, generate_next_booking_id

import re
from datetime import datetime
import config

status_options = ["New", "Confirmed", "Invoice", "Completed", "Cancelled"]

status_transitions = {
    "New":       [       "Confirmed",                         "Cancelled"],
    "Confirmed": [                    "Invoice", "Completed", "Cancelled"],
    "Invoice":   [                               "Completed",            ],
    "Completed": [                                                       ],
    "Cancelled": [                                                       ]
}

class Bookings:
    def __init__(self, calendar=None):
        self.calendar = calendar  # GoogleCalendar instance
        self.logger = logging.getLogger("app_logger")
        self.json_path = Path(CACHE_DIR, "bookings.json")
        self.data = {}
        self._load()
    
    def GetStates(self):
        return {"names": status_options, "transitions": status_transitions}

    def Age(self):
        """
        String showing the age of the bookings or when they were last retrieved from sheets

        Returns:
            str: Either 'NEVER' if not data exists, or string like '1d 5h 35m 17s'
        """
        if "timestamp" in self.data:
            return secs_to_hr(int(time.time()) - self.data["timestamp"])
        else:
            return "NEVER!"
    
    def Get(self, booking_id=None):
        
        # ToDo - Only added during dev. Force loading of JSON each time to allow me to edit data
        self._load()
            
        # If booking_id is provided, return only that booking
        if booking_id:
            # Fetch the specific booking data by booking_id
            booking_data = self.data["bookings"].get(booking_id)
            if booking_data:
                return booking_data  # Return as a list to keep consistency

        # If no booking_id provided, return the full list of bookings
        bookings = []
        for booking_id, b in self.data["bookings"].items():
            simplified = {
                "ID": booking_id,
                "Group": b.get("Group"),
                "Leader": b.get("Leader"),
                "Arriving": b.get("Arriving"),
                "Departing": b.get("Departing"),
                "Number": b.get("Number"),
                "Status": b.get("Status")
            }
            bookings.append(simplified)

        # Optional: sort by status options order
        bookings.sort(key=lambda b: status_options.index(b["Status"]))

        return bookings

    def _can_transition(self, from_status, to_status):
        return to_status in status_transitions.get(from_status, [])


    def Update(self, booking_id, updates: dict) -> bool:
        """
        Update a booking in self.data. Only allowed fields are updated.
        Changes are logged only if a value is actually modified.

        Args:
            booking_id (str): The ID of the booking to update.
            updates (dict): The fields to update.

        Returns:
            bool: True if update was successful, False if booking not found.
        """
        allowed_fields = {"Group", "Leader", "Number", "Status", "Arriving", "Departing", "Notes"}

        self._load()  # Ensure fresh data

        if booking_id not in self.data["bookings"]:
            flash(f"Cannot update booking as ID not found {booking_id}", "danger")
            return False  # Booking not found

        booking = self.data["bookings"][booking_id]
        changes = []
        
        new_status = updates.get("Status")
        current_status = booking.get("Status")
        
        if new_status and new_status != current_status:
            if not self._can_transition(current_status, new_status):
                self.logger.warning(f"Invalid status transition: {booking_id}: {current_status} > {new_status}")
                flash(f"Invalid state transition: {booking_id}: {current_status} > {new_status}", "danger")
                return False
            
            # Update and log the transition
            booking["Status"] = new_status
            changes.append(("Status", current_status, new_status))
        
            # Record history
            booking.setdefault("StatusHistory", []).append({
                "from": current_status,
                "to": new_status,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            if new_status == "Confirmed":
                send_booking_confirmation(booking)

                if "google_calendar_event_id" not in booking:
                    event_id = self.calendar.AddEvent(booking)
                    if event_id:
                        booking["google_calendar_event_id"] = event_id
            else:
                if new_status == "Cancelled":
                    if booking.get("google_calendar_event_id"):
                        self.calendar.DeleteEvent(booking["google_calendar_event_id"])
                        booking["google_calendar_event_id"] = None


        # Apply other updates
        for key, value in updates.items():
            if key in allowed_fields and key != "Status":
                if booking.get(key) != value:
                    changes.append((key, booking.get(key), value))
                    booking[key] = value
                    
        if changes:
            self.logger.info(f"Updated booking {booking_id}: " + "; ".join(
                f"{key}: {old} > {new}" for key, old, new in changes
            ))
                
        self._save()
        return True
    
    def _save(self):
        # Searilise the BookingType ENUM to string before saving
        for booking in self.data.get("bookings", {}).values():
            if isinstance(booking.get("booking_type"), BookingType):
                booking["booking_type"] = booking["booking_type"].name  # Or .label if you prefer

        with open(self.json_path, 'w') as f:
            self.logger.info(f"Saving bookings data to file")
            json.dump(self.data, f, indent=2)
            
            
    def _load(self):
        if self.json_path.exists():
            with open(self.json_path, 'r') as f:
                self.logger.info(f"Loading bookings data from file cache")
                self.data = json.load(f)
                
                # Deseralise BookingType string back to ENUM
                for booking in self.data.get("bookings", {}).values():
                    bt_raw = booking.get("booking_type")
                    try:
                        booking["booking_type"] = BookingType[bt_raw]
                    except (KeyError, TypeError):
                        self.logger.warning(f"Unknown or missing booking_type: {bt_raw}")
    
        else:
            self.data = {
                "timestamp": int(time.time()),
                "bookings": {}
            }
    
    def _md5_of_dict(self, data):
        # Ensure consistent ordering to get a consistent hash
        # Convert dict into a string of bytes for use with hashlib
        encoded = json.dumps(data, sort_keys=True).encode()
        return hashlib.md5(encoded).hexdigest()


    def _find_booking_by_md5(self, target_md5):
        for booking_id, booking in self.data["bookings"].items():
            if booking.get("original_sheet_md5") == target_md5:
                return booking_id, booking
        return None, None



    #
    ## Function to load a sheet of data in dict format into our booking structure
    def AddNewData(self, sheet_bookings, booking_type):
        
        bookings_added = 0
        
        if "timestamp" in sheet_bookings and sheet_bookings["timestamp"]:
            
            self.data["timestamp"] = sheet_bookings["timestamp"]
            
            #
            ## Need to normalise the new data from Sheet to our structure
            for sb in sheet_bookings["sheet_data"]:
                
                #
                ## Create MD5 of sheet line item so we can track if its new or seen before
                new_booking_md5 = self._md5_of_dict(sb)
                booking_id, booking = self._find_booking_by_md5(new_booking_md5)
                
                if not booking:
                    
                    start_dt = datetime.strptime(sb["Arrival Date / Time"], "%d/%m/%Y %H:%M:%S")
                    
                    # Parse the departure time and replace the time part of arrival
                    dep_time = datetime.strptime(sb["Departure Time"], "%H:%M:%S").time()
                    end_dt = start_dt.replace(hour=dep_time.hour, minute=dep_time.minute, second=0)
                    
                    existing_ids = list(self.data["bookings"].keys())
                    new_booking_id = generate_next_booking_id(existing_ids, booking_type, start_dt.year)
                    
                    new_booking = {
                        new_booking_id: {
                            "original_sheet_md5": new_booking_md5,
                            "original_sheet_data": sb,
                            "booking_type": booking_type.name,
                            "Group": sb["Chelmsford Scout Group"],
                            "Leader": sb["Name of Lead Person"],
                            "Arriving": int(start_dt.timestamp()),
                            "Departing": int(end_dt.timestamp()),
                            "Number": sb["Number of people"],
                            "Status": "New",
                            "Notes": ""
                        }
                    }
                    

                    self.data["bookings"].update(new_booking)
                    bookings_added += 1

            self._save()
            
        return bookings_added
