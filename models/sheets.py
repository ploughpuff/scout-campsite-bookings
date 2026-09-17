"""
sheets.py - Handle pull operations to Google sheets.
"""

import logging

from config import FIELD_MAPPINGS_DICT, SERVICE_ACCOUNT_PATH
from models.net import classify, google_service
from models.utils import normalize_key, now_uk

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def get_sheet_data() -> dict:
    """
    Fetch and normalize data from all enabled Google Sheets.

    Sheets are defined in a JSON file called field_mappings.
    Each row's keys are converted to snake_case for safer use
    in Python and Jinja templates.

    Returns:
        dict: {
            "updated": Datetime object for when data was pulled,
            "sheet_data": List of row dicts with normalized keys
        }
    """
    logger = logging.getLogger("app_logger")
    all_data = []

    for sheet_cfg in FIELD_MAPPINGS_DICT.get("sheets"):
        if not sheet_cfg.get("use"):
            logger.info("Skipping sheet %s (disabled via 'use' flag).", sheet_cfg.get("id"))
            continue

        if not sheet_cfg.get("id") or not sheet_cfg.get("range"):
            logger.warning("Skipping sheet due to missing ID or range: %s", sheet_cfg)
            continue

        sheet_id = sheet_cfg.get("id")
        sheet_range = sheet_cfg.get("range")
        group_type = sheet_cfg.get("group_type")
        contains = sheet_cfg.get("contains")

        new_data = _fetch_google_sheets_data(sheet_id, sheet_range)

        # Normalize column headers for each row
        normalized_sheet_data = [{normalize_key(k): v for k, v in rec.items()} for rec in new_data]

        # Override or create new keys as defined in the config table
        for b in normalized_sheet_data:
            if sheet_cfg.get("address"):
                b["address"] = sheet_cfg.get("address")

            if sheet_cfg.get("facilities"):
                b["facilities"] = sheet_cfg.get("facilities")

        all_data.append(
            {"sheet_data": normalized_sheet_data, "group_type": group_type, "contains": contains}
        )

    return {
        "updated": now_uk(),
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

    Raises:
        Retryable or Permanent: the fetch did not happen. Never returns [] for a
        failure - an empty list means the sheet really was empty, and conflating
        the two let a 503 be recorded as a successful pull of zero bookings,
        leaving every indicator green while booking forms stopped arriving.
    """
    logger = logging.getLogger("app_logger")

    try:
        service = google_service("sheets", "v4", SCOPES, SERVICE_ACCOUNT_PATH)

        # pylint: disable=no-member
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=spreadsheet_id, range=sheet_range).execute()
    except Exception as e:  # pylint: disable=broad-except
        #
        ## Credential load and build() are inside the try as well: the service
        ## account's token exchange happens lazily on the first call, so an
        ## outage surfaces from any of these three lines, not just the last.
        error = classify(e)
        logger.error("Could not fetch sheet %s: %s", spreadsheet_id, error)
        raise error from e

    values = result.get("values", [])

    if not values or len(values) < 2:
        logger.warning("No data rows found in spreadsheet.")
        return []

    # Convert rows to list of dicts
    headers = values[0]
    return [dict(zip(headers, row)) for row in values[1:] if any(row)]
