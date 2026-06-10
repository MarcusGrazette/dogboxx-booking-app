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

Reference implementation: `#bookingConfirmedModal` + `showConfirmed(summary,
srcModal, srcEl)` in `app/templates/admin_dogs.html`. The helper is modal-agnostic
— pass the source modal instance and element so it stacks correctly without
backdrop/scroll-lock conflicts (it waits for the source modal's `hidden.bs.modal`
before showing the confirm modal).

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
| Pausing a dog's walks | **Pause** | The "pause walks" feature uses *pause* everywhere in the UI (title, button "Pause N walks", summaries) even though it cancels the bookings in the DB. |
| Brand name | **DogBoxx** | Capital D, capital B. Never "Dogboxx" / "DogBox". |

**Rule:** a feature's verb must be consistent across its title, buttons, preview
text, and success summary. Mixed wording (a "Pause" title with a "Cancel" button)
is the exact drift this guide exists to prevent.

---

## 4. Where the reference patterns live

- Stacked confirm modal + `showConfirmed()` — `app/templates/admin_dogs.html`
- Always-visible-but-disabled action button — pause-walks modal, same file
- Spinner-safe button reset (`resetBcConfirmBtn`) — same file
