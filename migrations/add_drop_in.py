"""Migration: add drop-in support.

Changes:
  1. walkers.does_drop_ins      BOOLEAN NOT NULL DEFAULT FALSE
  2. pricing_configs.price_per_drop_in  NUMERIC(8,2) NOT NULL DEFAULT 5.00
  3. Insert 'drop-in' ServiceType row (idempotent)
  4. Set does_drop_ins=TRUE for the walker whose user email is 'lydia@...' — UPDATE
     the email below to match the real account before running.

Run from the project root:
    python migrations/add_drop_in.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from sqlalchemy import text

# ── SET THIS to Lydia's actual account email before running on production ─────
# Dev seed uses testwalker@dogboxx.org — update for prod before running there.
LYDIA_EMAIL = 'lydia@dogboxx.org'
# ─────────────────────────────────────────────────────────────────────────────


def run():
    app = create_app()
    with app.app_context():

        # 1. walkers.does_drop_ins
        try:
            db.session.execute(text(
                "ALTER TABLE walkers ADD COLUMN does_drop_ins BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            print("✓ Added walkers.does_drop_ins")
        except Exception as e:
            if 'already exists' in str(e).lower() or 'duplicate' in str(e).lower():
                print("  walkers.does_drop_ins already exists — skipping")
            else:
                raise
        db.session.commit()

        # 2. pricing_configs.price_per_drop_in
        try:
            db.session.execute(text(
                "ALTER TABLE pricing_configs ADD COLUMN price_per_drop_in NUMERIC(8,2) NOT NULL DEFAULT 5.00"
            ))
            print("✓ Added pricing_configs.price_per_drop_in (default £5.00)")
        except Exception as e:
            if 'already exists' in str(e).lower() or 'duplicate' in str(e).lower():
                print("  pricing_configs.price_per_drop_in already exists — skipping")
            else:
                raise
        db.session.commit()

        # 3. Insert drop-in ServiceType (idempotent)
        from app.models import ServiceType
        if not ServiceType.query.filter_by(slug='drop-in').first():
            db.session.add(ServiceType(
                name='Drop In',
                slug='drop-in',
                description='Short comfort-break visit at home. Morning or afternoon slots.',
                capacity_model='walker_assigned',
                slot_type='morning_afternoon',
                requires_walker=True,
                requires_compatibility_check=False,
                default_max_capacity=6,
                active=True,
                settings={
                    'cancellation_notice_days': 5,
                    'max_booking_days_ahead': 90,
                },
            ))
            db.session.commit()
            print("✓ Inserted drop-in ServiceType")
        else:
            print("  drop-in ServiceType already exists — skipping")

        # 4. Enable does_drop_ins for Lydia
        from app.models import User, Walker
        lydia_user = User.query.filter_by(email=LYDIA_EMAIL).first()
        if lydia_user:
            lydia_walker = Walker.query.filter_by(user_id=lydia_user.id).first()
            if lydia_walker:
                lydia_walker.does_drop_ins = True
                db.session.commit()
                print(f"✓ Set does_drop_ins=True for {LYDIA_EMAIL}")
            else:
                print(f"  No walker record found for {LYDIA_EMAIL} — skipping does_drop_ins flag")
        else:
            print(f"  User not found: {LYDIA_EMAIL} — skipping does_drop_ins flag")
            print("  → Update LYDIA_EMAIL in this script and re-run, or set manually in DB.")

        print("\n✓ Migration complete.")


if __name__ == '__main__':
    run()
