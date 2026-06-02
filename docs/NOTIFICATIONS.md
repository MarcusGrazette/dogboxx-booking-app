# Notification Audit — DogBoxx

> **Status:** descriptive audit of the system *as it exists today* (not a target design).
> **Scope:** in-app notification bell + Web Push (iOS/Android PWA), plus the admin **activity feed**
> at `/admin/activity` (§8) and how it lines up with the notification stream.
> **Out of scope:** email — used solely for broadcasts, newsletter, password reset, and bug reports.
> Generated from a trace of all 47 `create_notification()` call sites across the client, walker, and admin blueprints, plus the `activity_feed()` route.

---

## 1. Delivery model

Every notification in the app is created through a single helper,
`app/utils/notifications.py::create_notification()`. That helper does **all** of the following
in one call:

1. Inserts a `Notification` row (the bell entry).
2. Queues an **SSE event** so open browser tabs / PWA windows update the bell live.
3. Queues a **Web Push payload** for every registered `PushSubscription` (iOS + Android PWA).

Both SSE and Web Push fire from the same `after_commit` hook in `app/__init__.py`.

**Key consequence:** bell and push are *coupled*. There is no path that pushes without a bell row,
or writes a bell row without attempting push. "Which channel" is therefore never a per-notification
decision — it is purely *"does this recipient have a registered device?"*. There is currently **no
way to send a bell-only (quiet) update or a push-only nudge** without new plumbing.

| Property | Value |
|---|---|
| Channels | Bell (live via SSE) **+** Web Push — always both, automatically |
| Recipient resolution | `recipient_id` per call; admin notifications loop over all `is_admin=True` users |
| Stored cap | 50 per user (oldest pruned at insert) |
| Page cap | 20 on the notifications page |
| Bell dropdown cap | 5 |

---

## 2. Notification type catalogue

`notification_type` is purely a **styling key** — it maps to an icon + colour in
`NOTIFICATION_META` (`app/utils/notifications.py`) and nothing else. It does **not** affect delivery.

| Type | Icon / colour | Meaning |
|---|---|---|
| `booking_confirmed` | check-circle, green | A walk/drop-in is confirmed with a walker |
| `booking_requested` | calendar-plus, pink | Booking awaiting manual confirmation, or waitlisted |
| `same_day_request` | lightning, orange | Same-day booking — urgent, skips auto-assign |
| `booking_cancelled` | x-circle, red | Booking cancelled / declined / paused |
| `walker_assigned` | person-check, blue | Walker told a walk is now on their schedule |
| `walker_availability` | calendar-x, orange | Walker changed their availability (admin-facing) |
| `system` | info-circle, grey | Slot moved, access revoked, reassignment notice, broadcast |
| `dental_confirmed` | check-circle, green | **DEAD** — defined in META, zero call sites |
| `dental_available` | calendar-event, pink | **DEAD** — defined in META, zero call sites |

---

## 3. Client-triggered actions

| Action (route) | Recipient | Type | Example notification (title — body) |
|---|---|---|---|
| **Book walk, auto-assign succeeds** (`/book`, `/`) | Client (actor) | `booking_confirmed` | "Daisy's morning walk on Mon 1 Jun has been confirmed" — "Booked with Alice." |
| | All admins | `booking_confirmed` | "John booked Daisy's morning walk on Mon 1 Jun" |
| | Co-owners | `booking_confirmed` | "John booked Daisy's morning walk on Mon 1 Jun" |
| **Book walk, no walker free** | All admins | `booking_requested` | "John requested Daisy's morning walk on Mon 1 Jun" |
| | Client (actor) | `booking_requested` | "Daisy's morning walk on Mon 1 Jun has been requested" — "We'll confirm shortly." |
| | Co-owners | `booking_requested` | "John requested Daisy's morning walk on Mon 1 Jun" |
| **Book same-day walk** (skips auto-assign) | All admins | `same_day_request` | "Same-day request — John requested Daisy's morning walk on Mon 1 Jun" |
| | Client (actor) | `same_day_request` | "Daisy's morning walk on Mon 1 Jun has been requested" — "Same-day request — Lydia will confirm shortly." |
| | Co-owners | `booking_requested` | "John requested Daisy's morning walk on Mon 1 Jun" |
| **Book walk, slot full → waitlisted** | All admins | `booking_requested` | "New booking request for Mon 1 Jun" — "John requested Morning for Daisy" |
| | Client (actor) | `booking_requested` | "Daisy's morning walk on Mon 1 Jun is on the waitlist" — "We'll let you know when a spot opens up." |
| | Co-owners | `booking_requested` | "John requested Daisy's morning walk on Mon 1 Jun" |
| **Book AM+PM together** (`/book_both`) | Client (actor) | consolidated `booking_confirmed` / `booking_requested` | "Daisy's walks on Mon 1 Jun have been confirmed" — "Morning and afternoon both booked." |
| | All admins | `booking_confirmed` and/or `booking_requested` | "John booked Daisy's morning & afternoon walks on Mon 1 Jun" |
| | Co-owners | per-slot `booking_confirmed` / `booking_requested` | "John booked Daisy's morning walk on Mon 1 Jun" |
| **Request drop-in** (`/book_drop_in`) — never auto-assigned | All admins | `booking_requested` / `same_day_request` | "New drop-in request for Mon 1 Jun" — "John requested Morning drop-in for Daisy" |
| | Client (actor) | `booking_requested` / `same_day_request` | "Daisy's morning drop-in on Mon 1 Jun has been requested" — "We'll confirm shortly." |
| | Co-owners | `booking_requested` | "John requested Daisy's morning drop-in on Mon 1 Jun" |
| **Set up recurring bookings** (`/recurring_booking`) | Client (actor) | summary `booking_confirmed` / `booking_requested` | "Daisy's recurring walks have been confirmed" — "5 weekly AM + PM walks booked." |
| | All admins (if any pending) | `booking_requested` | "Recurring booking request — 5 walks" — "John requested weekly AM + PM walks for Daisy" |
| | Co-owners | *(none — recurring skips co-owner notify)* | — |
| **Cancel one booking** (`/cancel_booking`) | All admins | `booking_cancelled` | "John Doe cancelled Daisy's morning walk on Mon 1 Jun" |
| | Co-owners | `booking_cancelled` | "John cancelled Daisy's morning walk on Mon 1 Jun" |
| | Assigned walker | `booking_cancelled` | "John cancelled Daisy's morning walk on Mon 1 Jun" |
| **Pause walks over a range** (`/pause-walks`) | All admins | `booking_cancelled` | "John paused morning walks 1 Jun–7 Jun" — "5 bookings cancelled · Daisy, Rex" |
| | Each co-owner | `booking_cancelled` | "John paused Daisy, Rex's morning walks 1 Jun–7 Jun" — "5 bookings cancelled." |
| | Each affected walker | `booking_cancelled` | "John paused morning walks 1 Jun–7 Jun" — "3 of your assigned walks cancelled" |

---

## 4. Admin-triggered actions

