"""
sheets.py - Handle pull operations to Google sheets.
"""

import logging

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import SERVICE_ACCOUNT_PATH, SHEETS_TO_PULL
from models.booking_types import parse_booking_type
from models.utils import datetime_to_iso_uk, normalize_key, now_uk

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def get_sheet_data() -> dict:
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
    logger = logging.getLogger("app_logger")
    all_data = []

    for sheet_cfg in SHEETS_TO_PULL:
        if not sheet_cfg.get("use"):
            logger.info("Skipping sheet %s (disabled via 'use' flag).", sheet_cfg.get("id"))
            continue

        if not sheet_cfg.get("id") or not sheet_cfg.get("range") or not sheet_cfg.get("type"):
            logger.warning("Skipping sheet due to missing ID or range: %s", sheet_cfg)
            continue

        sheet_id = sheet_cfg.get("id")
        sheet_range = sheet_cfg.get("range")
        booking_type = parse_booking_type(sheet_cfg.get("type"))

        new_data = _fetch_google_sheets_data(sheet_id, sheet_range)

        # Normalize column headers for each row
        normalized_sheet_data = [{normalize_key(k): v for k, v in rec.items()} for rec in new_data]

        all_data.append({"type": booking_type, "sheet_data": normalized_sheet_data})

    return {
        "timestamp": datetime_to_iso_uk(now_uk()),
        "data": all_data,
    }


def _fetch_google_sheets_data(spreadsheet_id, sheet_range):
    """
    Fetch data from Google Sheets API and return as list of dicts.

    Args:
        spreadsheet_id (str): The ID of the spreadsheet.
        sheet_range (str): The A1 notation range to fetch.

    Returns:
        list[dict]: List of rows as dictionaries using the first row as headers.
    """
    logger = logging.getLogger("app_logger")

    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_PATH, scopes=SCOPES
    )

    service = build("sheets", "v4", credentials=credentials)

    # pylint: disable=no-member
    sheet = service.spreadsheets()

    try:
        result = sheet.values().get(spreadsheetId=spreadsheet_id, range=sheet_range).execute()
    except HttpError as e:
        logger.error("Google Sheets API error: %s", e)
        return []
    except (TypeError, ValueError, KeyError) as e:
        logger.error("Unexpected data format: %s", e)
        return []
    except TimeoutError as e:
        logger.error("Timeout waiting to receive data from Google sheets: %s", e)
        return []

    values = result.get("values", [])

    if not values or len(values) < 2:
        logger.warning("No data rows found in spreadsheet.")
        return []

    # Convert rows to list of dicts
    headers = values[0]
    return [dict(zip(headers, row)) for row in values[1:] if any(row)]
