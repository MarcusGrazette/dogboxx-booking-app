from . import db
from datetime import datetime, timezone
from .validators import password_strength_check


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    firstname = db.Column(db.String(80), nullable=False)
    lastname = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    role = db.Column(db.Enum('client', 'walker', name='user_roles'),
                     nullable=False, default='client')
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_super_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    active = db.Column(db.Boolean, default=True, nullable=False)
    hashed_password = db.Column(db.String(256), nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    email_marketing = db.Column(db.Boolean, default=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    profile_pic = db.Column(db.String(256), nullable=True)
    notification_preference = db.Column(
        db.Enum('email', name='notification_pref'),
        nullable=False, default='email'
    )

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

    def make_unsubscribe_token(self):
        """Return a signed token for one-click newsletter unsubscribe."""
        from itsdangerous import URLSafeTimedSerializer
        from flask import current_app
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        return s.dumps({'user_id': self.id}, salt='newsletter-unsubscribe')

    @staticmethod
    def verify_unsubscribe_token(token, max_age=60 * 60 * 24 * 30):
        """Verify an unsubscribe token (valid for 30 days). Returns User or None."""
        from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
        from flask import current_app
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token, salt='newsletter-unsubscribe', max_age=max_age)
        except (SignatureExpired, BadSignature):
            return None
        from . import db
        return db.session.get(User, data.get('user_id'))

    @staticmethod
    def validate_password(password):
        return password_strength_check(password)


class Client(db.Model):
    __tablename__ = 'clients'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)

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

    maps_url = db.Column(db.String(2048), nullable=True)

    # Relationship
    user = db.relationship('User', backref=db.backref('client', uselist=False))

    def __repr__(self):
        return f'<Client {self.user.full_name if self.user else self.id}>'

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'street_address': self.street_address,
            'city': self.city,
            'state': self.state,
            'postal_code': self.postal_code,
            'country': self.country,
            'onboarding_completed': self.onboarding_completed,
            'onboarding_completed_at': self.onboarding_completed_at.isoformat() if self.onboarding_completed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Dog(db.Model):
    __tablename__ = 'dogs'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    date_of_birth = db.Column(db.Date, nullable=True)
    gender = db.Column(db.Enum('male', 'female', name='dog_gender'), nullable=True)
    breed = db.Column(db.String(100), nullable=True)
    allergies = db.Column(db.String(200), nullable=True)
    other_info = db.Column(db.String(500), nullable=True)
    pic = db.Column(db.String(300), nullable=True)
    whatsapp_group_url = db.Column(db.String(2048), nullable=True)
    pickup_instructions = db.Column(db.String(1000), nullable=True)
    hold_key = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Many-to-many relationship with owners via DogOwner
    owners = db.relationship('DogOwner', back_populates='dog', lazy='dynamic')

    def __repr__(self):
        return f'<Dog {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'date_of_birth': self.date_of_birth.isoformat() if self.date_of_birth else None,
            'gender': self.gender,
            'breed': self.breed,
            'allergies': self.allergies,
            'other_info': self.other_info,
            'pic': self.pic,
            'pickup_instructions': self.pickup_instructions,
        }

    @property
    def primary_owner(self):
        """Return the primary owner's User record."""
        do = DogOwner.query.filter_by(dog_id=self.id, role='primary').first()
        return do.user if do else None

    @property
    def owners_display(self):
        """Return owners' first names joined with ' & ', primary first.
        E.g. 'Hugh' for a single owner, 'Hugh & Gillian' for two owners."""
        ownerships = self.owners.order_by(DogOwner.role).all()  # 'primary' < 'secondary' alphabetically
        names = [o.user.firstname for o in ownerships if o.user and o.user.firstname]
        return ' & '.join(names) if names else ''


