# Feature Tracker — DogBoxx

A short public roadmap: what's planned, what's shipped, what was dropped. One line per item.

> **Priority:** P1 must-have · P2 should-have · P3 nice-to-have · P4 someday
> **Effort:** S < 1 hr · M 1–4 hrs · L 4 hrs+
> **Status:** 🔲 todo · 📋 scoped, not started · 🔧 in progress · ✅ done · ❌ dropped

## Open backlog

| # | Priority | Effort | Status | Feature | Summary |
|---|---|---|---|---|---|
| 58 | P3 | M | 📋 | **QuickBooks Online export** | Send monthly invoices to QBO — CSV export first, API push only if needed. |
| 67 | P3 | L | 🔲 | **Progress indicator for large recurring bookings** | Warn before submitting a very long series; show progress while it's created. |
| 69 | P3 | M | 🔲 | **Build pipeline hardening** | Tighten how the production image is built. |
| 75 | P3 | L | 🔲 | **Locking consistency for closure and availability changes** | Make closures and availability changes coordinate with in-flight bookings. |
| 72 | P3 | S | 🔲 | **Handle rate-limited push re-registration** | Back off quietly when push re-subscription is throttled. |
| 24 | P3 | L | 🔲 | **Dental cleans service type** | Admin-managed time slots that clients book into. |
| 74 | P4 | S | 🔲 | **Drop `User.notification_preference`** | Remove a column that only ever holds `'email'`. |
| 76 | P4 | L | 🔲 | **Keyboard operability** | Make the calendar and assignment board fully keyboard-usable. |

## Shipped

### Bookings

| # | Feature | Summary |
|---|---|---|
| 1 | Booking workflow with capacity checks | Capacity = available walkers × per-walker max; waitlist when full; auto-assigns the least-loaded walker. |
| 2 | Recurring bookings (client) | Date range + frequency, expanded server-side; skips weekends and duplicates. |
| 5 | Duplicate-booking prevention | One booking per dog per slot per day, enforced by a database index. |
| 8 | "Book both walks" | Book morning and afternoon in one action. |
| 29 | Drop-in service | Morning/afternoon drop-in visits with their own board, walker flag and pricing. |
| 78 | Booking lifecycle guards | Stale or repeated actions can't reopen or re-cancel a booking; never-confirmed cancellations aren't billed. |
| 77 | Transaction integrity | A failed request can't leave partial changes behind. |

### Admin

| # | Feature | Summary |
|---|---|---|
| 3 | Booking board | Calendar + slot view to confirm, cancel and assign; pickup order sequenced automatically. |
| 4 | Dogs view | Searchable dog list; book on an owner's behalf (one-off or recurring). |
| 20 | Client management | Create, view and edit client accounts, with a per-client notification history. |
| 21 | Walker management | Create walkers and manage their schedules and time off. |
| 22 | Admin is also a walker | Admins can walk too, with their own pickup list. |
| 23 | Dashboard | Headline stats, a 4-week booking chart and a walker availability grid. |
| 26 | Invoicing | Monthly per-client invoices with configurable pricing, late-cancel billing and discounts. |
| 27 | Multiple owners per dog | Primary/secondary owners; co-owners can book and view shared dogs. |
| 28 | CSV client/dog import | Bulk-create clients and dogs from a CSV, with a per-row error report. |
| 34 | Multi-dog clients | Add further dogs to a client from the client detail page. |
| 35 | Bulk booking operations | Preview-then-cancel a dog's upcoming bookings, filterable by service. |
| 36 | Closures | Close a date or range: cancels bookings (with notices) and blocks new ones. |
| 37 | Daily walker messages | Announcements shown on the walkers' pickup list. |
| 38 | Broadcasts | One-shot bell/email message to everyone booked in a date and slot. |
| 47 | Activity feed | One feed of booking changes and account changes, with bulk actions grouped. |
| 66 | Weekly Overview | Week roster for admins and a weekly tab for walkers. |
| 71 | Safe concurrent walker assignment | Simultaneous assignments can't over-fill a walker. |
| 73 | Slot freeze | Hold new bookings for a date/slot as requests instead of auto-confirming. |
| 79 | Double-slot discount fix | The discount only applies when the dog actually gets both walks. |

