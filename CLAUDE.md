# CLAUDE.md — Dogboxx Booking App

## Project Overview

Dogboxx is a booking management platform for a real small dog walking business (~50 clients). It handles the full lifecycle: client request → walker assignment → pickup. Flask + PostgreSQL, Blueprint architecture.

**Repo:** `git@github.com:MarcusGrazette/dogboxx-booking-app.git`
**Local:** `/home/marcus/claude/dogboxx-booking-app/`

---

## Branching & Deployment

- **`main`** — production, deployed automatically on Railway. Never push directly.
- **`develop`** — active development. All work goes here or in a feature branch off here.
- **Feature branches** — create `feature/<name>` off `develop`, PR back to `develop`.
- **Deploy** — merge `develop` → `main` via GitHub PR. Railway auto-deploys on merge to `main`.

Always check out `develop` before starting new work.

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12, Flask, SQLAlchemy, Flask-Migrate (Alembic) |
| Auth | Flask-Login, Flask-WTF (CSRF), Flask-Limiter (rate limiting) |
| Database | PostgreSQL (prod), SQLite supported for quick local dev |
| Frontend | Jinja2, Bootstrap 5, AdminLTE 3, Bootstrap Icons, vanilla JS |
| Email | Resend API (`noreply@dogboxx.org` verified) |
| CI | GitHub Actions — Postgres-backed: `flask db upgrade` → `flask db check` → `pytest` on push/PR |

---

## App Structure

```
app/
  blueprints/
    admin/      Admin routes (dashboard, bookings, dogs, clients, walkers, invoicing, newsletter)
    auth/       Login, logout, password reset, unsubscribe
    client/     Home, onboarding, bookings, profile
    walker/     Pickup list, schedule, unavailability
    api/        JSON endpoints (calendar data, booking actions)
  templates/    Jinja2 templates (admin_layout.html, base.html, partials/)
  static/       CSS, JS, images, uploads
  models.py     SQLAlchemy models
  capacity.py   Walk capacity and auto-assign logic
  forms.py      WTForms definitions
  utils/        Notifications, decorators, DB error handling, uploads
config.py       Dev / Test / Production config classes
migrations/     Alembic migration files
seed.py         Base seed data
seed_demo_bookings.py  Demo booking data for presentations
```

---

## Data Model

| Model | Notes |
|---|---|
| `User` | All users. `role` = client/walker, `is_admin` boolean. Business owner is walker + admin. |
| `Client` | Address + onboarding data |
| `Walker` | Linked to User via 1:1 |
| `Dog` | Profile (name, breed, DOB, photo, notes, pickup_instructions). Pickup notes are per-dog and shared by all co-owners. |
| `DogOwner` | Many-to-many dogs ↔ users, `role` = primary/secondary |
| `Booking` | Links user, dog, service type, date, slot, walker, status |
| `ServiceType` | Currently: Group Walk, Drop-in |
| `WalkerSchedule` | Default weekly pattern (day_of_week + slot) |
| `WalkerUnavailability` | Date-specific exceptions to a walker's schedule |
| `WalkerAdHocAvailability` | One-off available days outside a walker's default schedule |
| `PricingConfig` | Pricing history (used by invoicing). Fields: `price_per_walk`, `double_slot_discount`, `weekly_discount` (per-walk, for weeks ≥5 walks), `price_per_drop_in`, `effective_from`. |
| `Notification` | In-app bell notifications (read/unread) |

**Booking statuses:** `requested` → `confirmed` / `waitlisted` / `cancelled`

**Capacity:** `available walkers × max_per_walker` per slot/day. Exceeded → `waitlisted`. Auto-assign picks least-loaded walker and confirms immediately if capacity available.

---

## Environment Variables

See `.env.example`. Required:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask sessions / CSRF |
| `FLASK_ENV` | `development` or `production` |
| `DATABASE_URL` | PostgreSQL connection string |
| `RESEND_API_KEY` | Email via Resend |
| `MAIL_FROM` | Verified sender (noreply@dogboxx.org) |
| `APP_BASE_URL` | Public URL — used in password reset links |

