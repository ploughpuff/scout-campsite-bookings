import json

from pathlib import Path
from datetime import datetime, timedelta
import random
import logging
import time

from config import CACHE_DIR

    
class Sheets:
    def __init__(self):
        self.logger = logging.getLogger("app_logger")
        self.json_path = Path(CACHE_DIR, "sheet_cache.json")
        self.data = {}
        self._load()
    
    
    def Get(self,pull_new=False):
        
        #
        ## Force read of sheet data from service provider
        if pull_new is True:
            self.logger.info(f"User forced update of sheet data from provider")
            
            #
            ## ToDo - Use Google API to pull sheet data
            ## We have internal and external types of data but just do internal for now
            new_data = self._ti_create_test_data(count=2)
            
            # ToDo - For testing append new data, not replace it
            if self.data.get("sheet_data"):
                sheet_data = self.data["sheet_data"] + new_data
            else:
                sheet_data = new_data
                
            self.data = {
                "timestamp": int(time.time()),
                "sheet_data": sheet_data
            }
            
            self._save()
        else:
            self.logger.info(f"Read sheet data from file cache")
            self._load()
           
        return self.data

    
    def Age(self):
        if "timestamp" in self.data:
            age_secs = int(time.time()) - self.data["timestamp"]
            return(self._secs_to_human_readable(age_secs))
        else:
            return "NEVER!"


    def _secs_to_human_readable(self, seconds):
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        d, h = divmod(h, 24)

        parts = []
        if d > 0:
            parts.append(f"{d}d")
        if h > 0:
            parts.append(f"{h}h")
        if m > 0:
            parts.append(f"{m}m")
        if s > 0 or not parts:
            parts.append(f"{s}s")

        return ":".join(parts)
    
    
    def _save(self):
        with open(self.json_path, 'w') as f:
            self.logger.info(f"Saving sheet data to file cache")
            json.dump(self.data, f, indent=2)
    
    def _load(self):
        if self.json_path.exists():
            with open(self.json_path, 'r') as f:
                self.logger.info(f"Loading sheet data from file cache")
                self.data = json.load(f)
        else:
            self.data = {}
            
    def _ti_create_test_data(self, count=1):
        
        DISTRICT_GROUPS = ["1st Town", "2nd Village", "3rd City", "4th Smallville"]
        FACILITIES = ["Top", "Bottom", "Trees", "Campfire", "Badgers"]
        
        test_data = []
        now = datetime.now()

        for _ in range(count):

            now = datetime.now()
            arrival = now + timedelta(days=random.randint(5, 30))
            departure_hour = arrival.hour + 3  # 3-hour event
            departure_str = f"{departure_hour:02d}:{arrival.minute:02d}"

            data = {
                "Timestamp": "03/04/2025 13:42:28",
                "Email address": "me@here.com",
                "Name of Lead Person": "Me You",
                "Mobile Number for Lead Person": "0123456789",
                "Chelmsford Scout Group": random.choice(DISTRICT_GROUPS),
                "Number of people": str(random.randint(10, 30)),
                "Arrival Date / Time": "23/06/2025 18:00:00",
                "Departure Time": "21:00:00",
                "Campsite": random.choice(FACILITIES)
            }

            test_data.append(data)

        return test_data
