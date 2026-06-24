from flask import request, render_template, redirect, flash, url_for, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timezone
import logging

from app.blueprints.admin import admin_bp
from app.utils.decorators import admin_required
from app.models import User, Dog, Client, DogOwner
from app import db
from werkzeug.security import generate_password_hash
import secrets


# ── CSV Client Import ─────────────────────────────────────────────────────────

CSV_IMPORT_COLUMNS = [
    'firstname', 'lastname', 'email', 'phone',
    'address_line_1', 'address_line_2', 'address_line_3', 'postcode',
    'pickup_instructions',
    'dog_name', 'dog_gender', 'dog_breed', 'dog_dob',
]

def _parse_csv_row(row, row_num):
    """Validate a single CSV row. Returns (cleaned_dict, list_of_errors)."""
    import re
    errors = []

    firstname = row.get('firstname', '').strip().title()
    lastname  = row.get('lastname',  '').strip().title()
    email     = row.get('email',     '').strip().lower()
    phone     = row.get('phone',     '').strip() or None

    if not firstname:
        errors.append('First name is required')
    if not lastname:
        errors.append('Last name is required')
    if not email:
        errors.append('Email is required')
    elif not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        errors.append(f'Invalid email: {email}')

    dog_name   = row.get('dog_name',   '').strip()
    dog_gender = row.get('dog_gender', '').strip().upper()
    dog_breed  = row.get('dog_breed',  '').strip()
    dog_dob    = row.get('dog_dob',    '').strip()

    parsed_dob = None
    if dog_dob:
        from datetime import date as date_type
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                parsed_dob = datetime.strptime(dog_dob, fmt).date()
                break
            except ValueError:
                pass
        if parsed_dob is None:
            errors.append(f'Invalid dog_dob format (use YYYY-MM-DD): {dog_dob}')

    if dog_gender and dog_gender not in ('M', 'F'):
        errors.append(f'dog_gender must be M or F (got: {dog_gender})')

    has_dog = bool(dog_name)

    cleaned = {
        'row_num':             row_num,
        'firstname':           firstname,
        'lastname':            lastname,
        'email':               email,
        'phone':               phone,
        'address_line_1':      row.get('address_line_1', '').strip() or None,
        'address_line_2':      row.get('address_line_2', '').strip() or None,
        'address_line_3':      row.get('address_line_3', '').strip() or None,
        'postcode':            row.get('postcode',        '').strip() or None,
        'pickup_instructions': row.get('pickup_instructions', '').strip() or None,
        'has_dog':             has_dog,
        'dog_name':            dog_name or None,
        'dog_gender':          dog_gender or None,
        'dog_breed':           dog_breed or None,
        'dog_dob':             parsed_dob.isoformat() if parsed_dob else None,
        'errors':              errors,
    }
    return cleaned, errors


@admin_bp.route("/clients/import", methods=["GET"])
@login_required
@admin_required
def csv_import():
    """CSV client import — upload form."""
    return render_template("admin_csv_import.html")


@admin_bp.route("/clients/import/preview", methods=["POST"])
@login_required
@admin_required
def csv_import_preview():
    """Parse uploaded CSV and return a preview for confirmation."""
    import csv, io

    f = request.files.get('csv_file')
    if not f or not f.filename.lower().endswith('.csv'):
        flash("Please upload a .csv file.", "error")
        return redirect(url_for('admin.csv_import'))

    content = f.read()
    if len(content) > 500 * 1024:
        flash("File is too large — maximum size is 500 KB.", "error")
        return redirect(url_for('admin.csv_import'))

    try:
        text = content.decode('utf-8-sig')  # handle BOM from Excel
    except UnicodeDecodeError:
        flash("Could not read file — please save as UTF-8 CSV.", "error")
        return redirect(url_for('admin.csv_import'))

    reader = csv.DictReader(io.StringIO(text))

    # Normalise header names (strip whitespace, lowercase)
    if not reader.fieldnames:
        flash("CSV file appears to be empty.", "error")
        return redirect(url_for('admin.csv_import'))

    headers = [h.strip().lower() for h in reader.fieldnames]
    required = {'firstname', 'lastname', 'email'}
    missing = required - set(headers)
    if missing:
        flash(f"CSV is missing required columns: {', '.join(sorted(missing))}", "error")
        return redirect(url_for('admin.csv_import'))

    rows = []
    for i, raw_row in enumerate(reader, start=2):
        # Re-key with normalised headers
        normalised = {k.strip().lower(): v for k, v in raw_row.items()}
        cleaned, _ = _parse_csv_row(normalised, i)

        # Check if email already exists in DB
        if cleaned['email'] and User.query.filter_by(email=cleaned['email']).first():
            cleaned['errors'].append('Email already exists — will be skipped')
            cleaned['duplicate'] = True
        else:
            cleaned['duplicate'] = False

        rows.append(cleaned)

    if not rows:
        flash("No data rows found in CSV.", "error")
        return redirect(url_for('admin.csv_import'))

    valid_count   = sum(1 for r in rows if not r['errors'])
    invalid_count = sum(1 for r in rows if r['errors'])

    # Store validated rows server-side in the session; don't pass them as a
    # hidden form field (which can be tampered with client-side).
    from flask import session as flask_session
    flask_session['csv_import_rows'] = [r for r in rows if not r['errors']]

    return render_template(
        "admin_csv_preview.html",
        rows=rows,
        valid_count=valid_count,
        invalid_count=invalid_count,
    )


