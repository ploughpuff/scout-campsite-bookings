"""
config.py - Contains configuration settings for Scout Campsite Bookings

"""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

APP_ENV = os.getenv("APP_ENV", "development")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
LOG_DIR = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "data")
EMAIL_TEMP_DIR = os.path.join(BASE_DIR, "email_templates")

LOG_FILENAME = "app.log"
LOG_FILE_PATH = os.path.join(LOG_DIR, LOG_FILENAME)

# Make sure these directories exist when config is loaded
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_CREDS", "credentials.json")

SHEETS_TO_PULL = [
    {
        "use": False,
        "id": os.getenv("GOOGLE_SPREADSHEET_ID_TEST_DATA"),
        "range": os.getenv("GOOGLE_SPREADSHEET_RANGE_TEST_DATA"),
        "type": "DISTRICT_DAY_VISIT",
    },
    {
        "use": True,
        "id": os.getenv("GOOGLE_SPREADSHEET_ID_REP_TEST_DATA"),
        "range": os.getenv("GOOGLE_SPREADSHEET_RANGE_REP_TEST_DATA"),
        "type": "DISTRICT_DAY_VISIT",
    },
]

UK_TZ = ZoneInfo("Europe/London")
DATE_FORMAT = "%Y-%m-%d %H:%M"
DATE_FORMAT_WITH_SECONDS = "%Y-%m-%d %H:%M:%S"

#
## Anonymise bookings by removing personnel information once a completed is X days old
ARCHIVE_BOOKINGS_AFTER_DEPARTING_DAYS = 90


MAX_BACKUPS_TO_KEEP = 50  # Keep 50 recent JSON backups for safety
