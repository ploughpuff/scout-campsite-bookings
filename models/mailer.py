"""
mailer.py - Provide functions to send emails from the app.
"""

import logging
import smtplib
from datetime import datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import html2text
from flask import flash
from jinja2 import Environment, FileSystemLoader, TemplateError

import config
from models import outbox
from models.net import SMTP_TIMEOUT, classify_smtp
from models.schemas import LiveBooking
from models.utils import get_pretty_date_str, is_email_enabled, now_uk

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

    body = _build_email_body(rec)
    msg = _create_email_message(body, rec, subject_append_str)

    if not msg:
        return False

    #
    ## Stamp only once the message is on the queue. It used to be stamped first
    ## and never rolled back, so after an SMTP outage the record claimed an
    ## email the notes had no trace of - two halves of the same record
    ## disagreeing, with the wrong half being the one that survived.
    if not _send_email(msg, rec.leader.email, booking_id=rec.booking.id):
        return False

    if rec.tracking.status == "Pending":
        rec.tracking.pending_email_sent = now_uk()
    elif rec.tracking.status == "Confirmed":
        rec.tracking.confirm_email_sent = now_uk()
    else:
        rec.tracking.cancel_email_sent = now_uk()

    return True


def send_invoice_email(
    rec: LiveBooking,
    invoice_number: str,
    online_url: str = None,
    pdf_bytes: bytes = None,
    due_date_iso: str = None,
) -> bool:
    """Email the Xero invoice to the booking's leader, with the PDF attached.

    Sent from the app (not Xero) so it goes to the leader's address on the
    booking rather than whatever emails the Xero contact holds.
    """
    due_str = None
    if due_date_iso:
        try:
            due_str = get_pretty_date_str(datetime.fromisoformat(due_date_iso), full_month=True)
        except ValueError:
            due_str = due_date_iso

    context = {
        "rec": rec,
        "sitename": config.SITENAME,
        "invoice_number": invoice_number,
        "amount_str": f"{rec.tracking.cost_estimate / 100:.2f}",
        "due_str": due_str,
        "online_url": online_url,
        "arriving_str": get_pretty_date_str(rec.booking.arriving, inc_time=True, full_month=True),
        "departing_str": get_pretty_date_str(
            rec.booking.departing, inc_time=True, full_month=True
        ),
    }

    try:
        body = env.get_template("invoice_email.html").render(context)
    except TemplateError as e:
        logger.error("%s trouble rendering invoice email: %s", rec.booking.id, e)
        return False

    msg = EmailMessage()
    msg["Subject"] = f"{config.SITENAME} Invoice {invoice_number}: Booking {rec.booking.id}"
    msg["From"] = f"{config.EMAIL_DISPLAY_USERNAME} <{config.EMAIL_FROM_ADDRESS}>"
    msg["To"] = rec.leader.email

    h = html2text.HTML2Text()
    msg.set_content(h.handle(body))
    msg.add_alternative(body, subtype="html")

    if pdf_bytes:
        msg.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=f"{invoice_number}.pdf",
        )

    return _send_email(msg, rec.leader.email, booking_id=rec.booking.id)


def send_confirm_numbers_email(rec: LiveBooking) -> bool:
    """Email the leader asking them to confirm their actual attendance numbers.

    Sent before the invoice is raised when the final headcount isn't yet known.
    The leader replies with the confirmed per-day numbers (and Roxby Hut usage
    where relevant); no PDF is attached.
    """
    nights = [
        {
            "label": get_pretty_date_str(
                rec.booking.arriving + timedelta(days=i), full_month=True, always_year=True
            ),
            "size": rec.booking.size_for_night((rec.booking.arriving + timedelta(days=i)).date()),
        }
        for i in range(rec.booking.num_overnights())
    ]

    context = {
        "rec": rec,
        "sitename": config.SITENAME,
        "arriving_str": get_pretty_date_str(rec.booking.arriving, inc_time=True, full_month=True),
        "departing_str": get_pretty_date_str(
            rec.booking.departing, inc_time=True, full_month=True
        ),
        "nights": nights,
        "roxby": "Roxby Hut" in rec.booking.facilities,
    }

    try:
        body = env.get_template("confirm_numbers_email.html").render(context)
    except TemplateError as e:
        logger.error("%s trouble rendering confirm numbers email: %s", rec.booking.id, e)
        return False

    msg = EmailMessage()
    msg["Subject"] = f"{config.SITENAME} Booking {rec.booking.id}: please confirm your numbers"
    msg["From"] = f"{config.EMAIL_DISPLAY_USERNAME} <{config.EMAIL_FROM_ADDRESS}>"
    msg["To"] = rec.leader.email

    h = html2text.HTML2Text()
    msg.set_content(h.handle(body))
    msg.add_alternative(body, subtype="html")

    return _send_email(msg, rec.leader.email, booking_id=rec.booking.id)


