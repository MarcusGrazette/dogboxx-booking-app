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
        print("\n✓ Seed complete!")


if __name__ == '__main__':
    seed()
