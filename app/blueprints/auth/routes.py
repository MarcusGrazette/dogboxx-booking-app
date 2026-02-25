"""
Authentication routes.

This module defines routes for user authentication, including login, registration,
and logout functionality.
"""

from flask import request, redirect, render_template, flash, url_for
from flask_login import login_required, current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from app.models import User, Client
from app import db, limiter
from app.utils.db_error_handler import handle_db_errors, DBErrorHandler
from app.forms import LoginForm, RegisterForm, PasswordChangeForm
import logging
import traceback
from datetime import datetime, timezone

from app.blueprints.auth import auth_bp


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

        # Track failed login attempts with redis-based rate limiting
        if not user or not check_password_hash(user.hashed_password, password):
            # Log the failed attempt (for security auditing)
            logging.warning(f"Failed login attempt for email: {email} from IP: {request.remote_addr}")
            
            # Show generic error message (don't reveal if email exists)
            flash("Invalid email or password", "error")
            return render_template("login.html", form=form)

        # Check if user account is active
        if not user.is_active():
            flash("Your account has been deactivated. Please contact support.", "error")
            return render_template("login.html", form=form)

        # Log user in
        login_user(user, remember=remember_me)

        # Redirect based on user role
        return _redirect_by_role(user)

    return render_template("login.html", form=form)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Public registration is disabled. Clients are created by admin."""
    from flask import abort
    abort(404)


@auth_bp.route("/logout")
@login_required
def logout():
    """Log user out"""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
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
            
            flash("Your password has been changed successfully.", "success")
            
            # Redirect based on role
            return _redirect_by_role(current_user)
            
        except SQLAlchemyError as e:
            db.session.rollback()
            logging.error(f"Error changing password for user {current_user.id}: {e}")
            flash("An error occurred while changing your password. Please try again.", "error")
    
    return render_template("change_password.html", form=form)


def _redirect_by_role(user):
    """Helper function to redirect users based on their role"""
    if user.is_admin:
        return redirect(url_for('admin.index'))
    elif user.role == 'walker':
        return redirect(url_for('walker.schedule'))
    elif user.role == 'client':
        return redirect(url_for('client.index'))
    else:
        # Handle unexpected roles gracefully
        flash("Unknown user role. Please contact support.", "warning")
        return redirect(url_for('client.index'))
