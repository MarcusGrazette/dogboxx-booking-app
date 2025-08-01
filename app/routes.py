from flask import request, redirect, render_template, flash, url_for
from flask_login import login_required, current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.exc import IntegrityError
from app.models import User, Client, Dog
from app import db
from email_validator import validate_email, EmailNotValidError
import logging
import os
from datetime import datetime, timezone

# Temporary logging
logging.basicConfig(level=logging.DEBUG)

def register_routes(app):
    app.config["UPLOAD_FOLDER"] = os.path.join(os.getcwd(), "app", "static", "images")
    
    @app.route("/")
    @login_required
    def index():
        """Render the home page."""
        return render_template("index.html", user=current_user)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """Log user in"""
        # Redirect if user is already authenticated
        if current_user.is_authenticated:
            return redirect("/")

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            remember_me = bool(request.form.get("remember_me"))

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

            # Check if user account is active
            if not user.is_active():
                flash("Your account has been deactivated. Please contact support.", "error")
                return render_template("login.html")

            # Log user in using Flask-Login
            login_user(user, remember=remember_me)
            flash(f"Welcome back, {user.firstname}!", "success")
            
            # Handle redirect to next page if specified
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/'):
                return redirect(next_page)
            
            # Check if user needs onboarding (for clients)
            if user.role == 'client':
                client = Client.query.filter_by(user_id=user.id).first()
                if not client or not client.onboarding_completed:
                    return redirect("/onboard")
            
            return redirect("/")

        return render_template("login.html")
    
    @app.route("/onboard", methods=["GET", "POST"])
    @login_required
    def onboard():
        """Handle user onboarding process"""
        # Only clients need onboarding for now
        if current_user.role != 'client':
            flash("Onboarding is only required for clients.", "info")
            return redirect("/")
        
        # Check if user already completed onboarding
        client = Client.query.filter_by(user_id=current_user.id).first()
        if client and client.onboarding_completed:
            flash("You have already completed onboarding!", "info")
            return redirect("/")

        if request.method == "GET":
            api_key = os.getenv("GOOGLE_MAPS_API_KEY")
            return render_template("onboarding.html", google_maps_api_key=api_key)
        
        if request.method == "POST":
            try:
                # Check if user wants to skip address entry
                skip_address = request.form.get("skip_address") == "true"
                
                if skip_address:
                    # Create client record without address
                    if not client:
                        client = Client(user_id=current_user.id)
                        db.session.add(client)
                    
                    client.onboarding_completed = True
                    client.onboarding_completed_at = datetime.now(timezone.utc)
                    db.session.commit()
                    
                    flash("Welcome! You can add your address later in your profile settings.", "success")
                    return redirect("/")
                
                # Get Google Places data from form
                place_id = request.form.get("place_id", "").strip()
                formatted_address = request.form.get("formatted_address", "").strip()
                display_name = request.form.get("display_name", "").strip()
                
                # Get coordinates
                latitude = request.form.get("latitude")
                longitude = request.form.get("longitude")
                
                # Convert coordinates to float if provided
                try:
                    latitude = float(latitude) if latitude else None
                    longitude = float(longitude) if longitude else None
                except (ValueError, TypeError):
                    latitude = longitude = None
                
                # Get pickup instructions
                pickup_instructions = request.form.get("pickup_instructions", "").strip()
                
                # Validation
                errors = []
                
                if not place_id or not formatted_address:
                    errors.append("Please select an address from the dropdown suggestions")
                
                if errors:
                    for error in errors:
                        flash(error, "error")
                    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
                    return render_template("onboarding.html", google_maps_api_key=api_key)
                
                # Create or update client record
                if not client:
                    client = Client(user_id=current_user.id)
                    db.session.add(client)
                
                # Update client with Google Places information
                client.place_id = place_id
                client.formatted_address = formatted_address
                client.display_name = display_name
                client.latitude = latitude
                client.longitude = longitude
                client.pickup_instructions = pickup_instructions
                client.onboarding_completed = True
                client.onboarding_completed_at = datetime.now(timezone.utc)
                
                db.session.commit()
                
                flash(f"Welcome to our platform, {current_user.firstname}! Your profile is now complete.", "success")
                logging.info(f"User {current_user.email} completed onboarding with address: {formatted_address}")
                
                return redirect("/")
                
            except Exception as e:
                db.session.rollback()
                logging.error(f"Error during onboarding for user {current_user.email}: {e}")
                flash("There was an error saving your information. Please try again.", "error")
                api_key = os.getenv("GOOGLE_MAPS_API_KEY")
                return render_template("onboarding.html", google_maps_api_key=api_key)


    @app.route("/onboard/dog", methods=["POST"])
    @login_required
    def onboard_dog():
        """Handle dog information submission"""
        from werkzeug.utils import secure_filename
        from uuid import uuid1

        # Get form data
        dog_name = request.form.get("dog_name", "").strip()
        dog_gender = request.form.get("dog_gender", "").strip()
        dog_breed = request.form.get("dog_breed", "").strip()
        dog_allergies = request.form.get("dog_allergies", "").strip()
        dog_other_info = request.form.get("dog_other_info", "").strip()
        dog_years = int(request.form.get("dog_years", 0))
        dog_months = int(request.form.get("dog_months", 0))

        # Calculate birth year and month
        today = datetime.now()
        birth_year = today.year - dog_years
        birth_month = today.month - dog_months
        if birth_month <= 0:
            birth_year -= 1
            birth_month += 12

        # Handle file upload
        dog_pic = request.files.get("dog_pic")
        pic_filename = None
        if dog_pic:
            original_filename = secure_filename(dog_pic.filename)
            unique_filename = f"{uuid1()}_{original_filename}"
            upload_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)
            dog_pic.save(upload_path)
            pic_filename = unique_filename

        # Save dog information to database
        new_dog = Dog(
            user_id=current_user.id,
            name=dog_name,
            gender=dog_gender,
            breed=dog_breed,
            allergies=dog_allergies,
            other_info=dog_other_info,
            birth_year_month=birth_year * 100 + birth_month,
            pic=pic_filename
        )
        db.session.add(new_dog)
        db.session.commit()

        flash("Your dog's information has been saved!", "success")
        return redirect("/")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        """Register a new user with improved validation and error handling"""
        # Redirect if user is already authenticated
        if current_user.is_authenticated:
            return redirect("/")

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

                # Log the user in automatically, redirect to the onboarding page
                login_user(new_user)
                flash(f"Welcome to our platform, {firstname}!", "success")
                
                logging.info(f"New user registered: {email}")
                return redirect("/onboard")

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
    @login_required
    def logout():
        """Log user out"""
        logout_user()
        flash("You have been logged out", "info")
        return redirect("/login")
        
    @app.route("/profile")
    @login_required
    def profile():
        """Display user profile"""
        client = None
        if current_user.role == 'client':
            client = Client.query.filter_by(user_id=current_user.id).first()
        return render_template("profile.html", user=current_user, client=client)