"""
Tests for /admin/clients/<int:client_id>/edit — focused on the newly
enabled email-address editing.

Covers:
- Happy path: admin updates a client's email; new value sticks, old fails.
- Lowercase normalisation matches the login lookup.
- Duplicate email is rejected with a form error (no IntegrityError 500).
- Editing other fields without touching email leaves email unchanged.
"""
import pytest
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text

from app import db
from app.models import User, Client


TRUNCATE_ORDER = [
    'booking_status_changes', 'bookings', 'notifications',
    'dog_owners', 'dogs', 'walker_unavailabilities',
    'walker_adhoc_availability', 'walker_schedules',
    'walkers', 'clients', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        for t in TRUNCATE_ORDER:
            try:
                db.session.execute(text(f'DELETE FROM {t}'))
            except Exception:
                db.session.rollback()
        db.session.commit()
    yield


def _make_user(email, role='client', is_admin=False, password='Testpass1!'):
    u = User(
        firstname='Test', lastname='Person',
        email=email, role=role, is_admin=is_admin,
        active=True,
        hashed_password=generate_password_hash(password),
    )
    db.session.add(u)
    db.session.flush()
    return u


def _make_client(email):
    u = _make_user(email)
    db.session.add(Client(user_id=u.id, onboarding_completed=True))
    db.session.commit()
    return u


def _make_admin(email='admin@editclient.test.com'):
    u = _make_user(email, role='walker', is_admin=True)
    db.session.commit()
    return u


def _login(client, email):
    return client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!',
    }, follow_redirects=True)


def _base_post_data(firstname='Jane', lastname='Smith', email='jane@test.com'):
    """Minimum payload that satisfies ClientCreateForm's required fields."""
    return {
        'firstname': firstname,
        'lastname':  lastname,
        'email':     email,
    }


class TestAdminEditClientEmail:

    def test_happy_path_admin_can_change_client_email(self, app, client):
        with app.app_context():
            admin = _make_admin()
            target = _make_client('typo@gmial.com')
            target_id = target.id
            admin_email = admin.email

        _login(client, admin_email)
        resp = client.post(
            f'/admin/clients/{target_id}/edit',
            data=_base_post_data(firstname='Typo', lastname='Recovered',
                                 email='fixed@gmail.com'),
            follow_redirects=False,
        )
        # Successful edit redirects back to the client detail page
        assert resp.status_code in (301, 302), resp.data.decode()[:600]

        with app.app_context():
            refreshed = db.session.get(User, target_id)
            assert refreshed.email == 'fixed@gmail.com'
            # Password is untouched — they sign in with the new email + the
            # same password they had before (the actual login flow is covered
            # in test_auth.py; here we just verify the field changed).
            assert check_password_hash(refreshed.hashed_password, 'Testpass1!')
            # User lookup now finds them under the new email and not the old
            assert User.query.filter_by(email='fixed@gmail.com').first() is not None
            assert User.query.filter_by(email='typo@gmial.com').first() is None

    def test_email_is_lowercased_to_match_login_lookup(self, app, client):
        with app.app_context():
            admin = _make_admin()
            target = _make_client('mixed@test.com')
            target_id = target.id
            admin_email = admin.email

        _login(client, admin_email)
        client.post(
            f'/admin/clients/{target_id}/edit',
            data=_base_post_data(email='NEW.Address@Example.COM'),
        )
        with app.app_context():
            refreshed = db.session.get(User, target_id)
            assert refreshed.email == 'new.address@example.com'

    def test_duplicate_email_is_rejected_with_form_error(self, app, client):
        """A second user already has the target email — change must be
        refused as a friendly form error, not propagate as 500."""
        with app.app_context():
            admin = _make_admin()
            other = _make_client('taken@test.com')
            target = _make_client('original@test.com')
            target_id = target.id
            admin_email = admin.email

        _login(client, admin_email)
        resp = client.post(
            f'/admin/clients/{target_id}/edit',
            data=_base_post_data(email='taken@test.com'),
        )
        # Form re-renders (not a redirect) with the validation error
        assert resp.status_code == 200, resp.data.decode()[:500]
        body = resp.data.decode().lower()
        assert 'already' in body  # "Another account already uses this email."

        # Original email survived
        with app.app_context():
            refreshed = db.session.get(User, target_id)
            assert refreshed.email == 'original@test.com'

    def test_editing_other_fields_leaves_email_unchanged(self, app, client):
        """Submitting the form with the existing email + a new firstname
        should not log an email change or otherwise touch the email."""
        with app.app_context():
            admin = _make_admin()
            target = _make_client('stable@test.com')
            target_id = target.id
            admin_email = admin.email

        _login(client, admin_email)
        client.post(
            f'/admin/clients/{target_id}/edit',
            data=_base_post_data(firstname='Renamed', lastname='Person',
                                 email='stable@test.com'),
        )
        with app.app_context():
            refreshed = db.session.get(User, target_id)
            assert refreshed.email == 'stable@test.com'
            assert refreshed.firstname == 'Renamed'
