# Phase 1 Plan — Foundation

## Goals
1. Refactor the data model to support multi-dog ownership and multiple service types
2. Implement a proper booking request workflow with state machine
3. Add email notifications for booking state changes
4. Admin walker management (add/remove/deactivate)
5. Migrate from SQLite to PostgreSQL

---

## 1. Data Model Changes

### 1.1 Dog ↔ Owner Relationship (Many-to-Many)
**Current:** 1:1 client→dog via `dog.user_id` FK
**Proposed:** Many-to-many. One owner can have multiple dogs, and one dog can have multiple owners (eg a couple who both want to book services and check schedules for the same dog).

New join table:
```
DogOwner
  id              Integer PK
  dog_id          FK → dogs
  user_id         FK → users
  role            Enum "primary" | "secondary"   (primary = created the dog profile)
  created_at      DateTime
  
  unique constraint on (dog_id, user_id)
```

Changes:
- Remove `dog.user_id` FK (replaced by DogOwner join table)
- Onboarding: add first dog during onboarding (as now), owner is "primary"
- Client dashboard: "My Dogs" section with add/edit/remove dogs
- Client can **share** a dog with another registered user (eg invite partner by email)
- Booking form: dog picker shows all dogs the client is linked to
- Admin views: show dog name alongside owner name in booking lists
- Any owner of a dog can book services and view that dog's schedule
- Only primary owner (or admin) can edit dog profile details

### 1.2 Service Types
**Current:** Implicit — everything is a "walk" with morning/afternoon slots
**Proposed:** Explicit ServiceType model to support walks now, day care later, and other services in future.

```
ServiceType
  id              Integer PK
  name            String          eg "Group Walk", "Doggy Day Care"
  slug            String unique   eg "group-walk", "day-care"
  description     Text nullable
  capacity_model  Enum            "walker_assigned" | "facility_capacity"
  slot_type       Enum            "morning_afternoon" | "full_half_day" | "hourly"
  requires_walker Boolean         True for walks, False for day care
  requires_compatibility_check  Boolean   True for group walks
  default_max_capacity  Integer nullable   eg 6 for walks (per walker), 20 for day care (facility)
  active          Boolean default True
  settings        JSON nullable   Future-proofing for service-specific config
  created_at      DateTime
```

Seed data:
- "Group Walk" — walker_assigned, morning_afternoon slots, requires compatibility check, max 6 per walker
- "Doggy Day Care" — facility_capacity, full_half_day slots, no walker, no compatibility check, fixed facility capacity

### 1.3 Capacity Model

**Walks:** Dynamic capacity based on walker availability.
- Each walker handles max **6 dogs** per slot
- Number of walkers varies by day and by slot (morning/afternoon)
- Total walk capacity for a given slot = `available_walkers_count × 6`
- Walker availability is managed by admin initially (Phase 2: walkers self-manage)
- When a client requests a walk, the system checks if there's remaining capacity for that date+slot

**Day care:** Fixed facility capacity.
- A single number (eg 20 dogs max per day)
- Can offer full day or half day bookings
- Half day AM/PM each count toward the day's capacity
- Configurable via ServiceType.default_max_capacity

This means we need a way to know which walkers are available on which days/slots:

```
WalkerSchedule
  id              Integer PK
  walker_id       FK → walkers
  day_of_week     Integer (0=Monday, 6=Sunday)
  slot            Enum "Morning" | "Afternoon"
  active          Boolean default True
```

This stores the walker's **default weekly pattern** (eg "Alice works Monday morning, Tuesday all day"). The admin sets this up. Phase 2 adds the ability for walkers to flag exceptions (holidays, sick days).

To check walk capacity for a date+slot:
1. Find all walkers scheduled for that day_of_week + slot
2. Count confirmed+requested bookings for that date+slot
3. Available slots = (scheduled_walkers × 6) − current_bookings

### 1.4 Booking Model Refactor
**Current:** Booking with status enum (Pending/Confirmed/Modified/Cancelled)
**Proposed:** Richer model with service type support and audit trail.

```
Booking
  id              Integer PK
  user_id         FK → users
  dog_id          FK → dogs
  service_type_id FK → service_types
  date            Date
  slot            Enum  "Morning" | "Afternoon" | "Full Day" | "Half Day AM" | "Half Day PM"
  walker_id       FK → walkers (nullable — only for walker-assigned services)
  pickup_order    Integer nullable   (set by admin drag-drop; 1 = first pickup)
  status          Enum  (see state machine below)
  client_notes    Text nullable   (client's message with the request)
  admin_notes     Text nullable   (admin's reason for modification/rejection)
  created_at      DateTime
  updated_at      DateTime
  confirmed_at    DateTime nullable
  cancelled_at    DateTime nullable
  cancelled_by    Enum nullable   "client" | "admin"
```

### 1.5 Booking Status State Machine

```
  requested ←─── (client can edit while in this state)
     ↓
  ┌──┴──────────────┐
  ↓                 ↓
confirmed        rejected
  ↓
modified (admin changed date/slot/walker)
  ↓
  → client sees notification, can accept or cancel

Any non-terminal state → cancelled (by client or admin)
```

