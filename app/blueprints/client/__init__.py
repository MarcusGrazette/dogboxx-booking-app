"""
Client blueprint.

This module contains routes related to client functionality, including booking
management, profile management, and dog information.
"""
from flask import Blueprint

client_bp = Blueprint('client', __name__, url_prefix='/')

from app.blueprints.client.views import (  # noqa: E402,F401
    general, bookings, profile, onboarding,
)
