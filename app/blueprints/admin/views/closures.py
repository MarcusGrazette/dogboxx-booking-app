import uuid
from collections import OrderedDict
from datetime import date as date_type

from flask import request, render_template, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
import logging

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import Booking, Closure
from app import db
from app.utils.booking_notify import notify_cancelled
from app.utils.booking_status import transition_booking
from app.utils.date_ranges import parse_range, dates_in_range, range_label

ACTIVE_STATUSES = ('requested', 'confirmed', 'waitlisted')
# Sanity guard against a fat-fingered multi-year range wiping out the calendar.
MAX_CLOSURE_RANGE_DAYS = 60


@admin_bp.route("/closures")
@login_required
@admin_required
def closures():
    all_closures = Closure.query.order_by(Closure.date).all()
    groups = OrderedDict()
    for c in all_closures:
        groups.setdefault(c.range_id, []).append(c)
    closure_groups = sorted(groups.values(), key=lambda members: members[0].date)
    return render_template('admin_closures.html', closure_groups=closure_groups, today=date_type.today())


@admin_bp.route("/closures/preview")
@login_required
@admin_required
def closures_preview():
    try:
        start, end = parse_range(request.args, max_days=MAX_CLOSURE_RANGE_DAYS)
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400

    dates = dates_in_range(start, end)
    existing_dates = {c.date for c in Closure.query.filter(Closure.date.in_(dates)).all()}
    new_dates = [d for d in dates if d not in existing_dates]
    conflict_dates = [d for d in dates if d in existing_dates]

    bookings = []
    if new_dates:
        bookings = (Booking.query
                    .filter(Booking.date.in_(new_dates), Booking.status.in_(ACTIVE_STATUSES))
                    .options(joinedload(Booking.dog), joinedload(Booking.user))
                    .order_by(Booking.date)
                    .all())

    return jsonify(
        success=True,
        total_days=len(dates),
        new_days=len(new_dates),
        conflict_dates=[d.isoformat() for d in conflict_dates],
        count=len(bookings),
        bookings=[{
            'dog':    b.dog.name if b.dog else '?',
            'owner':  f"{b.user.firstname} {b.user.lastname}" if b.user else '?',
            'date':   b.date.strftime('%-d %b'),
            'slot':   b.slot,
            'status': b.status,
        } for b in bookings],
    )


@admin_bp.route("/closures", methods=["POST"])
@login_required
@admin_required
def add_closure():
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, message="No data received"), 400

        try:
            start, end = parse_range(data, max_days=MAX_CLOSURE_RANGE_DAYS)
        except ValueError as e:
            return jsonify(success=False, message=str(e)), 400

        reason = (data.get('reason') or '').strip() or None

        dates = dates_in_range(start, end)
        existing_dates = {c.date for c in Closure.query.filter(Closure.date.in_(dates)).all()}
        new_dates = [d for d in dates if d not in existing_dates]
        skipped_dates = [d for d in dates if d in existing_dates]

        if not new_dates:
            return jsonify(success=False, message="All dates in that range are already closed"), 400

        # One range_id ties together the per-date rows created by this action so
        # the admin list and activity feed can collapse them into one entry.
        range_id = uuid.uuid4().hex
        for d in new_dates:
            db.session.add(Closure(date=d, reason=reason, created_by_id=current_user.id, range_id=range_id))

        bookings = Booking.query.filter(
            Booking.date.in_(new_dates),
            Booking.status.in_(ACTIVE_STATUSES)
        ).options(
            joinedload(Booking.dog),
            joinedload(Booking.service_type),
            joinedload(Booking.walker),
        ).all()

        # One batch_id ties together every cancellation caused by this closure
        # (single date or range) so the activity feed can cluster them.
        batch_id = uuid.uuid4().hex
        body_text = f"DogBoxx is closed {range_label(new_dates)}" + (f" — {reason}." if reason else ".")

        # Each owner gets one grouped notice, even across several dates in the
        # range.
        notify_cancelled(bookings, actor=current_user, by_admin=True, reason=body_text)

        for booking in bookings:
            # Closure cancel intentionally leaves walker_id set (unlike client
            # cancellations) — preserve that by not passing walker_id.
            # A closure is DogBoxx's decision, never the client's, so it is
            # never billed — persisted explicitly rather than left to the
            # legacy None branch (see bill_cancellation_for in invoicing.py).
            transition_booking(booking, 'cancelled', actor_id=current_user.id,
                               cancelled_by='admin', batch_id=batch_id,
                               bill_cancellation=False)

        db.session.commit()
        return jsonify(
            success=True,
            created_days=len(new_dates),
            skipped_dates=[d.isoformat() for d in skipped_dates],
            cancelled_count=len(bookings),
        )

    except Exception as e:
        db.session.rollback()
        logging.exception(f"Error in add_closure: {e}")
        return jsonify(success=False, message="Server error"), 500


@admin_bp.route("/closures/range/<range_id>", methods=["DELETE"])
@login_required
@admin_required
def delete_closure_range(range_id):
    matched = Closure.query.filter_by(range_id=range_id).all()
    if not matched:
        return jsonify(success=False, message="Closure not found"), 404
    for c in matched:
        db.session.delete(c)
    db.session.commit()
    return jsonify(success=True)