Optional: `REDIS_URL` for persistent rate limiting (falls back to in-memory).
Optional: `TEST_DATABASE_URL` — PostgreSQL URL used by `TestingConfig` in CI. If not set, tests use SQLite.

---

## Local Development

```bash
# Activate venv
source venv/bin/activate

# Apply migrations
flask db upgrade

# Seed test data
python seed.py

# Run dev server
venv/bin/flask run --host=0.0.0.0 --port=5000
```

**Test accounts:**

| Role | Email | Password |
|---|---|---|
| Admin + Walker | admin@dogboxx.org | adminpass |
| Walker | testwalker@dogboxx.org | walkies123 |
| Client | john.doe@example.com | clientpass |

---

## Tests

```bash
pytest                    # run all tests (SQLite locally)
pytest tests/test_auth.py # specific file
```

145 tests across auth, bookings, capacity, multi-owner, notifications, drop-in, invoicing. All should pass. CI runs on every push/PR — don't merge anything that breaks CI.

CI runs three steps in order against a real Postgres instance:
1. `flask db upgrade` — verifies the full migration chain runs cleanly from scratch
2. `flask db check` — fails if any model column lacks a matching migration (catches standalone scripts that bypass Alembic)
3. `pytest` — runs the test suite against Postgres (enforces enum constraints, catches type differences invisible in SQLite)

---

## Railway (Production)

- Railway CLI: `/home/marcus/.npm-global/bin/railway` (v4.36.1, logged in as Marcus)
- Add `railway` to PATH or use the full path
- Project linked in `railway.toml`
- Auto-deploy on merge to `main`
- PostgreSQL and (optionally) Redis plugins provisioned on Railway
- Health endpoint: `GET /health` — used by Railway healthcheck

Useful commands:
```bash
railway logs              # tail production logs
railway shell             # interactive prod shell (local subshell with Railway env vars — not SSH)
```

**Running migrations on prod from local** — `railway run` injects the internal `DATABASE_URL` (unreachable locally). Use `DATABASE_PUBLIC_URL` instead:
```bash
DATABASE_URL="<DATABASE_PUBLIC_URL value>" FLASK_ENV=production flask db upgrade
```
`DATABASE_PUBLIC_URL` must be set in Railway dashboard variables first.

**CLI commands for admin setup:**
```bash
flask create-admin        # create admin user + walker record (prompts for details)
flask make-walker --email user@example.com  # add Walker record to existing user
flask seed-service-types  # seed Group Walk / Drop In / Day Care service types (idempotent)
```

---

## Key Files to Know

| File | Purpose |
|---|---|
| `app/capacity.py` | Walk capacity checks + `auto_assign_walker()` |
| `app/models.py` | All SQLAlchemy models |
| `app/utils/notifications.py` | Notification creation helpers |
| `config.py` | Dev/Test/Prod config classes |
| `FEATURES.md` | Feature tracker — check here before starting new work |
| `Procfile` | Railway start command |
| `railway.toml` | Railway project config |

---

## Workflow Notes

- Check `FEATURES.md` for open items before starting new features
- Feature branches: `feature/<short-name>` off `develop`
- PRs to `develop` first; then `develop` → `main` for production
- After any schema change: `flask db migrate -m "description"` + commit the migration. Never add columns via standalone scripts — always use Alembic so CI catches drift.
- **Always** bump `CACHE_VERSION` in `app/static/js/sw.js` whenever `brand.css`, `reusable-calendar.css`, `reusable-calendar.js`, or any other file in `PRECACHE_ASSETS` changes. Forgetting this causes browsers to serve stale cached assets after deploy.
- This is a live production app with real clients — be careful with data migrations and deploys
- **PR workflow**: push changes to `develop` and notify the user to test first. Only open a PR to `main` after the user has confirmed the changes look good. This avoids merging then immediately following up with a fix PR.
- **Notification preferences**: `notification_preference` on `User` is always `'email'` — WhatsApp was removed. The email toggle on `/profile` controls `email_marketing` (newsletter), not booking notification emails.
- **Invoicing discounts**: double-slot discount (same-day AM+PM walks) and weekly discount (≥5 confirmed walks in ISO week) are both applied in `app/utils/invoicing.py`. Both are configurable via `/admin/revenue`.
