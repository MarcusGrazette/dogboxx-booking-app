# Feature Tracker — Dogboxx

> Priority: **P1** = must-have, **P2** = should-have, **P3** = nice-to-have
> Effort: **S** = small (< 1hr), **M** = medium (1-4hrs), **L** = large (4hrs+)
> Status: 🔲 todo · 🔧 in progress · ✅ done · ❌ dropped

## Core Booking Flow

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 1 | P1 | L | ✅ | **Booking workflow with capacity checks** | Walker availability × max_per_walker. Waitlist when full. Auto-assigns least-loaded walker on creation — confirms immediately if capacity available. |
| 2 | P1 | M | ✅ | **Recurring bookings (client)** | Start/end date + frequency (daily/weekly). Server expands to individual bookings. Skips weekends, duplicates. |
| 3 | P1 | M | ✅ | **Admin booking board** | Calendar + slot view. Confirm/cancel requests. Drag-to-reorder pickup order per walker. |
| 4 | P1 | M | ✅ | **Admin dogs view** | Searchable table of all dogs. Book on owner's behalf (one-off or recurring) via modal. Same pending flow as client-initiated. |
| 5 | P1 | M | ✅ | **Prevent duplicate bookings** | Block same dog+date+slot. Max 2 bookings per dog per day (one per slot). DB partial unique index. |
| 6 | P1 | M | ✅ | **Password reset** | "Forgot password?" → token email → set new password. Resend API, itsdangerous token (1hr expiry, invalidated on use). |
| 7 | P1 | M | ❌ | **Booking capacity display for clients** | Dropped — "available" indicator in UI is sufficient. Exact slot counts not needed. |
| 8 | P2 | L | ✅ | **"Book both walks" option (client)** | Checkbox books AM + PM in one action via /client/book_both. Admin board shows layered-icon modifier pill. |

## Walker

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 10 | P1 | M | ✅ | **Walker pickup list** | Daily route view with dog photo, owner, address, pickup instructions, ordered by pickup_order. Date navigation. |
| 11 | P2 | M | ✅ | **Walker schedule management** | Default weekly schedule (day + slot). Admin sets. Walker can view. |
| 12 | P2 | M | ✅ | **Walker unavailability** | Date-specific exceptions (per slot). Admin marks unavailability. Reduces capacity for that slot automatically. |
| 13 | P3 | L | ✅ | **Walker self-manage availability** | Walkers flag their own exceptions (holidays, sick days) rather than admin doing it. |
| 14 | P3 | S | ✅ | **Google Maps pickup directions** | Maps button on each pickup card in /walker/pickups. |
| 15 | P2 | M | ✅ | **Walker ad hoc available days** | Walkers can add one-off available days outside their default schedule. Inverse of the existing unavailability model. New `walker_adhoc_availability` table (or reuse existing with a flag). Capacity checks need updating to include these. |
| 16 | P2 | M | ✅ | **Admin override walker unavailability on allocation board** | Admin can assign dogs to a slot even if walker has marked themselves unavailable. Override shown visually (warning state). Booking creation bypasses the unavailability block when admin-initiated. |

