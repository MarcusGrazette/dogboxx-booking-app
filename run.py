import os

import click

# Make psycopg2 cooperative under gevent. Gunicorn's gevent worker monkey-patches
# the stdlib before importing this module, so a patched `socket` reliably means
# "we're in a gevent worker". Without this, psycopg2 (a C extension, immune to
# monkey-patching) blocks the worker's entire event loop on every DB call —
# serialising all greenlets, including held-open SSE streams, behind each query.
# flask run / pytest / flask db upgrade import with an unpatched socket and skip.
try:
    from gevent import monkey
except ImportError:  # local envs without gevent installed — nothing to patch
    monkey = None

if monkey is not None and monkey.is_module_patched('socket'):
    from psycogreen.gevent import patch_psycopg
    patch_psycopg()

from app import create_app

# Create app with environment-specific configuration
app = create_app(os.environ.get('FLASK_ENV', 'development'))


@app.cli.command("seed-service-types")
def seed_service_types_cmd():
    """Seed essential service types (group-walk, drop-in). Safe to run multiple times."""
    from app.seed_db.seeder import seed_service_types
    from app import db
    with app.app_context():
        seed_service_types()
        db.session.commit()
    click.echo("Service types seeded.")


@app.cli.command("create-admin")
@click.option("--email", prompt=True, help="Admin email address")
@click.option("--firstname", prompt=True, help="First name")
@click.option("--lastname", prompt=True, help="Last name")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Password")
def create_admin(email, firstname, lastname, password):
    """Create an admin user with a walker record. Safe to run on a live DB."""
    from app.models import User, Walker
    from app import db
    from werkzeug.security import generate_password_hash
    with app.app_context():
        existing = User.query.filter_by(email=email.lower()).first()
        if existing:
            click.echo(f"User {email} already exists.")
            return
        user = User(
            firstname=firstname.strip().title(),
            lastname=lastname.strip().title(),
            email=email.strip().lower(),
            role='walker',
            is_admin=True,
            hashed_password=generate_password_hash(password),
            must_change_password=False,
            active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(Walker(user_id=user.id))
        db.session.commit()
        click.echo(f"Admin user {email} created successfully (with walker record).")


@app.cli.command("make-walker")
@click.option("--email", prompt=True, help="Email of existing user to give a walker record")
def make_walker(email):
    """Add a Walker record to an existing user. Safe to run if record already exists."""
    from app.models import User, Walker
    from app import db
    with app.app_context():
        user = User.query.filter_by(email=email.lower()).first()
        if not user:
            click.echo(f"No user found with email {email}.")
            return
        if Walker.query.filter_by(user_id=user.id).first():
            click.echo(f"{email} already has a walker record.")
            return
        db.session.add(Walker(user_id=user.id))
        db.session.commit()
        click.echo(f"Walker record created for {email}.")


@app.cli.command("reconcile-uploads")
def reconcile_uploads_cmd():
    """Compare the uploads volume against the R2 backup bucket and report any
    gaps. Read-only — never writes or deletes anything on either side. Run
    from a dedicated Railway cron service (see FEATURES.md #63b); needs
    WEB_INTERNAL_URL, INTERNAL_API_SECRET, and the R2_* env vars set."""
    from app.utils.uploads_reconcile import (
        diff_manifests, fetch_r2_manifest, fetch_volume_manifest,
    )
    from app.utils.email import send_email

    with app.app_context():
        internal_url = os.environ.get("WEB_INTERNAL_URL", "http://web.railway.internal:8080")
        secret = os.environ.get("INTERNAL_API_SECRET", "")
        bucket = os.environ.get("R2_BUCKET_UPLOADS", "dogboxx-uploads-backup")

        volume_files = fetch_volume_manifest(internal_url, secret)
        r2_files = fetch_r2_manifest(bucket)
        missing_from_r2, orphaned_in_r2, size_mismatches = diff_manifests(volume_files, r2_files)

        click.echo(f"Volume files: {len(volume_files)}, R2 files: {len(r2_files)}")
        click.echo(f"Missing from R2: {len(missing_from_r2)}")
        click.echo(f"Orphaned in R2 (no volume file — expected, not a problem): {len(orphaned_in_r2)}")
        click.echo(f"Size mismatches: {len(size_mismatches)}")

        if missing_from_r2 or size_mismatches:
            for key in missing_from_r2:
                click.echo(f"  MISSING FROM R2: {key}")
            for key in size_mismatches:
                click.echo(f"  SIZE MISMATCH: {key} (volume={volume_files[key]}, r2={r2_files[key]})")

            bug_reports = os.environ.get("BUG_REPORTS")
            if bug_reports:
                items = "".join(f"<li>Missing from R2: {key}</li>" for key in missing_from_r2)
                items += "".join(
                    f"<li>Size mismatch: {key} (volume={volume_files[key]}, r2={r2_files[key]})</li>"
                    for key in size_mismatches
                )
                send_email(
                    to=bug_reports,
                    subject="DogBoxx: uploads reconciliation found a problem",
                    html=f"<p>Upload reconciliation found the following:</p><ul>{items}</ul>",
                )
        else:
            click.echo("All volume files present in R2 with matching sizes.")


@app.cli.command("send-walker-summaries")
@click.option("--kind", type=click.Choice(["preview", "morning"]), default=None,
              help="Send this summary now instead of the one due by the London clock.")
@click.option("--date", "date_str", default=None,
              help="YYYY-MM-DD the summary describes (with --kind; default: the due date).")
def send_walker_summaries_cmd(kind, date_str):
    """Send the daily walker summaries (app/utils/walker_summaries.py). With no
    options, sends whichever is due now — 'morning' at 08:00 London, 'preview'
    at 15:00 — and does nothing at any other hour; the walker-summaries cron
    service runs it at both UTC hours of each so BST and GMT are covered.
    Safe to re-run: walkers already sent that summary are skipped."""
    from datetime import date
    import gevent
    from app.utils.walker_summaries import (
        due_summary, next_working_day, send_walker_summaries,
    )
    from app.utils.dates import local_today

    with app.app_context():
        if kind:
            if date_str:
                target = date.fromisoformat(date_str)
            else:
                today = local_today()
                target = today if kind == "morning" else next_working_day(today)
        else:
            due = due_summary()
            if due is None:
                click.echo("No walker summary due at this hour (London).")
                return
            kind, target = due

        sent = send_walker_summaries(kind, target)
        click.echo(f"Sent {sent} walker {kind} summar{'y' if sent == 1 else 'ies'} for {target}.")

    # Web Push goes out from greenlets spawned in the after_commit hook
    # (app/__init__.py). Gunicorn's gevent worker runs those; a CLI process
    # only does when it yields to the hub, so wait for them before exiting.
    gevent.wait(timeout=120)


@app.cli.command("sweep-push-subscriptions")
def sweep_push_subscriptions_cmd():
    """Delete push subscriptions not seen in 90+ days (M31). A still-valid
    endpoint whose device is long gone keeps returning 201 from the push
    service forever, so send_web_push()'s 404/410 pruning never catches it —
    last_seen_at (set on every push-subscribe upsert) is the only real
    liveness signal. Run from the session-cleanup Railway cron service
    alongside `flask session_cleanup` — same shape of housekeeping, no
    dedicated service needed."""
    from datetime import datetime, timezone, timedelta
    from app import db
    from app.models import PushSubscription

    with app.app_context():
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        deleted = (PushSubscription.query
                   .filter(PushSubscription.last_seen_at < cutoff)
                   .delete(synchronize_session=False))
        db.session.commit()
        click.echo(f"Swept {deleted} push subscription(s) not seen since {cutoff.date()}.")


if __name__ == "__main__":
    # Get port from environment or default to 5000
    port = int(os.environ.get('PORT', 5000))
    
    # In development, debug=True. In production, respect the app's config
    debug = os.environ.get('FLASK_ENV', 'development') != 'production'
    app.run(debug=debug, host='127.0.0.1', port=port)