| Action (route) | Recipient | Type | Example notification (title — body) |
|---|---|---|---|
| **Assign / confirm walker** (`/assign_walker`) | Client (owner) | `booking_confirmed` | "Daisy's morning walk on Mon 1 Jun has been confirmed" — "Booked with Alice." |
| | Assigned walker (≠ actor) | `walker_assigned` | "You have been assigned a walk on Mon 1 Jun" — "Daisy — Morning" |
| **Assign with slot override** (slot changed) | Client (owner) | `system` *(replaces the confirm)* | "Daisy's walk on Mon 1 Jun has been moved to afternoon" — "Originally booked for morning." |
| | Assigned walker | `walker_assigned` | "You have been assigned a walk on Mon 1 Jun" — "Daisy — Afternoon" |
| **Decline pending/waitlisted** (`/decline`) | Client (owner) | `booking_cancelled` | "Daisy's morning walk on Mon 1 Jun has been declined" — "Please get in touch if you'd like to discuss." |
| **Cancel a client's booking** (`/cancel_booking`, admin path) | Client (owner) | `booking_cancelled` | "Daisy's morning walk on Mon 1 Jun has been cancelled" — "Please get in touch if you'd like to discuss." |
| | Assigned walker (≠ actor) | `booking_cancelled` | "Daisy's morning walk on Mon 1 Jun was cancelled" |
| **Book on behalf of client** (`/book_for_dog`) | Client (owner) — **confirmed slots only** | `booking_confirmed` | "Lydia booked a morning walk for Daisy on your behalf" — "Booked on Mon 1 Jun with Alice." |
| | Assigned walker (≠ actor, not past) | `walker_assigned` | "You have been assigned a group walk on 1 Jun 2026" — "Daisy — Morning" |
| **Recurring bookings for a dog** (`/recurring_for_dog`) | Client (owner) — **confirmed only** | `booking_confirmed` | "Daisy's morning walk on Mon 1 Jun has been confirmed" — "Booked with Alice." |
| | Assigned walker (≠ actor) | `walker_assigned` | "You have been assigned a walk on 1 Jun 2026" — "Daisy — Morning" |
| **Bulk-cancel a dog's bookings** (`/dogs/<id>/bulk-cancel`) | Dog owners (excl. admins) | `booking_cancelled` | "Daisy's Mon morning walks have been cancelled 1 Jun–7 Jun" — "5 bookings cancelled." |
| **Create a closure** (`/closures`) | Each affected booking's owner | `booking_cancelled` | "Daisy's morning walk on Mon 1 Jun has been cancelled" — "DogBoxx is closed — Bank holiday." |
| **Revoke secondary's dog access** (`/revoke-access`) | Secondary user | `system` | "Your access to Daisy has been removed" — "Contact Dogboxx if you think this is a mistake." |
| **Edit walker weekly schedule, remove a slot** (`/schedule-json`) | Each affected client | `system` | "Status change - 3 bookings moved to 'requested'" — "A walker availability change means we need to reassign your bookings…" |
| **Send broadcast (bell channel)** (`/broadcasts`) | Each booked recipient (primary + secondary) | `system` | "<subject>" — "<body>" |

---

## 5. Walker-triggered actions

| Action (route) | Recipient | Type | Example notification (title — body) |
|---|---|---|---|
| **Mark self unavailable** (`/unavailability`) | All admins (excl. self) | `walker_availability` | "Alice is unavailable — Morning on Mon 1 Jun" — "2 bookings need reassigning" |
| **Add ad-hoc availability** (`/adhoc`) | All admins (excl. self) | `walker_availability` | "Alice added Morning availability on Mon 1 Jun" |
| **Batch schedule changes** (`/schedule-changes/batch`) | All admins | `walker_availability` | "Alice is unavailable 1 Jun–7 Jun" — "5 slots, 2 bookings need reassigning" |

---

## 6. Multi-row actions — notification grouping

Some actions create or cancel **many booking rows in one request**. For each, a recipient either gets
**one consolidated notification** (*grouped*) or **one notification per booking row** (*individual*).
"Grouping" here means *per-recipient consolidation* — fan-out across multiple recipients of the same
role (e.g. every admin gets a copy) is a separate axis.

**The client-initiated bulk actions consolidate; most of their admin-initiated equivalents do not.**
That is the core inconsistency: a client pausing a week of walks produces one tidy admin notification,
but an admin booking that same week on the client's behalf produces one notification *per walk*.

| Action (route) | Trigger | Rows | Recipient | Grouping |
|---|---|---|---|---|
| **Pause walks** (`/pause-walks`) | Client | many | Admins | **Grouped** — 1/admin ("John paused morning walks 1 Jun–7 Jun · 5 bookings") |
| | | | Each co-owner | **Grouped** — 1/co-owner |
| | | | Each ex-walker | **Grouped** — 1/walker ("3 of your assigned walks cancelled") |
| **Recurring bookings** (`/recurring_booking`) | Client | many | Client | **Grouped** — 1 summary ("5 weekly AM + PM walks booked") |
| | | | Admins | **Grouped** — 1/admin (pending total only) |
| | | | Assigned walker | *None* — client flows never notify the walker (§7.9) |
| **Book AM+PM** (`/book_both`) | Client | 1–2 | Client | **Grouped** — 1 consolidated |
| | | | Admins | **Grouped** — 1/admin per status group (≤2) |
| | | | Co-owners | **Individual** — 1 per slot |
| | | | Assigned walker | *None* (§7.9) |
| **Book on behalf** (`/book_for_dog`) | Admin | 1–2 | Client | **Individual** — 1 per slot |
| | | | Assigned walker | **Individual** — 1 per slot |
| **Recurring for dog** (`/recurring_for_dog`) | Admin | many | Client | **Individual** — 1 per confirmed booking |
| | | | Assigned walker | **Individual** — 1 per confirmed booking |
| **Bulk-cancel a dog** (`/dogs/<id>/bulk-cancel`) | Admin | many | Dog owners | **Grouped** — 1/owner ("5 bookings cancelled") |
| | | | Assigned walker | *None* (§7.4) |
| **Create closure** (`/closures`) | Admin | many | Each booking's owner | **Individual** — 1 per booking |
| | | | Co-owners / walker | *None* (§7.4) |
| **Edit weekly schedule** (`/schedule-json`) | Admin | many | Affected clients | **Grouped** — 1/client, per-client count |
| **Batch schedule changes** (`/schedule-changes/batch`) | Walker | many | Admins | **Grouped** — 1/admin, covers whole batch |
| | | | Affected clients | *None* (§7.1) |
| **Mark unavailable** (`/unavailability`) | Walker | 1 slot, many bookings | Admins | **Grouped** — 1/admin ("2 bookings need reassigning") |
| | | | Affected clients | *None* (§7.1) |

**Where grouping diverges:**

- **Recurring:** client `recurring_booking` = grouped (1 summary); admin `recurring_for_dog` = individual (1 per booking). A 20-walk recurring setup by an admin produces **20** client notifications.
- **AM+PM:** client `book_both` = grouped (1); admin `book_for_dog` = individual (2).
- **Closure:** individual per booking — a client with two bookings on a closed day gets **two** cancellation notifications.
- **Bulk-cancel (per dog):** the admin path **is** grouped (1/owner) — the one admin bulk action that already matches the client pattern.
- **Co-owners** are notified **individually per slot** even in the otherwise-grouped client `book_both`.

---

## 7. Gaps & inconsistencies

Surfaced while tracing call sites. These are holes in "who gets told what" — recorded here so the
target notification design (next phase) can decide which to close.

### 7.1 Walker self-service availability changes don't tell affected clients
`/unavailability` (`walker/routes.py:284-303`) and `/schedule-changes/batch`
(`walker/routes.py:599-659`) reset confirmed bookings to `requested` but notify **admins only**.
The admin-side `/schedule-json` path *does* notify clients. So whether a client's booking silently
reverts depends on **who** changed the schedule.

