# Feature Tracker — Dogboxx

> Priority: **P1** = must-have, **P2** = should-have, **P3** = nice-to-have
> Effort: **S** = small (< 1hr), **M** = medium (1-4hrs), **L** = large (4hrs+)
> Status: 🔲 todo · 🔧 in progress · ✅ done · ❌ dropped

## Core Booking Flow

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 1 | P1 | L | ✅ | **Booking workflow with capacity checks** | Walker availability × max_per_walker. Waitlist when full. |
| 2 | P1 | M | ✅ | **Recurring bookings (client)** | Start/end date + frequency (daily/weekly). Server expands to individual bookings. Skips weekends, duplicates. |
| 3 | P1 | M | ✅ | **Admin booking board** | Calendar + slot view. Confirm/cancel requests. Drag-to-reorder pickup order per walker. |
| 4 | P1 | M | ✅ | **Admin dogs view** | Searchable table of all dogs. Book on owner's behalf (one-off or recurring) via modal. Same pending flow as client-initiated. |
| 5 | P1 | M | ✅ | **Prevent duplicate bookings** | Block same dog+date+slot. Max 2 bookings per dog per day (one per slot). DB partial unique index. |
| 6 | P1 | M | 🔲 | **Password reset** | "Forgot password?" → token email → set new password. Blocked on no-reply@dogboxx.org + Resend setup. |
| 7 | P1 | M | ❌ | **Booking capacity display for clients** | Dropped — "available" indicator in UI is sufficient. Exact slot counts not needed. |
| 8 | P2 | L | ✅ | **"Book both walks" option (client)** | Checkbox books AM + PM in one action via /client/book_both. Admin board shows layered-icon modifier pill. |

## Walker

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 10 | P1 | M | ✅ | **Walker pickup list** | Daily route view with dog photo, owner, address, pickup instructions, ordered by pickup_order. Date navigation. |
| 11 | P2 | M | ✅ | **Walker schedule management** | Default weekly schedule (day + slot). Admin sets. Walker can view. |
| 12 | P2 | M | ✅ | **Walker unavailability** | Date-specific exceptions (per slot). Admin marks unavailability. Reduces capacity for that slot automatically. |
| 13 | P3 | L | 🔲 | **Walker self-manage availability** | Walkers flag their own exceptions (holidays, sick days) rather than admin doing it. |
| 14 | P3 | S | ✅ | **Google Maps pickup directions** | Maps button on each pickup card in /walker/pickups. |
| 15 | P2 | M | 🔲 | **Walker ad hoc available days** | Walkers can add one-off available days outside their default schedule. Inverse of the existing unavailability model. New `walker_adhoc_availability` table (or reuse existing with a flag). Capacity checks need updating to include these. |
| 16 | P2 | M | 🔲 | **Admin override walker unavailability on allocation board** | Admin can assign dogs to a slot even if walker has marked themselves unavailable. Override shown visually (warning state). Booking creation bypasses the unavailability block when admin-initiated. |

## Admin

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 20 | P1 | M | ✅ | **Client management** | Create, view, edit client accounts. Notification audit trail per client. |
| 21 | P1 | M | ✅ | **Walker management** | Create walkers. Set/edit default schedule. Mark unavailability. |
| 22 | P2 | S | ✅ | **Admin is also a walker** | is_admin flag on User. Admin can be a walker. "My Pickup List" in admin sidebar. |
| 23 | P2 | M | ✅ | **Admin dashboard stats** | Stat cards (pending, clients, dogs, walkers), 4-week booking chart by slot+status, walker availability grid. Revenue on /admin/revenue. |
| 24 | P3 | L | 🔲 | **Dental cleans service type** | Admin: manage available date+time slots. Client: book from available slots. Stubbed in nav. |
| 25 | P3 | L | ❌ | **Invoicing (standalone)** | Superseded by #26. |
| 26 | P2 | L | ✅ | **Invoicing view (admin)** | /admin/invoicing: monthly summary per client. /admin/invoicing/<id>: line items + weekly breakdown. Billable cancels (<5 days notice), double-slot discount, drop-in pricing. PricingConfig history. |
| 27 | P2 | L | ✅ | **Multiple clients per dog** | dog_owners join table with primary/secondary roles. Admin join/revoke modal. Secondary owners can book and view shared dogs. |
| 29 | P2 | L | ✅ | **Drop-in service type** | Client books AM/PM drop-in visits. Admin drop-in board (assign walkers, confirm/cancel, reorder). Walker pickup list includes drop-ins. Invoicing tracks drop-ins separately at price_per_drop_in. does_drop_ins flag on walkers. |
| 28 | P3 | M | 🔲 | **CSV client/dog import** | Upload CSV matching the create-client form fields. Bulk create client + dog records. Validation with error report on bad rows. No need to handle joined accounts — those are done manually post-import. |

