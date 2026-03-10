"""
seed_demo_bookings.py
=====================
Seeds demo bookings across the next 3 weeks for the Dogboxx demo.

Strategy:
  - Adds walker unavailability on 4 days so only 1 walker covers those mornings
    (capacity drops from 18 → 6, making waitlisting achievable with 13 dogs)
  - Spreads 'requested' bookings across all weekdays in weeks 1-3
  - Forces 'waitlisted' bookings on constrained days
  - Promotes some week-1 bookings to 'confirmed' so the dashboard looks live

Run with:  flask shell < seed_demo_bookings.py
       or:  python seed_demo_bookings.py
"""

from app import create_app, db
from app.models import (
    Walker, WalkerSchedule, WalkerUnavailability,
    Booking, Dog, DogOwner, User, ServiceType
)
from datetime import date, timedelta

app = create_app()

with app.app_context():

    today = date.today()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def next_weekday(start, weekday):
        """Return the next occurrence of weekday (0=Mon) on or after start."""
        d = start
        while d.weekday() != weekday:
            d += timedelta(days=1)
        return d

    # ── Collect data ─────────────────────────────────────────────────────────

    svc = ServiceType.query.filter_by(slug='group-walk', active=True).first()
    assert svc, "group-walk service type not found"

    # Walkers with schedules
    walkers = Walker.query.all()
    walker_map = {}  # id → Walker
    for w in walkers:
        walker_map[w.id] = w

    # All dogs with their primary owner user_id
    rows = (
        Dog.query
        .join(DogOwner, DogOwner.dog_id == Dog.id)
        .join(User, User.id == DogOwner.user_id)
        .filter(DogOwner.role == 'primary')
        .add_columns(User.id.label('user_id'))
        .order_by(Dog.id)
        .all()
    )
    dogs = [(row[0], row[1]) for row in rows]  # [(Dog, user_id), ...]
    print(f"Found {len(dogs)} dogs")

    # Identify walkers with active schedules (the ones that matter for capacity)
    scheduled_walker_ids = set(
        ws.walker_id
        for ws in WalkerSchedule.query.filter_by(active=True).all()
    )
    print(f"Walkers with schedules: {scheduled_walker_ids}")

    # We want to leave only ONE walker covering certain days.
    # Keep Alice (walker_id=2) on those days; mark the others unavailable.
    alice_id = 2
    constrained_walker_ids = scheduled_walker_ids - {alice_id}
    print(f"Walkers to mark unavailable on constrained days: {constrained_walker_ids}")

    # ── Week boundaries ───────────────────────────────────────────────────────

    # Week 1: Mon 10 Mar
    week1_mon = next_weekday(today + timedelta(days=1), 0)
    week2_mon = week1_mon + timedelta(weeks=1)
    week3_mon = week1_mon + timedelta(weeks=2)

    def weekdays(monday):
        return [monday + timedelta(days=i) for i in range(5)]

    w1 = weekdays(week1_mon)
    w2 = weekdays(week2_mon)
    w3 = weekdays(week3_mon)

    print(f"\nWeek 1: {w1[0]} – {w1[4]}")
    print(f"Week 2: {w2[0]} – {w2[4]}")
    print(f"Week 3: {w3[0]} – {w3[4]}")

    # ── Clear existing future pending/waitlisted bookings ─────────────────────

    cutoff = today + timedelta(days=1)
    deleted = (
        Booking.query
        .filter(
            Booking.date >= cutoff,
            Booking.status.in_(['requested', 'waitlisted'])
        )
        .delete(synchronize_session=False)
    )
    print(f"\nCleared {deleted} future requested/waitlisted bookings")

    # Clear any existing demo unavailability we're about to re-add
    existing_unavail = WalkerUnavailability.query.filter(
        WalkerUnavailability.date >= cutoff
    ).delete(synchronize_session=False)
    print(f"Cleared {existing_unavail} future walker unavailabilities")

    db.session.flush()

    # ── Add walker unavailability on constrained days ─────────────────────────
    # Week 1: Wed + Thu mornings — only Alice covers those
    # Week 2: Tue + Wed mornings — only Alice covers those
    constrained_slots = [
        (w1[2], 'Morning'),   # Wed week 1
        (w1[3], 'Morning'),   # Thu week 1
        (w2[1], 'Morning'),   # Tue week 2
        (w2[2], 'Morning'),   # Wed week 2
    ]

    for (d, slot) in constrained_slots:
        for wid in constrained_walker_ids:
            db.session.add(WalkerUnavailability(
                walker_id=wid,
                date=d,
                slot=slot,
                reason='demo_seed'
            ))

    print(f"Added unavailability on {[str(d) for d, _ in constrained_slots]} (walkers {constrained_walker_ids})")

    # ── Build booking schedule ────────────────────────────────────────────────
    # dogs[0..12] = 13 dogs
    # Capacity on normal days:  18 per slot (3 walkers × 6)
    # Capacity on constrained:   6 per slot (1 walker × 6)
    #
    # We'll book 7 dogs on constrained mornings so 1 waitlists.

    active_statuses = ('requested', 'confirmed', 'modified', 'waitlisted')

    def existing_booking(dog_id, bdate, slot):
        return Booking.query.filter(
            Booking.dog_id == dog_id,
            Booking.date == bdate,
            Booking.slot == slot,
            Booking.status.in_(active_statuses)
        ).first()

    def add_booking(dog, user_id, bdate, slot, status):
        if existing_booking(dog.id, bdate, slot):
            return False
        db.session.add(Booking(
            user_id=user_id,
            dog_id=dog.id,
            service_type_id=svc.id,
            date=bdate,
            slot=slot,
            status=status,
        ))
        return True

    created = 0
    waitlisted = 0

    # ─── Week 1 — mix of requested; first 3 days will get confirmed later ────

    # Mon: 8 dogs morning, 6 dogs afternoon
    for dog, uid in dogs[:8]:
        if add_booking(dog, uid, w1[0], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[2:8]:
        if add_booking(dog, uid, w1[0], 'Afternoon', 'requested'): created += 1

    # Tue: 7 dogs morning, 5 dogs afternoon
    for dog, uid in dogs[1:8]:
        if add_booking(dog, uid, w1[1], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[0:5]:
        if add_booking(dog, uid, w1[1], 'Afternoon', 'requested'): created += 1

    # Wed (CONSTRAINED morning — only Alice, cap=6): book 7 → 6 requested + 1 waitlisted
    for dog, uid in dogs[:6]:
        if add_booking(dog, uid, w1[2], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[6:7]:
        if add_booking(dog, uid, w1[2], 'Morning', 'waitlisted'): waitlisted += 1
    # Wed afternoon normal
    for dog, uid in dogs[3:9]:
        if add_booking(dog, uid, w1[2], 'Afternoon', 'requested'): created += 1

    # Thu (CONSTRAINED morning): 6 requested + 2 waitlisted
    for dog, uid in dogs[:6]:
        if add_booking(dog, uid, w1[3], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[6:8]:
        if add_booking(dog, uid, w1[3], 'Morning', 'waitlisted'): waitlisted += 1
    for dog, uid in dogs[5:11]:
        if add_booking(dog, uid, w1[3], 'Afternoon', 'requested'): created += 1

    # Fri: 9 dogs morning, 7 dogs afternoon
    for dog, uid in dogs[:9]:
        if add_booking(dog, uid, w1[4], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[4:11]:
        if add_booking(dog, uid, w1[4], 'Afternoon', 'requested'): created += 1

    # ─── Week 2 ────────────────────────────────────────────────────────────

    # Mon: 6 dogs morning, 5 afternoon
    for dog, uid in dogs[3:9]:
        if add_booking(dog, uid, w2[0], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[0:5]:
        if add_booking(dog, uid, w2[0], 'Afternoon', 'requested'): created += 1

    # Tue (CONSTRAINED morning): 6 requested + 1 waitlisted
    for dog, uid in dogs[2:8]:
        if add_booking(dog, uid, w2[1], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[8:9]:
        if add_booking(dog, uid, w2[1], 'Morning', 'waitlisted'): waitlisted += 1
    for dog, uid in dogs[1:7]:
        if add_booking(dog, uid, w2[1], 'Afternoon', 'requested'): created += 1

    # Wed (CONSTRAINED morning): 6 requested + 2 waitlisted
    for dog, uid in dogs[0:6]:
        if add_booking(dog, uid, w2[2], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[9:11]:
        if add_booking(dog, uid, w2[2], 'Morning', 'waitlisted'): waitlisted += 1
    for dog, uid in dogs[2:8]:
        if add_booking(dog, uid, w2[2], 'Afternoon', 'requested'): created += 1

    # Thu: 8 morning, 6 afternoon
    for dog, uid in dogs[1:9]:
        if add_booking(dog, uid, w2[3], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[4:10]:
        if add_booking(dog, uid, w2[3], 'Afternoon', 'requested'): created += 1

    # Fri: 7 morning, 8 afternoon
    for dog, uid in dogs[0:7]:
        if add_booking(dog, uid, w2[4], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[3:11]:
        if add_booking(dog, uid, w2[4], 'Afternoon', 'requested'): created += 1

    # ─── Week 3 — lighter, more spread out ────────────────────────────────

    for dog, uid in dogs[:6]:
        if add_booking(dog, uid, w3[0], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[5:10]:
        if add_booking(dog, uid, w3[0], 'Afternoon', 'requested'): created += 1

    for dog, uid in dogs[2:8]:
        if add_booking(dog, uid, w3[1], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[0:4]:
        if add_booking(dog, uid, w3[1], 'Afternoon', 'requested'): created += 1

    for dog, uid in dogs[1:7]:
        if add_booking(dog, uid, w3[2], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[6:11]:
        if add_booking(dog, uid, w3[2], 'Afternoon', 'requested'): created += 1

    for dog, uid in dogs[0:5]:
        if add_booking(dog, uid, w3[3], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[3:8]:
        if add_booking(dog, uid, w3[3], 'Afternoon', 'requested'): created += 1

    for dog, uid in dogs[2:9]:
        if add_booking(dog, uid, w3[4], 'Morning', 'requested'): created += 1
    for dog, uid in dogs[0:6]:
        if add_booking(dog, uid, w3[4], 'Afternoon', 'requested'): created += 1

    db.session.flush()

    # ── Promote some week-1 bookings to confirmed ─────────────────────────────
    # Mon + Tue week 1: confirm all the requested ones, assign to Alice
    alice = Walker.query.get(alice_id)
    confirmed_count = 0
    for bdate in [w1[0], w1[1]]:
        for slot in ('Morning', 'Afternoon'):
            bks = Booking.query.filter(
                Booking.date == bdate,
                Booking.slot == slot,
                Booking.status == 'requested'
            ).all()
            for b in bks:
                b.status = 'confirmed'
                b.walker_id = alice_id
                confirmed_count += 1

    db.session.commit()

    print(f"\n✓ Done!")
    print(f"  Requested bookings created : {created}")
    print(f"  Waitlisted bookings created: {waitlisted}")
    print(f"  Confirmed (week 1 Mon/Tue) : {confirmed_count}")
    print(f"\nConstrained days (Alice only, cap=6):")
    for d, slot in constrained_slots:
        n_req = Booking.query.filter(Booking.date == d, Booking.slot == slot, Booking.status == 'requested').count()
        n_wl  = Booking.query.filter(Booking.date == d, Booking.slot == slot, Booking.status == 'waitlisted').count()
        print(f"  {d} {slot}: {n_req} requested, {n_wl} waitlisted")