Statuses: `requested` → `confirmed` / `rejected` / `modified`
          `modified` → `confirmed` (client accepts) / `cancelled`
          `confirmed` → `completed` / `cancelled`

**Client editing:** Clients can edit their own requests (change date/slot/dog) while status is `requested` (ie before admin review). Once confirmed, they can only cancel.

**Cancellation policy:** Walks cancelled with fewer than 5 days' notice are still billable. The system tracks `cancelled_at` and compares to `date` to flag late cancellations for invoicing (Phase 3). Cancellation rules stored per ServiceType in `settings` JSON for flexibility.

### 1.6 Booking Status History (Audit Trail)

```
BookingStatusChange
  id              Integer PK
  booking_id      FK → bookings
  from_status     String nullable (null for initial creation)
  to_status       String
  changed_by_id   FK → users
  notes           Text nullable
  created_at      DateTime
```

Full history of every booking state change, who did it, and why.

### 1.7 Walker Model
The Walker model is fine as-is. We add:
- **WalkerSchedule** (see 1.3) for default weekly availability
- Admin UI to create/deactivate/reactivate walkers and manage their schedules

---

## 2. Account Creation & Onboarding

### Invite-Only Client Registration
**Current:** Clients self-register via public registration form.
**Proposed:** Remove public registration. Admin creates all client accounts.

**Admin creates client:**
1. Admin enters client's email + first name + last name
2. System creates User (role='client') + Client record with a temporary password
3. Welcome email sent to client with login link
4. On first login, client is prompted to change their password
5. Client then completes onboarding: add address, pickup instructions, and first dog profile
6. Client can add more dogs and share dogs with other owners later

This matches the real-world flow — the business owner meets the dog and owner first, then sets them up in the system.

**Implications:**
- Remove the public `/register` route and form
- Add `must_change_password` Boolean field to User model (default False, set True for admin-created accounts)
- Login flow checks `must_change_password` → redirects to password change screen before anything else
- Admin can also create walker accounts the same way (already planned)

### Admin Account Creation Routes
- `GET /admin/clients` — list all clients (active/inactive)
- `GET /admin/clients/new` — form to add client
- `POST /admin/clients` — create client (User + Client records, temp password, sends welcome email)
- `GET /admin/clients/<id>` — view client details, dogs, booking history
- `POST /admin/clients/<id>/deactivate` — soft-delete
- `POST /admin/clients/<id>/activate` — reactivate

---

## 3. Booking Request Workflow

### Client Flow
1. Client selects service type (initially just "Group Walk", more later)
2. Client picks date + slot + dog (if multiple dogs)
3. System checks capacity — shows available slots only
4. Client can add notes ("Please walk with Bella if possible")
5. Submit → status = `requested`
6. Client can **edit** the request while it's still `requested`
7. Client sees booking in dashboard with status badge
8. Client receives email when status changes

### Admin Flow
1. Admin dashboard shows pending requests (filterable by date/service)
2. Admin can:
   - **Confirm** → assigns walker (for walk services), status = `confirmed`
   - **Modify** → change date/slot/walker, status = `modified`, adds admin_notes
   - **Reject** → status = `rejected`, must provide reason in admin_notes
3. Drag-and-drop allocation (existing UX) still works for confirmed bookings
   - The **order** dogs are dropped into a walker's list sets `pickup_order` (1st = collected first, 2nd = second, etc.)
   - Admin can reorder within a walker's list to optimise the route based on her knowledge of the area
   - When admin saves/confirms, the `pickup_order` values are persisted to each booking
4. Admin sees compatibility notes as manual judgment call (no automated system)

### Notifications (triggered by status changes)
- `requested` → email to admin ("New booking request from [client] for [dog]")
- `confirmed` → email to client ("Your booking is confirmed — [date, slot, walker]")
- `modified` → email to client ("Your booking has been modified — [what changed, reason]")
- `rejected` → email to client ("Your booking request was not approved — [reason]")
- `cancelled` → email to admin (if client cancels) or client (if admin cancels)

---

## 4. Walker Pickup List

A mobile-friendly interface for walkers to use during their rounds.

### What the walker sees
For each slot (morning/afternoon), a list of their assigned dogs **sorted by `pickup_order`** (as set by admin during drag-drop allocation), showing:
- **Dog name** + photo (if available)
- **Owner name**
- **Address** with a button to **open in Google Maps** (uses the lat/lng from client profile)
- **Pickup instructions** (expandable — door codes, access notes, etc.)
- **Status buttons:** `En Route` → `Picked Up` → `Dropped Off`

### Pickup status tracking

```
WalkEvent
  id              Integer PK
  booking_id      FK → bookings
  event_type      Enum "en_route" | "picked_up" | "dropped_off"
  created_at      DateTime
  latitude        Float nullable   (walker's location if available, for future use)
  longitude       Float nullable
```

