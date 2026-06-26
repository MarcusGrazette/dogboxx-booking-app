# Contributing to DogBoxx

This guide covers full local setup against PostgreSQL, the test workflow, and the
branching model. For a quick SQLite-only start, see the [README](../README.md#local-development).

---

## Branching & Pull Requests

- **`develop`** — active development; branch off here.
- **`main`** — production. Railway auto-deploys on merge to `main`.
- Create a `feature/<short-name>` branch off `develop` and open a PR back to `develop`.
- Deploy by merging `develop` → `main` via PR.

After any schema change, generate and commit a migration (`flask db migrate -m "…"`) —
never add columns via standalone scripts, so CI's `flask db check` catches drift.

---

## Full Local Setup (PostgreSQL)

Production and CI run on **PostgreSQL 16** — matching it locally gives full fidelity.
SQLite is supported as a fast escape hatch but silently hides a class of enum and
foreign-key bugs that only surface on Postgres.

### 1. Clone and create the virtual environment

```bash
git clone git@github.com:MarcusGrazette/dogboxx-booking-app.git
cd dogboxx-booking-app
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt   # app + test tooling (pytest etc.)
```

`requirements-dev.txt` includes `requirements.txt` plus the test suite tooling. Use the
plain `requirements.txt` only for runtime-only installs (production does this automatically).

### 2. Install and start Postgres 16

```bash
# Debian/Ubuntu
sudo apt install -y postgresql postgresql-client libpq-dev
sudo systemctl enable --now postgresql        # starts now + on every boot

# macOS (Homebrew)
brew install postgresql@16
brew services start postgresql@16
```

### 3. Create the role and databases

```bash
# Debian/Ubuntu — run as the postgres superuser:
sudo -u postgres createuser dogboxx
sudo -u postgres psql -c "ALTER ROLE dogboxx WITH PASSWORD 'choose-a-dev-password';"
sudo -u postgres createdb -O dogboxx dogboxx          # dev database
sudo -u postgres createdb -O dogboxx dogboxx_test     # test database (used by pytest)
```

On Homebrew, local connections use your macOS username with no password (trust auth), so
you can skip `createuser`/`ALTER ROLE` and just run `createdb dogboxx` / `createdb dogboxx_test`.

### 4. Configure environment variables

Copy `.env.example` to `.env` and fill in the values. The test DB is wired up here too so
plain `pytest` finds it:

```bash
# .env
SECRET_KEY=any-long-random-string
FLASK_ENV=development
FLASK_DEBUG=1
DATABASE_URL=postgresql://dogboxx:choose-a-dev-password@localhost:5432/dogboxx
TEST_DATABASE_URL=postgresql://dogboxx:choose-a-dev-password@localhost:5432/dogboxx_test
```

| Variable | Description |
|---|---|
| `SECRET_KEY` | Flask secret key (any long random string) |
| `FLASK_ENV` | `development` or `production` |
| `FLASK_DEBUG` | `1` for development, `0` for production |
| `DATABASE_URL` | PostgreSQL connection string |
| `TEST_DATABASE_URL` | PostgreSQL connection string for the test database |

### 5. Initialise and run

```bash
flask db upgrade        # runs all migrations against DATABASE_URL
python seed.py          # seeds test data
flask run --host=0.0.0.0 --port=5000
```

The app is available at `http://localhost:5000`.

---

## Test Accounts

After seeding, these accounts are available:

| Role | Email | Password |
|---|---|---|
| Admin + Walker (Owner) | lydia@dogboxx.org | changeme123! |
| Walker | testwalker@dogboxx.org | walkies123 |
| Client | john.doe@example.com | clientpass |

The seed also creates ~13 client accounts with dogs — see `seed.py` for the full list.

### Demo data

Two demo seed scripts add realistic booking data:

```bash
python seed_may_demo.py   # ~10 walks/slot/weekday across two weeks, ±2 randomised, 3–5 drop-ins/day
python seed_june_demo.py  # mix of normal and at-capacity days to demonstrate the waitlist
```

---

## Running Tests

The suite runs against **PostgreSQL by default**, matching CI and production. SQLite
silently hides bugs that only surface on Postgres (native enum constraints, enum-type
comparisons in raw SQL, foreign-key visibility across request connections), so Postgres
is the trustworthy signal.

```bash
pytest                          # runs against dogboxx_test (Postgres)
pytest tests/test_auth.py       # a single file
```

Tests pick up `TEST_DATABASE_URL` from `.env` automatically (it's loaded before config is
read — no shell export needed). If it's unset, `TestingConfig` falls back to the
credentials CI uses (`postgresql://dogboxx:dogboxx@localhost:5432/dogboxx_test`); locally
that simply fails to authenticate rather than touching any real data.

### Fast SQLite escape hatch

For a quicker inner loop (the suite is ~2–3× faster on in-memory SQLite):

```bash
USE_SQLITE=1 pytest
```

Use this for rapid iteration, but **re-run against Postgres before pushing** — CI always
runs on Postgres regardless.

---

## Configuration

The app uses three config classes in `config.py`:

| Config | Used when | Notes |
|---|---|---|
| `DevelopmentConfig` | `FLASK_ENV=development` | Debug on, SQLAlchemy echo, CSP report-only |
| `TestingConfig` | Tests | PostgreSQL by default (`USE_SQLITE=1` for SQLite), CSRF disabled |
| `ProductionConfig` | `FLASK_ENV=production` | Secure cookies, strict CSP, Redis rate limiting |
