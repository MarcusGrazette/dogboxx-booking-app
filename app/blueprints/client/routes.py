"""
Client routes.

This module defines routes for client functionality, including home page, profile
management, onboarding, and booking management.
"""

from flask import current_app, request, redirect, render_template, flash, url_for, jsonify, session
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from app.models import User, Client, Dog, Booking, DogOwner, ServiceType, Walker, Closure
from app import db, limiter
from app.utils.db_error_handler import handle_db_errors, DBErrorHandler
from app.utils.uploads import process_dog_photo, process_cropped_photo
from app.utils.booking_access import get_accessible_dog_ids, user_can_access_booking
from app.capacity import check_availability, get_slot_availability_summary, auto_assign_walker, get_walker_slot_count, acquire_booking_lock, is_date_closed
from app.forms import OnboardingForm, BookingForm, ProfileForm
import logging
import traceback
import uuid
from datetime import datetime, timezone, timedelta, date as date_type

from app.blueprints.client import client_bp
from app.utils.notifications import create_notification, NotificationBatch
from app.utils.booking_status import (
    transition_booking, record_booking_created, bulk_transition,
    _UNSET as _UNSET_BILL,
)
from app.utils.invoicing import is_late_cancellation
from app.utils.decorators import has_client_access
from app.services.booking_service import create_booking, CapacityError


@client_bp.route("/help")
def help_page():
    return render_template('help.html')


@client_bp.route("/get-started")
def get_started():
    return render_template('get_started.html')


@client_bp.route("/switch-view", methods=["POST"])
@login_required
def switch_view():
    """Toggle between walker and client view for dual-role users."""
    if current_user.role != 'walker' or current_user.client is None:
        return redirect(url_for('client.index'))
    view = request.form.get('view')
    if view in ('walker', 'client'):
        session['active_view'] = view
    if session.get('active_view') == 'client':
        return redirect(url_for('client.index'))
    return redirect(url_for('walker.pickups'))


@client_bp.route("/report-bug", methods=["POST"])
@login_required
@limiter.limit("3 per hour", key_func=lambda: f"report-bug:{current_user.id}")
def report_bug():
    from app.utils.email import send_email
    from app.utils.logging_config import recent_log_buffer
    from html import escape

    description = (request.form.get("description") or "").strip()
    if not description:
        return jsonify(success=False, message="Please describe the issue."), 400

    user = current_user
    user_agent = request.headers.get("User-Agent", "unknown")
    referrer = request.form.get("page_url") or request.referrer or "unknown"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    logs = list(recent_log_buffer)
    log_section = "\n".join(logs) if logs else "(no recent warnings or errors)"

    html = f"""
    <h2 style="color:#1B1B1B;font-family:sans-serif;">Bug Report</h2>
    <table style="font-family:sans-serif;font-size:14px;border-collapse:collapse;width:100%;">
      <tr><td style="padding:6px 12px 6px 0;color:#555;white-space:nowrap;"><strong>User</strong></td>
          <td style="padding:6px 0;">{escape(user.firstname)} {escape(user.lastname or '')} &lt;{escape(user.email)}&gt; — {escape(user.role)}</td></tr>
      <tr><td style="padding:6px 12px 6px 0;color:#555;"><strong>URL</strong></td>
          <td style="padding:6px 0;">{escape(referrer)}</td></tr>
      <tr><td style="padding:6px 12px 6px 0;color:#555;"><strong>Browser</strong></td>
          <td style="padding:6px 0;">{escape(user_agent)}</td></tr>
      <tr><td style="padding:6px 12px 6px 0;color:#555;"><strong>Time</strong></td>
          <td style="padding:6px 0;">{timestamp}</td></tr>
    </table>

    <h3 style="font-family:sans-serif;margin-top:24px;">Description</h3>
    <p style="font-family:sans-serif;font-size:14px;white-space:pre-wrap;">{escape(description)}</p>

    <h3 style="font-family:sans-serif;margin-top:24px;">Recent server logs (WARNING / ERROR)</h3>
    <pre style="background:#f4f4f4;padding:12px;font-size:12px;overflow-x:auto;border-radius:4px;">{escape(log_section)}</pre>
    """

    ok = send_email(
        to=current_app.config['BUG_REPORTS_EMAIL'],
        subject=f"Bug report from {user.firstname} {user.lastname or ''}".strip(),
        html=html,
    )

    if ok:
        return jsonify(success=True)
    return jsonify(success=False, message="Failed to send — please try again."), 500


def _is_same_day(booking_date):
    """True if booking_date is today (server UTC). Same-day bookings skip
    auto-assignment — walker schedules are planned in advance, so Lydia
    reviews these manually."""
    return booking_date == datetime.now(timezone.utc).date()


def _maybe_auto_confirm(booking, dog, service_slug=ServiceType.WALK, notify=True,
                        same_day=False, batch_id=None):
    """Try to auto-assign a walker to a newly-created 'requested' booking.

    If a walker with capacity is available, sets the booking to confirmed,
    assigns the walker, and (if notify=True) notifies the client. Otherwise
    (if notify=True) notifies admins of the pending request. Must be called
    before db.session.commit(). Returns True if auto-confirmed, False if
    left as requested.

    notify=False skips both the client (actor) notification on confirm and
    the admin notification on no-walker — used by /book_both, which composes
    a single consolidated notification covering every slot in the request.
    Co-owner notifications are always written per-slot regardless.

    same_day=True forces the booking to stay 'requested' (no auto-assign)
    and uses the 'same_day_request' notification type so admins can spot it.
    """
    walker = None if same_day else auto_assign_walker(
        booking.date, booking.slot, service_slug=service_slug
    )
    if walker:
        # transition_booking sets status='confirmed', confirmed_at, walker_id and
        # logs the requested→confirmed BSC row. Actor is the booking creator.
        transition_booking(booking, 'confirmed', actor_id=current_user.id,
                            walker_id=walker.id, batch_id=batch_id)
        # Set pickup_order to next available position in this walker's slot
        from app.capacity import get_walker_slot_count
        booking.pickup_order = get_walker_slot_count(walker.id, booking.date, booking.slot, service_slug=service_slug)
        if notify:
            walker_first = walker.user.firstname if walker and walker.user else None
            svc_label = 'drop-in' if service_slug == ServiceType.DROP_IN else 'walk'
            batch = NotificationBatch(actor_id=current_user.id)
            batch.add(booking.user_id, 'booking_confirmed',
                      dog_name=dog.name, slot=booking.slot, date=booking.date,
                      walker_name=walker_first, svc_label=svc_label)
            for admin in User.query.filter_by(is_admin=True).all():
                batch.add(admin.id, 'booking_confirmed',
                          actor_first=booking.user.firstname,
                          link=f'/admin/clients/{booking.user_id}',
                          dog_name=dog.name, slot=booking.slot, date=booking.date,
                          walker_name=walker_first, svc_label=svc_label)
            # Notify the auto-assigned walker (§7.9, D3). Skip if the walker is
            # the booking user themselves (edge case: owner-walker books own dog).
            if walker.user_id != booking.user_id:
                batch.add(walker.user_id, 'walker_assigned',
                          dog_name=dog.name, slot=booking.slot, date=booking.date,
                          svc_label=svc_label)
            batch.flush()
        _notify_co_owners_of_booking(booking, dog.name, confirmed=True)
        return True
    else:
        if notify:
            # Notify admins — needs manual assignment
            admins = User.query.filter_by(is_admin=True).all()
            date_str = booking.date.strftime('%a %-d %b')
            ntype = 'same_day_request' if same_day else 'booking_requested'
            title_prefix = 'Same-day request — ' if same_day else ''
            for admin in admins:
                create_notification(
                    recipient_id=admin.id,
                    notification_type=ntype,
                    title=f"{title_prefix}{booking.user.firstname} requested {dog.name}'s {booking.slot.lower()} walk on {date_str}",
                    link='/admin',
                    sender_id=booking.user_id,
                )
            # Client (actor) notification — without this, a non-auto-confirmed
            # request leaves the bell silent until an admin acts.
            create_notification(
                recipient_id=booking.user_id,
                notification_type=ntype,
                title=f"{dog.name}'s {booking.slot.lower()} walk on {date_str} has been requested",
                body=(
                    f"Same-day request — {current_app.config['OWNER_FIRSTNAME']} will confirm shortly."
                    if same_day else
                    "We'll confirm shortly."
                ),
                link='/',
            )
        _notify_co_owners_of_booking(booking, dog.name, confirmed=False)
        return False


def _summarise_book_both_for_client(slot_entries, dog_name, booking_date):
    """Build (title, body, notification_type) for the single consolidated client
    notification from /book_both using summarise() for canonical wording.

    slot_entries: list of (slot_name, status, booking) tuples.
    booking_date: datetime.date — passed through to summarise() for consistent
                  date formatting (summarise uses %-d, not a pre-formatted string).
    """
    from app.utils.notifications import summarise as _summarise
    ordered = sorted(slot_entries, key=lambda x: 0 if x[0] == 'Morning' else 1)

    confirmed  = [(s, b) for s, st, b in ordered if st == 'confirmed']
    waitlisted = [(s, b) for s, st, b in ordered if st == 'waitlisted']
    requested  = [(s, b) for s, st, b in ordered if st not in ('confirmed', 'waitlisted')]

    # Pure cases: delegate to summarise() for the canonical title; for the 2-slot
    # AM+PM case keep an explicit slot body ("morning and afternoon both booked.")
    # since "2 walks booked." drops the slot context that matters here.
    if confirmed and not waitlisted and not requested:
        payloads = [dict(dog_name=dog_name, slot=s,
                         date=booking_date,
                         walker_name=b.walker.user.firstname if b.walker and b.walker.user else None)
                    for s, b in confirmed]
        title, body, ntype, _ = _summarise('booking_confirmed', payloads)
        if len(confirmed) == 2:
            slot_names = [s for s, _ in sorted(confirmed, key=lambda x: 0 if x[0] == 'Morning' else 1)]
            body = f"{' and '.join(s.lower() for s in slot_names)} both booked.".capitalize()
        return title, body, ntype

    if waitlisted and not confirmed and not requested:
        payloads = [dict(dog_name=dog_name, slot=s, date=booking_date)
                    for s, _ in waitlisted]
        title, body, ntype, _ = _summarise('booking_waitlisted', payloads)
        return title, body, ntype

    if requested and not confirmed and not waitlisted:
        payloads = [dict(dog_name=dog_name, slot=s, date=booking_date)
                    for s, _ in requested]
        title, body, ntype, _ = _summarise('booking_requested', payloads)
        return title, body, ntype

    # Mixed outcome (e.g. one confirmed + one waitlisted): build a short
    # bespoke summary. The notification type is pessimistic (booking_requested).
    def _status_label(st):
        return {'confirmed': 'confirmed', 'waitlisted': 'on the waitlist'}.get(st, 'requested')
    parts = [f'{s.lower()} {_status_label(st)}' for s, st, _ in ordered]
    from app.utils.notifications import _fmt_day
    title = f"{dog_name}'s walks on {_fmt_day(booking_date)}"
    body  = ', '.join(parts).capitalize() + '.'
    return title, body, 'booking_requested'


