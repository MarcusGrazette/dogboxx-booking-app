"""
Route tests for GET /admin/weekly-overview — the admin "Week view": a grid
with one column per walker booked that week and, per weekday, rows for AM
drop-ins, AM walks, PM walks and PM drop-ins. Dogs are cards showing their
walk note with a pencil to edit it; each row counts its unassigned bookings.
Copy buttons (per walker + team) carry plain-text summaries (data-copy-text)
built from confirmed bookings only, never including notes.

DogBoxx only operates Monday-Friday, so the page and the copy text must
never surface Saturday/Sunday.
"""
import datetime

from app import db
from app.models import Booking, Walker
from app.utils.weekly_schedule import get_week_start
from tests.conftest import login, make_user

WEEK = datetime.date(2026, 10, 19)  # a Monday — pinned, for tests that don't need "this week"


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
        # Simulate a real browser navigation so admin_required returns the HTML
        # redirect path rather than the JSON 403 branch it serves to fetch callers.
        resp = client.get(
            '/admin/weekly-overview',
            headers={'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'},
            follow_redirects=False,
        )
        assert resp.status_code == 302  # admin_required bounces non-admins away

    def test_admin_can_access(self, logged_in_admin):
        resp = logged_in_admin.get('/admin/weekly-overview')
        assert resp.status_code == 200
        assert b'Week view' in resp.data

    def test_sidebar_link_present_highlighted_and_between_dashboard_and_activity(self, logged_in_admin):
        resp = logged_in_admin.get('/admin/weekly-overview')
        html = resp.data.decode()
        assert '/admin/weekly-overview' in html
        # active class only proves the nav-link's endpoint check works
        assert 'nav-link active' in html
        assert '<span>Week view</span>' in html
        assert 'Weekly Overview' not in html
        assert (html.index('<span>Dashboard</span>') < html.index('<span>Week view</span>')
                < html.index('<span>Activity</span>'))


class TestWeeklyOverviewContent:

    def test_empty_week_shows_empty_state(self, logged_in_admin):
        resp = logged_in_admin.get('/admin/weekly-overview')
        assert b'No walks assigned this week' in resp.data

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


class TestTeamCopy:
    """The "Copy team schedule" button — AM/PM walker names per weekday."""

    def _two_walkers(self, prefix):
        sarah = make_user(firstname='Sarah', lastname='S', role='walker',
                           email=f'sarah_{prefix}@test.dogboxx.org')
        priya = make_user(firstname='Priya', lastname='P', role='walker',
                           email=f'priya_{prefix}@test.dogboxx.org')
        sarah_w, priya_w = Walker(user_id=sarah.id), Walker(user_id=priya.id)
        db.session.add_all([sarah_w, priya_w])
        db.session.commit()
        return sarah_w, priya_w

    def test_team_and_walker_copy_buttons(self, app, logged_in_admin, dog, service_type, client_user):
        sarah_w, priya_w = self._two_walkers('copy')
        week_start = get_week_start(datetime.date.today())
        _confirm_booking(client_user, dog, sarah_w, service_type, week_start, 'Morning')
        _confirm_booking(client_user, dog, priya_w, service_type, week_start, 'Afternoon')

        html = logged_in_admin.get('/admin/weekly-overview').data.decode()
        # 3 buttons (team + one per walker) + 2 fixed mentions in the shared
        # clipboard JS (admin_layout.html's comment + selector).
        assert html.count('copy-week-btn') == 5
        assert f'Team schedule — week of {week_start.strftime("%-d %b")}' in html
        assert 'Mon: AM Sarah · PM Priya' in html  # inside data-copy-text (raw, not HTML-entity)

    def test_team_copy_text_is_all_dashes_for_empty_week(self, logged_in_admin):
        html = logged_in_admin.get('/admin/weekly-overview').data.decode()
        # Just the team button (no walkers) + the 2 fixed JS mentions.
        assert html.count('copy-week-btn') == 3
        assert 'Mon: —\nTue: —\nWed: —\nThu: —\nFri: —' in html

    def test_team_copy_dedupes_walker_with_walk_and_dropin_in_same_slot(
        self, app, logged_in_admin, dog, service_type, client_user
    ):
        from app.models import Dog, DogOwner, ServiceType
        dropin = ServiceType(name='Drop In', slug=ServiceType.DROP_IN,
                              capacity_model='walker_assigned', slot_type='morning_afternoon',
                              requires_walker=True, default_max_capacity=6, active=True)
        db.session.add(dropin)
        sarah_w, _ = self._two_walkers('dedupe')
        second_dog = Dog(name='Rex', breed='Mixed')
        db.session.add(second_dog)
        db.session.flush()
        db.session.add(DogOwner(dog_id=second_dog.id, user_id=client_user.id, role='primary'))
        db.session.commit()

        week_start = get_week_start(datetime.date.today())
        _confirm_booking(client_user, dog, sarah_w, service_type, week_start, 'Morning')
        _confirm_booking(client_user, second_dog, sarah_w, dropin, week_start, 'Morning')

        html = logged_in_admin.get('/admin/weekly-overview').data.decode()
        assert 'Mon: AM Sarah\n' in html
        assert 'Sarah, Sarah' not in html

    def test_copy_text_never_includes_notes(self, app, logged_in_admin, dog, service_type, client_user):
        sarah_w, _ = self._two_walkers('nonotes')
        b = _confirm_booking(client_user, dog, sarah_w, service_type, WEEK, 'Morning')
        b.client_notes = 'Gate code 4471'
        db.session.commit()
        html = logged_in_admin.get(f'/admin/weekly-overview/{WEEK.isoformat()}').data.decode()
        for chunk in html.split('data-copy-text="')[1:]:
            assert 'Gate code' not in chunk.split('"', 1)[0]
        assert 'Gate code 4471' in html  # but it is on the dog card

    def test_walker_column_reflects_prev_next_week_navigation(
        self, app, logged_in_admin, dog, service_type, client_user
    ):
        walker_u = make_user(firstname='Priya', lastname='P', role='walker',
                              email='priya_nav@test.dogboxx.org')
        walker = Walker(user_id=walker_u.id)
        db.session.add(walker)
        db.session.commit()

        week_start = get_week_start(datetime.date.today())
        next_week_start = week_start + datetime.timedelta(days=7)
        _confirm_booking(client_user, dog, walker, service_type, next_week_start, 'Morning')

        assert 'Priya' not in logged_in_admin.get('/admin/weekly-overview').data.decode()
        resp = logged_in_admin.get(f'/admin/weekly-overview/{next_week_start.strftime("%Y-%m-%d")}')
        assert 'Priya' in resp.data.decode()


