"""
Admin blueprint.

This module contains routes related to admin functionality, including user
management, booking management, and system administration.
"""
from flask import Blueprint

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

from app.blueprints.admin import routes
