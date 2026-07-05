# Code Review — July 2026

> Full-codebase review run 2026-07-03 (senior-engineer pass: best practices,
> performance, resilience, UX consistency). This file is the working tracker —
> update the status column as items ship, and add the commit/PR reference.
> Findings are ordered by priority within each section; the agreed attack order
> is in the checklist at the bottom.

**Context:** overall the codebase is in good shape — the booking-status
chokepoint, canonical pricing module, batched month-data queries, advisory-lock
TOCTOU protection, session rotation on privilege boundaries, and the
migration-head health check are all solid. The findings below are the gaps.

---

## Status checklist (agreed attack order)

| # | Finding | Status | Ref |
|---|---------|--------|-----|
| 1 | psycogreen + pool_pre_ping | ✅ deployed | `9ff4d1d`, PR #143 — merged + deployed 2026-07-03. Logs verified: clean gevent boot, /health 200 (real query through the patched wait callback), SSE Redis listener subscribed on both workers |
| 2 | UserMixin / deactivation doesn't end sessions | ✅ deployed | `322d9b8`, PR #144 — merged + deployed 2026-07-03. Logs verified: clean boot, /health 200, SSE listeners on both workers, live client sessions unaffected. Prod pre-check: zero `active=false` users |
| 3 | Web Push: pass `timeout=10` | ✅ deployed | `600658e`, PR #145 — merged + deployed 2026-07-03. Logs clean (boot, /health 200, SSE listeners); no push traffic observed yet to exercise it live |
| 5 | EXIF strip via re-save | ✅ deployed | `1d1a533`, PR #145 — merged + deployed 2026-07-03. Logs clean; user already verified via manual upload on develop before merge |
| 4 | Static asset caching | ✅ deployed | `06ef7f4`, PR #148 — merged + deployed 2026-07-04. Chose **option (a) split policy** (not versioned URLs): `/static/*` exempted from `no-store`; CSS/images/fonts → `public, max-age=3600`, JS → `no-cache` (revalidate → 304, zero skew, preserves SW network-first). `/sw.js` route's own `no-cache` no longer clobbered. Rationale: SW-controlled clients already serve CSS/images cache-first, so the broad win is the JS 304 (was a full 200 re-download under `no-store`); JS stays revalidated to avoid template↔JS version skew (files aren't fingerprinted). Logs verified clean (both gevent workers, /health 200, SSE listener subscribed, no migration). **Prod headers confirmed live**: JS `no-cache`+ETag → 304/0 bytes on conditional GET, CSS `public, max-age=3600`, `/sw.js` `no-cache`, HTML `no-store`. User smoke-tested on develop (DevTools SW two-row pattern). |
| 6 | Missing indexes (BSC.booking_id, push_subscriptions.user_id) | ✅ deployed | `712af80`, PR #146 — merged + deployed 2026-07-04. Migration ran clean on boot, /health 200 |
| 7 | Board owners_display N+1 | ✅ deployed | `7d60dbb`, PR #146 — merged + deployed 2026-07-04. Logs clean; user verified board rendering pre-merge |
| 15 | UX sweep: native confirm()/alert() | ✅ deployed | `6bf4621`, PR #147 — merged + deployed 2026-07-04. Shared `partials/confirm_modal.html` + `static/js/confirm-modal.js`/`toast.js` (promoted from `layout.html`), included in both layouts. All 10 listed templates converted plus the 3 fast-follow files (`admin_clients.html`, `profile.html`, `onboarding.html`) found during the sweep; `admin-override-form.js` was already using the modal pattern (reference for this work). `walker_schedule.html` was dead code (`/walker/schedule` redirects to `/walker/profile`, no `render_template` reference) — removed. Found and fixed a pre-existing bug in `admin_client_form.html`'s email-change confirm while converting it: `form[method="post"]` is case-insensitive on `method` and matched the navbar logout form instead, so the confirm never fired even natively — now scoped via `emailInput.closest('form')`. User smoke-tested on develop 2026-07-04; logs verified clean post-merge (boot, /health 200, SSE listener subscribed — no migration in this PR). |
| 16 | Flash categories: standardise on "error" | ✅ deployed | `e524c06`, PR #147 — merged + deployed 2026-07-04, bundled with #15 (same UX-consistency theme). 11 `flash(..., "danger")` sites → `"error"`. Logs verified clean. |
| 9 | email.py config source of truth | ✅ deployed | `84de82c`, PR #149 — merged + deployed 2026-07-04. Resolved by **making env the single source** (not routing email.py through config): deleted the 3 dead `RESEND_API_KEY`/`MAIL_NO_REPLY`/`MAIL_REPLY` defs in `config.py` — nothing read them, and `MAIL_REPLY`'s config default (`Lydia <…>`) even contradicted the documented "fall back to MAIL_NO_REPLY". `email.py` keeps reading `os.environ` (stays context-free — no `current_app` coupling). Verified sender resolution + documented fallback. Audited full email routing (no changes needed): password-reset ← `MAIL_NO_REPLY`, newsletter/broadcasts ← `MAIL_REPLY`, bug-reports → `BUG_REPORTS` (lydia@). Noted `MAIL_FROM`/`NEWSLETTER_MAIL_FROM` are orphaned Railway vars (unreferenced) — Marcus to delete in dashboard. Logs clean on deploy (boot, /health 200, SSE listener). |
| 10 | logging.exception traceback sweep | ✅ deployed | `84de82c`, PR #149 — merged + deployed 2026-07-04. Swept **34 broad `except Exception` handlers** across blueprints: `logging.error(f"…: {e}")` → `logging.exception(…)` (message + `{e}` kept; `e` stays referenced). Left the 8 typed handlers (IntegrityError/ValueError/RequestException — expected, return `str(e)` to user), `db_error_handler.py` (deliberate central handler, already logs `traceback.format_exc()`), and `uploads.py:44` (`.warning` for best-effort R2 backup — escalating to ERROR would misrepresent it). 392 tests pass. Post-deploy logs clean — no spurious ERROR/traceback noise under normal traffic (only fires on an actual caught exception). |
| 8 | Onboarding check runs 2 queries every client request | ✅ on develop | Session-cached: `check_password_change_required` now skips the `Client`/`DogOwner` lookups once `session['onboarding_ok']` is set. Flag is cleared on both login and logout (`auth/routes.py`) so it can't leak between accounts on a shared browser session — `session_interface.regenerate()` rotates the session ID but preserves session *data*, so an explicit pop was needed. Residual (accepted, per original finding): a client whose onboarding is later reset skates past the redirect until next login. 392 tests pass (SQLite + Postgres). |
| 11 | Small security/HTTP nits | ✅ on develop | Fixed 2 of 3: `enforce_https` redirect 301→308 (preserves method); CSRF handler validates `request.referrer` is same-host (`urlparse(...).netloc`) before redirecting, else falls back to `auth.login` — closes the open-redirect. Left the deactivated-account message as-is — explicitly called out in the original finding as an accepted support-clarity trade-off, not a bug. |
| 12–14 | Lower priority — see findings | 🔲 todo | Pick up opportunistically |

