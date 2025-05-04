"""
config.py - Contains configuration settings for Scout Campsite Bookings

"""

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

APP_ENV = os.getenv("APP_ENV", "development")

if APP_ENV == "production":
    load_dotenv(".env.production")
else:
    load_dotenv(".env")

APP_SECRET_KEY = os.getenv("SECRET_KEY")
SITENAME = os.getenv("SITENAME")

# BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = Path(BASE_DIR) / "templates"
LOG_DIR = Path(BASE_DIR) / "logs"
DATA_DIR = Path(BASE_DIR) / "data"
EMAIL_TEMP_DIR = Path(BASE_DIR) / "email_templates"

LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
EMAIL_TEMP_DIR.mkdir(parents=True, exist_ok=True)

EMAIL_BODY_FILE_PATH = Path(EMAIL_TEMP_DIR) / "confirmed_body.html"
EMAIL_BODY_BACKUP_DIR = Path(EMAIL_TEMP_DIR) / "backups"
EMAIL_BODY_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE_PATH = Path(LOG_DIR) / "app.log"
DATA_FILE_PATH = Path(DATA_DIR) / "bookings.json"
ARCHIVE_FILE_PATH = Path(DATA_DIR) / "archive.json"

LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
SERVICE_ACCOUNT_PATH = Path(BASE_DIR) / os.getenv("GOOGLE_CREDS", "credentials.json")

FIELD_MAPPING_PATH = Path(BASE_DIR) / "field_mappings.json"

if not FIELD_MAPPING_PATH.exists():
    raise RuntimeError(f"Missing required config file: {FIELD_MAPPING_PATH}")

with open(FIELD_MAPPING_PATH, "r", encoding="utf-8") as f:
    FIELD_MAPPINGS = json.load(f)

APP_ENV = os.getenv("APP_ENV", "development")

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
