"""
seed_may_demo.py
================
Seeds demo bookings for 18–29 May 2026 (weekdays only).

  Walk bookings : Varies per slot — light days ~6–10, busy days up to the
                  6-per-walker maximum (5 walkers × 6 = 30). A few slots
                  hit near-capacity and spill into waitlisted.
  Drop-in bookings: 2–6 per day, slot chosen randomly each day.

Run with:  python seed_may_demo.py
       or:  flask shell < seed_may_demo.py
"""

import random
from app import create_app, db
from app.models import Booking, Dog, DogOwner, User, Walker, ServiceType
from datetime import date, datetime, timezone

SEED = 7   # fixed seed so re-runs produce the same data

app = create_app()

with app.app_context():

    rng = random.Random(SEED)

    # ── Collect fixtures ──────────────────────────────────────────────────────

    walk_svc   = ServiceType.query.filter_by(slug='group-walk', active=True).first()
    dropin_svc = ServiceType.query.filter_by(slug='drop-in',    active=True).first()
    assert walk_svc,   "group-walk service type not found"
    assert dropin_svc, "drop-in service type not found"

    rows = (
        Dog.query
        .join(DogOwner, DogOwner.dog_id == Dog.id)
        .join(User,     User.id == DogOwner.user_id)
        .filter(DogOwner.role == 'primary')
        .add_columns(User.id.label('user_id'))
        .order_by(Dog.id)
        .all()
    )
    dogs = [(row[0], row[1]) for row in rows]   # [(Dog, user_id), ...]

    walkers = Walker.query.order_by(Walker.id).all()
    n_walkers  = len(walkers)
    max_per_slot = n_walkers * 6   # hard capacity ceiling

    assert len(dogs)   >= 10, f"Need at least 10 dogs, found {len(dogs)}"
    assert n_walkers   >= 1,  "No walkers found"

    print(f"Dogs: {len(dogs)}, Walkers: {n_walkers}, Max per slot: {max_per_slot}")

    # ── Date range: weekdays 18–29 May 2026 ──────────────────────────────────

    all_days = [date(2026, 5, d) for d in range(18, 30)]
    weekdays = [d for d in all_days if d.weekday() < 5]
    print(f"Weekdays: {[str(d) for d in weekdays]}")

    # ── Clear existing bookings in this window ────────────────────────────────

    deleted = (
        Booking.query
        .filter(
            Booking.date >= date(2026, 5, 18),
            Booking.date <= date(2026, 5, 29),
        )
        .delete(synchronize_session=False)
    )
    db.session.flush()
    print(f"Cleared {deleted} existing bookings")

    # ── Per-day booking counts (hand-crafted for realism, jittered by rng) ───
    # (morning_count, afternoon_count) — counts above max_per_slot get waitlisted
    day_plans = [
        # Mon 18: busy start to the week
        (22, 18),
        # Tue 19: moderate
        (14, 11),
        # Wed 20: quieter mid-week
        (8,  10),
        # Thu 21: picks up again
        (20, 24),
        # Fri 22: heavy Friday
        (26, 28),
        # Mon 25: bank holiday vibes — lighter
        (7,  6),
        # Tue 26: back to normal
        (15, 13),
        # Wed 27: busy
        (24, 20),
        # Thu 28: moderate
        (12, 16),
        # Fri 29: big finish — some slots hit capacity
        (29, 27),
    ]

    # Apply a small ±2 jitter per slot so re-runs still feel hand-tuned
    # but numbers aren't suspiciously round.
    jittered = []
    for am, pm in day_plans:
        am = max(4, min(len(dogs), am + rng.randint(-2, 2)))
        pm = max(4, min(len(dogs), pm + rng.randint(-2, 2)))
        jittered.append((am, pm))

    # ── Split dog pools ───────────────────────────────────────────────────────
    # Reserve the last 5 dogs exclusively for drop-ins so busy walk days
    # (which may sample nearly all 29 dogs) never leave the drop-in pool empty.
    walk_pool   = dogs[:-5]   # first 24 dogs for walks (max 24 per slot)
    dropin_dogs = dogs[-5:]   # last 5 dogs always available for drop-ins

    # ── Drop-in plan per day ──────────────────────────────────────────────────
    # 2–5 bookings (capped to pool size), slot chosen randomly.
    dropin_plan = []
    for i in range(len(weekdays)):
        n    = rng.randint(2, min(5, len(dropin_dogs)))
        slot = rng.choice(['Morning', 'Afternoon'])
        dropin_plan.append((n, slot))

    # ── Seed ─────────────────────────────────────────────────────────────────

    now = datetime.now(timezone.utc)
    walk_confirmed = walk_waitlisted = drop_in_created = 0

    for day_idx, day in enumerate(weekdays):
        am_count, pm_count = jittered[day_idx]
        n_dropins, dropin_slot = dropin_plan[day_idx]

        # Sample walk dogs from walk_pool only; dropin_dogs pool is fully separate
        # so there's no (dog_id, date, slot) unique constraint conflict.
        walk_am = rng.sample(walk_pool, min(am_count, len(walk_pool)))
        walk_pm = rng.sample(walk_pool, min(pm_count, len(walk_pool)))
        dropin_sample = rng.sample(dropin_dogs, min(n_dropins, len(dropin_dogs)))

        for slot, selected in [('Morning', walk_am), ('Afternoon', walk_pm)]:
            walker_order = {}   # walker_id → pickup count for this slot, reset each slot
            for i, (dog, user_id) in enumerate(selected):
                if i < max_per_slot:
                    walker = walkers[i % n_walkers]
                    walker_order[walker.id] = walker_order.get(walker.id, 0) + 1
                    db.session.add(Booking(
                        user_id         = user_id,
                        dog_id          = dog.id,
                        service_type_id = walk_svc.id,
                        date            = day,
                        slot            = slot,
                        status          = 'confirmed',
                        walker_id       = walker.id,
                        confirmed_at    = now,
                        pickup_order    = walker_order[walker.id],
                    ))
                    walk_confirmed += 1
                else:
                    db.session.add(Booking(
                        user_id         = user_id,
                        dog_id          = dog.id,
                        service_type_id = walk_svc.id,
                        date            = day,
                        slot            = slot,
                        status          = 'waitlisted',
                    ))
                    walk_waitlisted += 1

        for dog, user_id in dropin_sample:
            db.session.add(Booking(
                user_id         = user_id,
                dog_id          = dog.id,
                service_type_id = dropin_svc.id,
                date            = day,
                slot            = dropin_slot,
                status          = 'requested',
            ))
            drop_in_created += 1

    db.session.commit()

    print(f"\n✓ Done!")
    print(f"  Walk confirmed  : {walk_confirmed}")
    print(f"  Walk waitlisted : {walk_waitlisted}")
    print(f"  Drop-ins created: {drop_in_created}")
    print(f"\nPer-day summary:")
    for day_idx, day in enumerate(weekdays):
        am_c  = Booking.query.filter_by(date=day, slot='Morning',   service_type_id=walk_svc.id).count()
        pm_c  = Booking.query.filter_by(date=day, slot='Afternoon', service_type_id=walk_svc.id).count()
        di    = Booking.query.filter_by(date=day, service_type_id=dropin_svc.id).count()
        _, ds = dropin_plan[day_idx]
        print(f"  {day.strftime('%a %-d %b')}: AM={am_c:2d}  PM={pm_c:2d}  drop-ins={di} ({ds})")
