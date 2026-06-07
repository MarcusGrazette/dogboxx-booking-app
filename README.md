# Dogboxx 🐾

A booking management platform for a small dog walking business. Built with Flask and PostgreSQL, it manages the full lifecycle of walk bookings — from client request through walker assignment and pickup.

## Tech Stack

- **Backend:** Python 3.12, Flask, SQLAlchemy, Flask-Migrate (Alembic), Flask-Login, Flask-WTF
- **Database:** PostgreSQL (SQLite supported for local dev)
- **Frontend:** Jinja2 templates, Bootstrap 5, Bootstrap Icons
- **Auth:** Flask-Login with CSRF protection and rate limiting via Flask-Limiter
- **File uploads:** UUID-named uploads with server-side image validation

## Features

### Client
- Onboarding (address, pickup instructions, dog profile)
- Book walks — one-off, recurring (daily/weekly), or both AM + PM slots in one action
- Drop-in visits — bookable separately from group walks
- Booking dashboard with status tracking (requested → confirmed / waitlisted)
- In-app notification bell for booking confirmations, cancellations, and walker assignments
- Web Push notifications on iOS and Android when installed as a PWA
- Profile and dog profile editing (photo upload, per-dog pickup instructions)
- Multi-dog support — dog selector on booking form; all dogs shown on profile
- Monthly walk summary with booking history

### Admin
- Dashboard with booking stats, 4-week chart, and walker availability grid
- **Bookings board** — confirm/cancel requests, assign walkers, drag-to-reorder pickup order per walker per slot
- **Drop-in board** — separate board for drop-in visits with the same confirm/assign/reorder flow
- **Dogs view** — searchable table; book on behalf of any owner (one-off or recurring); add additional dogs to a client
- **Clients** — create, view, manage accounts; add secondary co-owners; CSV bulk import
- **Walkers** — create walkers, set default weekly schedules, mark unavailability, add ad hoc available days; override unavailability on the allocation board
- **Invoicing** — monthly summary per client with line items, weekly breakdown, configurable pricing (walk, drop-in, double-slot discount, weekly discount)
- **Newsletter** — WYSIWYG editor with merge tags, recipient sidebar, test send, one-click unsubscribe
- **Daily messages** — admin posts announcements visible to walkers on their pickup list
- **Broadcasts** — one-shot message to all clients booked on a chosen date/slot, delivered via bell and/or email
- **Closures** — mark dates as closed; cancels existing bookings and blocks new ones with notifications
- **Activity feed** — append-only audit log of all booking status changes, with bulk action grouping
- Notification audit trail per client
- Admin is also a walker — "My Pickup List" in the sidebar

### Walker
- Daily pickup list with dog photo, owner, address, pickup instructions, ordered pickup sequence, and daily announcements
- Date navigation (past/future pickup lists)
- Profile page — default schedule, unavailability exceptions, ad hoc available days
- Monthly summary — walk slots, drop-in visits, and dog counts with month navigation

### Capacity & Waitlisting
- Walk capacity is dynamic: `available walkers × max_per_walker` per slot per day
- Walker unavailability reduces capacity automatically
- Bookings beyond capacity go to `waitlisted` status
- Admin can promote waitlisted bookings by assigning additional walkers

### Notifications
- Persistent bell notification system (admin + client)
- Triggered on: booking requested, confirmed, cancelled, walker assigned, walker reset
- Read/unread state with timestamps
- Web Push delivery to installed PWA devices (iOS + Android) via VAPID

---

## Local Development Setup

### Prerequisites
- Python 3.12+
- PostgreSQL (or SQLite for quick start)
- Git

### 1. Clone and set up the virtual environment

```bash
git clone git@github.com:MarcusGrazette/dogboxx-booking-app.git
cd dogboxx-booking-app
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt   # local dev: app + test tooling (pytest etc.)
```

> `requirements-dev.txt` includes `requirements.txt` plus the test suite tooling. Use the plain `requirements.txt` only for runtime-only installs (production does this automatically).

### 2. Configure environment variables

Copy `.env.example` and fill in the values:

```bash
cp .env.example .env
```

Required variables:

| Variable | Description |
|---|---|
| `SECRET_KEY` | Flask secret key (any long random string) |
| `FLASK_ENV` | `development` or `production` |
| `FLASK_DEBUG` | `1` for development, `0` for production |
| `DATABASE_URL` | PostgreSQL connection string, e.g. `postgresql://user:pass@localhost/dogboxx` |

