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
from models.schemas import SitePlusLeader
from models.utils import get_pretty_date_str, now_uk

logger = logging.getLogger("app_logger")

# Setup Jinja2 environment to load from templates folder
env = Environment(loader=FileSystemLoader([config.EMAIL_TEMP_DIR]))


def send_email_notification(booking: SitePlusLeader):
    """
    Send an email notification based on the booking status.

    Args:
        booking (dict): Booking details.

    Returns:
        bool: True if the email was sent successfully, False otherwise.
    """
    if booking.site.status not in {"Confirmed", "Cancelled", "Pending"}:
        return False

    context = _build_email_context(booking)
    msg = _create_email_message(context, booking)

    if not msg:
        return False

    if booking.site.status == "Pending":
        booking.site.pending_email_sent = now_uk()
    elif booking.site.status == "Confirmed":
        booking.site.confirm_email_sent = now_uk()
    else:
        booking.site.cancel_email_sent = now_uk()

    return _send_email(msg, booking.leader.email)


def _build_email_context(booking: SitePlusLeader):
    """
    Confirmed
    Cancelled
    Pending
    """

    arriving_str = get_pretty_date_str(booking.site.arriving)
    summary = f"Your campsite booking for {arriving_str} "
    body = "TBD"

    if booking.site.status == "Confirmed":
        summary += "is <strong>CONFIRMED</strong>."

        file_path = Path(os.path.join(config.EMAIL_TEMP_DIR, "confirmed_body.html"))

        if file_path.exists():
            with file_path.open("r", encoding="utf-8") as file:
                body = file.read()

    elif booking.site.status == "Cancelled":
        summary += "is <strong>CANCELLED</strong>."
        body = f"Reason: {booking.site.cancel_reason}"
    elif booking.site.status == "Pending":
        summary += (
            "has been set to <strong>PENDING</strong> with the following note to answer please:"
        )
        body = f"Pending Question: {booking.site.pend_question}"

    return {
        "leader": booking.leader.name,
        "booking_id": booking.site.id,
        "summary": summary,
        "body": body,
    }


def _create_email_message(context, booking: SitePlusLeader):
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
        logger.error("%s trouble rendering email templates: %s: %s", booking.site.id, booking, e)
        return None

    arriving_str = get_pretty_date_str(booking.site.arriving)
    msg = EmailMessage()
    msg["Subject"] = (
        f"{config.SITENAME} Booking - {arriving_str} - "
        f"{booking.site.id} - {booking.site.status.upper()}"
    )
    msg["From"] = config.EMAIL_USER
    msg["To"] = booking.leader.email

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
        try:
            with smtplib.SMTP("localhost", 25) as server:
                server.send_message(msg)
        except smtplib.SMTPException as e:
            logger.error("Failed to send email to %s: %s", recipient, e)
            flash(f"Failed to send email to {recipient}: {e}", "danger")
            return False
        except ConnectionRefusedError as e:
            logger.error("Failed to send email to %s: %s", recipient, e)
            flash(f"Failed to send email to {recipient}: {e}", "danger")
            return False
    return True
