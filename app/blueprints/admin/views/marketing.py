from flask import request, render_template, redirect, flash, url_for, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from datetime import datetime, timezone, timedelta

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import User, DogOwner, Broadcast
from app import db
from app.utils.notifications import create_notification


# ── Newsletter ────────────────────────────────────────────────────────────────

@admin_bp.route("/newsletter", methods=["GET", "POST"])
@login_required
@admin_required
def newsletter():
    """Compose and send a newsletter to all active, opted-in clients."""
    from app.utils.email import send_newsletter_batch
    from flask import current_app

    # Build recipient list: active clients who have opted in
    clients = User.query.filter_by(role='client', active=True, email_marketing=True).all()

    result = None  # {'sent': int, 'failed': int} after a send

    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        html_body = request.form.get("html_body", "").strip()

        if not subject or not html_body:
            flash("Subject and body are required.", "error")
        else:
            # Batch-resolve each client's primary dog name in one query — there
            # is no Client.dogs shortcut, dogs are owned via the DogOwner join
            # table. Falls back to "your dog" for clients with no primary.
            client_ids = [u.id for u in clients]
            primary_ownerships = (
                DogOwner.query
                .filter(DogOwner.user_id.in_(client_ids),
                        DogOwner.role == 'primary')
                .options(joinedload(DogOwner.dog))
                .all()
            )
            dog_name_by_user = {}
            for o in primary_ownerships:
                if o.user_id not in dog_name_by_user and o.dog:
                    dog_name_by_user[o.user_id] = o.dog.name

            base_url = current_app.config.get("APP_BASE_URL", "").rstrip("/")
            recipients = []
            for u in clients:
                token = u.make_unsubscribe_token()
                recipients.append({
                    "email": u.email,
                    "firstname": u.firstname,
                    "dog_name": dog_name_by_user.get(u.id, "your dog"),
                    "unsubscribe_url": f"{base_url}/auth/unsubscribe/{token}",
                })

            result = send_newsletter_batch(
                subject=subject,
                html_template=html_body,
                recipients=recipients,
            )
            if result["failed"] == 0:
                flash(f"Newsletter sent to {result['sent']} client(s).", "success")
            else:
                flash(f"Sent {result['sent']}, failed {result['failed']}. Check logs.", "warning")

    return render_template(
        "admin_newsletter.html",
        clients=clients,
        result=result,
    )


@admin_bp.route("/newsletter/test", methods=["POST"])
@login_required
@admin_required
def newsletter_test():
    """Send a test newsletter to lydia@dogboxx.org.

    Returns JSON so the compose page never reloads — keeps the user's
    draft (subject + Quill body) intact while they iterate on the test.
    """
    from app.utils.email import send_newsletter_batch
    from flask import current_app

    subject = request.form.get("subject", "").strip()
    html_body = request.form.get("html_body", "").strip()

    if not subject or not html_body:
        return jsonify(
            success=False,
            message="Write a subject and body before sending a test.",
        ), 400

    base_url = current_app.config.get("APP_BASE_URL", "").rstrip("/")
    result = send_newsletter_batch(
        subject=f"[TEST] {subject}",
        html_template=html_body,
        recipients=[{
            "email": "lydia@dogboxx.org",
            "firstname": "Lydia",
            "dog_name": "Luna",
            "unsubscribe_url": f"{base_url}/auth/unsubscribe/test",
        }],
    )
    if result.get("sent"):
        return jsonify(success=True,
                       message="Test email sent to lydia@dogboxx.org.")
    return jsonify(success=False,
                   message="Test email failed — check logs."), 500


# ── Broadcasts ────────────────────────────────────────────────────────────────

