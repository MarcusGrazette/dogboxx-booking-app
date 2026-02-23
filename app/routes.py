from flask import request, redirect, render_template, flash, url_for, jsonify
from flask_login import login_required, current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from app.models import User, Client, Dog, DogOwner, Booking, Walker, ServiceType
from app import db, limiter
from app.utils.db_error_handler import handle_db_errors, DBErrorHandler
from email_validator import validate_email, EmailNotValidError
import logging
import os
import uuid
import traceback
from datetime import datetime, timezone, timedelta
from werkzeug.utils import secure_filename
from app.forms import LoginForm, RegisterForm, OnboardingForm, BookingForm
from flask_wtf.csrf import generate_csrf

# Temporary logging
logging.basicConfig(level=logging.DEBUG)

def register_routes(app):

    # Custom error handler for rate limiting
    @app.errorhandler(429)  # 429 Too Many Requests
    def ratelimit_handler(e):
        """Custom error handler for rate limit exceeded"""
        return render_template('error.html',
                              error_code=429,
                              error_message="Too many attempts. Please try again later.",
                              error_description="For security reasons, we limit the number of requests. Please wait a few minutes before trying again."), 429

    @app.context_processor
    def inject_csrf_token():
        return dict(csrf_token=generate_csrf)
    
    app.config["UPLOAD_FOLDER"] = os.path.join(os.getcwd(), "app", "static", "uploads", "dogs")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    
    @app.route("/", methods=["GET", "POST"])
    @login_required
    def index():
        """Render the home page."""
        user = User.query.options(
        joinedload(User.client),
        ).filter_by(id=current_user.id).first()

        # Get user's dogs through DogOwner relationship
        user_dogs = Dog.query.join(DogOwner).filter(DogOwner.user_id == current_user.id).all()

        # Return upcoming bookings
        today = datetime.now(timezone.utc).date()
        upcoming_bookings_query = Booking.query.filter(
            Booking.user_id == current_user.id,
            Booking.status != 'cancelled',
            Booking.date > today
        ).order_by(Booking.date.asc()).limit(15)

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
            
            # Validate slot against allowed enum values
            if booking_slot not in ("Morning", "Afternoon"):
                errors.append("Invalid booking slot selected.")

             # Ensure the user has at least one dog to book
            if not user or not user_dogs:
                errors.append("No dog found on your account. Please add a dog before booking.")

            if errors:
                for e in errors:
                    flash(e, "danger")
            else:
                # Use context manager for error handling
                with DBErrorHandler(
                    flash_message=True, 
                    custom_error_messages={
                        IntegrityError: "Could not create booking due to a conflict. You might already have a booking at this time.",
                        OperationalError: "Our booking system is temporarily unavailable. Please try again later."
                    }
                ):
                    dog_id = user_dogs[0].id
                    # Look up default service type by slug
                    default_service = ServiceType.query.filter_by(slug='group-walk', active=True).first()
                    if not default_service:
                        flash("No service type available. Please contact support.", "danger")
                        return redirect(url_for("index"))
                    new_booking = Booking(
                        user_id=user.id,
                        dog_id=dog_id,
                        service_type_id=default_service.id,
                        date=booking_date,
                        slot=booking_slot
                    )
                    db.session.add(new_booking)
                    db.session.commit()
                    flash("Success - booking request submitted", "success")
                    return redirect(url_for("index"))
        
        return render_template("index.html", user=user, client=user.client, dogs=user_dogs, bookings=upcoming_bookings, form=form) # type: ignore

    @app.route("/login", methods=["GET", "POST"])
    @limiter.limit("5 per minute, 20 per hour")  # Limit login attempts
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

            # Handle next page parameter with role-based fallback
            #next_page = request.args.get('next')
            #if next_page and next_page.startswith('/'):
            #    return redirect(next_page)
            
            # Redirect based on user role
            return _redirect_by_role(user)

        return render_template("login.html", form=form)

    def _redirect_by_role(user):
        """Helper function to redirect users based on their role"""
        if user.role == 'admin':
            return redirect(url_for('admin'))
        elif user.role == 'walker':
            return redirect(url_for('walker'))
        elif user.role == 'client':
            return redirect(url_for('index'))
        else:
            # Handle unexpected roles gracefully
            flash("Unknown user role. Please contact support.", "warning")
            return redirect(url_for('index'))

    @app.route("/onboard", methods=["GET", "POST"])
    @login_required
    def onboard():
        """Handle complete user onboarding process"""
        if current_user.role != 'client':
            flash("Onboarding is only required for clients.", "info")
            return redirect(url_for('index'))

        client = Client.query.filter_by(user_id=current_user.id).first()
        if client and client.onboarding_completed:
            flash("You have already completed onboarding!", "info")
            return redirect(url_for('index'))

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

                # Handle file upload with enhanced security
                pic_filename = None
                if 'file' in request.files:
                    dog_pic = request.files['file']
                    if dog_pic and dog_pic.filename:
                        try:
                            # Import required libraries
                            from PIL import Image
                            from pathlib import Path
                            import uuid
                            
                            # 1. Verify it's a valid image by attempting to open it
                            try:
                                img = Image.open(dog_pic)
                                img.verify()  # Verify it's a valid image
                                dog_pic.seek(0)  # Reset file pointer after verification
                            except Exception:
                                raise ValueError("Invalid image file")
                            
                            # 2. Check allowed extensions using a whitelist approach
                            file_extension = Path(secure_filename(dog_pic.filename)).suffix.lower()
                            if file_extension not in ['.jpg', '.jpeg', '.png', '.gif']:
                                raise ValueError("Unsupported file format")
                                
                            # 3. Process image to strip metadata and resize
                            img = Image.open(dog_pic)
                            
                            # Create a new image without metadata
                            img_without_exif = Image.new(img.mode, img.size)
                            img_without_exif.putdata(list(img.getdata()))
                            
                            # Resize the image to a standard size
                            max_size = (800, 800)
                            img_without_exif.thumbnail(max_size, Image.LANCZOS)
                            
                            # 4. Generate completely new filename
                            unique_filename = f"{uuid.uuid4()}{file_extension}"
                            upload_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)
                            
                            # Save with appropriate format
                            if file_extension == '.png':
                                img_without_exif.save(upload_path, 'PNG')
                            elif file_extension in ['.jpg', '.jpeg']:
                                img_without_exif.save(upload_path, 'JPEG', quality=85)
                            elif file_extension == '.gif':
                                img_without_exif.save(upload_path, 'GIF')
                            
                            pic_filename = unique_filename
                            
                        except ValueError as e:
                            logging.error(f"Invalid file upload: {e}")
                            flash(f"Upload error: {str(e)}. Please try a different file.", "error")
                            return render_template("onboarding.html", google_maps_api_key=os.environ.get('GOOGLE_MAPS_API_KEY'), form=form)
                        except Exception as e:
                            logging.error(f"Error processing uploaded file: {e}")
                            flash("There was an error processing your image. Please try a different file.", "error")
                            return render_template("onboarding.html", google_maps_api_key=os.environ.get('GOOGLE_MAPS_API_KEY'), form=form)

                # Create dog record
                new_dog = Dog(
                    name=dog_name,
                    gender=dog_gender,
                    breed=dog_breed,
                    allergies=dog_allergies,
                    birth_year_month=birth_year * 100 + birth_month,
                    pic=pic_filename
                )
                db.session.add(new_dog)
                db.session.flush()  # Get new_dog.id

                # Create DogOwner relationship (user is primary owner)
                dog_owner = DogOwner(
                    dog_id=new_dog.id,
                    user_id=current_user.id,
                    role='primary'
                )
                db.session.add(dog_owner)
                
                # Commit all changes together
                db.session.commit()

                flash(f"Welcome to our platform, {current_user.firstname}! Your profile is now complete.", "success")
                return redirect(url_for('index'))

            except Exception as e:
                db.session.rollback()
                logging.error(f"Error during onboarding for user {current_user.email}: {e}")
                logging.debug(f"Exception details: {traceback.format_exc()}")
                
                # Check for specific error types
                if isinstance(e, SQLAlchemyError):
                    if isinstance(e, IntegrityError):
                        flash("There was a conflict with existing data. This might be because the information already exists in our system.", "error")
                    elif isinstance(e, OperationalError):
                        flash("The database is currently unavailable. Please try again later.", "error")
                    else:
                        flash("There was a database error. Please try again.", "error")
                else:
                    flash("There was an error saving your information. Please try again.", "error")

        return render_template("onboarding.html", google_maps_api_key=os.environ.get('GOOGLE_MAPS_API_KEY'), form=form)

    @app.route("/register", methods=["GET", "POST"])
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
                return redirect(url_for('onboard'))

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

    @app.route("/logout")
    def logout():
        """Log user out (accessible even if the session expired).

        Removing the @login_required decorator prevents Flask-Login from
        intercepting requests to /logout and redirecting to the login page
        with a `next` parameter (and flashing the login message) when the
        user is already unauthenticated.
        """
        # logout_user() is safe to call for anonymous users; it will no-op.
        logout_user()
        return redirect(url_for('login'))
        
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
            # Return the next 10 pending bookings
            today = datetime.now(timezone.utc).date()
            pending_bookings = (
                Booking.query
                .options(joinedload(Booking.dog))
                .filter(Booking.status == 'requested', Booking.date > today)
                .order_by(Booking.date.asc())
                .limit(10)
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

    # assign_walker has been migrated to admin blueprint
         
    # admin_bookings_by_date has been migrated to admin blueprint
    
    @app.route("/cancel_booking", methods=["POST"])
    @login_required
    def cancel_booking():
        """Cancel a booking by changing status to 'Cancelled'. Returns JSON for AJAX requests."""
        
        # Accept JSON or form-encoded data
        data = request.get_json(silent=True) or request.form
        booking_id = data.get("booking_id")
        
        logging.debug(f"Cancel booking request received for booking_id: {booking_id}")
        
        if not booking_id:
            logging.error("Missing booking_id in cancel request")
            return jsonify(success=False, message="Missing booking ID"), 400

        try:
            booking = Booking.query.filter_by(
                id=int(booking_id), 
                user_id=current_user.id  # Ensure user can only cancel their own bookings
            ).first()
            
            logging.debug(f"Booking found: {booking}")
            
            if not booking:
                logging.error(f"Booking not found or user {current_user.id} not authorized")
                return jsonify(success=False, message="Booking not found or not authorized"), 404
                
            # Check if booking can be cancelled (not already cancelled)
            if booking.status == 'cancelled':
                logging.warning(f"Booking {booking_id} is already cancelled")
                return jsonify(success=False, message="Booking is already cancelled"), 400

            # Update booking status
            booking.status = "cancelled"
            db.session.commit()
            
            logging.info(f"Booking {booking_id} successfully cancelled by user {current_user.id}")

            return jsonify(
                success=True, 
                message="Booking cancelled successfully", 
                booking={"id": booking.id, "status": booking.status}
            ), 200
            
        except ValueError:
            logging.error(f"Invalid booking_id format: {booking_id}")
            return jsonify(success=False, message="Invalid booking ID"), 400
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error cancelling booking {booking_id}: {e}")
            return jsonify(success=False, message="Server error"), 500
    
    @app.route("/walker_schedule", methods=["GET", "POST"])
    @login_required
    def walker_schedule():
        """Show a walker their schedule for this week."""
        if current_user.role != 'walker':
            return "Forbidden", 403
        
        return render_template("walker_schedule.html", user=current_user)

    # admin_calendar_data has been migrated to admin blueprint
        
        