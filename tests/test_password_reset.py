"""
Password reset flow tests.

Covers:
- /auth/forgot-password issues a token and sends an email for an existing active user
- /auth/forgot-password does not send an email for unknown / inactive users (no enumeration)
- /auth/reset-password/<token> renders the form for a valid token, redirects for an invalid one
- A valid token sets the new password, lets the user log in, invalidates the old password
- An expired token is rejected
- A previously-used token cannot be reused (the password hash it embeds has changed)
- Changing the password via /auth/change-password invalidates outstanding reset tokens
- Tampered tokens and tokens for unknown users are rejected
- The password_reset.html email template renders without error
"""
import pytest
from werkzeug.security import generate_password_hash, check_password_hash
from flask import render_template

from app import db
from app.models import User
from app.blueprints.auth.routes import _make_reset_token, _verify_reset_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(email='reset_user@test.com', password='OldPass123!', active=True):
    u = User(
        firstname='Reset', lastname='User', email=email,
        role='client', is_admin=False, active=active,
        hashed_password=generate_password_hash(password),
    )
    db.session.add(u)
    db.session.flush()
    db.session.commit()
    return u


@pytest.fixture
def captured_emails(monkeypatch):
    """Capture send_email calls instead of hitting Resend."""
    sent = []

    def fake_send_email(to, subject, html):
        sent.append({'to': to, 'subject': subject, 'html': html})
        return True

    # The route does `from app.utils.email import send_email` at request time.
    # Patching the module attribute is sufficient because subsequent imports
    # resolve through sys.modules.
    monkeypatch.setattr('app.utils.email.send_email', fake_send_email)
    return sent


# ---------------------------------------------------------------------------
# /auth/forgot-password
# ---------------------------------------------------------------------------

