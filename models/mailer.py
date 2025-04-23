"""
mailer.py - Provide functions to send emails from the app.
"""

import logging
import smtplib
from email.message import EmailMessage

from flask import flash
from jinja2 import Environment, FileSystemLoader, TemplateError

import config
from models.utils import get_pretty_datetime_str

logger = logging.getLogger("app_logger")

# Setup Jinja2 environment to load from templates folder
env = Environment(loader=FileSystemLoader(config.EMAIL_TEMP_DIR))


def send_email_notification(booking_id, booking):
    """
    Send an email notification based on the booking status.

    Args:
        booking_id (str): Unique identifier of the booking.
        booking (dict): Booking details.

    Returns:
        bool: True if the email was sent successfully, False otherwise.
    """
    status = booking.get("Status")
    if status not in {"Confirmed", "Cancelled", "Pending"}:
        return False

    recipient = booking.get("original_sheet_data", {}).get("email_address")
    if not recipient:
        logger.warning("No email address found for booking %s", booking_id)
        return False

    context = _build_email_context(booking_id, booking)
    msg = _create_email_message(status, context, recipient, booking_id, booking)
    if not msg:
        return False

    booking["email_confirmation_sent"] = get_pretty_datetime_str(include_seconds=True)
    return _send_email(msg, recipient)


def _build_email_context(booking_id, booking):
    """
    Build the context dictionary used for rendering email templates.

    Args:
        booking_id (str): Unique identifier of the booking.
        booking (dict): Booking details.

    Returns:
        dict: Context with placeholder values for email content.
    """
    return {
        "leader": booking.get("Leader", "Leader"),
        "arriving": booking.get("Arriving", "a future date"),
        "campsite": booking.get("Campsite", "the campsite"),
        "booking_id": booking_id,
    }


def _create_email_message(status, context, recipient, booking_id, booking):
    """
    Generate the email message object with both plain text and HTML content.

    Args:
        status (str): The booking status (Confirmed, Cancelled, Pending).
        context (dict): Data used in template rendering.
        recipient (str): Email address of the recipient.
        booking_id (str): Booking identifier.
        booking (dict): The full booking dictionary.

    Returns:
        EmailMessage or None: A composed email message, or None if templates fail.
    """
    template_map = {
        "Confirmed": ("booking_confirmed.txt", "booking_confirmed.html"),
        "Cancelled": ("booking_cancelled.txt", "booking_cancelled.html"),
        "Pending": ("booking_pending.txt", "booking_pending.html"),
    }

    try:
        text_template_name, html_template_name = template_map[status]
        body_text = env.get_template(text_template_name).render(context)
        body_html = env.get_template(html_template_name).render(context)
    except TemplateError as e:
        logger.error("%s trouble rendering email templates: %s: %s", booking_id, booking, e)
        return None

    msg = EmailMessage()
    msg["Subject"] = f"Campsite Booking {status}"
    msg["From"] = config.EMAIL_USER
    msg["To"] = recipient
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")
    return msg


def _send_email(msg, recipient):
    """
    Send the prepared email message to the recipient.

    In production, this sends via SMTP. Otherwise, logs the content for testing.

    Args:
        msg (EmailMessage): The email message to be sent.
        recipient (str): The recipient's email address.

    Returns:
        bool: True if email was sent/logged successfully, False if sending failed.
    """
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
