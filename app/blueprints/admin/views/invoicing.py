from collections import defaultdict
from flask import request, render_template
from flask_login import login_required
from sqlalchemy.orm import joinedload

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import User, Dog, DogOwner
from app import db
from app.utils.invoicing import invoice_for_client as _invoice_for_client


@admin_bp.route("/invoicing")
@login_required
@admin_required
def invoicing():
    """Monthly invoicing summary — one row per client."""
    from datetime import date
    from calendar import monthrange

    # ── Month selection ───────────────────────────────────────────────────
    today = date.today()
    month_str = request.args.get('month', f'{today.year}-{today.month:02d}')
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
        if not (1 <= month <= 12):
            raise ValueError
    except (ValueError, IndexError):
        year, month = today.year, today.month

    month_start = date(year, month, 1)
    month_end   = date(year + (month // 12), (month % 12) + 1, 1)

    # Prev / next month helpers for navigation
    if month == 1:
        prev_month = f'{year - 1}-12'
    else:
        prev_month = f'{year}-{month - 1:02d}'
    if month == 12:
        next_month = f'{year + 1}-01'
    else:
        next_month = f'{year}-{month + 1:02d}'

    # ── Pricing configs (loaded once) ─────────────────────────────────────
    from app.models import PricingConfig
    all_configs = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= month_end)
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )

    # ── Clients ───────────────────────────────────────────────────────────
    # Membership test is *presence of a Client record*, not role == 'client' —
    # a dual-role user (role='walker' with a Client record, e.g. the owner who
    # also has their own dog walked) is a billable client. role == 'client'
    # silently dropped them from the list even though their detail page (which
    # uses User.client != None) works. Matches clients.py / decorators.py.
    clients = (
        User.query
        .options(joinedload(User.client))
        .filter(User.client != None)  # noqa: E711 — SQLAlchemy uses != None for EXISTS
        .order_by(User.lastname, User.firstname)
        .all()
    )

    # Batch all DogOwner / Dog / secondary-User lookups — avoids ~2 queries per client
    client_ids = [u.id for u in clients]

    primary_ownership_by_user = {
        o.user_id: o
        for o in DogOwner.query.filter(
            DogOwner.user_id.in_(client_ids),
            DogOwner.role == 'primary',
        ).all()
    }

    primary_dog_ids = [o.dog_id for o in primary_ownership_by_user.values()]
    dogs_by_id = (
        {d.id: d for d in Dog.query.filter(Dog.id.in_(primary_dog_ids)).all()}
        if primary_dog_ids else {}
    )

    secondary_owners_by_dog: dict = defaultdict(list)
    if primary_dog_ids:
        sec_ownerships = DogOwner.query.filter(
            DogOwner.dog_id.in_(primary_dog_ids),
            DogOwner.role == 'secondary',
        ).all()
        sec_user_ids = [so.user_id for so in sec_ownerships]
        sec_users_by_id = (
            {u.id: u for u in User.query.filter(User.id.in_(sec_user_ids)).all()}
            if sec_user_ids else {}
        )
        for so in sec_ownerships:
            user = sec_users_by_id.get(so.user_id)
            if user:
                secondary_owners_by_dog[so.dog_id].append(user)

    rows = []
    for u in clients:
        inv = _invoice_for_client(u.id, month_start, month_end, all_configs)
        if inv is None or inv['total_billable'] == 0:
            continue
        primary_ownership = primary_ownership_by_user.get(u.id)
        dog = dogs_by_id.get(primary_ownership.dog_id) if primary_ownership else None
        secondary_owners = secondary_owners_by_dog.get(dog.id, []) if dog else []
        rows.append({
            'client':           u,
            'dog':              dog,
            'secondary_owners': secondary_owners,
            **inv,
        })

    grand_total = round(sum(r['subtotal'] for r in rows), 2)

    return render_template(
        'admin_invoicing.html',
        rows=rows,
        grand_total=grand_total,
        month_start=month_start,
        prev_month=prev_month,
        next_month=next_month,
        today=today,
    )


