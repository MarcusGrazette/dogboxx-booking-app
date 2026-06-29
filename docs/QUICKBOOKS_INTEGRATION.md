# QuickBooks Integration — Scoping Notes

**Status:** Scoping only — not scheduled for implementation.
**Target:** QuickBooks **Online** (QBO). *(Not Desktop — Desktop uses IIF files, not CSV; if that ever changes this doc needs revisiting.)*
**Last updated:** 2026-06-28

DogBoxx invoices ~50 clients monthly in QuickBooks Online. This doc scopes how the
booking app could feed that process — either by exporting a file Lydia uploads, or by
pushing invoices over the QBO API.

The key starting point: **the app already computes the invoice.**
`app/utils/invoicing.py::invoice_for_client(user_id, month_start, month_end, all_configs)`
returns a per-client monthly dict (confirmed walks, drop-ins, billable late-cancels,
double-slot + weekly discounts, subtotal). The line-item math lives in
`app/utils/pricing.py` (`build_line_items`, `build_double_slot_discounts`,
`weekly_discount_for_walks`). So getting data *into* QuickBooks is a "format and ship"
problem, not a "recompute" one — and both options below consume the *same*
`invoice_for_client` dict, so neither phase is throwaway.

---

## The two paths

| | CSV import | QBO API |
|---|---|---|
| **Mechanism** | App emits CSV → Lydia uploads via QBO *Import Data → Invoices* | App pushes `Invoice` objects over REST |
| **Auth** | None | OAuth 2.0 app registration + token refresh (permanent) |
| **New deps** | None (reuses existing `csv` + `Response` code) | `intuit-oauth`, `python-quickbooks` |
| **New schema** | None | `Client.qbo_customer_id`, OAuth token storage |
| **Monthly effort (Lydia)** | One download + one upload | One button click |
| **Build effort** | ~half a day | ~2–4 days + ongoing maintenance |
| **Prod failure modes** | None (read-only, offline export) | Token expiry, Intuit downtime, rate limits, duplicate customers |
| **Direction** | One-way (push only) | Two-way (can read payment status back) |

---

## Option 1 — CSV export (recommended starting point)

Add an admin route, e.g. `GET /admin/invoicing/export?month=YYYY-MM`, that walks the same
client loop `app/blueprints/admin/views/invoicing.py::invoicing()` already runs and streams
a QBO-import-shaped CSV. Reuses the `Response(..., mimetype='text/csv')` +
`csv.DictWriter` pattern already in `app/blueprints/admin/views/csv_import.py:250-260` — no
new dependencies.

QBO import: **Gear → Tools → Import Data → Invoices**, CSV/Excel. Columns roughly:

```
InvoiceNo, Customer, InvoiceDate, DueDate, Item(Product/Service),
ItemDescription, ItemQuantity, ItemRate, ItemAmount
```

A multi-line invoice = repeated rows sharing the same `InvoiceNo` + `Customer`.

### Gotchas (QBO's importer is stricter than it looks)

1. **Customers match by display name.** A mismatch (e.g. "John Doe" vs "Doe, John")
   silently *creates a new customer* → duplicate books. The export's customer string must
   equal the QBO display name exactly. One-time name-alignment pass with Lydia's existing
   QBO customers fixes this for all 50.
2. **Products/Services must pre-exist** in QBO ("Group Walk", "Drop In") before import.
   One-time setup.
3. **Date format must match the QBO company locale** (DD/MM/YYYY vs MM/DD/YYYY) — common
   silent fail.
4. **Discounts → explicit negative-amount lines.** QBO invoice lines have no native
   "weekly ≥5 discount" concept, so represent each as its own negative line
   ("Weekly discount ×N walks", "Double-slot discount"). This is clearer on the client's
   invoice than a hidden adjusted rate, and `invoice_for_client` already returns these as
   separate totals (`weekly_discount_total`, `doubles`).

### Why it's the safe default

A read-only GET that streams a file adds **zero** prod runtime risk — no OAuth secrets to
leak, no Intuit dependency in the request path. Matters for a live money app. Lydia
downloads once a month and uploads.

---

## Option 2 — QBO API (only if/when needed)

REST + JSON, **OAuth 2.0**. Register an app at the
[Intuit Developer portal](https://developer.intuit.com) for a client ID/secret; a free
sandbox company is available for testing. Python SDKs: `intuit-oauth` (OAuth dance) +
`python-quickbooks` (object wrappers) — both fit the Flask stack.

Flow: one-time OAuth connect (Lydia authorizes DogBoxx → her QBO company) → store refresh
token → a "Send to QuickBooks" button on `/admin/invoicing` POSTs each client's computed
invoice as a QBO `Invoice` object.

### The real cost is identity mapping + token lifecycle (not invoice creation)

- Store each client's QBO `Customer` Id (new nullable `Client.qbo_customer_id`, set on
  first sync) so you **update** rather than **duplicate**.
- Persist + refresh the OAuth token. **Refresh tokens rotate (~100 days);** let one lapse
  and sync breaks *silently* until the books are noticed stale. This is the classic trap.
- Rate limits (500 req/min, 40/sec) — fine for 50 invoices, mind it if fanning out
  alongside other work.

### When the API is actually worth it

Only if you want one of:
- **Zero manual steps** (button vs download-then-upload), or
- **Payment status flowing back** — the app knowing an invoice was paid (CSV can't; it's
  push-only).

For 50 clients invoiced monthly, neither usually justifies owning an OAuth token-refresh
lifecycle on a money path. Revisit if the monthly CSV upload becomes a genuine pain.

---

## Recommendation

**Phase 1 (when prioritised):** CSV export. Self-contained, no new deps, no prod risk,
~90% of the value.

**Phase 2 (only if needed):** API push, reusing the *same* `invoice_for_client` dict — the
CSV row-builder and the API line-item-builder consume identical data, so Phase 1 is not
throwaway. You swap the output sink, not the logic.

---

## Open decisions to settle with Lydia before building

1. **Invoice numbering** — app-assigned (app owns the sequence, enables later app↔QBO
   reconciliation) vs QBO auto-numbers on import. Shapes the CSV's `InvoiceNo` column.
2. **Customer name alignment** — confirm the 50 app clients map 1:1 to existing QBO
   customer display names (drives gotcha #1).
3. **Grab QBO's current invoice-import column template** (downloadable from the QBO import
   screen) so the CSV headers match exactly — QBO's template is the source of truth, not
   the column list sketched above.
