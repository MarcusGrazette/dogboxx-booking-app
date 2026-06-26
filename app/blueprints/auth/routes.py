"""
Authentication routes.

This module defines routes for user authentication, including login, logout,
password change, and password reset. Public self-service registration was
removed — clients are created by an admin.
"""

from flask import request, redirect, render_template, flash, url_for, current_app, session
from flask_login import login_required, current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from app.models import User, Client
from app import db, limiter
from app.utils.db_error_handler import handle_db_errors, DBErrorHandler
from app.forms import LoginForm, PasswordChangeForm
import hashlib
import logging
import traceback
from datetime import datetime, timezone

from app.blueprints.auth import auth_bp


# Constant-time-ish login (SECURITY_REVIEW.md #3): when the submitted email isn't
# in the DB we still run check_password_hash against this dummy hash, so the
# response spends the same scrypt CPU as a real "wrong password" check. Without
# it, an unknown email short-circuits (~7ms) while an existing one pays for the
# hash (~100ms), letting an attacker enumerate registered emails despite the
# generic error message. Generated via generate_password_hash so it always uses
# the same algorithm/cost params as live hashes (a hardcoded string could drift).
_DUMMY_PASSWORD_HASH = generate_password_hash("dogboxx-login-timing-equalizer")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")  # Limit login attempts
def login():
    """Log user in"""
    # Redirect if user is already authenticated
    if current_user.is_authenticated:
        # Use role-based redirect even for already authenticated users
        return _redirect_by_role(current_user)

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        password = form.password.data
        remember_me = form.remember_me.data

        # Query database for user
        user = User.query.filter_by(email=email).first()

        # Verify the password. When the email is unknown, hash against a dummy so
        # both paths cost the same scrypt work — closes the user-enumeration timing
        # oracle (SECURITY_REVIEW.md #3). Don't short-circuit on `not user`.
        if user:
            password_ok = check_password_hash(user.hashed_password, password)
        else:
            check_password_hash(_DUMMY_PASSWORD_HASH, password)  # spend the time, discard
            password_ok = False

        # Track failed login attempts with redis-based rate limiting
        if not password_ok:
            # Log the failed attempt (for security auditing)
            logging.warning(f"Failed login attempt for email: {email} from IP: {request.remote_addr}")

            # Show generic error message (don't reveal if email exists)
            flash("Invalid email or password", "error")
            return render_template("login.html", form=form)

        # Check if user account is active
        if not user.is_active():
            flash("Your account has been deactivated. Please contact DogBoxx.", "error")
            return render_template("login.html", form=form)

        # Rotate the session ID before associating the session with this user.
        # Defeats session-fixation: any pre-planted SID cookie is now a dead row.
        current_app.session_interface.regenerate(session)

        # Log user in
        login_user(user, remember=remember_me)

        # Redirect based on user role
        return _redirect_by_role(user)

    return render_template("login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    """Log user out. POST-only so a GET <img>/link from another origin can't
    force-logout a logged-in user (CSRF). Flask-WTF protects the POST."""
    logout_user()
    # Clear any stale flash messages left in the session before adding ours.
    # With SESSION_PERMANENT=True sessions persist for 14 days, so unconsumed
    # flashes from previous requests can accumulate and cause double messages.
    from flask import session as flask_session
    flask_session.pop('_flashes', None)
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
@limiter.limit("5 per minute", methods=["POST"])
def change_password():
    """Allow users to change their password"""
    form = PasswordChangeForm()
    
    if form.validate_on_submit():
        try:
            # Check current password
            if not check_password_hash(current_user.hashed_password, form.current_password.data):
                flash("Current password is incorrect.", "error")
                return render_template("change_password.html", form=form)
            
            # Update password
            current_user.hashed_password = generate_password_hash(form.new_password.data)
            current_user.must_change_password = False

            db.session.commit()

            # Rotate the SID now that a privilege-relevant credential changed.
            # The user stays logged in (session data, including _user_id, is
            # preserved across the rotation); only the SID changes.
            current_app.session_interface.regenerate(session)

            flash("Your password has been changed successfully.", "success")

            # Redirect based on role
            return _redirect_by_role(current_user)
            
        except SQLAlchemyError as e:
            db.session.rollback()
            logging.error(f"Error changing password for user {current_user.id}: {e}")
            flash("An error occurred while changing your password. Please try again.", "error")
    
    return render_template("change_password.html", form=form)


def _redirect_by_role(user):
    """Redirect users to their home page based on role + device type.

    Walkers on mobile (Android) land on pickups — their daily operational
    view when they're out and about. Walkers on desktop go to schedule for
    planning. Admins and clients are device-agnostic for now.
    """
    from app.utils.ua import get_device_info

    if user.is_admin:
        return redirect(url_for('admin.index'))
    elif user.role == 'walker':
        return redirect(url_for('walker.pickups'))
    elif user.role == 'client':
        return redirect(url_for('client.index'))
    else:
        flash("Unknown user role. Please contact support.", "warning")
        return redirect(url_for('client.index'))


# ── Password reset helpers ────────────────────────────────────────────────────

def _password_fingerprint(hashed_password: str) -> str:
    """Stable 16-char fingerprint of a password hash, used inside reset
    tokens so the token is invalidated when the password changes. Hashing
    the hash avoids storing any password material in the token, and avoids
    werkzeug's algorithm prefix problem (hashed_password[:16] equals
    'scrypt:32768:8:1' for every scrypt-hashed password)."""
    return hashlib.sha256(hashed_password.encode()).hexdigest()[:16]


def _make_reset_token(user):
    """Return a signed, time-limited token embedding a fingerprint of the
    user's current password hash so the token is invalidated the moment the
    password changes."""
    from itsdangerous import URLSafeTimedSerializer
    from flask import current_app
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return s.dumps(
        {'user_id': user.id, 'pw': _password_fingerprint(user.hashed_password)},
        salt='password-reset',
    )


def _verify_reset_token(token, max_age=3600):
    """Return the User for a valid token, or None if expired/invalid/already used."""
    from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
    from flask import current_app
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        data = s.loads(token, salt='password-reset', max_age=max_age)
    except (SignatureExpired, BadSignature):
        return None
    user = db.session.get(User, data.get('user_id'))
    if not user:
        return None
    # Reject if password has already been changed since token was issued
    if _password_fingerprint(user.hashed_password) != data.get('pw'):
        return None
    return user


# ── Forgot password ───────────────────────────────────────────────────────────

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def forgot_password():
    """Step 1 — user enters their email to request a reset link."""
    from app.forms import ForgotPasswordForm
    from app.utils.email import send_email
    from flask import current_app

    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    form = ForgotPasswordForm()
    sent = False

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter_by(email=email).first()

        # Always show the same success message — don't reveal whether the email exists
        if user and user.is_active():
            token = _make_reset_token(user)
            base_url = current_app.config.get('APP_BASE_URL', '').rstrip('/')
            reset_url = f"{base_url}/auth/reset-password/{token}"

            html = render_template(
                "email/password_reset.html",
                firstname=user.firstname,
                reset_url=reset_url,
            )
            send_email(
                to=user.email,
                subject="Reset your DogBoxx password",
                html=html,
            )
            logging.info(f"Password reset requested for {email}")

        sent = True  # Always show success — prevents email enumeration

    return render_template("forgot_password.html", form=form, sent=sent)


# ── Reset password ────────────────────────────────────────────────────────────

@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password(token):
    """Step 2 — user clicks the link in the email and sets a new password."""
    from app.forms import ResetPasswordForm

    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    user = _verify_reset_token(token)
    if not user:
        flash("That reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for('auth.forgot_password'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        try:
            from werkzeug.security import generate_password_hash
            user.hashed_password = generate_password_hash(form.password.data)
            user.must_change_password = False
            db.session.commit()
            # Rotate the anonymous SID on this privilege boundary — invalidates
            # any session cookie picked up during the reset flow before the user
            # heads to /auth/login to authenticate fresh.
            current_app.session_interface.regenerate(session)
            flash("Password updated! You can now log in.", "success")
            logging.info(f"Password reset completed for user {user.id}")
            return redirect(url_for('auth.login'))
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error resetting password for user {user.id}: {e}")
            flash("Something went wrong. Please try again.", "error")

    return render_template("reset_password.html", form=form, token=token)


# ── Newsletter unsubscribe ────────────────────────────────────────────────────

@auth_bp.route("/unsubscribe/<token>")
def unsubscribe(token):
    """One-click unsubscribe from newsletter emails."""
    user = User.verify_unsubscribe_token(token)
    if not user:
        flash("This unsubscribe link is invalid or has expired.", "error")
        return redirect(url_for('auth.login'))

    if not user.email_marketing:
        flash("You're already unsubscribed from newsletter emails.", "info")
        return redirect(url_for('auth.login'))

    try:
        user.email_marketing = False
        db.session.commit()
        logging.info(f"User {user.id} unsubscribed from newsletter")
        flash("You've been unsubscribed from newsletter emails.", "success")
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error unsubscribing user {user.id}: {e}")
        flash("Something went wrong. Please try again.", "error")

    return redirect(url_for('auth.login'))


