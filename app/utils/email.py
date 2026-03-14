"""
Email sending via Resend.

Usage:
    from app.utils.email import send_email
    send_email(
        to="user@example.com",
        subject="Reset your password",
        html="<p>Click <a href='...'>here</a> to reset.</p>"
    )

Required environment variable:
    RESEND_API_KEY  — from https://resend.com/api-keys
    MAIL_FROM       — verified sender address, e.g. "Dogboxx <noreply@dogboxx.org>"
"""

import logging
import os
import requests

RESEND_API_URL = "https://api.resend.com/emails"


def send_email(to: str, subject: str, html: str) -> bool:
    """Send a transactional email via Resend. Returns True on success."""
    api_key = os.environ.get("RESEND_API_KEY")
    mail_from = os.environ.get("MAIL_FROM", "Dogboxx <noreply@dogboxx.org>")

    if not api_key:
        logging.error("RESEND_API_KEY is not set — cannot send email")
        return False

    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": mail_from,
                "to": [to],
                "subject": subject,
                "html": html,
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            logging.info(f"Email sent to {to}: {subject}")
            return True
        else:
            logging.error(f"Resend error {resp.status_code}: {resp.text}")
            return False
    except requests.RequestException as e:
        logging.error(f"Email send failed: {e}")
        return False
