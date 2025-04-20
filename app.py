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
def all_bookings():
    return render_template('all_bookings.html', bookings=bookings.Get(), age=bookings.Age())

@app.route("/booking/<booking_id>")
def booking_detail(booking_id):
    booking = bookings.Get(booking_id)
    
    if not booking:
        flash("Booking not found", "danger")
        return redirect(url_for("all_bookings"))
    
    transitions = bookings.GetStates()["transitions"]
    current_status = booking["Status"]
    
    return render_template(
        "booking.html",
        booking_id=booking_id,
        booking=booking,
        valid_transitions=transitions.get(current_status, []),
        time_now=int(time.time())
    )
    
    
@app.route("/booking/<new_status>/<booking_id>", methods=["POST"])
def change_status(new_status, booking_id):
    
    description = request.form.get("description")
    bookings.ChangeStatus(booking_id, new_status, description)
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/booking/modify_fields/<booking_id>", methods=["POST"])
def modify_fields(booking_id):
        
    updated_fields = {
        "Number": request.form.get("Number"),
        "Arriving": int(datetime.fromisoformat(request.form.get("Arriving")).timestamp()),
        "Departing": int(datetime.fromisoformat(request.form.get("Departing")).timestamp())
    }
    
    bookings.ModifyFields(booking_id, updated_fields)
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/test-flash")
def test_flash():
    flash("This is a success message!", "success")
    flash("This is a warning message.", "warning")
    flash("Something went wrong!", "danger")
    return redirect(url_for("all_bookings"))  # Or your actual view name

@app.route("/pull")
def pull_now():
    added = bookings.AddNewData(sheets.Get(pull_new=True), BookingType.DISTRICT_DAY_VISIT)
    flash(f"New Bookings Added from Google Sheets: {added}", "success")
    return redirect(url_for("all_bookings"))

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

    return redirect(url_for('all_bookings'))


@app.errorhandler(Exception)
def handle_exception(e):
    logger.exception(f"Unhandled exception: {type(e).__name__}: {e}")
    return render_template("500.html"), 500


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
def html_datetime(epoch_time):
    """Convert an epoch timestamp to HTML datetime-local format (YYYY-MM-DDTHH:MM)."""
    if not epoch_time:
        return ""
    
    try:
        dt = datetime.fromtimestamp(int(epoch_time))
        return dt.strftime("%Y-%m-%dT%H:%M")
    except (ValueError, TypeError):
        return ""
    

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
