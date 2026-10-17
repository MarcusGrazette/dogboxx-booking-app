"""resume-refresh.js (reload a page left hidden for an hour) is loaded on
every page of both layouts — the walker pickup list (layout.html) and the
admin screens (admin_layout.html)."""

SCRIPT = 'js/resume-refresh.js'


def test_walker_layout_loads_resume_refresh(logged_in_walker):
    html = logged_in_walker.get('/walker/pickups/2026-10-15').get_data(as_text=True)
    assert SCRIPT in html


def test_admin_layout_loads_resume_refresh(logged_in_admin):
    html = logged_in_admin.get('/admin/activity').get_data(as_text=True)
    assert SCRIPT in html


def test_script_is_served(client):
    resp = client.get('/static/' + SCRIPT)
    assert resp.status_code == 200
    assert b'visibilitychange' in resp.data
