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
import re
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
    mail_from = os.environ.get("NEWSLETTER_MAIL_FROM") or os.environ.get("MAIL_FROM", "Dogboxx <noreply@dogboxx.org>")

    if not api_key:
        logging.error("RESEND_API_KEY is not set — cannot send newsletter")
        return {'sent': 0, 'failed': len(recipients)}

    RESEND_BATCH_URL = "https://api.resend.com/emails/batch"

    SHELL_TOP = """<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title></title>
</head>
<body style="margin:0;padding:0;background-color:#f6f3f2;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
    <tr>
      <td align="center" style="background-color:#f6f3f2;padding:32px;">
        <table role="presentation" cellpadding="0" cellspacing="0" width="560" style="max-width:560px;width:100%;">
          <tr>
            <td style="background-color:#1B1B1B;border-bottom:3px solid #E02FAC;padding:18px 32px;border-radius:6px 6px 0 0;">
              <span style="font-size:1.35rem;font-weight:800;color:#ffffff;letter-spacing:-0.01em;">DogBoxx</span>
            </td>
          </tr>
          <tr>
            <td style="background-color:#ffffff;border:1px solid #e2dfde;border-top:none;border-radius:0 0 6px 6px;padding:36px 32px 28px;">
              <div style="font-size:1rem;color:#3d3d3d;line-height:1.6;">"""

    SHELL_BOTTOM = """</div>
              <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin-top:24px;">
                <tr>
                  <td style="border-top:1px solid #e2dfde;padding-top:16px;font-size:0.8rem;color:#888888;line-height:1.6;text-align:center;">
                    You're receiving this because you're a DogBoxx client.
                    <a href="%%UNSUBSCRIBE_URL%%" style="color:#888888;">Unsubscribe</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    # Strip empty paragraphs (<p><br></p>) that Quill inserts for blank lines —
    # they render as large gaps in email clients.
    html_template = re.sub(r'<p>\s*<br\s*/?>\s*</p>', '', html_template)

    batch = []
    for r in recipients:
        html = SHELL_TOP + html_template + SHELL_BOTTOM
        html = html.replace("{{firstname}}", r.get("firstname", ""))
        html = html.replace("{{dog_name}}", r.get("dog_name", "your dog"))
        html = html.replace("%%UNSUBSCRIBE_URL%%", r["unsubscribe_url"])
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
