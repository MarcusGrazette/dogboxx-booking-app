from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.exc import IntegrityError
from app.models import User, Client, Dog
from app import db
from email_validator import validate_email, EmailNotValidError
import logging
import os
from datetime import datetime, timezone
from werkzeug.utils import secure_filename
from uuid import uuid1
from app.forms import LoginForm, RegisterForm, OnboardingForm

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

        form = LoginForm()
        if form.validate_on_submit():
            email = form.email.data.strip().lower()
            password = form.password.data
            remember_me = form.remember_me.data

            # Query database for user
            user = User.query.filter_by(email=email).first()

            # Check credentials
            if not user or not check_password_hash(user.hashed_password, password):
                flash("Invalid email or password", "error")
                return render_template("login.html", form=form)

            # Check if user account is active
            if not user.is_active():
                flash("Your account has been deactivated. Please contact support.", "error")
                return render_template("login.html", form=form)

            # Log user in
            login_user(user, remember=remember_me)

            # Redirect to next page or home
            next_page = request.args.get('next')
            return redirect(next_page if next_page and next_page.startswith('/') else "/")

        return render_template("login.html", form=form)

    @app.route("/onboard", methods=["GET", "POST"])
    @login_required
    def onboard():
        """Handle complete user onboarding process"""
        if current_user.role != 'client':
            flash("Onboarding is only required for clients.", "info")
            return redirect("/")

        client = Client.query.filter_by(user_id=current_user.id).first()
        if client and client.onboarding_completed:
            flash("You have already completed onboarding!", "info")
            return redirect("/")

        form = OnboardingForm()
        if form.validate_on_submit():
            try:
                # Step 1: Address information
                place_id = form.place_id.data.strip()
                formatted_address = form.formatted_address.data.strip()
                display_name = form.display_name.data.strip()
                latitude = float(form.latitude.data) if form.latitude.data else None
                longitude = float(form.longitude.data) if form.longitude.data else None
                pickup_instructions = form.pickup_instructions.data.strip()

                # Create or update client record
                if not client:
                    client = Client(user_id=current_user.id)
                    db.session.add(client)

                client.place_id = place_id
                client.formatted_address = formatted_address
                client.display_name = display_name
                client.latitude = latitude
                client.longitude = longitude
                client.pickup_instructions = pickup_instructions
                client.onboarding_completed = True
                client.onboarding_completed_at = datetime.now(timezone.utc)

                # Step 2: Dog information
                dog_name = form.dog_name.data.strip()
                dog_gender = form.dog_gender.data.strip()
                dog_years = int(form.dog_years.data)
                dog_months = int(form.dog_months.data)
                dog_breed = form.dog_breed.data.strip() if form.dog_breed.data else ""
                dog_allergies = form.dog_allergies.data.strip() if form.dog_allergies.data else ""
            

                # Calculate birth year and month
                today = datetime.now()
                birth_year = today.year - dog_years
                birth_month = today.month - dog_months
                if birth_month <= 0:
                    birth_year -= 1
                    birth_month += 12

                # Handle file upload
                pic_filename = None
                if 'file' in request.files:
                    dog_pic = request.files['file']
                    if dog_pic and dog_pic.filename:
                        original_filename = secure_filename(dog_pic.filename)
                        unique_filename = f"{uuid1()}_{original_filename}"
                        upload_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)
                        dog_pic.save(upload_path)
                        pic_filename = unique_filename

                # Create dog record
                new_dog = Dog(
                    user_id=current_user.id,
                    name=dog_name,
                    gender=dog_gender,
                    breed=dog_breed,
                    allergies=dog_allergies,
                    birth_year_month=birth_year * 100 + birth_month,
                    pic=pic_filename
                )
                db.session.add(new_dog)
                
                # Commit all changes together
                db.session.commit()

                flash(f"Welcome to our platform, {current_user.firstname}! Your profile is now complete.", "success")
                return redirect("/")

            except Exception as e:
                db.session.rollback()
                logging.error(f"Error during onboarding for user {current_user.email}: {e}")
                flash("There was an error saving your information. Please try again.", "error")

        return render_template("onboarding.html", google_maps_api_key=os.environ.get('GOOGLE_MAPS_API_KEY'), form=form)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        """Register a new user with improved validation and error handling"""
        # Redirect if user is already authenticated
        if current_user.is_authenticated:
            return redirect("/")

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
                return redirect("/onboard")

            except IntegrityError:
                db.session.rollback()
                flash("An error occurred. Please try again.", "error")
            except Exception as e:
                db.session.rollback()
                logging.error(f"Error during registration: {e}")
                flash("An unexpected error occurred. Please try again.", "error")

        return render_template("register.html", form=form)

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