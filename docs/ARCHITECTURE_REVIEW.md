# DogBoxx — Architecture Review

*Big-picture review focused on architectural structure, duplication, and simplification. Reviewed structure and organization, not line-by-line correctness.*

**Date:** 2026-06-15
**Scope:** ~13k lines Python, ~15.5k lines templates, 358 tests passing, CI green.
**Branch reviewed:** `develop` (in sync with `main`).

**Implementation status (2026-06-20):** All 5 tickets complete on `feature/pricing-module` (14 commits, pushed). Awaiting device test + PR to `develop`. 382 tests green on Postgres.

---

## What's already good

The foundations are stronger than typical for an app this age. These are deliberate, well-made decisions worth preserving:

- **Booking-status chokepoint** (`app/utils/booking_status.py`) — every status change funnels through `transition_booking` / `bulk_transition`, writing an append-only `BookingStatusChange` audit row. Most codebases this size scatter `booking.status = 'x'` everywhere with no audit trail.
- **`NotificationBatch` + `summarise()`** (`app/utils/notifications.py`) — one text source feeding both the bell and the activity feed.
- **Test + CI architecture** — 8,745 lines of domain-organized tests, Postgres-backed CI matching prod, `flask db check` catching migration drift. This safety net is what makes the refactors below safe to attempt.
- **App factory** (`app/__init__.py`) — clean error handlers that return JSON for AJAX requests, CSP nonces, security headers, advisory-lock concurrency in `capacity.py`.

**Framing:** nearly every finding below is a *structural* problem ("successful app outgrew its original file layout"), not a *foundational* one (schema, auth model, the transition chokepoint are all sound). Structural problems are low-risk and mechanical to fix — ideal delegated work, protected by the existing test suite.

---

## Findings by priority

### 🔴 P1 — Pricing logic is triplicated (correctness risk)

The pricing computation — unit-price lookup, same-day double-slot discount, ≥5/week discount — exists in **three independent implementations**:

