"""Admin board JS (app/static/js/admin-board-core.js) under Node.

The board moves cards optimistically and loads each date with a fetch, so
replies can arrive late or out of order. These tests drive the real script
against a small fake DOM and a fetch whose replies the test releases by hand:

- an older date's board load must not overwrite a newer one
- a failed assign/unassign puts back only that booking: it must not undo
  another move made meanwhile, nor repaint a date the admin has left
- toast messages are plain text (server messages can contain names)
"""
import json
import pathlib
import shutil
import subprocess

import pytest

BOARD_JS = (pathlib.Path(__file__).resolve().parent.parent
            / 'app' / 'static' / 'js' / 'admin-board-core.js')

pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')

# Fake DOM, fetch and bootstrap, then the board script, then the scenario
# (argv[2], a JS async function body). `board` is the board, `api` holds the
# helpers below; the scenario's return value is printed as JSON.
_HARNESS = r"""
const fs = require('fs');

class FakeElement {
    constructor(tag) {
        this.tagName = tag; this.className = ''; this.dataset = {}; this.style = {};
        this.children = []; this.parent = null; this.listeners = {};
        this._html = ''; this.textContent = '';
        const el = this;
        this.classList = {
            add: c => { if (!el.hasClass(c)) el.className += ' ' + c; },
            remove: c => { el.className = el.className.split(/\s+/).filter(x => x !== c).join(' '); },
            toggle: (c, on) => on ? el.classList.add(c) : el.classList.remove(c),
            contains: c => el.hasClass(c),
        };
    }
    hasClass(c) { return this.className.split(/\s+/).includes(c); }
    set innerHTML(v) { this._html = v; this.children.forEach(ch => ch.parent = null); this.children = []; }
    get innerHTML() { return this._html; }
    appendChild(ch) { ch.parent = this; this.children.push(ch); return ch; }
    removeChild(ch) { this.children = this.children.filter(x => x !== ch); ch.parent = null; }
    get parentNode() { return this.parent; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    fire(type) {
        const ev = { target: { closest: () => null }, stopPropagation() {} };
        (this.listeners[type] || []).forEach(fn => fn.call(this, ev));
    }
    walk(fn) { fn(this); this.children.forEach(ch => ch.walk(fn)); }
    // Real queries parse innerHTML; here a class selector finds a matching
    // child, else a detached stand-in (card buttons are only in innerHTML).
    querySelector(sel) {
        let hit = null;
        if (sel.startsWith('.')) this.walk(el => { if (!hit && el !== this && el.hasClass(sel.slice(1))) hit = el; });
        return hit || (this._stub ||= {})[sel] || (this._stub[sel] = new FakeElement('stub'));
    }
    querySelectorAll() { return []; }
    getBoundingClientRect() { return { left: 0 }; }
    // Everything the user would read: markup set via innerHTML plus children.
    text() { let t = this._html + this.textContent; this.children.forEach(ch => t += ch.text()); return t; }
}

const byId = {};
global.document = {
    body: new FakeElement('body'),
    querySelector: () => null,
    getElementById: id => byId[id] || (byId[id] = new FakeElement('div')),
    createElement: tag => new FakeElement(tag),
};
global.window = global;
window.matchMedia = () => ({ matches: true });   // reduced motion: no column animation
global.bootstrap = { Modal: class { show() {} hide() {} } };
global.refreshPendingBadges = () => {};
console.error = () => {};

// fetch replies wait until the scenario releases them.
const calls = [];
global.fetch = (url, opts) => new Promise(resolve => {
    calls.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null,
                 reply: data => resolve({ json: async () => data }) });
});
const settle = () => new Promise(r => setTimeout(r, 0));

const columns = () => document.getElementById('board-columns');
const api = {
    calls, settle,
    lastCall: () => calls[calls.length - 1],
    boardText: () => columns().text(),
    toastText: () => document.body.text(),
    card(id) {
        let hit = null;
        columns().walk(el => { if (!hit && el.hasClass('board-card') && String(el.dataset.id) === String(id)) hit = el; });
        return hit;
    },
    // 'pending', the walker id whose column holds the card, or null.
    whereIs(id) {
        let el = api.card(id);
        while (el) {
            if (el.hasClass('pending-column')) return 'pending';
            if (el.dataset.walkerId !== undefined && el.hasClass('board-column')) return Number(el.dataset.walkerId);
            el = el.parent;
        }
        return null;
    },
    lane(walkerId, slot) {
        let hit = null;
        columns().walk(el => {
            if (!hit && el.hasClass('lane-cards') && String(el.dataset.walkerId) === String(walkerId)
                && el.dataset.slot === slot) hit = el.parent;
        });
        return hit;
    },
    async assign(id, walkerId, slot) {
        api.card(id).fire('click');
        api.lane(walkerId, slot).fire('click');
        await settle();
    },
};

eval(fs.readFileSync(process.argv[1], 'utf8'));
const board = createBoard({
    assignUrl: '/assign', boardDataUrl: '/board/DATE',
    emptyIcon: 'bi-x', emptyText: 'Nothing', declineLabel: 'walk',
});

(async () => {
    const scenario = new (Object.getPrototypeOf(async function () {}).constructor)('board', 'api', process.argv[2]);
    console.log(JSON.stringify(await scenario(board, api)));
    process.exit(0);   // don't wait out the toasts' removal timers
})().catch(e => { process.stderr.write(String(e.stack || e)); process.exit(1); });
"""


