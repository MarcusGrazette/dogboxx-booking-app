"""
Tests for the global error handlers in app/__init__.py — audit e0a155f added
a JSON branch to the 500 handler so fetch() callers get res.json()-able errors
instead of an HTML page that breaks .json(). The shared wants_json() helper
itself is covered by tests/test_wants_json.py.
"""
import logging


_ERROR_TEST_PATH = '/__test_error_handlers_500__'


def _request_expecting_500(client, app, headers):
    """Hit the crash route with the real 500 handler enabled, then restore state."""
    app.testing = False
    try:
        return client.get(_ERROR_TEST_PATH, headers=headers)
    finally:
        app.testing = True


def test_500_returns_json_for_fetch_caller(app, client, crash_next_request):
    """A 500 on a fetch() call (Accept: */*) must return JSON, not HTML."""
    resp = _request_expecting_500(
        client, app,
        headers={'Accept': '*/*'}
    )
    assert resp.status_code == 500
    assert resp.is_json
    body = resp.get_json()
    assert body['success'] is False
    assert 'message' in body


def test_500_returns_html_for_browser_navigation(app, client, crash_next_request):
    """A 500 on a normal page navigation should still render 500.html."""
    resp = _request_expecting_500(
        client, app,
        headers={'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'}
    )
    assert resp.status_code == 500
    assert resp.is_json is False
    # 500.html is the site's generic error page; we just need to know it
    # rendered HTML, not a JSON body.
    assert b'500' in resp.data or b'Something went wrong' in resp.data


def test_500_logs_exception_once(app, client, crash_next_request, caplog):
    """Flask's own handle_exception() logs the path/method/traceback at ERROR
    before dispatching to our handler (see internal_error's comment) — assert
    that still happens, and that our handler doesn't add a second, duplicate
    log line on top of it."""
    caplog.set_level(logging.INFO)

    resp = _request_expecting_500(
        client, app,
        headers={'Accept': '*/*'}
    )
    assert resp.status_code == 500

    matches = [
        r for r in caplog.records
        if r.levelno == logging.ERROR and f'Exception on {_ERROR_TEST_PATH}' in r.message
    ]
    assert len(matches) == 1
