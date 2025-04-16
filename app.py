from flask import Flask, render_template, request, redirect, url_for, flash
from models.Logger import setup_logger
from models.Sheets import Sheets
from models.Bookings import Bookings
from models.Calendar import GoogleCalendar
import time
from markupsafe import Markup
from datetime import datetime
from markupsafe import Markup
from models.booking_types import BookingType
import config



app = Flask(__name__)
app.secret_key = 'dev-key'

logger = setup_logger()
logger.info("Starting")

sheets = Sheets()
sheet_bookings = sheets.Get()

gc = GoogleCalendar(config.SERVICE_ACCOUNT_FILE, config.CALENDAR_ID)

bookings = Bookings(calendar=gc)
bookings.AddNewData(sheet_bookings, BookingType.DISTRICT_DAY_VISIT)


@app.route('/')
@app.route('/bookings')
def show_bookings():
    return render_template('sheet.html', bookings=bookings.Get(), age=bookings.Age(), states=bookings.GetStates())

@app.route("/pull")
def pull_now():
    added = bookings.AddNewData(sheets.Get(pull_new=True), BookingType.DISTRICT_DAY_VISIT)
    flash(f"Bookings added: {added}")
    return redirect(url_for("show_bookings"))

@app.route('/update/<booking_id>', methods=['POST'])
def update(booking_id):
    updates = request.form.to_dict()
    
    # Convert date strings to epoch if they exist
    for field in ["Arriving", "Departing"]:
        if updates.get(field):
            try:
                dt = datetime.fromisoformat(updates[field])
                updates[field] = int(dt.timestamp())
            except ValueError:
                flash(f"Invalid {field} datetime format.", "danger")
                updates[field] = None
    
    print(updates)
    
    success = bookings.Update(booking_id, updates)

    if not success:
        flash("Booking not found", "danger")

    return redirect(url_for('show_bookings'))

@app.template_filter("pretty_date")
def pretty_date(value):
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value)
    elif isinstance(value, datetime):
        dt = value
    else:
        return str(value)

    day = dt.day
    suffix = 'th' if 11 <= day <= 13 else {1:'st', 2:'nd', 3:'rd'}.get(day % 10, 'th')
    month = dt.strftime('%B')
    time_part = dt.strftime('%H:%M')
    year = dt.year
    current_year = datetime.now().year

    date_str = f"{day}{suffix} {month}"
    if year != current_year:
        date_str += f" {year}"

    return Markup(f"{date_str}<br>{time_part}")

@app.template_filter("html_datetime")
def html_datetime(value):
    """Convert an epoch timestamp to HTML datetime-local format (YYYY-MM-DDTHH:MM)."""
    try:
        dt = datetime.fromtimestamp(int(value))
        return dt.strftime("%Y-%m-%dT%H:%M")
    except (ValueError, TypeError):
        return ""


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
