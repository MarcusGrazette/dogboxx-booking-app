from flask import request, render_template, redirect, flash, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
import logging
import uuid

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import (
    User, Booking, Walker, Client, WalkerSchedule, WalkerUnavailability,
    WalkerAdHocAvailability,
)
from app import db
from app.forms import WalkerCreateForm, WalkerScheduleForm
from app.utils.notifications import NotificationBatch
from app.utils.booking_status import transition_booking, bulk_transition
from werkzeug.security import generate_password_hash
import secrets


@admin_bp.route("/walkers")
@login_required
@admin_required
def walkers():
    """List all walkers (admin only)"""
# Get all users with role='walker' and their walker records
    walkers = (
        User.query
        .options(joinedload(User.walker), joinedload(User.client))
        .filter(User.role == 'walker')
        .order_by(User.lastname, User.firstname)
        .all()
    )

    return render_template("admin_walkers.html", walkers=walkers)


@admin_bp.route("/walkers/<int:walker_user_id>/toggle-admin", methods=["POST"])
@login_required
@admin_required
def toggle_walker_admin(walker_user_id):
    """Promote or demote a walker's admin access. Super-admin only."""
    if not current_user.is_super_admin:
        return jsonify(success=False, message="Only the business owner can change admin access."), 403

    if walker_user_id == current_user.id:
        return jsonify(success=False, message="You cannot change your own admin access."), 400

    target = User.query.filter_by(id=walker_user_id, role='walker').first_or_404()

    if target.is_super_admin:
        return jsonify(success=False, message="Cannot change admin access for the business owner."), 400

    target.is_admin = not target.is_admin
    db.session.commit()

    return jsonify(success=True, is_admin=target.is_admin)


@admin_bp.route("/walkers/<int:walker_user_id>/toggle-drop-ins", methods=["POST"])
@login_required
@admin_required
def toggle_walker_drop_ins(walker_user_id):
    """Toggle whether a walker does drop-in visits."""
    target = User.query.filter_by(id=walker_user_id, role='walker').first_or_404()
    if not target.walker:
        return jsonify(success=False, message="No walker record found."), 400
    target.walker.does_drop_ins = not target.walker.does_drop_ins
    db.session.commit()
    return jsonify(success=True, does_drop_ins=target.walker.does_drop_ins)


@admin_bp.route("/walkers/<int:walker_user_id>/toggle-client", methods=["POST"])
@login_required
@admin_required
def toggle_walker_client(walker_user_id):
    """Create or remove a Client record for a walker, making them dual-role."""
    user = User.query.filter_by(id=walker_user_id, role='walker').first_or_404()

    if user.client:
        db.session.delete(user.client)
        db.session.commit()
        return jsonify(success=True, has_client=False)

    client = Client(
        user_id=user.id,
        onboarding_completed=True,
        onboarding_completed_at=datetime.now(timezone.utc),
    )
    db.session.add(client)
    db.session.commit()
    logging.info(f"Admin {current_user.id} added client record for walker {user.id}")
    return jsonify(success=True, has_client=True)


@admin_bp.route("/walkers/<int:walker_user_id>/remove-walker-role", methods=["POST"])
@login_required
@admin_required
def remove_walker_role(walker_user_id):
    """Transition a dual-role user from walker → client-only.

    Deactivates their walker schedule and reassigns future confirmed bookings,
    then changes their role to 'client' so they can still log in as a client.
    Requires the user to already have a Client record.
    """
    user = User.query.filter_by(id=walker_user_id, role='walker').first_or_404()

    if not user.client:
        return jsonify(success=False, message="This walker has no client record. Add a client record first."), 400

    if user.id == current_user.id:
        return jsonify(success=False, message="You cannot remove your own walker role."), 400

    from datetime import date as _date
    today = _date.today()

    # Reassign future confirmed bookings so they stay on the board. Fetch the
    # rows (not a bulk .update()) so each transition is logged via bulk_transition
    # with a shared batch_id. Actor = the admin removing the role.
    affected = Booking.query.filter(
        Booking.walker_id == user.walker.id,
        Booking.date >= today,
        Booking.status == 'confirmed',
    ).all()
    bulk_transition(affected, 'requested', actor_id=current_user.id,
                    walker_id=None, batch_id=uuid.uuid4().hex)

    # Notify each affected client (§7.2): one grouped booking_reset per user.
    # Removing the walker role unassigns their confirmed walks just like
    # deactivate_walker — the client must be told their booking reverted to
    # pending, not left to discover it silently.
    if affected:
        client_batch = NotificationBatch(actor_id=current_user.id)
        for b in affected:
            client_batch.add(b.user_id, 'booking_reset',
                             dog_name=b.dog.name if b.dog else 'Unknown', slot=b.slot, date=b.date,
                             svc_label='drop-in' if b.service_type and b.service_type.slug == 'drop-in' else 'walk')
        client_batch.flush()

    # Deactivate schedule so they no longer appear on future capacity
    WalkerSchedule.query.filter_by(walker_id=user.walker.id).update(
        {'active': False}, synchronize_session=False
    )

    user.role = 'client'
    db.session.commit()
    logging.info(f"Admin {current_user.id} removed walker role for user {user.id} (kept client record)")
    return jsonify(success=True)


