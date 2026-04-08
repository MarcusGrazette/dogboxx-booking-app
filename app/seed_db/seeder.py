#!/usr/bin/env python3
"""
Database seeder script for Flask dog walking app.
Populates the database with test data from JSON files.
"""

import json
import sys
import os
from datetime import datetime, timezone, date
from werkzeug.security import generate_password_hash

# Add the project root to the path so we can import our Flask app when running this
# script directly. The seeder lives at <project>/app/seed_db/seeder.py, so we need the
# directory above `app` on sys.path (the project root), not `app` itself.
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    # Insert at front so local packages take precedence
    sys.path.insert(0, project_root)

from app import create_app, db
from app.models import User, Client, Dog, Walker, Booking, ServiceType, DogOwner


def load_json_data(filename):
    """Load data from a JSON file."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: {filename} not found.")
        return None
    except json.JSONDecodeError as e:
        print(f"Error parsing {filename}: {e}")
        return None


def seed_users(users_data):
    """Create users from JSON data."""
    print("Creating users...")
    created_users = []
    
    for user_data in users_data:
        # Hash the password
        hashed_password = generate_password_hash(user_data.get('password', 'defaultpassword'))
        
        user = User(
            firstname=user_data['firstname'],
            lastname=user_data['lastname'],
            email=user_data['email'],
            role=user_data.get('role', 'client'),
            is_admin=user_data.get('is_admin', False),
            hashed_password=hashed_password,
            active=user_data.get('active', True)
        )
        
        db.session.add(user)
        created_users.append(user)
        print(f"  Created user: {user.email} ({user.role})")
    
    return created_users


def seed_clients(clients_data, users):
    """Create clients from JSON data."""
    print("Creating clients...")
    created_clients = []
    
    # Create a mapping of email to user for easy lookup
    user_map = {user.email: user for user in users}
    
    for client_data in clients_data:
        user_email = client_data.get('user_email')
        user = user_map.get(user_email)
        
        if not user:
            print(f"  Warning: User with email {user_email} not found, skipping client")
            continue
            
        client = Client(
            user_id=user.id,
            place_id=client_data.get('place_id'),
            formatted_address=client_data.get('formatted_address'),
            display_name=client_data.get('display_name'),
            latitude=client_data.get('latitude'),
            longitude=client_data.get('longitude'),
            street_address=client_data.get('street_address'),
            city=client_data.get('city'),
            state=client_data.get('state'),
            postal_code=client_data.get('postal_code'),
            country=client_data.get('country', 'USA'),
            onboarding_completed=client_data.get('onboarding_completed', True),
            pickup_instructions=client_data.get('pickup_instructions')
        )
        
        # Set onboarding completion timestamp if completed
        if client.onboarding_completed:
            client.onboarding_completed_at = datetime.now(timezone.utc)
        
        db.session.add(client)
        created_clients.append(client)
        print(f"  Created client for: {user.email}")
    
    return created_clients


def seed_dogs(dogs_data, users):
    """Create dogs from JSON data."""
    print("Creating dogs...")
    created_dogs = []
    
    # Create a mapping of email to user for easy lookup
    user_map = {user.email: user for user in users}
    
    for dog_data in dogs_data:
        user_email = dog_data.get('user_email')
        user = user_map.get(user_email)
        
        if not user:
            print(f"  Warning: User with email {user_email} not found, skipping dog {dog_data.get('name')}")
            continue
            
        dog = Dog(
            name=dog_data['name'],
            date_of_birth=datetime.strptime(dog_data['date_of_birth'], '%Y-%m-%d').date() if dog_data.get('date_of_birth') else None,
            gender=dog_data.get('gender'),
            breed=dog_data.get('breed'),
            allergies=dog_data.get('allergies'),
            other_info=dog_data.get('other_info'),
            pic=dog_data.get('pic')
        )
        
        db.session.add(dog)
        db.session.flush()  # Get dog.id
        
        # Create DogOwner relationship (user is primary owner)
        dog_owner = DogOwner(
            dog_id=dog.id,
            user_id=user.id,
            role='primary'
        )
        db.session.add(dog_owner)
        
        created_dogs.append(dog)
        print(f"  Created dog: {dog.name} (owner: {user.email})")
    
    return created_dogs


def seed_walkers(walkers_data, users):
    """Create walkers from JSON data. Associates each walker with a User via user_email.

    If a referenced user is not found, a new User record will be created for the walker
    using the provided firstname/lastname and email (or a generated email when missing).
    """
    print("Creating walkers...")
    created_walkers = []

    # Map existing users by email for quick lookup (users were created earlier)
    user_map = {user.email: user for user in users}

    for walker_data in walkers_data:
        firstname = walker_data.get('firstname')
        lastname = walker_data.get('lastname')
        user_email = walker_data.get('user_email')

        user = None
        if user_email:
            user = user_map.get(user_email)

        # If user not found, create one (assume role 'walker')
        if not user:
            # Generate an email if none provided
            if not user_email:
                safe_first = (firstname or 'walker').strip().lower()
                safe_last = (lastname or 'user').strip().lower()
                user_email = f"{safe_first}.{safe_last}@example.com"

            hashed_password = generate_password_hash(walker_data.get('password', 'defaultpassword'))
            user = User(
                firstname=firstname or 'Walker',
                lastname=lastname or 'User',
                email=user_email,
                role='walker',
                hashed_password=hashed_password,
                active=walker_data.get('active', True)
            )
            db.session.add(user)
            # Add to local lists so later lookups can find it before commit
            users.append(user)
            user_map[user.email] = user
            print(f"  Created user account for walker: {user.email}")

        # Create walker record linked to the user
        walker = Walker(
            user_id=user.id,
        )

        db.session.add(walker)
        created_walkers.append(walker)
        print(f"  Created walker: {walker.firstname} {walker.lastname} (user: {user.email})")

    return created_walkers


def seed_bookings(bookings_data, users, dogs, walkers):
    """Create bookings from JSON data."""
    print("Creating bookings...")
    
    # Create mappings for easy lookup
    user_map = {user.email: user for user in users}
    # Build dog_map using DogOwner relationship (dogs no longer have user_id)
    dog_map = {}
    for dog in dogs:
        # Find primary owner via DogOwner join table
        dog_owner = DogOwner.query.filter_by(dog_id=dog.id, role='primary').first()
        if dog_owner:
            owner = User.query.get(dog_owner.user_id)
            if owner:
                dog_map[f"{dog.name.lower()}_{owner.email}"] = dog

    walker_map = {f"{walker.firstname.lower()}_{walker.lastname.lower()}": walker for walker in walkers}
    
    # Look up service type once before the loop
    group_walk = ServiceType.query.filter_by(slug='group-walk').first()
    if not group_walk:
        print("  Error: 'group-walk' service type not found. Run seed_service_types() first.")
        return

    for booking_data in bookings_data:
        user_email = booking_data.get('user_email')
        dog_name = booking_data.get('dog_name', '').lower()
        walker_name = booking_data.get('walker_name', '').lower().replace(' ', '_')
        
        user = user_map.get(user_email)
        dog = dog_map.get(f"{dog_name}_{user_email}")
        walker = walker_map.get(walker_name) if walker_name else None
        
        if not user:
            print(f"  Warning: User {user_email} not found, skipping booking")
            continue
            
        if not dog:
            print(f"  Warning: Dog {dog_name} for user {user_email} not found, skipping booking")
            continue
        
        # Parse date string to date object
        booking_date = datetime.strptime(booking_data['date'], '%Y-%m-%d').date()
        
        booking = Booking(
            user_id=user.id,
            dog_id=dog.id,
            service_type_id=group_walk.id,
            date=booking_date,
            slot=booking_data.get('slot', 'Morning'),
            walker_id=walker.id if walker else None,
            status=booking_data.get('status', 'requested')
        )
        
        db.session.add(booking)
        print(f"  Created booking: {dog.name} on {booking_date} ({booking.slot})")


def seed_service_types():
    """Create default service types. Idempotent — adds missing types, skips existing ones."""
    print("Creating service types...")

    service_types = [
        {
            'name': 'Group Walk',
            'slug': 'group-walk',
            'description': 'Standard group dog walking service',
            'capacity_model': 'walker_assigned',
            'slot_type': 'morning_afternoon',
            'requires_walker': True,
            'requires_compatibility_check': True,
            'default_max_capacity': 6,
            'active': True,
            'settings': {'cancellation_notice_days': 5},
        },
        {
            'name': 'Drop In',
            'slug': 'drop-in',
            'description': 'Short comfort-break visit at home. Morning or afternoon slots.',
            'capacity_model': 'walker_assigned',
            'slot_type': 'morning_afternoon',
            'requires_walker': True,
            'requires_compatibility_check': False,
            'default_max_capacity': 6,
            'active': True,
            'settings': {'cancellation_notice_days': 5},
        },
        {
            'name': 'Doggy Day Care',
            'slug': 'day-care',
            'description': 'Full day care service for dogs',
            'capacity_model': 'facility_capacity',
            'slot_type': 'full_half_day',
            'requires_walker': False,
            'requires_compatibility_check': False,
            'default_max_capacity': 20,
            'active': True,
            'settings': {},
        },
    ]

    for service_data in service_types:
        if ServiceType.query.filter_by(slug=service_data['slug']).first():
            print(f"  Service type '{service_data['slug']}' already exists — skipping")
            continue
        service = ServiceType(**service_data)
        db.session.add(service)
        print(f"  Created service type: {service.name}")


def main():
    """Main seeder function."""
    print("Starting database seeding...")
    
    # Create Flask app context
    app = create_app()
    
    with app.app_context():
        # Load all JSON data files
        users_data = load_json_data('seed_data/users.json')
        clients_data = load_json_data('seed_data/clients.json')
        dogs_data = load_json_data('seed_data/dogs.json')
        walkers_data = load_json_data('seed_data/walkers.json')
        bookings_data = load_json_data('seed_data/bookings.json')
        
        if not all([users_data, clients_data, dogs_data, walkers_data, bookings_data]):
            print("Error: One or more data files could not be loaded. Exiting.")
            return
        
        try:
            # Create service types first
            seed_service_types()
            db.session.commit()
            
            # Create all records
            users = seed_users(users_data)
            db.session.commit()
            
            clients = seed_clients(clients_data, users)
            db.session.commit()
            
            dogs = seed_dogs(dogs_data, users)
            db.session.commit()
            
            walkers = seed_walkers(walkers_data, users)
            db.session.commit()
            
            seed_bookings(bookings_data, users, dogs, walkers)
            db.session.commit()
            
            print(f"\nSeeding completed successfully!")
            print(f"Created: {len(users)} users, {len(clients)} clients, {len(dogs)} dogs, {len(walkers)} walkers")
            
        except Exception as e:
            print(f"Error during seeding: {e}")
            db.session.rollback()
            raise


if __name__ == '__main__':
    main()