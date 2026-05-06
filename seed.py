"""Seed the database with initial data (service types, admin account)."""

from app import create_app, db
from app.models import ServiceType, User, Walker
from werkzeug.security import generate_password_hash


def seed():
    app = create_app()
    with app.app_context():
        # Create all tables
        db.create_all()

        # Service Types
        if not ServiceType.query.filter_by(slug='group-walk').first():
            walk = ServiceType(
                name='Group Walk',
                slug='group-walk',
                description='Group dog walk with a professional walker. Morning or afternoon slots.',
                capacity_model='walker_assigned',
                slot_type='morning_afternoon',
                requires_walker=True,
                requires_compatibility_check=True,
                default_max_capacity=6,  # per walker
                active=True,
                settings={
                    'cancellation_notice_days': 5,
                    'max_booking_days_ahead': 90,
                },
            )
            db.session.add(walk)
            print("✓ Created service type: Group Walk")

        if not ServiceType.query.filter_by(slug='day-care').first():
            daycare = ServiceType(
                name='Doggy Day Care',
                slug='day-care',
                description='Full or half day care at our facility.',
                capacity_model='facility_capacity',
                slot_type='full_half_day',
                requires_walker=False,
                requires_compatibility_check=False,
                default_max_capacity=20,  # facility-wide
                active=True,
                settings={
                    'cancellation_notice_days': 5,
                    'max_booking_days_ahead': 90,
                },
            )
            db.session.add(daycare)
            print("✓ Created service type: Doggy Day Care")

        if not ServiceType.query.filter_by(slug='drop-in').first():
            drop_in = ServiceType(
                name='Drop In',
                slug='drop-in',
                description='Short comfort-break visit at home. Morning or afternoon slots.',
                capacity_model='walker_assigned',
                slot_type='morning_afternoon',
                requires_walker=True,
                requires_compatibility_check=False,
                default_max_capacity=6,  # per walker
                active=True,
                settings={
                    'cancellation_notice_days': 5,
                    'max_booking_days_ahead': 90,
                },
            )
            db.session.add(drop_in)
            print("✓ Created service type: Drop In")

        # Ensure Lydia's walker record has does_drop_ins=True (idempotent)
        lydia_user = User.query.filter_by(email='lydia@dogboxx.org').first()
        if lydia_user:
            lydia_walker = Walker.query.filter_by(user_id=lydia_user.id).first()
            if lydia_walker and not lydia_walker.does_drop_ins:
                lydia_walker.does_drop_ins = True
                db.session.commit()
                print("✓ Enabled does_drop_ins for Lydia's walker record")

        # Owner / admin account
        if not User.query.filter_by(email='lydia@dogboxx.org').first():
            admin = User(
                firstname='Lydia',
                lastname='Maxwell',
                email='lydia@dogboxx.org',
                role='walker',
                is_admin=True,
                is_super_admin=True,
                hashed_password=generate_password_hash('changeme123!'),
                must_change_password=True,
                active=True,
            )
            db.session.add(admin)
            print("✓ Created owner account: lydia@dogboxx.org (password: changeme123!)")

        db.session.commit()
        print("\n✓ Base seed complete — loading test data from seed_data/...")

        from app.seed_db.seeder import (
            load_json_data, seed_users, seed_clients,
            seed_dogs, seed_walkers, seed_bookings,
        )

        users_data = load_json_data('seed_data/users.json')
        clients_data = load_json_data('seed_data/clients.json')
        dogs_data = load_json_data('seed_data/dogs.json')
        walkers_data = load_json_data('seed_data/walkers.json')
        bookings_data = load_json_data('seed_data/bookings.json')

        if not all([users_data, clients_data, dogs_data, walkers_data, bookings_data]):
            print("⚠ One or more seed_data files missing — skipping test data.")
            return

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

        print(f"\n✓ Seed complete! Created {len(users)} users, {len(clients)} clients, "
              f"{len(dogs)} dogs, {len(walkers)} walkers.")


if __name__ == '__main__':
    seed()
