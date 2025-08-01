from flask import Flask
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Initialize extensions
db = SQLAlchemy()
login_manager = LoginManager()

def create_app():
    app = Flask(__name__)

    # Configuration
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config["SESSION_PERMANENT"] = False
    app.config["SESSION_TYPE"] = "filesystem"
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or 'dev-key-change-me'

    # Initialize Flask-Session
    Session(app)

    # Initialize SQLAlchemy
    db.init_app(app)

    # Initialize Flask-Login
    login_manager.init_app(app)
    login_manager.login_view = "login"
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id):
        """Load a user by their ID."""
        # Import here to avoid circular dependency
        from app.models import User
        return User.query.get(int(user_id))

    @app.after_request
    def after_request(response):
        """Ensure responses aren't cached"""
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Expires"] = 0
        response.headers["Pragma"] = "no-cache"
        return response

    # Create database tables
    with app.app_context():
        from app.models import User, Client  # Import all models
        db.create_all()  # Create tables if they don't exist

    # Import and register routes
    from app.routes import register_routes
    register_routes(app)

    return app