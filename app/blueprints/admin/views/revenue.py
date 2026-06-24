from flask import request, render_template, redirect, flash, url_for, jsonify
from flask_login import login_required

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app import db


# ─────────────────────────────────────────────────────────────────────────────
# Revenue helpers
# ─────────────────────────────────────────────────────────────────────────────

def _revenue_for_range(start, end):
    """Return ``(daily, weekly_discount_total)`` for start..end (inclusive).

    ``daily`` is a list of per-day dicts:
        {date, revenue, walks, drop_ins, doubles, price_per_walk,
         price_per_drop_in, discount}
    where each day's ``revenue`` is gross of the weekly discount (the daily chart
    bars). ``weekly_discount_total`` is the ≥5-walks-per-week discount summed
    across households for the whole range — it is a *weekly* concept and so can't
    be attributed to a single day, hence returned separately. Callers subtract it
    from the headline total so the dashboard reflects what is actually invoiced.

    Logic per day:
      - Group walks: count confirmed bookings by (dog_id, slot); double-slot
        discount for dogs with BOTH Morning + Afternoon on the same day
      - Drop-ins: counted separately, priced at price_per_drop_in (no discount)
    Weekly discount is computed per billing household (a dog's primary owner) so
    it matches the sum of per-client invoices. Uses the PricingConfig with the
    highest effective_from <= the relevant day. Note: like the invoice path's
    double-slot keying, weekly grouping is by primary owner — see
    docs/ARCHITECTURE_REVIEW.md for the two-dog-household caveat (out of scope).
    """
    from datetime import timedelta
    from app.models import PricingConfig, Booking, ServiceType, DogOwner
    from app.utils.pricing import config_for_date, weekly_discount_for_walks

    all_configs = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= end)
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )

    # Group walk bookings: (date, dog_id, slot)
    walk_rows = (
        Booking.query
        .join(ServiceType)
        .filter(
            Booking.date >= start,
            Booking.date <= end,
            Booking.status == 'confirmed',
            Booking.slot.in_(['Morning', 'Afternoon']),
            ServiceType.slug == ServiceType.WALK,
        )
        .with_entities(Booking.date, Booking.dog_id, Booking.slot)
        .all()
    )

    # Drop-in bookings: (date,)
    drop_in_rows = (
        Booking.query
        .join(ServiceType)
        .filter(
            Booking.date >= start,
            Booking.date <= end,
            Booking.status == 'confirmed',
            ServiceType.slug == ServiceType.DROP_IN,
        )
        .with_entities(Booking.date)
        .all()
    )

    # Build lookups
    day_dog_slots = {}
    for r in walk_rows:
        day_dog_slots.setdefault(r.date, {}).setdefault(r.dog_id, set()).add(r.slot)

    day_drop_ins = {}
    for r in drop_in_rows:
        day_drop_ins[r.date] = day_drop_ins.get(r.date, 0) + 1

    results = []
    d = start
    while d <= end:
        dog_slots  = day_dog_slots.get(d, {})
        walks      = sum(len(slots) for slots in dog_slots.values())
        doubles    = sum(1 for slots in dog_slots.values()
                         if 'Morning' in slots and 'Afternoon' in slots)
        drop_ins   = day_drop_ins.get(d, 0)
        cfg = config_for_date(all_configs, d)
        if cfg:
            price          = float(cfg.price_per_walk)
            drop_in_price  = float(cfg.price_per_drop_in)
            discount       = float(cfg.double_slot_discount)
            revenue        = round(
                walks * price - doubles * discount + drop_ins * drop_in_price, 2
            )
        else:
            price = drop_in_price = discount = revenue = 0.0
        results.append({
            'date':              d,
            'revenue':           revenue,
            'walks':             walks,
            'drop_ins':          drop_ins,
            'doubles':           doubles,
            'price_per_walk':    price,
            'price_per_drop_in': drop_in_price,
            'discount':          discount,
        })
        d += timedelta(days=1)

    # Weekly ≥5-walk discount — grouped by billing household (a dog's primary
    # owner) so the rollup equals the sum of per-client invoices. Walks whose dog
    # has no primary owner are skipped (defensive; shouldn't occur for billable).
    dog_ids = {r.dog_id for r in walk_rows}
    primary_owner = dict(
        db.session.query(DogOwner.dog_id, DogOwner.user_id)
        .filter(DogOwner.dog_id.in_(dog_ids), DogOwner.role == 'primary')
        .all()
    ) if dog_ids else {}
    walks_by_household = {}
    for r in walk_rows:
        uid = primary_owner.get(r.dog_id)
        if uid is None:
            continue
        walks_by_household.setdefault(uid, []).append(r.date)

    weekly_discount_total = round(sum(
        weekly_discount_for_walks(dates, all_configs)[0]
        for dates in walks_by_household.values()
    ), 2)

    return results, weekly_discount_total


