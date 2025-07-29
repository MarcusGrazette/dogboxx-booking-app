from flask import request, session, redirect, render_template, flash, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.exc import IntegrityError
from app.models import User
from app import db
from email_validator import validate_email, EmailNotValidError
import logging

def register_routes(app):
    
    @app.route("/")
    def index():
        """Render the home page or redirect to login."""
        if "user_id" not in session:
            return redirect("/login")

        user = User.query.get(session["user_id"])
        if not user:
            session.clear()
            return redirect("/login")

        return render_template("index.html", user=user)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """Log user in"""
        session.clear()

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            # Validation
            if not email:
                flash("Please provide email address", "error")
                return render_template("login.html")

            if not password:
                flash("Please provide password", "error")
                return render_template("login.html")

            # Query database for user
            user = User.query.filter_by(email=email).first()

            # Check credentials
            if not user or not check_password_hash(user.hashed_password, password):
                flash("Invalid email or password", "error")
                return render_template("login.html")

            # Log user in
            session["user_id"] = user.id
            flash(f"Welcome back, {user.firstname}!", "success")
            return redirect("/")

        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        """Register a new user with improved validation and error handling"""
        if request.method == "POST":
            try:
                # Get and sanitize form data
                firstname = request.form.get("firstname", "").strip().title()
                lastname = request.form.get("lastname", "").strip().title()
                email = request.form.get("email", "").strip().lower()
                password = request.form.get("password", "")
                confirmation = request.form.get("confirmation", "")

                # Validation
                errors = []

                # Name validation
                if not firstname or len(firstname) < 2:
                    errors.append("First name must be at least 2 characters")
                if not lastname or len(lastname) < 2:
                    errors.append("Last name must be at least 2 characters")

                # Email validation
                if not email:
                    errors.append("Email address is required")
                else:
                    try:
                        validated_email = validate_email(email)
                        email = validated_email.email
                    except EmailNotValidError as e:
                        errors.append(f"Invalid email: {str(e)}")

                # Password validation
                if not password:
                    errors.append("Password is required")
                elif not confirmation:
                    errors.append("Password confirmation is required")
                elif password != confirmation:
                    errors.append("Passwords do not match")
                else:
                    is_valid, message = User.validate_password(password)
                    if not is_valid:
                        errors.append(message)

                # Check if user already exists
                if email and User.query.filter_by(email=email).first():
                    errors.append("An account with this email already exists")

                # If there are validation errors, return them
                if errors:
                    for error in errors:
                        flash(error, "error")
                    return render_template("register.html")

                # Create new user
                hashed_password = generate_password_hash(password)
                new_user = User(
                    firstname=firstname,
                    lastname=lastname,
                    email=email,
                    hashed_password=hashed_password,
                    role="client"
                )

                # Save to database
                db.session.add(new_user)
                db.session.commit()

                # Log the user in automatically
                session["user_id"] = new_user.id
                flash(f"Welcome to our platform, {firstname}!", "success")
                
                logging.info(f"New user registered: {email}")
                return redirect("/")

            except IntegrityError as e:
                # Handle database constraint violations
                db.session.rollback()
                logging.error(f"Database integrity error during registration: {e}")
                flash("An account with this email already exists", "error")
                return render_template("register.html")

            except Exception as e:
                # Handle unexpected errors
                db.session.rollback()
                logging.error(f"Unexpected error during registration: {e}")
                flash("Registration failed. Please try again.", "error")
                return render_template("register.html")

        return render_template("register.html")

    @app.route("/logout")
    def logout():
        """Log user out"""
        session.clear()
        flash("You have been logged out", "info")
        return redirect("/login")

    def apology(message, code=400):
        """Render message as an apology to user."""
        return render_template("apology.html", message=message), code