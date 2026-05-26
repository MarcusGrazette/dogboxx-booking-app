"""
One-shot backfill for "UI-orphan" bookings that pre-date the 90c5924 fix.

Identifies confirmed future bookings whose assigned walker can no longer
work the (date, slot) — no matching WalkerSchedule entry, no ad-hoc
availability, or a WalkerUnavailability row — and resets them to
walker_id=NULL, status='requested' so they surface in the pending column
on the admin board.

Each affected client is sent exactly one 'system' notification carrying
their per-client count, identical wording to the in-app handler in
walker_schedule_json so the UX matches.

Usage:
    # See what would happen — no DB writes (default):
    venv/bin/python scripts/fix_orphaned_bookings.py

    # Actually do it:
    venv/bin/python scripts/fix_orphaned_bookings.py --apply

    # Against prod from your laptop — DATABASE_URL must point at the
    # public Railway URL (the internal one is unreachable from local):
    DATABASE_URL="<DATABASE_PUBLIC_URL>" FLASK_ENV=production \
        venv/bin/python scripts/fix_orphaned_bookings.py --apply

Idempotent: once a booking is reset its status is 'requested' (not
'confirmed'), so it no longer matches the orphan query — re-running is a
no-op.
"""
import argparse
import sys
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

# Allow running as `venv/bin/python scripts/fix_orphaned_bookings.py`
# by putting the project root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

# Flask app context — gives us db.session, create_notification, and the
# after_commit hooks that fire SSE / Web Push to clients.
from app import create_app, db
from app.models import Booking
from app.utils.notifications import create_notification


ORPHAN_SQL = """
WITH candidate_bookings AS (
    SELECT
        b.id                                           AS booking_id,
        b.date                                         AS booking_date,
        b.slot                                         AS booking_slot,
        b.walker_id,
        b.user_id                                      AS client_user_id,
        (EXTRACT(ISODOW FROM b.date)::int - 1)         AS booking_dow
    FROM bookings b
    JOIN service_types st ON st.id = b.service_type_id
    WHERE b.status         = 'confirmed'
      AND b.walker_id      IS NOT NULL
      AND b.date           >= CURRENT_DATE
      AND st.slug          = 'group-walk'
)
SELECT
    cb.booking_id,
    cb.booking_date,
    cb.booking_slot,
    cb.client_user_id,
    u_walker.firstname || ' ' || u_walker.lastname AS walker_name,
    u_client.firstname || ' ' || u_client.lastname AS client_name,
    CASE
        WHEN unavail.id IS NOT NULL          THEN 'walker marked unavailable'
        WHEN sched.id   IS NULL
         AND adhoc.id   IS NULL              THEN 'no schedule, no ad-hoc'
        ELSE 'unknown'
    END                                                AS orphan_reason
FROM candidate_bookings cb
JOIN users   u_client ON u_client.id = cb.client_user_id
JOIN walkers w        ON w.id        = cb.walker_id
JOIN users   u_walker ON u_walker.id = w.user_id
LEFT JOIN walker_schedules sched
       ON sched.walker_id    = cb.walker_id
      AND sched.day_of_week  = cb.booking_dow
      AND sched.slot::text   = cb.booking_slot::text
      AND sched.active       = TRUE
LEFT JOIN walker_adhoc_availability adhoc
       ON adhoc.walker_id    = cb.walker_id
      AND adhoc.date         = cb.booking_date
      AND adhoc.slot::text   = cb.booking_slot::text
LEFT JOIN walker_unavailabilities unavail
       ON unavail.walker_id  = cb.walker_id
      AND unavail.date       = cb.booking_date
      AND unavail.slot::text = cb.booking_slot::text
WHERE (sched.id IS NULL AND adhoc.id IS NULL)
   OR unavail.id IS NOT NULL
ORDER BY cb.booking_date, cb.booking_slot, walker_name;
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='Actually mutate. Without this flag the '
                             'script is read-only (dry run).')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        rows = db.session.execute(text(ORPHAN_SQL)).mappings().all()

        if not rows:
            print('No orphaned bookings found.')
            return 0

        # Header + summary
        print(f'Found {len(rows)} orphaned booking(s):\n')
        print(f'{"id":>6}  {"date":<11} {"slot":<10} '
              f'{"walker":<22} {"client":<24} reason')
        print('-' * 100)
        per_client = defaultdict(int)
        per_reason = defaultdict(int)
        booking_ids = []
        for r in rows:
            print(f'{r["booking_id"]:>6}  {str(r["booking_date"]):<11} '
                  f'{r["booking_slot"]:<10} {r["walker_name"]:<22} '
                  f'{r["client_name"]:<24} {r["orphan_reason"]}')
            per_client[r['client_user_id']] += 1
            per_reason[r['orphan_reason']] += 1
            booking_ids.append(r['booking_id'])

        print('\nBy reason:')
        for reason, n in per_reason.items():
            print(f'  {reason}: {n}')
        print(f'\nDistinct clients to notify: {len(per_client)}')

        if not args.apply:
            print('\n[DRY RUN] No changes made. Pass --apply to mutate.')
            return 0

        # ── Mutate ──────────────────────────────────────────────────────
        print('\nApplying reset…')
        today = _date.today()
        bookings = (
            Booking.query
            .filter(Booking.id.in_(booking_ids))
            .all()
        )
        actually_changed = 0
        for b in bookings:
            # Guard against TOCTOU — only reset if still confirmed + future
            # + still assigned. Anything that has shifted underneath us
            # since the SELECT (cancelled, reassigned, made past) is skipped.
            if (b.status == 'confirmed'
                    and b.walker_id is not None
                    and b.date >= today):
                b.walker_id = None
                b.status = 'requested'
                actually_changed += 1

        for client_user_id, n in per_client.items():
            noun = 'booking' if n == 1 else 'bookings'
            create_notification(
                recipient_id=client_user_id,
                notification_type='system',
                title=f"Status change - {n} {noun} moved to 'requested'",
                body=(
                    "A walker availability change means we need to reassign "
                    "your bookings. No need to do anything, you'll get "
                    "notifications as the bookings are updated."
                ),
                link='/',
                sender_id=None,
            )

        db.session.commit()
        print(f'Reset {actually_changed} booking(s); '
              f'notified {len(per_client)} client(s).')
        if actually_changed != len(rows):
            print(f'Note: {len(rows) - actually_changed} row(s) skipped '
                  '(state changed between SELECT and UPDATE — re-run the '
                  'diagnostic SQL to check what remains).')
        return 0


if __name__ == '__main__':
    sys.exit(main())