### Owner notifications
- **"En route"** → push/email to owner: "[Walker] is on their way to collect [Dog]!"
- **"Picked up"** → notification to owner: "[Dog] has been picked up by [Walker]"
- **"Dropped off"** → notification to owner: "[Dog] is back home!"

### Walker routes
- `GET /walker/pickup-list` — today's pickup list for the logged-in walker (default: current/next slot)
- `POST /walker/pickup-list/<booking_id>/status` — update pickup status (en_route/picked_up/dropped_off)

### Design notes
- Must be **very mobile-friendly** — big tap targets, minimal scrolling
- Walker uses this one-handed while walking dogs
- Google Maps link: `https://www.google.com/maps/dir/?api=1&destination={lat},{lng}`
- Consider grouping by proximity / suggesting an efficient pickup route (future enhancement)

---

## 5. Email Setup

**Provider:** Outlook 365 SMTP via existing @dogboxx.org account
**Library:** Flask-Mail

SMTP settings:
```
MAIL_SERVER=smtp.office365.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=<dogboxx.org email>
MAIL_PASSWORD=<app password or OAuth token>
MAIL_DEFAULT_SENDER=<dogboxx.org email>
```

Templates (plain text initially, HTML later):
- Booking confirmed
- Booking modified (includes what changed + admin notes)
- Booking rejected (includes reason)
- Booking cancelled
- New request notification (to admin)

---

## 6. Admin Walker Management

New admin routes:
- `GET /admin/walkers` — list all walkers (active/inactive)
- `GET /admin/walkers/new` — form to add walker
- `POST /admin/walkers` — create walker (User + Walker records, temp password)
- `POST /admin/walkers/<id>/deactivate` — soft-delete
- `POST /admin/walkers/<id>/activate` — reactivate
- `GET /admin/walkers/<id>/schedule` — view/edit weekly schedule
- `POST /admin/walkers/<id>/schedule` — update weekly schedule

Walker onboarding: admin creates the account with a temporary password. Walker receives email with login instructions.

---

## 7. Database Migration: SQLite → PostgreSQL

**Why:** Concurrent access (admin + clients simultaneously), proper data types, JSON column support, better constraint handling, production-ready.

**Steps:**
1. Install PostgreSQL on the LXC container
2. Create database + user
3. Update Flask config (SQLALCHEMY_DATABASE_URI)
4. Use Flask-Migrate (Alembic) for schema management going forward
5. Seed with ServiceType data + any existing test data

Since this is pre-production with no real user data to migrate, we can start fresh with the new schema.

---

## 8. Implementation Order

1. **Database setup** — install Postgres, configure Flask-Migrate
2. **Models** — ServiceType, DogOwner, WalkerSchedule, WalkEvent, BookingStatusChange, updated Booking, User.must_change_password
3. **Seed data** — service types, test data
4. **Admin account management** — create clients + walkers, welcome emails
5. **Password change flow** — forced change on first login for admin-created accounts
6. **Client onboarding** — updated flow (add dogs, share dogs with other owners)
7. **Booking workflow** — updated client booking flow with service types + capacity checks
8. **Admin booking review** — confirm/modify/reject UI
9. **Walker pickup list** — mobile-friendly pickup interface with status tracking + owner notifications
10. **Email notifications** — Flask-Mail setup + all templates
11. **Testing** — manual test of full lifecycle (admin creates client → client books → admin confirms → walker picks up → owner notified)

---

## 9. File Structure (proposed changes)

```
app/
  models.py                → updated with new models
  forms.py                 → add ServiceType-aware booking form, walker forms
  email.py                 → new: email sending utilities
  capacity.py              → new: capacity checking logic
  templates/
    email/                 → new: email templates
    admin_walkers.html     → new: walker management
    admin_walker_form.html → new: add/edit walker
    partials/              → existing: reusable components
  blueprints/
    admin/routes.py        → add walker management + booking review routes
    client/routes.py       → update booking flow with service types + capacity
    api/routes.py          → update for new booking statuses + capacity endpoint
```

---

## Decisions Log

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | Dog compatibility | Admin gut feel only | Keep it simple, no temperament tracking |
| 2 | Client editing | Can edit while `requested` | Simpler than cancel+re-request; locked after admin review |
| 3 | Walk capacity | Max 6 per walker, dynamic by day/slot | Walker count varies; WalkerSchedule tracks availability |
| 4 | Email provider | Outlook 365 SMTP (@dogboxx.org) | Existing account, no extra cost |
| 5 | Database | PostgreSQL | Concurrent access, proper types, production-ready |
| 6 | Day care capacity | Fixed facility limit, full/half day | Configurable per ServiceType |
| 7 | Cancellation policy | <5 days notice = still billable | Tracked via timestamps, enforced in invoicing (Phase 3) |
| 8 | Client registration | Invite-only (admin creates accounts) | Matches real business flow; admin vets clients first |
| 9 | Dog↔Owner relationship | Many-to-many via DogOwner table | Supports couples/families sharing a dog profile |
| 10 | Walker pickup tracking | WalkEvent model with en_route/picked_up/dropped_off | Enables real-time owner notifications |
