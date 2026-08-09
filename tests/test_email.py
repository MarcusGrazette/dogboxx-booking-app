"""
Regression tests for app/utils/email.py (audit M12, M13):
- M12: batch sends must chunk at Resend's 100-recipient cap, not fail whole.
- M13: merge tags ({{firstname}}, {{dog_name}}) must be HTML-escaped before
  being interpolated into the email body — they're client-editable values.
"""
import pytest

from app.utils import email as email_mod


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


@pytest.fixture(autouse=True)
def resend_api_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("MAIL_REPLY", "DogBoxx <lydia@dogboxx.org>")


def test_newsletter_batch_escapes_merge_tags(monkeypatch):
    captured = []

    def fake_post(url, headers, json, timeout):
        captured.append(json)
        return _FakeResponse(200)

    monkeypatch.setattr(email_mod.requests, "post", fake_post)

    recipients = [{
        "email": "a@test.com",
        "firstname": '<img src=x onerror=alert(1)>',
        "dog_name": "</div><script>evil()</script>",
        "unsubscribe_url": "https://app.dogboxx.org/auth/unsubscribe/tok",
    }]

    result = email_mod.send_newsletter_batch(
        subject="Hi", html_template="<p>Hello {{firstname}} and {{dog_name}}</p>",
        recipients=recipients,
    )

    assert result == {'sent': 1, 'failed': 0}
    sent_html = captured[0][0]["html"]
    # The escaped text legitimately still contains the substrings "<img" and
    # "onerror=" as harmless text content (e.g. "&lt;img ... onerror=...") —
    # the actual invariant is that no *live* tag/handler survives escaping.
    assert "<script>" not in sent_html
    assert "<img" not in sent_html
    assert "&lt;img" in sent_html
    assert "&lt;script&gt;evil()&lt;/script&gt;" in sent_html


def test_broadcast_batch_escapes_merge_tags(monkeypatch):
    captured = []

    def fake_post(url, headers, json, timeout):
        captured.append(json)
        return _FakeResponse(200)

    monkeypatch.setattr(email_mod.requests, "post", fake_post)

    recipients = [{
        "email": "a@test.com",
        "firstname": '"><script>evil()</script>',
        "dog_name": "Rex",
    }]

    result = email_mod.send_broadcast_batch(
        subject="Weather", body_text="Hi {{firstname}}, walks for {{dog_name}} are cancelled.",
        recipients=recipients,
    )

    assert result == {'sent': 1, 'failed': 0}
    sent_html = captured[0][0]["html"]
    assert "<script>" not in sent_html
    assert "&lt;script&gt;evil()&lt;/script&gt;" in sent_html


def test_newsletter_batch_chunks_past_100_recipients(monkeypatch):
    """Regression (audit M12): Resend's batch endpoint caps at 100 messages
    per request. 250 recipients must go out as 3 chunked requests (100, 100,
    50), not one oversized request that fails everything."""
    chunk_sizes = []

    def fake_post(url, headers, json, timeout):
        chunk_sizes.append(len(json))
        return _FakeResponse(200)

    monkeypatch.setattr(email_mod.requests, "post", fake_post)

    recipients = [
        {"email": f"user{i}@test.com", "firstname": f"User{i}",
         "dog_name": "Fido", "unsubscribe_url": "https://app.dogboxx.org/u/tok"}
        for i in range(250)
    ]

    result = email_mod.send_newsletter_batch(
        subject="Hi", html_template="<p>Hello {{firstname}}</p>", recipients=recipients,
    )

    assert result == {'sent': 250, 'failed': 0}
    assert chunk_sizes == [100, 100, 50]


def test_broadcast_batch_partial_failure_reports_correct_counts(monkeypatch):
    """A chunk that fails must be counted as failed without sinking the
    chunks that succeeded — the whole point of chunking instead of one
    all-or-nothing request."""
    call_count = {"n": 0}

    def fake_post(url, headers, json, timeout):
        call_count["n"] += 1
        if call_count["n"] == 2:
            return _FakeResponse(500, "server error")
        return _FakeResponse(200)

    monkeypatch.setattr(email_mod.requests, "post", fake_post)

    recipients = [
        {"email": f"user{i}@test.com", "firstname": f"User{i}", "dog_name": "Fido"}
        for i in range(150)
    ]

    result = email_mod.send_broadcast_batch(
        subject="Weather", body_text="Hi {{firstname}}", recipients=recipients,
    )

    # First chunk (100) succeeds, second chunk (50) fails
    assert result == {'sent': 100, 'failed': 50}
