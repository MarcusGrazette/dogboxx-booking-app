"""
Route tests for GET /admin/weekly-overview — the admin's manual weekly
check-in page: a list of walkers scheduled that week, each with a Copy
button carrying a plain-text summary of their week (data-copy-text).

Same booking-driven data source as the walker weekly view, grouped by
walker instead of by day. DogBoxx only operates Monday-Friday, so the page
and the copy text must never surface Saturday/Sunday.
"""
import datetime

from app import db
from app.models import Booking, Walker
from app.utils.weekly_schedule import get_week_start
from tests.conftest import login, make_user


def _confirm_booking(user, dog, walker, service_type, date, slot):
    b = Booking(user_id=user.id, dog_id=dog.id, service_type_id=service_type.id,
                date=date, slot=slot, walker_id=walker.id, status='confirmed')
    db.session.add(b)
    db.session.commit()
    return b


class TestWeeklyOverviewAccess:

    def test_requires_login(self, client):
        resp = client.get('/admin/weekly-overview')
        assert resp.status_code == 302

    def test_non_admin_cannot_access(self, app, client, walker_user):
        login(client, walker_user.email)
        resp = client.get('/admin/weekly-overview', follow_redirects=False)
        assert resp.status_code == 302  # admin_required bounces non-admins away

    def test_admin_can_access(self, logged_in_admin):
        resp = logged_in_admin.get('/admin/weekly-overview')
        assert resp.status_code == 200
        assert b'Weekly Overview' in resp.data

    def test_sidebar_link_present_and_highlighted(self, logged_in_admin):
        resp = logged_in_admin.get('/admin/weekly-overview')
        html = resp.data.decode()
        assert '/admin/weekly-overview' in html
        # active class only proves the nav-link's endpoint check works
        assert 'nav-link active' in html


class TestWeeklyOverviewContent:

    def test_empty_week_shows_empty_state(self, logged_in_admin):
        resp = logged_in_admin.get('/admin/weekly-overview')
        assert b'No walkers scheduled this week.' in resp.data

    def test_walker_with_bookings_appears_with_copy_text(
        self, app, logged_in_admin, dog, service_type, client_user
    ):
        walker_u = make_user(firstname='Priya', lastname='P', role='walker',
                              email='priya_admin_wk@test.dogboxx.org')
        walker = Walker(user_id=walker_u.id)
        db.session.add(walker)
        db.session.commit()

        week_start = get_week_start(datetime.date.today())
        _confirm_booking(client_user, dog, walker, service_type, week_start, 'Morning')

        resp = logged_in_admin.get('/admin/weekly-overview')
        html = resp.data.decode()

        assert 'Priya' in html
        assert dog.name in html
        assert 'copy-week-btn' in html
        assert f'Priya — week of {week_start.strftime("%-d %b")}' in html
        assert f'Mon: AM {dog.name}' in html  # inside data-copy-text
        assert 'Saturday' not in html
        assert 'Sunday' not in html

    def test_walker_with_no_bookings_this_week_is_excluded(
        self, app, logged_in_admin, dog, service_type, client_user
    ):
        busy = make_user(firstname='Busy', lastname='B', role='walker',
                          email='busy_admin_wk@test.dogboxx.org')
        busy_walker = Walker(user_id=busy.id)
        idle = make_user(firstname='Idle', lastname='I', role='walker',
                          email='idle_admin_wk@test.dogboxx.org')
        idle_walker = Walker(user_id=idle.id)
        db.session.add_all([busy_walker, idle_walker])
        db.session.commit()

        week_start = get_week_start(datetime.date.today())
        _confirm_booking(client_user, dog, busy_walker, service_type, week_start, 'Morning')

        resp = logged_in_admin.get('/admin/weekly-overview')
        html = resp.data.decode()
        assert 'Busy' in html
        assert 'Idle' not in html

    def test_prev_next_week_links_are_seven_days_apart(self, logged_in_admin):
        resp = logged_in_admin.get('/admin/weekly-overview')
        html = resp.data.decode()
        week_start = get_week_start(datetime.date.today())
        prev_week = (week_start - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        next_week = (week_start + datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        assert f'/admin/weekly-overview/{prev_week}' in html
        assert f'/admin/weekly-overview/{next_week}' in html
