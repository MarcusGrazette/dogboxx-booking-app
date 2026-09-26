from flask import request, render_template, redirect, flash, url_for, jsonify
from flask_login import login_required, current_user

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app import db
from app.utils.activity_log import record_admin_action, diff_fields


# ─────────────────────────────────────────────────────────────────────────────
# Revenue helpers
# ─────────────────────────────────────────────────────────────────────────────

def _revenue_for_range(start, end):
    """Return ``(daily, weekly_discount_total)`` for start..end (inclusive).

    ``daily`` is a list of per-day dicts:
        {date, revenue, walks, drop_ins, doubles, cancel_fees}
    where each day's ``revenue`` is what that day bills, gross of the weekly
    discount (the daily chart bars): confirmed walks and drop-ins, plus billed
    late-cancellation fees (``cancel_fees``), minus double-slot discounts.
    ``weekly_discount_total`` is the ≥5-walks-per-week discount summed across
    households for the whole range — it is a *weekly* concept and so can't be
    attributed to a single day, hence returned separately. Callers subtract it
    from the headline total so the dashboard equals the sum of the per-client
    invoices.

    Billed items come from ``invoicing.billable_bookings`` — the same selection
    ``invoice_for_client`` uses — so a billed late cancel counts here too.
    ``walks`` / ``drop_ins`` / ``doubles`` count delivered (confirmed) services
    only; a cancellation fee is money, not a walk. The double-slot discount is
    per dog with BOTH Morning + Afternoon confirmed on the day. Weekly discount
    is computed per billing household (a dog's primary owner), qualifying on the
    whole ISO week so a week straddling the range edge splits like the invoices.
    """
    from collections import defaultdict
    from datetime import timedelta
    from decimal import Decimal
    from sqlalchemy.orm import joinedload
    from app.models import PricingConfig, Booking, DogOwner
    from app.utils.invoicing import billable_bookings
    from app.utils.pricing import (
        config_for_date, is_drop_in, iso_weeks_window, unit_price,
        weekly_discount_for_walks,
    )

    all_configs = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= end)
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )

    # Whole ISO weeks overlapping the range, for the weekly discount; the
    # daily figures use the in-range subset.
    range_end = end + timedelta(days=1)
    weeks_from, weeks_to = iso_weeks_window(start, range_end)
    week_bookings = (
        Booking.query
        .options(joinedload(Booking.service_type))
        .filter(
            Booking.date >= weeks_from,
            Booking.date < weeks_to,
            Booking.status.in_(Booking.INVOICE_STATUSES),
        )
        .all()
    )
    confirmed, late_cancels = billable_bookings(
        [b for b in week_bookings if start <= b.date <= end])

    day_dog_slots = defaultdict(lambda: defaultdict(set))
    day_walks = defaultdict(int)
    day_drop_ins = defaultdict(int)
    for b in confirmed:
        if is_drop_in(b):
            day_drop_ins[b.date] += 1
        else:
            day_walks[b.date] += 1
            day_dog_slots[b.date][b.dog_id].add(b.slot)

    day_gross = defaultdict(Decimal)
    day_fees = defaultdict(Decimal)
    for b in confirmed + late_cancels:
        price = unit_price(b, config_for_date(all_configs, b.date))
        day_gross[b.date] += price
        if b.status == 'cancelled':
            day_fees[b.date] += price

    # Money stays Decimal until each figure leaves this function: the per-day
    # figures are float throughout (JSON API + chart data).
    results = []
    d = start
    while d <= end:
        doubles = sum(1 for slots in day_dog_slots[d].values()
                      if 'Morning' in slots and 'Afternoon' in slots)
        cfg = config_for_date(all_configs, d)
        discount = cfg.double_slot_discount if cfg else Decimal('0.00')
        results.append({
            'date':        d,
            'revenue':     float(round(day_gross[d] - doubles * discount, 2)),
            'walks':       day_walks[d],
            'drop_ins':    day_drop_ins[d],
            'doubles':     doubles,
            'cancel_fees': float(day_fees[d]),
        })
        d += timedelta(days=1)

    # Weekly ≥5-walk discount — grouped by billing household (a dog's primary
    # owner) so the rollup equals the sum of per-client invoices. Walks whose dog
    # has no primary owner are skipped (defensive; shouldn't occur for billable).
    week_walks = [b for b in week_bookings
                  if b.status == 'confirmed' and not is_drop_in(b)]
    dog_ids = {b.dog_id for b in week_walks}
    primary_owner = dict(
        db.session.query(DogOwner.dog_id, DogOwner.user_id)
        .filter(DogOwner.dog_id.in_(dog_ids), DogOwner.role == 'primary')
        .all()
    ) if dog_ids else {}
    walks_by_household = defaultdict(list)
    for b in week_walks:
        uid = primary_owner.get(b.dog_id)
        if uid is not None:
            walks_by_household[uid].append(b.date)

    weekly_discount_total = round(sum(
        float(weekly_discount_for_walks(dates, all_configs,
                                        bill_from=start, bill_to=range_end)[0])
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
        cancel_fees=round(sum(r['cancel_fees'] for r in daily), 2),
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
        cancel_fees=round(sum(r['cancel_fees'] for r in daily), 2),
        current_pricing=current_pricing.to_dict() if current_pricing else None,
    )


@admin_bp.route("/revenue/pricing", methods=["POST"])
@login_required
@admin_required
def update_pricing():
    """Add a new pricing tier."""
    from datetime import date
    from decimal import Decimal, InvalidOperation
    from app.models import PricingConfig

    try:
        price            = Decimal(request.form['price_per_walk'])
        discount         = Decimal(request.form['double_slot_discount'])
        weekly_disc      = Decimal(request.form.get('weekly_discount', 0))
        drop_in_price    = Decimal(request.form.get('price_per_drop_in', 5))
        eff_from         = date.fromisoformat(request.form['effective_from'])
    except (KeyError, ValueError, InvalidOperation) as e:
        flash(f"Invalid pricing data: {e}", "error")
        return redirect(url_for('admin.revenue'))

    PRICING_FIELDS = [
        'price_per_walk', 'double_slot_discount', 'weekly_discount', 'price_per_drop_in',
    ]

    # Check for duplicate effective_from
    existing = PricingConfig.query.filter_by(effective_from=eff_from).first()
    if existing:
        before = {f: getattr(existing, f) for f in PRICING_FIELDS}
        existing.price_per_walk       = price
        existing.double_slot_discount = discount
        existing.weekly_discount      = weekly_disc
        existing.price_per_drop_in    = drop_in_price
        changes = diff_fields(before, existing, PRICING_FIELDS)
        if changes:
            record_admin_action(
                'pricing', existing.id, 'updated', actor_id=current_user.id,
                summary=f"Updated pricing tier effective from {eff_from}", changes=changes,
            )
        flash(f"Pricing for {eff_from} updated.", "success")
    else:
        new_tier = PricingConfig(
            price_per_walk=price,
            double_slot_discount=discount,
            weekly_discount=weekly_disc,
            price_per_drop_in=drop_in_price,
            effective_from=eff_from,
        )
        db.session.add(new_tier)
        db.session.flush()  # populate new_tier.id before logging
        record_admin_action(
            'pricing', new_tier.id, 'created', actor_id=current_user.id,
            summary=f"Added new pricing tier effective from {eff_from}",
        )
        flash(f"New pricing tier effective from {eff_from} added.", "success")

    db.session.commit()
    return redirect(url_for('admin.revenue'))
