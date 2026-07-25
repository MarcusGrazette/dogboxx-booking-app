"""
Unit tests for app/utils/weekly_schedule.py — the shared data layer behind
the walker "Weekly overview" tab and the admin "Weekly Overview" roster.

Both consumers group the exact same booking data two different ways (by day,
by walker); these tests exercise the grouping/formatting functions directly
rather than going through either route, so a regression here is pinned to
the shared layer, not one specific page.

Fixed Mon-Fri dates (2026-07-27 .. 2026-07-31) are used throughout so the
tests don't depend on which day they happen to run.
"""
import datetime

from werkzeug.security import generate_password_hash

from app import db
from app.models import User, Walker, Dog, DogOwner, Client, ServiceType, Booking
from app.utils.weekly_schedule import (
    get_week_start,
    fetch_week_bookings,
    build_week_by_day,
    build_week_by_walker,
    format_walker_week_text,
    day_slot_parts,
    WEEKDAYS,
    WEEKDAY_LABELS,
)

MON = datetime.date(2026, 7, 27)
TUE = datetime.date(2026, 7, 28)
WED = datetime.date(2026, 7, 29)
THU = datetime.date(2026, 7, 30)
FRI = datetime.date(2026, 7, 31)
SAT = datetime.date(2026, 8, 1)
SUN = datetime.date(2026, 7, 26)


