"""app.utils.ua.is_mobile_request — the {{ is_mobile }} template flag."""
import pytest

from app.utils.ua import is_mobile_request

IPHONE = ('Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 '
          '(KHTML, like Gecko) Version/27.0.1 Mobile/15E148 Safari/604.1')
ANDROID_TABLET = 'Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 Chrome/130.0 Safari/537.36'
ANDROID_PHONE = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36'
MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/152.0 Safari/537.36'


@pytest.mark.parametrize('ua, expected', [
    (IPHONE, True), (ANDROID_PHONE, True), (ANDROID_TABLET, True), (MAC, False), ('', False),
])
def test_is_mobile_request(app, ua, expected):
    with app.test_request_context(headers={'User-Agent': ua}):
        assert is_mobile_request() is expected


def test_template_flag_switches_drop_in_board_layout(logged_in_admin):
    phone = logged_in_admin.get('/admin/drop-in-board', headers={'User-Agent': IPHONE})
    desktop = logged_in_admin.get('/admin/drop-in-board', headers={'User-Agent': MAC})
    assert b'col-12 mt-3' in phone.data and b'col-12 mt-3' not in desktop.data
