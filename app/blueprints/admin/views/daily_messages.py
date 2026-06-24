from flask import request, render_template, redirect, flash, url_for
from flask_login import login_required, current_user
from datetime import datetime, timezone, timedelta

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app import db


# ── Daily Messages ────────────────────────────────────────────────────────────

@admin_bp.route("/daily-messages", methods=["GET", "POST"])
@login_required
@admin_required
def daily_messages():
    """Create or update a daily message for the walker team."""
    from app.models import DailyMessage
    from datetime import date as date_type
    import bleach

    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        content = request.form.get("content", "").strip()

        if not date_str or not content:
            flash("Date and message content are required.", "danger")
            return redirect(url_for("admin.daily_messages"))

        try:
            msg_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid date format.", "danger")
            return redirect(url_for("admin.daily_messages"))

        # Sanitise HTML from Quill — allow basic formatting tags only
        allowed_tags = list(bleach.sanitizer.ALLOWED_TAGS) + [
            'p', 'br', 'h1', 'h2', 'h3', 'ul', 'ol', 'li', 'strong', 'em',
            'u', 's', 'blockquote', 'pre', 'code', 'a', 'span',
        ]
        allowed_attrs = {'a': ['href', 'target', 'rel'], 'span': ['class'], '*': ['class']}
        clean_content = bleach.clean(content, tags=allowed_tags, attributes=allowed_attrs)

        msg = DailyMessage.query.filter_by(date=msg_date).first()
        now = datetime.now(timezone.utc)
        if msg:
            msg.content = clean_content
            msg.updated_at = now
        else:
            msg = DailyMessage(
                date=msg_date,
                content=clean_content,
                created_by_id=current_user.id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(msg)

        db.session.commit()
        flash(f"Message saved for {msg_date.strftime('%A, %-d %B %Y')}.", "success")
        return redirect(url_for("admin.daily_messages"))

    messages = (
        DailyMessage.query
        .order_by(DailyMessage.date.desc())
        .all()
    )
    today = datetime.now(timezone.utc).date()
    return render_template("admin_daily_messages.html", messages=messages, today=today)


@admin_bp.route("/daily-messages/<int:message_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_daily_message(message_id):
    from app.models import DailyMessage
    msg = db.get_or_404(DailyMessage, message_id)
    db.session.delete(msg)
    db.session.commit()
    flash("Message deleted.", "success")
    return redirect(url_for("admin.daily_messages"))


@admin_bp.route("/daily-messages/bulk-delete-old", methods=["POST"])
@login_required
@admin_required
def bulk_delete_old_daily_messages():
    from app.models import DailyMessage
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date()
    deleted = DailyMessage.query.filter(DailyMessage.date < cutoff).delete()
    db.session.commit()
    flash(f"Deleted {deleted} message{'s' if deleted != 1 else ''} older than 30 days.", "success")
    return redirect(url_for("admin.daily_messages"))