def _make_walker(firstname, email):
    u = User(firstname=firstname, lastname='W', email=email, role='walker',
             active=True, hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.flush()
    w = Walker(user_id=u.id)
    db.session.add(w)
    db.session.commit()
    return w


def _make_dog(name, email):
    u = User(firstname='Client', lastname='X', email=email, role='client',
             active=True, hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.flush()
    db.session.add(Client(user_id=u.id, onboarding_completed=True))
    dog = Dog(name=name, breed='Mixed')
    db.session.add(dog)
    db.session.flush()
    db.session.add(DogOwner(dog_id=dog.id, user_id=u.id, role='primary'))
    db.session.commit()
    return u, dog


def _service_type(slug, name):
    st = ServiceType(name=name, slug=slug, capacity_model='walker_assigned',
                      slot_type='morning_afternoon', requires_walker=True,
                      default_max_capacity=6, active=True)
    db.session.add(st)
    db.session.commit()
    return st


def _booking(user, dog, walker, service_type, date, slot, status='confirmed'):
    b = Booking(user_id=user.id, dog_id=dog.id, service_type_id=service_type.id,
                date=date, slot=slot, walker_id=walker.id, status=status)
    db.session.add(b)
    db.session.commit()
    return b


class TestGetWeekStart:

    def test_monday_returns_itself(self, app):
        assert get_week_start(MON) == MON

    def test_midweek_returns_that_weeks_monday(self, app):
        assert get_week_start(THU) == MON

    def test_friday_returns_that_weeks_monday(self, app):
        assert get_week_start(FRI) == MON

    def test_saturday_resolves_to_that_weeks_monday(self, app):
        # DogBoxx never operates weekends, but a walker/admin might still
        # open the page on one — it must resolve to the week just finished,
        # not silently jump to the next week.
        assert get_week_start(SAT) == MON

    def test_sunday_resolves_to_previous_mondays_week(self, app):
        assert get_week_start(SUN) == MON - datetime.timedelta(days=7)


class TestDaySlotPartsAndFormat:

    def test_day_slot_parts_empty_day(self, app):
        assert day_slot_parts([]) == []

    def test_day_slot_parts_single_slot(self, app):
        walker = _make_walker('Sarah', 'sarah_w@test.dogboxx.org')
        walk = _service_type('group-walk', 'Group Walk')
        bella_owner, bella = _make_dog('Bella', 'owner_bella@test.com')
        max_owner, max_ = _make_dog('Max', 'owner_max@test.com')
        b1 = _booking(bella_owner, bella, walker, walk, MON, 'Morning')
        b2 = _booking(max_owner, max_, walker, walk, MON, 'Morning')

        parts = day_slot_parts([b1, b2])
        assert parts == [('AM', 'Bella, Max')]

    def test_day_slot_parts_both_slots(self, app):
        walker = _make_walker('Sarah', 'sarah_w2@test.dogboxx.org')
        walk = _service_type('group-walk', 'Group Walk')
        rex_owner, rex = _make_dog('Rex', 'owner_rex@test.com')
        luna_owner, luna = _make_dog('Luna', 'owner_luna@test.com')
        b1 = _booking(rex_owner, rex, walker, walk, TUE, 'Morning')
        b2 = _booking(luna_owner, luna, walker, walk, TUE, 'Afternoon')

        parts = day_slot_parts([b1, b2])
        assert parts == [('AM', 'Rex'), ('PM', 'Luna')]

    def test_format_walker_week_text_is_always_five_lines(self, app):
        walker = _make_walker('Sarah', 'sarah_w3@test.dogboxx.org')
        text = format_walker_week_text(walker, {}, MON)
        lines = text.split('\n')
        assert len(lines) == WEEKDAYS + 1  # header + Mon..Fri
        assert lines[0] == f"Sarah — week of {MON.strftime('%-d %b')}"
        for i, label in enumerate(WEEKDAY_LABELS):
            assert lines[i + 1] == f"{label}: —"

    def test_format_walker_week_text_mixed_week(self, app):
        walker = _make_walker('Sarah', 'sarah_w4@test.dogboxx.org')
        walk = _service_type('group-walk', 'Group Walk')
        bella_owner, bella = _make_dog('Bella', 'owner_bella2@test.com')
        max_owner, max_ = _make_dog('Max', 'owner_max2@test.com')
        rex_owner, rex = _make_dog('Rex', 'owner_rex2@test.com')
        luna_owner, luna = _make_dog('Luna', 'owner_luna2@test.com')

        days = {
            MON: [_booking(bella_owner, bella, walker, walk, MON, 'Morning'),
                  _booking(max_owner, max_, walker, walk, MON, 'Morning')],
            TUE: [_booking(rex_owner, rex, walker, walk, TUE, 'Morning'),
                  _booking(luna_owner, luna, walker, walk, TUE, 'Afternoon')],
        }

        text = format_walker_week_text(walker, days, MON)
        expected = (
            "Sarah — week of 27 Jul\n"
            "Mon: AM Bella, Max\n"
            "Tue: AM Rex · PM Luna\n"
            "Wed: —\n"
            "Thu: —\n"
            "Fri: —"
        )
        assert text == expected

    def test_format_walker_week_text_combines_walk_and_dropin_in_same_slot(self, app):
        """The chosen copy-text detail level has no drop-in flag — a walk and
        a drop-in for the same walker/day/slot are just listed together."""
        walker = _make_walker('Sarah', 'sarah_w5@test.dogboxx.org')
        walk = _service_type('group-walk', 'Group Walk')
        dropin = _service_type('drop-in', 'Drop In')
        bella_owner, bella = _make_dog('Bella', 'owner_bella3@test.com')
        max_owner, max_ = _make_dog('Max', 'owner_max3@test.com')

        days = {
            MON: [_booking(bella_owner, bella, walker, walk, MON, 'Morning'),
                  _booking(max_owner, max_, walker, dropin, MON, 'Morning')],
        }
        text = format_walker_week_text(walker, days, MON)
        assert "Mon: AM Bella, Max" in text
        assert "DROP" not in text.upper()


class TestBuildWeekByDayAndByWalker:

    def test_build_week_by_day_groups_by_slot_per_day(self, app):
        sarah = _make_walker('Sarah', 'sarah_bd@test.dogboxx.org')
        walk = _service_type('group-walk', 'Group Walk')
        bella_owner, bella = _make_dog('Bella', 'owner_bella4@test.com')
        _booking(bella_owner, bella, sarah, walk, MON, 'Morning')

        bookings = fetch_week_bookings(MON)
        week = build_week_by_day(bookings, MON)

        assert set(week.keys()) == {MON + datetime.timedelta(days=i) for i in range(WEEKDAYS)}
        assert week[MON]['Morning']['dog_count'] == 1
        assert week[MON]['Afternoon']['dog_count'] == 0
        assert week[TUE]['Morning']['dog_count'] == 0

    def test_fetch_week_bookings_excludes_weekend_dates(self, app):
        sarah = _make_walker('Sarah', 'sarah_wk@test.dogboxx.org')
        walk = _service_type('group-walk', 'Group Walk')
        bella_owner, bella = _make_dog('Bella', 'owner_bella5@test.com')
        # A booking on the Saturday just past this week's Friday should never
        # be pulled in, even if one somehow exists in the data.
        _booking(bella_owner, bella, sarah, walk, SAT, 'Morning')

        bookings = fetch_week_bookings(MON)
        assert bookings == []

    def test_build_week_by_walker_sorted_and_scoped_to_walkers_with_bookings(self, app):
        sarah = _make_walker('Sarah', 'sarah_bw@test.dogboxx.org')
        aaron = _make_walker('Aaron', 'aaron_bw@test.dogboxx.org')
        idle_walker = _make_walker('Zed', 'zed_bw@test.dogboxx.org')  # noqa: F841 — no bookings
        walk = _service_type('group-walk', 'Group Walk')
        bella_owner, bella = _make_dog('Bella', 'owner_bella6@test.com')
        rex_owner, rex = _make_dog('Rex', 'owner_rex3@test.com')

        _booking(bella_owner, bella, sarah, walk, MON, 'Morning')
        _booking(rex_owner, rex, aaron, walk, TUE, 'Afternoon')

        bookings = fetch_week_bookings(MON)
        by_walker = build_week_by_walker(bookings)

        assert [entry['walker'].user.firstname for entry in by_walker] == ['Aaron', 'Sarah']
        sarah_entry = next(e for e in by_walker if e['walker'].id == sarah.id)
        assert list(sarah_entry['days'].keys()) == [MON]
        assert len(sarah_entry['days'][MON]) == 1
