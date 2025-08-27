"""
Walker blueprint.

This module contains routes related to walker functionality, including schedule
management and walk history.
"""
from flask import Blueprint

walker_bp = Blueprint('walker', __name__, url_prefix='/walker')

from app.blueprints.walker import routes
