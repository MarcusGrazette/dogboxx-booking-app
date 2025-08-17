from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError
from app.models import User, Client, Dog, Booking, Walker
from app import db
from email_validator import validate_email, EmailNotValidError
import logging
import os
from datetime import datetime, timezone, timedelta
from werkzeug.utils import secure_filename
from uuid import uuid1
from app.forms import LoginForm, RegisterForm, OnboardingForm, BookingForm
from flask_wtf.csrf import generate_csrf

# Temporary logging
logging.basicConfig(level=logging.DEBUG)

def register_routes(app):

    @app.context_processor
    def inject_csrf_token():
        return dict(csrf_token=generate_csrf)
    
    app.config["UPLOAD_FOLDER"] = os.path.join(os.getcwd(), "app", "static", "images")
    
    @app.route("/", methods=["GET", "POST"])
    @login_required
    def index():
        """Render the home page."""
        user = User.query.options(
        joinedload(User.client),  
        joinedload(User.dogs)
        ).filter_by(id=current_user.id).first()

        # Return the next 5 confirmed bookings
        today = datetime.now(timezone.utc).date()
        upcoming_bookings_query = Booking.query.filter(
            Booking.user_id == current_user.id,
            Booking.status == 'Confirmed',
            Booking.date > today
        ).order_by(Booking.date.asc()).limit(5)

        upcoming_bookings = list(upcoming_bookings_query)
        for b in upcoming_bookings:
            if b.date:
                b.date_display = b.date.strftime("%d %b")
            else:
                b.date_display = None

        form = BookingForm()
        if form.validate_on_submit():
            booking_date = form.date.data
            booking_slot = form.slot.data

            # Set date bounds, not in the past or more than three months in the future
            today = datetime.now(timezone.utc).date()
            max_date = (datetime.now(timezone.utc) + timedelta(days=90)).date()

            errors = []
            if booking_date < today:
                errors.append("Booking date cannot be in the past.")
            if booking_date > max_date:
                errors.append("Booking date cannot be more than 3 months in the future.")
            
            # Validate slot against allowed enum values
            if booking_slot not in ("Morning", "Afternoon"):
                errors.append("Invalid booking slot selected.")

             # Ensure the user has at least one dog to book
            if not user or not getattr(user, "dogs", None):
                errors.append("No dog found on your account. Please add a dog before booking.")

            if errors:
                for e in errors:
                    flash(e, "danger")
            else:
                dog_id = user.dogs[0].id  # This assumes a one to one user to dog relationship
                new_booking = Booking(
                    user_id=user.id,
                    dog_id=dog_id,
                    date=booking_date,
                    slot=booking_slot
            )
            db.session.add(new_booking)
            db.session.commit()
            flash("Booking created successfully.", "success")
            return redirect(url_for("index"))
        

        return render_template("index.html", user=user, client=user.client, dogs=user.dogs, bookings=upcoming_bookings, form=form) # type: ignore

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """Log user in"""
        # Redirect if user is already authenticated
        if current_user.is_authenticated:
            return redirect("/")

        form = LoginForm()
        if form.validate_on_submit():
            email = form.email.data.strip().lower() # type: ignore
            password = form.password.data
            remember_me = form.remember_me.data

            # Query database for user
            user = User.query.filter_by(email=email).first()

            # Check credentials
            if not user or not check_password_hash(user.hashed_password, password): # type: ignore
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
                place_id = form.place_id.data.strip() # type: ignore
                formatted_address = form.formatted_address.data.strip() # type: ignore
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
    
    @app.route("/admin")
    @login_required
    def admin():
        if current_user.role == 'admin':
            # Return the next 5 confirmed bookings (could I turn this block into a resuable function? I've used it twice)
            today = datetime.now(timezone.utc).date()
            pending_bookings = (
                Booking.query
                .options(joinedload(Booking.dog))
                .filter(Booking.status == 'Pending', Booking.date == today)
                .order_by(Booking.date.asc())
                .limit(50)
                .all()
            )
            
            for b in pending_bookings:
                b.date_display = b.date.strftime("%a %-d %b") if b.date else None
                b.dog_name = b.dog.name if b.dog else None

            walkers = Walker.query.all()
            
            return render_template("admin.html", 
                                   user=current_user, 
                                   pending_bookings=pending_bookings, 
                                   walkers=walkers,
                                   today_date=today.strftime('%Y-%m-%d'))
        else:
            flash("Only admins can access.", "danger")
            return redirect(url_for("index"))

    @app.route("/admin/assign_walker", methods=["POST"])
    @login_required
    def assign_walker():
        """Assign a walker to a booking without changing status (admin only). Returns JSON for AJAX requests."""
        if current_user.role != 'admin':
            return jsonify(success=False, message="Forbidden"), 403

        # Accept JSON or form-encoded
        data = request.get_json(silent=True) or request.form
        booking_id = data.get("booking_id")
        walker_id = data.get("walker_id")

        if not booking_id or not walker_id:
            return jsonify(success=False, message="Missing booking or walker"), 400

        try:
            booking = Booking.query.filter_by(id=int(booking_id)).first()
            walker = Walker.query.filter_by(id=int(walker_id)).first()

            if not booking or not walker:
                return jsonify(success=False, message="Booking or walker not found"), 404

            # Only update the walker assignment - do not change status
            booking.walker_id = walker.id
            db.session.commit()

            return jsonify(
                success=True, 
                message="Walker assigned", 
                walker={"id": walker.id, "name": walker.firstname}
            ), 200
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error assigning walker: {e}")
            return jsonify(success=False, message="Server error"), 500
        
    @app.route("/admin/confirm_booking", methods=["POST"])
    @login_required
    def confirm_booking():
        """Confirm a booking by changing status to 'Confirmed' (admin only). Returns JSON for AJAX requests."""
        if current_user.role != 'admin':
            return jsonify(success=False, message="Forbidden"), 403

        # Accept JSON or form-encoded
        data = request.get_json(silent=True) or request.form
        booking_id = data.get("booking_id")

        if not booking_id:
            return jsonify(success=False, message="Missing booking ID"), 400

        try:
            booking = Booking.query.filter_by(id=int(booking_id)).first()

            if not booking:
                return jsonify(success=False, message="Booking not found"), 404

            # Check if booking already has a walker assigned
            if not booking.walker_id:
                return jsonify(success=False, message="Cannot confirm booking without assigned walker"), 400

            # Update booking status to Confirmed
            booking.status = "Confirmed"
            db.session.commit()

            return jsonify(
                success=True, 
                message="Booking confirmed", 
                booking={"id": booking.id, "status": booking.status}
            ), 200
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error confirming booking: {e}")
            return jsonify(success=False, message="Server error"), 500
        
    @app.route("/admin/bookings_by_date")
    @login_required
    def admin_bookings_by_date():
        """Return HTML fragment of pending bookings for a specific date (admin only)."""
        if current_user.role != 'admin':
            return "Forbidden", 403

        # Get date from query parameter
        date_str = request.args.get('date')
        if not date_str:
            return "Missing date parameter", 400

        try:
            # Parse the date string (format: YYYY-MM-DD)
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # Get pending bookings for the selected date
            pending_bookings = (
                Booking.query
                .options(joinedload(Booking.dog))
                .filter(
                    Booking.status == 'Pending', 
                    Booking.date == selected_date
                )
                .order_by(Booking.slot.asc())
                .all()
            )
            
            # Add display properties
            for b in pending_bookings:
                b.dog_name = b.dog.name if b.dog else None

            # Get walkers for the select dropdowns
            walkers = Walker.query.all()
            
            # Return just the table HTML fragment
            if pending_bookings:
                table_html = '''
                <table class="table table-striped table-bordered">
                    <thead class="table-dark">
                        <tr>
                            <td>Dog</td>
                            <td>Slot</td>
                            <td>Assigned to</td>
                            <td>Confirm</td>
                        </tr>
                    </thead>
                    <tbody id="bookings-tbody">
                '''
                
                for booking in pending_bookings:
                    walker_options = ""
                    for w in walkers:
                        selected = "selected" if booking.walker_id == w.id else ""
                        walker_options += f'<option value="{w.id}" {selected}>{w.firstname}</option>'
                    
                    disabled = "" if booking.walker_id else "disabled"
                    
                    table_html += f'''
                    <tr id="booking-row-{booking.id}">
                        <td>{booking.dog_name}</td>
                        <td>{booking.slot}</td>
                        <td>
                            <select class="form-select assign-walker-select" data-booking-id="{booking.id}">
                                {walker_options}
                            </select>
                        </td>
                        <td>
                            <button class="btn btn-success btn-sm confirm-booking-btn" 
                                    data-booking-id="{booking.id}" {disabled}>
                                <i class="bi bi-check-circle"></i> Confirm
                            </button>
                        </td>
                    </tr>
                    '''
                
                table_html += '''
                    </tbody>
                </table>
                '''
                return table_html
            else:
                return '<p class="card-text">No pending bookings for this date.</p>'
                
        except ValueError:
            return "Invalid date format", 400
        except Exception as e:
            logging.error(f"Error loading bookings by date: {e}")
            return "Server error", 500