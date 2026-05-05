# CLAUDE.md — DogBoxx Booking App

## Project Overview

DogBoxx is a booking management platform for a real small dog walking business (~50 clients). It handles the full lifecycle: client request → walker assignment → pickup. Flask + PostgreSQL, Blueprint architecture.

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
seed.py                Base seed data + loads test data from seed_data/ JSON files
seed_may_demo.py       Demo bookings for May 18–29 2026: 10 walks/slot/weekday (randomised ±2) + 3–5 drop-ins/day
seed_data/             JSON files (users, clients, dogs, walkers, bookings) loaded by seed.py
app/seed_db/seeder.py  Seeder functions — called by seed.py, not run directly
```

---

## Data Model

| Model | Notes |
|---|---|
| `User` | All users. `role` = client/walker, `is_admin` boolean. Business owner is walker + admin. |
| `Client` | Address + onboarding data. **No `pickup_instructions` field** — that lives on `Dog`. |
| `Walker` | Linked to User via 1:1 |
| `Dog` | Profile (name, breed, DOB, photo, notes, pickup_instructions). Pickup notes are per-dog and shared by all co-owners. |
| `DogOwner` | Many-to-many dogs ↔ users, `role` = primary/secondary |
| `Booking` | Links user, dog, service type, date, slot, walker, status |
| `ServiceType` | Currently: Group Walk, Drop-in |
| `WalkerSchedule` | Default weekly pattern (day_of_week + slot) |
| `WalkerUnavailability` | Date-specific exceptions to a walker's schedule |
| `WalkerAdHocAvailability` | One-off available days outside a walker's default schedule |
| `PricingConfig` | Pricing history (used by invoicing). Fields: `price_per_walk`, `double_slot_discount`, `weekly_discount` (per-walk, for weeks ≥5 walks), `price_per_drop_in`, `effective_from`. |
| `DailyMessage` | Admin-authored announcements shown to walkers on the pickup list. |
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
| Admin + Walker (Owner) | lydia@dogboxx.org | changeme123! |
| Walker | testwalker@dogboxx.org | walkies123 |
| Client | john.doe@example.com | clientpass |

---

## Tests

```bash
pytest                    # run all tests (SQLite locally)
pytest tests/test_auth.py # specific file
```

168 tests across auth, bookings, capacity, multi-owner, notifications, drop-in, invoicing, password reset. All should pass. CI runs on every push/PR — don't merge anything that breaks CI.

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
| `app/capacity.py` | Walk capacity checks, `auto_assign_walker()`, `acquire_booking_lock()` (advisory lock for concurrent booking requests) |
| `app/models.py` | All SQLAlchemy models |
| `app/utils/notifications.py` | Notification creation helpers |
| `app/templates/email/password_reset.html` | Password reset email — table-based Jinja template, CSS-only wordmark, no embedded image |
| `config.py` | Dev/Test/Prod config classes |
| `FEATURES.md` | Feature tracker — check here before starting new work |
| `scripts/start.sh` | Production startup — creates volume symlink, runs migrations, starts gunicorn |
| `railway.toml` | Railway project config — `startCommand` calls `scripts/start.sh` |

---

## Workflow Notes

- Check `FEATURES.md` for open items before starting new features
- Feature branches: `feature/<short-name>` off `develop`
- PRs to `develop` first; then `develop` → `main` for production
- After any schema change: `flask db migrate -m "description"` + commit the migration. Never add columns via standalone scripts — always use Alembic so CI catches drift.
- **`CACHE_VERSION` in `app/static/js/sw.js` is bumped automatically** by a PostToolUse hook (`.claude/hooks/bump-cache-version.sh`) whenever `brand.css`, `reusable-calendar.css`, or `reusable-calendar.js` are edited. For other changes to `PRECACHE_ASSETS`, bump manually.
- This is a live production app with real clients — be careful with data migrations and deploys
- **PR workflow**: push changes to `develop` and notify the user to test first. Only open a PR to `main` after the user has confirmed the changes look good. This avoids merging then immediately following up with a fix PR.
- **Notification preferences**: `notification_preference` on `User` is always `'email'` — WhatsApp was removed. The email toggle on `/profile` controls `email_marketing` (newsletter), not booking notification emails.
- **Invoicing discounts**: double-slot discount (same-day AM+PM walks) and weekly discount (≥5 confirmed walks in ISO week) are both applied in `app/utils/invoicing.py`. Both are configurable via `/admin/revenue`.
- **Brand name spelling**: `DogBoxx` — capital D and capital B. Used consistently across all templates.
- **Per-dog editable fields on `/profile`**: pickup instructions, DOB, and health notes all use raw `name="field_{{ dog.id }}"` inputs (not WTForms fields) and are saved by iterating `primary_dogs` in the route — same pattern for all three.
- **Client profile DOB/allergies**: clients can edit these on `/profile`; name/gender/breed remain admin-managed (hidden form fields round-trip the values).
- **CSV import dog gender**: CSV accepts `M`/`F`; must be mapped to `male`/`female` before writing to the `Dog` model (PostgreSQL enum). Already fixed in `csv_import_confirm`.
- **`ClientCreateForm` dog validation**: name + gender required together if either is provided; DOB is optional.
- **Slot override in `assign_walker`**: POST accepts `slot_override: true` (JSON boolean) to bypass the walker schedule check and allow assigning a booking to a different slot than booked. The route captures `old_slot` before updating and sends a `system` notification to the client if the slot changed. A pre-commit conflict check (409) guards against the case where the dog already has an active booking for the target slot.
- **Cross-service duplicate bookings**: a partial unique index on `(dog_id, date, slot)` for active bookings means a dog cannot have two bookings in the same slot regardless of service type. The booking flow treats any same-slot duplicate as an error with a descriptive message (e.g. "Fido already has a drop in booked for that slot") — no override UX.
- **Concurrent booking safety**: `acquire_booking_lock()` in `capacity.py` acquires a PostgreSQL transaction-scoped advisory lock on `(service, date, slot)` before each capacity check. Called at all 7 booking-creation sites. No-op on SQLite.
- **Admin notification fan-out**: all admin notifications use `User.query.filter_by(is_admin=True).all()` — any walker promoted to admin via the toggle on `/admin/walkers` (sets `is_admin=True`) immediately receives the full admin notification stream. They also get full access to `/admin/*` routes.
- **Invoicing DogOwner queries**: `/admin/invoicing` and `/admin/clients/<id>` both use batched DogOwner lookups (4–5 fixed queries, not N+1). Pattern: collect all IDs, batch-fetch, index into dicts, loop body is pure lookups.
- **`seed_data/bookings.json`** uses `date_offset` (integer days from today) instead of hard-coded dates, so test bookings are always relative to the current date. The seeder also accepts `date` (absolute ISO string) for backward compatibility.

---

## Claude Code Tooling

- `.claude/settings.json` — project hooks config (PostToolUse: auto-bump sw.js CACHE_VERSION)
- `.claude/hooks/bump-cache-version.sh` — fires on Edit/Write to watched static assets
- **GitHub MCP** is configured at user scope — use it to read issues, PR status, and CI results directly rather than running `gh` CLI commands
