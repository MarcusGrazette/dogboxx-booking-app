# Contributing to DogBoxx

This guide covers full local setup against PostgreSQL, the test workflow, and the
branching model.

---

## Branching & Pull Requests

- **`develop`** — active development; branch off here.
- **`main`** — production. Railway auto-deploys on merge to `main`.
- Create a `feature/<short-name>` branch off `develop` and open a PR back to `develop`.
- Deploy by merging `develop` → `main` via PR.
- If you stack a PR on another branch, merge the lower one first (merge commit, not squash) and delete its branch, so GitHub retargets the upper PR to `develop`.

After any schema change, generate and commit a migration (`flask db migrate -m "…"`) —
never add columns via standalone scripts, so CI's `flask db check` catches drift.

---

## Full Local Setup (PostgreSQL)

Production and CI run **Python 3.12** and **PostgreSQL 18**; use the same versions locally. The
app refuses to start without a `postgresql://` `DATABASE_URL`.

### 1. Clone and create the virtual environment

```bash
git clone git@github.com:MarcusGrazette/dogboxx-booking-app.git
cd dogboxx-booking-app
uv venv --python 3.12 venv        # or: python3.12 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
uv pip install -r requirements-dev.txt   # app + test tooling (pytest etc.)
```

[`uv`](https://docs.astral.sh/uv/) downloads its own Python 3.12 if your system doesn't
have it, and the venv keeps using it after an OS upgrade changes the system Python. If
you use plain `python3.12 -m venv`, install with `pip` instead of `uv pip`.

`requirements-dev.txt` includes `requirements.txt` plus the test suite tooling. Use the
plain `requirements.txt` only for runtime-only installs (production does this automatically).

### 2. Install and start Postgres 18

```bash
# Debian/Ubuntu (Ubuntu 26.04 ships 18; older releases need the apt.postgresql.org repo)
sudo apt install -y postgresql postgresql-client libpq-dev
sudo systemctl enable --now postgresql        # starts now + on every boot

# macOS (Homebrew)
brew install postgresql@18
brew services start postgresql@18
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
python seed.py          # seeds test data — run once, on an empty database
flask run --host=0.0.0.0 --port=5000
```

The app is available at `http://localhost:5000`.

---

## Test Accounts

After seeding, these accounts are available:

| Role | Email | Password |
|---|---|---|
| Admin + Walker (Owner, super-admin) | lydia@dogboxx.org | changeme123! — you'll be asked to set a new password on first login |
| Admin + Walker | admin@dogboxx.org | adminpass |
| Walker | testwalker@dogboxx.org | walkies123 |
| Client | john.doe@example.com | clientpass |

The seed also creates 10 client accounts with dogs — see `seed_data/` for the full list.

`seed.py`'s test-data step isn't idempotent: running it a second time on a seeded database fails on duplicate emails. To re-seed, drop and recreate the database (or delete the test data) first.

### Demo data

Two demo seed scripts add realistic booking data:

```bash
python seed_may_demo.py   # ~10 walks/slot/weekday across two weeks, ±2 randomised, 3–5 drop-ins/day
python seed_june_demo.py  # mix of normal and at-capacity days to demonstrate the waitlist
```

Both use fixed May/June 2026 dates, so their bookings now appear in the past. Navigate the calendar back to those months to see them.

---

## Running Tests

The suite runs against **PostgreSQL**, matching CI and production. The full suite
takes under a minute.

```bash
pytest                          # runs against dogboxx_test (Postgres)
pytest tests/test_auth.py       # a single file
```

Tests pick up `TEST_DATABASE_URL` from `.env` automatically (it's loaded before config is
read — no shell export needed). If it's unset, `TestingConfig` falls back to the
credentials CI uses (`postgresql://dogboxx:dogboxx@localhost:5432/dogboxx_test`); locally
that simply fails to authenticate rather than touching any real data.

Don't run two `pytest` processes against the test database at once — each run creates and
drops the tables, so concurrent runs deadlock or fail spuriously.

A few tests run browser JavaScript under **Node** (`tests/test_board_js.py`,
`tests/test_london_date.py`). They're skipped if `node` isn't on your `PATH`; CI has it.

### What CI runs

On every push and pull request to `main`/`develop`, GitHub Actions runs:

1. `flask db upgrade` — the full migration chain from an empty Postgres database.
2. `flask db check` — fails if a model change has no matching migration.
3. `pytest` — the suite, on Postgres.
4. `pip-audit` — a separate job that installs `requirements.txt` into a clean environment and fails on any known CVE in it.

---

## Configuration

The app uses three config classes in `config.py`:

| Config | Used when | Notes |
|---|---|---|
| `DevelopmentConfig` | `FLASK_ENV=development` | Debug on, CSP report-only; SQL logging opt-in via `SQL_ECHO=1` |
| `TestingConfig` | Tests | PostgreSQL (`TEST_DATABASE_URL`), CSRF disabled |
| `ProductionConfig` | `FLASK_ENV=production` | Secure cookies, enforced CSP, DB statement timeouts; rate limits in Redis when `REDIS_URL` is set |
