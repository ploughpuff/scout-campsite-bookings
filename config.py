"""
config.py - Contains configuration settings for Scout Campsite Bookings

"""

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

APP_ENV = os.getenv("APP_ENV", "development")
print(f"APP_ENV: {APP_ENV}")

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

else:
    load_dotenv(".env")

    required_vars = [
        "APP_ENV",
        "SECRET_KEY",
        "SITENAME",
        "LOG_LEVEL",
        "EMAIL_ENABLED",
        "EMAIL_DISPLAY_USERNAME",
        "EMAIL_FROM_ADDRESS",
        "GOOGLE_CALENDAR_ID",
    ]

missing = [var for var in required_vars if not os.getenv(var)]
if missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


APP_VERSION = os.environ.get("APP_VERSION", "dev")

APP_SECRET_KEY = os.getenv("SECRET_KEY")
SITENAME = os.getenv("SITENAME")

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

LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()

EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "False")
# SMTP server login credentials
EMAIL_LOGIN_USERNAME = os.getenv("EMAIL_LOGIN_USERNAME")
EMAIL_LOGIN_PASSWD = os.getenv("EMAIL_LOGIN_PASSWD")
# How to form the FROM field "DISPLAY_USERNAME <FROM_ADDRESS>"
EMAIL_DISPLAY_USERNAME = os.getenv("EMAIL_DISPLAY_USERNAME")
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS")

CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")

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
