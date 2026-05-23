"""
Tests for the admin broadcasts feature.

Covers:
- Recipient resolver: scope slot mapping (all / morning / afternoon),
  co-owner inclusion, status filtering, deduplication.
- /admin/broadcasts POST: writes notifications, sends batch email,
  audits a Broadcast row.
- /admin/broadcasts/preview JSON endpoint.
- Auth: non-admins are blocked.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Booking, Broadcast, Client, Dog, DogOwner, Notification, ServiceType, User
)
from app.utils.broadcasts import resolve_recipients


TRUNCATE_ORDER = [
    'broadcasts', 'booking_status_changes', 'bookings', 'notifications',
    'dog_owners', 'dogs', 'clients', 'walkers', 'service_types', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        for table in TRUNCATE_ORDER:
            db.session.execute(text(f'DELETE FROM {table}'))
        db.session.commit()
    yield


# ── helpers ────────────────────────────────────────────────────────────────

def _user(email, firstname='Test', role='client', is_admin=False):
    u = User(
        firstname=firstname, lastname='Person', email=email,
        role=role, is_admin=is_admin,
        hashed_password=generate_password_hash('Testpass1!'),
        active=True,
    )
    db.session.add(u)
    db.session.flush()
    return u


def _client(user_id):
    db.session.add(Client(user_id=user_id, onboarding_completed=True))
    db.session.flush()


def _dog(name='Buddy'):
    d = Dog(name=name, breed='Lab')
    db.session.add(d)
    db.session.flush()
    return d


def _own(dog_id, user_id, role='primary'):
    db.session.add(DogOwner(dog_id=dog_id, user_id=user_id, role=role))
    db.session.flush()


def _service_type():
    st = ServiceType(
        name='Group Walk', slug='group-walk',
        capacity_model='walker_assigned', slot_type='morning_afternoon',
        requires_walker=True, default_max_capacity=6, active=True,
    )
    db.session.add(st)
    db.session.flush()
    return st


def _booking(user_id, dog_id, service_type_id, date_, slot, status='confirmed'):
    b = Booking(
        user_id=user_id, dog_id=dog_id, service_type_id=service_type_id,
        date=date_, slot=slot, status=status,
    )
    db.session.add(b)
    db.session.flush()
    return b


def _login(flask_client, email):
    return flask_client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!',
    }, follow_redirects=True)


# ── Resolver ───────────────────────────────────────────────────────────────

class TestResolveRecipients:

    def test_returns_empty_when_no_bookings(self, app):
        with app.app_context():
            result = resolve_recipients(date.today(), 'all')
            assert result == []

    def test_morning_scope_matches_morning_half_day_am_and_full_day(self, app):
        with app.app_context():
            st = _service_type()
            d = date.today() + timedelta(days=1)
            # Three different clients, three slot types, all should match 'morning'
            u1 = _user('m@bcast-test.com'); _client(u1.id); dog1 = _dog('A'); _own(dog1.id, u1.id)
            u2 = _user('ha@bcast-test.com'); _client(u2.id); dog2 = _dog('B'); _own(dog2.id, u2.id)
            u3 = _user('fd@bcast-test.com'); _client(u3.id); dog3 = _dog('C'); _own(dog3.id, u3.id)
            _booking(u1.id, dog1.id, st.id, d, 'Morning')
            _booking(u2.id, dog2.id, st.id, d, 'Half Day AM')
            _booking(u3.id, dog3.id, st.id, d, 'Full Day')
            db.session.commit()

            recipients = resolve_recipients(d, 'morning')
            emails = {u.email for u, _ in recipients}
            assert emails == {'m@bcast-test.com', 'ha@bcast-test.com', 'fd@bcast-test.com'}

    def test_morning_scope_excludes_afternoon_only_bookings(self, app):
        with app.app_context():
            st = _service_type()
            d = date.today() + timedelta(days=2)
            u_am = _user('am@bcast-test.com'); _client(u_am.id); d_am = _dog('AM'); _own(d_am.id, u_am.id)
            u_pm = _user('pm@bcast-test.com'); _client(u_pm.id); d_pm = _dog('PM'); _own(d_pm.id, u_pm.id)
            _booking(u_am.id, d_am.id, st.id, d, 'Morning')
            _booking(u_pm.id, d_pm.id, st.id, d, 'Afternoon')
            db.session.commit()

            recipients = resolve_recipients(d, 'morning')
            emails = {u.email for u, _ in recipients}
            assert emails == {'am@bcast-test.com'}

    def test_afternoon_scope_matches_afternoon_half_day_pm_and_full_day(self, app):
        with app.app_context():
            st = _service_type()
            d = date.today() + timedelta(days=3)
            u1 = _user('a@bcast-test.com'); _client(u1.id); d1 = _dog('A'); _own(d1.id, u1.id)
            u2 = _user('hp@bcast-test.com'); _client(u2.id); d2 = _dog('B'); _own(d2.id, u2.id)
            u3 = _user('fd2@bcast-test.com'); _client(u3.id); d3 = _dog('C'); _own(d3.id, u3.id)
            u4 = _user('m2@bcast-test.com'); _client(u4.id); d4 = _dog('D'); _own(d4.id, u4.id)
            _booking(u1.id, d1.id, st.id, d, 'Afternoon')
            _booking(u2.id, d2.id, st.id, d, 'Half Day PM')
            _booking(u3.id, d3.id, st.id, d, 'Full Day')
            _booking(u4.id, d4.id, st.id, d, 'Morning')  # excluded
            db.session.commit()

            recipients = resolve_recipients(d, 'afternoon')
            emails = {u.email for u, _ in recipients}
            assert emails == {'a@bcast-test.com', 'hp@bcast-test.com', 'fd2@bcast-test.com'}

    def test_all_scope_matches_every_slot(self, app):
        with app.app_context():
            st = _service_type()
            d = date.today() + timedelta(days=4)
            u_am = _user('am@bcast-test.com'); _client(u_am.id); d_am = _dog('A'); _own(d_am.id, u_am.id)
            u_pm = _user('pm@bcast-test.com'); _client(u_pm.id); d_pm = _dog('B'); _own(d_pm.id, u_pm.id)
            _booking(u_am.id, d_am.id, st.id, d, 'Morning')
            _booking(u_pm.id, d_pm.id, st.id, d, 'Afternoon')
            db.session.commit()

            recipients = resolve_recipients(d, 'all')
            emails = {u.email for u, _ in recipients}
            assert emails == {'am@bcast-test.com', 'pm@bcast-test.com'}

    def test_excludes_cancelled_rejected_completed_waitlisted_requested(self, app):
        with app.app_context():
            st = _service_type()
            d = date.today() + timedelta(days=5)
            for status in ('cancelled', 'rejected', 'completed', 'waitlisted', 'requested'):
                u = _user(f'{status}@bcast-test.com')
                _client(u.id)
                dg = _dog(status)
                _own(dg.id, u.id)
                _booking(u.id, dg.id, st.id, d, 'Morning', status=status)
            db.session.commit()

            assert resolve_recipients(d, 'all') == []

    def test_includes_modified_status(self, app):
        with app.app_context():
            st = _service_type()
            d = date.today() + timedelta(days=6)
            u = _user('mod@bcast-test.com'); _client(u.id); dg = _dog('M'); _own(dg.id, u.id)
            _booking(u.id, dg.id, st.id, d, 'Morning', status='modified')
            db.session.commit()

            recipients = resolve_recipients(d, 'all')
            emails = {u.email for u, _ in recipients}
            assert emails == {'mod@bcast-test.com'}

    def test_includes_secondary_owners(self, app):
        with app.app_context():
            st = _service_type()
            d = date.today() + timedelta(days=7)
            primary = _user('primary@bcast-test.com'); _client(primary.id)
            secondary = _user('secondary@bcast-test.com'); _client(secondary.id)
            shared = _dog('Shared')
            _own(shared.id, primary.id, role='primary')
            _own(shared.id, secondary.id, role='secondary')
            _booking(primary.id, shared.id, st.id, d, 'Morning')
            db.session.commit()

            recipients = resolve_recipients(d, 'all')
            emails = {u.email for u, _ in recipients}
            assert emails == {'primary@bcast-test.com', 'secondary@bcast-test.com'}

    def test_deduplicates_client_with_multiple_bookings(self, app):
        with app.app_context():
            st = _service_type()
            d = date.today() + timedelta(days=8)
            u = _user('dup@bcast-test.com'); _client(u.id)
            d1 = _dog('First'); _own(d1.id, u.id)
            d2 = _dog('Second'); _own(d2.id, u.id)
            _booking(u.id, d1.id, st.id, d, 'Morning')
            _booking(u.id, d2.id, st.id, d, 'Afternoon')
            db.session.commit()

            recipients = resolve_recipients(d, 'all')
            assert len(recipients) == 1
            user, dogs = recipients[0]
            assert user.email == 'dup@bcast-test.com'
            dog_names = {d.name for d in dogs}
            assert dog_names == {'First', 'Second'}

    def test_excludes_inactive_users(self, app):
        with app.app_context():
            st = _service_type()
            d = date.today() + timedelta(days=9)
            u = _user('inactive@bcast-test.com'); _client(u.id)
            u.active = False
            dg = _dog('X'); _own(dg.id, u.id)
            _booking(u.id, dg.id, st.id, d, 'Morning')
            db.session.commit()

            assert resolve_recipients(d, 'all') == []

    def test_invalid_scope_raises(self, app):
        with app.app_context():
            with pytest.raises(ValueError):
                resolve_recipients(date.today(), 'evening')


# ── Route: POST /admin/broadcasts ──────────────────────────────────────────

@pytest.fixture
def captured_broadcasts(monkeypatch):
    """Capture send_broadcast_batch calls instead of hitting Resend."""
    sent = []

    def fake_send(subject, body_text, recipients):
        sent.append({'subject': subject, 'body': body_text, 'recipients': recipients})
        return {'sent': len(recipients), 'failed': 0}

    monkeypatch.setattr('app.utils.email.send_broadcast_batch', fake_send)
    return sent


class TestBroadcastSend:

    def test_send_creates_notifications_email_and_audit_row(
            self, app, client, captured_broadcasts):
        with app.app_context():
            admin = _user('admin@b-bcast.test.com', firstname='Admin', role='walker', is_admin=True)
            st = _service_type()
            d = date.today() + timedelta(days=1)
            c1 = _user('c1@b-bcast.test.com', firstname='Alice'); _client(c1.id)
            dog1 = _dog('Daisy'); _own(dog1.id, c1.id)
            _booking(c1.id, dog1.id, st.id, d, 'Morning')
            db.session.commit()
            admin_email = admin.email
            scope_date_iso = d.isoformat()
            c1_id = c1.id

        _login(client, admin_email)
        resp = client.post('/admin/broadcasts', data={
            'scope_date': scope_date_iso,
            'scope_slot': 'all',
            'subject': 'Weather alert',
            'body': 'Walks cancelled — heavy rain.',
            'channel_bell': 'on',
            'channel_email': 'on',
        }, follow_redirects=False)
        assert resp.status_code == 302

        with app.app_context():
            notifs = Notification.query.filter_by(recipient_id=c1_id).all()
            assert len(notifs) == 1
            assert notifs[0].title == 'Weather alert'
            assert notifs[0].body == 'Walks cancelled — heavy rain.'
            assert notifs[0].link is None

            broadcasts = Broadcast.query.all()
            assert len(broadcasts) == 1
            b = broadcasts[0]
            assert b.recipient_count == 1
            assert b.bell_sent is True
            assert b.email_sent is True
            assert b.scope_slot == 'all'

        assert len(captured_broadcasts) == 1
        assert captured_broadcasts[0]['subject'] == 'Weather alert'
        assert captured_broadcasts[0]['recipients'][0]['email'] == 'c1@b-bcast.test.com'

    def test_send_bell_only_skips_email_batch(
            self, app, client, captured_broadcasts):
        with app.app_context():
            admin = _user('admin@b2-bcast.test.com', role='walker', is_admin=True)
            st = _service_type()
            d = date.today() + timedelta(days=1)
            c1 = _user('c@b2-bcast.test.com'); _client(c1.id)
            dog1 = _dog('Dog'); _own(dog1.id, c1.id)
            _booking(c1.id, dog1.id, st.id, d, 'Morning')
            db.session.commit()
            admin_email = admin.email
            iso = d.isoformat()

        _login(client, admin_email)
        client.post('/admin/broadcasts', data={
            'scope_date': iso, 'scope_slot': 'all',
            'subject': 'X', 'body': 'Y', 'channel_bell': 'on',
        })
        # email batch never called
        assert captured_broadcasts == []
        with app.app_context():
            b = Broadcast.query.one()
            assert b.email_sent is False
            assert b.bell_sent is True

    def test_send_rejects_no_recipients(self, app, client, captured_broadcasts):
        with app.app_context():
            admin = _user('admin@b3-bcast.test.com', role='walker', is_admin=True)
            db.session.commit()
            admin_email = admin.email

        _login(client, admin_email)
        resp = client.post('/admin/broadcasts', data={
            'scope_date': (date.today() + timedelta(days=10)).isoformat(),
            'scope_slot': 'all',
            'subject': 'X', 'body': 'Y',
            'channel_bell': 'on', 'channel_email': 'on',
        })
        assert resp.status_code == 200  # re-renders form with error
        with app.app_context():
            assert Broadcast.query.count() == 0
            assert Notification.query.count() == 0
        assert captured_broadcasts == []

    def test_send_rejects_no_channel(self, app, client, captured_broadcasts):
        with app.app_context():
            admin = _user('admin@b4-bcast.test.com', role='walker', is_admin=True)
            st = _service_type()
            d = date.today() + timedelta(days=1)
            c1 = _user('c@b4-bcast.test.com'); _client(c1.id)
            dog1 = _dog('D'); _own(dog1.id, c1.id)
            _booking(c1.id, dog1.id, st.id, d, 'Morning')
            db.session.commit()
            admin_email = admin.email
            iso = d.isoformat()

        _login(client, admin_email)
        resp = client.post('/admin/broadcasts', data={
            'scope_date': iso, 'scope_slot': 'all',
            'subject': 'X', 'body': 'Y',
            # neither channel checked
        })
        assert resp.status_code == 200
        with app.app_context():
            assert Broadcast.query.count() == 0

    def test_non_admin_blocked(self, app, client):
        with app.app_context():
            c1 = _user('client@b5-bcast.test.com'); _client(c1.id)
            db.session.commit()
            email = c1.email

        _login(client, email)
        resp = client.get('/admin/broadcasts', follow_redirects=False)
        # admin_required decorator redirects or 403s
        assert resp.status_code in (302, 403)


# ── Route: GET /admin/broadcasts/preview ───────────────────────────────────

class TestBroadcastPreview:

    def test_preview_returns_json_recipients(self, app, client):
        with app.app_context():
            admin = _user('admin@p-bcast.test.com', role='walker', is_admin=True)
            st = _service_type()
            d = date.today() + timedelta(days=1)
            c1 = _user('c1@p-bcast.test.com', firstname='Alice'); _client(c1.id)
            dog1 = _dog('Daisy'); _own(dog1.id, c1.id)
            _booking(c1.id, dog1.id, st.id, d, 'Morning')
            db.session.commit()
            admin_email = admin.email
            iso = d.isoformat()

        _login(client, admin_email)
        resp = client.get(f'/admin/broadcasts/preview?scope_date={iso}&scope_slot=morning')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['count'] == 1
        assert data['recipients'][0]['email'] == 'c1@p-bcast.test.com'
        assert data['recipients'][0]['dogs'] == ['Daisy']

    def test_preview_rejects_invalid_date(self, app, client):
        with app.app_context():
            admin = _user('admin@p2-bcast.test.com', role='walker', is_admin=True)
            db.session.commit()
            admin_email = admin.email

        _login(client, admin_email)
        resp = client.get('/admin/broadcasts/preview?scope_date=notadate&scope_slot=all')
        assert resp.status_code == 400

    def test_preview_rejects_invalid_scope(self, app, client):
        with app.app_context():
            admin = _user('admin@p3-bcast.test.com', role='walker', is_admin=True)
            db.session.commit()
            admin_email = admin.email

        _login(client, admin_email)
        iso = date.today().isoformat()
        resp = client.get(f'/admin/broadcasts/preview?scope_date={iso}&scope_slot=evening')
        assert resp.status_code == 400


# ── GET /admin/broadcasts deep-link prefill ───────────────────────────────

class TestBroadcastDeepLink:

    def test_prefill_from_query_params(self, app, client):
        with app.app_context():
            admin = _user('admin@dl-bcast.test.com', role='walker', is_admin=True)
            db.session.commit()
            admin_email = admin.email

        _login(client, admin_email)
        target = (date.today() + timedelta(days=2)).isoformat()
        resp = client.get(
            f'/admin/broadcasts?scope_date={target}&scope_slot=morning'
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # The composer's date input gets the prefilled value
        assert f'value="{target}"' in html
        # The "morning" radio is the one marked checked
        assert 'id="scope_morning"' in html
        # Find the morning radio's snippet and confirm it's checked
        morning_idx = html.find('id="scope_morning"')
        assert morning_idx > 0
        snippet = html[max(0, morning_idx - 50):morning_idx + 300]
        assert 'checked' in snippet

    def test_bad_params_fall_back_to_defaults(self, app, client):
        """Invalid query params should not 400 — the page is interactive,
        the admin can fix the scope inline. Falls back to today + all."""
        with app.app_context():
            admin = _user('admin@dl2-bcast.test.com', role='walker', is_admin=True)
            db.session.commit()
            admin_email = admin.email

        _login(client, admin_email)
        resp = client.get(
            '/admin/broadcasts?scope_date=notadate&scope_slot=evening'
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Falls back to today
        assert f'value="{date.today().isoformat()}"' in html
        # Falls back to scope_all
        all_idx = html.find('id="scope_all"')
        snippet = html[max(0, all_idx - 50):all_idx + 300]
        assert 'checked' in snippet