@admin_bp.route("/clients/import/confirm", methods=["POST"])
@login_required
@admin_required
def csv_import_confirm():
    """Execute the import using the validated rows stored in the session."""
    from flask import session as flask_session

    rows = flask_session.pop('csv_import_rows', None)
    if rows is None:
        flash("Import session expired or not found. Please re-upload the CSV.", "error")
        return redirect(url_for('admin.csv_import'))

    created = 0
    skipped = 0

    for r in rows:
        # Double-check email hasn't been created since preview
        if User.query.filter_by(email=r['email']).first():
            skipped += 1
            continue
        try:
            temp_password = secrets.token_urlsafe(12)
            user = User(
                firstname=r['firstname'],
                lastname=r['lastname'],
                email=r['email'],
                role='client',
                hashed_password=generate_password_hash(temp_password),
                must_change_password=True,
                phone=r.get('phone'),
            )
            db.session.add(user)
            db.session.flush()

            client = Client(user_id=user.id)
            parts = [p for p in [r.get('address_line_1'), r.get('address_line_2'), r.get('address_line_3')] if p]
            if parts:
                client.street_address = '\n'.join(parts)
            client.postal_code = r.get('postcode')
            has_address = bool(r.get('address_line_1'))
            db.session.add(client)
            db.session.flush()

            has_dog = r.get('has_dog') and r.get('dog_name')
            if has_dog:
                from datetime import date as date_type
                dob = date_type.fromisoformat(r['dog_dob']) if r.get('dog_dob') else None
                _gender_map = {'M': 'male', 'F': 'female'}
                dog = Dog(
                    name=r['dog_name'],
                    gender=_gender_map.get(r.get('dog_gender'), 'male'),
                    breed=r.get('dog_breed') or '',
                    allergies='',
                    date_of_birth=dob,
                    pickup_instructions=r.get('pickup_instructions'),
                )
                db.session.add(dog)
                db.session.flush()
                db.session.add(DogOwner(dog_id=dog.id, user_id=user.id, role='primary'))

            if has_address and has_dog:
                client.onboarding_completed = True
                client.onboarding_completed_at = datetime.now(timezone.utc)

            db.session.commit()
            created += 1
        except Exception as e:
            db.session.rollback()
            logging.error(f"CSV import error for {r.get('email')}: {e}")
            skipped += 1

    if created:
        flash(f"Import complete — {created} client{'s' if created != 1 else ''} created"
              + (f", {skipped} skipped" if skipped else "") + ".", "success")
    else:
        flash("No clients were imported.", "warning")

    return redirect(url_for('admin.clients'))


@admin_bp.route("/clients/import/sample")
@login_required
@admin_required
def csv_import_sample():
    """Download a sample CSV template."""
    from flask import Response
    sample = (
        "firstname,lastname,email,phone,address_line_1,address_line_2,address_line_3,"
        "postcode,pickup_instructions,dog_name,dog_gender,dog_breed,dog_dob\n"
        "Jane,Smith,jane.smith@example.com,07700900001,12 Elm Street,Flat 2,,SE1 3QJ,"
        "\"Door code 1234, ring top bell\",Biscuit,F,Labrador,2021-03-15\n"
        "Tom,Jones,tom.jones@example.com,07700900002,,,,,,,,,\n"
    )
    return Response(
        sample,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=dogboxx_import_template.csv'}
    )
