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

        # Return upcoming bookings
        today = datetime.now(timezone.utc).date()
        upcoming_bookings_query = Booking.query.filter(
            Booking.user_id == current_user.id,
            Booking.status != 'Cancelled',
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
            flash("Success - booking request submitted", "success")
            return redirect(url_for("index"))
        
        return render_template("index.html", user=user, client=user.client, dogs=user.dogs, bookings=upcoming_bookings, form=form) # type: ignore

    @app.route("/login", methods=["GET", "POST"])
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
                .filter(Booking.status == 'Pending', Booking.date > today)
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

    @app.route("/admin/assign_walker", methods=["POST"])
    @login_required
    def assign_walker():
        """Assign a walker and slot to a booking (admin only). Returns JSON for AJAX requests."""
        if current_user.role != 'admin':
            return jsonify(success=False, message="Forbidden"), 403

        # Accept JSON or form-encoded
        data = request.get_json(silent=True) or request.form
        booking_id = data.get("booking_id")
        walker_id = data.get("walker_id")
        slot = data.get("slot")  # New parameter for slot assignment

        # booking_id is required
        if not booking_id:
            return jsonify(success=False, message="Missing booking ID"), 400

        # Normalize walker_id: treat None, empty string, or literal 'null'/'None' as unassign
        unassign = False
        if walker_id is None or str(walker_id).strip() == "" or str(walker_id).lower() in ("null", "none"):
            unassign = True

        # Validate slot if provided
        if slot and slot not in ("Morning", "Afternoon"):
            return jsonify(success=False, message="Invalid slot value"), 400

        try:
            booking = Booking.query.filter_by(id=int(booking_id)).first()
            if not booking:
                return jsonify(success=False, message="Booking not found"), 404

            # Handle unassign (move back to pending)
            if unassign:
                if slot:
                    booking.slot = slot
                booking.walker_id = None
                booking.status = 'Pending'
                db.session.commit()
                return jsonify(
                    success=True,
                    message="Booking unassigned and set to Pending",
                    booking={"id": booking.id, "walker_id": None, "status": booking.status}
                ), 200

            # Otherwise, assign to a walker (normal flow)
            walker = Walker.query.filter_by(id=int(walker_id)).first()
            if not walker:
                return jsonify(success=False, message="Walker not found"), 404

            # Check walker capacity for the given slot and date
            if slot:
                same_slot_bookings = Booking.query.filter(
                    Booking.walker_id == walker.id,
                    Booking.date == booking.date,
                    Booking.slot == slot,
                    Booking.status != 'Cancelled',
                    Booking.id != booking.id  # Exclude current booking if reassigning
                ).count()
                
                if same_slot_bookings >= 6:
                    return jsonify(success=False, message=f"Walker already has maximum bookings (6) for {slot} slot"), 400

            # Update walker assignment and slot
            booking.walker_id = walker.id
            booking.status = 'Confirmed'
            if slot:
                booking.slot = slot
            db.session.commit()

            return jsonify(
                success=True, 
                message="Walker and slot assigned successfully", 
                booking={
                    "id": booking.id, 
                    "walker_id": walker.id,
                    "walker_name": walker.firstname,
                    "slot": booking.slot
                }
            ), 200
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error assigning/unassigning walker: {e}")
            return jsonify(success=False, message="Server error"), 500
         
    @app.route("/admin/bookings_by_date")
    @login_required
    def admin_bookings_by_date():
        """Return HTML fragment of drag-and-drop booking allocation interface (admin only)."""
        if current_user.role != 'admin':
            return "Forbidden", 403

        # Get date from query parameter
        date_str = request.args.get('date')
        if not date_str:
            return "Missing date parameter", 400

        try:
            # Parse the date string (format: YYYY-MM-DD)
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # Get all bookings for the selected date (pending and assigned)
            all_bookings = (
                Booking.query
                .options(joinedload(Booking.dog), joinedload(Booking.walker))
                .filter(
                    Booking.date == selected_date,
                    Booking.status != 'Cancelled'
                )
                .all()
            )
            
            # Separate pending and assigned bookings
            pending_bookings = [b for b in all_bookings if b.status == 'Pending']
            assigned_bookings = [b for b in all_bookings if b.walker_id is not None]
            
            # Add display properties to all bookings
            for b in all_bookings:
                b.dog_name = b.dog.name if b.dog else "Unknown"
                b.dog_pic = b.dog.pic if b.dog and b.dog.pic else None
                b.walker_name = b.walker.firstname if b.walker else None

            # Get all walkers
            walkers = Walker.query.all()
            
            # Create walker capacity tracking
            walker_capacity = {}
            for walker in walkers:
                walker_capacity[walker.id] = {
                    'Morning': 0,
                    'Afternoon': 0
                }
            
            # Count assigned bookings per walker per slot
            for booking in assigned_bookings:
                if booking.walker_id and booking.slot:
                    walker_capacity[booking.walker_id][booking.slot] += 1
            
            # Generate the drag-and-drop HTML interface
            if not pending_bookings and not assigned_bookings:
                return '<p class="card-text"><i class="bi bi-info-circle"></i> No booking requests for the selected date. </p>'
            
            html = '''
            <div class="row g-3" id="drag-drop-container">
                <!-- Pending Bookings Column -->
                <div class="col-md-3">
                    <div class="card h-100">
                        <div class="card-header bg-warning text-dark">
                            <h6 class="mb-0"><i class="bi bi-hourglass"></i> Pending</h6>
                        </div>
                        <div class="card-body p-2">
                            <!-- Morning Pending -->
                            <div class="drop-zone pending-zone" data-slot="Morning" data-walker-id="">
                                <h6 class="text-muted mb-2">Morning</h6>
                                <div class="booking-cards">
            '''
            
            # Add morning pending bookings
            morning_pending = [b for b in pending_bookings if b.slot == 'Morning']
            for booking in morning_pending:
                pic_src = f"/static/images/{booking.dog_pic}" if booking.dog_pic else "/static/images/default-dog.png"
                html += f'''
                    <div class="card booking-card draggable bg-light border-dark" 
                        draggable="true" 
                        data-booking-id="{booking.id}"
                        data-current-slot="{booking.slot}"
                        data-current-walker-id="{booking.walker_id or ''}"
                        data-dog-name="{booking.dog_name}"
                        data-dog-pic="{booking.dog_pic or ''}">
                        <div class="d-flex align-items-center gap-2 p-2">
                            <div style="width: 30px; height: 30px; overflow: hidden;" class="rounded-circle flex-shrink-0">
                                <img src="{pic_src}" class="img-fluid" alt="{booking.dog_name}" 
                                    style="width: 100%; height: 100%; object-fit: cover;"
                                    onerror="this.onerror=null; this.src='/static/images/default-dog.png'">
                            </div>
                            <div>
                                <small>{booking.dog_name}</small>
                            </div>
                        </div>
                    </div>
                '''
            
            html += '''
                                </div>
                            </div>
                            
                            <hr>
                            
                            <!-- Afternoon Pending -->
                            <div class="drop-zone pending-zone" data-slot="Afternoon" data-walker-id="">
                                <h6 class="text-muted mb-2">Afternoon</h6>
                                <div class="booking-cards">
            '''
            
            # Add afternoon pending bookings
            afternoon_pending = [b for b in pending_bookings if b.slot == 'Afternoon']
            for booking in afternoon_pending:
                pic_src = f"/static/images/{booking.dog_pic}" if booking.dog_pic else "/static/images/default-dog.png"
                html += f'''
                    <div class="card booking-card draggable bg-light border-dark" 
                        draggable="true" 
                        data-booking-id="{booking.id}"
                        data-current-slot="{booking.slot}"
                        data-current-walker-id="{booking.walker_id or ''}"
                        data-dog-name="{booking.dog_name}"
                        data-dog-pic="{booking.dog_pic or ''}">
                        <div class="d-flex align-items-center gap-2 p-2">
                            <div style="width: 30px; height: 30px; overflow: hidden;" class="rounded-circle flex-shrink-0">
                                <img src="{pic_src}" class="img-fluid" alt="{booking.dog_name}" 
                                    style="width: 100%; height: 100%; object-fit: cover;"
                                    onerror="this.onerror=null; this.src='/static/images/default-dog.png'">
                            </div>
                            <div>
                                <small>{booking.dog_name}</small>
                            </div>
                        </div>
                    </div>
                '''
            
            html += '''
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            '''
            
            # Add walker columns
            for walker in walkers:
                morning_count = walker_capacity[walker.id]['Morning']
                afternoon_count = walker_capacity[walker.id]['Afternoon']
                morning_assigned = [b for b in assigned_bookings if b.walker_id == walker.id and b.slot == 'Morning']
                afternoon_assigned = [b for b in assigned_bookings if b.walker_id == walker.id and b.slot == 'Afternoon']
                
                html += f'''
                <!-- Walker {walker.firstname} Column -->
                <div class="col-md-3">
                    <div class="card h-100">
                        <div class="card-header bg-primary text-white">
                            <h6 class="mb-0"><i class="bi bi-person-walking"></i> {walker.firstname}</h6>
                        </div>
                        <div class="card-body p-2">
                            <!-- Morning Assignments -->
                            <div class="drop-zone walker-zone {'bg-light' if morning_count < 6 else 'bg-danger bg-opacity-25'}" 
                                data-slot="Morning" data-walker-id="{walker.id}">
                                <h6 class="text-muted mb-2">Morning ({morning_count}/6)</h6>
                                <div class="booking-cards">
                '''
                
                # Add assigned morning bookings for this walker
                for booking in morning_assigned:
                    pic_src = f"/static/images/{booking.dog_pic}" if booking.dog_pic else "/static/images/default-dog.png"
                    html += f'''
                        <div class="card booking-card draggable bg-light border-success" 
                            draggable="true" 
                            data-booking-id="{booking.id}"
                            data-current-slot="{booking.slot}"
                            data-current-walker-id="{booking.walker_id}"
                            data-dog-name="{booking.dog_name}"
                            data-dog-pic="{booking.dog_pic or ''}">
                            <div class="d-flex align-items-center gap-2 p-2">
                                <div style="width: 30px; height: 30px; overflow: hidden;" class="rounded-circle flex-shrink-0">
                                    <img src="{pic_src}" class="img-fluid" alt="{booking.dog_name}" 
                                        style="width: 100%; height: 100%; object-fit: cover;"
                                        onerror="this.src='/static/images/default-dog.png'">
                                </div>
                                <div>
                                    <small>{booking.dog_name}</small>
                                </div>
                            </div>
                        </div>
                    '''
                
                html += f'''
                                </div>
                            </div>
                            
                            <hr>
                            
                            <!-- Afternoon Assignments -->
                            <div class="drop-zone walker-zone {'bg-light' if afternoon_count < 6 else 'bg-danger bg-opacity-25'}" 
                                data-slot="Afternoon" data-walker-id="{walker.id}">
                                <h6 class="text-muted mb-2">Afternoon ({afternoon_count}/6)</h6>
                                <div class="booking-cards">
                '''
                
                # Add assigned afternoon bookings for this walker
                for booking in afternoon_assigned:
                    pic_src = f"/static/images/{booking.dog_pic}" if booking.dog_pic else "/static/images/default-dog.png"
                    html += f'''
                        <div class="card booking-card draggable bg-light border-success" 
                            draggable="true" 
                            data-booking-id="{booking.id}"
                            data-current-slot="{booking.slot}"
                            data-current-walker-id="{booking.walker_id}"
                            data-dog-name="{booking.dog_name}"
                            data-dog-pic="{booking.dog_pic or ''}">
                            <div class="d-flex align-items-center gap-2 p-2">
                                <div style="width: 30px; height: 30px; overflow: hidden;" class="rounded-circle flex-shrink-0">
                                    <img src="{pic_src}" class="img-fluid" alt="{booking.dog_name}" 
                                        style="width: 100%; height: 100%; object-fit: cover;"
                                        onerror="this.src='/static/images/default-dog.png'">
                                </div>
                                <div>
                                    <small>{booking.dog_name}</small>
                                </div>
                            </div>
                        </div>
                    '''
                
                html += '''
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                '''
            
            html += '''
            </div>
            
            <style>
            .drop-zone {
                min-height: 120px;
                border: 2px dashed transparent;
                border-radius: 0.375rem;
                padding: 0.5rem;
                transition: all 0.3s ease;
            }
            
            .drop-zone.drag-over {
                border-color: #0d6efd;
                background-color: rgba(13, 110, 253, 0.1) !important;
            }
            
            .drop-zone.drag-over-invalid {
                border-color: #dc3545;
                background-color: rgba(220, 53, 69, 0.1) !important;
            }
            
            .booking-card {
                margin-bottom: 0.5rem;
                border-radius: 0.375rem;
                cursor: grab;
                transition: all 0.3s ease;
            }
            
            .booking-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            }
            
            .booking-card.dragging {
                opacity: 0.5;
                transform: rotate(5deg);
            }
            
            .booking-card.snap-back {
                animation: snapBack 0.5s ease-out;
            }
            
            @keyframes snapBack {
                0% { transform: scale(1.1) rotate(5deg); }
                50% { transform: scale(0.95); }
                100% { transform: scale(1) rotate(0deg); }
            }
            
            .booking-cards {
                min-height: 60px;
            }
            </style>
            '''
            
            return html
            
        except ValueError:
            return "Invalid date format", 400
        except Exception as e:
            logging.error(f"Error loading bookings by date: {e}")
            return "Server error", 500
    
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
            if booking.status == 'Cancelled':
                logging.warning(f"Booking {booking_id} is already cancelled")
                return jsonify(success=False, message="Booking is already cancelled"), 400

            # Update booking status to Cancelled
            booking.status = "Cancelled"
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
        
        