### Walker

| # | Feature | Summary |
|---|---|---|
| 10 | Pickup list | Daily route with dog photos, addresses and pickup notes, in pickup order. |
| 11 | Schedules | Default weekly schedule, set by the admin. |
| 12 | Unavailability | Date/slot exceptions that reduce capacity automatically. |
| 13 | Self-managed availability | Walkers mark their own time off. |
| 14 | Maps directions | One-tap directions from each pickup card. |
| 15 | Ad hoc available days | Walkers add one-off days outside their usual schedule. |
| 16 | Admin override on allocation | Admin can assign over a walker's unavailability, with a visible warning. |

### Client

| # | Feature | Summary |
|---|---|---|
| 30 | Onboarding | Address, map pin, pickup notes and dog profile on first login. |
| 31 | Profile editing | Address, newsletter preference, dog details and photos. |
| 32 | Monthly walk summary | What was walked and billed each month. |
| 65 | Rich-text pickup notes | Formatted pickup notes plus a reference photo; formatted broadcasts. |

### Notifications & newsletter

| # | Feature | Summary |
|---|---|---|
| 40 | In-app notifications | Bell with unread count and read state. |
| 41–45 | Booking notifications | Confirmations, requests, cancellations and walker assignments, with an admin audit trail. |
| 46 | Notification overhaul | Status-change audit log and grouped notifications for bulk actions. |
| 48 | Web Push | Push notifications and home-screen badge for the installed app (iOS and Android). |
| 60 | Newsletter | Rich-text newsletter with merge tags and test send. |
| 61 | Email opt-out | One-click unsubscribe link in every newsletter. |

### Platform & quality

| # | Feature | Summary |
|---|---|---|
| 6 / 55 | Password reset | Emailed single-use reset link. |
| 50 | PostgreSQL | Postgres with Alembic migrations. |
| 51 | Security hardening | CSRF, rate limiting, CSP, secure cookies, safe uploads, session hardening. |
| 52 | Database indexes | Indexes on the hot booking query paths. |
| 53 | Branching model | `develop` for work, `main` for production, PRs between them. |
| 54 | Test suite | Postgres-backed test suite, run in CI. |
| 56 | CI | Migrations from scratch, schema-drift check, tests and dependency audit on every push and PR. |
| 57 | PWA | Installable app with offline shell, pull-to-refresh and per-user page cache. |
| 59 | July 2026 code review | All findings resolved. |
| 62 | Monitoring | Error tracking and uptime checks via Sentry. |
| 63 | Backup confidence | Tested database restore and upload-backup reconciliation. |
| 64 | Session cleanup | Scheduled removal of expired sessions. |
| 68 | Strict script policy | No inline event handlers; CSP blocks them. |
| 70 | Login lockout | Temporary per-account lockout after repeated wrong passwords. |
| 80 | Code tidy-up | Unused variables removed during the client routes split. |

## Dropped

| # | Feature | Reason |
|---|---|---|
| 7 | Capacity display for clients | An "available" indicator is enough. |
| 25 | Standalone invoicing | Superseded by #26. |
| — | Firebase Auth | Overkill at this scale; Flask-Login is enough. |
| — | Public self-registration | The business creates client accounts itself. |
| — | Walker pickup status tracking | Won't build (owner decision). |

## How to use this file

- **Adding an item:** give it the next free `#`, a priority and effort, and a one-line summary. Put design notes and history in the private handbook, not here.
- **Security-sensitive items:** the row stays neutral (what, not how it could be exploited); the detail goes in the handbook's private backlog.
- **Finishing an item:** move it from the backlog to the right Shipped table.
