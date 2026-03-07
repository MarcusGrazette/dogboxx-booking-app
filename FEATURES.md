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
| 20 | P1 | S | ✅ | **Brand colours / Phase 1 polish** | CSS variable overrides, dark navbar, pink accents, auth card login |
| 21 | P1 | M | ✅ | **AdminLTE sidebar / Phase 2** | admin_layout.html, sidebar nav (Dashboard/Walks/People/Dental stub), page header bar |
| 22 | P1 | L | 🔧 | **Notification system** | Persistent bell notifications with read audit trail. Foundation done (model, helpers, bell UI, routes). Integration work remaining — see sub-tasks below |
| 22a | P1 | S | 🔲 | **Notif: wire booking_confirmed** | Call create_notification() in admin assign_walker route when status → confirmed. Notify client. |
| 22b | P1 | S | 🔲 | **Notif: wire booking_requested** | Call create_notification() when client submits a booking. Notify admin. |
| 22c | P1 | S | 🔲 | **Notif: wire booking_cancelled** | On admin cancel, notify client. On client cancel (future), notify admin. |
| 22d | P1 | S | 🔲 | **Notif: wire walker_assigned** | When admin drag-drops to a walker, notify that walker. |
| 22e | P2 | M | 🔲 | **Notif: admin audit view on client page** | Show notification history (sent_at, read_at) on the admin client detail page |
| 23 | P1 | L | 🔲 | **Dental cleans service type** | Admin: manage available date+time slots. Client: book from available slots. Data model: dental_slots table. Slot-based approach agreed. |
| 7 | P3 | M | 🔲 | **Client booking history** | Past bookings list with status and walker info |
| 8 | P3 | S | 🔲 | **Admin dashboard stats** | Booking counts, utilisation, revenue summary |
| 9 | P1 | M | 🔧 | **Client profile edit** | Edit address, notification prefs, dog details + photo. Pre-filled from onboarding. Merges old #5 (dog edit) |
| 10 | P1 | M | 🔲 | **Password reset (forgot password)** | Email-based reset flow: "Forgot password?" link on login → enter email → token email → set new password |
| 11 | P3 | L | 🔲 | **Firebase Auth migration** | Replace Flask-Login auth with Firebase Auth. Handles MFA, social login, OTP. Plan as a full auth layer swap post-launch |
| 12 | P2 | M | ✅ | **Walker unavailability** | Date-specific exceptions to default schedule. Per-slot, soft block on admin dashboard |
| 13 | P2 | S | ✅ | **Walker schedule page** | Shows default weekly schedule + manage unavailability exceptions |
| 14 | P1 | M | ✅ | **is_admin role model** | Admins can also be walkers. Walkers promotable via is_admin flag |
| 15 | P1 | S | ✅ | **Fix profile photo upload** | Fixed: route missing POST method; FilePond config matched to onboarding layout |
| 16 | P1 | S | ✅ | **Prevent duplicate bookings** | Block same dog+date+slot. Allow max 2 bookings per dog per day (one per slot) |
| 17 | P1 | S | ✅ | **Fix admin reorder 500 error** | /admin/reorder_pickups returning 500. Debug and fix |
| 18 | P2 | S | ✅ | **Default dog image** | Create a default-dog.png placeholder (dog emoji or simple icon). Fix 404 |
| 19 | P2 | S | ✅ | **Flash message layout shift** | Match admin's no-shift flash behaviour across all pages. Use fixed/overlay positioning |

---

## How to use this file

**Adding a request:** Add a row to the table. Assign a `#`, estimate priority and effort.

**Effort guide:**
- **S (small):** Template tweak, new route, simple query — under an hour
- **M (medium):** New page/flow, moderate logic, touches 2-3 files — a few hours
- **L (large):** New subsystem, multiple routes/templates, external integrations — half a day+

**Reassessing:** Update priority/effort as we learn more. Move notes into the Notes column.

## Backlog (Low Priority)
- [ ] Client profile photo upload — show photo in navbar avatar instead of initials; collect during onboarding alongside dog photo
