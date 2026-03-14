# Dogboxx 🐾

A booking management platform for a small dog walking business. Built with Flask and PostgreSQL, it manages the full lifecycle of walk bookings — from client request through walker assignment and pickup.

## Tech Stack

- **Backend:** Python 3.12, Flask, SQLAlchemy, Flask-Migrate (Alembic), Flask-Login, Flask-WTF
- **Database:** PostgreSQL (SQLite supported for local dev)
- **Frontend:** Jinja2 templates, Bootstrap 5, AdminLTE 3 (admin panel), Bootstrap Icons
- **Auth:** Flask-Login with CSRF protection and rate limiting via Flask-Limiter
- **File uploads:** UUID-named uploads with server-side image validation

## Features

### Client
- Self-registration and onboarding (address, pickup instructions, dog profile)
- Book walks — one-off or recurring (daily/weekly, expand into individual bookings)
- Booking dashboard with status tracking (requested → confirmed / waitlisted)
- In-app notification bell for booking confirmations, cancellations, and walker assignments
- Profile and dog profile editing (including photo upload)

### Admin
- Dashboard with booking stats and a calendar view of upcoming walks
- **Bookings board** — review pending requests, confirm/cancel, assign walkers, drag-to-reorder pickup order per walker per slot
- **Dogs view** — searchable table of all dogs on the books; book on behalf of any dog's owner (one-off or recurring)
- **Clients** — create, view, and manage client accounts
- **Walkers** — create and manage walkers, set default weekly schedules, mark unavailability by date/slot
- Notification audit trail per client
- Admin is also a walker — "My Pickup List" accessible directly from the sidebar

### Walker
- Daily pickup list with dog photo, owner name, address, pickup instructions, and ordered pickup sequence
- Date navigation (past/future pickup lists)
- Schedule management — view default schedule and manage unavailability exceptions

### Capacity & Waitlisting
- Walk capacity is dynamic: `available walkers × max_per_walker` per slot per day
- Walker unavailability reduces capacity automatically
- Bookings beyond capacity go to `waitlisted` status
- Admin can promote waitlisted bookings by assigning additional walkers

### Notifications
- Persistent bell notification system (admin + client)
- Triggered on: booking requested, confirmed, cancelled, walker assigned
- Read/unread state with timestamps

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
pip install -r requirements.txt
```

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

### 3. Initialise the database

```bash
flask db upgrade        # runs all migrations
python seed.py          # seeds test data (see Test Accounts below)
```

### 4. Run the development server

```bash
# In a tmux session or background terminal:
venv/bin/flask run --host=0.0.0.0 --port=5000
```

The app is available at `http://localhost:5000`.

---

## Test Accounts

| Role | Email | Password |
|---|---|---|
| Admin + Walker | admin@dogboxx.org | adminpass |
| Walker | testwalker@dogboxx.org | walkies123 |
| Client | john.doe@example.com | clientpass |

The seed also creates ~13 client accounts with dogs. See `seed.py` for the full list.

### Demo data

To populate 3 weeks of realistic bookings (including waitlisted slots for demo purposes):

```bash
python seed_demo_bookings.py
```

This adds walker unavailability on specific days to reduce capacity and trigger the waitlist, then seeds requested/confirmed/waitlisted bookings across all 3 weeks.

---

## Project Structure

```
app/
  blueprints/
    admin/          Admin routes (dashboard, bookings, dogs, clients, walkers)
    auth/           Login, logout, registration
    client/         Client home, onboarding, bookings, profile
    walker/         Pickup list, schedule, unavailability
    api/            JSON endpoints (calendar data, booking actions)
  templates/        Jinja2 templates (admin_layout.html, base.html, partials/)
  static/           CSS, JS, images, uploads
  models.py         SQLAlchemy models
  capacity.py       Walk capacity and availability logic
  forms.py          WTForms form definitions
  utils/            Notifications, decorators, DB error handling, uploads
config.py           Development / Testing / Production config classes
migrations/         Alembic migration files
seed.py             Base seed data (users, dogs, walkers, schedules)
seed_demo_bookings.py  Demo booking data for presentations
```

---

## Data Model Overview

| Model | Description |
|---|---|
| `User` | All users — has `role` (client/walker) and `is_admin` flag |
| `Client` | Address and onboarding data for client users |
| `Walker` | Walker record linked to a User |
| `Dog` | Dog profile (name, breed, DOB, photo, notes) |
| `DogOwner` | Many-to-many join: dogs ↔ users, with `role` (primary/secondary) |
| `Booking` | Walk booking — links user, dog, service type, date, slot, walker, status |
| `ServiceType` | Service definition (currently: Group Walk) |
| `WalkerSchedule` | Walker's default weekly availability (day_of_week + slot) |
| `WalkerUnavailability` | Date-specific exceptions to a walker's schedule |
| `Notification` | In-app notification records (recipient, type, read state) |

### Booking statuses
`requested` → `confirmed` / `waitlisted` / `cancelled`

---

## Branching & Deployment

- **`develop`** — active development branch; all work goes here
- **`main`** — production-ready only; updated via PR from `develop`

To deploy: merge `develop` → `main` via GitHub PR, then pull on the production server and restart Flask.

---

## Configuration

The app uses three config classes in `config.py`:

| Config | Used when | Notes |
|---|---|---|
| `DevelopmentConfig` | `FLASK_ENV=development` | Debug on, SQLAlchemy echo, CSP report-only |
| `TestingConfig` | Tests | In-memory SQLite, CSRF disabled |
| `ProductionConfig` | `FLASK_ENV=production` | Secure cookies, strict CSP, Redis rate limiting |

---

## Known Limitations / Roadmap

- Password reset (forgot password flow) not yet implemented
- Email notifications not yet wired (in-app notifications are live; SMTP integration pending)
- Dental cleans service type stubbed in nav but not yet built
- No automated test suite yet (unit testing setup in progress)

See `FEATURES.md` for the full feature tracker.
