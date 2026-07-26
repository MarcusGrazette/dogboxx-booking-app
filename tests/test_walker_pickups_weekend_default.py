"""
Regression coverage for GET /walker/pickups' default-date resolution.

DogBoxx only operates Monday-Friday. A walker landing on the pickup list
with no explicit date (i.e. straight after login, via / -> /walker/pickups)
on a Saturday or Sunday should default to the coming Monday, not a closed
day with nothing to show.
"""
import datetime as dt


class _FrozenDateTime(dt.datetime):
    """Stand-in for the `datetime` class imported into
    app.blueprints.walker.routes, freezing .now() while leaving .strptime()
    (used to parse an explicit ?date=) untouched."""
    _frozen = None

    @classmethod
    def now(cls, tz=None):
        return cls._frozen if tz is None else cls._frozen.replace(tzinfo=tz)


def _freeze(monkeypatch, frozen_date):
    frozen = dt.datetime.combine(frozen_date, dt.time(9, 0), tzinfo=dt.timezone.utc)
    fake = type('FrozenDateTime', (_FrozenDateTime,), {'_frozen': frozen})
    monkeypatch.setattr('app.blueprints.walker.routes.datetime', fake)


class TestPickupsWeekendDefault:

    def test_saturday_defaults_to_following_monday(self, monkeypatch, logged_in_walker):
        saturday = dt.date(2026, 7, 25)
        assert saturday.weekday() == 5
        _freeze(monkeypatch, saturday)

        resp = logged_in_walker.get('/walker/pickups')
        html = resp.data.decode()

        monday = saturday + dt.timedelta(days=2)
        assert f"initialDate = '{monday.strftime('%Y-%m-%d')}'" in html

    def test_sunday_defaults_to_following_monday(self, monkeypatch, logged_in_walker):
        sunday = dt.date(2026, 7, 26)
        assert sunday.weekday() == 6
        _freeze(monkeypatch, sunday)

        resp = logged_in_walker.get('/walker/pickups')
        html = resp.data.decode()

        monday = sunday + dt.timedelta(days=1)
        assert f"initialDate = '{monday.strftime('%Y-%m-%d')}'" in html

    def test_weekday_still_defaults_to_itself(self, monkeypatch, logged_in_walker):
        tuesday = dt.date(2026, 7, 28)
        assert tuesday.weekday() == 1
        _freeze(monkeypatch, tuesday)

        resp = logged_in_walker.get('/walker/pickups')
        html = resp.data.decode()

        assert f"initialDate = '{tuesday.strftime('%Y-%m-%d')}'" in html

    def test_explicit_weekend_date_is_not_overridden(self, monkeypatch, logged_in_walker):
        """An explicit ?date= (or path param) for a weekend day is left as-is
        — only the no-date default should roll forward."""
        saturday = dt.date(2026, 7, 25)
        _freeze(monkeypatch, dt.date(2026, 7, 28))  # "today" is unrelated Tuesday

        resp = logged_in_walker.get(f'/walker/pickups/{saturday.strftime("%Y-%m-%d")}')
        html = resp.data.decode()

        assert f"initialDate = '{saturday.strftime('%Y-%m-%d')}'" in html
