"""Regression test for app/models.py DailyMessage (audit M18): updated_at
was missing onupdate=, a copy-paste gap versus every other updated_at column
in the model (Client, Booking, PushSubscription) — it silently recorded
creation time forever instead of tracking edits."""
import time
from datetime import date

from app import db
from app.models import DailyMessage


def test_updated_at_changes_on_edit(app):
    with app.app_context():
        msg = DailyMessage(date=date(2026, 3, 1), content='Original message')
        db.session.add(msg)
        db.session.commit()
        created_at = msg.created_at
        updated_at_initial = msg.updated_at

        time.sleep(0.05)  # ensure datetime.now() advances between the two commits
        msg.content = 'Edited message'
        db.session.commit()

        assert msg.updated_at > updated_at_initial
        assert msg.created_at == created_at  # created_at must not move
