"""
API routes.

JSON API endpoints for AJAX calls from the frontend.
"""

from flask import request, jsonify
from flask_login import login_required
from datetime import datetime

from app.blueprints.api import api_bp
from app.capacity import get_slot_availability_summary


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

    summary = get_slot_availability_summary(date)
    return jsonify(summary)
