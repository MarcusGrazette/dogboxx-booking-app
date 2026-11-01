"""Client home bookings list (app/static/js/client-bookings-list.js) under Node.

Develop review #21: the list paged by per-row numbers that booking and
cancelling patched separately, so rows could become unreachable or invisible,
and new bookings landed at the top whatever their date. These tests drive the
real script against a small fake DOM (and a fetch the test answers by hand):

- 10 -> 11 bookings shows the pager; 11 -> 10 hides it with every row visible
- cancelling the only row on the last page steps back a page
- a new booking lands in date order, Morning before Afternoon
- a past calendar date is fetched; only the latest request's reply is shown
- names from the server are escaped
"""
import json
import pathlib
import shutil
import subprocess

import pytest

LIST_JS = (pathlib.Path(__file__).resolve().parent.parent
           / 'app' / 'static' / 'js' / 'client-bookings-list.js')

pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')

TODAY = '2026-11-02'

# Fake DOM, fetch and globals, then the list script, then the scenario
# (argv[2], a JS async function body). `L` is window.BookingsList, `api` the
# helpers below; the scenario's return value is printed as JSON.
_HARNESS = r"""
const fs = require('fs');

class FakeElement {
    constructor(tag) {
        this.tagName = tag; this.className = ''; this.dataset = {}; this.style = {};
        this.children = []; this.parentNode = null; this._html = ''; this.textContent = '';
        this.disabled = false;
        const el = this;
        this.classList = {
            add: c => { if (!el.hasClass(c)) el.className += ' ' + c; },
            remove: c => { el.className = el.className.split(/\s+/).filter(x => x !== c).join(' '); },
            toggle: (c, on) => on ? el.classList.add(c) : el.classList.remove(c),
            contains: c => el.hasClass(c),
        };
    }
    hasClass(c) { return this.className.split(/\s+/).includes(c); }
    set innerHTML(v) { this._html = v; this.children = []; }
    get innerHTML() { return this._html; }
    appendChild(ch) {
        if (ch.parentNode) ch.parentNode.removeChild(ch);
        ch.parentNode = this; this.children.push(ch); return ch;
    }
    removeChild(ch) { this.children = this.children.filter(x => x !== ch); ch.parentNode = null; }
    querySelector() { return null; }
}

const byId = {};
const lists = [];
global.document = {
    getElementById: id => byId[id] || null,
    createElement: tag => new FakeElement(tag),
    querySelectorAll: sel => {
        if (sel !== 'ul[data-bookings-list]') throw new Error('unexpected selector ' + sel);
        return lists;
    },
};
global.window = global;
global.requestAnimationFrame = fn => fn();
global.DogBoxxDates = { today: () => '__TODAY__' };
const toasts = [];
global.showToast = (msg, kind) => toasts.push(msg);

const calls = [];
global.fetch = url => new Promise(resolve => {
    calls.push({ url, reply: data => resolve({ json: async () => data }) });
});
const settle = () => new Promise(r => setTimeout(r, 0));
const wait = ms => new Promise(r => setTimeout(r, ms));

function make(id, tag) { const el = new FakeElement(tag || 'div'); byId[id] = el; return el; }

const api = {
    calls, toasts, settle, wait,
    // A dog card as index.html renders it, with rows given as 'date|slot'.
    makeDog(dogId, rows) {
        const ul = make(`bookings-list-${dogId}`, 'ul');
        ul.dataset.dogId = String(dogId);
        lists.push(ul);
        rows.forEach(r => ul.appendChild(api.row(r)));
        ['past-bookings-list', 'dog-filter-empty', 'dog-filter-empty-text', 'walk-pager',
         'dog-empty', 'walk-page-label', 'walk-prev', 'walk-next', 'dog-subtitle']
            .forEach(p => make(`${p}-${dogId}`));
        L.refreshList(String(dogId));
        return ul;
    },
    row(key) {
        const [date, slot] = key.split('|');
        const li = new FakeElement('li');
        Object.assign(li.dataset, { date, slot, walkIdx: '0' });
        return li;
    },
    key: li => `${li.dataset.date}|${li.dataset.slot}`,
    all: dogId => byId[`bookings-list-${dogId}`].children.map(api.key),
    visible: dogId => byId[`bookings-list-${dogId}`].children
        .filter(li => !li.hasClass('d-none')).map(api.key),
    shown: id => !byId[id].hasClass('d-none'),
    pastHtml: dogId => byId[`past-bookings-list-${dogId}`].innerHTML,
    filterEmptyShown: dogId => byId[`dog-filter-empty-${dogId}`].style.display === '',
    findRow(dogId, key) { return byId[`bookings-list-${dogId}`].children.find(li => api.key(li) === key); },
    async cancel(dogId, key) { L.removeRow(api.findRow(dogId, key)); await wait(320); },
    book(dogId, key) {
        const [date, slot] = key.split('|');
        return L.injectBooking({ id: 999, dog_id: dogId, date_iso: date, slot, status: 'confirmed',
                                 date_display: date });
    },
    // n consecutive Mornings from 2026-11-03.
    days(n) {
        return Array.from({ length: n }, (_, i) => {
            const d = new Date(Date.UTC(2026, 10, 3 + i));
            return d.toISOString().slice(0, 10) + '|Morning';
        });
    },
};

eval(fs.readFileSync(process.argv[1], 'utf8'));
const L = window.BookingsList;
L.configure({ dayBookingsUrl: '/day_bookings/0000-00-00' });

(async () => {
    const scenario = new (Object.getPrototypeOf(async function () {}).constructor)('L', 'api', process.argv[2]);
    console.log(JSON.stringify(await scenario(L, api)));
    process.exit(0);   // don't wait out the "New" badge timers
})().catch(e => { process.stderr.write(String(e.stack || e)); process.exit(1); });
""".replace('__TODAY__', TODAY)


