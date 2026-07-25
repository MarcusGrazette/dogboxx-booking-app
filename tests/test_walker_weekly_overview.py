"""
Route tests for GET /walker/weekly — the walker-facing "Weekly overview" tab
(third tab alongside "My pickups" / "Daily overview").

Data source matches the existing daily overview: a walker only shows up for
a day/slot where a dog is actually booked to them. DogBoxx only operates
Monday-Friday, so the page must never render Saturday/Sunday.
"""
import datetime

from app import db
from app.models import Booking, Walker
from app.utils.weekly_schedule import get_week_start, WEEKDAY_LABELS
from tests.conftest import login


def _confirm_booking(user, dog, walker, service_type, date, slot):
    b = Booking(user_id=user.id, dog_id=dog.id, service_type_id=service_type.id,
                date=date, slot=slot, walker_id=walker.id, status='confirmed')
    db.session.add(b)
    db.session.commit()
    return b


class TestWeeklyOverviewAccess:

    def test_requires_login(self, client):
        resp = client.get('/walker/weekly')
        assert resp.status_code == 302

    def test_client_cannot_access(self, app, client, client_user):
        login(client, client_user.email)
        resp = client.get('/walker/weekly', follow_redirects=False)
        assert resp.status_code == 302  # walker_required bounces non-walkers away

    def test_walker_can_access(self, logged_in_walker):
        resp = logged_in_walker.get('/walker/weekly')
        assert resp.status_code == 200
        assert b'Weekly Overview' in resp.data

    def test_admin_can_also_access(self, app, logged_in_admin, admin_user):
        # walker_required allows is_admin through too, same as the other walker
        # pages — but (like the daily pickups page) it also needs a Walker
        # profile row, which the plain admin_user fixture doesn't create.
        walker = Walker(user_id=admin_user.id)
        db.session.add(walker)
        db.session.commit()

        resp = logged_in_admin.get('/walker/weekly')
        assert resp.status_code == 200


class TestWeeklyOverviewContent:

    def test_only_five_weekdays_ever_render(
        self, app, logged_in_walker, walker_user, dog, service_type, client_user
    ):
        # Need at least one booking — an entirely empty week collapses to a
        # single empty-state card, same as the daily overview's empty state.
        walker = Walker.query.filter_by(user_id=walker_user.id).first()
        week_start = get_week_start(datetime.date.today())
        _confirm_booking(client_user, dog, walker, service_type, week_start, 'Morning')

        resp = logged_in_walker.get('/walker/weekly')
        html = resp.data.decode()
        for label in WEEKDAY_LABELS:
            assert f'{label},' in html
        assert 'Saturday' not in html
        assert 'Sunday' not in html
        assert html.count('pickup-slot-header') == len(WEEKDAY_LABELS)  # one per day

    def test_todays_booking_shows_up_in_current_week(
        self, app, logged_in_walker, walker_user, dog, service_type, client_user
    ):
        walker = Walker.query.filter_by(user_id=walker_user.id).first()
        week_start = get_week_start(datetime.date.today())
        _confirm_booking(client_user, dog, walker, service_type, week_start, 'Morning')

        resp = logged_in_walker.get('/walker/weekly')
        html = resp.data.decode()
        assert dog.name in html
        assert 'No walks or drop-ins scheduled this week.' not in html

    def test_empty_week_shows_empty_state(self, logged_in_walker):
        resp = logged_in_walker.get('/walker/weekly')
        assert b'No walks or drop-ins scheduled this week.' in resp.data

    def test_weekend_anchor_resolves_to_that_weeks_monday(
        self, app, logged_in_walker, walker_user, dog, service_type, client_user
    ):
        """Opening the page anchored on a Saturday should show that week's
        Mon-Fri (the week just finished), not silently roll to the next one."""
        walker = Walker.query.filter_by(user_id=walker_user.id).first()
        week_start = get_week_start(datetime.date.today())
        _confirm_booking(client_user, dog, walker, service_type, week_start, 'Afternoon')

        saturday = week_start + datetime.timedelta(days=5)
        resp = logged_in_walker.get(f'/walker/weekly/{saturday.strftime("%Y-%m-%d")}')
        assert resp.status_code == 200
        assert dog.name in resp.data.decode()

    def test_prev_next_week_links_are_seven_days_apart(self, logged_in_walker):
        resp = logged_in_walker.get('/walker/weekly')
        html = resp.data.decode()
        today = datetime.date.today()
        week_start = get_week_start(today)
        prev_week = (week_start - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        next_week = (week_start + datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        assert f'/walker/weekly/{prev_week}' in html
        assert f'/walker/weekly/{next_week}' in html
