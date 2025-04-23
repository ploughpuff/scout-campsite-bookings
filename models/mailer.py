"""
mailer.py - Provide functions to send emails from the app.
"""

import logging
import smtplib
from email.message import EmailMessage

from flask import flash
from jinja2 import Environment, FileSystemLoader

import config
from models.utils import get_pretty_datetime_str

logger = logging.getLogger("app_logger")

# Setup Jinja2 environment to load from templates folder
env = Environment(loader=FileSystemLoader(config.EMAIL_TEMP_DIR))


def send_email_notification(booking_id, booking):
    """Send email based on booking record status.

    Args:
        booking_id (str): Booking ID
        booking (dict): The booking record.

    Returns:
        Boolean: True on success, otherwise False.
    """
    status = booking.get("Status")

    if status not in {"Confirmed", "Cancelled", "Pending"}:
        return False

    recipient = booking.get("original_sheet_data", {}).get("email_address")
    if not recipient:
        logger.warning("No email address found for booking %s", booking_id)
        return False

    context = {
        "leader": booking.get("Leader", "Leader"),
        "arriving": booking.get("Arriving", "a future date"),
        "campsite": booking.get("Campsite", "the campsite"),
        "booking_id": booking_id,
    }

    template_map = {
        "Confirmed": ("booking_confirmed.txt", "booking_confirmed.html"),
        "Cancelled": ("booking_cancelled.txt", "booking_cancelled.html"),
        "Pending": ("booking_pending.txt", "booking_pending.html"),
    }

    text_template_name, html_template_name = template_map[status]

    try:
        text_template = env.get_template(text_template_name)
        html_template = env.get_template(html_template_name)
        body_text = text_template.render(context)
        body_html = html_template.render(context)
    except smtplib.SMTPException as e:
        logger.error("%s trouble rendering email templates: %s: %s", booking_id, booking, e)
        return False

    msg = EmailMessage()
    msg["Subject"] = f"Campsite Booking {status}"
    msg["From"] = config.EMAIL_USER
    msg["To"] = recipient
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")

    booking["email_confirmation_sent"] = get_pretty_datetime_str(include_seconds=True)

    if config.APP_ENV == "production":
        try:
            with smtplib.SMTP("smtp.office365.com", 587) as server:
                server.starttls()
                server.login(config.EMAIL_USER, config.EMAIL_PASS)
                server.send_message(msg)
        except smtplib.SMTPException as e:
            logger.error("Failed to send email to %s: %s", recipient, e)
            flash(f"Failed to send email to {recipient}: {e}", "danger")
            return False
    else:
        logger.info("=== EMAIL LOG ===")
        logger.info("To: %s", msg["To"])
        logger.info("Subject: %s", msg["Subject"])
        plain = next(p for p in msg.iter_parts() if p.get_content_type() == "text/plain")
        logger.info("Body:\n%s", plain.get_content())

    return True