## Admin

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 20 | P1 | M | ✅ | **Client management** | Create, view, edit client accounts. Notification audit trail per client. |
| 21 | P1 | M | ✅ | **Walker management** | Create walkers. Set/edit default schedule. Mark unavailability. |
| 22 | P2 | S | ✅ | **Admin is also a walker** | is_admin flag on User. Admin can be a walker. "My Pickup List" in admin sidebar. |
| 23 | P2 | M | ✅ | **Admin dashboard stats** | Stat cards (pending, clients, dogs, walkers), 4-week booking chart by slot+status, walker availability grid. Revenue on /admin/revenue. |
| 24 | P3 | L | 🔲 | **Dental cleans service type** | Admin: manage available date+time slots. Client: book from available slots. Nav stub removed — add back when built. Premature `dental_confirmed`/`dental_available` notification types were removed (2026-05-29) — re-add when built. |
| 25 | P3 | L | ❌ | **Invoicing (standalone)** | Superseded by #26. |
| 26 | P2 | L | ✅ | **Invoicing view (admin)** | /admin/invoicing: monthly summary per client. /admin/invoicing/<id>: line items + weekly breakdown. Billable cancels (<5 days notice), double-slot discount, drop-in pricing. PricingConfig history. |
| 27 | P2 | L | ✅ | **Multiple clients per dog** | dog_owners join table with primary/secondary roles. Admin join/revoke modal. Secondary owners can book and view shared dogs. |
| 29 | P2 | L | ✅ | **Drop-in service type** | Client books AM/PM drop-in visits. Admin drop-in board (assign walkers, confirm/cancel, reorder). Walker pickup list includes drop-ins. Invoicing tracks drop-ins separately at price_per_drop_in. does_drop_ins flag on walkers. |
| 28 | P3 | M | ✅ | **CSV client/dog import** | Upload CSV matching the create-client form fields. Bulk create client + dog records. Validation with error report on bad rows. No need to handle joined accounts — those are done manually post-import. |
| 35 | P2 | M | ✅ | **Admin bulk booking operations** | Bulk-cancel upcoming bookings for a dog from `/admin/dogs` view (with preview modal showing dates). Recurring create on admin's behalf from the same view. Day-of-week filter on pause-walks. |
| 36 | P2 | M | ✅ | **Closures** | Admin marks a date as closed — auto-cancels active bookings with client notifications. Prevents new bookings on closed dates. Preview endpoint before confirming. |
| 37 | P3 | S | ✅ | **Daily walker messages** | Admin-authored announcements shown to walkers on the pickup list for a given date. Auto-purge of old messages. |
| 38 | P2 | L | ✅ | **Admin broadcasts** | One-shot message to all clients booked on a chosen date + slot scope (all / morning / afternoon). Bell + email delivery. Recipients resolved at send time from confirmed bookings (primary + secondary co-owners). Broadcast bar shown on assignment board. Audit rows kept. PRs #99–102. |

## Client

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 30 | P1 | M | ✅ | **Client onboarding** | Manual address entry + optional Google Maps pin URL (Places autocomplete removed), pickup instructions, dog profile. |
| 31 | P1 | M | ✅ | **Client profile edit** | Edit address, notification prefs, dog details + photo. |
| 32 | P2 | M | ✅ | **Monthly walk summary** | Client-facing summary of walks taken and any outstanding items. |
| 34 | P3 | M | ✅ | **Multi-dog support (admin)** | Admin can add a second (or subsequent) primary dog to a client via modal on the client detail page. Data model and booking flow already supported multiple dogs. Client self-service out of scope. |

## Newsletter

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 60 | P2 | L | ✅ | **Client newsletter (admin)** | `/admin/newsletter` — Quill WYSIWYG, merge tags ({{firstname}}, {{dog_name}}), recipient sidebar, test send to lydia@dogboxx.org, confirm modal. Resend batch API. |
| 61 | P2 | S | ✅ | **Email marketing opt-out** | `email_marketing` boolean on User (default True). One-click `/auth/unsubscribe/<token>` link in every newsletter. GDPR compliant. |

