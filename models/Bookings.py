import json
from pathlib import Path
import logging
import hashlib
import json
import time
from datetime import datetime

from config import CACHE_DIR




ALLOWED_STATUSES = {"pending", "confirmed", "cancelled", "invoice", "completed"}

REQUIRED_FIELDS = [
        "booking_id", "email", "group", "name", "address", "phone",
        "event_type", "num_of_people", "start_date", "start_time",
        "end_date", "end_time", "facilities", "status"
    ]

class Bookings:
    def __init__(self):
        self.logger = logging.getLogger("app_logger")
        self.json_path = Path(CACHE_DIR, "bookings.json")
        self.data = {}
        self._load()
    
    def Get(self):
        return self.data["bookings"]
    
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
                        "status": "pending",
                        "start": int(start_dt.timestamp()),
                        "end": int(end_dt.timestamp())
                    }
                }
                
                self.data["bookings"].update(new_booking)

        self._save()
