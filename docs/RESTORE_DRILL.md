# Postgres Restore Drill

Runbook for periodically proving the nightly R2 backup (`.github/workflows/backup.yml`)
is actually restorable — not just "the job stayed green" (see FEATURES.md #63a; this
exact complacency is what let backups silently dump empty 20-byte files for ~3 months
until 2026-07-06).

**Never restore into the live `web` Postgres.** Always into the scratch project below.

## Scratch environment

- Railway project: `dogboxx-restore-drill` (separate project, same workspace as prod —
  not a service inside `disciplined-creation`).
- The project + its `production` environment are kept around permanently so re-running
  the drill doesn't mean re-creating the project each time. The Postgres service itself
  (and its volume) is deleted after each drill — recreate it fresh each time so the
  restore always starts from a genuinely empty DB.

## Steps

1. **Add a Postgres service** to `dogboxx-restore-drill` via the Railway dashboard
   (Railway's own agent/API can't provision a bare database plugin — this step is
   dashboard-only). Once it's up, grab `DATABASE_PUBLIC_URL` from its Variables tab.

2. **Find R2 credentials.** `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_ENDPOINT_URL`
   live in two places: GitHub Actions repo secrets (write-only, can't be read back), and
   the `reconcile-uploads` Railway service's variables (readable). The DB backup bucket
   is `dogboxx-db-backups` — a different bucket from `R2_BUCKET_UPLOADS`
   (`dogboxx-uploads-backup`), same R2 account/credentials.

3. **List + download the latest dump.** No `aws` CLI is installed locally as of this
   writing — use `boto3` (already available) instead:

   ```python
   import boto3, os
   s3 = boto3.client('s3', endpoint_url=R2_ENDPOINT_URL,
                      aws_access_key_id=R2_ACCESS_KEY_ID,
                      aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                      region_name='auto')
   # sanity-check the bucket before downloading — confirms no repeat of the
   # empty-dump incident (real dumps are several hundred KB+, never ~20 bytes)
   resp = s3.list_objects_v2(Bucket='dogboxx-db-backups')
   # sorted by Key gives chronological order (YYYY-MM-DD.sql.gz filenames)
   s3.download_file('dogboxx-db-backups', '<latest>.sql.gz', '<local path>')
   ```

4. **Unzip and strip the pg17+ dump-guard lines.** `pg_dump` on Postgres 17+ wraps the
   dump in `\restrict <token>` / `\unrestrict <token>` — a client-side psql meta-command
   guard against executing untrusted dump content, unrelated to schema/data. If the
   local `psql` client predates this (check `psql --version` — Ubuntu 24.04 ships 16.x),
   it won't recognize the command. Safe to delete both lines since this is our own
   trusted dump:

   ```bash
   gunzip -k <dump>.sql.gz
   grep -v -E '^\\(restrict|unrestrict)' <dump>.sql > <dump>.clean.sql
   ```

5. **Restore**, failing fast on any real error:

   ```bash
   psql "$SCRATCH_DATABASE_PUBLIC_URL" -v ON_ERROR_STOP=1 -f <dump>.clean.sql > restore.log 2>&1
   grep -i error restore.log   # should be empty
   ```

6. **Verify.** All of the following should pass before calling the drill successful:
   - `psql "$SCRATCH_URL" -c "\dt"` — table count matches prod's model set.
   - Row counts on a few key tables (`users`, `clients`, `dogs`, `bookings`,
     `booking_status_changes`, `walkers`) look plausible, not zero/truncated.
   - `psql "$SCRATCH_URL" -t -c "select version_num from alembic_version;"` matches the
     single current head in `migrations/versions/` (no down_revision pointing to it).
   - `DATABASE_URL="$SCRATCH_DATABASE_PUBLIC_URL" FLASK_ENV=production flask db upgrade`
     exits 0 with **no** "Running upgrade..." lines — proves the restored schema is
     already at head, not stale.
   - Spot-check a couple of real rows (e.g. known user emails, recent bookings) —
     confirms it's not just structurally correct but actually the right data.

7. **Tear down.** Delete the Postgres service + volume in the dashboard (API/agent has
   no delete capability for services or projects — dashboard only). Leave the
   `dogboxx-restore-drill` project + environment in place for next time.

## Gotchas specific to this process

- **Don't relink the local `railway` CLI to the scratch project to fetch its connection
  string.** It stays linked to prod (`disciplined-creation`/`web`) for local dev; use
  `railway link -p <id> -s <service> -e <environment>` non-interactively only if you
  must, and relink back + verify with `railway status` immediately after. Simpler: copy
  `DATABASE_PUBLIC_URL` from the dashboard by hand.
- The advisory-lock / SSE / rate-limiter stuff in the app doesn't matter here — this
  drill only exercises `psql`/`pg_dump` output against a bare Postgres, never the Flask
  app itself (except the one-shot `flask db upgrade` check in step 6, which only reads
  the Alembic version table).

## History

- 2026-07-11: first drill run, using the `2026-07-10.sql.gz` dump (886 KB, from the
  first batch of dumps after the empty-dump bug was fixed). All checks passed. See
  FEATURES.md #63a.
