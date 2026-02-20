from . import db
from datetime import datetime, timezone
from .validators import password_strength_check


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    firstname = db.Column(db.String(80), nullable=False)
    lastname = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    role = db.Column(db.Enum('client', 'walker', 'admin', name='user_roles'),
                     nullable=False, default='client')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    active = db.Column(db.Boolean, default=True, nullable=False)
    hashed_password = db.Column(db.String(256), nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)

    # Flask-Login required methods
    def get_id(self):
        return str(self.id)

    def is_authenticated(self):
        return True

    def is_active(self):
        return self.active

    def is_anonymous(self):
        return False

    def __repr__(self):
        return f'<User {self.email}>'

    def to_dict(self):
        return {
            'id': self.id,
            'firstname': self.firstname,
            'lastname': self.lastname,
            'email': self.email,
            'role': self.role,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_active': self.active,
            'must_change_password': self.must_change_password,
        }

    @property
    def full_name(self):
        return f"{self.firstname} {self.lastname}"

    @property
    def is_admin(self):
        return self.role == 'admin'

    @staticmethod
    def validate_password(password):
        return password_strength_check(password)


class Client(db.Model):
    __tablename__ = 'clients'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)

    # Google Places data
    place_id = db.Column(db.String(200), nullable=True)
    formatted_address = db.Column(db.String(300), nullable=True)
    display_name = db.Column(db.String(200), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    # Legacy address fields
    street_address = db.Column(db.String(200), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(50), nullable=True)
    postal_code = db.Column(db.String(20), nullable=True)
    country = db.Column(db.String(50), nullable=True)

    # Onboarding
    onboarding_completed = db.Column(db.Boolean, default=False, nullable=False)
    onboarding_completed_at = db.Column(db.DateTime, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    pickup_instructions = db.Column(db.String(500), nullable=True)

    # Relationship
    user = db.relationship('User', backref=db.backref('client', uselist=False))

    def __repr__(self):
        return f'<Client {self.user.full_name if self.user else self.id}>'

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'place_id': self.place_id,
            'formatted_address': self.formatted_address,
            'display_name': self.display_name,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'street_address': self.street_address,
            'city': self.city,
            'state': self.state,
            'postal_code': self.postal_code,
            'country': self.country,
            'onboarding_completed': self.onboarding_completed,
            'onboarding_completed_at': self.onboarding_completed_at.isoformat() if self.onboarding_completed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'pickup_instructions': self.pickup_instructions,
        }


class Dog(db.Model):
    __tablename__ = 'dogs'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    birth_year_month = db.Column(db.Numeric, nullable=True)
    gender = db.Column(db.Enum('male', 'female', name='dog_gender'), nullable=True)
    breed = db.Column(db.String(100), nullable=True)
    allergies = db.Column(db.String(200), nullable=True)
    other_info = db.Column(db.String(500), nullable=True)
    pic = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Many-to-many relationship with owners via DogOwner
    owners = db.relationship('DogOwner', back_populates='dog', lazy='dynamic')

    def __repr__(self):
        return f'<Dog {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'birth_year_month': float(self.birth_year_month) if self.birth_year_month else None,
            'gender': self.gender,
            'breed': self.breed,
            'allergies': self.allergies,
            'other_info': self.other_info,
            'pic': self.pic,
        }

    @property
    def primary_owner(self):
        """Return the primary owner's User record."""
        do = DogOwner.query.filter_by(dog_id=self.id, role='primary').first()
        return do.user if do else None


