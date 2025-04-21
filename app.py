"""
app.py - Main Flask application entry point for Scout Campsite Booking.

Handles routing, app initialization, and integrates with the Bookings class.
"""

from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.exceptions import HTTPException
from markupsafe import Markup

from models.bookings import Bookings
from models.calendar import GoogleCalendar
from models.logger import setup_logger
from models.sheets import Sheets
from models.booking_types import BookingType

from utils import now_uk
from config import TEMPLATE_DIR, SERVICE_ACCOUNT_FILE, CALENDAR_ID, UK_TZ


app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.secret_key = "dev-key"

logger = setup_logger()
logger.info("Starting")

sheets = Sheets()
sheet_bookings = sheets.get_sheet_data()

gc = GoogleCalendar(SERVICE_ACCOUNT_FILE, CALENDAR_ID)

bookings = Bookings(calendar=gc)
bookings.add_new_data(sheet_bookings, BookingType.DISTRICT_DAY_VISIT)


@app.route("/")
@app.route("/bookings")
def all_bookings():
    """Render the main bookings table page."""
    return render_template(
        "all_bookings.html", bookings=bookings.get_booking(), age=bookings.age()
    )


@app.route("/booking/<booking_id>")
def booking_detail(booking_id):
    """Render the booking detail page for a specific booking ID."""
    booking = bookings.get_booking(booking_id)

    if not booking:
        flash("Booking not found", "danger")
        return redirect(url_for("all_bookings"))

    transitions = bookings.get_states()["transitions"]
    current_status = booking["Status"]

    return render_template(
        "booking.html",
        booking_id=booking_id,
        booking=booking,
        valid_transitions=transitions.get(current_status, []),
        time_now=now_uk(),
    )


@app.route("/booking/<new_status>/<booking_id>", methods=["POST"])
def change_status(new_status, booking_id):
    """Handle status change triggered by button press."""
    description = request.form.get("description")
    bookings.change_status(booking_id, new_status, description)
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/booking/modify_fields/<booking_id>", methods=["POST"])
def modify_fields(booking_id):
    """Handle modifying fields from details page."""
    updated_fields = {
        "Number": request.form.get("Number"),
        "Arriving": datetime.fromisoformat(request.form.get("Arriving")).replace(
            tzinfo=UK_TZ
        ),
        "Departing": datetime.fromisoformat(request.form.get("Departing")).replace(
            tzinfo=UK_TZ
        ),
    }

    bookings.modify_fields(booking_id, updated_fields)
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/pull")
def pull_now():
    """Pull new data from sheets and add to bookings."""
    added = bookings.add_new_data(
        sheets.get_sheet_data(pull_new=True), BookingType.DISTRICT_DAY_VISIT
    )
    flash(f"New Bookings Added from Google Sheets: {added}", "success")
    return redirect(url_for("all_bookings"))


@app.errorhandler(404)
def page_not_found(e):
    """
    Handle 404 Not Found errors by logging the event and rendering
    a custom 404 error page.

    Args:
        e (HTTPException): The exception raised for the 404 error.

    Returns:
        Response: A rendered 404.html template with HTTP 404 status code.
    """
    app.logger.warning("404 error: %s", e)
    return render_template("404.html"), 404


@app.errorhandler(Exception)
def handle_exception(e):
    """Catch all exceptions and log them. Render 500 for non-HTTP errors."""

    # Let Flask handle its own HTTP errors (like 404, 403, etc.)
    if isinstance(e, HTTPException):
        return e

    logger.exception("Unhandled exception: [%s]: %s", type(e).__name__, str(e))
    return render_template("500.html"), 500


@app.template_filter("datetime_local_value")
def datetime_local_value(value):
    """
    Format a datetime or ISO 8601 string into a format suitable for
    <input type="datetime-local"> HTML fields.

    Returns 'YYYY-MM-DDTHH:MM' (no seconds, no timezone).

    Args:
        value (datetime | str): The value to format.

    Returns:
        str: A valid datetime-local string or an empty string on failure.
    """
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M")

    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%Y-%m-%dT%H:%M")
    except (ValueError, TypeError):
        return ""


@app.template_filter("pretty_date")
def pretty_date(value):
    """Create a pretty date string from an poch int."""
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value)
    elif isinstance(value, datetime):
        dt = value
    else:
        return str(value)

    day = dt.day
    suffix = (
        "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    )
    month = dt.strftime("%B")
    time_part = dt.strftime("%H:%M")
    year = dt.year
    current_year = now_uk().year

    date_str = f"{day}{suffix} {month}"
    if year != current_year:
        date_str += f" {year}"

    return Markup(f"{date_str}<br>{time_part}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
