# Disaster Recovery — Full Environment Rebuild

Scope: what to do if you lose the Railway project/services themselves (not just
the database — full recreation from nothing). For a DB-only restore drill
(the piece that's actually been rehearsed end-to-end), see
`docs/RESTORE_DRILL.md` — this doc builds on that plus everything else needed
to bring the whole app back up.

**Status: not yet rehearsed as a full drill.** The Postgres-restore portion is
proven (see `docs/RESTORE_DRILL.md`'s history section). The rest of this — web
service recreation, cron services, DNS — is written from reading the current
config, not from having actually done it. Treat it as a starting checklist,
not a guarantee; if a real disaster ever forces using this, expect to adapt.

## What survives a Railway-side disaster

These are independent of Railway and should still exist even if the whole
Railway project is gone:

- **GitHub repo** (`git@github.com:MarcusGrazette/dogboxx-booking-app.git`) —
  source of truth for all code and config-as-code files.
- **R2 bucket `dogboxx-db-backups`** — nightly Postgres dumps.
- **R2 bucket `dogboxx-uploads-backup`** — live mirror of every dog/profile
  photo (written at upload time, not just on a schedule — see
  `app/utils/uploads.py::_backup_to_r2()`).
- **1Password entry with the production `.env`** — the only backup of actual
  secret values (`SECRET_KEY`, `RESEND_API_KEY`, R2 credentials, VAPID keys,
  `INTERNAL_API_SECRET`, etc.). Without this, `config.py`/`.env.example` show
  which vars exist but not their real values — there is no other copy.

## Rebuild order

1. **New Railway project**, or new environment in the existing one, depending
   on what's actually lost.

2. **Postgres plugin.** Before pointing anything else at it, restore the
   latest dump from `dogboxx-db-backups` directly into it — same mechanics as
   `docs/RESTORE_DRILL.md` steps 2–5 (grab `DATABASE_PUBLIC_URL`, download
   latest `<date>.sql.gz` via the boto3 snippet there, strip `\restrict`/
   `\unrestrict`, `psql -v ON_ERROR_STOP=1 -f`). Doing this **before** `web`
   ever boots against this database means `web`'s automatic `flask db
   upgrade` (in `scripts/start.sh`) finds it already at head and is a no-op —
   exactly what the drill verified. Restoring after `web` has already run
   migrations against an empty DB would still work (the dump's `--clean
   --if-exists` drops and recreates everything) but is a needless extra step.

3. **Redis plugin.** No data to restore — it only ever held rate-limit
   counters and the SSE cross-worker pub/sub, both fine to lose. Degrades
   gracefully if briefly unset (`RATELIMIT_STORAGE_URI` falls back to
   `memory://`, `SSE_REDIS_URL` falls back to `None` → per-process SSE only),
   but set `REDIS_URL` before `web`'s first deploy so it isn't running
   degraded any longer than necessary.

4. **`web` service** — "New Service from GitHub Repo", this repo, `main`
   branch. The root `railway.toml` already has the right `builder`/
   `startCommand`/`healthcheckPath`, so no config-as-code changes needed here
   (unlike the cron services below). Attach a new volume mounted at
   `/data/uploads` (see `scripts/start.sh`'s comment — that exact mount path
   is what triggers the symlink-and-restore logic). Set every var from the
   1Password `.env` backup (`DATABASE_URL`/`REDIS_URL` can instead be Railway
   variable references to the new plugins, e.g. `${{Postgres.DATABASE_URL}}`,
   rather than copied literal values).

5. **First `web` deploy.** `scripts/start.sh` runs automatically and, in
   order: seeds bundled default photos onto the new volume, detects the
   volume looks empty and restores every real photo from
   `dogboxx-uploads-backup` (already-built DR logic — nothing to do here, see
   the memory note below on why this isn't a separate script), swaps in the
   `/static/uploads` symlink, runs `flask db upgrade` (no-op per step 2), runs
   `flask seed-service-types` (idempotent), then starts gunicorn.

6. **Cron services** — recreate `reconcile-uploads` and `session-cleanup` as
   separate services from the same repo/branch. For **each one**, set
   Settings → "Railway Config File" to `railway.reconcile-uploads.toml` /
   `railway.session-cleanup.toml` respectively — **do this before the first
   deploy**, since a same-repo service with no custom config path silently
   inherits the root `railway.toml` (wrong start command, plus an
   unsatisfiable `/health` check that will mark every deployment "Failed").
   Vars: `reconcile-uploads` needs `INTERNAL_API_SECRET` (same value as
   `web`), `WEB_INTERNAL_URL` (`http://web.railway.internal:8080` unless
   `web`'s bound port differs — check its deploy logs), and the `R2_*` vars;
   `session-cleanup` needs `DATABASE_URL`/`SECRET_KEY`/`FLASK_ENV` (can be
   `${{web.*}}` references once `web` exists). Neither actually executes
   until its cron schedule fires — a "SUCCESS" status right after
   creation/redeploy only means "image built," not "task ran."

7. **DNS.** Re-point `app.dogboxx.org`'s CNAME at the new Railway service's
   generated domain. **DNS provider/registrar isn't recorded anywhere in this
   repo or in memory** — find out where DNS is actually managed before this
   step is needed for real, so this doc can be updated with the concrete
   procedure rather than a placeholder.

## Verification checklist

- `GET /health` returns 200 (it checks a real table and the Alembic head, not
  just process liveness).
- `railway ssh --service web -- /opt/venv/bin/flask db upgrade` reports no
  pending migrations.
- Log in as a real user (e.g. the owner account) and confirm booking history
  looks right — proves the DB restore actually landed the right data, not
  just an empty schema.
- Open a dog's profile photo in the browser — proves `start.sh`'s uploads
  restore actually pulled real files from R2, not just created empty
  directories.
- `railway logs --service web | grep "SSE: Redis listener"` — confirms Redis
  is wired up for cross-worker SSE, not silently degraded.
- Once `reconcile-uploads` is deployed, manually trigger it once via
  `railway ssh --service web -- ... flask reconcile-uploads` style
  invocation (see CLAUDE.md's Railway section for the exact `railway ssh`
  gotchas) and confirm zero missing/mismatched files.
- If `SENTRY_DSN` is set, trigger a deliberate test exception and confirm it
  shows up in Sentry tagged with the right environment.

## Why there's no separate "restore uploads" script

`scripts/start.sh` already restores uploads from R2 automatically whenever the
volume looks empty (checked on every deploy, not just after a disaster) — see
its inline comment block. A parallel standalone script was built and then
reverted in the same session this doc was written, once the overlap was
noticed — see the project memory for that incident. If the inline version
ever needs improving (it currently only gates on the `dogs/` folder's count,
not `profiles/`, and only checks file *presence* rather than catching a
corrupted/truncated file), the fix belongs in `start.sh` itself, not a second
implementation.
