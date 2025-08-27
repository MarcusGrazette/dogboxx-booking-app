"""
Authentication blueprint.

This module contains routes related to user authentication, including login,
registration, logout, and password reset.
"""
from flask import Blueprint

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

from app.blueprints.auth import routes