# ─────────────────────────────────────────────────────────────────────────────
# Revenue page
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route("/revenue")
@login_required
@admin_required
def revenue():
    """Revenue tracker page."""
    from datetime import date
    from app.models import PricingConfig

    today = date.today()
    # Default: current calendar month
    start = today.replace(day=1)
    end   = (start.replace(month=start.month % 12 + 1, day=1)
             if start.month < 12
             else start.replace(year=start.year + 1, month=1, day=1))
    import datetime as _dt
    end = end - _dt.timedelta(days=1)

    daily, weekly_discount = _revenue_for_range(start, end)
    gross_revenue = sum(r['revenue'] for r in daily)

    current_pricing = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= today)
        .order_by(PricingConfig.effective_from.desc())
        .first()
    )
    all_pricing = (
        PricingConfig.query
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )

    return render_template(
        "admin_revenue.html",
        today_iso=today.isoformat(),
        start_iso=start.isoformat(),
        chart_labels=[r['date'].strftime('%-d') for r in daily],
        chart_revenue=[r['revenue'] for r in daily],
        chart_walks=[r['walks'] for r in daily],
        total_revenue=round(gross_revenue - weekly_discount, 2),
        total_walks=sum(r['walks'] for r in daily),
        total_doubles=sum(r['doubles'] for r in daily),
        weekly_discount=weekly_discount,
        current_pricing=current_pricing,
        all_pricing=all_pricing,
    )


@admin_bp.route("/api/revenue-data")
@login_required
@admin_required
def revenue_data():
    """JSON revenue data for a calendar month. ?start=YYYY-MM-DD (any day in the month)."""
    from datetime import date
    import datetime as _dt

    today = date.today()
    start_str = request.args.get('start')
    try:
        raw = date.fromisoformat(start_str)
    except (TypeError, ValueError):
        raw = today

    start = raw.replace(day=1)
    end   = (start.replace(month=start.month % 12 + 1, day=1)
             if start.month < 12
             else start.replace(year=start.year + 1, month=1, day=1))
    end = end - _dt.timedelta(days=1)

    daily, weekly_discount = _revenue_for_range(start, end)
    gross_revenue = sum(r['revenue'] for r in daily)

    from app.models import PricingConfig
    current_pricing = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= today)
        .order_by(PricingConfig.effective_from.desc())
        .first()
    )

    return jsonify(
        start=start.isoformat(),
        labels=[r['date'].strftime('%-d') for r in daily],
        month_label=start.strftime('%B %Y'),
        revenue=[r['revenue'] for r in daily],
        walks=[r['walks'] for r in daily],
        total_revenue=round(gross_revenue - weekly_discount, 2),
        total_walks=sum(r['walks'] for r in daily),
        total_doubles=sum(r['doubles'] for r in daily),
        weekly_discount=round(weekly_discount, 2),
        current_pricing=current_pricing.to_dict() if current_pricing else None,
    )


@admin_bp.route("/revenue/pricing", methods=["POST"])
@login_required
@admin_required
def update_pricing():
    """Add a new pricing tier."""
    from datetime import date
    from app.models import PricingConfig

    try:
        price            = float(request.form['price_per_walk'])
        discount         = float(request.form['double_slot_discount'])
        weekly_disc      = float(request.form.get('weekly_discount', 0))
        drop_in_price    = float(request.form.get('price_per_drop_in', 5))
        eff_from         = date.fromisoformat(request.form['effective_from'])
    except (KeyError, ValueError) as e:
        flash(f"Invalid pricing data: {e}", "danger")
        return redirect(url_for('admin.revenue'))

    # Check for duplicate effective_from
    existing = PricingConfig.query.filter_by(effective_from=eff_from).first()
    if existing:
        existing.price_per_walk       = price
        existing.double_slot_discount = discount
        existing.weekly_discount      = weekly_disc
        existing.price_per_drop_in    = drop_in_price
        flash(f"Pricing for {eff_from} updated.", "success")
    else:
        db.session.add(PricingConfig(
            price_per_walk=price,
            double_slot_discount=discount,
            weekly_discount=weekly_disc,
            price_per_drop_in=drop_in_price,
            effective_from=eff_from,
        ))
        flash(f"New pricing tier effective from {eff_from} added.", "success")

    db.session.commit()
    return redirect(url_for('admin.revenue'))