class TestForgotPassword:

    def test_existing_user_receives_reset_email(self, app, client, captured_emails):
        with app.app_context():
            make_user(email='alice@test.com')

        resp = client.post('/auth/forgot-password', data={'email': 'alice@test.com'},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert len(captured_emails) == 1
        sent = captured_emails[0]
        assert sent['to'] == 'alice@test.com'
        assert 'DogBoxx' in sent['subject']
        assert '/auth/reset-password/' in sent['html']

    def test_email_lookup_is_case_insensitive(self, app, client, captured_emails):
        """Emails are stored lowercased; users typing UPPERCASE should still get a link."""
        with app.app_context():
            make_user(email='bob@test.com')

        client.post('/auth/forgot-password', data={'email': 'BOB@TEST.COM'},
                    follow_redirects=True)
        assert len(captured_emails) == 1
        assert captured_emails[0]['to'] == 'bob@test.com'

    def test_unknown_email_no_send_no_enumeration(self, app, client, captured_emails):
        """Unknown email: response should still look successful, but no email goes out."""
        resp = client.post('/auth/forgot-password', data={'email': 'ghost@test.com'},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert captured_emails == []
        # The "we sent you a link" UI should be shown — same as the happy path.
        # Search for the user-facing copy that the template renders on `sent=True`.
        body = resp.data.decode().lower()
        assert 'check your' in body or 'sent' in body or 'email' in body

    def test_inactive_user_no_email(self, app, client, captured_emails):
        with app.app_context():
            make_user(email='deactivated@test.com', active=False)

        client.post('/auth/forgot-password', data={'email': 'deactivated@test.com'},
                    follow_redirects=True)
        assert captured_emails == []


# ---------------------------------------------------------------------------
# /auth/reset-password/<token>
# ---------------------------------------------------------------------------

class TestResetPassword:

    def test_valid_token_renders_reset_form(self, app, client):
        with app.app_context():
            user = make_user(email='carol@test.com')
            token = _make_reset_token(user)

        resp = client.get(f'/auth/reset-password/{token}')
        assert resp.status_code == 200
        body = resp.data.decode().lower()
        assert 'password' in body  # form is rendered

    def test_invalid_token_redirects_to_forgot_password(self, app, client):
        resp = client.get('/auth/reset-password/this-is-not-a-real-token',
                          follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert '/auth/forgot-password' in resp.headers.get('Location', '')

    def test_valid_token_post_changes_password(self, app, client):
        with app.app_context():
            user = make_user(email='dan@test.com', password='OldPass123!')
            user_id = user.id
            token = _make_reset_token(user)

        resp = client.post(f'/auth/reset-password/{token}', data={
            'password': 'BrandNew456!',
            'confirm_password': 'BrandNew456!',
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert '/auth/login' in resp.headers.get('Location', '')

        with app.app_context():
            refreshed = db.session.get(User, user_id)
            assert check_password_hash(refreshed.hashed_password, 'BrandNew456!')
            assert not check_password_hash(refreshed.hashed_password, 'OldPass123!')

    def test_weak_password_is_rejected(self, app, client):
        """Regression for finding #4: ResetPasswordForm used to only enforce
        Length(min=8), letting users reset to passwords like '12345678' that
        the regular change-password flow would reject. Now both flows share
        wtforms_password_validator (upper + lower + digit + 8 chars)."""
        with app.app_context():
            user = make_user(email='weakreset@test.com', password='OldPass123!')
            user_id = user.id
            token = _make_reset_token(user)

        weak_passwords = [
            ('short', 'must be at least 8'),                # too short
            ('alllowercase1', 'at least one uppercase'),    # missing uppercase
            ('ALLUPPERCASE1', 'at least one lowercase'),    # missing lowercase
            ('NoDigitsHere', 'at least one number'),        # missing digit
        ]
        for pw, expected_fragment in weak_passwords:
            resp = client.post(f'/auth/reset-password/{token}', data={
                'password': pw, 'confirm_password': pw,
            }, follow_redirects=False)
            # Form re-rendered, not redirected to login
            assert resp.status_code == 200, f"weak pw {pw!r} should re-render the form"
            assert expected_fragment.encode() in resp.data.lower(), (
                f"weak pw {pw!r} should show error mentioning {expected_fragment!r}"
            )

        # Password unchanged after every weak attempt
        with app.app_context():
            refreshed = db.session.get(User, user_id)
            assert check_password_hash(refreshed.hashed_password, 'OldPass123!')

    def test_can_log_in_with_new_password_after_reset(self, app, client):
        with app.app_context():
            make_user(email='eve@test.com', password='OldPass123!')
            user = User.query.filter_by(email='eve@test.com').one()
            token = _make_reset_token(user)

        client.post(f'/auth/reset-password/{token}', data={
            'password': 'BrandNew456!',
            'confirm_password': 'BrandNew456!',
        })

        # Old password fails, new password succeeds.
        bad = client.post('/auth/login', data={
            'email': 'eve@test.com', 'password': 'OldPass123!',
        }, follow_redirects=True)
        assert b'invalid' in bad.data.lower() or b'incorrect' in bad.data.lower()

        good = client.post('/auth/login', data={
            'email': 'eve@test.com', 'password': 'BrandNew456!',
        }, follow_redirects=False)
        assert good.status_code in (301, 302)


# ---------------------------------------------------------------------------
# Token lifecycle: expiry, single-use, tampering
# ---------------------------------------------------------------------------

class TestTokenLifecycle:

    def test_expired_token_is_rejected(self, app):
        with app.app_context():
            user = make_user(email='frank@test.com')
            token = _make_reset_token(user)
            # max_age=-1 forces SignatureExpired even with age=0
            assert _verify_reset_token(token, max_age=-1) is None

    def test_used_token_cannot_be_reused(self, app, client):
        """After a successful reset the password hash changes, so the embedded
        hash slice no longer matches and the same token is invalid."""
        with app.app_context():
            user = make_user(email='gina@test.com', password='OldPass123!')
            token = _make_reset_token(user)

        # First use — succeeds
        client.post(f'/auth/reset-password/{token}', data={
            'password': 'BrandNew456!',
            'confirm_password': 'BrandNew456!',
        })

        # Second use — same token should now be invalid
        with app.app_context():
            assert _verify_reset_token(token) is None

        # And the route should redirect to forgot-password
        resp = client.get(f'/auth/reset-password/{token}', follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert '/auth/forgot-password' in resp.headers.get('Location', '')

    def test_password_change_invalidates_outstanding_token(self, app, client):
        """If a user changes their password via /auth/change-password, any
        previously-issued reset token must stop working."""
        with app.app_context():
            user = make_user(email='hank@test.com', password='OldPass123!')
            token = _make_reset_token(user)

        client.post('/auth/login', data={
            'email': 'hank@test.com', 'password': 'OldPass123!',
        })
        client.post('/auth/change-password', data={
            'current_password': 'OldPass123!',
            'new_password': 'OtherPass789!',
            'confirm_password': 'OtherPass789!',
        })

        with app.app_context():
            assert _verify_reset_token(token) is None

    def test_tampered_token_is_rejected(self, app):
        with app.app_context():
            user = make_user(email='ivy@test.com')
            token = _make_reset_token(user)
            # Flip a character in the middle — should fail signature verification
            mid = len(token) // 2
            tampered = token[:mid] + ('A' if token[mid] != 'A' else 'B') + token[mid + 1:]
            assert _verify_reset_token(tampered) is None

    def test_token_for_deleted_user_is_rejected(self, app):
        with app.app_context():
            user = make_user(email='jane@test.com')
            token = _make_reset_token(user)
            db.session.delete(user)
            db.session.commit()
            assert _verify_reset_token(token) is None


# ---------------------------------------------------------------------------
# Email template rendering
# ---------------------------------------------------------------------------

class TestEmailTemplate:

    def test_password_reset_template_renders(self, app):
        # Use test_request_context so context processors that call url_for()
        # (e.g. inject_home_url) have an active request to bind to.
        with app.test_request_context():
            html = render_template(
                'email/password_reset.html',
                firstname='Sam',
                reset_url='https://example.com/auth/reset-password/dummy-token',
            )
            assert 'Sam' in html
            assert 'https://example.com/auth/reset-password/dummy-token' in html
