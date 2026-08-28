"""Admin-action audit chokepoint.

Every admin-initiated mutation outside the booking lifecycle (client/dog/walker
CRUD, schedule edits, pricing changes, newsletter sends) should route through
this module so the activity feed can't silently drift out of sync with reality
— the same guarantee app/utils/booking_status.py gives bookings.

`record_admin_action` queues an AdminActionLog row on the session; it does
**not** commit. The caller keeps ownership of the transaction. Unlike
BookingStatusChange, `summary` must be a fully-rendered string built *before*
any delete the caller is about to perform — the activity feed reads `summary`
directly rather than reconstructing it from live joins, so the row still
renders correctly after the underlying entity is gone.
"""

from decimal import Decimal
from datetime import date, datetime

from app.models import db, AdminActionLog

# Fields whose values are never persisted in `changes`, even though we still
# want to record *that* they changed. `changes` is a permanent store, not a
# transient view — diffing these naively would turn data admins can already
# see today (street addresses, Quill-authored pickup instructions, which are
# a real place a client puts a door code) into a durable historical record of
# it. One module-level set so extending it later is a one-line change.
REDACTED_FIELDS = {
    'street_address', 'city', 'state', 'postal_code', 'country', 'maps_url',
    'pickup_instructions',
}


def record_admin_action(entity_type, entity_id, action, *, actor_id, summary, changes=None):
    """Queue an AdminActionLog row. Does not commit."""
    row = AdminActionLog(entity_type=entity_type, entity_id=entity_id, action=action,
                          actor_id=actor_id, summary=summary, changes=changes)
    db.session.add(row)
    return row


def _jsonify_value(v):
    """Normalize a field value so it survives db.JSON's stdlib json.dumps.
    Decimal and date/datetime are not JSON-serializable by default and both
    occur in fields this module diffs (PricingConfig's Numeric columns,
    Dog.date_of_birth) — convert them explicitly rather than letting the
    commit raise a TypeError."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def diff_fields(before: dict, obj, fields: list) -> dict:
    """Compare a pre-mutation snapshot dict to an object's current field values.

    Returns {field: [old, new]} for only the fields that actually changed.
    Values are passed through _jsonify_value. A field in REDACTED_FIELDS that
    changed is stored as ['(redacted)', '(redacted)'] rather than its real
    values — the key still appears so the diff isn't silently incomplete.
    """
    changes = {}
    for field in fields:
        old = before.get(field)
        new = getattr(obj, field)
        if old == new:
            continue
        if field in REDACTED_FIELDS:
            changes[field] = ['(redacted)', '(redacted)']
        else:
            changes[field] = [_jsonify_value(old), _jsonify_value(new)]
    return changes
