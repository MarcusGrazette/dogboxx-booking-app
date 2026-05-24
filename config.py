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
    SESSION_COOKIE_SAMESITE = 'Lax'   # Prevent cross-site cookie sending
    REMEMBER_COOKIE_SAMESITE = 'Lax'  # Same for remember-me cookies
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)  # For remember me cookies
    
    # Content Security Policy
    # script-src omits 'unsafe-inline' — a per-request nonce is appended in
    # add_security_headers and inline <script> tags carry nonce="{{ csp_nonce }}".
    # script-src-attr keeps 'unsafe-inline' transitionally so onclick/onerror/onsubmit
    # handlers in templates keep working until they're migrated to event delegation.
    CSP = {
        'default-src': "'self'",
        'script-src': "'self' https://cdn.jsdelivr.net https://unpkg.com",
        'script-src-attr': "'unsafe-inline'",
        'style-src': "'self' https://cdn.jsdelivr.net https://unpkg.com https://fonts.googleapis.com 'unsafe-inline'",
        'img-src': "'self' data:",
        'font-src': "'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com",
        'connect-src': "'self' https://cdn.jsdelivr.net https://unpkg.com",
        'frame-src': "https://iframe.mediadelivery.net",
    }
    
    # Email (Resend)
    RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
    MAIL_FROM = os.environ.get('MAIL_FROM', 'DogBoxx <noreply@dogboxx.org>')
    APP_BASE_URL = os.environ.get('APP_BASE_URL', 'http://localhost:5000')

    # Rate Limiting Configuration
    RATELIMIT_DEFAULT = "200 per day, 50 per hour"
    RATELIMIT_STORAGE_URI = "memory://"  # Flask-Limiter 3.x key (was RATELIMIT_STORAGE_URL in 2.x)
    RATELIMIT_STRATEGY = "fixed-window"  # Options: fixed-window, moving-window
    RATELIMIT_HEADERS_ENABLED = True     # Add headers to responses


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
    
    # Enhanced logging for development
    LOG_LEVEL = "DEBUG"
    LOG_FILE = "logs/development.log"
    
    # SQL query logging for development — opt in with SQL_ECHO=1.
    # Default off: echo dumps every query to stdout, which the app's
    # logging config also re-emits via the root handler — page reloads
    # crawl under the doubled I/O.
    SQLALCHEMY_ECHO = os.environ.get('SQL_ECHO') == '1'
    
    # No HTTPS enforcement in development
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    
    # Enable CSP in report-only mode for development
    CSP_REPORT_ONLY = True


class TestingConfig(Config):
    """Testing configuration.

    Uses TEST_DATABASE_URL when set (PostgreSQL in CI), otherwise falls back
    to in-memory SQLite for fast local test runs.
    """
    TESTING = True
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('TEST_DATABASE_URL', 'sqlite:///:memory:')
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
    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "memory://")  # Flask-Limiter 3.x key (was RATELIMIT_STORAGE_URL in 2.x)
    RATELIMIT_STRATEGY = "moving-window"  # More accurate but more resource-intensive


# Default to development config if FLASK_ENV not set
config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