### 7.2 Two admin paths reset bookings with no notifications at all
`deactivate_walker` (`admin/routes.py:2510`) and `admin_add_unavailability`
(`admin/routes.py:2896`) flip confirmed → `requested` (`walker_id=None`) but notify **nobody** —
not client, admin, or walker. This contradicts the CLAUDE.md note claiming all three reset-paths
notify the client; in practice **only `walker_schedule_json` does**.

### 7.3 Admin-created bookings that land pending never reach the client
`book_for_dog` (`admin/routes.py:3158`) and `recurring_for_dog` (`admin/routes.py:3337`) only notify
the client on **confirmed** slots. A client can have a pending/waitlisted admin-made booking they
never hear about.

### 7.4 Closures and admin bulk-cancel skip co-owners and the assigned walker
- **Closure** (`add_closure`, `admin/routes.py:3680`) notifies only `booking.user_id` — not
  co-owners, not the assigned walker.
- **Bulk-cancel** (`dog_bulk_cancel`, `admin/routes.py:3578`) notifies dog owners only — the walker
  whose schedule just changed isn't told.

### 7.5 Walk pickup/drop-off events aren't recorded at all
`WalkEvent` is a **dead table** (like the `dental_*` types §7.6, and like `BookingStatusChange` §8.2
*was* before Session 1 wired it up): the model + `Booking` relationship (`models.py:577`) + `__init__.py`
import exist, but **no code ever writes a `WalkEvent` row, and there is no pickup/drop-off recording UI**. So clients get nothing
when their dog is collected or dropped home — and the gap is deeper than notifications: the events
themselves don't exist. Recording pickup/drop-off is a **prerequisite feature**, not just a missing
notification (see §9.9 D2).

### 7.6 Dead notification types
`dental_confirmed` and `dental_available` are defined in `NOTIFICATION_META`
(`notifications.py:38-39`) but have **zero call sites**. Cleanup candidate.

### 7.7 Minor wording inconsistency
`book_for_dog` walker notification (`admin/routes.py:3185`) uses `service.name.lower()`
("group walk" / "drop in") where the canonical client-facing label is `'walk'` / `'drop-in'`.
Cosmetic, not a bug.

### 7.8 Bulk-notification grouping is inconsistent between client and admin equivalents
Client-initiated bulk actions consolidate per recipient; most admin-initiated equivalents do not.
`recurring_for_dog` (`admin/routes.py:3356`) and `book_for_dog` (`admin/routes.py:3158`) emit one
notification per booking, and `add_closure` (`admin/routes.py:3670`) emits one per cancelled booking —
where the client-side `recurring_booking`, `book_both`, and `pause-walks` each send a single grouped
summary. The admin per-dog bulk-cancel is the exception (already grouped). Full breakdown in §6.
Aligning the admin paths on the client grouping pattern would sharply cut notification volume on large
recurring/closure operations.

### 7.9 Client bookings never notify the auto-assigned walker
When a client books (single, `book_both`, or `recurring_booking`) and `auto_assign_walker` confirms a
walker, that walker receives **no** notification — they discover the walk only via their pickup list.
Admin assignment (`assign_walker`, `book_for_dog`, `recurring_for_dog`) *does* send `walker_assigned`.
So whether the walker is told about a newly-assigned walk depends on who created the booking.

---

## 8. Activity feed (`/admin/activity`)

The activity feed gives the admin a single timeline of what's happening across the app. It is built by
a **different mechanism** from notifications, which is the source of most consistency issues below.

### 8.1 How it's built

Unlike notifications (emitted at action time by `create_notification()`), the feed is **reconstructed
at view time** by querying domain tables for the selected month (`activity_feed()`,
`admin/routes.py:1493`). It never reads `Notification` rows. Four event sources, each emitting **one
event per row** (no grouping anywhere):

| Source | Query | Timestamp | Badge |
|---|---|---|---|
| New bookings | `Booking`, status ∉ (cancelled, rejected) | `created_at` | **current** status |
| Cancellations | `Booking`, status ∈ (cancelled, rejected) | `cancelled_at` | cancelled |
| Walker unavailability | `WalkerUnavailability` | `created_at` | unavailable |
| Walker adhoc availability | `WalkerAdHocAvailability` | `created_at` | available |

UI: client-side **actor filter** (All / Clients only / Walkers only / Admin only), **type filter**
(bookings / cancellations / availability), and pagination. Month dropdown runs back to the earliest
recorded activity.

### 8.2 Root cause — the audit table is dead

`BookingStatusChange` (`models.py:379`; table `booking_status_changes`, migration `421fe98dd8f0`,
relationship `Booking.status_history`) is purpose-built for this feed: `from_status`, `to_status`,
`changed_by_id` (**NOT NULL**), `notes`, `created_at` — a full per-transition trail naming the acting
user.

~~**It is never written. Zero constructor calls exist in the codebase; the table is always empty.**~~
Because the real transition log doesn't exist, the feed reconstructs an approximation from *current*
`Booking` state — which is why it cannot show confirmations / reassignments / slot changes and cannot
reliably attribute cancellations (§8.3–8.4).

