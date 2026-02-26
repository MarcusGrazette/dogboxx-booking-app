# Feature Tracker — Dog Walking Booking App

> Priority: **P1** = must-have, **P2** = should-have, **P3** = nice-to-have
> Effort: **S** = small (< 1hr), **M** = medium (1-4hrs), **L** = large (4hrs+)
> Status: 🔲 todo · 🔧 in progress · ✅ done · ❌ dropped

| # | Priority | Effort | Status | Feature | Notes |
|---|----------|--------|--------|---------|-------|
| 1 | P1 | M | 🔲 | **Client onboarding improvements** | Multi-dog support, edit after completion |
| 2 | P1 | L | 🔲 | **Booking workflow with capacity checks** | Enforce walker max capacity per slot, waitlist logic |
| 3 | P1 | M | 🔲 | **Admin booking review** | Approve/reject requests, bulk actions |
| 4 | P2 | M | ✅ | **Walker pickup list** | Daily route view with dog details, addresses, order |
| 5 | P2 | S | ✅ | **Dog DOB field** | Replaced years/months dropdowns with date_of_birth date picker |
| 6 | P2 | L | 🔲 | **Email notifications** | Booking confirmation, status changes, reminders |
| 7 | P3 | M | 🔲 | **Client booking history** | Past bookings list with status and walker info |
| 8 | P3 | S | 🔲 | **Admin dashboard stats** | Booking counts, utilisation, revenue summary |
| 9 | P1 | M | 🔧 | **Client profile edit** | Edit address, notification prefs, dog details + photo. Pre-filled from onboarding. Merges old #5 (dog edit) |
| 10 | P1 | M | 🔲 | **Password reset (forgot password)** | Email-based reset flow: "Forgot password?" link on login → enter email → token email → set new password |
| 11 | P3 | L | 🔲 | **Firebase Auth migration** | Replace Flask-Login auth with Firebase Auth. Handles MFA, social login, OTP. Plan as a full auth layer swap post-launch |
| 12 | P2 | M | ✅ | **Walker unavailability** | Date-specific exceptions to default schedule. Per-slot, soft block on admin dashboard |
| 13 | P2 | S | ✅ | **Walker schedule page** | Shows default weekly schedule + manage unavailability exceptions |
| 14 | P1 | M | ✅ | **is_admin role model** | Admins can also be walkers. Walkers promotable via is_admin flag |

---

## How to use this file

**Adding a request:** Add a row to the table. Assign a `#`, estimate priority and effort.

**Effort guide:**
- **S (small):** Template tweak, new route, simple query — under an hour
- **M (medium):** New page/flow, moderate logic, touches 2-3 files — a few hours
- **L (large):** New subsystem, multiple routes/templates, external integrations — half a day+

**Reassessing:** Update priority/effort as we learn more. Move notes into the Notes column.
