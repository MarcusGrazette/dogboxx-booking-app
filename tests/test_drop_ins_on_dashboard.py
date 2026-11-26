"""
FEATURES #94: drop-ins are managed from the dashboard — the Assign modal's
Walks | Drop-ins switch, an amber calendar icon for drop-ins to assign
(tests/test_dashboard_slot_stats.py), and a house badge on the Dashboard
sidebar link. The separate drop-in page is retired; its address redirects.
"""
import re

from app import db
from app.models import Booking, Notification, ServiceType
from app.utils.booking_notify import notify_created
from app.utils.dates import local_today


def _drop_in_service():
    st = ServiceType(name='Drop In', slug=ServiceType.DROP_IN, capacity_model='walker_assigned',
                     slot_type='morning_afternoon', requires_walker=True,
                     default_max_capacity=6, active=True)
    db.session.add(st)
    db.session.commit()
    return st


def _drop_in(user, dog, status='requested', day=None):
    b = Booking(user_id=user.id, dog_id=dog.id, service_type_id=_drop_in_service().id,
                date=day or local_today(), slot='Morning', status=status)
    db.session.add(b)
    db.session.commit()
    return b


def _dashboard_link(html):
    m = re.search(r'<a href="/admin/"\s+class="nav-link[^"]*">(.*?)</a>', html, re.S)
    assert m, 'Dashboard sidebar link not found'
    return m.group(1)


class TestSidebar:

    def test_dashboard_link_shows_drop_ins_to_assign(self, logged_in_admin, client_user, dog):
        _drop_in(client_user, dog)
        html = logged_in_admin.get('/admin/').get_data(as_text=True)

        link = _dashboard_link(html)
        assert 'id="pending-badge-drop-ins"' in link
        assert 'bi-house-door' in link
        assert re.search(r'id="pending-badge-drop-ins"[^>]*>.*?1\s*</span>', link, re.S)
        assert 'Drop Ins' not in html

    def test_badge_hidden_when_nothing_to_assign(self, logged_in_admin, client_user, dog):
        _drop_in(client_user, dog, status='confirmed')
        link = _dashboard_link(logged_in_admin.get('/admin/').get_data(as_text=True))
        assert re.search(r'id="pending-badge-drop-ins"[^>]*display:\s*none', link, re.S)


class TestRetiredPage:

    def test_old_address_redirects_to_dashboard(self, logged_in_admin):
        resp = logged_in_admin.get('/admin/drop-in-board')
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/admin/')

    def test_page_only_calendar_endpoint_is_gone(self, logged_in_admin):
        assert logged_in_admin.get('/admin/calendar_data/2030/1').status_code == 404

    def test_dashboard_assign_modal_has_the_switch(self, logged_in_admin):
        html = logged_in_admin.get('/admin/').get_data(as_text=True)
        assert 'data-assign-view="walks"' in html
        assert 'data-assign-view="drop-ins"' in html


class TestNotificationLinks:

    def test_same_day_drop_in_links_to_that_day(self, app, admin_user, client_user, dog):
        b = _drop_in(client_user, dog)
        notify_created([b], actor=client_user, by_admin=False, same_day=True)
        db.session.commit()

        n = Notification.query.filter_by(recipient_id=admin_user.id).one()
        assert n.link == f'/admin?date={b.date.isoformat()}'
