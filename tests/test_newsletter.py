"""
Admin newsletter tests.

Covers:
- /admin/newsletter POST does not 500 (regression — used to access u.client.dogs
  which doesn't exist; dogs are owned via the DogOwner join table).
- Primary dog name is resolved correctly per recipient; clients without a primary
  dog get the 'your dog' fallback.
- /admin/newsletter/test returns JSON (so the compose page can stay loaded and
  the user's draft survives across a test send).
"""
import json
import pytest
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from app import db
from app.models import User, Client, Dog, DogOwner


TRUNCATE_ORDER = [
    'booking_status_changes', 'bookings', 'notifications',
    'dog_owners', 'dogs', 'clients', 'walkers', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        for table in TRUNCATE_ORDER:
            db.session.execute(text(f'DELETE FROM {table}'))
        db.session.commit()
    yield


@pytest.fixture
def captured_newsletters(monkeypatch):
    """Capture send_newsletter_batch calls instead of hitting Resend."""
    sent = []

    def fake_send_newsletter_batch(subject, html_template, recipients):
        sent.append({
            'subject': subject,
            'html_template': html_template,
            'recipients': recipients,
        })
        return {'sent': len(recipients), 'failed': 0}

    # The route does `from app.utils.email import send_newsletter_batch` at
    # request time; patching the module attribute is sufficient.
    monkeypatch.setattr(
        'app.utils.email.send_newsletter_batch', fake_send_newsletter_batch
    )
    return sent


def _make_user(email, role='client', is_admin=False):
    u = User(
        firstname='Test', lastname='Person',
        email=email, role=role, is_admin=is_admin,
        hashed_password=generate_password_hash('Testpass1!'),
        active=True, email_marketing=True,
    )
    db.session.add(u)
    db.session.flush()
    return u


def _make_client(user_id):
    c = Client(user_id=user_id, onboarding_completed=True)
    db.session.add(c)
    db.session.flush()
    return c


def _attach_primary_dog(user_id, dog_name):
    d = Dog(name=dog_name, breed='Labrador')
    db.session.add(d)
    db.session.flush()
    db.session.add(DogOwner(dog_id=d.id, user_id=user_id, role='primary'))
    db.session.flush()
    return d


def _login(client, email):
    return client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!',
    }, follow_redirects=True)


# ---------------------------------------------------------------------------
# /admin/newsletter — main send
# ---------------------------------------------------------------------------

class TestNewsletterSend:

    def test_send_does_not_500_with_mixed_dog_ownership(self, app, client,
                                                       captured_newsletters):
        """Regression: route used to access u.client.dogs (no such attribute —
        dogs live on DogOwner). Every send to a non-empty subscriber list
        returned 500. Now it succeeds and resolves the primary dog via a
        batched DogOwner query."""
        with app.app_context():
            admin = _make_user('admin@nl-test.com', role='walker', is_admin=True)
            # Client with a primary dog
            c1 = _make_user('c1@nl-test.com')
            _make_client(c1.id)
            _attach_primary_dog(c1.id, 'Buddy')
            # Client with NO dog (e.g. account pending dog assignment)
            c2 = _make_user('c2@nl-test.com')
            _make_client(c2.id)
            db.session.commit()
            admin_email = admin.email

        _login(client, admin_email)
        resp = client.post('/admin/newsletter', data={
            'subject': 'Hello',
            'html_body': '<p>Body</p>',
        })
        assert resp.status_code != 500, \
            f"newsletter route 500'd: {resp.data.decode()[:500]}"

        assert len(captured_newsletters) == 1
        recipients_by_email = {
            r['email']: r for r in captured_newsletters[0]['recipients']
        }
        assert recipients_by_email['c1@nl-test.com']['dog_name'] == 'Buddy'
        assert recipients_by_email['c2@nl-test.com']['dog_name'] == 'your dog'

    def test_send_skips_unsubscribed_clients(self, app, client,
                                             captured_newsletters):
        """Clients with email_marketing=False are not included in the batch."""
        with app.app_context():
            admin = _make_user('admin2@nl-test.com', role='walker', is_admin=True)
            u_in = _make_user('included@nl-test.com')
            _make_client(u_in.id)
            u_out = _make_user('excluded@nl-test.com')
            u_out.email_marketing = False
            _make_client(u_out.id)
            db.session.commit()
            admin_email = admin.email

        _login(client, admin_email)
        client.post('/admin/newsletter', data={
            'subject': 'X', 'html_body': '<p>Body</p>',
        })

        assert len(captured_newsletters) == 1
        emails = {r['email'] for r in captured_newsletters[0]['recipients']}
        assert 'included@nl-test.com' in emails
        assert 'excluded@nl-test.com' not in emails


# ---------------------------------------------------------------------------
# /admin/newsletter/test — JSON, draft-preserving
# ---------------------------------------------------------------------------

class TestNewsletterTest:

    def test_test_send_returns_json_not_redirect(self, app, client,
                                                 captured_newsletters):
        """Regression: the test endpoint used to redirect to /admin/newsletter,
        which wiped the in-progress draft. Now it returns JSON so the page
        stays loaded and the Quill editor keeps its content."""
        with app.app_context():
            admin = _make_user('admin3@nl-test.com', role='walker', is_admin=True)
            db.session.commit()
            admin_email = admin.email

        _login(client, admin_email)
        resp = client.post('/admin/newsletter/test', data={
            'subject': 'Hello', 'html_body': '<p>Body</p>',
        })
        assert resp.status_code == 200
        assert resp.content_type.startswith('application/json')
        data = resp.get_json()
        assert data['success'] is True
        # Test email goes to the current admin's own address, not a hardcoded one
        assert admin_email in data['message'].lower()

        # The captured batch was prefixed with [TEST] so it's clearly a test
        assert captured_newsletters[0]['subject'].startswith('[TEST] ')

    def test_test_send_rejects_empty_draft(self, app, client,
                                           captured_newsletters):
        """Empty subject or body returns 400 JSON, no send attempted."""
        with app.app_context():
            admin = _make_user('admin4@nl-test.com', role='walker', is_admin=True)
            db.session.commit()
            admin_email = admin.email

        _login(client, admin_email)
        resp = client.post('/admin/newsletter/test', data={
            'subject': '', 'html_body': '',
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['success'] is False
        assert captured_newsletters == []  # never called
