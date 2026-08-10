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


def test_500_handler_logs_warning(app, client, crash_next_request, caplog):
    """The 500 handler must emit a WARNING with the exception so ops can see
    which path failed, not just 'we got a 500'."""
    caplog.set_level(logging.WARNING)

    resp = _request_expecting_500(
        client, app,
        headers={'Accept': '*/*'}
    )
    assert resp.status_code == 500

    assert any(
        r.levelno == logging.WARNING
        and f'500 on GET {_ERROR_TEST_PATH}' in r.message
        for r in caplog.records
    )
