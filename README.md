# DogBoxx

A booking management app for a small dog walking business. Built with Flask and PostgreSQL, it replaces the previous manual booking system, in which clients messaged the owner, who then added each booking to a shared calendar and to a spreadsheet for billing. With the app, client bookings are entirely self-service, while the owner manages walker allocations and tracks revenue in one place. This saves time, reduces the risk of human error, and gives clients and walkers a better experience.

## Tech Stack

- **Backend:** Python 3.12, Flask, SQLAlchemy, Flask-Migrate (Alembic), Flask-Login, Flask-WTF
- **Database:** PostgreSQL (SQLite supported for local dev)
- **Frontend:** Jinja2 templates, Bootstrap 5, Bootstrap Icons
- **Auth:** Flask-Login with CSRF protection and rate limiting via Flask-Limiter
- **Email:** Transactional and newsletter delivery via the Resend API
- **Push:** Web Push (VAPID) to installed PWAs on iOS and Android via `pywebpush`
- **File uploads:** UUID-named uploads with server-side image validation

## Features

### Client
Clients can:
- Book and manage walks and drop-ins
- Get in-app and push (when installed as a progressive web app) notifications
- View and edit pick up instructions
- Edit their profile (upload a photo, upload a dog photo)
- See monthly walk summary

### Admin
Admins can:
- Confirm, cancel, and assign walkers to bookings on the walk and drop-in boards, and drag to set each walker's pickup order
- Book on behalf of any client (one-off or recurring) and manage their dogs
- Create and manage client accounts, including co-owners and CSV bulk import
- Create walkers and manage their schedules, time off, and ad hoc availability
- Generate monthly invoices with configurable pricing and discounts
- Send newsletters and one-shot broadcasts to booked clients
- Post daily announcements for walkers and mark business closures
- Track revenue and review a full audit trail of every booking change

### Walker
Walkers can:
- Work a daily pickup list with dog photos, addresses, pickup instructions, and ordered sequence
- See daily announcements and navigate to past or future days
- Set their default schedule, mark time off, and add ad hoc available days
- View a monthly summary of walks, drop-ins, and dog counts

## Deployment

- **CI:** GitHub Actions runs `flask db upgrade` → `flask db check` → `pytest` against Postgres on every push and pull request.
- **Hosting:** deployed on Railway; merging `develop` → `main` auto-deploys.
- **Backups:** a nightly job dumps production to Cloudflare R2 object storage, keeping 30 days of snapshots.

---

## Local Development

Quick start (SQLite — no Postgres needed):

```bash
git clone git@github.com:MarcusGrazette/dogboxx-booking-app.git
cd dogboxx-booking-app
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env       # set SECRET_KEY at minimum
flask db upgrade           # create the schema
python seed.py             # load test data
flask run --port=5000
```

The app runs at `http://localhost:5000`. With `DATABASE_URL` unset it falls back to SQLite, which is fine for a quick look.

For production-fidelity work — full PostgreSQL 16 setup, the test workflow, and the branching model — see **[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)**.

After seeding, log in with:

| Role | Email | Password |
|---|---|---|
| Admin + Walker (Owner) | lydia@dogboxx.org | changeme123! |
| Walker | testwalker@dogboxx.org | walkies123 |
| Client | john.doe@example.com | clientpass |

## Running Tests

```bash
pytest                 # runs against Postgres (matches CI)
USE_SQLITE=1 pytest    # faster in-memory SQLite loop
```

Tests run on PostgreSQL by default to match CI and production. See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md#running-tests) for test-database setup.

---

## Project Structure

```
app/
  blueprints/
    admin/          Admin routes — views/ package of domain modules (dashboard, board,
                    dogs, clients, walkers, invoicing, revenue, activity, closures,
                    marketing, csv_import, daily_messages)
    auth/           Login, logout, password reset, unsubscribe
    client/         Client home, onboarding, bookings, profile
    walker/         Pickup list, profile, monthly summary
    api/            JSON endpoints (calendar data, booking actions)
    notifications/  Notification bell endpoints (recent, unread count, mark read)
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
| `ServiceType` | Service definition (Group Walk, Drop In, and Doggy Day Care — seeded but not yet bookable) |
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
