import uuid
from collections import OrderedDict
from datetime import date as date_type, datetime, timedelta

from flask import request, render_template, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
import logging

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import User, Booking, DogOwner, ServiceType, Closure
from app import db
from app.utils.notifications import NotificationBatch
from app.utils.booking_status import transition_booking

ACTIVE_STATUSES = ('requested', 'confirmed', 'waitlisted')
# Sanity guard against a fat-fingered multi-year range wiping out the calendar.
MAX_CLOSURE_RANGE_DAYS = 60


def _parse_range(source):
    """Resolve a (start, end) date pair from either explicit start_date/end_date
    keys or a legacy single `date` key (start == end). Raises ValueError with a
    user-facing message on any invalid input."""
    single = source.get('date', '')
    start_str = source.get('start_date') or single
    end_str = source.get('end_date') or single

    try:
        start = datetime.strptime(start_str, '%Y-%m-%d').date()
        end = datetime.strptime(end_str, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError("Invalid date")

    if end < start:
        raise ValueError("End date is before start date")
    if (end - start).days + 1 > MAX_CLOSURE_RANGE_DAYS:
        raise ValueError(f"Range too long — max {MAX_CLOSURE_RANGE_DAYS} days")

    return start, end


def _dates_in_range(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _range_label(dates):
    if len(dates) == 1:
        return dates[0].strftime('%-d %b %Y')
    return f"{dates[0].strftime('%-d %b')} – {dates[-1].strftime('%-d %b %Y')}"


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
        start, end = _parse_range(request.args)
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400

    dates = _dates_in_range(start, end)
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
            start, end = _parse_range(data)
        except ValueError as e:
            return jsonify(success=False, message=str(e)), 400

        reason = (data.get('reason') or '').strip() or None

        dates = _dates_in_range(start, end)
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
        body_text = f"DogBoxx is closed {_range_label(new_dates)}" + (f" — {reason}." if reason else ".")

        # Batch-fetch co-owners to avoid N+1 (one DogOwner query per booking).
        dog_ids = [b.dog_id for b in bookings if b.dog_id]
        if dog_ids:
            ownerships = DogOwner.query.filter(DogOwner.dog_id.in_(dog_ids)).all()
            co_users = {u.id: u for u in User.query.filter(
                User.id.in_({o.user_id for o in ownerships})).all()}
            owners_by_dog = {}
            for o in ownerships:
                owners_by_dog.setdefault(o.dog_id, []).append(o)
        else:
            owners_by_dog, co_users = {}, {}

        # Grouped per recipient: primary owner, co-owners, and assigned walker
        # each get one consolidated notice — even across multiple cancelled
        # bookings spanning several dates in the range.
        batch = NotificationBatch(actor_id=current_user.id)
        for booking in bookings:
            # Closure cancel intentionally leaves walker_id set (unlike client
            # cancellations) — preserve that by not passing walker_id.
            transition_booking(booking, 'cancelled', actor_id=current_user.id,
                               cancelled_by='admin', batch_id=batch_id)
            svc_label = (
                'drop-in'
                if booking.service_type and booking.service_type.slug == ServiceType.DROP_IN
                else 'walk'
            )
            dog_name = booking.dog.name if booking.dog else 'Your dog'
            payload = dict(dog_name=dog_name, slot=booking.slot,
                            date=booking.date, svc_label=svc_label, reason=body_text)

            # Primary owner
            batch.add(booking.user_id, 'booking_cancelled', **payload)

            # Co-owners: other non-admin users who share this dog
            for o in owners_by_dog.get(booking.dog_id, []):
                if o.user_id == booking.user_id:
                    continue
                co_user = co_users.get(o.user_id)
                if co_user and not co_user.is_admin:
                    batch.add(co_user.id, 'booking_cancelled', **payload)

            # Assigned walker: skip if unset or if it's the acting admin
            if booking.walker_id and booking.walker:
                walker_uid = booking.walker.user_id
                if walker_uid and walker_uid != current_user.id:
                    batch.add(walker_uid, 'booking_cancelled', **payload)

        batch.flush()
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
