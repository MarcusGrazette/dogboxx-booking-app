# Solution 1: Rename the database column to avoid conflict
from . import db
from datetime import datetime, timezone
import re

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
    hashed_password = db.Column(db.String(128), nullable=False)

    # Flask-Login required methods
    def get_id(self):
        """Return the user ID as a string"""
        return str(self.id)
    
    def is_authenticated(self):
        """Return True if user is authenticated"""
        return True
    
    def is_active(self):
        """Return True if user account is active"""
        return self.active  # Now references the renamed column
    
    def is_anonymous(self):
        """Return True if user is anonymous (not logged in)"""
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
            'is_active': self.active  # Return the active status
            }

    @property
    def full_name(self):
        return f"{self.firstname} {self.lastname}"

    @staticmethod
    def validate_password(password):
        """Validate password strength"""
        if len(password) < 8:
            return False, "Password must be at least 8 characters long"
        if not re.search(r'[A-Z]', password):
            return False, "Password must contain at least one uppercase letter"
        if not re.search(r'[a-z]', password):
            return False, "Password must contain at least one lowercase letter"
        if not re.search(r'\d', password):
            return False, "Password must contain at least one number"
        return True, "Valid password"

class Client(db.Model):
    __tablename__ = 'clients'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    
    # Google Places data (primary address storage)
    place_id = db.Column(db.String(200), nullable=True)  # Google Place ID for future reference
    formatted_address = db.Column(db.String(300), nullable=True)  # Full formatted address from Google
    display_name = db.Column(db.String(200), nullable=True)  # Display name from Google Places
    
    # Coordinates (for distance calculations and mapping)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    
    # Legacy address fields (keep for backward compatibility and manual entry fallback)
    street_address = db.Column(db.String(200), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(50), nullable=True)
    postal_code = db.Column(db.String(20), nullable=True)
    country = db.Column(db.String(50), nullable=True)
    
    # Onboarding completion tracking
    onboarding_completed = db.Column(db.Boolean, default=False, nullable=False)
    onboarding_completed_at = db.Column(db.DateTime, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), 
                          onupdate=lambda: datetime.now(timezone.utc))

    # Relationship
    user = db.relationship('User', backref=db.backref('client', uselist=False))

    def __repr__(self):
        return f'<Client {self.user.full_name if self.user else self.id}>'

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            # Google Places data
            'place_id': self.place_id,
            'formatted_address': self.formatted_address,
            'display_name': self.display_name,
            'latitude': self.latitude,
            'longitude': self.longitude,
            # Legacy address fields
            'street_address': self.street_address,
            'city': self.city,
            'state': self.state,
            'postal_code': self.postal_code,
            'country': self.country,
            # Onboarding
            'onboarding_completed': self.onboarding_completed,
            'onboarding_completed_at': self.onboarding_completed_at.isoformat() if self.onboarding_completed_at else None,
            # Timestamps
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }