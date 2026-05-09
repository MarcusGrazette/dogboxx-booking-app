"""
Custom decorators for role-based access control.
"""

from functools import wraps
from flask import flash, redirect, url_for, jsonify, request
from flask_login import current_user


def admin_required(f):
    """Decorator that restricts access to admin users only."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify(success=False, message="Forbidden"), 403
            flash("Only admins can access this page.", "danger")
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
            flash("Only walkers can access this page.", "danger")
            return redirect(url_for("client.index"))
        return f(*args, **kwargs)
    return decorated_function


def has_client_access(user):
    """Return True if the user can access client-facing routes.

    A walker who also has a Client record (dual-role) can access the client
    section of the app by switching view in the navbar.
    """
    return user.role == 'client' or user.client is not None
