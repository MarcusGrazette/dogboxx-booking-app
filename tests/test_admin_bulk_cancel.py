"""
Tests for /admin/dogs/<dog_id>/cancel-preview and /bulk-cancel.

Covers the day-of-week filter added on top of the existing range + slot
filters. Empty/missing `days` = no filter (backward compat with any caller
that doesn't send the field).

Important: conftest fixtures (client_user, dog, service_type) only flush —
they don't commit. On Postgres, opening a nested `with app.app_context():`
gives a fresh session that can't see the uncommitted parents, and FK
checks fail. So we operate inside the autouse `db` fixture's context and
let `_seed_bookings` commit the whole graph in one transaction.
"""
import datetime
import json

from werkzeug.security import generate_password_hash

from app import db
from app.models import Booking, BookingStatusChange, Walker, WalkerSchedule, Notification


def _next_weekday(target_dow, after=None):
    """Nearest future date whose weekday() == target_dow (0=Mon)."""
    d = (after or datetime.date.today()) + datetime.timedelta(days=1)
    while d.weekday() != target_dow:
        d += datetime.timedelta(days=1)
    return d


def _seed_bookings(user, dog, service_type, dates, slot='Morning'):
    """Create requested bookings for the given dates. Returns list of IDs.

    Commits the whole session — so the flushed-but-uncommitted fixture
    rows (user, dog, dog_owner, service_type) are persisted together
    with the new booking, satisfying Postgres FK constraints."""
    ids = []
    for d in dates:
        b = Booking(
            user_id=user.id,
            dog_id=dog.id,
            service_type_id=service_type.id,
            date=d,
            slot=slot,
            status='requested',
        )
        db.session.add(b)
        db.session.flush()
        ids.append(b.id)
    db.session.commit()
    return ids