class TestWeekGrid:
    """The grid itself: walker columns, row labels, unassigned counts, dog
    cards with notes, the pencil and the both-walks marker."""

    def _walker(self, first, email):
        u = make_user(firstname=first, lastname='W', role='walker', email=email)
        w = Walker(user_id=u.id)
        db.session.add(w)
        db.session.commit()
        return w

    def _booking(self, client_user, dog, service_type, date, slot, *, status='confirmed',
                 walker=None, note=None):
        b = Booking(user_id=client_user.id, dog_id=dog.id, service_type_id=service_type.id,
                    date=date, slot=slot, status=status,
                    walker_id=walker.id if walker else None, client_notes=note)
        db.session.add(b)
        db.session.commit()
        return b

    def _html(self, flask_client):
        return flask_client.get(f'/admin/weekly-overview/{WEEK.isoformat()}').data.decode()

    def test_cards_show_note_pencil_and_unassigned_count(
        self, app, logged_in_admin, dog, service_type, client_user
    ):
        sarah = self._walker('Sarah', 'sarah_grid_page@test.dogboxx.org')
        b = self._booking(client_user, dog, service_type, WEEK, 'Morning', walker=sarah,
                          note='Bring the long lead')
        self._booking(client_user, dog, service_type, WEEK, 'Afternoon', status='requested',
                      note='Unassigned note')
        html = self._html(logged_in_admin)

        assert 'Sarah' in html
        assert 'Bring the long lead' in html
        assert f'data-booking-id="{b.id}"' in html
        assert 'wv-note-btn has-note' in html
        assert 'bi-pencil-square' in html
        # Unassigned bookings are counted (linking to the dashboard day), not carded.
        assert '1 unassigned' in html
        assert f'/admin/?date={WEEK.isoformat()}' in html
        assert 'Unassigned note' not in html
        # The shared note modal and its script are on the page.
        assert 'id="walkNoteModal"' in html
        assert 'walk-note-modal.js' in html

    def test_both_walks_marker(self, app, logged_in_admin, dog, service_type, client_user):
        sarah = self._walker('Sarah', 'sarah_both@test.dogboxx.org')
        self._booking(client_user, dog, service_type, WEEK, 'Morning', walker=sarah)
        self._booking(client_user, dog, service_type, WEEK, 'Afternoon', walker=sarah)
        html = self._html(logged_in_admin)
        # Two cards (grid) x two layouts (grid + phone list)
        assert html.count('bi-layers-fill') == 4

    def test_drop_in_rows_only_on_days_with_drop_ins(
        self, app, logged_in_admin, dog, service_type, client_user
    ):
        from app.models import ServiceType
        dropin = ServiceType(name='Drop In', slug=ServiceType.DROP_IN,
                              capacity_model='walker_assigned', slot_type='morning_afternoon',
                              requires_walker=True, default_max_capacity=6, active=True)
        db.session.add(dropin)
        db.session.commit()
        html = self._html(logged_in_admin)
        assert 'Drop-ins' not in html
        self._booking(client_user, dog, dropin, WEEK, 'Afternoon', status='requested')
        html = self._html(logged_in_admin)
        # One drop-in row, rendered in both layouts.
        assert html.count('class="wv-chip wv-chip-drop"') == 2

    def test_note_text_is_escaped(self, app, logged_in_admin, dog, service_type, client_user):
        sarah = self._walker('Sarah', 'sarah_esc@test.dogboxx.org')
        self._booking(client_user, dog, service_type, WEEK, 'Morning', walker=sarah,
                      note='<script>alert(1)</script>')
        html = self._html(logged_in_admin)
        assert '<script>alert(1)</script>' not in html
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
