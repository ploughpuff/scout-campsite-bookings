"""
app.py - Main Flask application entry point for Scout Campsite Booking.

Handles routing, app initialization, and integrates with the Bookings class.
"""

import io
import os
import shutil
import zipfile
from datetime import datetime

import bleach
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from markupsafe import Markup
from werkzeug.exceptions import HTTPException

from config import (
    APP_SECRET_KEY,
    ARCHIVE_FILE_PATH,
    CALENDAR_ID,
    DATA_FILE_PATH,
    EMAIL_BODY_BACKUP_DIR,
    EMAIL_BODY_FILE_PATH,
    LOG_FILE_PATH,
    SERVICE_ACCOUNT_FILE,
    TEMPLATE_DIR,
    UK_TZ,
)
from models.bookings import Bookings
from models.calendar import GoogleCalendar
from models.logger import setup_logger
from models.sheets import get_sheet_data
from models.utils import get_pretty_date_str, now_uk

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.secret_key = APP_SECRET_KEY

logger = setup_logger()
logger.info("Starting")

gc = GoogleCalendar(SERVICE_ACCOUNT_FILE, CALENDAR_ID)
bookings = Bookings(calendar=gc)


@app.route("/")
@app.route("/bookings")
def all_bookings():
    """Render the main bookings table page."""
    bookings.auto_update_statuses()
    return render_template("all_bookings.html", bookings=bookings.get_booking(), age=bookings.age())


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
        "Arriving": datetime.fromisoformat(request.form.get("Arriving")).replace(tzinfo=UK_TZ),
        "Departing": datetime.fromisoformat(request.form.get("Departing")).replace(tzinfo=UK_TZ),
    }

    bookings.modify_fields(booking_id, updated_fields)
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


@app.route("/edit_email_body", methods=["GET", "POST"])
def edit_email_body():
    """Route to edit the confirm email body"""

    # If the form is submitted, sanitize and save the content back to the file
    if request.method == "POST":
        new_content = request.form["email_content"]

        # Sanitize the HTML input, allowing only specific tags and attributes
        allowed_tags = [
            "p",
            "ul",
            "li",
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
        allowed_attributes = {"a": ["href", "title"]}
        sanitized_content = bleach.clean(
            new_content, tags=allowed_tags, attributes=allowed_attributes
        )

        # Save sanitized content to the file
        try:
            backup_email_template()
            with open(EMAIL_BODY_FILE_PATH, "w", encoding="utf-8") as file:
                file.write(sanitized_content)
            flash("Email template updated successfully!", "success")

        except (FileNotFoundError, PermissionError) as e:
            flash(f"Error updating the email template: {e}", "danger")
            logger.exception("Error updating the email template: [%s]", str(e))
            return redirect(url_for("edit_email_body"))

    # Read the current content of the template file to display
    try:
        with open(EMAIL_BODY_FILE_PATH, "r", encoding="utf-8") as file:
            current_content = file.read()
    except (FileNotFoundError, PermissionError) as e:
        flash(f"Error reading the email template: {e}", "danger")
        logger.exception("Error reading email confirm body template: [%s]", str(e))
        current_content = ""

    return render_template(
        "edit_email_body.html", content=current_content, backups=get_backup_list()
    )


def backup_email_template():
    """Backup the current email body to allow retrieval at later date"""
    if os.path.exists(EMAIL_BODY_FILE_PATH):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(EMAIL_BODY_BACKUP_DIR, f"confirmed_body_{timestamp}.html")
        shutil.copy2(EMAIL_BODY_FILE_PATH, backup_path)


def get_backup_list():
    """List all the files in the backup folder"""
    if not os.path.exists(EMAIL_BODY_BACKUP_DIR):
        return []
    return sorted(os.listdir(EMAIL_BODY_BACKUP_DIR), reverse=True)  # latest first


@app.route("/admin/load_backup")
def load_backup():
    """Called from JS, return file content for display in textarea box"""
    filename = request.args.get("filename")
    backup_path = os.path.join(EMAIL_BODY_BACKUP_DIR, filename)
    if os.path.isfile(backup_path):
        with open(backup_path, "r", encoding="utf-8") as f:
            return f.read()
    return "Backup not found", 404


@app.route("/admin/reload_json")
def reload_json():
    "Route to reload the bookings JSON file bypassing the checksum validation"
    bookings.load()
    return render_template("all_bookings.html", bookings=bookings.get_booking(), age=bookings.age())


@app.route("/admin/archive_old_bookings")
def archive_old_bookings():
    "Route to archive old bookings"
    bookings.archive_old_bookings()
    return render_template("all_bookings.html", bookings=bookings.get_booking(), age=bookings.age())


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

    return Markup(get_pretty_date_str(dt))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
