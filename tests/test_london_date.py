"""The London date is "today" everywhere (FEATURES #93).

The server runs on UTC, so during BST the UTC date is a day behind London
between 00:00 and 01:00. Every date rule and date-defaulting page must use
`app.utils.dates.local_today()`:

- the clock is frozen at 23:30 UTC on 14 July (00:30 on 15 July in London)
  and a few representative routes are checked against the London date
- a source guard fails if a UTC "today" creeps back into app/
- browser side (FEATURES #104): app/static/js/dates.js is run under Node in
  several time zones, and a guard keeps toISOString() date strings out of
  every other script and template
"""
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

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


# ── Browser side ─────────────────────────────────────────────────────────────

APP = pathlib.Path(__file__).resolve().parent.parent / 'app'
DATES_JS = APP / 'static' / 'js' / 'dates.js'

# Runs dates.js with "now" fixed at the given instant, prints the results.
_NODE_HARNESS = """
const fs = require('fs');
const RealDate = Date;
const fixed = new RealDate(process.argv[2]);
global.Date = class extends RealDate {
    constructor(...a) { if (a.length) { super(...a); } else { super(fixed); } }
    static now() { return fixed.getTime(); }
};
global.window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const D = window.DogBoxxDates;
console.log(JSON.stringify({
    today: D.today(),
    tomorrow: D.addDays(D.today(), 1),
    yearOn: D.addYears('2026-07-15', 1),
    acrossBstStart: D.addDays('2026-03-28', 1),
    acrossBstEnd: D.addDays('2026-10-24', 1),
    backOverMonth: D.addDays('2026-08-01', -1),
}));
"""


def _run_dates_js(instant, tz):
    out = subprocess.run(
        ['node', '-e', _NODE_HARNESS, str(DATES_JS), instant],
        capture_output=True, text=True, check=True, env={**os.environ, 'TZ': tz},
    )
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
@pytest.mark.parametrize('tz', ['Europe/London', 'UTC', 'America/New_York', 'Asia/Tokyo'])
def test_dates_js_uses_london_calendar_in_any_browser_zone(tz):
    # 23:30 UTC on 14 July = 00:30 BST on the 15th.
    got = _run_dates_js('2026-07-14T23:30:00Z', tz)
    assert got == {
        'today': '2026-07-15',
        'tomorrow': '2026-07-16',
        'yearOn': '2027-07-15',  # the old local-midnight round trip gave 2027-07-14
        'acrossBstStart': '2026-03-29',
        'acrossBstEnd': '2026-10-25',
        'backOverMonth': '2026-07-31',
    }


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_dates_js_today_in_winter():
    # GMT: London and UTC agree, right up to midnight.
    assert _run_dates_js('2026-12-14T23:30:00Z', 'Europe/London')['today'] == '2026-12-14'


_ISO_DATE_FROM_DATE = re.compile(r'toISOString\(\)\s*\.\s*(slice|substring|substr|split)\b')


def test_no_toisostring_date_strings_outside_dates_js():
    files = sorted(APP.joinpath('static', 'js').glob('*.js')) + sorted(APP.joinpath('templates').rglob('*.html'))
    offenders = [
        f'{path.relative_to(APP.parent)}:{n}: {line.strip()}'
        for path in files
        if path != DATES_JS
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if _ISO_DATE_FROM_DATE.search(line)
    ]
    assert offenders == [], 'Use DogBoxxDates (app/static/js/dates.js):\n' + '\n'.join(offenders)
