"""
sheets.py - Handle pull operations to Google sheets.
"""

import json

from pathlib import Path
import random
import logging

from google.oauth2 import service_account
from googleapiclient.discovery import build

from utils import now_uk, datetime_to_iso_uk, normalize_key
from config import (
    CACHE_DIR,
    SERVICE_ACCOUNT_FILE,
    SPREADSHEET_ID,
    SPREADSHEET_IMPORT_RANGE,
)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class Sheets:
    """Provides a class Sheets to access and manage Google sheets."""

    def __init__(self):
        self.logger = logging.getLogger("app_logger")
        self.json_path = Path(CACHE_DIR, "sheet_cache.json")
        self.data = {}
        self._load()

    def get_sheet_data(self, pull_new=False):
        """Read sheet data from Google.

        Args:
            pull_new (bool, optional): Force a re-read, not return file cache. Defaults to False.

        Returns:
            dict: Sheet data in a dictionary.
        """

        #
        ## Force read of sheet data from service provider
        if pull_new is True:
            self.logger.info("User forced update of sheet data from provider")

            #
            ## Use Google API to pull sheet data
            ## We have internal and external types of data but just do internal for now
            ## new_data = self._fetch_google_sheets_data()
            new_data = self._ti_create_test_data(count=2)

            #
            # w Normalise the column headers to snake-safe keys to avoid problems
            # in Python and Jinja templates later on
            normalized_sheet_data = [
                {normalize_key(k): v for k, v in rec.items()} for rec in new_data
            ]

            #
            ## For testing append new data, not replace it
            if self.data.get("sheet_data"):
                sheet_data = self.data["sheet_data"] + normalized_sheet_data
            else:
                sheet_data = normalized_sheet_data

            self.data = {
                "timestamp": datetime_to_iso_uk(now_uk()),
                "sheet_data": sheet_data,
            }

            self._save()
        else:
            self.logger.info("Read sheet data from file cache")
            self._load()

        return self.data

    def _save(self):
        with open(self.json_path, "w", encoding="utf-8") as f:
            self.logger.info("Saving sheet data to file cache")
            json.dump(self.data, f, indent=2)

    def _load(self):
        if self.json_path.exists():
            with open(self.json_path, "r", encoding="utf-8") as f:
                self.logger.info("Loading sheet data from file cache")
                self.data = json.load(f)
        else:
            self.data = {}

    def clear_cache(self):
        """Delete the sheets file cache"""
        if self.json_path.exists():
            self.json_path.unlink()

    def _fetch_google_sheets_data(self):
        """Fetch the data from Google Sheets API."""
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )

        service = build("sheets", "v4", credentials=credentials)

        # pylint: disable=no-member
        sheet = service.spreadsheets()

        result = (
            sheet.values()
            .get(spreadsheetId=SPREADSHEET_ID, range=SPREADSHEET_IMPORT_RANGE)
            .execute()
        )

        values = result.get("values", [])

        #
        ## Convert from lists of lists, to list of dict
        dicts = [dict(zip(values[0], row)) for row in values[1:]]

        return dicts

    def _ti_create_test_data(self, count=1):

        district_groups = ["1st Town", "2nd Village", "3rd City", "4th Smallville"]
        facilities = ["Top", "Bottom", "Trees", "Campfire", "Badgers"]

        test_data = []

        for _ in range(count):

            data = {
                "Timestamp": "03/04/2025 13:42:28",
                "Email address": "me@here.com",
                "Name of Lead Person": "Me You",
                "Mobile Number for Lead Person": "0123456789",
                "Chelmsford Scout Group": random.choice(district_groups),
                "Number of people": str(random.randint(10, 30)),
                "Arrival Date / Time": "23/06/2025 18:00:00",
                "Departure Time": "21:00:00",
                "Campsite": random.choice(facilities),
            }

            test_data.append(data)

        return test_data
