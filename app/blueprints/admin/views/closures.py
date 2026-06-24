from flask import request, render_template, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from datetime import datetime, timezone
import logging

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import User, Booking, Dog, DogOwner, ServiceType, Closure
from app import db
from app.utils.notifications import NotificationBatch
from app.utils.booking_status import transition_booking


@admin_bp.route("/closures")
@login_required
@admin_required
def closures():
    from datetime import date as date_type
    all_closures = Closure.query.order_by(Closure.date).all()
    return render_template('admin_closures.html', closures=all_closures, today=date_type.today())


@admin_bp.route("/closures/preview")
@login_required
@admin_required
def closures_preview():
    date_str = request.args.get('date', '')
    try:
        closure_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date"), 400

    active_statuses = ('requested', 'confirmed', 'waitlisted')
    bookings = (Booking.query
                .filter(Booking.date == closure_date, Booking.status.in_(active_statuses))
                .options(joinedload(Booking.dog), joinedload(Booking.user))
                .all())

    return jsonify(
        success=True,
        count=len(bookings),
        bookings=[{
            'dog':    b.dog.name if b.dog else '?',
            'owner':  f"{b.user.firstname} {b.user.lastname}" if b.user else '?',
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

        date_str = data.get('date', '')
        reason   = (data.get('reason') or '').strip() or None

        try:
            closure_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify(success=False, message="Invalid date"), 400

        if Closure.query.filter_by(date=closure_date).first():
            return jsonify(success=False, message="A closure already exists for that date"), 400

        closure = Closure(date=closure_date, reason=reason, created_by_id=current_user.id)
        db.session.add(closure)

        active_statuses = ('requested', 'confirmed', 'waitlisted')
        bookings = Booking.query.filter(
            Booking.date == closure_date,
            Booking.status.in_(active_statuses)
        ).options(
            joinedload(Booking.dog),
            joinedload(Booking.service_type),
            joinedload(Booking.walker),
        ).all()

        # One batch_id ties together every cancellation caused by this closure
        # so the activity feed can cluster them (NOTIFICATIONS.md §9.2, D4).
        batch_id  = __import__('uuid').uuid4().hex
        body_text = "DogBoxx is closed" + (f" — {reason}." if reason else ".")

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

        # Grouped per recipient (§9.3/§9.4, §7.4): primary owner, co-owners,
        # and assigned walker each get one consolidated notice.
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
            payload  = dict(dog_name=dog_name, slot=booking.slot,
                            date=closure_date, svc_label=svc_label, reason=body_text)

            # Primary owner
            batch.add(booking.user_id, 'booking_cancelled', **payload)

            # Co-owners (§7.4): other non-admin users who share this dog
            for o in owners_by_dog.get(booking.dog_id, []):
                if o.user_id == booking.user_id:
                    continue
                co_user = co_users.get(o.user_id)
                if co_user and not co_user.is_admin:
                    batch.add(co_user.id, 'booking_cancelled', **payload)

            # Assigned walker (§7.4): skip if unset or if it's the acting admin
            if booking.walker_id and booking.walker:
                walker_uid = booking.walker.user_id
                if walker_uid and walker_uid != current_user.id:
                    batch.add(walker_uid, 'booking_cancelled', **payload)

        batch.flush()
        db.session.commit()
        return jsonify(success=True, cancelled_count=len(bookings))

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in add_closure: {e}")
        return jsonify(success=False, message="Server error"), 500


@admin_bp.route("/closures/<int:closure_id>", methods=["DELETE"])
@login_required
@admin_required
def delete_closure(closure_id):
    closure = db.session.get(Closure, closure_id)
    if not closure:
        return jsonify(success=False, message="Closure not found"), 404
    db.session.delete(closure)
    db.session.commit()
    return jsonify(success=True)