## Client

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 30 | P1 | M | ✅ | **Client onboarding** | Address (with Google Places autocomplete), pickup instructions, dog profile. |
| 31 | P1 | M | ✅ | **Client profile edit** | Edit address, notification prefs, dog details + photo. |
| 32 | P2 | M | 🔲 | **Monthly walk summary** | Client-facing summary of walks taken, upcoming, and any outstanding items. |
| 33 | P3 | L | 🔲 | **Online payments** | Stripe integration for invoice payment. |
| 34 | P3 | M | 🔲 | **Multi-dog support** | Client adds multiple dogs. Share dog profile with another registered user (e.g. partner). |

## Notifications

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 40 | P1 | L | ✅ | **In-app notification system** | Bell icon, unread count, mark read. Persistent DB records. |
| 41 | P1 | S | ✅ | **Notify client: booking confirmed** | Triggered when admin confirms a booking. |
| 42 | P1 | S | ✅ | **Notify admin: booking requested** | Triggered when client submits a new booking. |
| 43 | P1 | S | ✅ | **Notify client: booking cancelled** | Triggered when admin cancels a booking. |
| 44 | P1 | S | ✅ | **Notify walker: assigned to booking** | Triggered when admin assigns a walker. |
| 45 | P1 | S | ✅ | **Notification audit trail (admin)** | Admin can see notification history per client on their detail page. |
| 46 | P2 | L | 🔲 | **Email notifications** | SMTP (Outlook 365 @dogboxx.org) for booking confirmations, cancellations, reminders. In-app notifications are live; email integration pending. |

## Infrastructure & Quality

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 50 | P1 | M | ✅ | **PostgreSQL migration** | Moved from SQLite to PostgreSQL. Flask-Migrate (Alembic) for schema management. |
| 51 | P1 | M | ✅ | **Security hardening** | CSRF, rate limiting, CSP headers, secure cookies, UUID file uploads, session hardening. |
| 52 | P1 | S | ✅ | **DB indexes** | Indexes on date, walker_id, user_id, dog_id, status for query performance. |
| 53 | P1 | M | ✅ | **Git branching** | `develop` for ongoing work, `main` for production. PRs required to merge to main. |
| 54 | P1 | L | ✅ | **Unit test suite** | 140 tests across auth, bookings, capacity, multi-owner, notifications, drop-in, invoicing. All passing, no deprecation warnings. |
| 55 | P2 | M | 🔲 | **Password reset flow** | Email-based token reset. Blocked on no-reply@dogboxx.org + Resend setup. |
| 56 | P3 | S | ✅ | **CI/CD pipeline** | GitHub Actions (test.yml): runs pytest on push to main/develop and all PRs. All runs green. |

---

## Dropped / Descoped

| Feature | Reason |
|---|---|
| Firebase Auth migration | Overkill for current scale. Flask-Login is sufficient. Revisit post-launch. |
| Public client self-registration | Business prefers admin-created accounts (vets clients first). Register route still exists but not promoted. |
| Walker pickup status tracking (en_route / picked_up / dropped_off) | WalkEvent model exists in docs plan. Deprioritised — pickup list is the priority. |

---

## How to use this file

**Adding a request:** Add a row to the relevant section. Assign a `#`, estimate priority and effort.

**Effort guide:**
- **S (small):** Template tweak, new route, simple query — under an hour
- **M (medium):** New page/flow, moderate logic, touches 2-3 files — a few hours
- **L (large):** New subsystem, multiple routes/templates, external integrations — half a day+

**Reassessing:** Update priority/effort as we learn more. Move notes into the Notes column.