@admin_bp.route("/invoicing/<int:client_id>")
@login_required
@admin_required
def invoicing_detail(client_id):
    """Per-client invoice detail — line items for the selected month."""
    from datetime import date
    from itertools import groupby

    client_user = User.query.filter(
        User.client != None, User.id == client_id
    ).first_or_404()

    today = date.today()
    month_str = request.args.get('month', f'{today.year}-{today.month:02d}')
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
        if not (1 <= month <= 12):
            raise ValueError
    except (ValueError, IndexError):
        year, month = today.year, today.month

    month_start = date(year, month, 1)
    month_end   = date(year + (month // 12), (month % 12) + 1, 1)

    from app.models import PricingConfig
    from app.utils.pricing import config_for_date, build_line_items, build_double_slot_discounts
    all_configs = (
        PricingConfig.query
        .filter(PricingConfig.effective_from <= month_end)
        .order_by(PricingConfig.effective_from.desc())
        .all()
    )

    inv = _invoice_for_client(client_user.id, month_start, month_end, all_configs)
    if inv is None:
        inv = {'confirmed': [], 'late_cancels': [], 'all_billable': [],
               'total_walks': 0, 'total_drop_ins': 0, 'total_cancels': 0,
               'total_billable': 0, 'doubles': 0, 'subtotal': 0.0}

    late_cancel_ids = {b.id for b in inv['late_cancels']}
    line_items = build_line_items(inv['all_billable'], late_cancel_ids, all_configs)
    discounts = build_double_slot_discounts(inv['all_billable'], all_configs)

    do = DogOwner.query.filter_by(user_id=client_user.id, role='primary').first()
    dog = db.session.get(Dog, do.dog_id) if do else None

    # ── Weekly breakdown ──────────────────────────────────────────────────
    # Find all Mon-commencing weeks that overlap the month
    from datetime import timedelta
    # First Monday on or before month_start
    first_monday = month_start - timedelta(days=month_start.weekday())
    weeks = []
    weekly_discounts = []  # per-qualifying-week discount line items for the line-items section
    wk_start = first_monday
    while wk_start < month_end:
        wk_end = wk_start + timedelta(days=7)  # exclusive

        # Line items in this week
        wk_items = [li for li in line_items if wk_start <= li['booking'].date < wk_end]
        wk_discounts = [d for d in discounts if wk_start <= d['date'] < wk_end]

        wk_confirmed  = sum(1 for li in wk_items if not li['is_cancel'] and not li['is_drop_in'])
        wk_drop_ins   = sum(1 for li in wk_items if not li['is_cancel'] and li['is_drop_in'])
        wk_cancels    = sum(1 for li in wk_items if li['is_cancel'])
        wk_double_discount = sum(d['amount'] for d in wk_discounts)

        # Weekly discount: ≥5 confirmed group walks in the week
        wk_weekly_discount = 0.0
        if wk_confirmed >= 5:
            cfg = config_for_date(all_configs, wk_start)
            if cfg and cfg.weekly_discount:
                wk_weekly_discount = round(float(cfg.weekly_discount) * wk_confirmed, 2)
                weekly_discounts.append({
                    'week_start':  wk_start,
                    'walk_count':  wk_confirmed,
                    'amount':      wk_weekly_discount,
                })

        wk_discount_total = round(wk_double_discount + wk_weekly_discount, 2)
        wk_gross      = sum(li['unit_price'] for li in wk_items)
        wk_subtotal   = round(wk_gross - wk_discount_total, 2)

        weeks.append({
            'commencing':          wk_start,
            'confirmed':           wk_confirmed,
            'drop_ins':            wk_drop_ins,
            'cancels':             wk_cancels,
            'double_discount':     wk_double_discount,
            'weekly_discount':     wk_weekly_discount,
            'discount_total':      wk_discount_total,
            'subtotal':            wk_subtotal,
            'has_activity':        bool(wk_items),
        })
        wk_start = wk_end

    # Prev/next month nav
    if month == 1:
        prev_month = f'{year - 1}-12'
    else:
        prev_month = f'{year}-{month - 1:02d}'
    if month == 12:
        next_month = f'{year + 1}-01'
    else:
        next_month = f'{year}-{month + 1:02d}'

    return render_template(
        'admin_invoicing_detail.html',
        client_user=client_user,
        dog=dog,
        inv=inv,
        line_items=line_items,
        discounts=discounts,
        weekly_discounts=weekly_discounts,
        weeks=weeks,
        month_start=month_start,
        prev_month=prev_month,
        next_month=next_month,
        today=today,
    )