def _run(scenario):
    out = subprocess.run(
        ['node', '-e', _HARNESS, str(BOARD_JS), scenario],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _booking(id, dog, walker_id=None, slot='Morning'):
    return {'id': id, 'dog_name': dog, 'owner_name': 'Owner', 'slot': slot,
            'status': 'requested', 'walker_id': walker_id, 'pickup_order': 1 if walker_id else None,
            'dog_pic': None, 'client_notes': None}


WALKER = {'id': 7, 'name': 'Wendy', 'available_slots': ['Morning', 'Afternoon'],
          'unavailable_slots': []}


def _board(*bookings):
    return {'success': True, 'max_capacity': 6, 'frozen_slots': [], 'walkers': [WALKER],
            'pending': [b for b in bookings if not b['walker_id']],
            'assigned': [b for b in bookings if b['walker_id']]}


# Board for Monday: Alfie and Bella waiting. Tuesday: Tilly waiting.
MONDAY = json.dumps(_board(_booking(1, 'Alfie'), _booking(2, 'Bella')))
TUESDAY = json.dumps(_board(_booking(9, 'Tilly')))


def test_older_board_load_cannot_overwrite_newer_date():
    got = _run(f"""
        board.setDate('2026-11-02');
        board.setDate('2026-11-03');
        api.calls[1].reply({TUESDAY});      // Tuesday answers first...
        await api.settle();
        api.calls[0].reply({MONDAY});       // ...then the stale Monday reply lands
        await api.settle();
        return api.boardText();
    """)
    assert 'Tilly' in got
    assert 'Alfie' not in got


def test_stale_load_failure_does_not_replace_newer_board():
    got = _run(f"""
        board.setDate('2026-11-02');
        board.setDate('2026-11-03');
        api.calls[1].reply({TUESDAY});
        await api.settle();
        api.calls[0].reply({{success: false}});
        await api.settle();
        return document.getElementById('board-placeholder').innerHTML;
    """)
    assert 'Could not load' not in got


def test_failed_assign_does_not_undo_a_later_assign():
    got = _run(f"""
        board.setDate('2026-11-02');
        api.calls[0].reply({MONDAY});
        await api.settle();
        await api.assign(1, 7, 'Morning');  // Alfie → Wendy, reply held
        await api.assign(2, 7, 'Morning');  // Bella → Wendy, reply held
        api.calls[2].reply({{success: true}});
        await api.settle();
        api.calls[1].reply({{success: false, message: 'Alfie was cancelled'}});
        await api.settle();
        return {{alfie: api.whereIs(1), bella: api.whereIs(2)}};
    """)
    assert got == {'alfie': 'pending', 'bella': 7}


def test_failed_assign_after_changing_date_keeps_new_date():
    got = _run(f"""
        board.setDate('2026-11-02');
        api.calls[0].reply({MONDAY});
        await api.settle();
        await api.assign(1, 7, 'Morning');  // reply held
        board.setDate('2026-11-03');
        api.calls[2].reply({TUESDAY});
        await api.settle();
        api.calls[1].reply({{success: false, message: 'nope'}});
        await api.settle();
        return api.boardText();
    """)
    assert 'Tilly' in got
    assert 'Alfie' not in got


def test_failed_unassign_puts_only_that_booking_back():
    monday = json.dumps(_board(_booking(1, 'Alfie', walker_id=7), _booking(2, 'Bella')))
    got = _run(f"""
        board.setDate('2026-11-02');
        api.calls[0].reply({monday});
        await api.settle();
        api.card(1).fire('click');          // select, then click again to unassign
        api.card(1).fire('click');
        await api.settle();
        await api.assign(2, 7, 'Morning');
        api.calls[2].reply({{success: true}});
        await api.settle();
        api.calls[1].reply({{success: false, message: 'nope'}});
        await api.settle();
        return {{alfie: api.whereIs(1), bella: api.whereIs(2), unassign: api.calls[1].body}};
    """)
    assert got['unassign']['walker_id'] is None
    assert got['alfie'] == 7
    assert got['bella'] == 7


def test_toast_shows_server_message_as_text():
    got = _run(f"""
        board.setDate('2026-11-02');
        api.calls[0].reply({MONDAY});
        await api.settle();
        await api.assign(1, 7, 'Morning');
        api.calls[1].reply({{success: false, message: '<img src=x> is booked'}});
        await api.settle();
        return api.toastText();
    """)
    assert '&lt;img src=x&gt; is booked' in got
    assert '<img' not in got
