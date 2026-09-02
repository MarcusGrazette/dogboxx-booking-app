"""Foundation PR — app.utils.admin_audit chokepoint.

Unit tests of record_admin_action / diff_fields in isolation, mirroring
TestTransitionHelpers in test_booking_status_log.py. Route-wiring integration
tests land per-PR as each call site is added (see the activity-feed expansion
plan); this file only covers the chokepoint itself plus the two documented
edge cases that motivated its design: Decimal/date JSON serialization and
PII redaction.
"""
import datetime
import uuid
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import AdminActionLog, User
from app.utils.admin_audit import record_admin_action, diff_fields, REDACTED_FIELDS, RICH_TEXT_FIELDS


class _Obj:
    """Minimal stand-in for a loaded model instance — diff_fields only needs
    getattr access to current field values."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def make_user(app):
    email = f'aa_{uuid.uuid4().hex[:12]}@test.com'
    u = User(firstname='Test', lastname='User', email=email, role='walker',
             is_admin=True, active=True,
             hashed_password=generate_password_hash('Testpass1!'))
    db.session.add(u)
    db.session.commit()
    return u


class TestRecordAdminAction:

    def test_queues_row_with_expected_fields(self, app):
        with app.app_context():
            admin = make_user(app)
            row = record_admin_action(
                'client', 42, 'created', actor_id=admin.id,
                summary='Added client Jane Smith', changes=None,
            )
            db.session.commit()
            assert row.id is not None
            assert row.entity_type == 'client'
            assert row.entity_id == 42
            assert row.action == 'created'
            assert row.actor_id == admin.id
            assert row.summary == 'Added client Jane Smith'
            assert row.changes is None

    def test_entity_id_may_be_null(self, app):
        """Newsletter sends have no row to point at."""
        with app.app_context():
            admin = make_user(app)
            row = record_admin_action(
                'newsletter', None, 'sent', actor_id=admin.id,
                summary='Newsletter sent to 42 clients',
            )
            db.session.commit()
            assert row.entity_id is None


class TestDiffFields:

    def test_only_changed_fields_are_reported(self, app):
        before = {'a': 1, 'b': 2}
        obj = _Obj(a=1, b=3)
        changes = diff_fields(before, obj, ['a', 'b'])
        assert changes == {'b': [2, 3]}

    def test_no_changes_returns_empty_dict(self, app):
        before = {'a': 1}
        obj = _Obj(a=1)
        assert diff_fields(before, obj, ['a']) == {}

    def test_redacted_fields_never_store_real_values(self, app):
        """PII (address, pickup instructions) shows as changed but the actual
        values must never land in `changes` — it's a permanent store."""
        assert 'street_address' in REDACTED_FIELDS
        assert 'pickup_instructions' in REDACTED_FIELDS
        before = {'street_address': '1 Old Road', 'name': 'Rex'}
        obj = _Obj(street_address='2 New Road', name='Rex')
        changes = diff_fields(before, obj, ['street_address', 'name'])
        assert changes == {'street_address': ['(redacted)', '(redacted)']}
        assert 'Old Road' not in str(changes)
        assert 'New Road' not in str(changes)

    def test_decimal_equal_values_with_different_representation_are_not_a_diff(self, app):
        """Decimal('12.0') == Decimal('12.00') is True under native comparison
        but their str()s differ ('12.0' vs '12.00') — diff_fields compares
        native values, not _jsonify_value-normalized ones, specifically to
        avoid a false-positive diff here. See the docstring's contract note:
        `before` must hold native types captured via getattr(), not a
        serialized/string snapshot."""
        before = {'price_per_walk': Decimal('12.00')}
        obj = _Obj(price_per_walk=Decimal('12.0'))
        assert diff_fields(before, obj, ['price_per_walk']) == {}

    def test_decimal_and_date_values_survive_a_real_commit(self, app):
        """db.JSON serializes via stdlib json.dumps, which raises TypeError on
        Decimal and date/datetime. PricingConfig's price fields are Numeric
        (Decimal) and Dog.date_of_birth is a Date — diff_fields must normalize
        both before they reach the changes column, verified end-to-end through
        an actual commit, not just the in-memory dict shape."""
        with app.app_context():
            admin = make_user(app)
            before = {
                'price_per_walk': Decimal('12.00'),
                'date_of_birth': datetime.date(2020, 1, 1),
            }
            obj = _Obj(
                price_per_walk=Decimal('14.50'),
                date_of_birth=datetime.date(2021, 6, 1),
            )
            changes = diff_fields(before, obj, ['price_per_walk', 'date_of_birth'])
            assert changes == {
                'price_per_walk': ['12.00', '14.50'],
                'date_of_birth': ['2020-01-01', '2021-06-01'],
            }

            row = record_admin_action(
                'pricing', 1, 'updated', actor_id=admin.id,
                summary='Pricing updated', changes=changes,
            )
            db.session.commit()  # would raise TypeError pre-fix

            fetched = db.session.get(AdminActionLog, row.id)
            assert fetched.changes['price_per_walk'] == ['12.00', '14.50']
            assert fetched.changes['date_of_birth'] == ['2020-01-01', '2021-06-01']

    def test_rich_text_field_with_reserialized_but_equivalent_html_is_not_a_diff(self, app):
        """Regression for a false positive found smoke-testing PR 3 (2026-08-31):
        edit_client/update_dog always resubmit pickup_instructions through a
        Quill editor even when the admin never touched it, and Quill's HTML
        re-serialization isn't guaranteed byte-identical to what was stored
        (attribute order, quoting, inter-tag whitespace) — sanitize_rich_text()
        itself is idempotent, so raw-string comparison was flagging this noise
        as a real change on nearly every edit_client/update_dog save."""
        assert 'pickup_instructions' in RICH_TEXT_FIELDS
        before = {'pickup_instructions': '<p>Ring the doorbell twice</p>'}
        # Same visible content, different byte-level serialization (attribute
        # order/quoting aside — here just inter-tag whitespace/self-closing tag
        # style, the class of noise Quill's round-trip introduces).
        obj = _Obj(pickup_instructions='<p>Ring the doorbell twice</p>\n')
        assert diff_fields(before, obj, ['pickup_instructions']) == {}

    def test_rich_text_field_with_real_content_change_is_still_a_diff(self, app):
        """The plain-text equality check must not swallow genuine edits."""
        before = {'pickup_instructions': '<p>Ring the doorbell twice</p>'}
        obj = _Obj(pickup_instructions='<p>Use the side gate</p>')
        changes = diff_fields(before, obj, ['pickup_instructions'])
        assert changes == {'pickup_instructions': ['(redacted)', '(redacted)']}
