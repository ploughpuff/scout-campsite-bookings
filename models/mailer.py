"""
mailer.py - Provide functions to send emails from the app.
"""

import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

import html2text
from flask import flash
from jinja2 import Environment, FileSystemLoader, TemplateError

import config
from models.schemas import LiveBooking
from models.utils import get_pretty_date_str, now_uk

logger = logging.getLogger("app_logger")

# Setup Jinja2 environment to load from templates folder
env = Environment(loader=FileSystemLoader([config.EMAIL_TEMP_DIR]))


def send_email_notification(rec: LiveBooking, subject_append_str: str = ""):
    """
    Send an email notification based on the booking status.

    Args:
        booking (dict): Booking details.

    Returns:
        bool: True if the email was sent successfully, False otherwise.
    """
    if rec.tracking.status not in {"Confirmed", "Cancelled", "Pending"}:
        return False

    context = _build_email_context(rec)
    msg = _create_email_message(context, rec, subject_append_str)

    if not msg:
        return False

    if rec.tracking.status == "Pending":
        rec.tracking.pending_email_sent = now_uk()
    elif rec.tracking.status == "Confirmed":
        rec.tracking.confirm_email_sent = now_uk()
    else:
        rec.tracking.cancel_email_sent = now_uk()

    return _send_email(msg, rec.leader.email)


def _build_email_context(rec: LiveBooking):
    """
    Confirmed
    Cancelled
    Pending
    """

    arriving_str = get_pretty_date_str(rec.booking.arriving, inc_time=True, full_month=True)
    departing_str = get_pretty_date_str(rec.booking.departing, inc_time=True, full_month=True)

    status_str = rec.tracking.status.upper()

    if rec.tracking.status == "Confirmed":
        status_colour = "green"
        body = ""

        file_path = Path(os.path.join(config.EMAIL_TEMP_DIR, "confirmed_body.html"))

        if file_path.exists():
            with file_path.open("r", encoding="utf-8") as file:
                body = file.read()

    elif rec.tracking.status == "Cancelled":
        status_colour = "red"
        body = f"Cancel Reason: {rec.tracking.cancel_reason}"
    elif rec.tracking.status == "Pending":
        status_colour = "#FF8C00"  # Dark Orange
        body = f"Pend Question: {rec.tracking.pend_question}"
    else:
        status_colour = "black"
        body = ""

    return {
        "leader": rec.leader.name,
        "booking_id": rec.booking.id,
        "arriving_str": arriving_str,
        "departing_str": departing_str,
        "status_str": status_str,
        "status_colour": status_colour,
        "body": body,
    }


def _create_email_message(context, rec: LiveBooking, subject_append_str: str = ""):
    """
    Generate the email message object with both plain text and HTML content.

    Args:
        status (str): The booking status (Confirmed, Cancelled, Pending).
        context (dict): Data used in template rendering.
        recipient (str): Email address of the recipient.
        booking (dict): The full booking dictionary.

    Returns:
        EmailMessage or None: A composed email message, or None if templates fail.
    """
    try:
        body = env.get_template("base_email.html").render(context)
    except TemplateError as e:
        logger.error("%s trouble rendering email templates: %s: %s", rec.booking.id, rec, e)
        return None

    arriving_str = get_pretty_date_str(rec.booking.arriving)
    msg = EmailMessage()
    subject = f"{config.SITENAME} Booking for {arriving_str}: {rec.booking.id} {rec.tracking.status.upper()}"
    if subject_append_str:
        subject += f" ({subject_append_str})"
    msg["Subject"] = subject
    msg["From"] = f"{config.EMAIL_DISPLAY_USERNAME} <{config.EMAIL_FROM_ADDRESS}>"

    msg["To"] = rec.leader.email

    h = html2text.HTML2Text()
    h.body = body
    body_text = h.handle(body)

    msg.set_content(body_text)
    msg.add_alternative(body, subtype="html")
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
    if config.EMAIL_ENABLED == "True":
        try:
            if config.APP_ENV == "production":
                with smtplib.SMTP("smtp.office365.com", 587) as server:
                    server.starttls()
                    server.login(config.EMAIL_LOGIN_USERNAME, config.EMAIL_LOGIN_PASSWD)
                    server.send_message(msg)
            else:
                with smtplib.SMTP("localhost", 25) as server:
                    server.send_message(msg)

        except (smtplib.SMTPException, ConnectionRefusedError) as e:
            logger.error("%s - Failed to send email to %s: %s", config.APP_ENV, recipient, e)
            flash(f"{config.APP_ENV} - Failed to send email to {recipient}: {e}", "danger")
            return False
    else:
        flash(f"Email sending disabled by env var EMAIL_ENABLED: {recipient}:", "info")
    return True