def _run(scenario):
    proc = subprocess.run(['node', '-e', _HARNESS, str(LIST_JS), scenario],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class TestPaging:

    def test_booking_an_eleventh_shows_the_pager(self):
        r = _run("""
            const days = api.days(11);
            api.makeDog(1, days.slice(0, 10));
            const before = api.shown('walk-pager-1');
            api.book(1, days[10]);
            const page0 = api.visible(1).length;
            L.turnPage('1', 1);
            return { before, after: api.shown('walk-pager-1'), page0, page1: api.visible(1) };
        """)
        assert r['before'] is False
        assert r['after'] is True
        assert r['page0'] == 10
        assert r['page1'] == ['2026-11-13|Morning']

    def test_cancelling_back_to_ten_shows_every_row(self):
        r = _run("""
            const days = api.days(11);
            api.makeDog(1, days);
            await api.cancel(1, days[3]);
            return { visible: api.visible(1).length, pager: api.shown('walk-pager-1'),
                     last: api.visible(1).includes(days[10]) };
        """)
        # The old code kept the 11th row's index at 10 with total=10, so it
        # could never be shown again.
        assert r == {'visible': 10, 'pager': False, 'last': True}

    def test_cancelling_the_only_row_on_the_last_page_steps_back(self):
        r = _run("""
            const days = api.days(21);
            api.makeDog(1, days);
            L.turnPage('1', 1); L.turnPage('1', 1);
            const lastPage = api.visible(1);
            await api.cancel(1, days[20]);
            return { lastPage, now: api.visible(1).length,
                     label: document.getElementById('walk-page-label-1').textContent,
                     nextDisabled: document.getElementById('walk-next-1').disabled };
        """)
        assert r['lastPage'] == ['2026-11-23|Morning']
        assert r['now'] == 10
        assert r['label'] == '11 – 20 of 20'
        assert r['nextDisabled'] is True

    def test_cancelling_the_last_booking_shows_the_empty_state(self):
        r = _run("""
            api.makeDog(1, ['2026-11-03|Morning']);
            const before = api.shown('dog-empty-1');
            await api.cancel(1, '2026-11-03|Morning');
            return { before, after: api.shown('dog-empty-1') };
        """)
        assert r == {'before': False, 'after': True}

    def test_turn_page_stays_in_range(self):
        r = _run("""
            api.makeDog(1, api.days(25));
            L.turnPage('1', -1);
            const first = document.getElementById('walk-page-label-1').textContent;
            L.turnPage('1', 5);
            return { first, last: document.getElementById('walk-page-label-1').textContent };
        """)
        assert r == {'first': '1 – 10 of 25', 'last': '21 – 25 of 25'}


class TestOrdering:

    def test_new_bookings_land_in_date_and_slot_order(self):
        r = _run("""
            api.makeDog(1, ['2026-11-03|Afternoon', '2026-11-10|Morning']);
            api.book(1, '2026-12-01|Morning');   // used to land at the top
            api.book(1, '2026-11-03|Morning');   // same day: Morning first
            return api.all(1);
        """)
        assert r == ['2026-11-03|Morning', '2026-11-03|Afternoon',
                     '2026-11-10|Morning', '2026-12-01|Morning']

    def test_booking_while_filtered_on_that_date_shows_it(self):
        r = _run("""
            api.makeDog(1, ['2026-11-03|Morning']);
            L.applyDateFilter('2026-11-05');
            const emptyBefore = api.filterEmptyShown(1);
            api.book(1, '2026-11-05|Afternoon');
            return { emptyBefore, visible: api.visible(1), emptyAfter: api.filterEmptyShown(1) };
        """)
        assert r == {'emptyBefore': True, 'visible': ['2026-11-05|Afternoon'], 'emptyAfter': False}


class TestPastDates:

    def test_past_date_is_fetched_and_shown_per_dog(self):
        r = _run("""
            api.makeDog(1, ['2026-11-03|Morning']);
            api.makeDog(2, []);
            L.applyDateFilter('2026-10-20');
            const url = api.calls[0].url;
            api.calls[0].reply({ success: true, bookings: [
                { id: 5, dog_id: 1, date_display: 'Mon 20 Oct', slot: 'Morning',
                  status: 'confirmed', walker_name: 'Sam', is_drop_in: false, note: '' }] });
            await api.settle();
            return { url, upcoming: api.visible(1), past1: api.pastHtml(1),
                     empty1: api.filterEmptyShown(1), empty2: api.filterEmptyShown(2) };
        """)
        assert r['url'] == '/day_bookings/2026-10-20'
        assert r['upcoming'] == []
        assert 'Mon 20 Oct' in r['past1'] and '(Sam)' in r['past1']
        assert 'cancel-booking-btn' not in r['past1']
        assert 'note-booking-btn' not in r['past1']   # no note: no icon at all
        assert 'note-view-btn' not in r['past1']
        assert r['empty1'] is False and r['empty2'] is True

    def test_a_past_note_is_view_only(self):
        r = _run("""
            api.makeDog(1, []);
            L.applyDateFilter('2026-10-20');
            api.calls[0].reply({ success: true, bookings: [{ id: 5, dog_id: 1,
                date_display: 'Mon 20 Oct', slot: 'Morning', status: 'confirmed', note: 'Muddy' }] });
            await api.settle();
            api.book(1, '2026-11-03|Morning');   // an upcoming row, for contrast
            return { past: api.pastHtml(1),
                     upcoming: api.findRow(1, '2026-11-03|Morning').innerHTML };
        """)
        assert 'note-view-btn' in r['past'] and 'bi-file-earmark' in r['past']
        assert 'note-booking-btn' not in r['past'] and 'bi-pencil' not in r['past']
        assert 'data-current-note="Muddy"' in r['past']
        assert 'note-booking-btn' in r['upcoming'] and 'cancel-booking-btn' in r['upcoming']

    def test_an_older_reply_does_not_overwrite_a_newer_date(self):
        r = _run("""
            api.makeDog(1, []);
            L.applyDateFilter('2026-10-20');
            L.applyDateFilter('2026-10-21');
            const row = d => ({ id: 1, dog_id: 1, date_display: d, slot: 'Morning',
                                status: 'confirmed', is_drop_in: false });
            api.calls[1].reply({ success: true, bookings: [row('Tue 21 Oct')] });
            await api.settle();
            api.calls[0].reply({ success: true, bookings: [row('Mon 20 Oct')] });
            await api.settle();
            return api.pastHtml(1);
        """)
        assert 'Tue 21 Oct' in r
        assert 'Mon 20 Oct' not in r

    def test_clearing_the_filter_drops_a_reply_in_flight(self):
        r = _run("""
            api.makeDog(1, ['2026-11-03|Morning']);
            L.applyDateFilter('2026-10-20');
            L.clearDateFilter();
            api.calls[0].reply({ success: true, bookings: [{ id: 1, dog_id: 1,
                date_display: 'Mon 20 Oct', slot: 'Morning', status: 'confirmed' }] });
            await api.settle();
            return { past: api.pastHtml(1), visible: api.visible(1) };
        """)
        assert r == {'past': '', 'visible': ['2026-11-03|Morning']}

    def test_a_failed_fetch_toasts(self):
        r = _run("""
            api.makeDog(1, []);
            L.applyDateFilter('2026-10-20');
            api.calls[0].reply({ success: false, message: 'Invalid date' });
            await api.settle();
            return api.toasts;
        """)
        assert r == ['Could not load bookings for that day. Please try again.']

    def test_names_are_escaped(self):
        r = _run("""
            api.makeDog(1, []);
            L.applyDateFilter('2026-10-20');
            api.calls[0].reply({ success: true, bookings: [{ id: 1, dog_id: 1,
                date_display: 'Mon 20 Oct', slot: 'Morning', status: 'confirmed',
                walker_name: '<img src=x>', note: '" onmouseover="x' }] });
            await api.settle();
            return api.pastHtml(1);
        """)
        assert '<img' not in r
        assert '&lt;img src=x&gt;' in r
        assert '&quot; onmouseover=&quot;x' in r
