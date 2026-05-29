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

### 7.5 Walk pickup/drop-off events produce no notifications
`WalkEvent` rows (walker-recorded pickup/drop-off) create **zero** notifications. Clients get nothing
when their dog is collected or dropped home — a likely-desirable addition.

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

**It is never written. Zero constructor calls exist in the codebase; the table is always empty.**
Because the real transition log doesn't exist, the feed reconstructs an approximation from *current*
`Booking` state — which is why it cannot show confirmations / reassignments / slot changes and cannot
reliably attribute cancellations (§8.3–8.4).

> ⚠️ **Contradicts CLAUDE.md**, which describes `BookingStatusChange` as "Audit-trail row for each
> booking status transition (who, when, from → to)." Nothing populates it. Wiring it up at every
> transition (it already carries `changed_by_id`) and driving the feed from it would fix §8.3 and
> §8.4 at the source — and let the feed and notifications share one description helper for text.

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
| Walk pickup / drop-off (`WalkEvent`) | none | ❌ |

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