class DogOwner(db.Model):
    """Many-to-many join table: one dog can have multiple owners,
    one owner can have multiple dogs."""
    __tablename__ = 'dog_owners'
    __table_args__ = (
        db.UniqueConstraint('dog_id', 'user_id', name='uq_dog_owner'),
    )

    id = db.Column(db.Integer, primary_key=True)
    dog_id = db.Column(db.Integer, db.ForeignKey('dogs.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    role = db.Column(db.Enum('primary', 'secondary', name='dog_owner_role'),
                     nullable=False, default='primary')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    dog = db.relationship('Dog', back_populates='owners')
    user = db.relationship('User', backref=db.backref('dog_ownerships', lazy='dynamic'))


class Walker(db.Model):
    __tablename__ = 'walkers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    does_drop_ins = db.Column(db.Boolean, default=False, nullable=False)

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
    walker_id = db.Column(db.Integer, db.ForeignKey('walkers.id'), nullable=False, index=True)
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

    WALK     = 'group-walk'
    DROP_IN  = 'drop-in'
    DAY_CARE = 'day-care'

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
    __table_args__ = (
        db.Index(
            'ix_booking_dog_date_slot_active',
            'dog_id', 'date', 'slot',
            unique=True,
            postgresql_where=db.text("status NOT IN ('cancelled', 'rejected', 'completed')")
        ),
        db.Index('ix_booking_date_slot_status', 'date', 'slot', 'status'),
    )

    # Named status groups — use these instead of inline string tuples
    CAPACITY_STATUSES = ('requested', 'confirmed', 'modified')       # counts toward slot capacity
    PENDING_STATUSES = ('requested', 'waitlisted')                    # awaiting confirmation
    ACTIVE_STATUSES = ('confirmed', 'requested', 'waitlisted')        # live bookings (admin views)
    WALKER_STATUSES = ('confirmed', 'completed')                      # shown on walker pickup list
    INVOICE_STATUSES = ('confirmed', 'completed', 'cancelled')        # billable activity

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    dog_id = db.Column(db.Integer, db.ForeignKey('dogs.id'), nullable=False)
    service_type_id = db.Column(db.Integer, db.ForeignKey('service_types.id'), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    slot = db.Column(db.Enum('Morning', 'Afternoon', 'Full Day', 'Half Day AM', 'Half Day PM',
                             name='booking_slot'), nullable=False)
    walker_id = db.Column(db.Integer, db.ForeignKey('walkers.id'), nullable=True, index=True)
    pickup_order = db.Column(db.Integer, nullable=True)  # set by admin drag-drop; 1 = first pickup
    status = db.Column(db.Enum('requested', 'confirmed', 'modified', 'rejected',
                               'cancelled', 'completed', 'waitlisted',
                               name='booking_status'),
                       nullable=False, default='requested', index=True)
    client_notes = db.Column(db.Text, nullable=True)
    admin_notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    confirmed_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancelled_by = db.Column(db.Enum('client', 'admin', name='cancelled_by_type'), nullable=True)
    # NULL = client booked it themselves (default for legacy rows and all client-route
    # bookings). Non-null + that user is_admin = admin-initiated booking.
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # Relationships
    user = db.relationship('User', foreign_keys=[user_id],
                           backref=db.backref('bookings', lazy=True))
    dog = db.relationship('Dog', backref=db.backref('bookings', lazy=True))
    walker = db.relationship('Walker', backref=db.backref('bookings', lazy=True))
    service_type = db.relationship('ServiceType', backref=db.backref('bookings', lazy=True))
    created_by = db.relationship('User', foreign_keys=[created_by_id])
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
            'created_by_id': self.created_by_id,
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
    # Correlates rows produced by one bulk action so the activity feed can
    # collapse them into a single expandable cluster. NULL for single-row
    # transitions. uuid4().hex generated once per bulk action, stamped on
    # every row it produces.
    batch_id = db.Column(db.String(36), nullable=True, index=True)
    # Indexed: the activity feed filters this source by created_at month range (F4).
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

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
            'batch_id': self.batch_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class WalkerUnavailability(db.Model):
    """Date-specific exceptions to a walker's default schedule."""
    __tablename__ = 'walker_unavailabilities'
    __table_args__ = (
        db.UniqueConstraint('walker_id', 'date', 'slot', name='uq_walker_date_slot'),
    )

    id = db.Column(db.Integer, primary_key=True)
    walker_id = db.Column(db.Integer, db.ForeignKey('walkers.id'), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False)
    slot = db.Column(db.Enum('Morning', 'Afternoon', name='schedule_slot', create_type=False), nullable=False)
    reason = db.Column(db.String(200), nullable=True)
    # Indexed: the activity feed filters this source by created_at month range (F4).
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)

    walker = db.relationship('Walker', backref='unavailabilities')
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def to_dict(self):
        return {
            'id': self.id,
            'walker_id': self.walker_id,
            'date': self.date.isoformat(),
            'slot': self.slot,
            'reason': self.reason,
        }


class WalkerAdHocAvailability(db.Model):
    """One-off available days outside a walker's default weekly schedule."""
    __tablename__ = 'walker_adhoc_availability'
    __table_args__ = (
        db.UniqueConstraint('walker_id', 'date', 'slot', name='uq_walker_adhoc_date_slot'),
    )

    id = db.Column(db.Integer, primary_key=True)
    walker_id = db.Column(db.Integer, db.ForeignKey('walkers.id'), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False)
    slot = db.Column(db.Enum('Morning', 'Afternoon', name='schedule_slot', create_type=False), nullable=False)
    reason = db.Column(db.String(200), nullable=True)
    # Indexed: the activity feed filters this source by created_at month range (F4).
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)

    walker = db.relationship('Walker', backref='adhoc_availabilities')
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def to_dict(self):
        return {
            'id': self.id,
            'walker_id': self.walker_id,
            'date': self.date.isoformat() if self.date else None,
            'slot': self.slot,
            'reason': self.reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Notification(db.Model):
    """Persistent notification system — stores cross-user events with read audit trail."""
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # None = system

    # Type drives icon/colour in UI. Keep as string for flexibility.
    # Expected values (see NOTIFICATION_META in app/utils/notifications.py):
    #   booking_confirmed, booking_cancelled, booking_requested, same_day_request,
    #   walker_assigned, walker_availability, system
    notification_type = db.Column(db.String(50), nullable=False, index=True)

    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(500), nullable=True)   # URL to navigate to on click

    read_at = db.Column(db.DateTime, nullable=True, index=True)  # None = unread
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    recipient = db.relationship('User', foreign_keys=[recipient_id],
                                backref=db.backref('notifications', lazy='dynamic',
                                                   order_by='Notification.created_at.desc()'))
    sender = db.relationship('User', foreign_keys=[sender_id])

    @property
    def is_unread(self):
        return self.read_at is None

    def mark_read(self):
        if self.read_at is None:
            self.read_at = datetime.now(timezone.utc)

    def to_dict(self):
        return {
            'id': self.id,
            'recipient_id': self.recipient_id,
            'sender_id': self.sender_id,
            'notification_type': self.notification_type,
            'title': self.title,
            'body': self.body,
            'link': self.link,
            'read_at': self.read_at.isoformat() if self.read_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_unread': self.is_unread,
        }

    def __repr__(self):
        return f'<Notification {self.id} → user:{self.recipient_id} [{self.notification_type}]>'


