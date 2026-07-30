"""
Tests for /admin/closures — Session 3 (§7.4) + date-range closures (#169).

Verifies that creating a closure notifies co-owners and the assigned walker
in addition to the primary booking owner, and that date-range closures fan
out cancellations/notifications correctly across every date in the range.
"""
import json
import datetime

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    User, Client, Walker, Dog, DogOwner, Booking, ServiceType, Notification, Closure,
)


def _next_weekday(target_dow):
    d = datetime.date.today() + datetime.timedelta(days=1)
    while d.weekday() != target_dow:
        d += datetime.timedelta(days=1)
    return d


def _make_user(email, role='client', is_admin=False):
    u = User(firstname='Test', lastname='User', email=email, role=role,
             is_admin=is_admin, hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.commit()
    return u


def _login(flask_client, email):
    return flask_client.post('/auth/login',
                             data={'email': email, 'password': 'Testpass1!'},
                             follow_redirects=True)


def _post_closure(flask_client, date_str, reason=None):
    return flask_client.post(
        '/admin/closures',
        data=json.dumps({'date': date_str, 'reason': reason}),
        content_type='application/json',
    )


def _post_closure_range(flask_client, start_str, end_str, reason=None):
    return flask_client.post(
        '/admin/closures',
        data=json.dumps({'start_date': start_str, 'end_date': end_str, 'reason': reason}),
        content_type='application/json',
    )


class TestClosureFanOut:
    """Closure cancellations must reach co-owners and the assigned walker."""

    def _setup(self, app):
        """Create: admin, primary owner, co-owner, walker, dog, service, booking."""
        monday = _next_weekday(0)
        with app.app_context():
            admin = _make_user('closure_admin@test.com', role='walker', is_admin=True)
            primary = _make_user('closure_primary@test.com', role='client')
            co_owner = _make_user('closure_coowner@test.com', role='client')
            walker_u = _make_user('closure_walker@test.com', role='walker')
            walker = Walker(user_id=walker_u.id)
            db.session.add(walker); db.session.flush()
            db.session.add(Client(user_id=primary.id, onboarding_completed=True))
            db.session.add(Client(user_id=co_owner.id, onboarding_completed=True))
            st = ServiceType(
                name='Group Walk', slug='group-walk',
                capacity_model='walker_assigned', slot_type='morning_afternoon',
                requires_walker=True, default_max_capacity=6, active=True,
            )
            db.session.add(st); db.session.flush()
            dog = Dog(name='Luna', breed='Spaniel')
            db.session.add(dog); db.session.flush()
            db.session.add(DogOwner(dog_id=dog.id, user_id=primary.id, role='primary'))
            db.session.add(DogOwner(dog_id=dog.id, user_id=co_owner.id, role='secondary'))
            booking = Booking(
                user_id=primary.id, dog_id=dog.id, service_type_id=st.id,
                date=monday, slot='Morning', status='confirmed',
                walker_id=walker.id,
            )
            db.session.add(booking); db.session.commit()
            return {
                'monday': monday,
                'admin_email': admin.email,
                'primary_id': primary.id,
                'co_owner_id': co_owner.id,
                'walker_uid': walker_u.id,
            }

    def test_closure_notifies_primary_owner(self, app, client):
        ids = self._setup(app)
        _login(client, ids['admin_email'])
        resp = _post_closure(client, ids['monday'].isoformat())
        assert resp.get_json()['cancelled_count'] == 1

        with app.app_context():
            notifs = Notification.query.filter_by(recipient_id=ids['primary_id']).all()
            assert len(notifs) == 1
            assert notifs[0].notification_type == 'booking_cancelled'

    def test_closure_notifies_co_owner(self, app, client):
        """Co-owner must also receive a booking_cancelled notification (§7.4)."""
        ids = self._setup(app)
        _login(client, ids['admin_email'])
        _post_closure(client, ids['monday'].isoformat())

        with app.app_context():
            notifs = Notification.query.filter_by(recipient_id=ids['co_owner_id']).all()
            assert len(notifs) == 1, "co-owner must get exactly one notification"
            assert notifs[0].notification_type == 'booking_cancelled'
            assert 'Luna' in notifs[0].title or 'cancelled' in notifs[0].title

    def test_closure_notifies_assigned_walker(self, app, client):
        """Assigned walker must receive a booking_cancelled notification (§7.4)."""
        ids = self._setup(app)
        _login(client, ids['admin_email'])
        _post_closure(client, ids['monday'].isoformat())

        with app.app_context():
            notifs = Notification.query.filter_by(recipient_id=ids['walker_uid']).all()
            assert len(notifs) == 1, "walker must get exactly one notification"
            assert notifs[0].notification_type == 'booking_cancelled'

    def test_closure_does_not_double_notify_admin_as_walker(self, app, client):
        """If the acting admin is also the assigned walker, they must NOT get a
        walker notification — they already know because they created the closure."""
        monday = _next_weekday(0)
        with app.app_context():
            # Admin IS the walker
            admin_u = _make_user('closure_adminwalker@test.com', role='walker', is_admin=True)
            admin_walker = Walker(user_id=admin_u.id)
            db.session.add(admin_walker); db.session.flush()
            primary = _make_user('closure_p2@test.com', role='client')
            db.session.add(Client(user_id=primary.id, onboarding_completed=True))
            st = ServiceType(
                name='Group Walk', slug='group-walk',
                capacity_model='walker_assigned', slot_type='morning_afternoon',
                requires_walker=True, default_max_capacity=6, active=True,
            )
            db.session.add(st); db.session.flush()
            dog = Dog(name='Rex', breed='Terrier')
            db.session.add(dog); db.session.flush()
            db.session.add(DogOwner(dog_id=dog.id, user_id=primary.id, role='primary'))
            booking = Booking(
                user_id=primary.id, dog_id=dog.id, service_type_id=st.id,
                date=monday, slot='Morning', status='confirmed',
                walker_id=admin_walker.id,
            )
            db.session.add(booking); db.session.commit()
            admin_email = admin_u.email
            admin_uid = admin_u.id

        _login(client, admin_email)
        _post_closure(client, monday.isoformat())

        with app.app_context():
            # Admin/walker should get zero notifications (they created the closure)
            notifs = Notification.query.filter_by(recipient_id=admin_uid).all()
            assert len(notifs) == 0


class TestClosureDateRange:
    """Date-range closures (#169): one admin action spanning multiple days."""

    def _setup_range(self, app):
        monday = _next_weekday(0)
        days = [monday, monday + datetime.timedelta(days=1), monday + datetime.timedelta(days=2)]
        with app.app_context():
            admin = _make_user('range_admin@test.com', role='walker', is_admin=True)
            primary = _make_user('range_primary@test.com', role='client')
            db.session.add(Client(user_id=primary.id, onboarding_completed=True))
            st = ServiceType(
                name='Group Walk', slug='group-walk',
                capacity_model='walker_assigned', slot_type='morning_afternoon',
                requires_walker=True, default_max_capacity=6, active=True,
            )
            db.session.add(st); db.session.flush()
            dog = Dog(name='Milo', breed='Beagle')
            db.session.add(dog); db.session.flush()
            db.session.add(DogOwner(dog_id=dog.id, user_id=primary.id, role='primary'))
            for d in days:
                db.session.add(Booking(
                    user_id=primary.id, dog_id=dog.id, service_type_id=st.id,
                    date=d, slot='Morning', status='confirmed',
                ))
            db.session.commit()
            return {'days': days, 'admin_email': admin.email, 'primary_id': primary.id}

    def test_range_creates_one_closure_row_per_date(self, app, client):
        ids = self._setup_range(app)
        _login(client, ids['admin_email'])
        resp = _post_closure_range(client, ids['days'][0].isoformat(), ids['days'][-1].isoformat(),
                                    reason='Christmas')
        data = resp.get_json()
        assert data['success'] is True
        assert data['created_days'] == 3
        assert data['cancelled_count'] == 3

        with app.app_context():
            closures = Closure.query.filter(Closure.date.in_(ids['days'])).all()
            assert len(closures) == 3
            range_ids = {c.range_id for c in closures}
            assert len(range_ids) == 1, "all rows from one range action must share a range_id"

            bookings = Booking.query.filter(Booking.date.in_(ids['days'])).all()
            assert all(b.status == 'cancelled' for b in bookings)

    def test_range_sends_one_grouped_notification_per_client(self, app, client):
        """A client with 3 cancelled bookings across the range gets ONE notification,
        not three (NotificationBatch groups by (recipient_id, kind) across dates)."""
        ids = self._setup_range(app)
        _login(client, ids['admin_email'])
        _post_closure_range(client, ids['days'][0].isoformat(), ids['days'][-1].isoformat())

        with app.app_context():
            notifs = Notification.query.filter_by(recipient_id=ids['primary_id']).all()
            assert len(notifs) == 1
            assert notifs[0].notification_type == 'booking_cancelled'

    def test_range_partial_conflict_skips_existing_date_and_creates_rest(self, app, client):
        """If the middle day already has a closure, the range action must skip it
        and still close the remaining (non-conflicting) dates."""
        ids = self._setup_range(app)
        middle_day = ids['days'][1]
        with app.app_context():
            admin = User.query.filter_by(email=ids['admin_email']).first()
            db.session.add(Closure(date=middle_day, reason='Pre-existing',
                                    created_by_id=admin.id, range_id='preexisting'))
            db.session.commit()

        _login(client, ids['admin_email'])
        resp = _post_closure_range(client, ids['days'][0].isoformat(), ids['days'][-1].isoformat(),
                                    reason='Christmas')
        data = resp.get_json()
        assert data['success'] is True
        assert data['created_days'] == 2
        assert data['skipped_dates'] == [middle_day.isoformat()]
        # Only the 2 newly-closed dates' bookings get cancelled by this action —
        # the conflicting date's booking was already handled by its own closure.
        assert data['cancelled_count'] == 2

        with app.app_context():
            middle_booking = Booking.query.filter_by(date=middle_day).first()
            assert middle_booking.status == 'confirmed'

    def test_range_all_dates_conflicting_rejects(self, app, client):
        ids = self._setup_range(app)
        with app.app_context():
            admin = User.query.filter_by(email=ids['admin_email']).first()
            for d in ids['days']:
                db.session.add(Closure(date=d, reason=None, created_by_id=admin.id,
                                        range_id=f'preexisting-{d.isoformat()}'))
            db.session.commit()

        _login(client, ids['admin_email'])
        resp = _post_closure_range(client, ids['days'][0].isoformat(), ids['days'][-1].isoformat())
        assert resp.status_code == 400
        assert resp.get_json()['success'] is False

    def test_delete_range_removes_all_dates(self, app, client):
        ids = self._setup_range(app)
        _login(client, ids['admin_email'])
        _post_closure_range(client, ids['days'][0].isoformat(), ids['days'][-1].isoformat())

        with app.app_context():
            range_id = Closure.query.filter(Closure.date.in_(ids['days'])).first().range_id

        resp = client.delete(f'/admin/closures/range/{range_id}')
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

        with app.app_context():
            assert Closure.query.filter(Closure.date.in_(ids['days'])).count() == 0

    def test_end_before_start_rejected(self, app, client):
        ids = self._setup_range(app)
        _login(client, ids['admin_email'])
        resp = _post_closure_range(client, ids['days'][-1].isoformat(), ids['days'][0].isoformat())
        assert resp.status_code == 400

    def test_legacy_single_date_payload_still_works(self, app, client):
        """The old {'date': ...} payload shape (no start_date/end_date) must
        keep working unchanged for a single-day closure."""
        ids = self._setup_range(app)
        _login(client, ids['admin_email'])
        resp = _post_closure(client, ids['days'][0].isoformat())
        data = resp.get_json()
        assert data['success'] is True
        assert data['created_days'] == 1
        assert data['cancelled_count'] == 1

    def test_range_list_page_renders_one_collapsed_row(self, app, client):
        """GET /admin/closures after a range creation must render ONE row
        spanning the dates, not one row per date (grouped by range_id)."""
        ids = self._setup_range(app)
        _login(client, ids['admin_email'])
        _post_closure_range(client, ids['days'][0].isoformat(), ids['days'][-1].isoformat(),
                             reason='Christmas')

        resp = client.get('/admin/closures')
        assert resp.status_code == 200
        html = resp.data.decode()
        # Exactly one <tr id="closure-row-..."> for this closure (not 3 separate
        # rows, one per date). Match the id attribute, not the bare substring —
        # the page's own JS also references "closure-row-" as a template literal.
        row_count = html.count('id="closure-row-')
        assert row_count == 1, f"expected 1 collapsed row, found {row_count}"
        assert '3 days' in html
