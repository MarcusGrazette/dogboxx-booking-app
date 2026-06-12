# UX Guide — DogBoxx

> **Status:** living conventions doc. Describes what we actually do today, with
> copy-paste reference snippets. Update it when a real UI decision is made —
> don't add aspirational rules nobody follows.

This exists so modals, buttons, and wording stay consistent as the app grows.
Read it before building any new admin/client UI; add to it when you settle a
convention this doc doesn't yet cover.

---

## 1. Success feedback — stacked confirm modal

After a successful create/destructive action that happened in a modal, **dismiss
the source modal and pop a small "Success" confirm modal** rather than leaving an
inline alert and the form open.

- Big green check: `<i class="bi bi-check-circle-fill text-success">` (~3.5rem)
- Heading: **"Success"**
- One-line summary of what happened (see wording rules below)
- **Auto-closes after 2.5s**, with a "Done" button as the manual fallback
- Timer is cleared on manual dismiss so it can't fire on an already-closed modal

Reference implementation (**shared** — reuse it, don't re-create):
- Markup: `{% include 'partials/success_modal.html' %}` (the `#successModal`)
- JS: `app/static/js/success-modal.js` exposes
  `showConfirmed(summary, srcModal, srcEl[, onClose])`

Include the partial once on the page and load `success-modal.js`. The helper is
modal-agnostic — pass the source modal instance and its element so it stacks
correctly without backdrop/scroll-lock conflicts (it waits for the source modal's
`hidden.bs.modal` before showing the success modal). The optional **`onClose`**
callback fires once when the success modal closes (covers both auto-close and
manual "Done") — use it for flows that must `location.reload()` afterwards (e.g.
`/admin/clients` deactivate, where the row needs refreshing). Flows that don't
need a refresh (e.g. `/admin/dogs` book/cancel) omit it.

**Errors** do *not* use the confirm modal — they render inline (red
`alert alert-danger`) and keep the source modal open so the user can retry.

---

## 2. Modal footer buttons

- **Order:** dismiss/secondary on the **left**, primary or destructive action on
  the **right** (Bootstrap's default footer order — first button = leftmost).
- **Always render the action button** from the moment the modal opens. Gate it
  with the `disabled` attribute until the form is valid (e.g. a required preview
  has run) — **never** `display:none`. Hiding it causes the lone Close button to
  sit on the right and then jump left when the action appears; keeping it present
  (disabled) gives every modal an identical, stable `[Close] [Action]` footer.
- Secondary/dismiss: `btn btn-outline-secondary`. Primary: `btn btn-primary`.
  Destructive: `btn btn-danger`.
- On submit, show a spinner + present-tense label ("Pausing…", "Requesting…")
  and re-enable on error. If you swap the button's `innerHTML` for the spinner,
  remember it destroys any inner `<span>` you cached a reference to — reset the
  markup (and re-acquire the reference) on modal-open and on error. See
  `resetBcConfirmBtn()` in `admin_dogs.html`.

---

## 3. Wording

| Concept | UI label | Notes |
|---|---|---|
| Group Walk service | **Walk** | DB enum/slug stays `group-walk`; map slug→label in the template only. |
| Cancelling a range of a dog's walks (**admin**) | **Cancel** + `bi-x-square` icon | Admin is literally cancelling bookings — use *cancel* throughout the admin modal (title, button "Cancel N walks", preview, summary) with the `x-square` icon. Lives in `admin_dogs.html` bulk-cancel modal. |
| Pausing walks (**client**) | **Pause** + `bi-pause-circle-fill` icon | Same underlying action (bookings are cancelled) but the client's mental model is pausing for a holiday, so client-facing copy uses *pause* throughout (`index.html`, `help.html`). |
| Brand name | **DogBoxx** | Capital D, capital B. Never "Dogboxx" / "DogBox". |

### Service iconography

When a service type is shown as an icon (compact tables), use the established
icon pairing — add a `title` with the full name for hover/accessibility:

| Service | Icon |
|---|---|
| Walk (`group-walk`) | `bi-person-walking` |
| Drop-in (`drop-in`) | `bi-house-door` |

**Colour is contextual, not part of the convention.** Colour the icons only
when the colour itself carries meaning — `admin_invoicing_detail.html` uses
green (`text-success`) / blue (`text-primary`) as decorative emphasis on its
own. Where the icon merely labels a row that already carries the meaning
elsewhere (e.g. the upcoming-bookings table in `admin_dogs.html`, where the
walker name sits beside it), leave them default grey. Reference:
`serviceIcon()` in `admin_dogs.html`.

**Rules:**
- A feature's verb must be consistent across its title, buttons, preview text,
  and success summary. Mixed wording (a "Pause" title with a "Cancel" button) is
  the exact drift this guide exists to prevent.
- **Wording can differ by audience for the same underlying action** when the
  mental models genuinely differ — admin "Cancel" vs client "Pause" is the
  canonical example. Keep each surface internally consistent; don't bleed one
  audience's verb into the other. (Internal route/function names like
  `pause_walks` are not user-facing and need not change.)

---

## 4. Where the reference patterns live

- Stacked success modal — `app/templates/partials/success_modal.html` +
  `app/static/js/success-modal.js` (`showConfirmed`). Consumers: `admin_dogs.html`
  (book/cancel), `admin_clients.html` + `admin_walkers.html` (deactivate/activate).
- Always-visible-but-disabled action button — bulk-cancel modal, `admin_dogs.html`
- Spinner-safe button reset (`resetBcConfirmBtn`) — same file
- Fetch-only toggle + own success UX — `toggleStatusRequest()` in
  `app/static/js/admin-toggle-status.js`
