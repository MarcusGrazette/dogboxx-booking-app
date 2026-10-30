"""The London date is "today" everywhere (FEATURES #93).

The server runs on UTC, so during BST the UTC date is a day behind London
between 00:00 and 01:00. Every date rule and date-defaulting page must use
`app.utils.dates.local_today()`:

- the clock is frozen at 23:30 UTC on 14 July (00:30 on 15 July in London)
  and a few representative routes are checked against the London date
- a source guard fails if a UTC "today" creeps back into app/
"""
import datetime as dt
import pathlib
import re

from app.utils.dates import local_today

# 23:30 UTC on Tue 14 Jul 2026 = 00:30 BST on Wed 15 Jul.
FROZEN_UTC = dt.datetime(2026, 7, 14, 23, 30, tzinfo=dt.timezone.utc)
UTC_DAY = dt.date(2026, 7, 14)
LONDON_DAY = dt.date(2026, 7, 15)


class _FrozenDateTime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FROZEN_UTC.replace(tzinfo=None)
        return FROZEN_UTC.astimezone(tz)


def _freeze(monkeypatch):
    # local_today() reads this module-level name, so one patch moves "today"
    # for every caller, however they imported local_today.
    monkeypatch.setattr('app.utils.dates.datetime', _FrozenDateTime)


class TestJustAfterMidnightInSummer:

    def test_local_today_is_the_london_date(self, monkeypatch):
        _freeze(monkeypatch)
        assert local_today() == LONDON_DAY

    def test_walker_pickups_default_to_the_london_date(self, monkeypatch, logged_in_walker):
        _freeze(monkeypatch)
        html = logged_in_walker.get('/walker/pickups').data.decode()
        assert f"initialDate = '{LONDON_DAY.isoformat()}'" in html

    def test_client_cannot_book_the_utc_date(self, monkeypatch, logged_in_client, dog,
                                             service_type):
        _freeze(monkeypatch)
        resp = logged_in_client.post('/book', json={
            'date': UTC_DAY.isoformat(), 'slot': 'Morning', 'dog_id': dog.id})
        assert resp.status_code == 400
        assert 'past' in resp.get_json()['message']

    def test_client_can_book_the_london_date(self, monkeypatch, logged_in_client, dog,
                                             service_type):
        _freeze(monkeypatch)
        resp = logged_in_client.post('/book', json={
            'date': LONDON_DAY.isoformat(), 'slot': 'Morning', 'dog_id': dog.id})
        assert resp.get_json()['success'] is True


# A UTC "today": date.today() under any alias, datetime.now(...).date()
# (incl. arithmetic before .date()), or a naive now() formatted as a date.
_UTC_TODAY = re.compile(
    r'(?<!local_)\b\w*date\w*\.today\(\)'
    r'|\bnow\([^)]*\)[^\n]*?\)?\.date\(\)'
    r"|\bnow\(\)\.strftime\(['\"]%Y-%m-%d"
)


def test_no_utc_today_in_app():
    root = pathlib.Path(__file__).resolve().parent.parent / 'app'
    offenders = [
        f'{path.relative_to(root.parent)}:{n}: {line.strip()}'
        for path in sorted(root.rglob('*.py'))
        if path.name != 'dates.py'
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if _UTC_TODAY.search(line)
    ]
    assert offenders == [], 'Use app.utils.dates.local_today():\n' + '\n'.join(offenders)
