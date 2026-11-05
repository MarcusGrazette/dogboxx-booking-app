"""FEATURES #95: the standalone /admin/board page and its chart endpoints are
gone; admins assign from the dashboard's Assign modal, which loads the board
from /admin/board-fragment."""
import pytest


def test_assign_modal_board_fragment_renders(logged_in_admin):
    resp = logged_in_admin.get('/admin/board-fragment')
    assert resp.status_code == 200
    assert b'id="board-columns"' in resp.data


@pytest.mark.parametrize('url', [
    '/admin/board',
    '/admin/api/board-chart-data',
    '/admin/api/chart-data',
])
def test_retired_board_urls_are_gone(logged_in_admin, url):
    assert logged_in_admin.get(url).status_code == 404
