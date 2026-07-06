"""
API routes.

JSON API endpoints for AJAX calls from the frontend.
"""

import os
from flask import current_app, request, jsonify
from flask_login import login_required
from datetime import datetime

from app.blueprints.api import api_bp
from app.capacity import get_slot_availability_summary
from app.utils.decorators import internal_only


@api_bp.route("/slot_availability")
@login_required
def slot_availability():
    """Return availability info for both slots on a given date.
    
    Query params:
        date: YYYY-MM-DD
    
    Returns JSON:
        {
            "Morning": {"total": 12, "booked": 8, "available": 4},
            "Afternoon": {"total": 6, "booked": 2, "available": 4}
        }
    """
    date_str = request.args.get('date')
    if not date_str:
        return jsonify(error="Missing date parameter"), 400

    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify(error="Invalid date format, expected YYYY-MM-DD"), 400

    service = request.args.get('service', 'walk')
    if service == 'drop-in':
        from app.capacity import get_drop_in_availability_summary
        summary = get_drop_in_availability_summary(date)
    else:
        summary = get_slot_availability_summary(date)
    return jsonify(summary)


@api_bp.route("/internal/uploads-manifest")
@internal_only
def uploads_manifest():
    """List every real (non-bundled-default) file under the uploads volume,
    with size in bytes. `web` is the only service with the uploads volume
    mounted (Railway volumes can't be shared across services), so a sibling
    reconciliation service calls this over Railway's private network to see
    what's actually on disk before diffing it against the R2 backup bucket.
    """
    root = os.path.join(current_app.static_folder, "uploads")
    manifest = {}
    for subfolder in ("dogs", "profiles"):
        folder = os.path.join(root, subfolder)
        if not os.path.isdir(folder):
            continue
        for filename in os.listdir(folder):
            if filename.startswith("default-dog"):
                continue  # bundled with the app, restored from the repo — never in R2
            path = os.path.join(folder, filename)
            if os.path.isfile(path):
                manifest[f"{subfolder}/{filename}"] = os.path.getsize(path)
    return jsonify(manifest)
