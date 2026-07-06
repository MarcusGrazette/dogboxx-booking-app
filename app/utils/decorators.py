"""
Custom decorators for role-based access control.
"""

import hmac
from functools import wraps
from flask import current_app, flash, redirect, url_for, jsonify, request
from flask_login import current_user


def admin_required(f):
    """Decorator that restricts access to admin users only."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify(success=False, message="Forbidden"), 403
            flash("Only admins can access this page.", "error")
            return redirect(url_for("client.index"))
        return f(*args, **kwargs)
    return decorated_function


def walker_required(f):
    """Decorator that restricts access to walker users only."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role != 'walker' and not current_user.is_admin:
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify(success=False, message="Forbidden"), 403
            flash("Only walkers can access this page.", "error")
            return redirect(url_for("client.index"))
        return f(*args, **kwargs)
    return decorated_function


def internal_only(f):
    """Restrict a route to callers presenting the shared internal secret.

    For endpoints that exist purely so one Railway service can read another's
    state — no logged-in user, no session. Railway volumes can only be mounted
    to a single service, so this is how the reconcile-uploads cron service
    (which has no volume of its own) reads the uploads volume that only `web`
    has mounted.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        expected = current_app.config.get('INTERNAL_API_SECRET')
        provided = request.headers.get('X-Internal-Secret', '')
        if not expected or not hmac.compare_digest(expected, provided):
            return jsonify(success=False, message="Forbidden"), 403
        return f(*args, **kwargs)
    return decorated_function


def has_client_access(user):
    """Return True if the user can access client-facing routes.

    A walker who also has a Client record (dual-role) can access the client
    section of the app by switching view in the navbar.
    """
    return user.role == 'client' or user.client is not None
