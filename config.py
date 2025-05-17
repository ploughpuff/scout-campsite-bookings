"""
config.py - Contains configuration settings for Scout Campsite Bookings

"""

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _get_and_print(k: str, d: str = "", show: bool = True) -> str:
    """Get, show, and return envar"""
    v = os.getenv(k, d)
    if show:
        print(f"{k}: {v}")
    return v


APP_ENV = _get_and_print("APP_ENV", "development")

if APP_ENV == "production":
    load_dotenv(".env.production", override=True)
    required_vars = [
        "APP_ENV",
        "SECRET_KEY",
        "SITENAME",
        "LOG_LEVEL",
        "EMAIL_ENABLED",
        "EMAIL_LOGIN_USERNAME",
        "EMAIL_LOGIN_PASSWD",
        "EMAIL_DISPLAY_USERNAME",
        "EMAIL_FROM_ADDRESS",
        "GOOGLE_CALENDAR_ID",
    ]

    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

else:
    load_dotenv(".env")

APP_VERSION = _get_and_print("APP_VERSION", "dev")

APP_SECRET_KEY = _get_and_print("SECRET_KEY", show=False)
SITENAME = _get_and_print("SITENAME", "Paddington")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(BASE_DIR) / "data"
TEMPLATE_DIR = Path(BASE_DIR) / "templates"
STATIC_DIR = Path(BASE_DIR) / "static"
CONFIG_DIR = Path(BASE_DIR) / "config"
EMAIL_TEMP_DIR = Path(BASE_DIR) / "email_templates"

DATA_DIR.mkdir(parents=True, exist_ok=True)
EMAIL_TEMP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

EMAIL_BODY_FILE_PATH = Path(EMAIL_TEMP_DIR) / "confirmed_body.html"
EMAIL_BODY_BACKUP_DIR = Path(EMAIL_TEMP_DIR) / "backups"
EMAIL_BODY_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE_PATH = Path(DATA_DIR) / "app.log"
DATA_FILE_PATH = Path(DATA_DIR) / "bookings.json"
ARCHIVE_FILE_PATH = Path(DATA_DIR) / "archive.json"

LOG_LEVEL_STR = _get_and_print("LOG_LEVEL", "INFO").upper()

EMAIL_ENABLED = _get_and_print("EMAIL_ENABLED", "False")
# SMTP server login credentials
EMAIL_LOGIN_USERNAME = _get_and_print("EMAIL_LOGIN_USERNAME", show=False)
EMAIL_LOGIN_PASSWD = _get_and_print("EMAIL_LOGIN_PASSWD", show=False)
# How to form the FROM field "DISPLAY_USERNAME <FROM_ADDRESS>"
EMAIL_DISPLAY_USERNAME = _get_and_print("EMAIL_DISPLAY_USERNAME")
EMAIL_FROM_ADDRESS = _get_and_print("EMAIL_FROM_ADDRESS")

CALENDAR_ID = _get_and_print("GOOGLE_CALENDAR_ID")

#
## Config data
SERVICE_ACCOUNT_PATH = Path(CONFIG_DIR) / "credentials.json"
FIELD_MAPPING_PATH = Path(CONFIG_DIR) / "field_mappings.json"

UK_TZ = ZoneInfo("Europe/London")

#
## Date formats used in notes
DATE_FORMAT = "%Y-%m-%d %H:%M"
DATE_FORMAT_WITH_SECONDS = "%Y-%m-%d %H:%M:%S"

#
## Anonymise bookings by removing personnel information once a completed is X days old
ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS = 90

MAX_BACKUPS_TO_KEEP = 50  # Keep 50 recent JSON backups for safety

EDIT_EMAIL_BODY_ALLOWED_TAGS = [
    "p",
    "ul",
    "li",
    "b",
    "i",
    "strong",
    "em",
    "a",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
]
EDIT_EMAIL_BODY_ALLOWED_ATTRIBS = {"a": ["href", "title"]}


if not FIELD_MAPPING_PATH.exists():
    # Needs moving.  Fake some data so pytest runs
    FIELD_MAPPINGS_DICT = {
        "group_types": {
            "my_group": {"description": "A group in my town", "prefix": "ABC"},
        },
        "key_mapping": {
            "leader": {
                "name": "name_of_lead_person",
                "email": "email_address",
                "phone": "mobile_number_for_lead_person",
            },
            "booking": {"group_name": "your_scout_group", "group_size": "number_of_people"},
        },
        "sheets": [
            {
                "use": False,
                "name": "Fake Test Data",
                "id": "asdfasdfasdfasdfasdf",
                "range": "2025!A:E",
            }
        ],
    }
else:
    FIELD_MAPPINGS_DICT = json.loads(FIELD_MAPPING_PATH.read_text())
