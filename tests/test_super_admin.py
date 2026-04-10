"""
Tests for the super-admin walker promotion/demotion feature.

Covers:
- Super-admin can promote a plain walker to admin
- Super-admin can demote a promoted walker back to standard
- Non-super-admin admin is forbidden from toggling
- Walker (non-admin) is forbidden from toggling
- Super-admin cannot toggle their own access
- Super-admin target cannot be demoted via the toggle
"""
import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import User, Walker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_walker(email, is_admin=False, is_super_admin=False):
    user = User(
        firstname='Walker', lastname='Test',
        email=email, role='walker',
        is_admin=is_admin,
        is_super_admin=is_super_admin,
        hashed_password=generate_password_hash('Testpass1!'),
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    walker = Walker(user_id=user.id)
    db.session.add(walker)
    db.session.flush()
    return user


def login(flask_client, email):
    return flask_client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!'
    }, follow_redirects=True)


def toggle(flask_client, walker_user_id):
    return flask_client.post(
        f'/admin/walkers/{walker_user_id}/toggle-admin',
        content_type='application/json',
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def super_admin():
    return make_walker('owner@dogboxx.org', is_admin=True, is_super_admin=True)


@pytest.fixture
def promoted_admin():
    return make_walker('promoted@dogboxx.org', is_admin=True, is_super_admin=False)


@pytest.fixture
def plain_walker():
    return make_walker('plain@dogboxx.org', is_admin=False, is_super_admin=False)


@pytest.fixture
def logged_in_super_admin(client, super_admin):
    login(client, super_admin.email)
    return client


@pytest.fixture
def logged_in_promoted_admin(client, promoted_admin):
    login(client, promoted_admin.email)
    return client


@pytest.fixture
def logged_in_plain_walker(client, plain_walker):
    login(client, plain_walker.email)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSuperAdminToggle:

    def test_super_admin_can_promote_plain_walker(self, app, logged_in_super_admin, plain_walker):
        resp = toggle(logged_in_super_admin, plain_walker.id)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['is_admin'] is True
        with app.app_context():
            assert User.query.get(plain_walker.id).is_admin is True

    def test_super_admin_can_demote_promoted_walker(self, app, logged_in_super_admin, promoted_admin):
        resp = toggle(logged_in_super_admin, promoted_admin.id)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['is_admin'] is False
        with app.app_context():
            assert User.query.get(promoted_admin.id).is_admin is False

    def test_promoted_admin_cannot_toggle(self, app, logged_in_promoted_admin, plain_walker):
        resp = toggle(logged_in_promoted_admin, plain_walker.id)
        assert resp.status_code == 403
        data = resp.get_json()
        assert data['success'] is False
        # Target should be unchanged
        with app.app_context():
            assert User.query.get(plain_walker.id).is_admin is False

    def test_plain_walker_cannot_toggle(self, app, logged_in_plain_walker, promoted_admin):
        # Walker has no admin access at all — should be redirected (302) not 403
        resp = toggle(logged_in_plain_walker, promoted_admin.id)
        assert resp.status_code in (302, 403)

    def test_super_admin_cannot_toggle_self(self, app, logged_in_super_admin, super_admin):
        resp = toggle(logged_in_super_admin, super_admin.id)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['success'] is False

    def test_super_admin_target_is_refused(self, app, logged_in_super_admin, super_admin, plain_walker):
        # Create a second super-admin to try to demote
        second_owner = make_walker('owner2@dogboxx.org', is_admin=True, is_super_admin=True)
        resp = toggle(logged_in_super_admin, second_owner.id)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['success'] is False
        with app.app_context():
            assert User.query.get(second_owner.id).is_super_admin is True
            assert User.query.get(second_owner.id).is_admin is True
