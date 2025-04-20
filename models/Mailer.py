# mailer.py
import smtplib
from email.message import EmailMessage
from jinja2 import Environment, FileSystemLoader
import os
import logging
import config

logger = logging.getLogger("app_logger")

# Setup Jinja2 environment to load from templates folder
env = Environment(loader=FileSystemLoader(config.EMAIL_TEMP_DIR))

   

        
        
        
def send_email_notification(booking_id, booking):
    
    recipient = booking.get("original_sheet_data", {}).get("Email address")
    
    if not recipient:
        logger.warning(f"No email address found for booking: {booking_id} {booking}")
        return False
    
    context = {
        "leader": booking.get("Leader", "Leader"),
        "arriving": booking.get("Arriving", "a future date"),
        "campsite": booking.get("Campsite", "the campsite"),
        "booking_id": booking_id
    }
    
    if booking["Status"] == "Confirmed":
        text_template = env.get_template("booking_confirmed.txt")
        html_template = env.get_template("booking_confirmed.html")
    
    elif booking["Status"] == "Cancelled":
        text_template = env.get_template("booking_cancelled.txt")
        html_template = env.get_template("booking_cancelled.html")
    
    elif booking["Status"] == "Pending":
        text_template = env.get_template("booking_pending.txt")
        html_template = env.get_template("booking_pending.html")
    
    else:
        logger.warning(f"No email to be sent for status: {booking["Status"]} {booking}")
        return False
    
    try:
        body_text = text_template.render(context)
        body_html = html_template.render(context)
    except Exception as e:
        logger.error(f"Error rendering email templates: {e} {booking}")
        return False

    msg = EmailMessage()
    msg["Subject"] = "✅ Campsite Booking " + booking["Status"]
    msg["From"] = config.EMAIL_USER
    msg["To"] = recipient

    # Add plain and HTML versions
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")

    try:
        #with smtplib.SMTP("smtp.office365.com", 587) as server:
        #    server.starttls()
        #    server.login(config.EMAIL_USER, config.EMAIL_PASS)
        #    server.send_message(msg)
        #booking["email_confirmation_sent"] = get_pretty_datetime_str(include_seconds=True)
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {recipient}: {e}")
        flash(f"Failed to send email to {recipient}: {e}", "danger")
        return False