class PricingConfig(db.Model):
    """Pricing tiers for walk/drop-in revenue calculation.

    Multiple rows allowed; the row with the highest effective_from that is
    still <= the booking date is used.  This means historical revenue figures
    remain accurate when prices change.
    """
    __tablename__ = 'pricing_configs'

    id                   = db.Column(db.Integer, primary_key=True)
    price_per_walk       = db.Column(db.Numeric(8, 2), nullable=False)
    double_slot_discount = db.Column(db.Numeric(8, 2), nullable=False, default=0)
    weekly_discount      = db.Column(db.Numeric(8, 2), nullable=False, default=0)
    price_per_drop_in    = db.Column(db.Numeric(8, 2), nullable=False, default=5)
    effective_from       = db.Column(db.Date, nullable=False, unique=True)
    created_at           = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return (f'<PricingConfig £{self.price_per_walk}/walk '
                f'£{self.price_per_drop_in}/drop-in '
                f'(−£{self.double_slot_discount} double, −£{self.weekly_discount} weekly) '
                f'from {self.effective_from}>')

    def to_dict(self):
        return {
            'id':                   self.id,
            'price_per_walk':       float(self.price_per_walk),
            'double_slot_discount': float(self.double_slot_discount),
            'weekly_discount':      float(self.weekly_discount),
            'price_per_drop_in':    float(self.price_per_drop_in),
            'effective_from':       self.effective_from.isoformat(),
        }


class PushSubscription(db.Model):
    """Stores Web Push subscriptions for PWA users.

    One row per browser/device per user. Identified by endpoint URL (unique
    per browser). Replaced on re-subscribe (upsert on endpoint).
    """
    __tablename__ = 'push_subscriptions'

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    endpoint    = db.Column(db.Text, nullable=False, unique=True)
    p256dh      = db.Column(db.Text, nullable=False)   # client public key
    auth        = db.Column(db.Text, nullable=False)   # auth secret
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref=db.backref('push_subscriptions', lazy=True))


class Closure(db.Model):
    """A date on which DogBoxx is closed. New bookings are rejected and existing
    active bookings are cancelled (with notifications) when a closure is created."""
    __tablename__ = 'closures'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True, index=True)
    reason = db.Column(db.String(200), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    # Indexed: the activity feed filters this source by created_at month range (F4).
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])


class Broadcast(db.Model):
    """Admin-authored one-shot message to all clients booked on a given date/slot.

    Recipients are computed at send time from confirmed bookings (primary +
    secondary co-owners). Delivered via in-app notification, email, or both.
    Rows are kept for audit / "past broadcasts" history.
    """
    __tablename__ = 'broadcasts'

    # scope_slot values: 'all' (whole day), 'morning', 'afternoon'.
    # 'morning' matches booking slots Morning + Half Day AM + Full Day;
    # 'afternoon' matches Afternoon + Half Day PM + Full Day.
    SCOPE_ALL = 'all'
    SCOPE_MORNING = 'morning'
    SCOPE_AFTERNOON = 'afternoon'
    VALID_SCOPES = (SCOPE_ALL, SCOPE_MORNING, SCOPE_AFTERNOON)

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    sent_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                        nullable=False, index=True)

    scope_date = db.Column(db.Date, nullable=False, index=True)
    scope_slot = db.Column(db.String(20), nullable=False)

    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)

    bell_sent = db.Column(db.Boolean, nullable=False, default=False)
    email_sent = db.Column(db.Boolean, nullable=False, default=False)

    recipient_count = db.Column(db.Integer, nullable=False, default=0)

    sender = db.relationship('User', foreign_keys=[sender_id])

    def __repr__(self):
        return (f'<Broadcast {self.id} {self.scope_date} {self.scope_slot} '
                f'→ {self.recipient_count} recipients>')


class DailyMessage(db.Model):
    """A message from the business owner to the walker team, shown at the top
    of the pickup list for a given date. One message per day (UNIQUE on date).
    """
    __tablename__ = 'daily_messages'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True, index=True)
    content = db.Column(db.Text, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    created_by = db.relationship('User', foreign_keys=[created_by_id])