## Notifications

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 40 | P1 | L | ✅ | **In-app notification system** | Bell icon, unread count, mark read. Persistent DB records. |
| 41 | P1 | S | ✅ | **Notify client: booking confirmed** | Triggered when admin confirms a booking. |
| 42 | P1 | S | ✅ | **Notify admin: booking requested** | Triggered when client submits a new booking. |
| 43 | P1 | S | ✅ | **Notify client: booking cancelled** | Triggered when admin cancels a booking. |
| 44 | P1 | S | ✅ | **Notify walker: assigned to booking** | Triggered when admin assigns a walker. |
| 45 | P1 | S | ✅ | **Notification audit trail (admin)** | Admin can see notification history per client on their detail page. |
| 46 | P1 | L | ✅ | **Notification system overhaul** | `BookingStatusChange` append-only audit log at every status transition (chokepoint in `app/utils/booking_status.py`). `NotificationBatch` + `summarise()` for grouped bulk notifications. Caps: DB=100, page=50, bell=5. `batch_id` correlates rows from one bulk action. PRs #114–121. |
| 47 | P2 | L | ✅ | **Admin activity feed** | `/admin/activity` rebuilt from `BookingStatusChange` log — slot moves, booking-reset events, bulk-action clustering. Collapsible batch groups. PR #118 (Session 4). |
| 48 | P3 | L | ✅ | **Web Push / PWA push notifications** | VAPID-signed push via `pywebpush`. iOS PWA service worker (`app/static/js/sw.js`). `PushSubscription` model stores endpoints per user/device. Bell notification triggers push on unread. Home-screen badge reconciled to server truth on every page load + `visibilitychange` (PRs #128/#129). SSE uses Redis pub/sub when `REDIS_URL` is set so cross-worker events don't drop. |

## Infrastructure & Quality

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 50 | P1 | M | ✅ | **PostgreSQL migration** | Moved from SQLite to PostgreSQL. Flask-Migrate (Alembic) for schema management. |
| 51 | P1 | M | ✅ | **Security hardening** | CSRF, rate limiting, CSP headers, secure cookies, UUID file uploads, session hardening. |
| 52 | P1 | S | ✅ | **DB indexes** | Indexes on date, walker_id, user_id, dog_id, status for query performance. |
| 53 | P1 | M | ✅ | **Git branching** | `develop` for ongoing work, `main` for production. PRs required to merge to main. |
| 54 | P1 | L | ✅ | **Unit test suite** | 337 tests across auth, bookings, capacity, multi-owner, notifications, drop-in, invoicing, activity feed, broadcasts, closures, walker schedule, bulk-cancel, SSE transport. All passing on Postgres CI. |
| 55 | P2 | M | ✅ | **Password reset flow** | Email-based token reset via Resend. noreply@dogboxx.org verified. RESEND_API_KEY + APP_BASE_URL needed in prod env. |
| 56 | P3 | S | ✅ | **CI/CD pipeline** | GitHub Actions (test.yml): runs pytest on push to main/develop and all PRs. All runs green. |
| 57 | P3 | M | ✅ | **PWA service worker** | iOS home-screen install + Android PWA support. Pre-cached assets, pull-to-refresh (shared IIFE in both layouts), standalone-mode detection (`navigator.standalone \|\| display-mode:standalone`). |

---

## Dropped / Descoped

| Feature | Reason |
|---|---|
| Firebase Auth migration | Overkill for current scale. Flask-Login is sufficient. Revisit post-launch. |
| Public client self-registration | Business prefers admin-created accounts (vets clients first). Register route + `RegisterForm` + `register.html` removed entirely in PR #124 (2026-06-05); `/onboard` remains as the post-creation detail-completion flow. |
| Walker pickup status tracking (en_route / picked_up / dropped_off) | Anticipatory `WalkEvent` model/table was never wired up (no writes, no recording UI) and has been **removed** (migration `b40f4de664d4`, 2026-05-29). Rebuild the table fresh in the same PR as the feature if/when prioritised — pickup list remains the priority. |

---

## How to use this file

**Adding a request:** Add a row to the relevant section. Assign a `#`, estimate priority and effort.

**Effort guide:**
- **S (small):** Template tweak, new route, simple query — under an hour
- **M (medium):** New page/flow, moderate logic, touches 2-3 files — a few hours
- **L (large):** New subsystem, multiple routes/templates, external integrations — half a day+

**Reassessing:** Update priority/effort as we learn more. Move notes into the Notes column.
