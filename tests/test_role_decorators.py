"""
Regression tests for app.utils.decorators role gates (admin_required,
walker_required). Audit e0a155f consolidated JSON detection into the shared
wants_json() helper; these decorators were the last place still using the
partial pattern `request.is_json or X-Requested-With` without the Accept-header
branch. The migration means a fetch caller sending Accept: */* (no
X-Requested-With) now gets a JSON 403 instead of a 302 redirect to /client/index.
"""
import pytest


# Default browser fetch headers: no X-Requested-With, Accept: */*.
_FETCH_HEADERS = {'Accept': '*/*'}


@pytest.mark.parametrize('endpoint', ['/admin/weekly-overview', '/admin/api/chart-data'])
def test_admin_required_returns_json_for_fetch_caller_without_xhr(
    app, client, client_user, endpoint
):
    """A non-admin fetch() caller must receive a JSON 403, not a redirect."""
    with app.app_context():
        from tests.conftest import login
        login(client, client_user.email)

    resp = client.get(endpoint, headers=_FETCH_HEADERS, follow_redirects=False)

    assert resp.status_code == 403
    assert resp.is_json
    body = resp.get_json()
    assert body == {'success': False, 'message': 'Forbidden'}


@pytest.mark.parametrize('endpoint', ['/walker/pickups', '/walker/schedule'])
def test_walker_required_returns_json_for_fetch_caller_without_xhr(
    app, client, client_user, endpoint
):
    """A non-walker fetch() caller must receive a JSON 403, not a redirect."""
    with app.app_context():
        from tests.conftest import login
        login(client, client_user.email)

    resp = client.get(endpoint, headers=_FETCH_HEADERS, follow_redirects=False)

    assert resp.status_code == 403
    assert resp.is_json
    body = resp.get_json()
    assert body == {'success': False, 'message': 'Forbidden'}