@admin_bp.route("/broadcasts", methods=["GET", "POST"])
@login_required
@admin_required
def broadcasts():
    """Compose and send a one-shot broadcast to clients booked on a given date/slot."""
    from datetime import date as _date
    from app.utils.broadcasts import resolve_recipients, scope_slot_label
    from app.utils.email import send_broadcast_batch

    today = _date.today()

    if request.method == "POST":
        # Parse + validate inputs
        date_str = request.form.get("scope_date", "").strip()
        scope_slot = request.form.get("scope_slot", "").strip()
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "").strip()
        channel_bell = request.form.get("channel_bell") == "on"
        channel_email = request.form.get("channel_email") == "on"

        errors = []
        try:
            scope_date = _date.fromisoformat(date_str)
        except ValueError:
            scope_date = None
            errors.append("Pick a valid date.")
        if scope_slot not in Broadcast.VALID_SCOPES:
            errors.append("Pick a valid slot scope.")
        if not subject:
            errors.append("Subject is required.")
        if not body:
            errors.append("Body is required.")
        if not (channel_bell or channel_email):
            errors.append("Pick at least one delivery channel.")

        recipients = []
        if scope_date and scope_slot in Broadcast.VALID_SCOPES:
            recipients = resolve_recipients(scope_date, scope_slot)
            if not recipients:
                errors.append("No clients are booked for that scope — nothing to send.")

        if errors:
            for e in errors:
                flash(e, "error")
            past_broadcasts = (
                Broadcast.query
                .options(joinedload(Broadcast.sender))
                .order_by(Broadcast.sent_at.desc())
                .all()
            )
            return render_template(
                "admin_broadcasts.html",
                today=today,
                form={
                    "scope_date": date_str,
                    "scope_slot": scope_slot,
                    "subject": subject,
                    "body": body,
                    "channel_bell": channel_bell,
                    "channel_email": channel_email,
                },
                past_broadcasts=past_broadcasts,
            )

        # Send: bell first (synchronous DB writes), then email batch.
        sender_id = current_user.id
        title = subject
        if channel_bell:
            for user, _dogs in recipients:
                create_notification(
                    recipient_id=user.id,
                    notification_type='system',
                    title=title,
                    body=body,
                    link=None,  # falls through to /notifications in the bell template
                    sender_id=sender_id,
                )

        email_result = {'sent': 0, 'failed': 0}
        if channel_email:
            email_recipients = [
                {"email": user.email, "firstname": user.firstname}
                for user, _dogs in recipients
                if user.email
            ]
            email_result = send_broadcast_batch(
                subject=subject,
                body_text=body,
                recipients=email_recipients,
            )

        # Audit row — written regardless of email success so we always have a record.
        broadcast = Broadcast(
            sender_id=sender_id,
            scope_date=scope_date,
            scope_slot=scope_slot,
            subject=subject,
            body=body,
            bell_sent=channel_bell,
            email_sent=channel_email,
            recipient_count=len(recipients),
        )
        db.session.add(broadcast)
        db.session.commit()

        # User-facing summary
        scope_label = scope_slot_label(scope_slot)
        parts = [f"Sent to {len(recipients)} client(s) for {scope_date.isoformat()} {scope_label}"]
        if channel_bell:
            parts.append("notification bell delivered")
        if channel_email:
            if email_result.get('failed'):
                parts.append(
                    f"email: {email_result['sent']} sent, {email_result['failed']} failed"
                )
            else:
                parts.append(f"email: {email_result['sent']} sent")
        flash(" — ".join(parts), "success")
        return redirect(url_for("admin.broadcasts"))

    # GET — fresh form. Honour ?scope_date and ?scope_slot query params so the
    # action bar on the board can deep-link into the composer pre-scoped.
    # Invalid values fall back to the defaults rather than 400 — the page is
    # interactive, the admin can still fix it via the form.
    prefill_date_str = request.args.get("scope_date", "").strip()
    try:
        prefill_date = _date.fromisoformat(prefill_date_str) if prefill_date_str else today
    except ValueError:
        prefill_date = today

    prefill_slot = request.args.get("scope_slot", "").strip()
    if prefill_slot not in Broadcast.VALID_SCOPES:
        prefill_slot = Broadcast.SCOPE_ALL

    past_broadcasts = (
        Broadcast.query
        .options(joinedload(Broadcast.sender))
        .order_by(Broadcast.sent_at.desc())
        .all()
    )

    return render_template(
        "admin_broadcasts.html",
        today=today,
        form={
            "scope_date": prefill_date.isoformat(),
            "scope_slot": prefill_slot,
            "subject": "",
            "body": "",
            "channel_bell": True,
            "channel_email": True,
        },
        past_broadcasts=past_broadcasts,
    )


@admin_bp.route("/broadcasts/test", methods=["POST"])
@login_required
@admin_required
def broadcasts_test():
    """Send a test broadcast email to the current admin.

    Returns JSON so the compose page never reloads — keeps the admin's draft
    (subject + body + scope + channel toggles) intact while they iterate. Test
    sends bypass scope resolution and the bell channel: a test is just the
    composed subject + body delivered as a single email to current_user.
    """
    from app.utils.email import send_broadcast_batch

    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()

    if not subject or not body:
        return jsonify(
            success=False,
            message="Write a subject and body before sending a test.",
        ), 400

    if not current_user.email:
        return jsonify(
            success=False,
            message="Your account has no email address on file.",
        ), 400

    result = send_broadcast_batch(
        subject=f"[TEST] {subject}",
        body_text=body,
        recipients=[{
            "email": current_user.email,
            "firstname": current_user.firstname or "there",
        }],
    )
    if result.get("sent"):
        return jsonify(success=True,
                       message=f"Test email sent to {current_user.email}.")
    return jsonify(success=False,
                   message="Test email failed — check logs."), 500


@admin_bp.route("/broadcasts/preview", methods=["GET"])
@login_required
@admin_required
def broadcasts_preview():
    """JSON endpoint — returns the resolved recipient list for a date + scope.

    Used by the composer page to live-update the recipient preview as the
    admin changes the scope picker.
    """
    from datetime import date as _date
    from app.utils.broadcasts import resolve_recipients

    date_str = request.args.get("scope_date", "").strip()
    scope_slot = request.args.get("scope_slot", "").strip()
    try:
        scope_date = _date.fromisoformat(date_str)
    except ValueError:
        return jsonify(error="Invalid date"), 400
    if scope_slot not in Broadcast.VALID_SCOPES:
        return jsonify(error="Invalid scope"), 400

    pairs = resolve_recipients(scope_date, scope_slot)
    return jsonify(
        count=len(pairs),
        recipients=[
            {
                "user_id": user.id,
                "name": user.full_name,
                "email": user.email,
                "dogs": [d.name for d in dogs],
            }
            for user, dogs in pairs
        ],
    )


@admin_bp.route("/broadcasts/bulk-delete-old", methods=["POST"])
@login_required
@admin_required
def bulk_delete_old_broadcasts():
    """Remove Broadcast audit rows older than 30 days (by sent_at).

    Mirrors /admin/daily-messages/bulk-delete-old. Broadcasts are immutable
    audit history; this is purely housekeeping for the admin's history view.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    deleted = Broadcast.query.filter(Broadcast.sent_at < cutoff).delete()
    db.session.commit()
    flash(
        f"Deleted {deleted} broadcast{'s' if deleted != 1 else ''} older than 30 days.",
        "success",
    )
    return redirect(url_for("admin.broadcasts"))
