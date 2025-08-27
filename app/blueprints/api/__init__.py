"""
API blueprint.

This module contains API routes for the application, providing JSON endpoints
for AJAX operations and potential external integrations.
"""
from flask import Blueprint

api_bp = Blueprint('api', __name__, url_prefix='/api')

from app.blueprints.api import routes