def _build_email_body(rec: LiveBooking):
    """
    Confirmed
    Cancelled
    Pending
    """

    arriving_str = get_pretty_date_str(rec.booking.arriving, inc_time=True, full_month=True)
    departing_str = get_pretty_date_str(rec.booking.departing, inc_time=True, full_month=True)

    context = {
        "rec": rec,
        "arriving_str": arriving_str,
        "departing_str": departing_str,
        "cancel_by_str": get_pretty_date_str(
            rec.booking.arriving - timedelta(weeks=2), full_month=True
        ),
        "event_type": rec.booking.event_type,
    }

    try:
        return env.get_template("base_email.html").render(context)
    except TemplateError as e:
        logger.error("%s trouble rendering email templates: %s: %s", rec.booking.id, rec, e)
        return None


def _create_email_message(body: str, rec: LiveBooking, subject_append_str: str = ""):
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
    arriving_str = get_pretty_date_str(rec.booking.arriving)
    msg = EmailMessage()
    subject = (
        f"{config.SITENAME} Booking for {arriving_str}: "
        f"{rec.booking.id} {rec.tracking.status.upper()}"
    )
    if subject_append_str:
        subject += f" ({subject_append_str})"
    msg["Subject"] = subject
    msg["From"] = f"{config.EMAIL_DISPLAY_USERNAME} <{config.EMAIL_FROM_ADDRESS}>"

    msg["To"] = rec.leader.email

    h = html2text.HTML2Text()
    body_text = h.handle(body)

    msg.set_content(body_text)
    msg.add_alternative(body, subtype="html")
    return msg


def _send_email(msg, recipient, booking_id: str = None) -> bool:
    """Queue a prepared message for delivery.

    Returns True when the message is on the queue and will therefore be
    delivered, False when email is switched off and it never will be. The
    distinction matters: this used to return True in both cases, so callers
    journalled "Email Sent" against mail that was never going anywhere.

    The send itself happens in the handler below, which may run long after this
    returns - so nothing here may depend on a request context.
    """
    if not is_email_enabled():
        flash(f"Email sending disabled by env var EMAIL_ENABLED: {recipient}:", "info")
        return False

    #
    ## Rendered now, stored as bytes, sent later. Freezing the message at the
    ## moment the decision was made means a booking edited - or archived - while
    ## the queue is backed up cannot change what the leader is told.
    outbox.enqueue(
        "email",
        {"recipient": recipient, "subject": msg["Subject"]},
        booking_id=booking_id,
        blob=msg.as_bytes(),
    )
    return True


@outbox.handler("email")
def _handle_email(item) -> None:
    """Actually put the message on the wire."""
    msg = BytesParser(policy=policy.default).parsebytes(outbox.read_payload(item.id))
    recipient = item.payload["recipient"]

    delivered = False
    try:
        if config.APP_ENV == "production":
            # Add bcc to site owner
            all_recipients = [recipient, config.EMAIL_FROM_ADDRESS]
            with smtplib.SMTP("smtp.office365.com", 587, timeout=SMTP_TIMEOUT) as server:
                server.starttls()
                server.login(config.EMAIL_LOGIN_USERNAME, config.EMAIL_LOGIN_PASSWD)
                server.send_message(msg, to_addrs=all_recipients)
                delivered = True
        else:
            with smtplib.SMTP("localhost", 25, timeout=SMTP_TIMEOUT) as server:
                server.send_message(msg)
                delivered = True

    except (smtplib.SMTPException, OSError) as e:
        #
        ## Once send_message returns, the mail has gone - whatever happens next.
        ## Office365 answers 250 to QUIT and smtplib's context manager raises on
        ## any reply but 221, so closing the session used to report a delivered
        ## email as a failure. Retrying that would send it twice.
        if delivered:
            logger.warning("Email to %s was accepted but the session ended badly: %s", recipient, e)
            return

        logger.error("%s - Could not send email to %s: %s", config.APP_ENV, recipient, e)
        raise classify_smtp(e) from e

    logger.info("Email sent to %s: %s", recipient, item.payload.get("subject"))
