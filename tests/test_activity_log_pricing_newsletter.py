"""
PR 5/5 of the activity-feed expansion — pricing + newsletter call sites in
app/blueprints/admin/views/revenue.py::update_pricing and
.../marketing.py::newsletter, routed through
app/utils/activity_log.py::record_admin_action.

Route-level integration tests: hit the real route via the `client` fixture,
then query ActivityLog to assert one row landed with the expected
entity_type/action/summary substring, mirroring
tests/test_activity_log_dog_walker.py (PR 3/5). One feed-rendering assertion
covers the 'admin' bucket for this PR's call sites.
"""
import datetime

import pytest
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from app import db
from app.models import ActivityLog, PricingConfig, User

TRUNCATE_ORDER = [
    'activity_logs', 'notifications', 'bookings', 'pricing_configs', 'users',
]


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        for t in TRUNCATE_ORDER:
            try:
                db.session.execute(text(f'DELETE FROM {t}'))
            except Exception:
                db.session.rollback()
        db.session.commit()
    yield


@pytest.fixture
def captured_newsletters(monkeypatch):
    """Capture send_newsletter_batch calls instead of hitting Resend."""
    sent = []

    def fake_send_newsletter_batch(subject, html_template, recipients):
        sent.append({'subject': subject, 'html_template': html_template, 'recipients': recipients})
        return {'sent': len(recipients), 'failed': 0}

    monkeypatch.setattr('app.utils.email.send_newsletter_batch', fake_send_newsletter_batch)
    return sent


def _make_admin(email='apn_admin@test.com'):
    u = User(firstname='Admin', lastname='User', email=email, role='walker',
              is_admin=True, active=True,
              hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.commit()
    return u


def _login(flask_client, email):
    return flask_client.post('/auth/login', data={
        'email': email, 'password': 'Testpass1!',
    }, follow_redirects=True)


def _last_log():
    return ActivityLog.query.order_by(ActivityLog.id.desc()).first()


class TestUpdatePricingCreate:

    def test_new_tier_logs_created_row(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email

        _login(client, admin_email)
        client.post('/admin/revenue/pricing', data={
            'price_per_walk': '15.00',
            'double_slot_discount': '2.00',
            'weekly_discount': '5.00',
            'price_per_drop_in': '6.00',
            'effective_from': '2026-10-01',
        })

        with app.app_context():
            tier = PricingConfig.query.filter_by(effective_from=datetime.date(2026, 10, 1)).first()
            assert tier is not None
            row = ActivityLog.query.filter_by(entity_type='pricing', entity_id=tier.id).first()
            assert row is not None
            assert row.action == 'created'
            assert row.changes is None
            assert '2026-10-01' in row.summary


class TestUpdatePricingUpdate:

    def test_existing_tier_edit_logs_diff(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            tier = PricingConfig(
                price_per_walk=10, double_slot_discount=1, weekly_discount=2,
                price_per_drop_in=5, effective_from=datetime.date(2026, 10, 1),
            )
            db.session.add(tier)
            db.session.commit()
            tier_id = tier.id

        _login(client, admin_email)
        client.post('/admin/revenue/pricing', data={
            'price_per_walk': '12.00',
            'double_slot_discount': '1.00',
            'weekly_discount': '2.00',
            'price_per_drop_in': '5.00',
            'effective_from': '2026-10-01',
        })

        with app.app_context():
            row = ActivityLog.query.filter_by(entity_type='pricing', entity_id=tier_id).first()
            assert row is not None
            assert row.action == 'updated'
            old, new = row.changes['price_per_walk']
            assert str(old).startswith('10')
            assert float(new) == 12.0

    def test_no_op_edit_logs_nothing(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email
            tier = PricingConfig(
                price_per_walk=10, double_slot_discount=1, weekly_discount=2,
                price_per_drop_in=5, effective_from=datetime.date(2026, 10, 1),
            )
            db.session.add(tier)
            db.session.commit()

        _login(client, admin_email)
        client.post('/admin/revenue/pricing', data={
            'price_per_walk': '10.00',
            'double_slot_discount': '1.00',
            'weekly_discount': '2.00',
            'price_per_drop_in': '5.00',
            'effective_from': '2026-10-01',
        })

        with app.app_context():
            assert ActivityLog.query.count() == 0


class TestNewsletterSendLogsRow:

    def test_send_logs_newsletter_row_with_counts(self, app, client, captured_newsletters):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email

        _login(client, admin_email)
        client.post('/admin/newsletter', data={
            'subject': 'October Update',
            'html_body': '<p>Body</p>',
        })

        with app.app_context():
            row = _last_log()
            assert row is not None
            assert row.entity_type == 'newsletter'
            assert row.entity_id is None
            assert row.action == 'sent'
            assert 'October Update' in row.summary
            assert '0 sent' in row.summary or 'sent' in row.summary

    def test_validation_error_logs_nothing(self, app, client, captured_newsletters):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email

        _login(client, admin_email)
        client.post('/admin/newsletter', data={
            'subject': '', 'html_body': '',
        })

        with app.app_context():
            assert ActivityLog.query.count() == 0
        assert captured_newsletters == []


class TestActivityFeedRendersPricingNewsletterRows:

    def test_pricing_update_appears_under_admin_bucket(self, app, client):
        with app.app_context():
            admin = _make_admin()
            admin_email = admin.email

        _login(client, admin_email)
        client.post('/admin/revenue/pricing', data={
            'price_per_walk': '15.00',
            'double_slot_discount': '2.00',
            'weekly_discount': '5.00',
            'price_per_drop_in': '6.00',
            'effective_from': '2026-10-01',
        })

        month = datetime.date.today().strftime('%Y-%m')
        resp = client.get(f'/admin/activity?month={month}')
        assert resp.status_code == 200
        assert b'data-activity="admin"' in resp.data
        assert b'2026-10-01' in resp.data
