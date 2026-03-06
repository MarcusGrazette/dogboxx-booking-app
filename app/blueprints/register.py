"""
Blueprint registration.

This module handles the registration of all blueprints in the application.
"""

def register_blueprints(app):
    """
    Register all application blueprints.
    
    Args:
        app: Flask application instance
    """
    # Import and register API blueprint
    from app.blueprints.api import api_bp
    app.register_blueprint(api_bp)
    
    # Import and register Admin blueprint
    from app.blueprints.admin import admin_bp
    app.register_blueprint(admin_bp)
    
    # Import and register Auth blueprint
    from app.blueprints.auth import auth_bp
    app.register_blueprint(auth_bp)
    
    # Import and register Client blueprint
    from app.blueprints.client import client_bp
    app.register_blueprint(client_bp)
    
    # Import and register Walker blueprint
    from app.blueprints.walker import walker_bp
    app.register_blueprint(walker_bp)

    # Import and register Notifications blueprint
    from app.blueprints.notifications import notifications_bp
    app.register_blueprint(notifications_bp)
