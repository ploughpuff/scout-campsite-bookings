"""
sheets.py - Handle pull operations to Google sheets.
"""

import random
import logging

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from utils import now_uk, datetime_to_iso_uk, normalize_key
from config import SHEETS_TO_PULL, SERVICE_ACCOUNT_FILE

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class Sheets:
    """Provides a class Sheets to access and manage Google sheets."""

    def __init__(self):
        self.logger = logging.getLogger("app_logger")

    def get_sheet_data(self) -> dict:
        """
        Fetch and normalize data from all enabled Google Sheets.

        Sheets are defined in SHEETS_TO_PULL. Each row's keys are converted
        to snake_case for safer use in Python and Jinja templates.

        Returns:
            dict: {
                "timestamp": ISO string of when data was pulled,
                "sheet_data": List of row dicts with normalized keys
            }
        """
        all_data = []

        for sheet_cfg in SHEETS_TO_PULL:
            if not sheet_cfg.get("use"):
                self.logger.info(
                    "Skipping sheet %s (disabled via 'use' flag).", sheet_cfg.get("id")
                )
                continue

            sheet_id = sheet_cfg.get("id")
            sheet_range = sheet_cfg.get("range")
            type = sheet_cfg.get("type")

            if not sheet_id or not sheet_range or not type:
                self.logger.warning(
                    "Skipping sheet due to missing ID or range: %s", sheet_cfg
                )
                continue

            new_data = self._fetch_google_sheets_data(sheet_id, sheet_range)

            # Normalize column headers for each row
            normalized_sheet_data = [
                {normalize_key(k): v for k, v in rec.items()} for rec in new_data
            ]

            all_data.append(
                {"type": sheet_cfg.get("type"), "sheet_data": normalized_sheet_data}
            )

        return {
            "timestamp": datetime_to_iso_uk(now_uk()),
            "data": all_data,
        }

    def _fetch_google_sheets_data(self, spreadsheet_id, sheet_range):
        """
        Fetch data from Google Sheets API and return as list of dicts.

        Args:
            spreadsheet_id (str): The ID of the spreadsheet.
            sheet_range (str): The A1 notation range to fetch.

        Returns:
            list[dict]: List of rows as dictionaries using the first row as headers.
        """
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )

        service = build("sheets", "v4", credentials=credentials)

        # pylint: disable=no-member
        sheet = service.spreadsheets()

        try:
            result = (
                sheet.values()
                .get(spreadsheetId=spreadsheet_id, range=sheet_range)
                .execute()
            )
        except HttpError as e:
            self.logger.error("Google Sheets API error: %s", e)
            return []
        except (TypeError, ValueError, KeyError) as e:
            self.logger.warning("Unexpected data format: %s", e)
            return []

        values = result.get("values", [])

        if not values or len(values) < 2:
            self.logger.warning("No data rows found in spreadsheet.")
            return []

        # Convert rows to list of dicts
        headers = values[0]
        return [dict(zip(headers, row)) for row in values[1:] if any(row)]

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
