#!/usr/bin/env python3
"""
Standalone admin-creation script for use inside the Railway container shell.

Usage:
    python3 create_admin_interactive.py

Run this via `railway shell --service web` when the Flask CLI is not available
in PATH (e.g. outside the virtual environment activated by Gunicorn).
"""

import os
import sys
import getpass


def main():
    # ── Bootstrap the Flask app ──────────────────────────────────────────────
    # Honour FLASK_ENV if set; fall back to 'production' so the script uses
    # the real DATABASE_URL rather than the SQLite dev default.
    flask_env = os.environ.get("FLASK_ENV", "production")

    try:
        from app import create_app, db
    except ImportError as exc:
        print(f"ERROR: Could not import the Flask app — {exc}")
        print("Make sure you are running this script from the project root directory.")
        sys.exit(1)

    app = create_app(flask_env)

    # ── Collect user input ───────────────────────────────────────────────────
    print("\n=== Create Admin User ===\n")

    try:
        email = input("Email: ").strip().lower()
        if not email:
            print("ERROR: Email cannot be empty.")
            sys.exit(1)

        firstname = input("First name: ").strip().title()
        if not firstname:
            print("ERROR: First name cannot be empty.")
            sys.exit(1)

        lastname = input("Last name: ").strip().title()
        if not lastname:
            print("ERROR: Last name cannot be empty.")
            sys.exit(1)

        password = getpass.getpass("Password: ")
        if not password:
            print("ERROR: Password cannot be empty.")
            sys.exit(1)

        password_confirm = getpass.getpass("Confirm password: ")
        if password != password_confirm:
            print("ERROR: Passwords do not match.")
            sys.exit(1)

    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(1)

    # ── Write to the database ────────────────────────────────────────────────
    with app.app_context():
        from app.models import User
        from werkzeug.security import generate_password_hash

        existing = User.query.filter_by(email=email).first()
        if existing:
            print(f"\nUser '{email}' already exists. No changes made.")
            sys.exit(0)

        user = User(
            firstname=firstname,
            lastname=lastname,
            email=email,
            role="walker",
            is_admin=True,
            hashed_password=generate_password_hash(password),
            must_change_password=False,
            active=True,
        )
        db.session.add(user)
        db.session.commit()

    print(f"\nAdmin user '{email}' created successfully.")


if __name__ == "__main__":
    main()
