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
            assert db.session.get(User, plain_walker.id).is_admin is True

    def test_super_admin_can_demote_promoted_walker(self, app, logged_in_super_admin, promoted_admin):
        resp = toggle(logged_in_super_admin, promoted_admin.id)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['is_admin'] is False
        with app.app_context():
            assert db.session.get(User, promoted_admin.id).is_admin is False

    def test_promoted_admin_cannot_toggle(self, app, logged_in_promoted_admin, plain_walker):
        resp = toggle(logged_in_promoted_admin, plain_walker.id)
        assert resp.status_code == 403
        data = resp.get_json()
        assert data['success'] is False
        # Target should be unchanged
        with app.app_context():
            assert db.session.get(User, plain_walker.id).is_admin is False

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
            assert db.session.get(User, second_owner.id).is_super_admin is True
            assert db.session.get(User, second_owner.id).is_admin is True


class TestAdminWalkersListRendering:

    def test_action_buttons_survive_apostrophe_in_name(self, app, client, super_admin):
        """Regression: the deactivate/activate/remove-role buttons used to
        interpolate the walker's name straight into an inline onclick JS
        string (confirmWalkerToggle(1, 'deactivate', '{{ name|e }}')).
        HTML-attribute escaping (|e) isn't JS-string escaping — a name like
        O'Brien survives |e as O&#39;Brien, which the browser HTML-decodes
        back to a literal apostrophe before treating the attribute as JS
        source, closing the string early. Buttons must pass the name via a
        data-* attribute instead, read off `this` in the click handler.

        The onclick attribute was later dropped entirely in favour of that
        same data-*-driven click handler, now wired up via event delegation
        rather than an inline attribute (FEATURES.md #68 — migrating admin
        templates off script-src-attr: 'unsafe-inline'). The data-* attribute
        is still the load-bearing part of this fix; the assertions below
        just track where that fix currently lives."""
        walker = make_walker('obrien.walker@dogboxx.org')
        walker.lastname = "O'Brien"
        db.session.commit()
        walker_id = walker.id

        login(client, super_admin.email)
        resp = client.get('/admin/walkers')
        html = resp.data.decode()

        assert "O&#39;Brien" in html  # name survives HTML-attribute escaping intact
        assert 'js-confirm-walker-toggle' in html
        assert 'onclick="confirmWalkerToggle(this)"' not in html  # migrated to event delegation (FEATURES.md #68)
        assert f"confirmWalkerToggle({walker_id}," not in html  # old vulnerable positional-arg call

    def test_dashboard_walker_override_button_builds_no_inline_onclick(self, app, client, super_admin, service_type):
        """Regression: the dashboard calendar's walker-override gear button
        (admin.html) had the exact same bug class as
        test_action_buttons_survive_apostrophe_in_name above — just missed
        by the PR #187 audit since that audit only looked at Jinja
        templates, and this button is rendered by client-side JS instead.
        It built its onclick via a JS template literal,
        `onclick="_openOverrideModal(id, '${esc(w.name)}')"`, where esc()
        only HTML-escapes (&, <, >, "), never an apostrophe — a walker named
        O'Brien would break the JS string mid-attribute.

        A Flask test client can't execute renderDetail()/esc() to check the
        resulting DOM the way the Jinja-rendered tests above check rendered
        HTML directly, so this instead asserts the vulnerable *shape* is
        gone from the shipped JS source: no admin.html script builds an
        onclick="..." attribute string by concatenating untrusted data, and
        the gear button carries the walker's name via data-walker-name
        instead. Also spot-checks that the name reaches the browser safely
        even through the earlier server-side hop: Jinja's tojson unicode-
        escapes the apostrophe (\\u0027) for safe embedding inside the
        <script> tag's JSON payload — a different mechanism than the |e
        HTML-attribute escaping the other tests check, and not the bug this
        migration fixed, but worth confirming it still holds."""
        walker = make_walker('obrien.dashboard@dogboxx.org')
        walker.firstname = "O'Brien"  # the dashboard displays w.user.firstname, not lastname
        db.session.commit()

        login(client, super_admin.email)
        resp = client.get('/admin/')
        html = resp.data.decode()

        assert "O\\u0027Brien" in html  # name survives tojson's script-context escaping intact
        assert 'detail-walker-gear' in html
        assert 'data-walker-name' in html  # the replacement mechanism is present
        assert 'onclick="' not in html  # no admin.html script builds an onclick attribute string at all
