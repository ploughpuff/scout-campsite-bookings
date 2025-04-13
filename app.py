from flask import Flask
from models.Logger import setup_logger
from models.Sheets import Sheets


app = Flask(__name__)


logger = setup_logger()
logger.info("Starting")

sheets = Sheets()
sheet_bookings = sheets.Get()
print(sheet_bookings)
print(sheets.Age())
sheet_bookings = sheets.Get(pull_new=True)
print(sheet_bookings)
print(sheets.Age())
