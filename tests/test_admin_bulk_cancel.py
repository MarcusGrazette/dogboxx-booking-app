"""
Tests for /admin/dogs/<dog_id>/cancel-preview and /bulk-cancel.

Covers the day-of-week filter added on top of the existing range + slot
filters. Empty/missing `days` = no filter (backward compat with any caller
that doesn't send the field).
"""
import datetime
import json

from app import db
from app.models import Booking


def _next_weekday(target_dow, after=None):
    """Nearest future date whose weekday() == target_dow (0=Mon)."""
    d = (after or datetime.date.today()) + datetime.timedelta(days=1)
    while d.weekday() != target_dow:
        d += datetime.timedelta(days=1)
    return d


def _seed_bookings(user, dog, service_type, dates, slot='Morning'):
    """Create requested bookings for the given dates. Returns list of IDs."""
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

    def test_filter_narrows_to_selected_weekday(self, app, client_user, dog, service_type,
                                                 logged_in_admin):
        with app.app_context():
            tue = _next_weekday(1)  # Tuesday
            wed = _next_weekday(2)  # Wednesday
            _seed_bookings(client_user, dog, service_type, [tue, wed])
            dog_id = dog.id
            start = (tue if tue < wed else wed).isoformat()
            end   = (tue + datetime.timedelta(days=14)).isoformat()

        # Only Tuesdays (weekday=1)
        resp = logged_in_admin.get(
            f'/admin/dogs/{dog_id}/cancel-preview'
            f'?start={start}&end={end}&days=1'
        )
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data['success'] is True
        # Both seeded bookings sit in the range; only Tuesday should match.
        assert data['count'] == 1

    def test_no_days_param_returns_all_days(self, app, client_user, dog, service_type,
                                             logged_in_admin):
        """Regression guard: existing callers don't send `days` and must keep working."""
        with app.app_context():
            tue = _next_weekday(1)
            wed = _next_weekday(2)
            _seed_bookings(client_user, dog, service_type, [tue, wed])
            dog_id = dog.id
            start = min(tue, wed).isoformat()
            end   = (tue + datetime.timedelta(days=14)).isoformat()

        resp = logged_in_admin.get(
            f'/admin/dogs/{dog_id}/cancel-preview?start={start}&end={end}'
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['count'] == 2

    def test_invalid_day_values_ignored_not_400(self, app, client_user, dog, service_type,
                                                  logged_in_admin):
        """Bogus values dropped silently, mirroring slot-filter behaviour."""
        with app.app_context():
            tue = _next_weekday(1)
            _seed_bookings(client_user, dog, service_type, [tue])
            dog_id = dog.id
            start = tue.isoformat()
            end   = (tue + datetime.timedelta(days=7)).isoformat()

        # 'foo' is non-int, 9 is out of 0..4 range — both ignored, leaving day=1.
        resp = logged_in_admin.get(
            f'/admin/dogs/{dog_id}/cancel-preview'
            f'?start={start}&end={end}&days=foo&days=9&days=1'
        )
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data['count'] == 1

    def test_day_and_slot_filter_combined(self, app, client_user, dog, service_type,
                                            logged_in_admin):
        with app.app_context():
            tue = _next_weekday(1)
            # Two bookings on Tuesday: AM and PM
            _seed_bookings(client_user, dog, service_type, [tue], slot='Morning')
            _seed_bookings(client_user, dog, service_type, [tue], slot='Afternoon')
            dog_id = dog.id
            start = tue.isoformat()
            end   = (tue + datetime.timedelta(days=7)).isoformat()

        resp = logged_in_admin.get(
            f'/admin/dogs/{dog_id}/cancel-preview'
            f'?start={start}&end={end}&days=1&slots=Morning'
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['count'] == 1
        assert data['bookings'][0]['slot'] == 'Morning'


class TestBulkCancelDayFilter:
    """POST /admin/dogs/<id>/bulk-cancel with the day filter."""

    def test_only_filtered_weekday_cancelled(self, app, client_user, dog, service_type,
                                              logged_in_admin):
        with app.app_context():
            tue = _next_weekday(1)
            wed = _next_weekday(2)
            ids = _seed_bookings(client_user, dog, service_type, [tue, wed])
            dog_id = dog.id
            start = min(tue, wed).isoformat()
            end   = (tue + datetime.timedelta(days=14)).isoformat()
            tue_id, wed_id = (ids[0], ids[1]) if tue < wed else (ids[1], ids[0])

        resp = logged_in_admin.post(
            f'/admin/dogs/{dog_id}/bulk-cancel',
            data=json.dumps({'start': start, 'end': end, 'days': [1]}),
            content_type='application/json',
        )
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data['cancelled_count'] == 1

        with app.app_context():
            assert db.session.get(Booking, tue_id).status == 'cancelled'
            assert db.session.get(Booking, wed_id).status == 'requested'

    def test_all_five_days_equivalent_to_no_filter(self, app, client_user, dog, service_type,
                                                     logged_in_admin):
        """User-facing UX: all-checked = same effect as the old no-day-filter button."""
        with app.app_context():
            tue = _next_weekday(1)
            wed = _next_weekday(2)
            _seed_bookings(client_user, dog, service_type, [tue, wed])
            dog_id = dog.id
            start = min(tue, wed).isoformat()
            end   = (tue + datetime.timedelta(days=14)).isoformat()

        resp = logged_in_admin.post(
            f'/admin/dogs/{dog_id}/bulk-cancel',
            data=json.dumps({
                'start': start, 'end': end, 'days': [0, 1, 2, 3, 4],
            }),
            content_type='application/json',
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['cancelled_count'] == 2
