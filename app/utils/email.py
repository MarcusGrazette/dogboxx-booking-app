"""
Email sending via Resend.

Usage:
    from app.utils.email import send_email, send_newsletter_batch, send_broadcast_batch
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
    MAIL_NO_REPLY   — verified sender for transactional mail (password reset, bug
                      report). e.g. "DogBoxx <noreply@dogboxx.org>"
    MAIL_REPLY      — verified sender for client-facing mail clients can reply to
                      (newsletter, broadcasts). e.g. "Lydia <lydia@dogboxx.org>".
                      Falls back to MAIL_NO_REPLY with a warning if unset.
    APP_BASE_URL    — e.g. https://dogboxx.up.railway.app
"""

import html as _htmllib
import logging
import os
import re
import requests

RESEND_API_URL = "https://api.resend.com/emails"
RESEND_BATCH_URL = "https://api.resend.com/emails/batch"

# Resend's batch endpoint hard-caps at 100 messages per request — a batch
# larger than this returns a non-2xx for the *whole* request, so anything
# past ~100 clients would otherwise fail the entire send silently.
RESEND_BATCH_CHUNK_SIZE = 100


def _send_batch_chunked(batch: list, label: str) -> dict:
    """POST `batch` to Resend's batch endpoint in chunks of
    RESEND_BATCH_CHUNK_SIZE, aggregating sent/failed across all chunks."""
    api_key = os.environ.get("RESEND_API_KEY")
    sent = failed = 0
    for i in range(0, len(batch), RESEND_BATCH_CHUNK_SIZE):
        chunk = batch[i:i + RESEND_BATCH_CHUNK_SIZE]
        try:
            resp = requests.post(
                RESEND_BATCH_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=chunk,
                timeout=30,
            )
            if resp.status_code in (200, 201):
                sent += len(chunk)
            else:
                logging.error(f"Resend {label} batch error {resp.status_code}: {resp.text}")
                failed += len(chunk)
        except requests.RequestException as e:
            logging.error(f"{label} batch send failed: {e}")
            failed += len(chunk)
    logging.info(f"{label} batch sent: {sent} emails, {failed} failed")
    return {'sent': sent, 'failed': failed}


def _mail_reply_sender() -> str:
    """Sender for client-replyable mail (newsletter, broadcasts).

    Falls back to MAIL_NO_REPLY if MAIL_REPLY is unset so a missed Railway var
    sends from noreply instead of failing the send entirely.
    """
    reply = os.environ.get("MAIL_REPLY")
    if reply:
        return reply
    logging.warning("MAIL_REPLY not set — falling back to MAIL_NO_REPLY")
    return os.environ.get("MAIL_NO_REPLY", "DogBoxx <noreply@dogboxx.org>")


def send_email(to: str, subject: str, html: str) -> bool:
    """Send a transactional email via Resend. Returns True on success."""
    api_key = os.environ.get("RESEND_API_KEY")
    mail_from = os.environ.get("MAIL_NO_REPLY", "DogBoxx <noreply@dogboxx.org>")

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


# Branded wrapper shared by the newsletter and broadcasts: the black DogBoxx
# header bar over a white card. _EMAIL_TOP + body + footer + _EMAIL_BOTTOM.
_EMAIL_TOP = """<!DOCTYPE html>
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

_EMAIL_BOTTOM = """
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

# Newsletter-only footer (marketing mail must offer an unsubscribe).
_UNSUBSCRIBE_FOOTER = """
              <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin-top:24px;">
                <tr>
                  <td style="border-top:1px solid #e2dfde;padding-top:16px;font-size:0.8rem;color:#888888;line-height:1.6;text-align:center;">
                    You're receiving this because you're a DogBoxx client.
                    <a href="%%UNSUBSCRIBE_URL%%" style="color:#888888;">Unsubscribe</a>
                  </td>
                </tr>
              </table>"""


def _send_personalised_batch(subject: str, body_html: str, recipients: list, *,
                             label: str, footer: str = '') -> dict:
    """Wrap Quill-authored, already-sanitized `body_html` in the branded
    shell once per recipient — filling the {{firstname}}/{{dog_name}} merge
    tags and any %%UNSUBSCRIBE_URL%% in `footer` — and send the lot via
    Resend's batch API. Returns {'sent': int, 'failed': int}."""
    if not os.environ.get("RESEND_API_KEY"):
        logging.error(f"RESEND_API_KEY is not set — cannot send {label.lower()}")
        return {'sent': 0, 'failed': len(recipients)}
    if not recipients:
        return {'sent': 0, 'failed': 0}

    mail_from = _mail_reply_sender()
    # Strip empty paragraphs (<p><br></p>) that Quill inserts for blank lines —
    # they render as large gaps in email clients.
    body_html = re.sub(r'<p>\s*<br\s*/?>\s*</p>', '', body_html)

    batch = []
    for r in recipients:
        # firstname/dog_name are client-editable (profile.py) — escape before
        # interpolating into HTML. sanitize_rich_text sanitized the body itself
        # (tags and attributes), not these substituted values, so they need
        # their own escaping pass here.
        body = body_html.replace("{{firstname}}", _htmllib.escape(r.get("firstname") or ""))
        body = body.replace("{{dog_name}}", _htmllib.escape(r.get("dog_name") or "your dog"))
        foot = footer.replace("%%UNSUBSCRIBE_URL%%", r.get("unsubscribe_url") or "")
        batch.append({
            "from": mail_from,
            "to": [r["email"]],
            "subject": subject,
            "html": _EMAIL_TOP + body + "</div>" + foot + _EMAIL_BOTTOM,
        })

    return _send_batch_chunked(batch, label)


def send_newsletter_batch(subject: str, html_template: str, recipients: list) -> dict:
    """
    Send a personalised newsletter to a list of recipients via Resend batch API.

    Each recipient dict must have:
        email, firstname, dog_name, unsubscribe_url

    Merge tags supported in html_template: {{firstname}}, {{dog_name}}

    Returns {'sent': int, 'failed': int}
    """
    return _send_personalised_batch(subject, html_template, recipients,
                                    label="Newsletter", footer=_UNSUBSCRIBE_FOOTER)


def send_broadcast_batch(subject: str, body_text: str, recipients: list) -> dict:
    """
    Send a stripped-down operational broadcast (e.g. weather cancellation) to a
    list of recipients via Resend batch API.

    body_text is Quill-authored HTML, already sanitized by the caller
    (app.utils.sanitize.sanitize_rich_text). No unsubscribe link (operational,
    not marketing).

    Each recipient dict must have: email, firstname. Optional: dog_name.

    Merge tags supported in body_text: {{firstname}}, {{dog_name}}. There is no
    auto-greeting — the admin writes the salutation (if any) into the body.

    Returns {'sent': int, 'failed': int}
    """
    return _send_personalised_batch(subject, body_text, recipients, label="Broadcast")
