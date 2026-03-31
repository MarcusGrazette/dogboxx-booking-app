"""
Email sending via Resend.

Usage:
    from app.utils.email import send_email, send_newsletter_batch
    send_email(
        to="user@example.com",
        subject="Reset your password",
        html="<p>Click <a href='...'>here</a> to reset.</p>"
    )

    # Newsletter batch — recipients is a list of dicts:
    #   [{'email': ..., 'firstname': ..., 'dog_name': ..., 'unsubscribe_url': ...}]
    send_newsletter_batch(
        subject="March update from Dogboxx",
        html_template="<p>Hi {{firstname}}, ...</p>",
        recipients=recipients,
    )

Required environment variables:
    RESEND_API_KEY  — from https://resend.com/api-keys
    MAIL_FROM       — verified sender address, e.g. "Dogboxx <noreply@dogboxx.org>"
    APP_BASE_URL    — e.g. https://dogboxx.up.railway.app
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


def send_newsletter_batch(subject: str, html_template: str, recipients: list) -> dict:
    """
    Send a personalised newsletter to a list of recipients via Resend batch API.

    Each recipient dict must have:
        email, firstname, dog_name, unsubscribe_url

    Merge tags supported in html_template: {{firstname}}, {{dog_name}}

    Returns {'sent': int, 'failed': int}
    """
    api_key = os.environ.get("RESEND_API_KEY")
    mail_from = os.environ.get("MAIL_FROM", "Dogboxx <noreply@dogboxx.org>")

    if not api_key:
        logging.error("RESEND_API_KEY is not set — cannot send newsletter")
        return {'sent': 0, 'failed': len(recipients)}

    RESEND_BATCH_URL = "https://api.resend.com/emails/batch"
    UNSUBSCRIBE_FOOTER = """
    <hr style="margin:32px 0;border:none;border-top:1px solid #eee;">
    <p style="color:#999;font-size:0.8em;text-align:center;">
      You're receiving this because you're a Dogboxx client.
      <a href="{unsubscribe_url}" style="color:#999;">Unsubscribe</a>
    </p>
    """

    batch = []
    for r in recipients:
        html = html_template
        html = html.replace("{{firstname}}", r.get("firstname", ""))
        html = html.replace("{{dog_name}}", r.get("dog_name", "your dog"))
        html += UNSUBSCRIBE_FOOTER.format(unsubscribe_url=r["unsubscribe_url"])
        batch.append({
            "from": mail_from,
            "to": [r["email"]],
            "subject": subject,
            "html": html,
        })

    try:
        resp = requests.post(
            RESEND_BATCH_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=batch,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            sent = len(batch)
            logging.info(f"Newsletter batch sent: {sent} emails, subject: {subject!r}")
            return {'sent': sent, 'failed': 0}
        else:
            logging.error(f"Resend batch error {resp.status_code}: {resp.text}")
            return {'sent': 0, 'failed': len(batch)}
    except requests.RequestException as e:
        logging.error(f"Newsletter batch send failed: {e}")
        return {'sent': 0, 'failed': len(batch)}