> ✅ **RESOLVED (Session 1, PR #114).** Every transition now writes a `BookingStatusChange` row via the
> `app/utils/booking_status.py` chokepoint (§9.3); `batch_id` added (migration `6913631b986e`). The
> table is now populated with correct `from`/`to`/`changed_by_id`. **The feed itself still does not
> read it** — `activity_feed()` continues to reconstruct from current `Booking` state, so §8.3/§8.4
> remain open until the Session 4 rebuild (§9.6) switches the feed to union the log sources.

### 8.3 Attribution correctness ("who initiated")

| Feed source | Assigned actor | Correct? | Issue |
|---|---|---|---|
| Client booking | client | ✅ | — |
| Admin booking on behalf | admin (via `Booking.created_by_id`) | ✅ | the **only** path that gets admin attribution right |
| **Cancellation** | **always `client`** | ❌ | admin cancels, declines, and closure cancellations all show the *client* as actor + a "(by admin)" text suffix. There is no `cancelled_by_id` — only a `cancelled_by` enum string — so the feed can't name the admin |
| Walker self-service unavailability / adhoc | walker | ✅ | — |
| **Admin-created unavailability** (`admin_add_unavailability`) | **walker** | ❌ | `WalkerUnavailability`/`WalkerAdHocAvailability` have **no `created_by`** field; an admin action is attributed to the walker |

**Consequence — the "Admin only" filter is broken.** It matches only `actor_type == 'admin'`, which
today is *just* admin bookings-on-behalf. Admin cancellations, declines, closure cancellations, and
admin schedule changes are filed under "client" or "walker" and never appear under "Admin only".

### 8.4 Comprehensiveness — events with a notification but no feed entry

| Action | Notification? | In feed? |
|---|---|---|
| Admin confirms / assigns walker (`assign_walker`) | ✅ client + walker | ❌ — only the original creation row exists; its badge silently flips to the current status |
| Slot override (booking moved to a different slot) | ✅ `system` | ❌ |
| Admin edits walker weekly pattern (`schedule-json`) | ✅ clients | ❌ — edits `WalkerSchedule`, a table the feed never queries |
| Walker deactivated (`deactivate_walker`) | none | ❌ |
| Closure created (`add_closure`) | ✅ per-booking cancels | ⚠️ partial — the resulting cancellations appear (mis-attributed, §8.3); the closure itself is not an event |
| Broadcast sent (`broadcasts`) | ✅ clients | ❌ |
| Dog access revoked (`revoke-access`) | ✅ secondary | ❌ |
| Walk pickup / drop-off (`WalkEvent`) | none | ❌ — and the events aren't recorded at all (dead table, §7.5) |

### 8.5 Grouping consistency (feed vs notifications)

The feed is **uniformly per-row (individual)** and never groups.

- **Matches** the admin paths — `book_for_dog` / `recurring_for_dog` are individual in both feed and notifications. ✅
- **Diverges** from the grouped client/walker bulk paths: client `recurring_booking` (1 notification vs N feed rows), `book_both` (1 vs 2), `pause-walks` (1 admin notification vs N cancellation rows), walker `schedule_changes_batch` (1 vs N).

> This divergence is **arguably correct**: an audit feed *wants* per-row granularity, whereas
> notifications group to avoid spam (§6). The two have different jobs. Flagged so a redesign decides
> deliberately — e.g. keep feed rows granular but visually cluster bulk operations — rather than
> blindly "grouping the feed to match notifications."

### 8.6 Text consistency

- Shared vocabulary: both use the `'walk'` / `'drop-in'` service labels. ✅
- The feed uses a **verb-prefix** style ("Booked …", "Requested …", "Waitlisted for …", "Cancelled …")
  with the actor in a separate avatar/name column; notifications use full sentences ("… has been
  confirmed"). Same facts, different phrasing — a shared description helper (§8.2) would align them.
- **Badge shows current status, not status at the event's timestamp.** A booking created `requested`
  then later confirmed renders as "Booked" *at its creation time* — so the feed can misrepresent what
  happened when, and the confirmation itself has no row (§8.4).

---

# Part II — Target design & implementation plan

> **✅ FULLY IMPLEMENTED — all sessions (1, 2, 2b, 3, 4, 5) shipped to `develop` (PRs #114, #116,
> #117, #118, #119, #120).** This section is now a record of what was built and why, not a live
> work queue. The roadmap below is preserved for context; §9.8 has per-session shipped status.
>
> Original note: This half was a build plan, written to be handed to a future implementer with no
> other context. Sessions were independently shippable and ordered so each built on the last.

## 9.1 Target architecture

Two layers, sharing one set of action points:

1. **Action log (system of record).** Durable, append-only, **never user-capped**. Records *what
   happened and who did it*, with `from → to` and a timestamp. Built on the already-migrated
   `BookingStatusChange` table for bookings, plus existing availability / closure / walk-event tables.
   Drives `/admin/activity`.
2. **Notifications (alerts).** Emitted from the same action points, **grouped per recipient** for bulk
   actions, and capped (the bell is ephemeral). Drives bell + Web Push.

**Five load-bearing principles:**

- **P1 — One chokepoint for booking transitions.** All status changes route through
  `transition_booking()` (§9.3). This is why the §7 gaps can't recur: you cannot change a status
  without logging it and (optionally) notifying. *Not* an ORM `after_flush` listener — two sites use
  bulk `.update()` (`admin/routes.py:2440`, `:2529`) that bypass ORM events, and the listener can't see
  `current_user` reliably.
- **P2 — Attribution is recorded, never inferred.** The feed reads `changed_by_id` / `created_by_id`
  from the log. It must never guess the actor from row ownership (the current §8.3 bug).
- **P3 — One text source.** A single `summarise()` produces notification text *and* feed descriptions
  for a given event kind, so wording is identical regardless of who triggered it (§8.6, §7.7).
- **P4 — Group at emit time for notifications; keep rows granular for the log.** Notifications
  consolidate per recipient (one "5 walks booked" alert). The action log keeps one row per booking
  (audit needs granularity) and *visually* clusters via a `batch_id` stamped on all rows from one bulk
  action (**decided D4: collapsible clusters**, §9.6).
- **P5 — Don't cap the audit, do cap the bell.** Caps apply only to `Notification` (§9.7); the action
  log retains everything.

## 9.2 Schema changes

| Change | Table | Migration | Notes |
|---|---|---|---|
| *(none — already migrated)* | `booking_status_changes` | — | Model + table + `Booking.status_history` exist. Just start writing rows. |
| Add `created_by_id` (FK users, nullable, indexed) | `walker_unavailabilities` | new | Fixes §8.3 admin-on-behalf attribution. Backfill NULL = legacy/self-service. |
| Add `created_by_id` (FK users, nullable, indexed) | `walker_adhoc_availability` | new | Same. |
| ✅ Add `batch_id` (String(36), nullable, indexed) | `booking_status_changes` | **`6913631b986e` (Session 1, PR #114, landed)** | Correlates rows from one bulk action so the feed clusters them (decided D4). Generated once per bulk action (`uuid4().hex`), stamped on every BSC row it produces. Stamped by all bulk paths now; **not read yet** (feed clustering is Session 4/5). |

All schema work goes through Alembic (`flask db migrate` + commit the file); CI's `flask db upgrade` →
`flask db check` will fail otherwise. Backfill existing rows in the same migration (set `created_by_id`
NULL — interpreted as "self-service / unknown").

> ⚠️ **Migration gotcha (hit this session):** `flask db migrate` autogenerate against local **SQLite**
> injects spurious `modify_type` ops for `bookings.status` and `users.notification_preference`
> (VARCHAR→Enum — a SQLite-only artifact; they're native enums on Postgres and match). For these
> small additive migrations, scaffold with `flask db revision -m "..."` (no `--autogenerate`) and
> hand-write the `op.add_column` / index, or strip the spurious lines from an autogenerated file.
> Validate on a throwaway DB — `DATABASE_URL=sqlite:////tmp/x.db flask db upgrade` then `downgrade` —
> never the dev DB. See CLAUDE.md → Workflow Notes for the canonical version of this trap.

## 9.3 Core helpers (new code)

**`app/utils/booking_status.py`** — the transition chokepoint (P1):

```python
_UNSET = object()

def transition_booking(booking, to_status, *, actor_id, notes=None,
                       walker_id=_UNSET, batch_id=None):
    """Mutate a booking's status and append a BookingStatusChange row.
    Sets confirmed_at / cancelled_at / cancelled_by as implied by to_status.
    If walker_id is passed, updates it (None to unassign). Returns the BSC row.
    Caller still commits."""

def record_booking_created(booking, *, actor_id, batch_id=None):
    """First BSC row for a new booking: from_status=None, to_status=booking.status."""

def bulk_transition(bookings, to_status, *, actor_id, notes=None, batch_id=None):
    """Loop helper for the bulk sites — replaces the two raw .update() calls so
    each affected row gets a BSC. Returns the list of BSC rows."""
```

**`app/utils/notifications.py`** — grouping (P3, P4):

```python
class NotificationBatch:
    """Collects notification intents during one request, then emits ONE grouped
    notification per (recipient_id, kind) on flush(). Reused by every bulk path
    so client- and admin-initiated actions group identically."""
    def __init__(self, sender_id): ...
    def add(self, recipient_id, kind, **payload): ...   # kind e.g. 'booking_confirmed'
    def flush(self): ...   # groups, calls summarise(), calls create_notification()

def summarise(kind, payloads) -> tuple[title, body, ntype, link]:
    """Single source of notification text. 1 payload → existing single-item wording;
    N payloads → grouped wording ('Daisy's 5 walks confirmed'). Also exported for the
    feed so descriptions match (P3)."""
```

Existing inline-grouped client paths (`pause_walks`, `book_both`, `recurring_booking`) are refactored
onto `NotificationBatch` so the bespoke summarisation logic lives in one place.

## 9.4 Notification text & grouping spec

The canonical contract `summarise()` implements. (Recipient fan-out is orthogonal — every row applies
per recipient.)

| Event kind | Single-item text | Grouped (N) text | Type |
|---|---|---|---|
| `booking_confirmed` | "Daisy's morning walk on Mon 1 Jun confirmed" | "Daisy's 5 walks confirmed (Mon 1 – Fri 5 Jun)" | `booking_confirmed` |
| `booking_requested` | "Daisy's morning walk on Mon 1 Jun requested" | "Daisy's 5 walks requested" | `booking_requested` |
| `booking_waitlisted` | "…on the waitlist" | "3 walks waitlisted" | `booking_requested` |
| `booking_cancelled` | "…cancelled" | "5 walks cancelled (Mon 1 – Fri 5 Jun)" | `booking_cancelled` |
| `booking_reset` *(new)* | "Daisy's Mon 1 Jun walk needs a new walker" | "3 of your walks are being reassigned" | `system` |
| `walker_assigned` | "Assigned a walk on 1 Jun — Daisy, Morning" | "Assigned 5 walks (1–5 Jun)" | `walker_assigned` |

## 9.5 Closing the gaps — §7/§8 → change map

| Finding | Fix | Session |
|---|---|---|
| §7.1 walker self-service reset is silent to clients | `transition_booking` reset → emit `booking_reset` (grouped per client) | 3 |
| §7.2 `deactivate_walker` / `admin_add_unavailability` reset, notify nobody | same — both routed through `bulk_transition` + batch notify | 3 |
| §7.3 admin pending bookings never reach client | notify client on requested/waitlisted (not just confirmed) in `book_for_dog`/`recurring_for_dog` | 2 |
| §7.4 closures & bulk-cancel skip co-owners + walker | expand recipients (co-owners, ex-walker) in `add_closure`, `dog_bulk_cancel` | 3 |
| §7.5 walk pickup/drop-off not recorded (dead `WalkEvent` table) | **out of scope** (decided D2) — recording pickup/drop-off is a separate prerequisite feature; revisit notifications once events exist | — |
| §7.6 dead `dental_*` types | delete from `NOTIFICATION_META` | 2 |
| §7.7 wording inconsistency | route walker text through `summarise()` (`'walk'`/`'drop-in'`) | 2 |
| §7.8 admin bulk grouping inconsistent | `NotificationBatch` in `recurring_for_dog`, `book_for_dog`, `add_closure`, `dog_bulk_cancel` | 2 |
| §7.9 client bookings don't notify walker | **decided D3: yes** — notify the walker (grouped) on auto-assign, matching admin assignment | 3 |
| §8.2 `BookingStatusChange` never written | ✅ **DONE (PR #114)** — written via `transition_booking` everywhere | 1 |
| §8.3 cancellations/admin-unavail mis-attributed | feed reads `changed_by_id`/`created_by_id` | 4 |
| §8.4 missing events (confirm, slot move, schedule edit, closure, broadcast) | feed unions the log sources (walk events excluded — not recorded, §7.5) | 4 |
| §8.5 feed/notif grouping mismatch | keep feed rows granular; `batch_id` collapses bulk actions into one expandable row (decided D4) | 4–5 |
| §8.6 badge = current status, not transition | feed badge from `to_status` of the BSC row | 4 |

## 9.6 Activity feed rebuild (§8 → robust action log)

Rewrite `activity_feed()` (`admin/routes.py:1493`) to **union the action-log sources** instead of
reconstructing from current state:

- `BookingStatusChange` — every booking event. Actor = `changed_by`; badge = `to_status`. Covers
  creation, confirm, reject, cancel, slot-change (recorded as a `"slot X → Y"` BSC note and rendered
  "Moved … to <slot>" — F6), reset/reassign.
- `WalkerUnavailability` / `WalkerAdHocAvailability` — actor = `created_by_id` if set, else the walker.
- `Closure` — "DogBoxx closed on <date>" event (actor = `created_by_id`, already on the model).
- `Broadcast` — "broadcast sent to N clients" (actor = `sender_id`).
- *(future)* `WalkEvent` — pickup / drop-off, **once recording is built** (§7.5, D2). Not a source today.

> **Feed phrasing vs `summarise()` (F7, decided):** an earlier draft said feed descriptions come from
> `summarise()`. In the shipped code the feed **deliberately builds its own verb-prefix descriptions**
> ("Confirmed …", "Cancelled …", "Moved … to <slot>") rather than calling `summarise()` (which produces
> recipient-facing full sentences, "… confirmed"). The two text systems are intentionally separate:
> `summarise()` owns *notification* wording (P3), the feed owns *audit* wording. A future editor changing
> notification text should know it will **not** change the feed, and vice-versa.

Then: actor filter ("Admin only" now correct, P2); `batch_id` collapses a bulk action into one
expandable row (decided D4); paginate the union (it can be large — keep the month scope, index
`created_at` on each source — done, F4). Per-booking history is now also available via
`Booking.status_history` for a future booking-detail timeline.

## 9.7 Notification caps review

Current: `NOTIF_DB_CAP=50`, `NOTIF_PAGE_CAP=20`, `NOTIF_BELL_CAP=5` (`notifications.py:22-24`).

- The **action log is uncapped** (P5) — full history now lives there, so the bell no longer needs to.
- Grouping (§9.4) cuts volume sharply, especially for admins (the heaviest recipients via fan-out).
- **Decided (D1):** bell dropdown **5** (unchanged); page **20 → 50**; DB store **50 → 100**. *Not*
  role-aware — a single flat cap for all users; admins rely on the action-log feed for deep history.
  No migration — these are constants in `notifications.py:22-24`.

## 9.8 Session breakdown (handoff checklist)

Each session is one PR to `develop`, green CI, independently shippable.

**✅ Session 1 — Action-log foundation (no behaviour change). DONE — PR #114, merged to `develop`.**
- ✅ Migration `6913631b986e`: added `batch_id` (String(36), nullable, indexed) to `booking_status_changes` (D4).
- ✅ Added `app/utils/booking_status.py` (`transition_booking`, `record_booking_created`, `bulk_transition`);
  bulk paths generate one `batch_id` (`uuid4().hex`) and pass it to every row. `transition_booking`/
  `bulk_transition` also take an explicit `cancelled_by` kwarg (client vs admin — not derivable from status).
- ✅ Refactored all transition sites. Sites: creation — `client/routes.py` index POST, `book`,
  `book_both`, `book_drop_in`, `recurring_booking`; `admin/routes.py` `book_for_dog`, `recurring_for_dog`.
  Confirm/assign — `_maybe_auto_confirm`, `assign_walker`. Reject — `decline_booking`. Cancel —
  `cancel_booking`, `pause_walks`, `dog_bulk_cancel`, `add_closure`. Reset — `add_unavailability`,
  `schedule_changes_batch`, `walker_schedule_json`, `admin_add_unavailability`, plus **both** raw
  `.update()` calls (`remove_walker_role` *and* `deactivate_walker`) converted to `bulk_transition`.
  - Note: `book_for_dog`/`recurring_for_dog` previously created rows at a placeholder `'waitlisted'`
    overwritten before flush — now compute the *resolved* initial status (`waitlisted` only if full,
    else `requested`) so the log shows the real created→confirmed path, not a phantom transition.
- ✅ **DoD met:** every transition writes one BSC row with correct `from`/`to`/`changed_by_id`; notifications
  unchanged; 274 tests pass on Postgres CI. New `tests/test_booking_status_log.py` (helper unit tests +
  per-transition route wiring); extended `test_admin_assign_walker.py`, `test_walker_schedule_modal_reset.py`,
  `test_admin_bulk_cancel.py` to assert BSC rows + actor + shared `batch_id`.
- **Carried into later sessions:** the feed still reconstructs from current `Booking` state — it does
  **not** read the log yet (Session 4, §9.6). `batch_id` is stamped but unread (Session 4/5 clustering).

**Session 2 — Notification grouping + text unification. ✅ SHIPPED (PR #116, merged to `develop`). Client-path convergence carried to follow-up.**
- ✅ Added `NotificationBatch` + `summarise()` in `app/utils/notifications.py` — the single text source
  (§9.4). Reflexive owner wording by default; **actor-prefixed when `actor_first` is passed** (decided
  this session: keep "Lydia booked …" for admin-on-behalf / fan-out rather than fully reflexive, so the
  client still sees *who* acted — aligns with the Issue #109 attribution want). `summarise()` returns
  `(title, body, ntype, link)`; `'booking_waitlisted'` styles as `booking_requested`.
- ✅ Migrated the four admin paths to grouped emit (§7.8): `recurring_for_dog`, `book_for_dog`,
  `add_closure`, `dog_bulk_cancel`.
- ✅ Fixed §7.3 (`book_for_dog`/`recurring_for_dog` now notify the client on **pending and waitlisted**,
  not only confirmed) and §7.7 (walker text uses canonical `walk`/`drop-in` via `summarise`, not
  `service.name.lower()`). §7.6 (`dental_*`) was **already clean** — `NOTIFICATION_META` has no such keys.
- **⏳ DEFERRED to a follow-up PR (decided this session):** refactoring the *client* paths
  (`pause_walks`, `book_both`, `recurring_booking`) onto the shared helper. They already group per
  recipient; routing them through `summarise()` would change their (working) client-facing wording, so
  it was split out to keep this PR's risk to the admin side. Until then, admin- and client-initiated
  recurring wording differ slightly (admin: canonical grouped form; client: existing bespoke form).
- **Tests:** new `tests/test_notification_grouping.py` (24 cases — `summarise()` wording matrix +
  `NotificationBatch` grouping incl. the "5 walks → 1 notification" DoD). Existing `test_bookings.py`
  admin recurring/book-for-dog route tests exercise the migrated wiring end-to-end. Full SQLite suite
  green; Postgres via CI.
- **Carried:** client-path convergence shipped separately (see below).

**Session 2b — Client-path convergence. ✅ SHIPPED (PR #120, merged to `develop`).**
- ✅ `pause_walks`: replaced three bespoke notification blocks (admin, co-owner, walker) with a single
  `NotificationBatch` built before `bulk_transition` (to capture walker IDs). All recipients now get
  canonical `booking_cancelled` text from `summarise()` with `actor_first` set.
- ✅ `book_both`: admin notifications migrated to `NotificationBatch` + `summarise()`. Same-day pending
  slots still emit a `same_day_request` notification separately (no kind in `summarise()` for this).
  `_summarise_book_both_for_client` rewritten to call `summarise()` for pure-outcome cases; mixed
  outcomes and the 2-slot body ("morning and afternoon both booked.") remain bespoke.
- ✅ `recurring_booking`: client + admin notifications migrated to `NotificationBatch`. Loop now also
  tracks `pending_bookings` (alongside existing `confirmed_bookings`) so both outcome groups are
  available without a second query. Client gets one notice per outcome kind; admins only notified
  when bookings remain pending.
- **Tests:** 317 pass. (The pre-existing Tuesday date-ordering bug in
  `test_admin_bulk_cancel.py::TestBulkCancelDayFilter::test_only_filtered_weekday_cancelled` was fixed
  separately in PR #119 as part of Session 5 CI fixes.)

**Session 3 — Close reset/recipient gaps. ✅ SHIPPED (PR #117, merged to `develop`).**
- ✅ Added `booking_reset` kind to `summarise()` — single: "Daisy's Mon 1 Jun walk needs a new walker";
  grouped: "N of your walks are being reassigned"; type `system`.
- ✅ All five reset paths now emit grouped `booking_reset` per affected client (§7.1, §7.2):
  `add_unavailability` and `schedule_changes_batch` (walker self-service, previously admins-only);
  `walker_schedule_json` (admin, migrated from bespoke per-client count text to `NotificationBatch`);
  `admin_add_unavailability` and `deactivate_walker` (admin, previously silent to everyone).
- ✅ Closure + bulk-cancel recipient expansion (§7.4): `add_closure` now fans out to co-owners and the
  assigned walker; `dog_bulk_cancel` now notifies the ex-walker.
- ✅ Client-booking auto-assign notifies the walker (§7.9, D3): `_maybe_auto_confirm` (notify=True),
  `book_both`, and `recurring_booking` all send grouped `walker_assigned` to the auto-assigned walker.
- ✅ Fixed pre-existing bug in `recurring_booking`: client/admin notifications were flushed but never
  committed (rolled back on teardown). Commit moved to after all notifications.
- **Tests:** `test_walker_schedule_changes.py` extended (single unavail + batch → client `booking_reset`);
  `test_walker_schedule_modal_reset.py` updated for new wording; new cases in `test_admin_bulk_cancel.py`
  (walker fan-out); new `test_closures.py` (co-owner + walker fan-out, no double-notify for admin-as-walker).
  304 tests pass.
- **Known gap (not in DoD):** no dedicated test for walker auto-assign notification in `_maybe_auto_confirm`
  / `book_both` / `recurring_booking` — routes are exercised end-to-end but walker notification count
  not asserted. Low risk; can be covered in a future pass.

**Session 4 — Activity feed → action log. ✅ SHIPPED (PR #118, merged to `develop`).**
- ✅ Migration `8f826da874ff`: `created_by_id` on `walker_unavailabilities` + `walker_adhoc_availability`.
  Set in `admin_add_unavailability`, `add_unavailability`, `schedule_changes_batch`.
- ✅ Rewrote `activity_feed()` to union BSC, WalkerUnavailability, WalkerAdHocAvailability, Closure,
  Broadcast sources. Actor from log FK (P2); badge from `to_status` (§8.6). Admin-filter now correct.
- **Tests:** `test_activity_feed.py` — 10 cases covering event presence, actor attribution,
  admin-filter correctness, badge = transition. 314 tests pass.

**Session 5 — Caps + feed clustering (polish). ✅ SHIPPED (PR #119, merged to `develop`).**
- ✅ Caps (D1): `NOTIF_DB_CAP` 50→100, `NOTIF_PAGE_CAP` 20→50, bell unchanged. JS `PAGE_SIZE` updated.
- ✅ Feed clustering (D4): `_cluster_events()` groups BSC rows by `batch_id` into collapsible cluster
  rows with chevron toggle and child row expansion. Cluster summary built from child payloads.
- **Tests:** cap-pruning test; cluster HTML presence test; plain-row (no batch_id) test. 317 tests pass.
- *(Out of scope, D2):* walk-event recording + notifications — a separate feature; the `WalkEvent` table
  is currently dead (§7.5).

## 9.9 Decisions

- **D1 — Caps → DECIDED: bump.** Page 20→50, DB store 50→100, bell 5 (unchanged). Flat for all users,
  not role-aware; admins use the action-log feed for deep history. (§9.7)
- **D2 — Walk-event notifications → DECIDED: out of scope.** Pickup/drop-off events **aren't recorded**.
  The speculative `WalkEvent` model + table were removed in PR #113 (migration `b40f4de664d4`, §7.5).
  Recording them (a walker UI + writes + a fresh table) is a separate prerequisite feature; notifications
  are revisited only once the events exist. Removed from Sessions 4 & 5.
- **D3 — Walker auto-assign notification → DECIDED: yes.** Client-booking auto-assign sends the walker a
  grouped `walker_assigned`, matching admin assignment. (§7.9, Session 3)
- **D4 — Feed clustering / `batch_id` → DECIDED: collapsible clusters.** `batch_id` on
  `booking_status_changes` (Session 1 migration); the feed collapses a bulk action into one expandable
  row (Session 5). (§9.6)
- **D5 — Backfill → DECIDED: start fresh, log-only, no legacy fallback** (confirmed 2026-06-02). The
  action log begins empty and records only transitions from rollout forward. **The hybrid-by-month
  fallback originally sketched here was deliberately dropped** to keep the feed simple: there is one code
  path (read the log) and no current-state reconstruction. The trade-off, accepted: **months before
  rollout render blank/sparse** in the feed (the bookings still exist — they're just not shown as feed
  events). This is intended, not a bug. ~~The feed is hybrid by month: read the log for months ≥ rollout,
  fall back to the existing current-state reconstruction for earlier months.~~

  *Why not backfill.* What we can recover from each existing `Booking`: `created_at`, `confirmed_at`,
  `cancelled_at`, `cancelled_by` (role only), `created_by_id`. Three hard limits make a full backfill
  lossy and dishonest:
  1. **`BookingStatusChange.changed_by_id` is `NOT NULL`** — every backfilled row needs an actor, but
     there is **no historical actor** for confirmations/assignments (no `confirmed_by`/`assigned_by`
     field ever existed) and only a *role* (`'client'`/`'admin'`), not a user, for cancellations. A
     backfill must therefore **fabricate** actors — polluting the log's most valuable column with fiction
     that can't be told apart from real rows.
  2. **Only coarse timestamps survive** — all reset/reassign churn is gone, so reconstructed history
     looks *cleaner than reality* (arguably worse than an honest gap).
  3. It's a **one-shot migration against live production data** — extra risk + tests for history nobody
     scrolls back to often.

  Start-fresh keeps the log 100% trustworthy (real actors only) and avoids the risky migration. Past
  months simply show no feed events (decided acceptable — see above); the feed becomes fully populated as
  rollout scrolls into the past.

  *What would change this → minimal backfill.* If per-booking history on a future booking-detail page is
  wanted for old bookings, do a **creation-rows-only** backfill (actor = `created_by_id`, else
  `user_id` — both real) and **skip** confirm/cancel rows rather than fabricate actors. Populates
  `Booking.status_history` honestly; old bookings just show a "created" event.

---

# Part III — Post-implementation code review (2026-06-02)

> Independent audit of the shipped code on `develop` (`083f4dc`) against the Part II spec. Not a
> re-implementation — a verification pass for spec-conformance, bugs, logic errors, and efficiency.
> **Reviewer method below is reproducible; line numbers are as of `083f4dc`.**

## 10. What was verified (and how)

- **P1 invariant (one chokepoint).** Swept the whole `app/` tree for `booking.status = …`, direct
  `confirmed_at`/`cancelled_at`/`cancelled_by` writes, and bulk `.update()` touching status. **Result:
  zero bypasses** — the only `.status =` hit is a docstring; the only `.update()` calls are on
  `WalkerSchedule.active` and `Notification.read_at`. Every status mutation routes through
  `app/utils/booking_status.py`. ✅
- **Creation coverage.** All 7 production `Booking(...)` sites (`client` 432/572/716/922/1978, `admin`
  3305/3501) are each paired with `record_booking_created()`. No booking can exist without a creation
  row in the log. ✅ (8th constructor is the seeder — out of scope.)
- **Actor non-null.** `BookingStatusChange.changed_by_id` is `NOT NULL`; every one of the ~30 call
  sites passes `current_user.id`. No system/anonymous transition can violate the constraint. ✅
- **Migrations.** `6913631b986e` (batch_id) and `8f826da874ff` (created_by_id ×2) reviewed; full chain
  `upgrade` from scratch + `downgrade` of both new revisions + re-`upgrade` all succeed on a throwaway
  SQLite DB; single head. Model `index=True` matches the migration indexes (no `flask db check` drift). ✅
- **Tests.** Full suite **317 passed** locally (`USE_SQLITE=1`); the three session-added suites
  (`test_booking_status_log`, `test_notification_grouping`, `test_activity_feed`) = 54 passed. CI runs
  the same on Postgres. ✅
- **Spec conformance.** `summarise()` wording cross-checked against the §9.4 matrix; gap-closure map
  (§9.5) checked site-by-site; feed rewrite checked against §9.6 (P2 attribution from FKs, badge from
  `to_status`, log-union sources, clustering UI, caps). Mostly conformant — deviations below.

**Overall:** the redesign is implemented soundly and the load-bearing invariant (P1) is fully intact.
Findings are a small set of edge-case/polish items plus two that merit a decision. No data-integrity or
security issues found.

## 11. Findings

| # | Severity | Area | Summary |
|---|---|---|---|
| F1 | ~~Medium~~ **Fixed** | §7.2 reset | `remove_walker_role` resets confirmed bookings silently — no `booking_reset` to clients. **Fixed on `feature/notif-review-fixes`.** |
| F2 | ~~Medium~~ **Resolved** | §9.6 / D5 | Feed has no hybrid legacy fallback — pre-rollout months render blank. **Decided 2026-06-02: intended (log-only, keep it simple).** D5 reconciled. |
| F3 | ~~Low~~ **Fixed** | §3 book_both | Same-day `book_both` double-notifies admins (grouped `booking_requested` **and** `same_day_request`). **Fixed on `feature/notif-review-fixes`.** |
| F4 | ~~Low~~ **Fixed** | §9.6 perf | `created_at` not indexed on BSC / unavail / adhoc / closure — feed month-range scans them. **Fixed (migration `f67a2a1712ad`).** |
| F5 | ~~Low~~ **Fixed** | §9.4 text | Grouped `booking_reset` hard-codes "walks" — drop-in resets are mislabelled. **Fixed on `feature/notif-review-fixes`.** |
| F6 | ~~Low~~ **Fixed** | §9.6 log | Slot-override writes a `confirmed→confirmed` BSC with no `notes` — feed can't tell a slot move from a re-confirm. **Fixed: BSC note + "Moved … to <slot>" feed row.** |
| F7 | ~~Low~~ **Resolved** | P3 | Feed builds its own row descriptions instead of `summarise()`. **Intentional (audit wording ≠ notification wording); §9.6 clarified.** |
| O1–O4 | Obs. | — | Non-blocking observations (below) |

### F1 — `remove_walker_role` resets bookings silently (Medium)
`admin/routes.py:2580`. Converting a dual-role user from walker→client deactivates their schedule and
resets future confirmed bookings to `requested` via `bulk_transition` (`:2606`) — **but emits no
`booking_reset` notification**. This is the **exact §7.2 silent-revert gap** Session 3 closed for the
structurally-identical `deactivate_walker` (`:2674`, which *does* notify, `:2703–2709`).
`remove_walker_role` was never in the §7 audit enumeration, so its fix was never written. A client's
confirmed walk flips to pending with no word. CLAUDE.md's own rule — *"any new path that removes walker
availability must do both the reset and the `booking_reset` notification"* — is violated here.
**Fix:** mirror `deactivate_walker`'s `NotificationBatch` block (capture `affected`, add one grouped
`booking_reset` per `b.user_id`, flush before commit).

### F2 — Activity feed has no legacy hybrid fallback; pre-rollout months are blank (Medium)
`activity_feed()` (`admin/routes.py:1498`) reads **only** the action log (BSC + availability + closure +
broadcast). D5 (§9.9) specified the feed be **"hybrid by month: read the log for months ≥ rollout, fall
back to the existing current-state reconstruction for earlier months (so past history isn't blank)."**
That fallback is **not implemented.** Because the log starts fresh (no backfill, by design), any month
before the Session 1 deploy shows only events logged *after* rollout — i.e. near-empty for historical
months where the old feed reconstructed bookings from current state. **This is a user-visible regression
for historical browsing** (the data still exists; the feed just stops showing it). Either (a) it was a
deliberate simplification — in which case update D5 to say so and drop the now-stale "hybrid" wording —
or (b) it's an oversight and the legacy reconstruction should be retained for `month < rollout`.

> **RESOLVED (2026-06-02):** intentional — log-only, no fallback, to keep the feed to a single code
> path. Pre-rollout blank months are accepted. D5 and §9.6 wording reconciled accordingly. *Optional
> future polish (not requested):* an empty-state note on the feed for old months ("Detailed activity
> began <date>") so an admin isn't misled into thinking nothing happened.

### F3 — Same-day `book_both` double-notifies admins (Low)
`client/routes.py:763–793`. The admin `NotificationBatch` loop (`:765`) adds every created slot — and
for a same-day request those slots are status `requested`, so each is added as `booking_requested` and
`flush()` (`:783`) emits one grouped notice. The same-day block (`:784–793`) then sends an *additional*
`same_day_request` for the same slots. Admins get **two** notifications for one same-day book-both. The
comment at `:779` says "override notification_type" but the code *adds* rather than replaces. (The
single-slot `book` path correctly emits only `same_day_request`.) **Fix:** when `same_day`, skip adding
the pending slots to `admin_batch` (emit only the `same_day_request`), or drop the separate
`same_day_request` and let the grouped notice carry the urgency.

### F4 — Feed source `created_at` columns are unindexed (Low, perf)
§9.6 called for indexing `created_at` "on each source." `Broadcast.sent_at` is indexed, but
`BookingStatusChange.created_at` (the highest-volume source — one row per transition),
`WalkerUnavailability.created_at`, `WalkerAdHocAvailability.created_at`, and `Closure.created_at` are
**not** (`models.py` 394/424/452/584). The feed filters all of them by month range, so each is a
sequential scan. Harmless at current volume (~50 clients) but it's a stated spec item and BSC grows
fastest. **Fix:** add `index=True` to `BookingStatusChange.created_at` (at minimum) + a migration.

> **FIXED** (`feature/notif-review-fixes`): `index=True` added to `created_at` on all four sources
> (`models.py`); migration `f67a2a1712ad` creates `ix_<table>_created_at` on each, validated
> upgrade→downgrade→re-upgrade on a throwaway DB. No autogenerate drift (model names match).

### F5 — Grouped `booking_reset` mislabels drop-ins as "walks" (Low)
`notifications.py:270` — the grouped branch hard-codes `_plural('walk', n)` ("N of your walks are being
reassigned") and ignores `svc_label`, even though every reset caller passes `svc_label='drop-in'` for
drop-ins. The single-item branch (`:267`) uses `svc` correctly. **Fix:** use the resolved `svc` in the
grouped string, matching the single-item path.

### F6 — Slot-override leaves no distinct trace in the log (Low)
`assign_walker` (`admin/routes.py:1286`) calls `transition_booking(..., 'confirmed')` then sets
`booking.slot = slot` (`:1289`) — with no `notes`. For an already-confirmed booking this writes a
`confirmed→confirmed` BSC row indistinguishable from a plain re-confirm; the feed can't show that a slot
move happened (§9.6 lists "slot-change (note)" as something the log should cover). The client *is*
notified (`system` notice), so this is a log-fidelity gap, not a silent change. **Fix:** pass
`notes=f"slot {old_slot}→{slot}"` on the override branch and surface it in the feed description.

> **FIXED** (`feature/notif-review-fixes`): `assign_walker` now passes `notes="slot X → Y"` to
> `transition_booking` on a slot override (and the duplicated `slot_was_changed` computation was
> consolidated); the feed renders such rows as "Moved … to <slot>". Regression test in
> `test_activity_feed.py::test_slot_override_renders_as_moved`.

### F7 — Feed descriptions bypass `summarise()` (Low, P3 partial)
P3 ("one text source") aimed for `summarise()` to drive *both* notifications and feed descriptions so
wording can't drift (§8.6). In practice the feed hand-builds its own verb-prefix strings
(`admin/routes.py:1682–1704` for rows, `:1636` for clusters) and never calls `summarise()`. This is a
defensible UX choice (feed verb-prefix vs notification full-sentence), but it means the §8.6 drift the
principle set out to eliminate can still occur — the two text systems are independent. **No action
required if intentional**; worth a one-line note in §9.6 that the feed deliberately keeps its own
phrasing, so a future editor doesn't assume `summarise()` covers it.

> **RESOLVED** (`feature/notif-review-fixes`): confirmed intentional — the feed owns *audit* wording
> (verb-prefix), `summarise()` owns *notification* wording (full sentences). §9.6 now states this
> explicitly (and corrects the stale "description via `summarise()`" line) so the two text systems
> aren't mistaken for one. No code change.

### Observations (non-blocking)
- **O1 — `confirmed_at` not cleared on reset.** `transition_booking` sets `confirmed_at` on
  →confirmed but never clears it on confirmed→requested, so a reset booking keeps a stale
  `confirmed_at`. **Verified harmless:** no code reads `confirmed_at` as a "currently confirmed" signal
  (only `to_dict` exposes it). The field now means "last confirmed at." Fine to leave; document if it
  ever feeds reporting.
- **O2 — N+1 in bulk recipient loops.** `pause_walks`, `dog_bulk_cancel`, and `add_closure` issue a
  `DogOwner.query` per booking and a `db.session.get(User)` per co-owner inside the loop. Negligible at
  current scale; if these ever run over large ranges, batch the owner/user lookups (the
  `client_detail` route already demonstrates the batched pattern).
- **O3 — Same-day `book_both` client wording.** `_summarise_book_both_for_client` routes same-day
  requests through the generic `booking_requested` branch, dropping the "Lydia will confirm shortly"
  same-day nuance the single-slot path keeps. Cosmetic.
- **O4 — §9.4 wording deltas (improvements).** Grouped `booking_requested`/`booking_waitlisted` titles
  include a date span (`(Mon 1 – Fri 5 Jun)`) the spec's matrix omits. This is better, not a defect —
  noted only so the matrix and code are known to differ intentionally.

## 12. Recommended action order
1. ~~**F1**~~ — **fixed** on `feature/notif-review-fixes`: `remove_walker_role` now emits a grouped
   `booking_reset` per affected client (parity with `deactivate_walker`). + regression test.
2. ~~**F2**~~ — **resolved 2026-06-02:** log-only is intended; doc reconciled. No code change.
3. ~~**F3, F5**~~ — **fixed** on `feature/notif-review-fixes`: same-day `book_both` no longer
   double-notifies admins (emits only `same_day_request`); grouped `booking_reset` uses the real
   service label. + regression tests.
4. ~~**F4**~~ — **fixed** on `feature/notif-review-fixes`: `created_at` indexed on all four feed
   sources (migration `f67a2a1712ad`).
5. ~~**F6, F7**~~ — **done** on `feature/notif-review-fixes`: F6 records the slot move in the BSC note
   and renders "Moved … to <slot>"; F7 confirmed intentional and §9.6 clarified.

**All review findings are now resolved** (F2/F7 by decision, F1/F3/F4/F5/F6 by fix). Remaining genuine
backlog from the audit is only the explicitly out-of-scope items (D2 walk-event recording).
