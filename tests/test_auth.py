"""
T4 — Auth and access control tests.

Covers:
- Unauthenticated access to protected routes → 302 redirect to login
- Client cannot access admin routes
- Walker (non-admin) cannot access admin routes
- Admin can access admin routes
- Walker can access walker routes; client cannot
- Client can access client routes; walker cannot
"""
import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import User, Client, Walker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(email, role='client', is_admin=False):
    u = User(
        firstname='Test', lastname='User',
        email=email, role=role, is_admin=is_admin,
        hashed_password=generate_password_hash('Testpass1!'),
        active=True,
    )
    db.session.add(u)
    db.session.flush()
    return u


def make_client_profile(user_id):
    c = Client(user_id=user_id, onboarding_completed=True)
    db.session.add(c)
    db.session.flush()
    return c


def make_walker_profile(user_id):
    w = Walker(user_id=user_id)
    db.session.add(w)
    db.session.flush()
    return w


def login(flask_client, email, password='Testpass1!'):
    return flask_client.post('/auth/login', data={
        'email': email,
        'password': password,
    }, follow_redirects=True)


# ---------------------------------------------------------------------------
# Representative protected routes to probe
# ---------------------------------------------------------------------------

ADMIN_ROUTES = [
    '/admin/',
    '/admin/clients',
    '/admin/dogs',
    '/admin/walkers',
    '/admin/board',
    '/admin/revenue',
]

WALKER_ROUTES = [
    '/walker/pickups',
    '/walker/schedule',
]

CLIENT_ROUTES = [
    '/',          # client home / booking page
    '/profile',
]


# ---------------------------------------------------------------------------
# T4a — Unauthenticated access
# ---------------------------------------------------------------------------

class TestUnauthenticatedAccess:
    """All protected routes must redirect an anonymous user to /auth/login."""

    @pytest.mark.parametrize('url', ADMIN_ROUTES + WALKER_ROUTES + CLIENT_ROUTES)
    def test_redirects_to_login(self, client, url):
        resp = client.get(url)
        # Either a direct 302 or a redirect chain landing on the login page
        assert resp.status_code in (302, 301) or b'login' in resp.data.lower(), (
            f"{url} did not redirect unauthenticated user (status {resp.status_code})"
        )


# ---------------------------------------------------------------------------
# T4b — Client access
# ---------------------------------------------------------------------------

class TestClientAccess:
    """Clients can reach client routes but are blocked from admin/walker routes."""

    @pytest.fixture(autouse=True)
    def setup(self, app, client):
        with app.app_context():
            user = make_user('client_ac@test.com', role='client')
            make_client_profile(user.id)
            db.session.commit()
        login(client, 'client_ac@test.com')
        self.client = client

    @pytest.mark.parametrize('url', CLIENT_ROUTES)
    def test_can_reach_client_routes(self, url):
        resp = self.client.get(url, follow_redirects=True)
        assert resp.status_code == 200

    @pytest.mark.parametrize('url', ADMIN_ROUTES)
    def test_blocked_from_admin_routes(self, url):
        # Don't follow redirects — decorators issue a 302 redirect away from admin
        resp = self.client.get(url)
        assert resp.status_code in (403, 302, 301), (
            f"Client reached admin route {url} unblocked (status {resp.status_code})"
        )

    @pytest.mark.parametrize('url', WALKER_ROUTES)
    def test_blocked_from_walker_routes(self, url):
        resp = self.client.get(url)
        assert resp.status_code in (403, 302, 301), (
            f"Client reached walker route {url} unblocked (status {resp.status_code})"
        )


# ---------------------------------------------------------------------------
# T4c — Walker (non-admin) access
# ---------------------------------------------------------------------------

class TestWalkerAccess:
    """Walkers can reach walker routes but are blocked from admin routes."""

    @pytest.fixture(autouse=True)
    def setup(self, app, client):
        with app.app_context():
            user = make_user('walker_ac@test.com', role='walker', is_admin=False)
            make_walker_profile(user.id)
            db.session.commit()
        login(client, 'walker_ac@test.com')
        self.client = client

    @pytest.mark.parametrize('url', WALKER_ROUTES)
    def test_can_reach_walker_routes(self, url):
        resp = self.client.get(url, follow_redirects=True)
        assert resp.status_code == 200

    @pytest.mark.parametrize('url', ADMIN_ROUTES)
    def test_blocked_from_admin_routes(self, url):
        resp = self.client.get(url)
        assert resp.status_code in (403, 302, 301), (
            f"Walker reached admin route {url} unblocked (status {resp.status_code})"
        )


# ---------------------------------------------------------------------------
# T4d — Admin access
# ---------------------------------------------------------------------------

class TestAdminAccess:
    """Admins (is_admin=True) can reach all admin routes."""

    @pytest.fixture(autouse=True)
    def setup(self, app, client):
        with app.app_context():
            user = make_user('admin_ac@test.com', role='walker', is_admin=True)
            make_walker_profile(user.id)
            db.session.commit()
        login(client, 'admin_ac@test.com')
        self.client = client

    @pytest.mark.parametrize('url', ADMIN_ROUTES)
    def test_can_reach_admin_routes(self, url):
        resp = self.client.get(url, follow_redirects=True)
        assert resp.status_code == 200, (
            f"Admin blocked from {url} (status {resp.status_code})"
        )


# ---------------------------------------------------------------------------
# T4e — Login / logout flow
# ---------------------------------------------------------------------------

class TestLoginLogout:

    def test_valid_login_redirects(self, app, client):
        with app.app_context():
            user = make_user('login_test@test.com', role='client')
            make_client_profile(user.id)
            db.session.commit()

        resp = client.post('/auth/login', data={
            'email': 'login_test@test.com',
            'password': 'Testpass1!',
        })
        assert resp.status_code in (302, 200)

    def test_wrong_password_stays_on_login(self, app, client):
        with app.app_context():
            make_user('badpass@test.com', role='client')
            db.session.commit()

        resp = client.post('/auth/login', data={
            'email': 'badpass@test.com',
            'password': 'WrongPassword!',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'login' in resp.data.lower() or b'invalid' in resp.data.lower() or b'incorrect' in resp.data.lower()

    def test_logout_ends_session(self, app, client):
        with app.app_context():
            user = make_user('logout_test@test.com', role='client')
            make_client_profile(user.id)
            db.session.commit()

        login(client, 'logout_test@test.com')
        client.post('/auth/logout', follow_redirects=True)

        # After logout, protected route should redirect
        resp = client.get('/profile')
        assert resp.status_code in (302, 301)

    def test_logout_rejects_get(self, app, client):
        """Regression: /auth/logout must reject GET so a cross-origin <img>
        or link can't force-logout a logged-in user (CSRF)."""
        with app.app_context():
            user = make_user('logout_get@test.com', role='client')
            make_client_profile(user.id)
            db.session.commit()

        login(client, 'logout_get@test.com')
        resp = client.get('/auth/logout')
        assert resp.status_code == 405  # Method Not Allowed

        # Session is still active — protected route renders, doesn't redirect to login
        resp = client.get('/profile', follow_redirects=False)
        assert resp.status_code == 200
