"""
Tests for app.utils.http.wants_json() — the shared JSON-detection helper
introduced in audit fix e0a155f (Block 7 L22).

The helper is called from four places: the 429 handler, the 500 handler, the
CSRF error handler, and notifications.mark_all. Each had its own divergent
implementation; this is the only one. Tests assert the matrix that mattered:
fetch()'s default Accept: */* with no X-Requested-With was the bug case
(wants_json() must return True; the deleted notifications._wants_json()
returned False because accept_mimetypes.best resolves */* to itself, not to
application/json).
"""
import pytest

from app.utils.http import wants_json


@pytest.fixture
def make_request(app):
    """Build a request and return the result of wants_json() for its headers."""
    def _build(accept=None, xhr=False, content_type=None):
        headers = {}
        if accept is not None:
            headers['Accept'] = accept
        if xhr:
            headers['X-Requested-With'] = 'XMLHttpRequest'
        kwargs = {'headers': headers}
        if content_type is not None:
            kwargs['content_type'] = content_type
        with app.test_request_context('/x', **kwargs):
            return wants_json()
    return _build


def test_wants_json_for_browser_html_navigation(make_request):
    # Default browser GET (Accept: text/html,...). Should be HTML, not JSON.
    assert make_request(
        accept='text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    ) is False


def test_wants_json_for_fetch_with_xhr_header(make_request):
    # Even with Accept: text/html, an XHR caller expects JSON.
    assert make_request(
        accept='text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        xhr=True,
    ) is True


def test_wants_json_for_fetch_default_accept_star_star(make_request):
    # The L22 bug case: fetch()'s default Accept: */*, no XHR header.
    # Old _wants_json() returned False; new helper returns True.
    assert make_request(accept='*/*') is True


def test_wants_json_for_explicit_json_accept(make_request):
    assert make_request(accept='application/json') is True


def test_wants_json_for_explicit_json_body(make_request):
    # POST with Content-Type: application/json — is_json is True regardless of Accept.
    assert make_request(
        accept='text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        content_type='application/json',
    ) is True


def test_wants_json_with_no_accept_header_at_all(make_request):
    # Some clients (curl, old proxies) omit Accept. The helper follows the
    # original 429 handler's logic: if Accept is empty or doesn't explicitly
    # request text/html, we treat the caller as JSON-capable. This is also a
    # safe default for API clients that send no Accept header at all.
    assert make_request(accept=None) is True