---

## High priority

### 1. gevent workers + psycopg2 without psycogreen — DB calls block the event loop ✅

`scripts/start.sh` runs gunicorn with `--worker-class gevent --worker-connections 100`,
but psycopg2 is a C extension gevent's monkey-patching can't reach: every DB
query froze the worker's entire event loop — all greenlets, including held-open
SSE streams, serialised behind each query. One slow query (invoicing rollup, a
long `recurring_booking` transaction) stalled everything on that worker.

**Fix (shipped `9ff4d1d`):** register `psycogreen.gevent.patch_psycopg()` at the
top of `run.py`, gated on `gevent.monkey.is_module_patched('socket')` (true only
inside gevent workers — `flask run`/pytest/`flask db upgrade` skip). Also set
`SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}` in `config.py` so idle
connections killed by Railway PG restarts reconnect transparently.

**Risk notes:** no data risk; behavior only observable in prod (worker class
doesn't exist locally). Ship solo, deploy at a quiet time, watch logs. The
accidental serialisation it removes was masking nothing we rely on — booking
correctness is cross-worker safe already (advisory locks + partial unique index
+ IntegrityError→409 catches).

### 2. Deactivating a user doesn't end their session — Flask-Login contract hand-rolled ✅

`User` (`app/models.py:33-37`) defines `is_authenticated` / `is_active` /
`is_anonymous` as **methods**, not properties, and doesn't inherit `UserMixin`.
Flask-Login reads them as attributes; a bound method is always truthy, so
`login_user()`'s own `is_active` gate is silently bypassed. Login works today
only because `auth/routes.py:72` explicitly calls `user.is_active()`.

Consequence: the `user_loader` (`app/__init__.py`) returns the user regardless
of `active`, and Flask-Login never re-checks after login — deactivating a
walker/client leaves their existing session and remember-me cookie fully
functional for up to 14 days.

**Fix:** inherit `UserMixin`; override `is_active` as a `@property` returning
`self.active`; make the `user_loader` return `None` (or check `active`) for
inactive users.

**Risk notes:**
- Every call site using parens — `user.is_active()` — breaks with "bool is not
  callable" once it's a property. Known sites: `auth/routes.py:72` (login) and
  `:225` (forgot-password). Sweep the whole repo including templates.
- The intended effect is itself a knock-on: on deploy, any currently-inactive
  user with a live session is logged out immediately. Before shipping, check
  prod for deliberately-inactive-but-expected-access users:
  `SELECT email, role, is_admin FROM users WHERE active = false` (dual-role
  precedent: PR #142).

### 3. Web Push fan-out is synchronous with no HTTP timeout ✅

The `after_commit` hook (`app/__init__.py`) calls `send_web_push`, which
sequentially POSTs to the push vendor per subscription
(`app/utils/webpush.py:108-123`) and never passes `timeout` to
`pywebpush.webpush()` → the underlying `requests.post` has **no timeout**. A
broadcast to 50 clients = up to 50+ sequential vendor POSTs before the admin's
request returns; one hung vendor connection hangs that request indefinitely
(gevent keeps the worker heartbeat alive, so gunicorn's `--timeout 120` won't
kill it).

**Fix:** pass `timeout=10` through to `webpush()`. (A background-greenlet
fan-out was considered and rejected — more moving parts, app-context plumbing;
pushes are best-effort and the PWA badge already self-heals on next app-open.)

**Risk:** very low — worst case a slow-but-successful push is abandoned.

---

## Performance

### 4. `Cache-Control: no-store` on every response, including all static assets

`add_security_headers` (`app/__init__.py`) unconditionally sets
`no-cache, no-store, must-revalidate` — right for HTML, but it also hits
`/static/*`: all CSS/JS/images/dog photos are re-downloaded on every page view
by anyone not on the installed PWA. It also overwrites the deliberate
`no-cache` the `/sw.js` route sets.

**Fix:** exempt by endpoint (`request.endpoint == 'static'`) so `/sw.js` (its
own route) keeps no-cache and SW updates propagate immediately.

**⚠ Version-skew trap:** CSS/JS files are NOT fingerprinted (`brand.css` is
always `brand.css`; the sw.js `CACHE_VERSION` hook only versions the PWA cache,
not browser HTTP cache). A long `max-age` means HTML updates instantly on
deploy while browsers keep stale JS → template↔JS mismatches. Safe options:
(a) modest `max-age=3600` — bounded staleness, most of the win; or
(b) add `?v=<version>` to static URLs (small `url_for` wrapper keyed off a
config value), then go long-lived. Dog photos are UUID-named / never
overwritten in place — safe for aggressive caching either way.

**Decision (deployed, PR #148):** option (a), refined into a per-type split in
`add_security_headers`. `/static/*` CSS/images/fonts → `public, max-age=3600`;
`/static/js/*` → `no-cache` (kept revalidated because the SW serves JS
network-first — a max-age there would silently re-introduce stale JS, and the
static endpoint's ETag gives cheap 304s anyway). `/sw.js` (its own route,
endpoint `service_worker`) is left untouched so its `no-cache` survives.
Rejected (b): static files aren't fingerprinted and there's no reliable
app-wide version source, so `immutable` would fail *unsafe* (year-long
staleness) on any missed bump — vs (a) which fails safe (≤1h, self-heals, and
a hard-refresh bypasses it). Most clients are on the PWA (CSS/images already
cache-first in the SW), so the win is concentrated on non-PWA admins.

### 5. EXIF stripping copies the image pixel-by-pixel through a Python list ✅

`process_dog_photo` (`app/utils/uploads.py:80-81`) does
`clean_img.putdata(list(img.getdata()))` — for a phone photo near the 10 MB
limit that's tens of millions of Python tuples: seconds of CPU and hundreds of
MB RAM per upload, on a gevent worker where CPU blocks everyone. Pillow only
writes EXIF if asked: re-saving the opened image (as `process_cropped_photo`
already does) drops metadata for free.

**Fix:** delete the putdata dance; just `img.save(...)` after `thumbnail()`.
**Risk:** low — new uploads only; current code already discards EXIF
orientation, so re-saving is behavior-identical.

### 6. Missing indexes on append-only / FK columns ✅

- `booking_status_changes.booking_id` (`app/models.py:391`) — no index. Table
  grows forever by design; `Booking.status_history` filters on it → per-booking
  history reads are sequential scans that get slower every month.
- `push_subscriptions.user_id` — unindexed; fetched per recipient on every
  notification. Tiny table, cheap insurance.

**Fix:** one hand-written Alembic revision (no `--autogenerate` — SQLite enum
noise, see CLAUDE.md), verify upgrade/downgrade on a throwaway SQLite DB.
**Risk:** lowest on the list. `CREATE INDEX` is purely additive — cannot modify
or lose row data; downgrade is `DROP INDEX`. Plain (non-CONCURRENT) build
briefly blocks writes, but at this table size it's milliseconds, and
`start.sh` runs migrations before the new gunicorn starts anyway.

### 7. N+1 queries via `Dog` convenience properties ✅

`Dog.owners_display` and `Dog.primary_owner` (`app/models.py:167-178`) each run
a query per call. `app/blueprints/admin/views/board.py:24` calls
`b.dog.owners_display` inside the booking serialiser loop — ~30-40 extra
queries per board-data fetch, on an endpoint that fires on every admin
interaction.

**Fix:** batch DogOwner lookups into a dict, same pattern the invoicing views
already use. **Risk:** low — read-path refactor; a bug shows wrong owner names,
nothing persisted.

### 8. Two extra queries on every request for every client ✅

`check_password_change_required` (`app/__init__.py`) runs a `Client` query +
`DogOwner` query on **every** request from a client-role user, forever, even
years after onboarding completed.

**Fix (shipped):** stash `onboarding_ok=True` in the session once the check
passes; skip thereafter. The flag is popped on both login and logout
(`auth/routes.py`) — `session_interface.regenerate()` at login only rotates
the session ID, it doesn't clear session *data*, so without an explicit pop a
shared-browser login by a different account would inherit the previous
user's cached flag. **Residual (accepted):** if a client's onboarding is ever
reset, they skate past the redirect until next login.

---

## Best practices

### 9. `app/utils/email.py` bypasses app config

Reads `RESEND_API_KEY` / `MAIL_NO_REPLY` / `MAIL_REPLY` from `os.environ`
directly even though `config.py` defines all three — two sources of truth.
~~**Fix:** use `current_app.config`.~~

**Decision (on develop):** collapsed the duplication from the *other* end —
made the env vars the single source and deleted the config copies (they were
dead: nothing read `config['RESEND_API_KEY']` etc., and `config.py`'s
`MAIL_REPLY` default contradicted the documented MAIL_NO_REPLY fallback).
Keeping `email.py` on `os.environ` avoids adding a `current_app`/app-context
dependency to a leaf utility that today has none (no CLI/background senders,
but future-proof). `BUG_REPORTS_EMAIL` stays in config — it *is* consumed via
`current_app.config`.

### 10. Error handling loses stack traces

Broad handlers like `client/routes.py` `except Exception` blocks log
`f"...: {e}"` — message only; prod 500s give no traceback in Railway logs.
**Fix:** mechanical sweep to `logging.exception(...)` / `exc_info=True`.

### 11. Small security/HTTP nits ✅ (2 of 3)

- ~~`enforce_https` redirects with **301** (lets clients rewrite POST→GET) — use
  **308**.~~ **Fixed** — `redirect(url, code=308)`.
- ~~CSRF error handler redirects to `request.referrer` unvalidated — open
  redirect via attacker-controlled Referer.~~ **Fixed** — `handle_csrf_error`
  now checks `urlparse(referrer).netloc == request.host` (relative referrers,
  which have no netloc, pass through unchanged) and falls back to
  `auth.login` otherwise.
- A deactivated account with the correct password gets a distinct
  "deactivated" message (`auth/routes.py:73`) — tiny account-existence oracle,
  inconsistent with the timing-oracle work. **Left as-is** — accepted
  support-clarity trade-off, not treated as a bug (per original finding).

### 12. `client/routes.py` is ~2,000 lines

Largest file in the app by 2×; mixes booking creation, profile, onboarding,
pause/cancel, calendar JSON. Same treatment as the admin split (PR #139):
`views/` package (bookings, profile, onboarding, calendar). **Risk:** endpoint
names must survive the move or every `url_for('client.…')` breaks — PR #139 is
the proven playbook; the test suite is the net.

### 13. Naive `DateTime` columns storing UTC

All models use `db.DateTime` (no `timezone=True`) with
`datetime.now(timezone.utc)` defaults — consistent UTC-naive, nothing broken,
but a latent trap (aware-vs-naive comparison raises `TypeError`). **Fix:** use
`db.DateTime(timezone=True)` for **new columns only**. **Do NOT migrate
existing columns** — table-rewrite ALTER on live data for no functional gain
(same reasoning as the `completed` enum note in CLAUDE.md).

### 14. `recurring_booking` lock accumulation (no action now — scale note)

A year of "daily, Both" is ~520 iterations × ~10 queries in a single
transaction, accumulating a `pg_advisory_xact_lock` per (service, date, slot) —
hundreds of locks held until final commit, blocking other bookings on those
slots meanwhile. Fine at 50 clients; revisit (chunked commits or coarser lock)
if the 1-year cap is raised or the client base grows materially.

---

## UX consistency

### 15. Native `confirm()`/`alert()` dialogs survive in ~10 templates ✅

`docs/UX_GUIDE.md` establishes toasts + the stacked success modal, but these
still use browser-native dialogs (jarring next to styled modals; in the iOS PWA
`alert()` renders as a bare system dialog):

`admin_broadcasts.html`, `admin_daily_messages.html` (×2),
`admin_client_detail.html`, `admin_client_form.html`, `admin_walkers.html`,
`admin_closures.html`, `walker_schedule.html` (×2), `walker_profile.html`,
`admin_newsletter.html`, `admin-override-form.js`.

**Fix:** shared confirm-modal partial for destructive confirms; toasts/inline
alerts for errors. **⚠ Hazard:** converting a `confirm()` guard and mis-wiring
it = destructive action fires *without* confirmation — manually click-test each
conversion. New inline JS must carry the CSP nonce.

### 16. Flash categories: 35× `"error"` vs 9× `"danger"` ✅

The toast partial normalises `danger` → `error` (`flash_toasts.html:24`) so
users see no difference — pure code-consistency nit. Standardise on `"error"`
(majority + the partial's native vocabulary); sweep the 9 stragglers.

**Fix:** swept all 11 `flash(..., "danger")` call sites (count was 11, not 9,
by the time this was picked up) to `"error"`: `decorators.py` (×2),
`revenue.py`, `walker/routes.py` (×3), `client/routes.py` (×2),
`daily_messages.py` (×2). Scope is server-side `flash()` only — the many
`showToast(msg, 'danger')` JS calls elsewhere (`client-home.js`,
`admin-board-core.js`, and the ones added by #15) are a separate, already-
consistent convention and were left alone.