@admin_bp.route("/walkers/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_walker():
    """Form to add a new walker (admin only)"""
    form = WalkerCreateForm()

    if form.validate_on_submit():
        try:
            # Check if user already exists
            existing_user = User.query.filter_by(email=form.email.data.lower()).first()
            if existing_user:
                flash("A user with this email already exists.", "error")
                return render_template("admin_walker_form.html", form=form, title="Add New Walker")

            # Generate temporary password
            temp_password = secrets.token_urlsafe(12)

            # Create User record
            user = User(
                firstname=form.firstname.data.strip().title(),
                lastname=form.lastname.data.strip().title(),
                email=form.email.data.strip().lower(),
                role='walker',
                hashed_password=generate_password_hash(temp_password),
                must_change_password=True
            )

            db.session.add(user)
            db.session.flush()  # Get user.id

            # Create Walker record
            walker = Walker(user_id=user.id)
            db.session.add(walker)

            db.session.commit()

            logging.info(f"Admin {current_user.id} created walker account for {user.email}")
            flash(f"Walker account created for {user.firstname} {user.lastname}.", "success")

            return redirect(url_for('admin.walkers'))

        except IntegrityError as e:
            db.session.rollback()
            logging.error(f"IntegrityError creating walker: {e}")
            flash("A walker with this email already exists.", "error")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error creating walker: {e}")
            flash("An error occurred while creating the walker.", "error")

    return render_template("admin_walker_form.html", form=form, title="Add New Walker")


@admin_bp.route("/walkers/<int:walker_id>/deactivate", methods=["POST"])
@login_required
@admin_required
def deactivate_walker(walker_id):
    """Deactivate a walker (soft delete)"""
    try:
        user = User.query.filter(User.role == 'walker', User.id == walker_id).first()
        if not user:
            return jsonify(success=False, message="Walker not found"), 404

        if user.id == current_user.id:
            return jsonify(success=False, message="You cannot deactivate your own account"), 400

        user.active = False

        # Return future confirmed bookings to pending so they stay visible on the
        # board. Fetch the rows (not a bulk .update()) so each transition is
        # logged via bulk_transition. Actor = the admin deactivating the walker.
        from datetime import date as _date
        today = _date.today()
        affected = Booking.query.filter(
            Booking.walker_id == user.walker.id,
            Booking.date >= today,
            Booking.status == 'confirmed',
        ).all()
        bulk_transition(affected, 'requested', actor_id=current_user.id,
                        walker_id=None, batch_id=uuid.uuid4().hex)

        # Notify each affected client (§7.2): one grouped booking_reset per user.
        if affected:
            client_batch = NotificationBatch(actor_id=current_user.id)
            for b in affected:
                client_batch.add(b.user_id, 'booking_reset',
                                 dog_name=b.dog.name if b.dog else 'Unknown', slot=b.slot, date=b.date,
                                 svc_label='drop-in' if b.service_type and b.service_type.slug == 'drop-in' else 'walk')
            client_batch.flush()

        # Deactivate schedule rows so the walker no longer appears on future board dates
        WalkerSchedule.query.filter_by(walker_id=user.walker.id).update(
            {'active': False}, synchronize_session=False
        )

        db.session.commit()

        logging.info(f"Admin {current_user.id} deactivated walker {user.id}")
        return jsonify(success=True, message="Walker deactivated successfully")

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deactivating walker {walker_id}: {e}")
        return jsonify(success=False, message="Error deactivating walker"), 500


@admin_bp.route("/walkers/<int:walker_id>/activate", methods=["POST"])
@login_required
@admin_required
def activate_walker(walker_id):
    """Reactivate a walker"""
    try:
        user = User.query.filter(User.role == 'walker', User.id == walker_id).first()
        if not user:
            return jsonify(success=False, message="Walker not found"), 404

        user.active = True
        WalkerSchedule.query.filter_by(walker_id=user.walker.id).update(
            {'active': True}, synchronize_session=False
        )
        db.session.commit()

        logging.info(f"Admin {current_user.id} activated walker {user.id}")
        return jsonify(success=True, message="Walker activated successfully")

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error activating walker {walker_id}: {e}")
        return jsonify(success=False, message="Error activating walker"), 500


@admin_bp.route("/walkers/<int:walker_id>/schedule", methods=["GET", "POST"])
@login_required
def walker_schedule(walker_id):
    """View/edit walker's weekly schedule"""
# Get walker
    walker = Walker.query.options(joinedload(Walker.user)).get_or_404(walker_id)

    # Admins can edit any walker's schedule; walkers can only edit their own
    if not current_user.is_admin:
        own_walker = Walker.query.filter_by(user_id=current_user.id).first()
        if not own_walker or own_walker.id != walker_id:
            return jsonify(success=False, message="Forbidden"), 403

    form = WalkerScheduleForm()

    if form.validate_on_submit():
        try:
            # Clear existing schedules
            WalkerSchedule.query.filter_by(walker_id=walker_id).delete()

            # Add new schedules based on form data
            days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            for day_index, day_name in enumerate(days):
                day_form = getattr(form, day_name)

                if day_form.morning.data:
                    schedule = WalkerSchedule(
                        walker_id=walker_id,
                        day_of_week=day_index,
                        slot='Morning',
                        active=True
                    )
                    db.session.add(schedule)

                if day_form.afternoon.data:
                    schedule = WalkerSchedule(
                        walker_id=walker_id,
                        day_of_week=day_index,
                        slot='Afternoon',
                        active=True
                    )
                    db.session.add(schedule)

            db.session.commit()

            flash("Walker schedule updated successfully.", "success")
            return redirect(url_for('admin.walkers'))

        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating walker schedule: {e}")
            flash("An error occurred while updating the schedule.", "error")

    # Pre-populate form with existing schedule
    existing_schedules = WalkerSchedule.query.filter_by(walker_id=walker_id, active=True).all()
    schedule_dict = {}
    for schedule in existing_schedules:
        if schedule.day_of_week not in schedule_dict:
            schedule_dict[schedule.day_of_week] = {'morning': False, 'afternoon': False}
        schedule_dict[schedule.day_of_week][schedule.slot.lower()] = True

    # Set form values
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    for day_index, day_name in enumerate(days):
        day_form = getattr(form, day_name)
        if day_index in schedule_dict:
            day_form.morning.data = schedule_dict[day_index].get('morning', False)
            day_form.afternoon.data = schedule_dict[day_index].get('afternoon', False)

    return render_template("admin_walker_schedule.html", walker=walker, form=form)


@admin_bp.route("/walkers/<int:walker_id>/schedule-json", methods=["GET", "POST"])
@login_required
@admin_required
def walker_schedule_json(walker_id):
    """JSON read/write endpoint for the schedule modal on /admin/walkers."""
    walker = db.get_or_404(Walker, walker_id)

    if request.method == 'GET':
        schedules = WalkerSchedule.query.filter_by(walker_id=walker_id, active=True).all()
        return jsonify(success=True, schedules=[
            {'day': s.day_of_week, 'slot': s.slot} for s in schedules
        ])

    # POST — replace schedule
    data = request.get_json()
    if data is None:
        return jsonify(success=False, message="No data received"), 400
    entries = data.get('schedules', [])
    valid_slots = ('Morning', 'Afternoon')
    for e in entries:
        if e.get('day') not in range(7) or e.get('slot') not in valid_slots:
            return jsonify(success=False, message="Invalid schedule data"), 400
    try:
        old_set = {
            (s.day_of_week, s.slot)
            for s in WalkerSchedule.query.filter_by(walker_id=walker_id, active=True).all()
        }
        new_set = {(e['day'], e['slot']) for e in entries}
        removed = old_set - new_set

        WalkerSchedule.query.filter_by(walker_id=walker_id).delete()
        for e in entries:
            db.session.add(WalkerSchedule(
                walker_id=walker_id, day_of_week=e['day'], slot=e['slot'], active=True
            ))

        # Confirmed future bookings on the now-removed (weekday, slot) combos
        # are no longer guaranteed — reset them to 'requested' (walker_id=None)
        # so they surface in the pending column on the board for reassignment,
        # and notify each affected client with a grouped booking_reset (§7.1).
        affected_count = 0
        if removed:
            from datetime import date as _date
            today = _date.today()
            future_confirmed = (
                Booking.query
                .filter(
                    Booking.walker_id == walker_id,
                    Booking.status == 'confirmed',
                    Booking.date >= today,
                )
                .all()
            )
            # Shared batch_id so the feed can cluster this schedule edit's resets.
            batch_id = uuid.uuid4().hex
            client_batch = NotificationBatch(actor_id=current_user.id)
            for b in future_confirmed:
                if (b.date.weekday(), b.slot) in removed:
                    transition_booking(b, 'requested', actor_id=current_user.id,
                                       walker_id=None, batch_id=batch_id)
                    client_batch.add(b.user_id, 'booking_reset',
                                     dog_name=b.dog.name if b.dog else 'Unknown', slot=b.slot, date=b.date,
                                     svc_label='drop-in' if b.service_type and b.service_type.slug == 'drop-in' else 'walk')
                    affected_count += 1
            client_batch.flush()

        db.session.commit()

        logging.info(f"Admin {current_user.id} updated schedule for walker {walker_id} via modal")
        return jsonify(success=True, affected_count=affected_count)
    except Exception as exc:
        db.session.rollback()
        logging.error(f"Error updating walker schedule (modal): {exc}")
        return jsonify(success=False, message="Error saving schedule"), 500


# ─── Walker schedule overrides (ad hoc available + unavailability) ───────────

@admin_bp.route("/api/schedule-changes")
@login_required
@admin_required
def admin_api_schedule_changes():
    """Return the merged schedule-changes list HTML partial for a given walker.

    Used by the admin overrides page to refresh the list after add/delete.
    ?walker_id=N required.
    """
    from datetime import date
    from app.blueprints.walker.routes import _build_schedule_change_groups

    try:
        wid = int(request.args.get('walker_id', ''))
    except (ValueError, TypeError):
        return "walker_id required", 400

    walker = db.get_or_404(Walker, wid)
    today = date.today()

    unavailabilities = WalkerUnavailability.query.filter(
        WalkerUnavailability.walker_id == walker.id,
        WalkerUnavailability.date >= today,
    ).order_by(WalkerUnavailability.date, WalkerUnavailability.slot).all()

    adhoc_availabilities = WalkerAdHocAvailability.query.filter(
        WalkerAdHocAvailability.walker_id == walker.id,
        WalkerAdHocAvailability.date >= today,
    ).order_by(WalkerAdHocAvailability.date, WalkerAdHocAvailability.slot).all()

    schedule_groups = _build_schedule_change_groups(adhoc_availabilities, unavailabilities)
    return render_template(
        "partials/walker_schedule_changes_list.html",
        schedule_groups=schedule_groups,
    )


@admin_bp.route("/walkers/overrides")
@login_required
@admin_required
def walker_overrides():
    """Admin page: manage ad hoc availability and unavailability for any walker."""
    from datetime import date

    active_walkers = (
        Walker.query
        .join(Walker.user)
        .filter(User.active == True, User.role == 'walker')
        .order_by(User.lastname, User.firstname)
        .all()
    )

    today = date.today()

    selected_walker = None
    adhoc_list = []
    unavail_list = []

    walker_id_str = request.args.get('walker_id')
    if walker_id_str:
        try:
            wid = int(walker_id_str)
            selected_walker = next((w for w in active_walkers if w.id == wid), None)
        except (ValueError, TypeError):
            pass

    if not selected_walker and active_walkers:
        selected_walker = active_walkers[0]

    if selected_walker:
        adhoc_list = (
            WalkerAdHocAvailability.query
            .filter(
                WalkerAdHocAvailability.walker_id == selected_walker.id,
                WalkerAdHocAvailability.date >= today,
            )
            .order_by(WalkerAdHocAvailability.date, WalkerAdHocAvailability.slot)
            .all()
        )
        unavail_list = (
            WalkerUnavailability.query
            .filter(
                WalkerUnavailability.walker_id == selected_walker.id,
                WalkerUnavailability.date >= today,
            )
            .order_by(WalkerUnavailability.date, WalkerUnavailability.slot)
            .all()
        )

    return render_template(
        'admin_walker_overrides.html',
        active_walkers=active_walkers,
        selected_walker=selected_walker,
        adhoc_list=adhoc_list,
        unavail_list=unavail_list,
        today=today,
    )


@admin_bp.route("/walker-overrides-fragment")
@login_required
@admin_required
def walker_overrides_fragment():
    """Bare HTML fragment of the override form + list, served for the
    dashboard modal. Caller wires up createOverrideForm() with the chosen
    walker_id (no walker selector in the fragment)."""
    return render_template('partials/admin_walker_overrides_form.html')


@admin_bp.route("/walkers/<int:walker_id>/adhoc", methods=["POST"])
@login_required
@admin_required
def admin_add_adhoc(walker_id):
    """Admin: add an ad hoc available slot for any walker."""
    from datetime import date

    walker = db.get_or_404(Walker, walker_id)

    data = request.get_json(silent=True)
    if not data:
        return jsonify(success=False, message="Invalid JSON"), 400

    date_str = data.get('date')
    slot = data.get('slot')

    if not date_str or slot not in ('Morning', 'Afternoon'):
        return jsonify(success=False, message="Date and valid slot are required"), 400

    try:
        adhoc_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date format (use YYYY-MM-DD)"), 400

    # Don't add if already in default schedule (redundant)
    day_of_week = adhoc_date.weekday()
    if WalkerSchedule.query.filter_by(walker_id=walker.id, day_of_week=day_of_week, slot=slot, active=True).first():
        return jsonify(success=False, message=f"{walker.user.full_name} is already scheduled for {slot} on {adhoc_date.strftime('%A')}s"), 400

    if WalkerAdHocAvailability.query.filter_by(walker_id=walker.id, date=adhoc_date, slot=slot).first():
        return jsonify(success=False, message="Already marked as available for this date/slot"), 400

    adhoc = WalkerAdHocAvailability(walker_id=walker.id, date=adhoc_date, slot=slot)
    db.session.add(adhoc)
    db.session.commit()

    return jsonify(success=True, adhoc=adhoc.to_dict()), 201


@admin_bp.route("/walkers/<int:walker_id>/adhoc/<int:adhoc_id>", methods=["DELETE"])
@login_required
@admin_required
def admin_delete_adhoc(walker_id, adhoc_id):
    """Admin: remove an ad hoc available slot."""
    adhoc = db.session.get(WalkerAdHocAvailability, adhoc_id)
    if not adhoc or adhoc.walker_id != walker_id:
        return jsonify(success=False, message="Not found"), 404
    db.session.delete(adhoc)
    db.session.commit()
    return jsonify(success=True)


@admin_bp.route("/walkers/<int:walker_id>/unavailability", methods=["POST"])
@login_required
@admin_required
def admin_add_unavailability(walker_id):
    """Admin: add an unavailability slot for any walker."""
    walker = db.get_or_404(Walker, walker_id)

    data = request.get_json(silent=True)
    if not data:
        return jsonify(success=False, message="Invalid JSON"), 400

    date_str = data.get('date')
    slot = data.get('slot')
    reason = data.get('reason', '').strip() or None

    if not date_str or slot not in ('Morning', 'Afternoon'):
        return jsonify(success=False, message="Date and valid slot are required"), 400

    try:
        unavail_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(success=False, message="Invalid date format (use YYYY-MM-DD)"), 400

    if WalkerUnavailability.query.filter_by(walker_id=walker.id, date=unavail_date, slot=slot).first():
        return jsonify(success=False, message="Already marked as unavailable for this date/slot"), 400

    unavail = WalkerUnavailability(walker_id=walker.id, date=unavail_date, slot=slot,
                                   reason=reason, created_by_id=current_user.id)
    db.session.add(unavail)

    # Any confirmed bookings this walker held for this date/slot are no longer
    # guaranteed — reset them to requested so they surface as pending on the board.
    affected = Booking.query.filter_by(
        walker_id=walker.id, date=unavail_date, slot=slot, status='confirmed',
    ).all()
    # Reset to requested (walker unassigned), logging a BSC row per booking.
    # Actor = the admin marking the walker unavailable.
    bulk_transition(affected, 'requested', actor_id=current_user.id,
                    walker_id=None, batch_id=uuid.uuid4().hex)

    # Notify each affected client (§7.2): one grouped booking_reset per user.
    if affected:
        client_batch = NotificationBatch(actor_id=current_user.id)
        for b in affected:
            client_batch.add(b.user_id, 'booking_reset',
                             dog_name=b.dog.name, slot=b.slot, date=b.date,
                             svc_label='drop-in' if b.service_type and b.service_type.slug == 'drop-in' else 'walk')
        client_batch.flush()

    db.session.commit()

    return jsonify(success=True, unavailability=unavail.to_dict(), unassigned=len(affected)), 201


@admin_bp.route("/walkers/<int:walker_id>/unavailability/<int:unavail_id>", methods=["DELETE"])
@login_required
@admin_required
def admin_delete_unavailability(walker_id, unavail_id):
    """Admin: remove an unavailability entry."""
    unavail = db.session.get(WalkerUnavailability, unavail_id)
    if not unavail or unavail.walker_id != walker_id:
        return jsonify(success=False, message="Not found"), 404
    db.session.delete(unavail)
    db.session.commit()
    return jsonify(success=True)
