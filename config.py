"""
config.py - Contains configuration settings for Scout Campsite Bookings

"""
import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv


BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR      = os.path.join(BASE_DIR, "cache")
LOG_DIR        = os.path.join(BASE_DIR, "logs")
DATA_DIR       = os.path.join(BASE_DIR, "data")
EMAIL_TEMP_DIR = os.path.join(BASE_DIR, "email_templates")

# Make sure these directories exist when config is loaded
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

DEBUG = True

load_dotenv()
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_CREDS", "credentials.json")

UK_TZ = ZoneInfo("Europe/London")
DATE_FORMAT = "%Y-%m-%d %H:%M"
DATE_FORMAT_WITH_SECONDS = "%Y-%m-%d %H:%M:%S"