class DogOwner(db.Model):
    """Many-to-many join table: one dog can have multiple owners,
    one owner can have multiple dogs."""
    __tablename__ = 'dog_owners'
    __table_args__ = (
        db.UniqueConstraint('dog_id', 'user_id', name='uq_dog_owner'),
    )

    id = db.Column(db.Integer, primary_key=True)
    dog_id = db.Column(db.Integer, db.ForeignKey('dogs.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.Enum('primary', 'secondary', name='dog_owner_role'),
                     nullable=False, default='primary')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    dog = db.relationship('Dog', back_populates='owners')
    user = db.relationship('User', backref=db.backref('dog_ownerships', lazy='dynamic'))


class Walker(db.Model):
    __tablename__ = 'walkers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)

    user = db.relationship('User', backref=db.backref('walker', uselist=False))
    schedules = db.relationship('WalkerSchedule', back_populates='walker', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'firstname': self.user.firstname if self.user else None,
            'lastname': self.user.lastname if self.user else None,
        }

    @property
    def firstname(self):
        return self.user.firstname if self.user else None

    @property
    def lastname(self):
        return self.user.lastname if self.user else None

    @property
    def full_name(self):
        return self.user.full_name if self.user else None


class WalkerSchedule(db.Model):
    """Default weekly availability for a walker.
    day_of_week: 0=Monday, 6=Sunday."""
    __tablename__ = 'walker_schedules'
    __table_args__ = (
        db.UniqueConstraint('walker_id', 'day_of_week', 'slot', name='uq_walker_day_slot'),
    )

    id = db.Column(db.Integer, primary_key=True)
    walker_id = db.Column(db.Integer, db.ForeignKey('walkers.id'), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False)  # 0=Mon, 6=Sun
    slot = db.Column(db.Enum('Morning', 'Afternoon', name='schedule_slot'), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    walker = db.relationship('Walker', back_populates='schedules')

    def to_dict(self):
        return {
            'id': self.id,
            'walker_id': self.walker_id,
            'day_of_week': self.day_of_week,
            'slot': self.slot,
            'active': self.active,
        }


class ServiceType(db.Model):
    __tablename__ = 'service_types'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    capacity_model = db.Column(
        db.Enum('walker_assigned', 'facility_capacity', name='capacity_model_type'),
        nullable=False)
    slot_type = db.Column(
        db.Enum('morning_afternoon', 'full_half_day', 'hourly', name='slot_type_enum'),
        nullable=False)
    requires_walker = db.Column(db.Boolean, default=True, nullable=False)
    requires_compatibility_check = db.Column(db.Boolean, default=False, nullable=False)
    default_max_capacity = db.Column(db.Integer, nullable=True)  # per walker for walks, facility-wide for daycare
    active = db.Column(db.Boolean, default=True, nullable=False)
    settings = db.Column(db.JSON, nullable=True)  # future-proofing
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<ServiceType {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'capacity_model': self.capacity_model,
            'slot_type': self.slot_type,
            'requires_walker': self.requires_walker,
            'requires_compatibility_check': self.requires_compatibility_check,
            'default_max_capacity': self.default_max_capacity,
            'active': self.active,
            'settings': self.settings,
        }


class Booking(db.Model):
    __tablename__ = 'bookings'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    dog_id = db.Column(db.Integer, db.ForeignKey('dogs.id'), nullable=False)
    service_type_id = db.Column(db.Integer, db.ForeignKey('service_types.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    slot = db.Column(db.Enum('Morning', 'Afternoon', 'Full Day', 'Half Day AM', 'Half Day PM',
                             name='booking_slot'), nullable=False)
    walker_id = db.Column(db.Integer, db.ForeignKey('walkers.id'), nullable=True)
    pickup_order = db.Column(db.Integer, nullable=True)  # set by admin drag-drop; 1 = first pickup
    status = db.Column(db.Enum('requested', 'confirmed', 'modified', 'rejected',
                               'cancelled', 'completed', 'waitlisted',
                               name='booking_status'),
                       nullable=False, default='requested')
    client_notes = db.Column(db.Text, nullable=True)
    admin_notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    confirmed_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancelled_by = db.Column(db.Enum('client', 'admin', name='cancelled_by_type'), nullable=True)

    # Relationships
    user = db.relationship('User', backref=db.backref('bookings', lazy=True))
    dog = db.relationship('Dog', backref=db.backref('bookings', lazy=True))
    walker = db.relationship('Walker', backref=db.backref('bookings', lazy=True))
    service_type = db.relationship('ServiceType', backref=db.backref('bookings', lazy=True))
    status_history = db.relationship('BookingStatusChange', back_populates='booking',
                                     order_by='BookingStatusChange.created_at', lazy='dynamic')

    def __repr__(self):
        return f'<Booking {self.id} {self.status}>'

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'dog_id': self.dog_id,
            'service_type_id': self.service_type_id,
            'date': self.date.isoformat() if self.date else None,
            'slot': self.slot,
            'walker_id': self.walker_id,
            'pickup_order': self.pickup_order,
            'status': self.status,
            'client_notes': self.client_notes,
            'admin_notes': self.admin_notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'confirmed_at': self.confirmed_at.isoformat() if self.confirmed_at else None,
            'cancelled_at': self.cancelled_at.isoformat() if self.cancelled_at else None,
            'cancelled_by': self.cancelled_by,
        }


class BookingStatusChange(db.Model):
    """Audit trail for booking status changes."""
    __tablename__ = 'booking_status_changes'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    from_status = db.Column(db.String(20), nullable=True)  # null for initial creation
    to_status = db.Column(db.String(20), nullable=False)
    changed_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    booking = db.relationship('Booking', back_populates='status_history')
    changed_by = db.relationship('User')

    def to_dict(self):
        return {
            'id': self.id,
            'booking_id': self.booking_id,
            'from_status': self.from_status,
            'to_status': self.to_status,
            'changed_by_id': self.changed_by_id,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class WalkEvent(db.Model):
    """Tracks pickup/dropoff events during walks."""
    __tablename__ = 'walk_events'

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    event_type = db.Column(db.Enum('en_route', 'picked_up', 'dropped_off',
                                    name='walk_event_type'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    booking = db.relationship('Booking', backref=db.backref('walk_events', lazy=True,
                                                             order_by='WalkEvent.created_at'))

    def to_dict(self):
        return {
            'id': self.id,
            'booking_id': self.booking_id,
            'event_type': self.event_type,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'latitude': self.latitude,
            'longitude': self.longitude,
        }
