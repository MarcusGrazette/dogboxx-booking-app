"""
Regression coverage for GET /walker/pickups?view=overview (the "Daily
overview" tab) — added alongside the Weekly Overview feature because this
page previously had zero test coverage, and that feature's implementation
refactored two things it depends on: `_build_daily_overview` now delegates
to app.utils.weekly_schedule.group_bookings_by_slot, and
partials/daily_overview.html now imports its walker-card macro from
partials/_overview_macros.html instead of defining it inline. Both changes
were meant to be behavior-preserving — these tests pin that down.
"""
import datetime

from app import db
from app.models import Booking, Walker


def _confirm_booking(user, dog, walker, service_type, date, slot):
    b = Booking(user_id=user.id, dog_id=dog.id, service_type_id=service_type.id,
                date=date, slot=slot, walker_id=walker.id, status='confirmed')
    db.session.add(b)
    db.session.commit()
    return b


class TestDailyOverviewStillWorks:

    def test_empty_day_shows_empty_state(self, logged_in_walker):
        resp = logged_in_walker.get('/walker/pickups?view=overview')
        assert resp.status_code == 200
        assert b'No walks or drop-ins scheduled for this day.' in resp.data

    def test_booking_shows_walker_and_dog(
        self, app, logged_in_walker, walker_user, dog, service_type, client_user
    ):
        walker = Walker.query.filter_by(user_id=walker_user.id).first()
        today = datetime.date.today()
        _confirm_booking(client_user, dog, walker, service_type, today, 'Morning')

        resp = logged_in_walker.get(f'/walker/pickups/{today.strftime("%Y-%m-%d")}?view=overview')
        html = resp.data.decode()
        assert walker_user.firstname in html
        assert dog.name in html
        assert 'overview-you-pill' in html  # viewing walker sees the "you" pill on their own card

    def test_tab_switcher_still_has_three_tabs(self, logged_in_walker):
        resp = logged_in_walker.get('/walker/pickups?view=overview')
        html = resp.data.decode()
        assert 'My pickups' in html
        assert 'Daily view' in html
        assert 'Weekly view' in html
        assert '/walker/weekly' in html
