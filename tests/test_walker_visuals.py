"""
Unit tests for app/utils/walker_visuals.py — the shared per-walker
color/initials helpers used by both the assignment board and the admin
weekly overview. Pure functions, so these run against lightweight fakes
rather than real ORM objects.
"""
from app.utils.walker_visuals import WALKER_COLORS, walker_color, walker_initials


class _FakeUser:
    def __init__(self, firstname=None, lastname=None):
        self.firstname = firstname
        self.lastname = lastname


class _FakeWalker:
    def __init__(self, user=None):
        self.user = user


class TestWalkerColor:

    def test_returns_a_palette_color(self):
        assert walker_color(1) == WALKER_COLORS[1]

    def test_cycles_through_the_palette(self):
        assert walker_color(len(WALKER_COLORS) + 1) == walker_color(1)


class TestWalkerInitials:

    def test_first_and_last_initial(self):
        walker = _FakeWalker(user=_FakeUser(firstname='Priya', lastname='Patel'))
        assert walker_initials(walker) == 'PP'

    def test_missing_lastname_returns_just_first_initial(self):
        walker = _FakeWalker(user=_FakeUser(firstname='Priya', lastname=None))
        assert walker_initials(walker) == 'P'

    def test_missing_firstname_returns_just_last_initial(self):
        walker = _FakeWalker(user=_FakeUser(firstname=None, lastname='Patel'))
        assert walker_initials(walker) == 'P'

    def test_no_user_does_not_crash(self):
        walker = _FakeWalker(user=None)
        assert walker_initials(walker) == '?'
