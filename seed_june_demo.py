"""
seed_june_demo.py
=================
Seeds demo bookings for 8-12 June 2026 (one Mon-Fri week) with a
mix of normal and at-capacity days so the dashboard's waitlisted
visualisation can be tested.

  Walk bookings only — drop-ins not seeded (the May demo already
  covers drop-ins). Capacity = active_walker_count * 6 (per
  models.WalkerSchedule). On capacity-busting days the overflow
  becomes 'waitlisted'.

Run with:  python seed_june_demo.py
"""

import random
from datetime import date, datetime, timezone

from app import create_app, db
from app.models import Booking, Dog, DogOwner, ServiceType, User, Walker

SEED = 11                              # fixed → re-runs reproduce the same shape
WINDOW_START = date(2026, 6, 8)
WINDOW_END   = date(2026, 6, 12)

app = create_app()

with app.app_context():

    rng = random.Random(SEED)

    # ── Fixtures ─────────────────────────────────────────────────────────────
    walk_svc = ServiceType.query.filter_by(slug='group-walk', active=True).first()
    assert walk_svc, "group-walk service type not found"

    rows = (
        Dog.query
        .join(DogOwner, DogOwner.dog_id == Dog.id)
        .join(User,     User.id == DogOwner.user_id)
        .filter(DogOwner.role == 'primary')
        .add_columns(User.id.label('user_id'))
        .order_by(Dog.id)
        .all()
    )
    dogs = [(row[0], row[1]) for row in rows]

    walkers      = Walker.query.join(User).filter(User.active == True).order_by(Walker.id).all()
    n_walkers    = len(walkers)
    max_per_slot = n_walkers * 6

    print(f"Dogs: {len(dogs)}, Walkers: {n_walkers}, Capacity per slot: {max_per_slot}")

    # ── Date range ───────────────────────────────────────────────────────────
    weekdays = []
    d = WINDOW_START
    while d <= WINDOW_END:
        if d.weekday() < 5:
            weekdays.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    print(f"Weekdays: {[str(d) for d in weekdays]}")

    # ── Clear existing bookings in the window ────────────────────────────────
    deleted = (
        Booking.query
        .filter(Booking.date >= WINDOW_START, Booking.date <= WINDOW_END)
        .delete(synchronize_session=False)
    )
    db.session.flush()
    print(f"Cleared {deleted} existing bookings in {WINDOW_START}..{WINDOW_END}")

    # ── Per-day plan: (am_count, pm_count) ───────────────────────────────────
    # Anything above max_per_slot (30) spills to 'waitlisted'.
    day_plans = [
        # Mon  8 Jun — AM hits capacity hard
        (33, 22),
        # Tue  9 Jun — both near capacity, no spill
        (26, 28),
        # Wed 10 Jun — small spill on both slots
        (32, 31),
        # Thu 11 Jun — quiet
        (18, 21),
        # Fri 12 Jun — both slots over capacity
        (33, 33),
    ]
    assert len(day_plans) == len(weekdays), \
        f"Day plan ({len(day_plans)}) doesn't match weekday count ({len(weekdays)})"

    # ── Seed ─────────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    confirmed = waitlisted = 0

    for day, (am_count, pm_count) in zip(weekdays, day_plans):
        for slot_name, count in [('Morning', am_count), ('Afternoon', pm_count)]:
            selected = rng.sample(dogs, min(count, len(dogs)))
            walker_order = {}
            for i, (dog, user_id) in enumerate(selected):
                if i < max_per_slot:
                    walker = walkers[i % n_walkers]
                    walker_order[walker.id] = walker_order.get(walker.id, 0) + 1
                    db.session.add(Booking(
                        user_id         = user_id,
                        dog_id          = dog.id,
                        service_type_id = walk_svc.id,
                        date            = day,
                        slot            = slot_name,
                        status          = 'confirmed',
                        walker_id       = walker.id,
                        confirmed_at    = now,
                        pickup_order    = walker_order[walker.id],
                    ))
                    confirmed += 1
                else:
                    db.session.add(Booking(
                        user_id         = user_id,
                        dog_id          = dog.id,
                        service_type_id = walk_svc.id,
                        date            = day,
                        slot            = slot_name,
                        status          = 'waitlisted',
                    ))
                    waitlisted += 1

    db.session.commit()

    print(f"\n✓ Seeded {confirmed} confirmed + {waitlisted} waitlisted walk bookings\n")
    print("Per-day summary:")
    for day in weekdays:
        am_conf = Booking.query.filter_by(date=day, slot='Morning',   status='confirmed').count()
        am_wl   = Booking.query.filter_by(date=day, slot='Morning',   status='waitlisted').count()
        pm_conf = Booking.query.filter_by(date=day, slot='Afternoon', status='confirmed').count()
        pm_wl   = Booking.query.filter_by(date=day, slot='Afternoon', status='waitlisted').count()
        print(f"  {day.strftime('%a %-d %b')}: "
              f"AM={am_conf:2d} confirmed + {am_wl} waitlisted   "
              f"PM={pm_conf:2d} confirmed + {pm_wl} waitlisted")
