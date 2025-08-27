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
from app.forms import LoginForm, RegisterForm
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
@limiter.limit("3 per minute, 10 per hour, 20 per day")  # Strict limits on registration to prevent abuse
def register():
    """Register a new user with improved validation and error handling"""
    # Redirect if user is already authenticated
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    form = RegisterForm()
    if form.validate_on_submit():
        try:
            firstname = form.firstname.data.strip().title()
            lastname = form.lastname.data.strip().title()
            email = form.email.data.strip().lower()
            password = form.password.data

            # Check if user already exists
            if User.query.filter_by(email=email).first():
                flash("An account with this email already exists.", "error")
                return render_template("register.html", form=form)

            # Create new user
            hashed_password = generate_password_hash(password)
            new_user = User(
                firstname=firstname,
                lastname=lastname,
                email=email,
                hashed_password=hashed_password,
                role="client"
            )

            db.session.add(new_user)
            db.session.commit()

            # Log the user in automatically, redirect to the onboarding page
            login_user(new_user)
            flash(f"Welcome to our platform, {firstname}!", "success")
            return redirect(url_for('client.onboard'))

        except IntegrityError as e:
            db.session.rollback()
            logging.error(f"IntegrityError during registration: {e}")
            logging.debug(traceback.format_exc())
            
            # Check for duplicate email (specific constraint violation)
            if "UNIQUE constraint failed: user.email" in str(e):
                flash("An account with this email already exists. Please log in instead.", "error")
            else:
                flash("There was a problem creating your account due to a data conflict. Please try again.", "error")
        except SQLAlchemyError as e:
            db.session.rollback()
            logging.error(f"SQLAlchemyError during registration: {e}")
            logging.debug(traceback.format_exc())
            
            if isinstance(e, OperationalError):
                flash("The service is temporarily unavailable. Please try again later.", "error")
            else:
                flash("A database error occurred while creating your account. Please try again.", "error")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Unexpected error during registration: {e}")
            logging.debug(traceback.format_exc())
            flash("An unexpected error occurred. Please try again.", "error")

    return render_template("register.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    """Log user out"""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


def _redirect_by_role(user):
    """Helper function to redirect users based on their role"""
    if user.role == 'admin':
        return redirect(url_for('admin.dashboard'))
    elif user.role == 'walker':
        return redirect(url_for('walker.schedule'))
    elif user.role == 'client':
        return redirect(url_for('client.index'))
    else:
        # Handle unexpected roles gracefully
        flash("Unknown user role. Please contact support.", "warning")
        return redirect(url_for('client.index'))
