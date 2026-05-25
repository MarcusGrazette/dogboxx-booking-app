-- ===========================================================================
-- find_orphaned_bookings.sql                                       READ-ONLY
-- ===========================================================================
--
-- Identifies confirmed future bookings assigned to a walker who can no longer
-- work that (date, slot) — i.e. "UI-orphan" bookings that don't appear under
-- any walker lane on the admin board (no schedule entry, no ad-hoc) and also
-- don't appear in the pending column (status is still 'confirmed').
--
-- These bookings were stranded before the fix in commit 90c5924, which now
-- resets them automatically when an admin removes a (weekday, slot) from a
-- walker's WalkerSchedule via the modal.  Bookings that were already in this
-- state at deploy time are NOT touched by the fix and remain stuck — this
-- script surfaces them.
--
-- A booking is reported if the walker has NEITHER:
--   * an active WalkerSchedule entry for the booking's (weekday, slot), NOR
--   * a WalkerAdHocAvailability row for the booking's (date, slot)
-- OR if the walker has a WalkerUnavailability row for the (date, slot).
--
-- Restricted to service_type slug 'group-walk' — the only service affected by
-- the WalkerSchedule model.  Drop-ins use a separate availability path.
--
-- DOW MAPPING NOTE
-- Python's date.weekday() returns 0=Mon..6=Sun and that is what is stored in
-- WalkerSchedule.day_of_week.  PostgreSQL's EXTRACT(DOW FROM date) returns
-- 0=Sun..6=Sat (different!).  Use EXTRACT(ISODOW FROM date)::int - 1 to get
-- 0=Mon..6=Sun, matching the model.
--
-- Run:
--   psql "$DATABASE_URL" -f scripts/find_orphaned_bookings.sql
--
-- ===========================================================================

WITH candidate_bookings AS (
    SELECT
        b.id                                           AS booking_id,
        b.date                                         AS booking_date,
        b.slot                                         AS booking_slot,
        b.walker_id,
        b.user_id                                      AS client_user_id,
        b.dog_id,
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
    (cb.booking_date - CURRENT_DATE)                   AS days_from_today,
    d.name                                             AS dog_name,
    u_client.firstname || ' ' || u_client.lastname     AS client_name,
    u_client.email                                     AS client_email,
    u_walker.firstname || ' ' || u_walker.lastname     AS walker_name,
    CASE
        WHEN unavail.id IS NOT NULL          THEN 'walker marked unavailable'
        WHEN sched.id   IS NULL
         AND adhoc.id   IS NULL              THEN 'no schedule, no ad-hoc'
        ELSE 'unknown'
    END                                                AS orphan_reason
FROM candidate_bookings cb
JOIN dogs    d        ON d.id        = cb.dog_id
JOIN users   u_client ON u_client.id = cb.client_user_id
JOIN walkers w        ON w.id        = cb.walker_id
JOIN users   u_walker ON u_walker.id = w.user_id
-- NB: bookings.slot is enum booking_slot; walker_schedules.slot, etc. are
-- schedule_slot. Same string values, different PG types — cast to text.
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
