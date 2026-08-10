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
        # Simulate a real browser navigation so walker_required returns the HTML
        # redirect path rather than the JSON 403 branch it serves to fetch callers.
        resp = client.get(
            '/walker/weekly',
            headers={'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'},
            follow_redirects=False,
        )
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

    def test_day_cards_are_closed_by_default(
        self, app, logged_in_walker, walker_user, dog, service_type, client_user
    ):
        """Every day card — including today's — starts collapsed; the user
        opens the ones they care about rather than the page dumping the
        whole week open on load."""
        walker = Walker.query.filter_by(user_id=walker_user.id).first()
        week_start = get_week_start(datetime.date.today())
        _confirm_booking(client_user, dog, walker, service_type, week_start, 'Morning')

        resp = logged_in_walker.get('/walker/weekly')
        html = resp.data.decode()
        assert 'collapse show' not in html
        for i in range(len(WEEKDAY_LABELS)):
            assert f'id="collapse-day-{i}" class="collapse"' in html

    def test_working_days_are_highlighted_non_working_days_are_not(
        self, app, logged_in_walker, walker_user, dog, service_type, client_user
    ):
        walker = Walker.query.filter_by(user_id=walker_user.id).first()
        week_start = get_week_start(datetime.date.today())
        # Only Monday has a booking for this walker.
        _confirm_booking(client_user, dog, walker, service_type, week_start, 'Morning')

        resp = logged_in_walker.get('/walker/weekly')
        html = resp.data.decode()
        # One highlight in the top "who's working, by day" roster row, one in
        # the day card below — both mark the same working Monday.
        assert html.count('card-accent-pink') == 2
        assert html.count('bi-calendar-check-fill') == 2

    def test_no_working_days_means_no_highlight(self, logged_in_walker):
        resp = logged_in_walker.get('/walker/weekly')
        html = resp.data.decode()
        assert 'card-accent-pink' not in html
        assert 'bi-calendar-check-fill' not in html

    def test_highlight_reflects_the_viewing_walker_not_other_walkers(
        self, app, logged_in_walker, walker_user, dog, service_type, client_user
    ):
        """A day another walker works but the viewing walker doesn't must not
        be highlighted for the viewing walker."""
        from tests.conftest import make_user
        other_user = make_user(firstname='Other', lastname='W', role='walker',
                                email='other_wk_highlight@test.dogboxx.org')
        other_walker = Walker(user_id=other_user.id)
        db.session.add(other_walker)
        db.session.commit()

        week_start = get_week_start(datetime.date.today())
        tuesday = week_start + datetime.timedelta(days=1)
        _confirm_booking(client_user, dog, other_walker, service_type, tuesday, 'Afternoon')

        resp = logged_in_walker.get('/walker/weekly')
        html = resp.data.decode()
        assert 'card-accent-pink' not in html