For SQLite (quick local start), omit `DATABASE_URL` — it defaults to `sqlite:///app.db`.

### 3. Set up PostgreSQL

Production and CI run on PostgreSQL 16 — match it locally for full fidelity (SQLite hides a class of enum/FK bugs; see [Running Tests](#running-tests)). To skip Postgres entirely for a quick look, omit `DATABASE_URL` and jump to step 4 — the app falls back to `sqlite:///app.db`.

Full from-scratch sequence on a fresh box:

```bash
# Debian/Ubuntu — install and start Postgres 16
sudo apt install -y postgresql postgresql-client libpq-dev
sudo systemctl enable --now postgresql        # starts now + on every boot

# macOS (Homebrew) equivalent:
#   brew install postgresql@16 && brew services start postgresql@16

# Create the dogboxx role and both databases, run as the postgres superuser.
# (Homebrew uses trust auth as your macOS user — you can skip createuser/ALTER ROLE
#  and just `createdb dogboxx` / `createdb dogboxx_test`.)
sudo -u postgres createuser dogboxx
sudo -u postgres psql -c "ALTER ROLE dogboxx WITH PASSWORD 'choose-a-dev-password';"
sudo -u postgres createdb -O dogboxx dogboxx          # dev database
sudo -u postgres createdb -O dogboxx dogboxx_test     # test database (used by pytest)
```

Then point `.env` at it (the test DB is wired up here too so plain `pytest` finds it):

```bash
# .env
DATABASE_URL=postgresql://dogboxx:choose-a-dev-password@localhost:5432/dogboxx
TEST_DATABASE_URL=postgresql://dogboxx:choose-a-dev-password@localhost:5432/dogboxx_test
```

### 4. Initialise the database

```bash
flask db upgrade        # runs all migrations against DATABASE_URL
python seed.py          # seeds test data (see Test Accounts below)
```

### 5. Run the development server

```bash
# In a tmux session or background terminal:
venv/bin/flask run --host=0.0.0.0 --port=5000
```

The app is available at `http://localhost:5000`.

---

## Test Accounts

| Role | Email | Password |
|---|---|---|
| Admin + Walker (Owner) | lydia@dogboxx.org | changeme123! |
| Walker | testwalker@dogboxx.org | walkies123 |
| Client | john.doe@example.com | clientpass |

The seed also creates ~13 client accounts with dogs. See `seed.py` for the full list.

### Demo data

Two demo seed scripts add realistic booking data for presentations:

```bash
python seed_may_demo.py   # ~10 walks/slot/weekday across two weeks, ±2 randomised, 3–5 drop-ins/day
python seed_june_demo.py  # mix of normal and at-capacity days to demonstrate the waitlist
```

---

## Running Tests

The test suite runs against **PostgreSQL by default**, matching CI and production. SQLite silently hides a class of bugs that only surface on Postgres (native enum constraints, enum-type comparisons in raw SQL, FK visibility across request connections), so Postgres is the trustworthy signal.

### Against Postgres (default — recommended)

**1. Install Postgres if you don't already have it.** Match the major version CI and production use (16) for full fidelity.

```bash
# macOS (Homebrew)
brew install postgresql@16
brew services start postgresql@16

# Debian/Ubuntu
sudo apt install postgresql && sudo service postgresql start
```

**2. Create a test database.** It's separate from any dev database and holds nothing but the test schema, which the suite creates and drops on every run.

```bash
createdb dogboxx_test
```

On a fresh Homebrew install, local connections use your macOS username with no password (trust auth), and `createdb` makes a DB owned by that user. If you already run a dev Postgres with a `dogboxx` role, create it owned by that role instead (`createdb -O dogboxx dogboxx_test`) so you can reuse your dev credentials.

**3. Point the suite at it** via a line in your `.env` (it's loaded before config is read, so plain `pytest` picks it up — no shell export needed). Use whatever user/password your Postgres expects:

```bash
# .env — Homebrew default (your macOS user, no password):
TEST_DATABASE_URL=postgresql://YOUR_MACOS_USERNAME@localhost:5432/dogboxx_test
# ...or, reusing an existing dev Postgres role:
# TEST_DATABASE_URL=postgresql://dogboxx:<your-dev-password>@localhost:5432/dogboxx_test
```

**4. Run it:**

```bash
pytest                          # runs against dogboxx_test
pytest tests/test_auth.py       # a single file
```

If `TEST_DATABASE_URL` is unset, `TestingConfig` falls back to `postgresql://dogboxx:dogboxx@localhost:5432/dogboxx_test` (the credentials CI uses); locally that simply fails to authenticate rather than touching any real data.

### Fast SQLite escape hatch

For a quicker inner loop (the suite is ~2–3× faster on in-memory SQLite), opt out:

```bash
USE_SQLITE=1 pytest
```

Use this for rapid iteration, but **re-run against Postgres before pushing** — and remember CI always runs on Postgres regardless.

---

## Project Structure

```
app/
  blueprints/
    admin/          Admin routes (dashboard, bookings, dogs, clients, walkers, invoicing, newsletter)
    auth/           Login, logout, password reset, unsubscribe
    client/         Client home, onboarding, bookings, profile
    walker/         Pickup list, profile, monthly summary
    api/            JSON endpoints (calendar data, booking actions)
  templates/        Jinja2 templates (admin_layout.html, layout.html, partials/)
  static/           CSS, JS, images, uploads
  models.py         SQLAlchemy models
  capacity.py       Walk capacity and availability logic
  forms.py          WTForms form definitions
  utils/            Notifications, decorators, DB error handling, uploads
config.py           Development / Testing / Production config classes
migrations/         Alembic migration files
scripts/start.sh    Production startup (volume symlink, migrations, gunicorn)
seed.py             Base seed data (users, dogs, walkers, schedules)
seed_may_demo.py    Demo bookings — walks + drop-ins across two weekday weeks
seed_june_demo.py   Demo bookings — mix of normal and at-capacity days for waitlist demo
```

---

## Data Model Overview

| Model | Description |
|---|---|
| `User` | All users — has `role` (client/walker) and `is_admin` flag |
| `Client` | Address and onboarding data for client users |
| `Walker` | Walker record linked to a User |
| `Dog` | Dog profile (name, breed, DOB, photo, pickup_instructions) |
| `DogOwner` | Many-to-many join: dogs ↔ users, with `role` (primary/secondary) |
| `Booking` | Walk booking — links user, dog, service type, date, slot, walker, status |
| `ServiceType` | Service definition (currently: Group Walk, Drop-in) |
| `WalkerSchedule` | Walker's default weekly availability (day_of_week + slot) |
| `WalkerUnavailability` | Date-specific exceptions to a walker's schedule |
| `WalkerAdHocAvailability` | One-off available days outside a walker's default schedule |
| `BookingStatusChange` | Append-only audit log of every booking status transition |
| `PricingConfig` | Pricing history — walk, drop-in, double-slot discount, weekly discount |
| `DailyMessage` | Admin announcements shown to walkers on the pickup list |
| `Closure` | Business closure dates — cancels existing bookings and blocks new ones |
| `Broadcast` | Admin one-shot messages to clients booked on a given date/slot |
| `PushSubscription` | Web Push endpoints per user/device for PWA notifications |
| `Notification` | In-app notification records (recipient, type, read state) |

### Booking statuses
`requested` → `confirmed` / `waitlisted` / `cancelled`

---

## Branching & Deployment

- **`develop`** — active development branch; all work goes here
- **`main`** — production-ready only; updated via PR from `develop`

To deploy: merge `develop` → `main` via GitHub PR. Railway auto-deploys on merge to `main`. The startup script `scripts/start.sh` runs on each deploy — it creates the persistent uploads volume symlink, runs `flask db upgrade`, seeds service types, then starts gunicorn.

---

## Configuration

The app uses three config classes in `config.py`:

| Config | Used when | Notes |
|---|---|---|
| `DevelopmentConfig` | `FLASK_ENV=development` | Debug on, SQLAlchemy echo, CSP report-only |
| `TestingConfig` | Tests | PostgreSQL by default (`USE_SQLITE=1` for SQLite), CSRF disabled |
| `ProductionConfig` | `FLASK_ENV=production` | Secure cookies, strict CSP, Redis rate limiting |

---

## Known Limitations / Roadmap

- Email notifications (Resend) are wired for password reset and unsubscribe; transactional booking emails not yet implemented
- Dental cleans service type stubbed in nav but not yet built

See `FEATURES.md` for the full feature tracker.
