import json
from pathlib import Path
import logging
import hashlib
import json
import time
from datetime import datetime

from config import CACHE_DIR

status_options = ["New", "Pending", "Confirmed", "Invoice", "Completed", "Cancelled"]

class Bookings:
    def __init__(self):
        self.logger = logging.getLogger("app_logger")
        self.json_path = Path(CACHE_DIR, "bookings.json")
        self.data = {}
        self._load()
    
    def GetStatusOptions(self):
        return status_options

    def GetLastRetrieved(self):
        return self.data["timestamp"]
    
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
        allowed_fields = {"Group", "Leader", "Number", "Status", "Arriving", "Departing"}

        self._load()  # Ensure fresh data

        if booking_id not in self.data["bookings"]:
            return False  # Booking not found

        booking = self.data["bookings"][booking_id]
        changes = {}

        for key, new_value in updates.items():
            if key in allowed_fields:
                old_value = booking.get(key)
                if str(old_value) != str(new_value):  # Compare as strings for safety
                    booking[key] = new_value
                    changes[key] = (old_value, new_value)

        if changes:
            self._save()
            change_list = ", ".join(
                f"{key}: '{old}' > '{new}'" for key, (old, new) in changes.items()
            )
            self.logger.info(f"Booking {booking_id} updated: {change_list}")
        else:
            self.logger.info(f"Booking {booking_id} checked for update — no changes.")

        return True


    
    def _save(self):
        with open(self.json_path, 'w') as f:
            self.logger.info(f"Saving bookings data to file")
            json.dump(self.data, f, indent=2)
            
            
    def _load(self):
        if self.json_path.exists():
            with open(self.json_path, 'r') as f:
                self.logger.info(f"Loading bookings data from file cache")
                self.data = json.load(f)
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

    #
    ## Function to load a sheet of data in dict format into our booking structure
    def Load(self, sheet_bookings):
        
        bookings_added = 0
        self.data["timestamp"] = int(time.time())
        print(self.data["timestamp"])
        
        #
        ## Need to normalise the new data from Sheet to our structure
        for sb in sheet_bookings["sheet_data"]:
            booking_id = self._md5_of_dict(sb)
            
            if booking_id not in self.data["bookings"]:
                
                start_dt = datetime.strptime(sb["Arrival Date / Time"], "%d/%m/%Y %H:%M:%S")
                
                # Parse the departure time and replace the time part of arrival
                dep_time = datetime.strptime(sb["Departure Time"], "%H:%M:%S").time()
                end_dt = start_dt.replace(hour=dep_time.hour, minute=dep_time.minute, second=0)
                
                new_booking = {
                    booking_id: {
                        "original_sheet_data": sb,
                        "Group": sb["Chelmsford Scout Group"],
                        "Leader": sb["Name of Lead Person"],
                        "Arriving": int(start_dt.timestamp()),
                        "Departing": int(end_dt.timestamp()),
                        "Number": sb["Number of people"],
                        "Status": "New",
                    }
                }
                
                self.data["bookings"].update(new_booking)
                bookings_added += 1

        self._save()
        return bookings_added
