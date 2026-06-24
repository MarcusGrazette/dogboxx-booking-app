"""
Admin blueprint.

This module contains routes related to admin functionality, including user
management, booking management, and system administration.
"""
from flask import Blueprint

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

from app.blueprints.admin.views import (  # noqa: E402,F401
    dashboard, revenue, board, activity, clients, walkers,
    dogs, closures, invoicing, marketing, csv_import, daily_messages,
)
