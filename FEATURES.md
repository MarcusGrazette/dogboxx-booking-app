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
| 6 | P1 | M | 🔲 | **Password reset** | "Forgot password?" → token email → set new password. |
| 7 | P1 | M | 🔲 | **Booking capacity display for clients** | Show remaining slots when client is booking, not just accept/reject. |
| 8 | P2 | L | 🔲 | **"Book both walks" option (client)** | Tick box above recurring toggle. Ticking it books AM + PM in one action. Confirmation modal before submit. Allocation board should show a double-walk icon (similar to waitlist indicator). |

## Walker

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 10 | P1 | M | ✅ | **Walker pickup list** | Daily route view with dog photo, owner, address, pickup instructions, ordered by pickup_order. Date navigation. |
| 11 | P2 | M | ✅ | **Walker schedule management** | Default weekly schedule (day + slot). Admin sets. Walker can view. |
| 12 | P2 | M | ✅ | **Walker unavailability** | Date-specific exceptions (per slot). Admin marks unavailability. Reduces capacity for that slot automatically. |
| 13 | P3 | L | 🔲 | **Walker self-manage availability** | Walkers flag their own exceptions (holidays, sick days) rather than admin doing it. |
| 14 | P3 | L | 🔲 | **Google Maps pickup directions** | Link from pickup list to Google Maps directions for each address. |
| 15 | P2 | M | 🔲 | **Walker ad hoc available days** | Walkers can add one-off available days outside their default schedule. Inverse of the existing unavailability model. New `walker_adhoc_availability` table (or reuse existing with a flag). Capacity checks need updating to include these. |
| 16 | P2 | M | 🔲 | **Admin override walker unavailability on allocation board** | Admin can assign dogs to a slot even if walker has marked themselves unavailable. Override shown visually (warning state). Booking creation bypasses the unavailability block when admin-initiated. |

## Admin

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 20 | P1 | M | ✅ | **Client management** | Create, view, edit client accounts. Notification audit trail per client. |
| 21 | P1 | M | ✅ | **Walker management** | Create walkers. Set/edit default schedule. Mark unavailability. |
| 22 | P2 | S | ✅ | **Admin is also a walker** | is_admin flag on User. Admin can be a walker. "My Pickup List" in admin sidebar. |
| 23 | P2 | M | 🔲 | **Admin dashboard stats** | Booking counts, utilisation, revenue summary. Currently shows upcoming bookings only. |
| 24 | P3 | L | 🔲 | **Dental cleans service type** | Admin: manage available date+time slots. Client: book from available slots. Stubbed in nav. |
| 25 | P3 | L | 🔲 | **Invoicing** | Generate invoices per client based on confirmed walks. Cancellation policy enforcement (<5 days notice = billable). |
| 26 | P2 | L | 🔲 | **Invoicing view (admin)** | Monthly summary per client: walks per week + cancellations within 5 days of walk date (still charged). Listed in admin dashboard. |
| 27 | P2 | L | 🔲 | **Multiple clients per dog** | One dog can be linked to multiple user accounts. Admin can "join" accounts via modal (select from existing users). All joined accounts can view/manage/book for that dog. Requires new `dog_owners` join table and permission checks throughout booking + client flows. Client profile should indicate the account is joined. |
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
| 54 | P1 | L | 🔧 | **Unit test suite** | pytest + pytest-flask. T1 (infrastructure) → T2 (capacity) → T3 (bookings) → T4 (auth) → T5 (notifications). See HEARTBEAT.md. |
| 55 | P2 | M | 🔲 | **Password reset flow** | Email-based token reset. Requires email notifications (#46) to be wired first. |
| 56 | P3 | L | 🔲 | **CI/CD pipeline** | GitHub Actions: run tests on push to develop, block PRs to main if tests fail. |

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
