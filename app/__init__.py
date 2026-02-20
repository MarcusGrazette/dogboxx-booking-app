from flask import Flask, request, redirect
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from dotenv import load_dotenv
import os
import logging
from flask_dropzone import Dropzone
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Load environment variables from .env file
load_dotenv()

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
dropzone = Dropzone()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)

def create_app(config_name=None):
    app = Flask(__name__)
    
    # Determine configuration to use
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'default')
    
    # Import and use configuration from config.py
    from config import config
    app.config.from_object(config[config_name])
    
    # Configure logging
    from app.utils.logging_config import configure_logging
    log_level = app.config.get('LOG_LEVEL', 'INFO')
    log_file = app.config.get('LOG_FILE')
    configure_logging(app_name=app.name, log_level=log_level, log_file=log_file)
    
    # Validate critical environment variables
    if not app.config.get('SECRET_KEY') and not app.debug:
        raise RuntimeError("SECRET_KEY environment variable is not set. "
                          "This is required for application security.")

    # Initialize Flask-Session
    Session(app)

    # Initialize Dropzone
    dropzone.init_app(app)

    # Initialize CSRF protection
    csrf.init_app(app)

    # Initialize SQLAlchemy
    db.init_app(app)
    
    # Initialize Flask-Migrate for database migrations
    migrate.init_app(app, db)
    
    # Initialize Rate Limiter with default limits
    limiter.init_app(app)

    # Initialize Flask-Login
    login_manager.init_app(app)
    login_manager.login_view = "login"
    login_manager.login_message = None

    @login_manager.user_loader
    def load_user(user_id):
        """Load a user by their ID."""
        # Import here to avoid circular dependency
        from app.models import User
        return User.query.get(int(user_id))

    # HTTPS redirection middleware
    @app.before_request
    def enforce_https():
        """Redirect HTTP requests to HTTPS"""
        # Only enforce in production
        if not app.debug and not app.testing:
            if not request.is_secure:
                url = request.url.replace('http://', 'https://', 1)
                return redirect(url, code=301)

    @app.before_request
    def check_password_change_required():
        """Redirect users who must change their password"""
        from flask_login import current_user
        
        # Skip for non-authenticated users
        if not current_user.is_authenticated:
            return
            
        # Skip for logout and change password routes
        if request.endpoint in ['auth.logout', 'auth.change_password']:
            return
            
        # Skip for static files and API endpoints
        if (request.endpoint and 
            (request.endpoint.startswith('static') or 
             request.endpoint.startswith('api.'))):
            return
            
        # Redirect if password change is required
        if current_user.must_change_password:
            return redirect('/auth/change-password')

    @app.after_request
    def add_security_headers(response):
        """Add security-related headers to response"""
        # Basic cache control
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Expires"] = "0"
        response.headers["Pragma"] = "no-cache"
        
        # Only add security headers in non-debug mode
        if not app.debug and not app.testing:
            # HSTS header (HTTP Strict Transport Security)
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
            
            # Other security headers
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'SAMEORIGIN'
            response.headers['X-XSS-Protection'] = '1; mode=block'
        
        # Add Content Security Policy header
        if app.config.get('CSP'):
            csp_parts = []
            for directive, sources in app.config['CSP'].items():
                csp_parts.append(f"{directive} {sources}")
            
            csp_header = '; '.join(csp_parts)
            
            # Determine whether to use report-only or enforcement mode
            if app.config.get('CSP_REPORT_ONLY', False):
                response.headers['Content-Security-Policy-Report-Only'] = csp_header
            else:
                response.headers['Content-Security-Policy'] = csp_header
        
        return response

    # Import models for Flask-Migrate
    with app.app_context():
        # Import all models so they're registered with SQLAlchemy
        from app.models import (User, Client, Dog, DogOwner, Walker,
                                WalkerSchedule, ServiceType, Booking,
                                BookingStatusChange, WalkEvent)

    # Register blueprints for modular routing
    from app.blueprints.register import register_blueprints
    register_blueprints(app)
    
    # Register legacy routes (will be migrated to blueprints)
    from app.routes import register_routes
    register_routes(app)

    return app