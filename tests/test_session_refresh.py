"""Session expiry refresh — at most one session write per user per day.

SESSION_REFRESH_EACH_REQUEST is False (config.py), so Flask-Session only writes
the sessions row when the session is modified. app/__init__.py::_touch_session_daily
modifies it on a user's first request of each (London) day, which keeps the
14-day expiry sliding without an UPSERT + commit on every request.
"""
import contextlib
import re

from sqlalchemy import event

from app import db
from app.utils.dates import local_today


_SESSION_WRITE = re.compile(r'\s*(INSERT\s+INTO|UPDATE)\s+"?sessions"?\b', re.IGNORECASE)


@contextlib.contextmanager
def _session_table_writes():
    """Collect INSERT/UPDATE statements against the sessions table."""
    writes = []

    def _capture(conn, cursor, statement, params, context, executemany):
        if _SESSION_WRITE.match(statement):
            writes.append(statement)

    event.listen(db.engine, 'before_cursor_execute', _capture)
    try:
        yield writes
    finally:
        event.remove(db.engine, 'before_cursor_execute', _capture)


class TestDailySessionTouch:

    def test_login_stamps_today(self, logged_in_admin):
        with logged_in_admin.session_transaction() as sess:
            assert sess.get('_touched') == local_today().isoformat()

    def test_repeat_request_same_day_does_not_write_session(self, logged_in_admin):
        with _session_table_writes() as writes:
            resp = logged_in_admin.get('/notifications/unread-count')
        assert resp.status_code == 200
        assert writes == []

    def test_first_request_of_a_new_day_refreshes_session(self, logged_in_admin):
        with logged_in_admin.session_transaction() as sess:
            sess['_touched'] = '2000-01-01'

        with _session_table_writes() as writes:
            resp = logged_in_admin.get('/notifications/unread-count')
        assert resp.status_code == 200
        assert len(writes) == 1

        with logged_in_admin.session_transaction() as sess:
            assert sess['_touched'] == local_today().isoformat()

    def test_anonymous_request_is_not_touched(self, client):
        client.get('/auth/login')
        with client.session_transaction() as sess:
            assert '_touched' not in sess
