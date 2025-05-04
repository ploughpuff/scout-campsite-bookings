"""
app.py - Main Flask application entry point for Scout Campsite Booking.

Handles routing, app initialization, and integrates with the Bookings class.
"""

import io
import os
import shutil
import zipfile
from collections import defaultdict
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
    DATA_FILE_PATH,
    EDIT_EMAIL_BODY_ALLOWED_ATTRIBS,
    EDIT_EMAIL_BODY_ALLOWED_TAGS,
    EMAIL_BODY_BACKUP_DIR,
    EMAIL_BODY_FILE_PATH,
    LOG_FILE_PATH,
    SITENAME,
    TEMPLATE_DIR,
)
from models.bookings import Bookings
from models.calendar import del_cal_events, get_cal_events, update_calendar_entry
from models.logger import setup_logger
from models.sheets import get_sheet_data
from models.utils import get_pretty_date_str, now_uk

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.secret_key = APP_SECRET_KEY

logger = setup_logger()
logger.info("Starting")

bookings = Bookings()


@app.context_processor
def inject_site_name():
    """Make sitename available globally in all templates"""
    return {"sitename": SITENAME}


@app.route("/")
@app.route("/bookings")
def all_bookings():
    """Render the main bookings table page."""
    bookings.auto_update_statuses()
    return render_template(
        "all_bookings.html", bookings=bookings.get_bookings_list(), age=bookings.age()
    )


@app.route("/booking/<booking_id>")
def booking_detail(booking_id):
    """Render the booking detail page for a specific booking ID."""
    bookings_list = bookings.get_bookings_list(booking_id=booking_id)
    # Returns a list[] of one dict{}
    booking = bookings_list[0] if bookings_list else None

    if not booking:
        flash(f"Booking {booking_id} not found ", "danger")
        return redirect(url_for("all_bookings"))

    transitions = bookings.get_states()["transitions"]

    # Only need to check if calendar is free when state is New or Pending
    bookings_list_clash = None
    if booking.site.status in ["New", "Pending"]:
        bookings_list_clash = bookings.get_bookings_list(
            date_range=(booking.site.arriving, booking.site.departing)
        )

        # Remove ourself from list of clashes
        bookings_list_clash = [b for b in bookings_list_clash if b.site.id != booking_id]

    return render_template(
        "booking.html",
        booking=booking,
        valid_transitions=transitions.get(booking.site.status, []),
        time_now=now_uk(),
        bookings_list_clash=bookings_list_clash,
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
    nested = defaultdict(dict)

    # names in post data are like site.group_size, leader.name
    # Create dict with left-side of dot as key, right-side of dot as value
    for full_key, value in request.form.items():
        if "." in full_key:
            section, key = full_key.split(".", 1)
            nested[section][key] = value
        else:
            logger.warning("No flat names in POST data please: [%s]", full_key)

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


@app.route("/edit_email_body", methods=["GET", "POST"])
def edit_email_body():
    """Route to edit the confirm email body"""

    # If the form is submitted, sanitize and save the content back to the file
    if request.method == "POST":
        new_content = request.form["email_content"]

        # Sanitize the HTML input, allowing only specific tags and attributes
        sanitized_content = bleach.clean(
            new_content,
            tags=EDIT_EMAIL_BODY_ALLOWED_TAGS,
            attributes=EDIT_EMAIL_BODY_ALLOWED_ATTRIBS,
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
        "edit_email_body.html",
        content=current_content,
        backups=get_backup_list(),
        allowed_tags=EDIT_EMAIL_BODY_ALLOWED_TAGS,
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
    bookings.load(use_checksum=False)
    return render_template(
        "all_bookings.html", bookings=bookings.get_bookings_list(), age=bookings.age()
    )


@app.route("/admin/archive_old_bookings")
def archive_old_bookings():
    "Route to archive old bookings"
    bookings.archive_old_bookings()
    return render_template(
        "all_bookings.html", bookings=bookings.get_bookings_list(), age=bookings.age()
    )


@app.route("/admin/list_cal_events")
def list_cal_events():
    "Route to list all calendar events"
    event_resource = get_cal_events()

    missing = []
    extra = []
    event_ids = [event["id"] for event in event_resource.get("items", [])]

    for booking in bookings.get_bookings_list(booking_state="Confirmed"):
        if booking.site.google_calendar_id not in event_ids:
            missing.append(booking)

    return render_template("list_cal_events.html", missing=missing, extra=extra)


@app.route("/admin/add_to_calendar")
def add_to_calendar(booking):
    """Add a booking id to google calendar"""
    update_calendar_entry(booking)
    return render_template("list_cal_events.html", events=get_cal_events())


@app.route("/admin/delete_cal_events")
def delete_cal_events():
    "Route to delete all calendar events"
    del_cal_events(get_cal_events())
    return render_template("list_cal_events.html", events=get_cal_events())


@app.route("/bookings/archived")
def show_archived_bookings():
    """Show archived bookings in list table"""
    archived_bookings = bookings.get_archive_list()
    return render_template("archived.html", bookings=archived_bookings)


@app.route("/admin")
def admin():
    """Show the Admin dashboard"""
    return render_template("admin.html", current="admin")


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
def pretty_date(value):
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

    return Markup(get_pretty_date_str(dt))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