class TestCancelPreviewDayFilter:
    """GET /admin/dogs/<id>/cancel-preview?days=…"""

    def test_filter_narrows_to_selected_weekday(self, client_user, dog, service_type,
                                                 logged_in_admin):
        tue = _next_weekday(1)  # Tuesday
        wed = _next_weekday(2)  # Wednesday
        _seed_bookings(client_user, dog, service_type, [tue, wed])
        start = min(tue, wed).isoformat()
        end   = (tue + datetime.timedelta(days=14)).isoformat()

        # Only Tuesdays (weekday=1)
        resp = logged_in_admin.get(
            f'/admin/dogs/{dog.id}/cancel-preview'
            f'?start={start}&end={end}&days=1'
        )
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data['success'] is True
        # Both seeded bookings sit in the range; only Tuesday should match.
        assert data['count'] == 1

    def test_no_days_param_returns_all_days(self, client_user, dog, service_type,
                                             logged_in_admin):
        """Regression guard: existing callers don't send `days` and must keep working."""
        tue = _next_weekday(1)
        wed = _next_weekday(2)
        _seed_bookings(client_user, dog, service_type, [tue, wed])
        start = min(tue, wed).isoformat()
        end   = (tue + datetime.timedelta(days=14)).isoformat()

        resp = logged_in_admin.get(
            f'/admin/dogs/{dog.id}/cancel-preview?start={start}&end={end}'
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['count'] == 2

    def test_invalid_day_values_ignored_not_400(self, client_user, dog, service_type,
                                                  logged_in_admin):
        """Bogus values dropped silently, mirroring slot-filter behaviour."""
        tue = _next_weekday(1)
        _seed_bookings(client_user, dog, service_type, [tue])
        start = tue.isoformat()
        end   = (tue + datetime.timedelta(days=7)).isoformat()

        # 'foo' is non-int, 9 is out of 0..4 range — both ignored, leaving day=1.
        resp = logged_in_admin.get(
            f'/admin/dogs/{dog.id}/cancel-preview'
            f'?start={start}&end={end}&days=foo&days=9&days=1'
        )
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data['count'] == 1

    def test_day_and_slot_filter_combined(self, client_user, dog, service_type,
                                            logged_in_admin):
        tue = _next_weekday(1)
        # Two bookings on Tuesday: AM and PM
        _seed_bookings(client_user, dog, service_type, [tue], slot='Morning')
        _seed_bookings(client_user, dog, service_type, [tue], slot='Afternoon')
        start = tue.isoformat()
        end   = (tue + datetime.timedelta(days=7)).isoformat()

        resp = logged_in_admin.get(
            f'/admin/dogs/{dog.id}/cancel-preview'
            f'?start={start}&end={end}&days=1&slots=Morning'
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['count'] == 1
        assert data['bookings'][0]['slot'] == 'Morning'

    def test_preview_caps_serialised_list_at_ten(self, client_user, dog, service_type,
                                                 logged_in_admin):
        """count is the true total; the bookings list is capped at 10."""
        start_date = datetime.date.today() + datetime.timedelta(days=1)
        dates = [start_date + datetime.timedelta(days=i) for i in range(12)]
        _seed_bookings(client_user, dog, service_type, dates)

        resp = logged_in_admin.get(
            f'/admin/dogs/{dog.id}/cancel-preview'
            f'?start={dates[0].isoformat()}&end={dates[-1].isoformat()}'
        )
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data['count'] == 12          # true total, uncapped
        assert len(data['bookings']) == 10  # serialised list capped


class TestBulkCancelDayFilter:
    """POST /admin/dogs/<id>/bulk-cancel with the day filter."""

    def test_only_filtered_weekday_cancelled(self, client_user, dog, service_type,
                                              logged_in_admin):
        tue = _next_weekday(1)
        wed = _next_weekday(2)
        ids = _seed_bookings(client_user, dog, service_type, [tue, wed])
        start = min(tue, wed).isoformat()
        end   = (max(tue, wed) + datetime.timedelta(days=1)).isoformat()
        # ids[0] is always the tue booking, ids[1] always wed — seeded in that order.
        tue_id, wed_id = ids[0], ids[1]

        resp = logged_in_admin.post(
            f'/admin/dogs/{dog.id}/bulk-cancel',
            data=json.dumps({'start': start, 'end': end, 'days': [1]}),
            content_type='application/json',
        )
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data['cancelled_count'] == 1

        assert db.session.get(Booking, tue_id).status == 'cancelled'
        assert db.session.get(Booking, wed_id).status == 'requested'

    def test_all_five_days_equivalent_to_no_filter(self, client_user, dog, service_type,
                                                     logged_in_admin):
        """User-facing UX: all-checked = same effect as the old no-day-filter button."""
        tue = _next_weekday(1)
        wed = _next_weekday(2)
        _seed_bookings(client_user, dog, service_type, [tue, wed])
        start = min(tue, wed).isoformat()
        end   = (tue + datetime.timedelta(days=14)).isoformat()

        resp = logged_in_admin.post(
            f'/admin/dogs/{dog.id}/bulk-cancel',
            data=json.dumps({
                'start': start, 'end': end, 'days': [0, 1, 2, 3, 4],
            }),
            content_type='application/json',
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['cancelled_count'] == 2

    def test_bulk_cancel_writes_bsc_rows_with_admin_actor(self, client_user, dog,
                                                          service_type, admin_user,
                                                          logged_in_admin):
        """Session 1: each cancellation logs a BSC row attributed to the admin,
        all sharing one batch_id so the feed can cluster the action."""
        tue = _next_weekday(1)
        wed = _next_weekday(2)
        _seed_bookings(client_user, dog, service_type, [tue, wed])
        start = min(tue, wed).isoformat()
        end   = (tue + datetime.timedelta(days=14)).isoformat()
        admin_id = admin_user.id

        resp = logged_in_admin.post(
            f'/admin/dogs/{dog.id}/bulk-cancel',
            data=json.dumps({'start': start, 'end': end, 'days': [0, 1, 2, 3, 4]}),
            content_type='application/json',
        )
        assert resp.status_code == 200
        assert resp.get_json()['cancelled_count'] == 2

        rows = BookingStatusChange.query.all()
        assert len(rows) == 2
        assert all(r.to_status == 'cancelled' for r in rows)
        assert all(r.from_status == 'requested' for r in rows)
        assert all(r.changed_by_id == admin_id for r in rows)
        batch_ids = {r.batch_id for r in rows}
        assert len(batch_ids) == 1 and None not in batch_ids


class TestBulkCancelWalkerNotification:
    """Session 3 (§7.4): assigned walkers must be notified when their bookings
    are bulk-cancelled."""

    def _make_walker(self, email='walker_bc@test.com'):
        from app.models import User as _User
        u = _User(firstname='Walker', lastname='BC', email=email, role='walker',
                  hashed_password=generate_password_hash('Testpass1!'))
        db.session.add(u); db.session.flush()
        w = Walker(user_id=u.id)
        db.session.add(w); db.session.commit()
        return u, w

    def test_bulk_cancel_notifies_assigned_walker(self, client_user, dog, service_type,
                                                  admin_user, logged_in_admin):
        """Confirmed bookings assigned to a walker are bulk-cancelled → the walker
        gets exactly one grouped booking_cancelled notification."""
        mon = _next_weekday(0)
        wed = _next_weekday(2)
        walker_u, walker = self._make_walker()

        # Seed confirmed bookings assigned to the walker.
        for d in (mon, wed):
            b = Booking(user_id=client_user.id, dog_id=dog.id,
                        service_type_id=service_type.id, date=d,
                        slot='Morning', status='confirmed', walker_id=walker.id)
            db.session.add(b)
        db.session.commit()

        start = min(mon, wed).isoformat()
        end   = (max(mon, wed) + datetime.timedelta(days=1)).isoformat()
        walker_uid = walker_u.id

        resp = logged_in_admin.post(
            f'/admin/dogs/{dog.id}/bulk-cancel',
            data=json.dumps({'start': start, 'end': end}),
            content_type='application/json',
        )
        assert resp.get_json()['cancelled_count'] == 2

        notifs = Notification.query.filter_by(recipient_id=walker_uid).all()
        assert len(notifs) == 1
        n = notifs[0]
        assert n.notification_type == 'booking_cancelled'
        # Grouped: "Buddy's 2 walks cancelled (Mon … – Wed …)"
        assert '2 walks cancelled' in n.title