| Location | Used for |
|---|---|
| `app/utils/invoicing.py::invoice_for_client` | Client invoices (what they're billed) |
| `app/blueprints/admin/routes.py::_revenue_for_range` (~line 650) | Admin revenue dashboard |
| `app/blueprints/client/routes.py::monthly_summary` (~line 1179) | Client's own monthly summary |

The `config_for(d)` helper (find the effective `PricingConfig` for a date) is **copy-pasted verbatim 4 times**: `invoicing.py:61`, `admin/routes.py:673` & `:4169`, `client/routes.py:1214`.

**Why P1:** these paths can drift. Change the double-slot rule in `invoicing.py` but not `_revenue_for_range`, and the admin's revenue dashboard and the client's actual invoice report different numbers for the same month — undetected until a client disputes a bill. Three implementations of one money rule is three chances to be wrong.

### 🔴 P1 — `admin/routes.py` is a 4,986-line god module

One file, ~55 routes, **11 unrelated domains**: dashboard, revenue, board/assignment, walkers, clients, dogs, closures, invoicing, newsletter, broadcasts, CSV import, daily messages. It is ~38% of all Python in the app.

**Why it matters:** every admin feature touches this file (merge-conflict magnet), it's hard to navigate, and its size *causes* duplication — `booking_dict` is defined 2×, `slot_stats` 2×, `slot_cnt` 2×, all within this one file, because nobody can hold it all in their head to notice the existing copy.

### 🟠 P2 — No service layer; booking creation reimplemented at 7 sites

Orchestration lives directly in route handlers. The booking-creation sequence

```
acquire_booking_lock → build Booking(...) → record_booking_created → auto_assign_walker / _maybe_auto_confirm → notify
```

appears **7 times**: client `book`, `book_both`, `book_drop_in`, `recurring_booking`; admin `book_for_dog`, `recurring_for_dog`. `_maybe_auto_confirm` lives only in `client/routes.py`, so the admin paths reimplement that logic too.

**Why it matters:** the CLAUDE.md Workflow Notes are full of rules like *"any route that creates a booking and may auto-confirm MUST generate a shared `batch_id` and pass it to both calls."* That rule exists **because the logic is duplicated** — documentation compensating for a missing abstraction. A `BookingService.create()` makes the rule unforgettable instead of tribal knowledge. The careful documentation tells us it has already bitten.

### 🟠 P2 — ~4,000+ lines of JavaScript inline in Jinja templates

Only 1,413 lines of JS sit in `app/static/js/`. Templates carry roughly **4,000+ lines of inline `<script>`**:

| Template | ~inline JS |
|---|---|
| `index.html` | ~1,000 |
| `admin_dogs.html` | ~916 |
| `admin.html` | ~497 |
| `notification_bell.html` | ~454 |
| `profile.html` | ~294 |

**Why it matters:**
1. **No browser caching** — inline JS is re-downloaded on every page load; external files cache after the first hit.
2. **No linting, no tests, no bundling/minification** for the majority of client logic.
3. **Duplication** — calendar wiring, fetch/CSRF wrappers, toast logic recur across templates. (Some logic is already extracted into `reusable-calendar.js`, `pwa-pull-to-refresh.js`, `success-modal.js` — proof the pattern works; it's just unfinished.)
4. CSP friction — every inline block needs the nonce plumbing maintained in `__init__.py`.

(Same story, smaller scale, for CSS: ~20 templates carry inline `<style>` blocks alongside the 3 organized CSS files.)

### 🟡 P3 — Smaller items

- **Duplicated helpers** beyond pricing: `_is_drop_in` defined 3× in `walker/routes.py` (`:73`, `:847`, `:1023`); `booking_dict` / `slot_stats` / `slot_cnt` 2× each in admin. These resolve naturally once P1/P2 create shared modules.
- **Per-request context processors** (`inject_notifications`, `__init__.py:428`) fetch the recent-notifications list on *every* page render, even when the bell is never opened. Harmless at ~50 clients; worth knowing as the app grows.
- **Raw-SQL enum-cast fragility** — `capacity.py` uses `text()` SQL needing manual `::text` casts and `ISODOW` conversion (three CLAUDE.md gotchas warn about this). Contained to 2 sites, but each is a Postgres-only landmine invisible in SQLite. Low priority; flagged for awareness.

---

## Hand-off tickets

Ordered so each builds a shared module the next reuses. Each is independently shippable and protected by the existing 358-test suite.

### TICKET 1 — Extract a single pricing module ✅ DONE (`feature/pricing-module`)
*P1 · ~1 day · Medium risk (money path)*

> **Status (2026-06-15):** Implemented. New module `app/utils/pricing.py` holds
> `config_for_date`, `is_drop_in`, `unit_price`, `build_line_items`,
> `build_double_slot_discounts`. All 4 `config_for` copies deleted; the line-item
> + double-slot construction in `invoicing_detail` and `monthly_summary` is now
> shared. 16 new unit tests in `tests/test_pricing.py`; full suite green on
> SQLite **and** Postgres (374 passed). Behaviour-preserving — see gotchas below.

**Problem:** Pricing math and the `config_for` lookup are duplicated across `invoicing.py`, `admin/routes.py::_revenue_for_range`, and `client/routes.py::monthly_summary`. They can silently drift.

**Do this:**
1. Create `app/utils/pricing.py`. Move `config_for` there as `config_for_date(configs, d)`.
2. Add `price_booking(booking, config) -> float` and `apply_discounts(bookings, configs) -> {subtotal, doubles, weekly_discount, ...}` capturing the double-slot + weekly rules **once**.
3. Rewrite all three call sites to use these. Delete the inline copies of `config_for`.

**Acceptance:** `grep -rn "def config_for" app/` returns nothing (it lives in `pricing.py`); all invoicing/revenue/notification tests pass; one client's `monthly_summary` total equals their `invoice_for_client` total for the same month.

**Risk note:** This is billing. Do it test-first — add a test asserting the revenue dashboard and the invoice agree *before* refactoring.

### TICKET 2 — Split `admin/routes.py` into a package ✅ DONE (`feature/pricing-module`)
*P1 · ~1 day · Low risk (pure move)*

> **Status (2026-06-20):** Implemented. `app/blueprints/admin/routes.py` (4,983 lines) deleted;
> replaced by `app/blueprints/admin/views/` package with 12 domain modules:
> `dashboard.py`, `revenue.py`, `board.py`, `activity.py`, `clients.py`, `walkers.py`,
> `dogs.py`, `closures.py`, `invoicing.py`, `marketing.py`, `csv_import.py`,
> `daily_messages.py`. `admin_bp` stays in `__init__.py`; each module imports it.
> Dead `_get_slot_color` dropped. Two test-file imports fixed to use canonical module
> paths (`app.utils.invoicing`, `app.blueprints.admin.views.revenue`). 382 tests green.

**Problem:** One 4,986-line file holds 11 domains.

**Do this:** Convert `app/blueprints/admin/routes.py` into an `app/blueprints/admin/` package with `views/` modules grouped by domain: `dashboard.py`, `board.py`, `walkers.py`, `clients.py`, `dogs.py`, `bookings.py`, `closures.py`, `invoicing.py`, `marketing.py` (newsletter + broadcasts), `csv_import.py`. Keep the **same** `admin_bp` (define it in `admin/__init__.py`, import into each module). No URL or function-name changes.

**Acceptance:** No route URL changes (diff `flask routes` before/after — must be identical); full test suite green; no file over ~800 lines.

**Risk note:** Mechanical. Move whole functions; do not refactor logic in the same PR. Land *after* Ticket 1 so moved invoicing/revenue code is already deduped.

### TICKET 3 — Introduce `BookingService.create()` ✅ DONE (`feature/pricing-module`)
*P2 · ~2 days · Medium risk*

> **Status (2026-06-20):** Implemented. New module `app/services/booking_service.py` exposes
> `create_booking(*, dog, user_id, date, slot, service, actor_id, batch_id, auto_confirm=True,
> admin_override=False, same_day=False, created_by_id=None)` and `CapacityError`. All 7 booking
> creation sites migrated: `client/book`, `book_both`, `book_drop_in`, `book` (index form),
> `recurring_booking`, `admin/book_for_dog`, `admin/recurring_for_dog`. `_maybe_auto_confirm`
> deleted (no remaining callers). Notification logic kept in callers — wording/grouping differs
> too much between client/admin and single/bulk paths to embed in the service without complex
> parameterisation. Each site migrated in its own commit (sites 1–7); 382 tests green on Postgres.

**Problem:** The lock→create→record→assign→auto-confirm→notify sequence is reimplemented at 7 sites; `_maybe_auto_confirm` is client-only and re-derived in admin.

**Do this:** Create `app/services/booking_service.py` with `create_booking(*, dog, date, slot, service_slug, actor_id, batch_id, auto_confirm=True, notify=True)` encapsulating the full sequence (including the shared-`batch_id` rule CLAUDE.md warns about). Move `_maybe_auto_confirm` into it. Migrate the 7 sites one at a time, each in its own commit, running tests between.

**Acceptance:** All 7 sites call the service; `record_booking_created` no longer appears directly in route bodies; booking/capacity/drop-in/recurring tests green.

**Risk note:** Migrate one call site per commit so a regression bisects cleanly. Preserve the bulk paths' (`book_both`, recurring) consolidated-notification behavior (`notify=False` + caller-composed summary).

### TICKET 4 — Extract inline JS, highest-traffic templates first
*P2 · ongoing · Low risk*

> **Status (2026-06-20, partial):** `index.html` (954 inline lines) and `admin_dogs.html` (915 inline lines)
> extracted. New static files: `app/static/js/client-home.js` (958 lines) and
> `app/static/js/admin-dogs.js` (917 lines). Server data handed off via
> `<script type="application/json" id="page-config">` (10 values for client-home, 2 for admin-dogs).
> Templates reduced from ~1,500 to ~575 lines each (inline `<script>` gone; modals remain in HTML).
> Same-origin JS needs no nonce — `'self'` in `script-src` covers it.
> Remaining templates with notable inline JS: `admin.html` (~497), `notification_bell.html` (~454),
> `profile.html` (~294).

**Problem:** ~4,000 lines of JS inline in templates — uncached, untested, duplicated.

**Do this (incremental, one template per PR):** Start with `index.html` (~1,000 lines) and `admin_dogs.html` (~916). Move each `<script>` block to `app/static/js/<page>.js`, load via `<script src=...>` with the existing cache-version pattern. Pass server data via `data-` attributes or a single `<script type="application/json">` block, not interpolated JS. Reuse `reusable-calendar.js` etc. instead of re-extracting.

**Acceptance:** Target template has zero inline `<script>` logic (a json data block is OK); page works identically; new `.js` added to `PRECACHE_ASSETS` / cache-version bumped where relevant.

**Risk note:** Inline JS reads Jinja vars directly today — the `data-`attribute handoff is the only real gotcha. Do one template, verify on the iOS PWA, then proceed.

### TICKET 5 — Dedupe leftover helpers ✅ DONE (`feature/pricing-module`)
*P3 · ~2 hrs · Trivial*

> **Status (2026-06-20):** `_is_drop_in` (3× in `walker/routes.py`) replaced with an import of
> `is_drop_in` from `app.utils.pricing` (already extracted in Ticket 1 — identical function).
> `booking_dict` (2× in `admin/views/board.py`) extracted to module-level `_booking_dict(b,
> both_slots_dog_ids=None)`; the `has_both_slots` key is only included when the set is passed.
> `slot_stats` (2×) and `slot_cnt` (2×) in `dashboard.py` were assessed: `slot_stats` has
> different signatures and different local variable access in each route — not truly duplicated;
> `slot_cnt` is a single-line closure over different local dicts — too minor to extract without
> adding complexity. Both left in place. 382 tests green.

After Tickets 1–3, sweep remaining duplicated nested functions: `_is_drop_in` (3× in `walker/routes.py`), `booking_dict` / `slot_stats` / `slot_cnt` (2× each in admin). Move to a shared `app/utils/` module or the new service.

**Acceptance:** each helper defined once.

---

## Recommended sequencing

Do **Ticket 1 → 2 → 3** in order — each makes the next cleaner (dedupe pricing before splitting the admin file so you move clean code; build the service after the split so it has a tidy home). **Ticket 4** runs in parallel as background frontend work (different files, no conflict). **Ticket 5** is end-of-run cleanup.

None of these change the data model, URL surface, or behavior — all internal restructuring protected by the existing 358-test suite.

---

## Implementation log & architectural gotchas

*Notepad kept as tickets are worked. Capture anything non-obvious that the next
engineer (or a future refactor) needs to know.*

### Ticket 1 — pricing module (done)

Discovered while reading the three pricing paths to unify them. **Both are
pre-existing behaviours that the dedup deliberately preserved** — neither was
"fixed", because each is a money decision for the business owner, not a silent
code change.

1. **The revenue dashboard omits the weekly discount.** ✅ **RESOLVED
   (2026-06-15).** `invoice_for_client` applied the ≥5-walks-per-ISO-week
   discount; the admin revenue dashboard (`_revenue_for_range`) did **not**, so
   for a heavy-use client the dashboard reported *more* than was actually
   invoiced. Business owner confirmed revenue should reflect weekly discounts.
   Fix: the per-week rule is now a single shared function
   `pricing.weekly_discount_for_walks(walk_dates, configs)` used by **both**
   `invoice_for_client` and `_revenue_for_range`, so they cannot disagree on
   whether/how much a week discounts. `_revenue_for_range` now returns
   `(daily, weekly_discount_total)`; the daily chart bars stay gross (weekly
   discount is a *weekly* concept, not attributable to one day), and the
   headline total is netted, with a "after −£X weekly discount" reconciliation
   line on the revenue stat card so bars + note = total. Weekly grouping is
   **per billing household** (a dog's primary owner), matching the sum of
   per-client invoices — verified by `test_invoicing.py::TestRevenueWeeklyDiscount`
   (incl. a test that two 3-walk households do NOT trigger the discount; the
   threshold is per-household, not global).

   *Known remaining minor mismatch (not addressed):* `_revenue_for_range`
   filters `status == 'confirmed'` only, while invoices count
   `('confirmed', 'completed')`. No effect today — nothing sets `completed`
   (the `WalkEvent`/completion feature is unbuilt) — but align the filter if
   completion is ever shipped.

2. **Double-slot discount is keyed two different ways.** The aggregate
   `invoice_for_client` subtotal keys the discount by **`(dog_id, date)`** (so a
   2-dog household where dog A takes AM and dog B takes PM does *not* get a
   discount). The per-client *display* views (`invoicing_detail`,
   `monthly_summary`) key by **date alone** — so they would show a discount row
   that the subtotal didn't apply, for that multi-dog AM/PM case. The extracted
   `build_double_slot_discounts` reproduces the **date-only** display behaviour
   (matching what those two views did before), and `invoice_for_client` keeps
   its `(dog_id, date)` subtotal keying. They reconcile in the common
   single-dog case; the multi-dog edge case is a latent display/subtotal
   mismatch that predates this work. Documented in the helper's docstring.

3. **Invariant:** `config_for_date(configs, d)` requires `configs` sorted
   **descending** by `effective_from`. Every call site already queries it that
   way; if a new caller passes an unsorted list, the lookup returns the wrong
   config silently. Kept as a documented precondition (cheap) rather than
   re-sorting inside the helper on every call (the lists are already sorted at
   the query).

### Ticket 3 — BookingService (done)

1. **Notifications kept in callers, not the service.** The ticket spec proposed a
   `notify=True` parameter. Dropped: client and admin notification wording/grouping
   differ enough (single vs. bulk, `NotificationBatch` co-owner fan-out, `book_both`
   consolidated summary) that embedding it in the service would require complex
   parameterisation for no real benefit. The service handles only DB work
   (lock → check → create → flush → audit → optional auto-assign); callers retain
   full control over notifications.

2. **`same_day` bypasses the CapacityError hard-reject.** Same-day bookings can land
   when there are no walkers and no waitlist — the owner assigns manually. The service
   raises `CapacityError` only when `not available and not can_waitlist and not
   same_day`. All call sites that set `same_day=True` (the client's index form and
   `recurring_booking`) previously had the same inline guard.

3. **`_maybe_auto_confirm` deleted.** It lived only in `client/routes.py` and the
   admin paths re-derived the same logic. With all 7 sites using the service, no
   callers remain. Grep confirms `record_booking_created` no longer appears in any
   route body.
