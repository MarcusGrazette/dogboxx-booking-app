import uuid
from collections import OrderedDict
from datetime import date as date_type

from flask import request, render_template, jsonify
from flask_login import login_required, current_user

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import SlotFreeze
from app import db
from app.utils.date_ranges import parse_range, dates_in_range, range_label
from app.utils.activity_log import record_admin_action

# Sanity guard against a fat-fingered multi-year range — mirrors Closure's.
MAX_FREEZE_RANGE_DAYS = 60

SLOT_LABELS = {'Morning': 'Morning', 'Afternoon': 'Afternoon', 'Both': 'Morning & Afternoon'}


def _slots_for_selection(slot_selection):
    """'Morning'/'Afternoon' -> one row; 'Both' -> two rows (Morning + Afternoon)."""
    if slot_selection == 'Both':
        return ('Morning', 'Afternoon')
    return (slot_selection,)


@admin_bp.route("/freezes")
@login_required
@admin_required
def freezes():
    all_freezes = SlotFreeze.query.order_by(SlotFreeze.date).all()
    groups = OrderedDict()
    for f in all_freezes:
        groups.setdefault(f.range_id, []).append(f)
    freeze_groups = sorted(groups.values(), key=lambda members: members[0].date)
    return render_template('admin_freezes.html', freeze_groups=freeze_groups, today=date_type.today())


@admin_bp.route("/freezes", methods=["POST"])
@login_required
@admin_required
def add_freeze():
    data = request.get_json()
    if not data:
        return jsonify(success=False, message="No data received"), 400

    try:
        start, end = parse_range(data, max_days=MAX_FREEZE_RANGE_DAYS)
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400

    slot_selection = data.get('slot')
    if slot_selection not in ('Morning', 'Afternoon', 'Both'):
        return jsonify(success=False, message="Choose Morning, Afternoon, or Both"), 400

    reason = (data.get('reason') or '').strip() or None

    dates = dates_in_range(start, end)
    slots = _slots_for_selection(slot_selection)

    existing = {
        (f.date, f.slot) for f in
        SlotFreeze.query.filter(SlotFreeze.date.in_(dates), SlotFreeze.slot.in_(slots)).all()
    }
    new_pairs = [(d, s) for d in dates for s in slots if (d, s) not in existing]
    skipped_dates = sorted({d.isoformat() for d, s in existing})

    if not new_pairs:
        return jsonify(success=False, message="All of that range is already frozen"), 400

    range_id = uuid.uuid4().hex
    for d, s in new_pairs:
        db.session.add(SlotFreeze(date=d, slot=s, reason=reason, created_by_id=current_user.id, range_id=range_id))

    record_admin_action(
        'slot_freeze', None, 'created', actor_id=current_user.id,
        summary=(
            f"Froze auto-confirm ({SLOT_LABELS[slot_selection]}) for {range_label(dates)}"
            + (f" — {reason}" if reason else "")
        ),
    )
    db.session.commit()

    return jsonify(
        success=True,
        created=len(new_pairs),
        skipped_dates=skipped_dates,
    )


@admin_bp.route("/freezes/range/<range_id>", methods=["DELETE"])
@login_required
@admin_required
def delete_freeze_range(range_id):
    matched = SlotFreeze.query.filter_by(range_id=range_id).all()
    if not matched:
        return jsonify(success=False, message="Freeze not found"), 404

    dates = sorted({f.date for f in matched})
    slots = sorted({f.slot for f in matched})
    slot_label = ' & '.join(slots)
    for f in matched:
        db.session.delete(f)

    record_admin_action(
        'slot_freeze', None, 'removed', actor_id=current_user.id,
        summary=f"Unfroze auto-confirm ({slot_label}) for {range_label(dates)}",
    )
    db.session.commit()
    return jsonify(success=True)