def _notify_co_owners_of_booking(booking, dog_name, confirmed):
    """Notify all co-owners of a dog about a booking event, excluding the actor.

    Sends to every DogOwner row for the dog whose user_id differs from
    booking.user_id. Must be called after db.session.flush() so booking.user
    is accessible.
    """
    other_owners = DogOwner.query.filter(
        DogOwner.dog_id == booking.dog_id,
        DogOwner.user_id != booking.user_id,
    ).all()
    if not other_owners:
        return
    date_str = booking.date.strftime('%a %-d %b')
    actor = booking.user.firstname if booking.user else 'Someone'
    verb = 'booked' if confirmed else 'requested'
    notif_type = 'booking_confirmed' if confirmed else 'booking_requested'
    service_label = (
        'drop-in'
        if booking.service_type and booking.service_type.slug == ServiceType.DROP_IN
        else 'walk'
    )
    for ownership in other_owners:
        create_notification(
            recipient_id=ownership.user_id,
            notification_type=notif_type,
            title=f"{actor} {verb} {dog_name}'s {booking.slot.lower()} {service_label} on {date_str}",
            link='/',
            sender_id=booking.user_id,
        )


def _resolve_dog(user_dogs, requested_id):
    """Return the Dog to book for.

    If requested_id is provided and belongs to this user, return that dog.
    Otherwise fall back to the first dog (single-dog accounts / legacy callers).
    Raises ValueError if requested_id is provided but not accessible.
    """
    if requested_id:
        try:
            requested_id = int(requested_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid dog selection.")
        dog = next((d for d in user_dogs if d.id == requested_id), None)
        if dog is None:
            raise ValueError("Dog not found on your account.")
        return dog
    return user_dogs[0]


@client_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    """Render the home page for clients."""
    # Admins land on /admin unless they've explicitly switched to client view
    if current_user.is_admin and session.get('active_view') != 'client':
        return redirect(url_for('admin.index'))
    if current_user.role == 'walker':
        # Dual-role walker in client view: let through. Otherwise send to walker home.
        if current_user.client is None or session.get('active_view') != 'client':
            return redirect(url_for('walker.pickups'))
        
    user = User.query.options(
        joinedload(User.client)
    ).filter_by(id=current_user.id).first()
    
    # Get user's dogs through DogOwner relationship
    user_dogs = Dog.query.join(DogOwner).filter(DogOwner.user_id == current_user.id).all()

    # Return all non-cancelled bookings (past and upcoming) for date-filter support
    today = datetime.now(timezone.utc).date()
    _index_dog_ids = get_accessible_dog_ids(current_user.id)
    upcoming_bookings_query = Booking.query.options(
        joinedload(Booking.walker).joinedload(Walker.user),
        joinedload(Booking.service_type),
    ).filter(
        Booking.dog_id.in_(_index_dog_ids),
        Booking.status.notin_(['cancelled', 'rejected']),
    ).order_by(Booking.date.asc())

    upcoming_bookings = list(upcoming_bookings_query)
    for b in upcoming_bookings:
        if b.date:
            b.date_display = b.date.strftime("%a %d %b")
        else:
            b.date_display = None
        b.is_drop_in = b.service_type and b.service_type.slug == ServiceType.DROP_IN
        b.is_past = (b.date < today) if b.date else False

    form = BookingForm()
    if form.validate_on_submit():
        booking_date = form.date.data
        booking_slot = form.slot.data

        today = datetime.now(timezone.utc).date()
        errors = []

        if booking_date < today:
            errors.append("Booking date cannot be in the past.")

        # Validate slot against allowed enum values
        if booking_slot not in ("Morning", "Afternoon"):
            errors.append("Invalid booking slot selected.")

         # Ensure the user has at least one dog to book
        if not user or not user_dogs:
            errors.append("No dog found on your account. Please add a dog before booking.")

        if not errors:
            try:
                selected_dog = _resolve_dog(user_dogs, request.form.get('dog_id'))
                dog_id = selected_dog.id
            except ValueError as e:
                errors.append(str(e))

        if not errors:
            # Prevent duplicate booking: same dog + date + slot (any service type)
            active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
            walk_service = ServiceType.query.filter_by(slug=ServiceType.WALK, active=True).first()
            existing = Booking.query.filter(
                Booking.dog_id == dog_id,
                Booking.date == booking_date,
                Booking.slot == booking_slot,
                Booking.status.in_(active_statuses),
            ).first()
            if existing:
                errors.append("This dog already has a booking for that slot on that date.")

            # Cap at 2 bookings per dog per day (one per slot)
            day_count = Booking.query.filter(
                Booking.dog_id == dog_id,
                Booking.date == booking_date,
                Booking.status.in_(active_statuses)
            ).count()
            if day_count >= 2:
                errors.append("This dog already has two bookings on that date (one per slot is the maximum).")

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            # Use context manager for error handling
            with DBErrorHandler(
                flash_message=True, 
                custom_error_messages={
                    IntegrityError: "Could not create booking due to a conflict. You might already have a booking at this time.",
                    OperationalError: "Our booking system is temporarily unavailable. Please try again later."
                }
            ):
                if not walk_service:
                    flash("No service type available. Please contact support.", "danger")
                    return redirect(url_for("client.index"))
                default_service = walk_service

                # Serialize concurrent requests for the same slot before counting
                acquire_booking_lock(default_service.slug, booking_date, booking_slot)
                available, can_waitlist, capacity_msg = check_availability(
                    default_service, booking_date, booking_slot
                )

                if not available and not can_waitlist:
                    # No walkers scheduled at all — hard reject
                    flash(capacity_msg, "warning")
                    return redirect(url_for("client.index"))

                # Determine booking status based on capacity
                same_day = _is_same_day(booking_date)
                booking_status = 'requested'
                if not same_day and not available and can_waitlist:
                    booking_status = 'waitlisted'

                dog = db.session.get(Dog, dog_id)
                new_booking = Booking(
                    user_id=user.id,
                    dog_id=dog_id,
                    service_type_id=default_service.id,
                    date=booking_date,
                    slot=booking_slot,
                    status=booking_status
                )
                db.session.add(new_booking)
                db.session.flush()  # get ID before notifications
                batch_id = uuid.uuid4().hex
                record_booking_created(new_booking, actor_id=current_user.id, batch_id=batch_id)

                if booking_status == 'waitlisted':
                    admins = User.query.filter_by(is_admin=True).all()
                    date_str_fmt = booking_date.strftime('%a %-d %b')
                    dog_name = dog.name if dog else 'a dog'
                    for admin in admins:
                        create_notification(
                            recipient_id=admin.id,
                            notification_type='booking_requested',
                            title=f'New booking request for {date_str_fmt}',
                            body=f'{current_user.firstname} requested {booking_slot} for {dog_name}',
                            link='/admin',
                            sender_id=current_user.id,
                        )
                    # Client (actor) notification — without this the bell shows
                    # nothing after a waitlist and the client sees only the
                    # ephemeral flash.
                    create_notification(
                        recipient_id=current_user.id,
                        notification_type='booking_requested',
                        title=f"{dog_name}'s {booking_slot.lower()} walk on {date_str_fmt} is on the waitlist",
                        body="We'll let you know when a spot opens up.",
                        link='/',
                    )
                    _notify_co_owners_of_booking(new_booking, dog_name, confirmed=False)
                    db.session.commit()
                    flash(f"All slots are currently full for {booking_slot} on {booking_date.strftime('%d %b')}. "
                          f"You've been added to the waitlist — we'll let you know if a spot opens up.", "info")
                else:
                    auto_confirmed = _maybe_auto_confirm(new_booking, dog, same_day=same_day, batch_id=batch_id)
                    db.session.commit()
                    if auto_confirmed:
                        flash(f"Booking confirmed for {booking_slot} on {booking_date.strftime('%d %b')}!", "success")
                    elif same_day:
                        flash(f"Same-day request submitted — {current_app.config['OWNER_FIRSTNAME']} will confirm shortly.", "info")
                    else:
                        flash("Booking request submitted — we'll confirm it shortly.", "success")
                return redirect(url_for("client.index"))
    
    has_drop_in_walkers = Walker.query.join(User).filter(
        Walker.does_drop_ins == True,
        User.active == True,
    ).first() is not None

    return render_template("index.html", user=user, client=user.client, dogs=user_dogs,
                           bookings=upcoming_bookings, form=form,
                           has_drop_in_walkers=has_drop_in_walkers,
                           today=today) # type: ignore


@client_bp.route("/book", methods=["POST"])
@login_required
def book():
    """AJAX single booking endpoint — returns JSON, no page reload.

    Accepts JSON body: { "date": "YYYY-MM-DD", "slot": "Morning"|"Afternoon" }
    Returns: { "success": bool, "message": str, "booking": {...} }
    """
    data = request.get_json(silent=True) or {}
    booking_date_str = data.get('date', '').strip()
    booking_slot     = data.get('slot', '').strip()

    # ── Validate inputs ───────────────────────────────────────────────────────
    if not booking_date_str or not booking_slot:
        return jsonify({'success': False, 'message': 'Date and slot are required.'}), 400

    try:
        booking_date = date_type.fromisoformat(booking_date_str)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400

    today   = datetime.now(timezone.utc).date()

    if booking_date < today:
        return jsonify({'success': False, 'message': 'Booking date cannot be in the past.'}), 400
    if booking_slot not in ('Morning', 'Afternoon'):
        return jsonify({'success': False, 'message': 'Invalid slot selected.'}), 400

    user_dogs = Dog.query.join(DogOwner).filter(DogOwner.user_id == current_user.id).all()
    if not user_dogs:
        return jsonify({'success': False, 'message': 'No dog found on your account. Please add a dog before booking.'}), 400

    try:
        dog = _resolve_dog(user_dogs, data.get('dog_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    dog_id = dog.id

    # ── Service type + duplicate / cap checks ────────────────────────────────
    default_service = ServiceType.query.filter_by(slug=ServiceType.WALK, active=True).first()
    if not default_service:
        return jsonify({'success': False, 'message': 'No service type available. Please contact support.'}), 500

    active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
    existing = Booking.query.filter(
        Booking.dog_id == dog_id,
        Booking.date   == booking_date,
        Booking.slot   == booking_slot,
        Booking.status.in_(active_statuses),
    ).first()
    if existing:
        svc_label = existing.service_type.name.lower() if existing.service_type else 'booking'
        return jsonify({'success': False, 'message': f'{dog.name} already has a {svc_label} booked for that slot.'}), 409

    day_count = Booking.query.filter(
        Booking.dog_id == dog_id,
        Booking.date   == booking_date,
        Booking.status.in_(active_statuses),
    ).count()
    if day_count >= 2:
        return jsonify({'success': False, 'message': 'This dog already has two bookings on that date.'}), 409

    # ── Create + auto-assign ─────────────────────────────────────────────────
    try:
        same_day = _is_same_day(booking_date)
        closed, close_msg = is_date_closed(booking_date)
        if closed:
            return jsonify({'success': False, 'message': close_msg}), 409

        batch_id = uuid.uuid4().hex
        try:
            new_booking, auto_confirmed = create_booking(
                dog=dog, user_id=current_user.id, date=booking_date, slot=booking_slot,
                service=default_service, actor_id=current_user.id, batch_id=batch_id,
                same_day=same_day,
            )
        except CapacityError as e:
            return jsonify({'success': False, 'message': str(e)}), 409

        booking_status = new_booking.status
        date_str_fmt   = booking_date.strftime('%a %-d %b')

        if booking_status == 'waitlisted':
            admins = User.query.filter_by(is_admin=True).all()
            for admin in admins:
                create_notification(
                    recipient_id      = admin.id,
                    notification_type = 'booking_requested',
                    title             = f'New booking request for {date_str_fmt}',
                    body              = f'{current_user.firstname} requested {booking_slot} for {dog.name}',
                    link              = '/admin',
                    sender_id         = current_user.id,
                )
            create_notification(
                recipient_id      = current_user.id,
                notification_type = 'booking_requested',
                title             = f"{dog.name}'s {booking_slot.lower()} walk on {date_str_fmt} is on the waitlist",
                body              = "We'll let you know when a spot opens up.",
                link              = '/',
            )
            _notify_co_owners_of_booking(new_booking, dog.name, confirmed=False)
            message = (f"All slots are full — you've been added to the waitlist "
                       f"for {booking_slot} on {booking_date.strftime('%d %b')}.")
        elif auto_confirmed:
            booking_status = 'confirmed'
            walker = new_booking.walker
            walker_first = walker.user.firstname if walker and walker.user else None
            batch = NotificationBatch(actor_id=current_user.id)
            batch.add(current_user.id, 'booking_confirmed',
                      dog_name=dog.name, slot=booking_slot, date=booking_date,
                      walker_name=walker_first, svc_label='walk')
            for admin in User.query.filter_by(is_admin=True).all():
                batch.add(admin.id, 'booking_confirmed',
                          actor_first=current_user.firstname,
                          link=f'/admin/clients/{current_user.id}',
                          dog_name=dog.name, slot=booking_slot, date=booking_date,
                          walker_name=walker_first, svc_label='walk')
            if walker and walker.user_id != current_user.id:
                batch.add(walker.user_id, 'walker_assigned',
                          dog_name=dog.name, slot=booking_slot, date=booking_date,
                          svc_label='walk')
            batch.flush()
            _notify_co_owners_of_booking(new_booking, dog.name, confirmed=True)
            message = f"Booking confirmed for {booking_slot} on {booking_date.strftime('%d %b')}!"
        else:
            ntype = 'same_day_request' if same_day else 'booking_requested'
            title_prefix = 'Same-day request — ' if same_day else ''
            for admin in User.query.filter_by(is_admin=True).all():
                create_notification(
                    recipient_id      = admin.id,
                    notification_type = ntype,
                    title             = f"{title_prefix}{current_user.firstname} requested {dog.name}'s {booking_slot.lower()} walk on {date_str_fmt}",
                    link              = '/admin',
                    sender_id         = current_user.id,
                )
            create_notification(
                recipient_id      = current_user.id,
                notification_type = ntype,
                title             = f"{dog.name}'s {booking_slot.lower()} walk on {date_str_fmt} has been requested",
                body              = (f"Same-day request — {current_app.config['OWNER_FIRSTNAME']} will confirm shortly."
                                     if same_day else "We'll confirm shortly."),
                link              = '/',
            )
            _notify_co_owners_of_booking(new_booking, dog.name, confirmed=False)
            message = (f"Same-day request submitted — {current_app.config['OWNER_FIRSTNAME']} will confirm shortly."
                       if same_day else "Booking request submitted — we'll confirm it shortly.")

        db.session.commit()

        walker_name = None
        if new_booking.walker_id and new_booking.walker:
            walker_name = new_booking.walker.user.firstname

        has_pickup_notes = bool(dog and dog.pickup_instructions)

        return jsonify({
            'success': True,
            'status':  booking_status,
            'message': message,
            'booking': {
                'id':               new_booking.id,
                'date_display':     booking_date.strftime('%a %-d %b'),
                'date_iso':         booking_date.isoformat(),
                'slot':             booking_slot,
                'status':           new_booking.status,
                'dog_id':           dog_id,
                'walker_name':      walker_name,
                'has_pickup_notes': has_pickup_notes,
            },
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f'AJAX booking error for user {current_user.id}: {e}')
        return jsonify({'success': False, 'message': 'An error occurred. Please try again.'}), 500


@client_bp.route("/book_both", methods=["POST"])
@login_required
def book_both():
    """AJAX endpoint: request both Morning and Afternoon for a single date.

    Accepts JSON: { "date": "YYYY-MM-DD" }
    Each slot is booked independently — one can be requested while the other
    is waitlisted if capacity is tight. Returns both results.
    """
    data             = request.get_json(silent=True) or {}
    booking_date_str = data.get('date', '').strip()

    if not booking_date_str:
        return jsonify({'success': False, 'message': 'Date is required.'}), 400

    try:
        booking_date = date_type.fromisoformat(booking_date_str)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400

    today    = datetime.now(timezone.utc).date()
    if booking_date < today:
        return jsonify({'success': False, 'message': 'Booking date cannot be in the past.'}), 400

    user_dogs = Dog.query.join(DogOwner).filter(DogOwner.user_id == current_user.id).all()
    if not user_dogs:
        return jsonify({'success': False, 'message': 'No dog found on your account.'}), 400

    try:
        dog = _resolve_dog(user_dogs, data.get('dog_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    dog_id = dog.id

    default_service = ServiceType.query.filter_by(slug=ServiceType.WALK, active=True).first()
    if not default_service:
        return jsonify({'success': False, 'message': 'No service type available.'}), 500

    active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
    same_day = _is_same_day(booking_date)
    closed, close_msg = is_date_closed(booking_date)
    if closed:
        return jsonify({'success': False, 'message': close_msg}), 409

    created = []   # (slot, status, booking_obj)
    skipped = []   # slot names skipped (duplicate / no walkers)

    for slot in ('Morning', 'Afternoon'):
        # Skip if any active booking already exists for this slot (any service type)
        if Booking.query.filter(
            Booking.dog_id == dog_id,
            Booking.date   == booking_date,
            Booking.slot   == slot,
            Booking.status.in_(active_statuses),
        ).first():
            skipped.append(slot)
            continue

        acquire_booking_lock(default_service.slug, booking_date, slot)
        available, can_waitlist, _ = check_availability(default_service, booking_date, slot)
        if not available and not can_waitlist and not same_day:
            skipped.append(slot)
            continue

        if same_day:
            status = 'requested'
        else:
            status = 'waitlisted' if (not available and can_waitlist) else 'requested'
        b = Booking(
            user_id         = current_user.id,
            dog_id          = dog_id,
            service_type_id = default_service.id,
            date            = booking_date,
            slot            = slot,
            status          = status,
        )
        db.session.add(b)
        created.append((slot, status, b))

    if not created:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'No new bookings created — slots may already be booked.'}), 409

    db.session.flush()  # get IDs before notifications

    # One batch_id ties together both slots of this single book-both action so
    # the activity feed can cluster them (NOTIFICATIONS.md §9.2, D4).
    batch_id = uuid.uuid4().hex
    for _slot, _status, b in created:
        record_booking_created(b, actor_id=current_user.id, batch_id=batch_id)

    # Auto-assign or notify admins for each booking independently. We pass
    # notify=False to _maybe_auto_confirm and compose one consolidated
    # client + admin notification below, so the bell shows a single entry
    # describing the whole booking action rather than one per slot (and so
    # the waitlisted slot isn't silently dropped when its sibling confirms).
    date_str_fmt = booking_date.strftime('%a %-d %b')
    final_created = []
    pending_slots = []
    for slot, status, b in created:
        if status == 'waitlisted':
            pending_slots.append((slot, status, b))
            _notify_co_owners_of_booking(b, dog.name, confirmed=False)
            # Keep waitlisted slots in final_created so the response payload
            # and the consolidated client notification cover them too.
            final_created.append((slot, b.status, b))
        else:
            auto_confirmed = _maybe_auto_confirm(b, dog, notify=False, same_day=same_day,
                                                 batch_id=batch_id)
            final_created.append((slot, b.status, b))
            if not auto_confirmed:
                pending_slots.append((slot, status, b))

    # Admin notifications. For a same-day request, admins get a single urgent
    # same_day_request notice instead of the grouped booking_requested — emitting
    # both (the old behaviour) double-notified admins for one action (F3). On
    # same-day every created slot is 'requested' (auto-assign is skipped), so the
    # same_day_request fully covers the admin side.
    admins = User.query.filter_by(is_admin=True).all()
    if not same_day:
        admin_batch = NotificationBatch(actor_id=current_user.id)
        admin_link  = f'/admin/clients/{current_user.id}'
        for slot, _, b in final_created:
            walker_first = b.walker.user.firstname if b.status == 'confirmed' and b.walker and b.walker.user else None
            if b.status == 'confirmed':
                kind = 'booking_confirmed'
            elif b.status == 'waitlisted':
                kind = 'booking_waitlisted'
            else:
                kind = 'booking_requested'
            for admin in admins:
                admin_batch.add(admin.id, kind,
                                actor_first=current_user.firstname,
                                link=admin_link,
                                dog_name=dog.name, slot=slot, date=booking_date,
                                walker_name=walker_first)
        admin_batch.flush()
    elif pending_slots:
        # summarise() has no same_day_request kind, so this stays a direct
        # create_notification with the urgency marker.
        pending_slot_names = [s.lower() for s, _, _ in pending_slots]
        for admin in admins:
            create_notification(
                recipient_id=admin.id,
                notification_type='same_day_request',
                title=f"Same-day request — {current_user.firstname} requested {dog.name}'s {' & '.join(pending_slot_names)} walk{'s' if len(pending_slots) > 1 else ''} on {date_str_fmt}",
                link='/admin',
                sender_id=current_user.id,
            )

    # Single consolidated client notification covering every created slot.
    # Closes the bug where mixed outcomes (one confirmed + one waitlisted)
    # only surfaced the confirmed slot in the bell.
    if final_created:
        title, body, ntype = _summarise_book_both_for_client(
            final_created, dog.name, booking_date
        )
        create_notification(
            recipient_id      = current_user.id,
            notification_type = ntype,
            title             = title,
            body              = body,
            link              = '/',
        )

    # Notify auto-assigned walkers (§7.9): one grouped walker_assigned per walker.
    walker_batch = NotificationBatch(actor_id=current_user.id)
    for slot, _status, b in final_created:
        if b.status == 'confirmed' and b.walker_id and b.walker:
            wuid = b.walker.user_id
            if wuid and wuid != current_user.id:
                walker_batch.add(wuid, 'walker_assigned',
                                 dog_name=dog.name, slot=slot, date=booking_date)
    walker_batch.flush()

    db.session.commit()
    created = final_created

    has_pickup_notes = bool(dog and dog.pickup_instructions)

    # Build response
    booking_payload = []
    for slot, status, b in created:
        booking_payload.append({
            'id':               b.id,
            'date_display':     booking_date.strftime('%a %-d %b'),
            'date_iso':         booking_date.isoformat(),
            'slot':             slot,
            'status':           b.status,
            'dog_id':           dog_id,
            'walker_name':      b.walker.user.firstname if b.walker_id and b.walker else None,
            'has_pickup_notes': has_pickup_notes,
        })

    parts = []
    for slot, status, _ in created:
        label = 'AM' if slot == 'Morning' else 'PM'
        word = 'waitlisted' if status == 'waitlisted' else ('confirmed' if status == 'confirmed' else 'requested')
        parts.append(f'{label} {word}')
    if skipped:
        parts.append(f'{", ".join(skipped)} skipped (already booked)')

    return jsonify({
        'success':  True,
        'bookings': booking_payload,
        'message':  ', '.join(parts) + '.',
    })


@client_bp.route("/book_drop_in", methods=["POST"])
@login_required
def book_drop_in():
    """AJAX endpoint: request a drop-in visit for a given date + slot.

    Accepts JSON: { "date": "YYYY-MM-DD", "slot": "Morning"|"Afternoon" }
    Returns JSON response (success/failure + booking info).
    """
    from app.capacity import check_availability as _check

    data             = request.get_json(silent=True) or {}
    booking_date_str = data.get('date', '').strip()
    booking_slot     = data.get('slot', '').strip()

    if not booking_date_str:
        return jsonify({'success': False, 'message': 'Date is required.'}), 400
    if booking_slot not in ('Morning', 'Afternoon'):
        return jsonify({'success': False, 'message': 'Invalid slot. Choose Morning or Afternoon.'}), 400

    try:
        booking_date = date_type.fromisoformat(booking_date_str)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400

    today    = datetime.now(timezone.utc).date()
    if booking_date < today:
        return jsonify({'success': False, 'message': 'Booking date cannot be in the past.'}), 400

    user_dogs = Dog.query.join(DogOwner).filter(DogOwner.user_id == current_user.id).all()
    if not user_dogs:
        return jsonify({'success': False, 'message': 'No dog found on your account.'}), 400

    try:
        dog = _resolve_dog(user_dogs, data.get('dog_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    dog_id = dog.id

    drop_in_service = ServiceType.query.filter_by(slug=ServiceType.DROP_IN, active=True).first()
    if not drop_in_service:
        return jsonify({'success': False, 'message': 'Drop-in service is not currently available.'}), 503

    # Prevent duplicate (any service type for this slot)
    active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
    existing = Booking.query.filter(
        Booking.dog_id == dog_id,
        Booking.date   == booking_date,
        Booking.slot   == booking_slot,
        Booking.status.in_(active_statuses),
    ).first()
    if existing:
        svc_label = existing.service_type.name.lower() if existing.service_type else 'booking'
        return jsonify({'success': False, 'message': f'{dog.name} already has a {svc_label} booked for that slot.'}), 409

    acquire_booking_lock(drop_in_service.slug, booking_date, booking_slot)
    available, can_waitlist, capacity_msg = _check(drop_in_service, booking_date, booking_slot)
    same_day = _is_same_day(booking_date)
    closed, close_msg = is_date_closed(booking_date)
    if closed:
        return jsonify({'success': False, 'message': close_msg}), 409
    if not available and not can_waitlist and not same_day:
        return jsonify({'success': False, 'message': capacity_msg}), 409

    if same_day:
        booking_status = 'requested'
    else:
        booking_status = 'waitlisted' if (not available and can_waitlist) else 'requested'

    new_booking = Booking(
        user_id         = current_user.id,
        dog_id          = dog_id,
        service_type_id = drop_in_service.id,
        date            = booking_date,
        slot            = booking_slot,
        status          = booking_status,
    )
    db.session.add(new_booking)
    db.session.flush()
    batch_id = uuid.uuid4().hex
    record_booking_created(new_booking, actor_id=current_user.id, batch_id=batch_id)
    db.session.commit()

    # Notify admins and co-owners
    date_str_fmt = booking_date.strftime('%a %-d %b')
    slot_lower = booking_slot.lower()
    ntype = 'same_day_request' if same_day else 'booking_requested'
    title_prefix = 'Same-day ' if same_day else 'New '
    for admin in User.query.filter_by(is_admin=True).all():
        create_notification(
            recipient_id      = admin.id,
            notification_type = ntype,
            title             = f'{title_prefix}drop-in request for {date_str_fmt}',
            body              = f'{current_user.firstname} requested {booking_slot} drop-in for {dog.name}',
            link              = '/admin/drop-in-board',
            sender_id         = current_user.id,
        )

    # Client (actor) notification — closes the gap where the client got no
    # bell entry for drop-in requests/waitlists.
    if booking_status == 'waitlisted':
        client_title = f"{dog.name}'s {slot_lower} drop-in on {date_str_fmt} is on the waitlist"
        client_body  = "We'll let you know when a spot opens up."
    else:
        client_title = f"{dog.name}'s {slot_lower} drop-in on {date_str_fmt} has been requested"
        client_body  = (
            f"Same-day request — {current_app.config['OWNER_FIRSTNAME']} will confirm shortly."
            if same_day else
            "We'll confirm shortly."
        )
    create_notification(
        recipient_id      = current_user.id,
        notification_type = ntype,
        title             = client_title,
        body              = client_body,
        link              = '/',
    )

    _notify_co_owners_of_booking(new_booking, dog.name, confirmed=False)
    db.session.commit()

    if same_day:
        message = f"Same-day drop-in request submitted — {current_app.config['OWNER_FIRSTNAME']} will confirm shortly."
    elif booking_status == 'waitlisted':
        message = (f"All drop-in slots are full for {booking_slot} on "
                   f"{booking_date.strftime('%d %b')}. You've been added to the waitlist.")
    else:
        message = "Drop-in request submitted — we'll confirm shortly."

    return jsonify({
        'success':  True,
        'status':   booking_status,
        'message':  message,
        'booking':  {
            'id':           new_booking.id,
            'dog_id':       dog_id,
            'date_display': booking_date.strftime('%a %-d %b'),
            'date_iso':     booking_date.isoformat(),
            'slot':         booking_slot,
            'status':       booking_status,
            'is_drop_in':   True,
        },
    })


@client_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Display and manage client profile, address, notifications and dog info."""
    if not has_client_access(current_user):
        return redirect(url_for('client.index'))

    client = Client.query.filter_by(user_id=current_user.id).first()

    # Get all primary dogs (user is the main owner — can edit photo/details)
    primary_ownerships = DogOwner.query.filter_by(user_id=current_user.id, role='primary').all()
    primary_dogs = []
    for _po in primary_ownerships:
        _d = db.session.get(Dog, _po.dog_id)
        if _d:
            primary_dogs.append(_d)
    dog = primary_dogs[0] if primary_dogs else None  # first primary dog (for form hidden fields)

    # Secondary-only owners (co-owners with no primary dog of their own) should be
    # allowed to view/edit their profile without going through onboarding.
    is_secondary_only = (not primary_ownerships and
                         DogOwner.query.filter_by(user_id=current_user.id, role='secondary').first() is not None)

    if not is_secondary_only and (not client or not client.onboarding_completed):
        return redirect(url_for('client.onboard'))

    # Get secondary dogs (user has shared access — read-only on the profile)
    secondary_ownerships = DogOwner.query.filter_by(user_id=current_user.id, role='secondary').all()
    secondary_dogs = []
    for so in secondary_ownerships:
        secondary_dog = db.session.get(Dog, so.dog_id)
        if not secondary_dog:
            continue
        primary_o = DogOwner.query.filter_by(dog_id=so.dog_id, role='primary').first()
        primary_user = db.session.get(User, primary_o.user_id) if primary_o else None
        primary_client = Client.query.filter_by(user_id=primary_o.user_id).first() if primary_o else None
        secondary_dogs.append({'dog': secondary_dog, 'primary_owner': primary_user, 'primary_client': primary_client})

    # Booking stats for the profile sidebar
    # Use dog_ids so secondary owners see all bookings for their shared dog,
    # not just bookings they personally created.
    from datetime import date
    today_date = date.today()
    month_start = date(today_date.year, today_date.month, 1)
    if today_date.month == 12:
        month_end = date(today_date.year + 1, 1, 1)
    else:
        month_end = date(today_date.year, today_date.month + 1, 1)

    accessible_dog_ids = get_accessible_dog_ids(current_user.id)

    month_bookings = Booking.query.filter(
        Booking.dog_id.in_(accessible_dog_ids),
        Booking.date >= month_start,
        Booking.date < month_end,
        Booking.status.notin_(['cancelled', 'rejected'])
    ).all()
    confirmed_this_month = sum(1 for b in month_bookings if b.status in ('confirmed', 'completed'))
    pending_this_month = sum(1 for b in month_bookings if b.status in ('requested', 'waitlisted'))

    next_booking = Booking.query.filter(
        Booking.dog_id.in_(accessible_dog_ids),
        Booking.date >= today_date,
        Booking.status == 'confirmed'
    ).order_by(Booking.date).first()

    total_completed = Booking.query.filter(
        Booking.dog_id.in_(accessible_dog_ids),
        Booking.status == 'completed'
    ).count()

    booking_stats = {
        'confirmed_this_month': confirmed_this_month,
        'pending_this_month': pending_this_month,
        'total_this_month': len(month_bookings),
        'next_booking': next_booking,
        'total_completed': total_completed,
        'month_name': today_date.strftime('%B'),
    }

    form = ProfileForm()

    if form.validate_on_submit():
        try:
            # Personal info
            current_user.firstname = form.firstname.data.strip()
            current_user.lastname = form.lastname.data.strip()

            # Create a Client record on first save if this is a secondary-only owner
            if not client:
                client = Client(user_id=current_user.id, onboarding_completed=True,
                                onboarding_completed_at=datetime.now(timezone.utc))
                db.session.add(client)

            # Address
            client.street_address = form.address_line_1.data.strip()
            if form.address_line_2.data:
                client.street_address += '\n' + form.address_line_2.data.strip()
            if form.address_line_3.data:
                client.street_address += '\n' + form.address_line_3.data.strip()
            client.postal_code = form.postcode.data.strip()
            client.maps_url = form.maps_url.data.strip() if form.maps_url.data else None

            # Pickup notes live on the dog, not the client
            if primary_dogs:
                # Per-dog raw fields (named pickup_instructions_{id}) in the template
                for _pd in primary_dogs:
                    _val = request.form.get(f'pickup_instructions_{_pd.id}', '').strip() or None
                    _pd.pickup_instructions = _val
            elif secondary_dogs:
                # Secondary-only path: update first shared dog's instructions via form field
                secondary_dogs[0]['dog'].pickup_instructions = (
                    form.pickup_instructions.data.strip() if form.pickup_instructions.data else None
                )

            # Notifications — email toggle controls newsletter subscription
            current_user.email_marketing = bool(form.notify_email.data)
            current_user.notification_preference = 'email'

            # Dog info — name/gender/breed are admin-managed (round-trip via hidden fields)
            # dob and allergies are client-editable via per-dog raw fields
            if dog:
                dog.name = form.dog_name.data.strip()
                dog.gender = form.dog_gender.data.strip()
                dog.breed = form.dog_breed.data.strip() if form.dog_breed.data else ""

            for _pd in primary_dogs:
                # Handle photo upload
                if 'file' in request.files and request.files['file'].filename:
                    try:
                        pic_filename = process_dog_photo(request.files['file'])
                        if pic_filename:
                            dog.pic = pic_filename
                    except ValueError as e:
                        flash(f"Upload error: {str(e)}", "error")
                        return render_template("profile.html", form=form, dog=dog, primary_dogs=primary_dogs, client=client, booking_stats=booking_stats, secondary_dogs=secondary_dogs, today=datetime.now().strftime("%Y-%m-%d"))
                    except Exception as e:
                        logging.error(f"Error processing uploaded file: {e}")
                        flash("Error processing your image. Please try a different file.", "error")
                        return render_template("profile.html", form=form, dog=dog, primary_dogs=primary_dogs, client=client, booking_stats=booking_stats, secondary_dogs=secondary_dogs, today=datetime.now().strftime("%Y-%m-%d"))

            db.session.commit()
            flash("Profile updated successfully!", "success")
            return redirect(url_for('client.profile'))

        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating profile for user {current_user.email}: {e}")
            flash("There was an error saving your changes. Please try again.", "error")

    elif request.method == 'GET':
        # Pre-fill form with existing data
        form.firstname.data = current_user.firstname
        form.lastname.data = current_user.lastname

        # Split street_address back into lines
        if client and client.street_address:
            address_lines = client.street_address.split('\n')
            form.address_line_1.data = address_lines[0] if len(address_lines) > 0 else ''
            form.address_line_2.data = address_lines[1] if len(address_lines) > 1 else ''
            form.address_line_3.data = address_lines[2] if len(address_lines) > 2 else ''
        if client:
            form.postcode.data = client.postal_code
            form.maps_url.data = client.maps_url

        # Pickup notes: primary dogs use per-dog raw fields in template;
        # secondary-only path pre-fills the form field for backward compat
        if not primary_dogs and secondary_dogs:
            form.pickup_instructions.data = secondary_dogs[0]['dog'].pickup_instructions

        # Notifications
        form.notify_email.data = current_user.email_marketing

        # Dog info
        if dog:
            form.dog_name.data = dog.name
            form.dog_gender.data = dog.gender
            form.dog_breed.data = dog.breed
            form.dog_dob.data = dog.date_of_birth
            form.dog_allergies.data = dog.allergies

    return render_template("profile.html", form=form, dog=dog, primary_dogs=primary_dogs, client=client, booking_stats=booking_stats, secondary_dogs=secondary_dogs, today=datetime.now().strftime("%Y-%m-%d"))


@client_bp.route("/monthly-summary")
@login_required
def monthly_summary():
    """Client-facing monthly summary: bookings and estimated charges for a given month."""
    from app.utils.invoicing import invoice_for_client
    from app.utils.pricing import build_line_items, build_double_slot_discounts
    from app.models import PricingConfig

    if not has_client_access(current_user):
        return redirect(url_for('client.index'))

    today = date_type.today()
    month_str = request.args.get('month', f'{today.year}-{today.month:02d}')
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
        if not (1 <= month <= 12):
            raise ValueError
    except (ValueError, IndexError):
        year, month = today.year, today.month

    # Cap at current month — no peeking ahead
    if (year, month) > (today.year, today.month):
        year, month = today.year, today.month

    month_start = date_type(year, month, 1)
    month_end   = date_type(year + (month // 12), (month % 12) + 1, 1)

    all_configs = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= month_end)
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )

    inv = invoice_for_client(current_user.id, month_start, month_end, all_configs)
    if inv is None:
        inv = {
            'confirmed': [], 'late_cancels': [], 'all_billable': [],
            'total_walks': 0, 'total_drop_ins': 0, 'total_cancels': 0,
            'total_billable': 0, 'doubles': 0, 'subtotal': 0.0,
        }

    late_cancel_ids = {b.id for b in inv['late_cancels']}
    line_items = build_line_items(inv['all_billable'], late_cancel_ids, all_configs)
    discounts = build_double_slot_discounts(inv['all_billable'], all_configs)

    # Month nav
    if month == 1:
        prev_month = f'{year - 1}-12'
    else:
        prev_month = f'{year}-{month - 1:02d}'
    if month == 12:
        next_month = f'{year + 1}-01'
    else:
        next_month = f'{year}-{month + 1:02d}'
    at_current = (year == today.year and month == today.month)

    return render_template(
        'client_monthly_summary.html',
        inv=inv,
        line_items=line_items,
        discounts=discounts,
        month_start=month_start,
        prev_month=prev_month,
        next_month=next_month,
        at_current=at_current,
        today=today,
    )


@client_bp.route("/profile/upload-dog-photo", methods=["POST"])
@login_required
def upload_dog_photo():
    """AJAX endpoint: accept a cropped image blob and save it as the dog's photo.

    Expects a multipart POST with a 'file' field containing the canvas blob
    from Cropper.js. Returns JSON {success, url} or {success, error}.
    """
    dog_id_param = request.args.get('dog_id') or request.form.get('dog_id')
    if dog_id_param:
        try:
            dog_id_param = int(dog_id_param)
        except (TypeError, ValueError):
            return jsonify(success=False, error="Invalid dog ID"), 400
        dog_owner = DogOwner.query.filter_by(
            user_id=current_user.id, dog_id=dog_id_param, role='primary'
        ).first()
    else:
        dog_owner = DogOwner.query.filter_by(user_id=current_user.id, role='primary').first()
    dog = db.session.get(Dog, dog_owner.dog_id) if dog_owner else None
    if not dog:
        return jsonify(success=False, error="Dog profile not found"), 404

    if 'file' not in request.files:
        return jsonify(success=False, error="No file provided"), 400

    try:
        filename = process_cropped_photo(request.files['file'])
        if not filename:
            return jsonify(success=False, error="Empty file"), 400

        dog.pic = filename
        db.session.commit()

        url = url_for('static', filename=f'uploads/dogs/{filename}')
        logging.info(f"Dog photo updated for client {current_user.email}: {filename}")
        return jsonify(success=True, url=url)

    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error saving cropped dog photo for {current_user.email}: {e}")
        return jsonify(success=False, error="Server error saving photo"), 500


@client_bp.route("/profile/upload-profile-photo", methods=["POST"])
@login_required
def upload_profile_photo():
    """AJAX endpoint: accept a cropped image blob and save it as the user's profile photo.

    Returns JSON {success, url} or {success, error}.
    """
    if 'file' not in request.files:
        return jsonify(success=False, error="No file provided"), 400

    try:
        filename = process_cropped_photo(request.files['file'], subfolder='profiles')
        if not filename:
            return jsonify(success=False, error="Empty file"), 400

        current_user.profile_pic = filename
        db.session.commit()

        url = url_for('static', filename=f'uploads/profiles/{filename}')
        logging.info(f"Profile photo updated for user {current_user.email}: {filename}")
        return jsonify(success=True, url=url)

    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error saving profile photo for {current_user.email}: {e}")
        return jsonify(success=False, error="Server error saving photo"), 500


@client_bp.route("/profile/update-pickup", methods=["POST"])
@login_required
def update_pickup():
    """AJAX: save pickup instructions (per dog) and newsletter preference."""
    # has_client_access also lets dual-role walkers (role='walker' with a
    # Client record) through. A bare role == 'client' check rejects them
    # even though they own dogs and use the client view.
    if not has_client_access(current_user):
        return jsonify(success=False, error="Forbidden"), 403

    client = Client.query.filter_by(user_id=current_user.id).first()
    primary_ownerships = DogOwner.query.filter_by(user_id=current_user.id, role='primary').all()
    primary_dogs = [db.session.get(Dog, po.dog_id) for po in primary_ownerships]
    primary_dogs = [d for d in primary_dogs if d]

    secondary_ownerships = DogOwner.query.filter_by(user_id=current_user.id, role='secondary').all()

    try:
        if primary_dogs:
            for _pd in primary_dogs:
                _val = request.form.get(f'pickup_instructions_{_pd.id}', '').strip() or None
                _pd.pickup_instructions = _val
        elif secondary_ownerships:
            sec_dog = db.session.get(Dog, secondary_ownerships[0].dog_id)
            if sec_dog:
                sec_dog.pickup_instructions = request.form.get('pickup_instructions', '').strip() or None

        current_user.email_marketing = request.form.get('notify_email') == 'true'
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating pickup notes for {current_user.email}: {e}")
        return jsonify(success=False, error="Server error"), 500


@client_bp.route("/profile/dog/<int:dog_id>/update-details", methods=["POST"])
@login_required
def update_dog_details(dog_id):
    """AJAX: save DOB and health notes for a dog the current user owns as primary."""
    ownership = DogOwner.query.filter_by(dog_id=dog_id, user_id=current_user.id, role='primary').first()
    if not ownership:
        return jsonify(success=False, error="Not authorised"), 403

    dog = db.session.get(Dog, dog_id)
    if not dog:
        return jsonify(success=False, error="Dog not found"), 404

    try:
        from datetime import date as _date_type
        dob_str = request.form.get('dob', '').strip()
        dog.date_of_birth = _date_type.fromisoformat(dob_str) if dob_str else None
        dog.allergies = request.form.get('health_notes', '').strip() or None
        db.session.commit()
        return jsonify(success=True)
    except ValueError:
        db.session.rollback()
        return jsonify(success=False, error="Invalid date"), 400
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating dog details for dog {dog_id}: {e}")
        return jsonify(success=False, error="Server error"), 500


@client_bp.route("/account-pending")
@login_required
def account_pending():
    """Holding page for client users whose account exists but has no Client record yet.

    This happens when an admin creates a User login but hasn't filled in the
    client details (address / dog info) in the admin panel.  The before_request
    guard redirects them here instead of to /onboard, which requires a Client row.
    """
    from app.models import Client
    # If the Client record appears (admin just finished setting up), redirect onward.
    client = Client.query.filter_by(user_id=current_user.id).first()
    if client:
        if client.onboarding_completed:
            return redirect(url_for('client.index'))
        return redirect(url_for('client.onboard'))
    return render_template('account_pending.html')


@client_bp.route("/onboard", methods=["GET", "POST"])
@login_required
def onboard():
    """Handle client onboarding.

    If the admin has already filled in address + dog info, onboarding_completed
    will already be True and this route redirects away immediately.  Otherwise
    the client fills in whatever the admin left blank.

    If the admin created a dog record during account setup, we update that
    existing dog rather than creating a duplicate.
    """
    if current_user.role != 'client':
        flash("Onboarding is only required for clients.", "info")
        return redirect(url_for('client.index'))

    client = Client.query.filter_by(user_id=current_user.id).first()
    if client and client.onboarding_completed:
        return redirect(url_for('client.index'))

    # Check for a dog already created by the admin
    existing_dog_owner = DogOwner.query.filter_by(user_id=current_user.id, role='primary').first()
    existing_dog = db.session.get(Dog, existing_dog_owner.dog_id) if existing_dog_owner else None

    has_address = bool(client and client.street_address)
    has_dog_info = bool(existing_dog and existing_dog.name and existing_dog.gender)

    form = OnboardingForm()

    if form.validate_on_submit():
        try:
            if not client:
                client = Client(user_id=current_user.id)
                db.session.add(client)

            # Address
            client.street_address = form.address_line_1.data.strip()
            if form.address_line_2.data:
                client.street_address += '\n' + form.address_line_2.data.strip()
            if form.address_line_3.data:
                client.street_address += '\n' + form.address_line_3.data.strip()
            client.postal_code = form.postcode.data.strip()
            client.maps_url = form.maps_url.data.strip() if form.maps_url.data else None
            client.onboarding_completed = True
            client.onboarding_completed_at = datetime.now(timezone.utc)

            current_user.notification_preference = 'email'

            # Handle file upload
            pic_filename = None
            if 'file' in request.files:
                try:
                    pic_filename = process_dog_photo(request.files['file'])
                except ValueError as e:
                    logging.error(f"Invalid file upload: {e}")
                    flash(f"Upload error: {str(e)}. Please try a different file.", "error")
                    return render_template("onboarding.html", form=form, existing_dog=existing_dog, has_address=has_address, has_dog_info=has_dog_info, today=datetime.now().strftime('%Y-%m-%d'))
                except Exception as e:
                    logging.error(f"Error processing uploaded file: {e}")
                    flash("There was an error processing your image. Please try a different file.", "error")
                    return render_template("onboarding.html", form=form, existing_dog=existing_dog, has_address=has_address, has_dog_info=has_dog_info, today=datetime.now().strftime('%Y-%m-%d'))

            # Dog: update existing record if admin already created one, else create fresh
            dog_name = form.dog_name.data.strip()
            dog_gender = form.dog_gender.data.strip()
            dog_dob = form.dog_dob.data
            dog_breed = form.dog_breed.data.strip() if form.dog_breed.data else ""
            dog_allergies = form.dog_allergies.data.strip() if form.dog_allergies.data else ""

            pickup_notes = form.pickup_instructions.data.strip() if form.pickup_instructions.data else None
            if existing_dog:
                existing_dog.name = dog_name
                existing_dog.gender = dog_gender
                existing_dog.breed = dog_breed
                existing_dog.allergies = dog_allergies
                existing_dog.date_of_birth = dog_dob
                existing_dog.pickup_instructions = pickup_notes
                if pic_filename:
                    existing_dog.pic = pic_filename
            else:
                new_dog = Dog(
                    name=dog_name,
                    gender=dog_gender,
                    breed=dog_breed,
                    allergies=dog_allergies,
                    date_of_birth=dog_dob,
                    pic=pic_filename,
                    pickup_instructions=pickup_notes,
                )
                db.session.add(new_dog)
                db.session.flush()
                db.session.add(DogOwner(dog_id=new_dog.id, user_id=current_user.id, role='primary'))

            db.session.commit()

            flash(f"Welcome to our platform, {current_user.firstname}! Your profile is now complete.", "success")
            return redirect(url_for('client.index'))

        except Exception as e:
            db.session.rollback()
            logging.error(f"Error during onboarding for user {current_user.email}: {e}")
            logging.debug(f"Exception details: {traceback.format_exc()}")

            if isinstance(e, SQLAlchemyError):
                if isinstance(e, IntegrityError):
                    flash("There was a conflict with existing data. This might be because the information already exists in our system.", "error")
                elif isinstance(e, OperationalError):
                    flash("The database is currently unavailable. Please try again later.", "error")
                else:
                    flash("There was a database error. Please try again.", "error")
            else:
                flash("There was an error saving your information. Please try again.", "error")

    elif request.method == 'GET':
        # Pre-fill anything the admin already entered — address and/or dog
        if client:
            if client.street_address:
                lines = client.street_address.split('\n')
                form.address_line_1.data = lines[0] if len(lines) > 0 else ''
                form.address_line_2.data = lines[1] if len(lines) > 1 else ''
                form.address_line_3.data = lines[2] if len(lines) > 2 else ''
            form.postcode.data = client.postal_code
            form.pickup_instructions.data = existing_dog.pickup_instructions if existing_dog else None
            form.maps_url.data = client.maps_url
        form.notify_email.data = current_user.email_marketing
        if existing_dog:
            form.dog_name.data = existing_dog.name
            form.dog_gender.data = existing_dog.gender
            form.dog_breed.data = existing_dog.breed
            form.dog_dob.data = existing_dog.date_of_birth
            form.dog_allergies.data = existing_dog.allergies

    return render_template("onboarding.html", form=form, existing_dog=existing_dog, has_address=has_address, has_dog_info=has_dog_info, today=datetime.now().strftime('%Y-%m-%d'))


@client_bp.route("/pause-walks/preview")
@login_required
def pause_walks_preview():
    """Return bookings that would be cancelled in a date range (no writes)."""
    if not has_client_access(current_user):
        return jsonify(success=False, error="Forbidden"), 403
    try:
        start = date_type.fromisoformat(request.args.get('start', ''))
        end   = date_type.fromisoformat(request.args.get('end', ''))
    except (ValueError, TypeError):
        return jsonify(success=False, error="Invalid dates"), 400

    today = datetime.now(timezone.utc).date()
    if start <= today:
        return jsonify(success=False, error="Start date must be in the future"), 400
    if end < start:
        return jsonify(success=False, error="End date must be after start date"), 400
    if (end - start).days > 365:
        return jsonify(success=False, error="Range cannot exceed one year"), 400

    # Optional slot filter — accepts repeated ?slots=Morning&slots=Afternoon.
    # 0 or 2 valid values = no filter (Both). Exactly 1 narrows to that slot.
    slot_filter = [s for s in request.args.getlist('slots') if s in ('Morning', 'Afternoon')]

    dog_ids = get_accessible_dog_ids(current_user.id)
    q = Booking.query.filter(
        Booking.dog_id.in_(dog_ids),
        Booking.date >= start,
        Booking.date <= end,
        Booking.status.notin_(['cancelled', 'rejected', 'completed']),
    )
    if len(slot_filter) == 1:
        q = q.filter(Booking.slot == slot_filter[0])
    bookings = q.order_by(Booking.date, Booking.slot).all()

    return jsonify(
        success=True,
        count=len(bookings),
        bookings=[{
            'date': b.date.strftime('%-d %b'),
            'slot': b.slot,
            'dog':  b.dog.name if b.dog else '',
        } for b in bookings],
    )


@client_bp.route("/pause-walks", methods=["POST"])
@login_required
def pause_walks():
    """Cancel all active bookings for the client's dogs within a date range."""
    if not has_client_access(current_user):
        return jsonify(success=False, error="Forbidden"), 403
    try:
        data  = request.get_json(silent=True) or {}
        start = date_type.fromisoformat(data.get('start', ''))
        end   = date_type.fromisoformat(data.get('end', ''))
    except (ValueError, TypeError):
        return jsonify(success=False, error="Invalid dates"), 400

    today = datetime.now(timezone.utc).date()
    if start <= today:
        return jsonify(success=False, error="Start date must be in the future"), 400
    if end < start:
        return jsonify(success=False, error="End date must be after start date"), 400
    if (end - start).days > 365:
        return jsonify(success=False, error="Range cannot exceed one year"), 400

    # Optional slot filter — 0 or 2 valid values = no filter (Both). 1 = narrow.
    slots_raw   = data.get('slots') or []
    slot_filter = [s for s in slots_raw if s in ('Morning', 'Afternoon')]

    dog_ids = get_accessible_dog_ids(current_user.id)
    q = Booking.query.filter(
        Booking.dog_id.in_(dog_ids),
        Booking.date >= start,
        Booking.date <= end,
        Booking.status.notin_(['cancelled', 'rejected', 'completed']),
    )
    if len(slot_filter) == 1:
        q = q.filter(Booking.slot == slot_filter[0])
    bookings = q.order_by(Booking.date).all()

    if not bookings:
        return jsonify(success=True, cancelled_count=0)

    n          = len(bookings)
    admins     = User.query.filter_by(is_admin=True).all()
    admin_ids  = {a.id for a in admins}
    actor_name = current_user.firstname
    admin_link = f'/admin/clients/{current_user.id}'

    # Build the notification batch before bulk_transition clears walker_id.
    # NotificationBatch groups by (recipient_id, kind) so each person gets
    # exactly one grouped notice regardless of how many bookings are in scope.
    notif_batch = NotificationBatch(actor_id=current_user.id)
    for b in bookings:
        if not b.dog:
            continue
        svc_label = 'drop-in' if b.service_type and b.service_type.slug == ServiceType.DROP_IN else 'walk'
        payload = dict(dog_name=b.dog.name, slot=b.slot, date=b.date, svc_label=svc_label)

        for admin in admins:
            notif_batch.add(admin.id, 'booking_cancelled', actor_first=actor_name,
                            link=admin_link, **payload)

        for ownership in DogOwner.query.filter(
            DogOwner.dog_id == b.dog_id,
            DogOwner.user_id != current_user.id,
        ).all():
            co_user = db.session.get(User, ownership.user_id)
            if co_user and not co_user.is_admin:
                notif_batch.add(co_user.id, 'booking_cancelled', actor_first=actor_name, **payload)

        if (b.walker_id and b.walker and b.walker.user_id
                and b.walker.user_id != current_user.id
                and b.walker.user_id not in admin_ids):
            notif_batch.add(b.walker.user_id, 'booking_cancelled', actor_first=actor_name,
                            link='/walker/schedule', **payload)

    # One batch_id ties together every cancellation in this pause action so the
    # activity feed can cluster them (NOTIFICATIONS.md §9.2, D4).
    batch_id = uuid.uuid4().hex
    bulk_transition(bookings, 'cancelled', actor_id=current_user.id,
                    walker_id=None, cancelled_by='client', batch_id=batch_id)

    notif_batch.flush()
    db.session.commit()
    return jsonify(success=True, cancelled_count=n)


@client_bp.route("/cancel_booking", methods=["POST"])
@login_required
def cancel_booking():
    """Cancel a booking.

    Authorization is handled below by user_can_access_booking() — which
    correctly allows the booking creator, any dog co-owner, or admins.
    No early role gate (used to reject dual-role walkers incorrectly).
    """
    try:
        booking_id = request.form.get("booking_id") or request.json.get("booking_id")
        if not booking_id:
            return jsonify(success=False, message="No booking ID provided"), 400
            
        booking = db.session.get(Booking, booking_id)
        if not booking:
            return jsonify(success=False, message="Booking not found"), 404
            
        # Check authorization — allow booking creator, any dog owner, or admins
        if not user_can_access_booking(current_user, booking):
            return jsonify(success=False, message="You are not authorized to cancel this booking"), 403

        is_admin_cancel = current_user.is_admin and booking.user_id != current_user.id
        # Capture the assigned walker's user_id before clearing the FK below —
        # we notify them at the end so they know the walk is off their schedule.
        prior_walker_user_id = booking.walker.user_id if booking.walker else None

        # Late-cancel billing override (admin cancels only). When an admin cancels
        # a booking inside the notice window, bill by default and let them waive
        # via `waive_late_fee` — see app/utils/invoicing.py. Client cancels leave
        # bill_cancellation=None so the default policy applies (unchanged).
        bill_cancellation = _UNSET_BILL
        if is_admin_cancel:
            today = datetime.now(timezone.utc).date()
            if is_late_cancellation(booking, today):
                form = request.form if request.form else (request.get_json(silent=True) or {})
                waive = str(form.get('waive_late_fee', '')).lower() in ('1', 'true', 'on', 'yes')
                bill_cancellation = not waive

        # transition_booking sets status, cancelled_at and logs the BSC row.
        # cancelled_by records who cancelled (admin acting on a client's booking
        # vs the client/owner themselves); walker_id=None unassigns.
        transition_booking(booking, 'cancelled', actor_id=current_user.id,
                            cancelled_by='admin' if is_admin_cancel else 'client',
                            walker_id=None, bill_cancellation=bill_cancellation)
        # Do NOT commit here — notifications are added below and everything
        # commits atomically at the end. An early commit would make the
        # cancellation irreversible if the notification step later raises.

        date_str_fmt = booking.date.strftime('%a %-d %b')
        dog_name = booking.dog.name if booking.dog else 'Unknown dog'

        if is_admin_cancel:
            # Notify the client their walk was cancelled by admin
            service_label = (
                'drop-in'
                if booking.service_type and booking.service_type.slug == ServiceType.DROP_IN
                else 'walk'
            )
            create_notification(
                recipient_id=booking.user_id,
                notification_type='booking_cancelled',
                title=f"{dog_name}'s {booking.slot.lower()} {service_label} on {date_str_fmt} has been cancelled",
                body="Please get in touch if you'd like to discuss.",
                link='/',
                sender_id=current_user.id,
            )
        else:
            # Notify all admins that a client cancelled
            admins = User.query.filter_by(is_admin=True).all()
            client_name = current_user.full_name
            for admin in admins:
                create_notification(
                    recipient_id=admin.id,
                    notification_type='booking_cancelled',
                    title=f"{client_name} cancelled {dog_name}'s {booking.slot.lower()} walk on {date_str_fmt}",
                    link=f'/admin/clients/{booking.user_id}',
                    sender_id=current_user.id,
                )
            # Notify any co-owners of the dog (e.g. primary owner if secondary cancelled)
            if booking.dog_id:
                other_owners = DogOwner.query.filter(
                    DogOwner.dog_id == booking.dog_id,
                    DogOwner.user_id != current_user.id,
                ).all()
                co_service_label = (
                    'drop-in'
                    if booking.service_type and booking.service_type.slug == ServiceType.DROP_IN
                    else 'walk'
                )
                for ownership in other_owners:
                    if not (ownership.user and ownership.user.is_admin):
                        create_notification(
                            recipient_id=ownership.user_id,
                            notification_type='booking_cancelled',
                            title=f"{current_user.firstname} cancelled {dog_name}'s {booking.slot.lower()} {co_service_label} on {date_str_fmt}",
                            link='/',
                            sender_id=current_user.id,
                        )

        # Notify the walker who had this booking assigned (skip if they cancelled it themselves).
        if prior_walker_user_id and prior_walker_user_id != current_user.id:
            if is_admin_cancel:
                walker_title = f"{dog_name}'s {booking.slot.lower()} walk on {date_str_fmt} was cancelled"
            else:
                walker_title = f"{current_user.firstname} cancelled {dog_name}'s {booking.slot.lower()} walk on {date_str_fmt}"
            create_notification(
                recipient_id=prior_walker_user_id,
                notification_type='booking_cancelled',
                title=walker_title,
                link='/walker/schedule',
                sender_id=current_user.id,
            )

        db.session.commit()
        return jsonify(success=True, message="Booking successfully cancelled")
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error cancelling booking: {e}")
        return jsonify(success=False, message="Server error"), 500


@client_bp.route("/calendar_data/<int:year>/<int:month>")
@login_required
def calendar_data(year, month):
    """Return this client's bookings for a given month, for the booking calendar."""
    try:
        start_date = date_type(year, month, 1)
        end_date = date_type(year + 1, 1, 1) if month == 12 else date_type(year, month + 1, 1)
    except ValueError:
        return jsonify(success=False, message="Invalid date"), 400

    accessible_dog_ids = get_accessible_dog_ids(current_user.id)
    bookings = Booking.query.filter(
        Booking.dog_id.in_(accessible_dog_ids),
        Booking.date >= start_date,
        Booking.date < end_date,
        Booking.status.notin_(['cancelled', 'rejected'])
    ).all()

    # Confirmed takes priority if multiple bookings on same day
    dates = {}
    for b in bookings:
        ds = b.date.strftime('%Y-%m-%d')
        if b.status == 'confirmed':
            dates[ds] = 'confirmed'
        elif ds not in dates:
            dates[ds] = 'pending'

    closures = Closure.query.filter(
        Closure.date >= start_date,
        Closure.date < end_date,
    ).all()
    closed_dates = [c.date.strftime('%Y-%m-%d') for c in closures]

    return jsonify(success=True, dates=dates, closed_dates=closed_dates)


@client_bp.route("/recurring_booking", methods=["POST"])
@login_required
def recurring_booking():
    """Create a series of bookings from a start date, end date, slot and frequency.

    POST body (JSON):
        start_date  (str)  'YYYY-MM-DD' — must be tomorrow or later
        end_date    (str)  'YYYY-MM-DD' — max 1 year from start_date (client limit)
        slot        (str)  'Morning' or 'Afternoon'
        frequency   (str)  'daily' (weekdays only) or 'weekly'

    For each date in the range:
        - Skips weekends when frequency='daily'
        - Skips dates where this dog already has an active booking in that slot
        - Skips dates where the dog already has 2 bookings (one per slot limit)
        - Books as 'requested' if capacity available, 'waitlisted' if full

    Returns JSON: { success, created, waitlisted, skipped }

    Note: the 1-year cap is a client-facing safeguard. Admins booking on behalf
    of clients via /admin/recurring_for_dog have no such cap.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, message="No data received"), 400

        start_str = data.get('start_date', '')
        end_str = data.get('end_date', '')
        slot = data.get('slot', '')
        frequency = data.get('frequency', '')

        if not all([start_str, end_str, slot, frequency]):
            return jsonify(success=False, message="Missing required fields"), 400

        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify(success=False, message="Invalid date format"), 400

        today = datetime.now(timezone.utc).date()
        tomorrow = today + timedelta(days=1)
        max_end = start_date + timedelta(days=365)

        if start_date < tomorrow:
            return jsonify(success=False, message="Start date must be in the future"), 400
        if end_date > max_end:
            return jsonify(success=False, message="End date must be within one year of the start date"), 400
        if end_date < start_date:
            return jsonify(success=False, message="End date must be after start date"), 400
        if slot not in ('Morning', 'Afternoon', 'Both'):
            return jsonify(success=False, message="Invalid slot"), 400
        if frequency not in ('daily', 'weekly'):
            return jsonify(success=False, message="Invalid frequency"), 400

        # Generate target dates
        target_dates = []
        delta = timedelta(days=1) if frequency == 'daily' else timedelta(weeks=1)
        current = start_date
        while current <= end_date:
            if frequency == 'daily' and current.weekday() >= 5:
                current += timedelta(days=1)
                continue  # Skip weekends for daily
            target_dates.append(current)
            current += delta

        if not target_dates:
            return jsonify(success=False, message="No valid dates in that range"), 400

        # Get dog
        user_dogs = Dog.query.join(DogOwner).filter(DogOwner.user_id == current_user.id).all()
        if not user_dogs:
            return jsonify(success=False, message="No dog found on your account"), 400
        try:
            dog = _resolve_dog(user_dogs, data.get('dog_id'))
        except ValueError as e:
            return jsonify(success=False, message=str(e)), 400

        service_type_param = data.get('service_type', 'walk')
        is_drop_in = (service_type_param == ServiceType.DROP_IN)
        service_slug = ServiceType.DROP_IN if is_drop_in else ServiceType.WALK
        default_service = ServiceType.query.filter_by(slug=service_slug, active=True).first()
        if not default_service:
            return jsonify(success=False, message="No service type available"), 400

        # Drop-ins are single-slot only — reject 'Both' for drop-in
        if is_drop_in and slot == 'Both':
            return jsonify(success=False, message="Drop-ins cannot use the 'Both' slot"), 400

        active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')
        confirmed = created = waitlisted = skipped = 0
        confirmed_bookings = []  # tracks confirmed rows for walker + client notifications
        pending_bookings   = []  # tracks requested/waitlisted rows for client + admin notifications

        # One batch_id ties together every booking in this recurring series so
        # the activity feed can cluster them (NOTIFICATIONS.md §9.2, D4).
        batch_id = uuid.uuid4().hex

        slots_to_book = ['Morning', 'Afternoon'] if slot == 'Both' else [slot]

        for d in target_dates:
            for s in slots_to_book:
                existing = Booking.query.filter(
                    Booking.dog_id == dog.id,
                    Booking.date   == d,
                    Booking.slot   == s,
                    Booking.status.in_(active_statuses),
                ).first()
                if existing:
                    skipped += 1
                    continue

                # Skip if already 2 bookings that day
                day_count = Booking.query.filter(
                    Booking.dog_id == dog.id,
                    Booking.date == d,
                    Booking.status.in_(active_statuses)
                ).count()
                if day_count >= 2:
                    skipped += 1
                    continue

                acquire_booking_lock(default_service.slug, d, s)
                available, can_waitlist, _ = check_availability(default_service, d, s)
                if not available and not can_waitlist:
                    skipped += 1
                    continue
                status = 'requested' if available else 'waitlisted'

                booking = Booking(
                    user_id=current_user.id,
                    dog_id=dog.id,
                    service_type_id=default_service.id,
                    date=d,
                    slot=s,
                    status=status,
                )
                db.session.add(booking)
                record_booking_created(booking, actor_id=current_user.id, batch_id=batch_id)

                if status == 'waitlisted':
                    waitlisted += 1
                    pending_bookings.append(booking)
                elif not is_drop_in:
                    # Try to auto-assign a walker, same as single bookings
                    walker = auto_assign_walker(d, s, service_slug=service_slug)
                    if walker:
                        transition_booking(booking, 'confirmed', actor_id=current_user.id,
                                           walker_id=walker.id, batch_id=batch_id)
                        booking.pickup_order = get_walker_slot_count(walker.id, d, s, service_slug=service_slug)
                        confirmed += 1
                        confirmed_bookings.append(booking)
                    else:
                        created += 1
                        pending_bookings.append(booking)
                else:
                    created += 1
                    pending_bookings.append(booking)

        # Flush to ensure booking IDs are set before querying walkers below.
        db.session.flush()

        freq_label    = 'daily' if frequency == 'daily' else 'weekly'
        slot_label    = 'AM + PM' if slot == 'Both' else slot
        service_label = 'drop-ins' if is_drop_in else 'walks'

        # Client + admin notifications via NotificationBatch / summarise().
        # Client gets one notice per outcome kind (confirmed / pending).
        # Admins are notified for all outcomes (confirmed and pending/waitlisted).
        pending_total = created + waitlisted
        total         = confirmed + pending_total
        svc_label     = 'drop-in' if is_drop_in else 'walk'

        client_batch = NotificationBatch(actor_id=current_user.id)
        admin_batch  = NotificationBatch(actor_id=current_user.id)
        admins       = User.query.filter_by(is_admin=True).all()

        for b in confirmed_bookings:
            walker_first = b.walker.user.firstname if b.walker and b.walker.user else None
            client_batch.add(current_user.id, 'booking_confirmed',
                             dog_name=dog.name, slot=b.slot, date=b.date,
                             svc_label=svc_label, walker_name=walker_first)
            for admin in admins:
                admin_batch.add(admin.id, 'booking_confirmed',
                                actor_first=current_user.firstname,
                                link=f'/admin/clients/{current_user.id}',
                                dog_name=dog.name, slot=b.slot, date=b.date,
                                svc_label=svc_label, walker_name=walker_first)

        for b in pending_bookings:
            kind = 'booking_waitlisted' if b.status == 'waitlisted' else 'booking_requested'
            client_batch.add(current_user.id, kind,
                             dog_name=dog.name, slot=b.slot, date=b.date,
                             svc_label=svc_label)
            for admin in admins:
                admin_batch.add(admin.id, kind,
                                actor_first=current_user.firstname,
                                link='/admin',
                                dog_name=dog.name, slot=b.slot, date=b.date,
                                svc_label=svc_label)

        if total > 0:
            client_batch.flush()
        admin_batch.flush()

        # Notify auto-assigned walkers (§7.9): one grouped walker_assigned per walker.
        if confirmed_bookings:
            walker_batch = NotificationBatch(actor_id=current_user.id)
            for b in confirmed_bookings:
                if b.walker_id and b.walker and b.walker.user_id != current_user.id:
                    walker_batch.add(b.walker.user_id, 'walker_assigned',
                                     dog_name=dog.name, slot=b.slot, date=b.date,
                                     svc_label=svc_label)
            walker_batch.flush()

        db.session.commit()
        return jsonify(success=True, confirmed=confirmed, created=created, waitlisted=waitlisted, skipped=skipped)

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error creating recurring bookings: {e}")
        return jsonify(success=False, message="Server error"), 500


@client_bp.route("/booking/<int:booking_id>/note", methods=["POST"])
@login_required
def update_booking_note(booking_id):
    """Save or clear the client note on a booking.

    Authorization is enforced via user_can_access_booking() below — admin,
    booking creator, or any dog co-owner. No early role gate (used to
    reject dual-role walkers incorrectly).
    """
    booking = db.session.get(Booking, booking_id)
    if not booking:
        return jsonify(success=False, message="Booking not found"), 404

    if not user_can_access_booking(current_user, booking):
        return jsonify(success=False, message="Not your booking"), 403

    data = request.get_json(silent=True) or {}
    note = (data.get('note') or '').strip()
    if len(note) > 500:
        return jsonify(success=False, message="Note must be 500 characters or fewer"), 400

    booking.client_notes = note or None
    db.session.commit()
    return jsonify(success=True, note=booking.client_notes)
