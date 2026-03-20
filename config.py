import os
from datetime import timedelta

class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Web Push (VAPID)
    VAPID_PRIVATE_KEY   = os.environ.get('VAPID_PRIVATE_KEY', '')
    VAPID_PUBLIC_KEY    = os.environ.get('VAPID_PUBLIC_KEY', '')
    VAPID_CLAIMS_EMAIL  = os.environ.get('VAPID_CLAIMS_EMAIL', 'admin@dogboxx.org')
    SESSION_TYPE = "sqlalchemy"          # store sessions in the app DB (works on Railway)
    SESSION_SQLALCHEMY_TABLE = "sessions" # table name in Postgres/SQLite
    SESSION_PERMANENT = True
    
    # Logging configuration
    LOG_LEVEL = "INFO"
    LOG_FILE = None  # No file logging by default
    
    # Upload folder (overridden in create_app to use static/uploads/dogs/)
    UPLOAD_FOLDER = os.path.join(os.getcwd(), "app", "static", "uploads", "dogs")

    # Cap request body size — protects against large file upload abuse
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB
    
    # Security settings
    SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to session cookie
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)  # For remember me cookies
    
    # Content Security Policy
    CSP = {
        'default-src': "'self'",
        'script-src': "'self' https://cdn.jsdelivr.net https://unpkg.com 'unsafe-inline'",
        'style-src': "'self' https://cdn.jsdelivr.net https://unpkg.com 'unsafe-inline'",
        'img-src': "'self' data:",
        'font-src': "'self' https://cdn.jsdelivr.net",
    }
    
    # Email (Resend)
    RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
    MAIL_FROM = os.environ.get('MAIL_FROM', 'Dogboxx <noreply@dogboxx.org>')
    APP_BASE_URL = os.environ.get('APP_BASE_URL', 'http://localhost:5000')

    # Rate Limiting Configuration
    RATELIMIT_DEFAULT = "200 per day, 50 per hour"
    RATELIMIT_STORAGE_URL = "memory://"  # Use in-memory storage for development
    RATELIMIT_STRATEGY = "fixed-window"  # Options: fixed-window, moving-window
    RATELIMIT_HEADERS_ENABLED = True     # Add headers to responses


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
    
    # Enhanced logging for development
    LOG_LEVEL = "DEBUG"
    LOG_FILE = "logs/development.log"
    
    # SQL query logging for development
    SQLALCHEMY_ECHO = True
    
    # No HTTPS enforcement in development
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    
    # Enable CSP in report-only mode for development
    CSP_REPORT_ONLY = True


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False

    # No HTTPS enforcement in testing
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    
    # HTTPS enforcement in production
    SESSION_COOKIE_SECURE = True  # Only send cookies over HTTPS
    REMEMBER_COOKIE_SECURE = True  # For remember me cookies
    
    # Set secure headers in production
    CSP_REPORT_ONLY = False
    
    # More strict rate limiting in production
    # Use Redis for distributed rate limiting if running multiple instances
    RATELIMIT_STORAGE_URL = os.environ.get("REDIS_URL", "memory://")  # Fallback to memory storage
    RATELIMIT_STRATEGY = "moving-window"  # More accurate but more resource-intensive


# Default to development config if FLASK_ENV not set
config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
