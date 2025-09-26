"""
app.py - Main Flask application entry point for Scout Campsite Booking.

Handles routing, app initialization, and integrates with the Bookings class.
"""

import io
import os
import zipfile
from collections import defaultdict
from datetime import datetime

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from markupsafe import Markup
from werkzeug.exceptions import HTTPException

from config import (
    APP_SECRET_KEY,
    APP_VERSION,
    ARCHIVE_FILE_PATH,
    DATA_FILE_PATH,
    EMAIL_ENABLED,
    FIELD_MAPPINGS_DICT,
    LOG_FILE_PATH,
    SITENAME,
    STATIC_DIR,
    TEMPLATE_DIR,
)
from models.bookings import Bookings
from models.logger import setup_logger
from models.sheets import get_sheet_data
from models.utils import get_pretty_date_str, is_email_enabled, now_uk

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.secret_key = APP_SECRET_KEY
app.config["EMAIL_ENABLED"] = EMAIL_ENABLED == "True"

logger = setup_logger()
logger.info("Starting")

bookings = Bookings()


@app.route("/")
@app.route("/bookings")
def all_bookings():
    """Render the main bookings table page."""
    bookings.auto_update_statuses()
    return render_template(
        "all_bookings.html", list=bookings.get_bookings_list(), age=bookings.age()
    )


@app.route("/booking/<booking_id>")
def booking_detail(booking_id):
    """Render the booking detail page for a specific booking ID."""
    bookings_list = bookings.get_bookings_list(booking_id=booking_id)
    # Returns a list[] of one dict{}
    rec = bookings_list[0] if bookings_list else None

    if not rec:
        flash(f"Booking {booking_id} not found ", "danger")
        return redirect(url_for("all_bookings"))

    transitions = bookings.get_states()["transitions"]

    # Check if calendar is free for the booking period
    rec_list_clash = bookings.get_bookings_list(
        date_range=(rec.booking.arriving, rec.booking.departing)
    )

    # Remove ourself from list of clashes
    rec_list_clash = [rec for rec in rec_list_clash if rec.booking.id != booking_id]

    return render_template(
        "booking.html",
        rec=rec,
        valid_transitions=transitions.get(rec.tracking.status, []),
        time_now=now_uk(),
        rec_list_clash=rec_list_clash,
        bookable_facilities=FIELD_MAPPINGS_DICT.get("bookable_facilities"),
    )


@app.route("/booking/<new_status>/<booking_id>", methods=["POST"])
def change_status(new_status, booking_id):
    """Handle status change triggered by button press."""
    description = request.form.get("description")
    bookings.change_status(booking_id, new_status, description)
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/booking/resend_email/<booking_id>", methods=["POST"])
def resend_email(booking_id):
    """Resend whatever is the latest email"""
    bookings.resend_email(booking_id)
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/booking/modify_fields/<booking_id>", methods=["POST"])
def modify_fields(booking_id):
    """Handle modifying fields from details page."""

    nested = defaultdict(dict)

    for full_key in request.form:
        if "." in full_key:
            section, key = full_key.split(".", 1)

            # Handle facilities (multiple checkboxes → list)
            if section == "booking" and key == "facilities":
                nested[section][key] = request.form.getlist(full_key)
                continue

            value = request.form[full_key]

            # Handle pounds → pence conversion for cost_estimate
            if section == "tracking" and key == "cost_estimate":
                try:
                    value = str(round(float(value) * 100))
                except (TypeError, ValueError):
                    value = "0"

            nested[section][key] = value

        else:
            logger.warning("Unexpected POST key without dot: [%s]", full_key)

    # Apply updates to the booking
    bookings.modify_fields(booking_id, dict(nested))

    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/pull")
def pull_now():
    """Pull new data from sheets and add to bookings."""
    added = bookings.add_new_data(get_sheet_data())
    flash(f"New Bookings Added from Google Sheets: {added}", "success")
    return redirect(url_for("all_bookings"))


@app.route("/logs")
def view_logs():
    """View app log"""
    if not os.path.exists(LOG_FILE_PATH):
        return "Log file not found", 404
    return render_template("logs.html")


@app.route("/logs/data")
def get_logs():
    """Filter the app log and display"""
    level_filter = request.args.get("level", "").upper()
    if not os.path.exists(LOG_FILE_PATH):
        return "", 204

    with open(LOG_FILE_PATH, "r", encoding="latin-1") as f:
        lines = f.readlines()

    if level_filter:
        lines = [line for line in lines if level_filter in line]

    return "".join(lines), 200, {"Content-Type": "text/plain"}


@app.route("/logs/download")
def download_logs():
    """Download the complete app log"""
    if os.path.exists(LOG_FILE_PATH):
        return send_file(LOG_FILE_PATH, as_attachment=True)
    return "Log file not found", 404


@app.route("/offline/analysis")
def offline_analysis():
    """Route to facilitate offline analysis"""
    ct_points = [DATA_FILE_PATH, ARCHIVE_FILE_PATH, LOG_FILE_PATH]

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w") as zf:
        for p in ct_points:
            zf.write(p, arcname=p.name)

    memory_file.seek(0)

    return send_file(
        memory_file,
        download_name="booking_data.zip",
        as_attachment=True,
        mimetype="application/zip",
    )


@app.route("/admin/reload_json")
def reload_json():
    "Route to reload the bookings JSON file bypassing the checksum validation"
    bookings.load(use_checksum=False)
    return render_template(
        "all_bookings.html", list=bookings.get_bookings_list(), age=bookings.age()
    )


@app.route("/admin/archive_old_bookings")
def archive_old_bookings():
    "Route to archive old bookings"
    bookings.archive_old_bookings()
    return render_template(
        "all_bookings.html", list=bookings.get_bookings_list(), age=bookings.age()
    )


@app.route("/admin/list_cal_events")
def list_cal_events():
    "Route to list all calendar events"
    dry_run = request.args.get("dry_run", "true").lower() == "true"
    # dry_run = True  # Hardcode to True whilst the real cal is updated

    data = bookings.fix_cal_events(dry_run)

    return render_template(
        "list_cal_events.html",
        good=data["good"],
        missing=data["missing"],
        delete=data["delete"],
        extra=data["extra"],
    )


@app.route("/bookings/archived")
def show_archived_bookings():
    """Show archived bookings in list table"""
    return render_template("archived.html", list=bookings.get_archive_list())


@app.route("/admin")
def admin():
    """Show the Admin dashboard"""
    return render_template("admin.html", current="admin", version=APP_VERSION)


@app.route("/toggle_email", methods=["POST"])
def toggle_email():
    """Route to toggle the sending of emails"""
    enabled = request.form.get("email_enabled") == "on"
    session["email_enabled"] = enabled
    flash(f"Email sending is now {'ENABLED' if enabled else 'DISABLED'}.", "info")
    return redirect(request.referrer or url_for("bookings"))


@app.context_processor
def inject_globals():
    """Make sitename available globally in all templates"""
    return {"sitename": SITENAME, "is_email_enabled": is_email_enabled}


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
    """Create an ISO date string from any input, less the seconds."""
    # If it's already a datetime object, format it to ISO format
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M")

    # If it's a string, first check if it's in ISO format
    if isinstance(value, str):
        # Check if the string matches the ISO format
        if len(value) == 19 and value[10] == "T":
            try:
                # Try parsing the ISO format string
                dt = datetime.fromisoformat(value)
                return dt.strftime("%Y-%m-%dT%H:%M")
            except ValueError:
                logger.warning("ISO parsing failed: [%s]", str(value))
                return value
        # Check if the string matches the custom 'dd/mm/yyyy HH:MM:SS' format from Google timestamp
        try:
            dt = datetime.strptime(value, "%d/%m/%Y %H:%M:%S")
            return dt.strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            logger.warning("Unknown date format so unable to create ISO string: [%s]", str(value))
            return value

    return value  # If the value is neither a string nor datetime object, return it as is


@app.template_filter("pretty_date")
def pretty_date(value, inc_time=False):
    """Create a pretty formatted date string from any input."""
    if not value:
        logger.warning("Asked to pretty a None value!")
        return ""

    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value)  # Epoch time

    elif isinstance(value, datetime):
        dt = value  # Datetime object

    else:
        try:
            dt = datetime.strptime(value, "%d/%m/%Y %H:%M:%S")  # Goodle sheet timestamp
        except ValueError:
            logger.warning("Unknown format so unable to create pretty string: [%s]", str(value))
            return value

    return Markup(get_pretty_date_str(dt, inc_time))


@app.template_filter("pence_to_pounds")
def pence_to_pounds(pence):
    """Jinja template to convert pence to pounds"""
    return f"{pence / 100:.2f}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
