from flask import Flask, request, redirect, render_template
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from dotenv import load_dotenv
import os
import logging
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Load environment variables from .env file
load_dotenv()

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)

def _home_url_for(user):
    """Return the most appropriate home URL for the given user."""
    from flask import url_for
    try:
        if user and user.is_authenticated:
            if user.is_admin:
                return url_for('admin.index')
            if user.role == 'walker':
                return url_for('walker.pickups')
            return url_for('client.index')
    except Exception:
        pass
    return url_for('auth.login')


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

    # Configure upload folder
    upload_folder = os.path.join(app.static_folder, 'uploads', 'dogs')
    app.config['UPLOAD_FOLDER'] = upload_folder
    os.makedirs(upload_folder, exist_ok=True)

    # Initialize CSRF protection
    csrf.init_app(app)

    # Initialize SQLAlchemy
    db.init_app(app)

    # Flask-Session: must point at the db instance before Session(app) is called,
    # so it uses the same engine and auto-creates the sessions table.
    app.config['SESSION_SQLALCHEMY'] = db
    Session(app)

    # Fire queued SSE broadcasts after each DB commit.
    # create_notification() stashes events in db.session.info['sse_pending'];
    # this listener drains them once the transaction is safely committed.
    from sqlalchemy import event as sa_event

    @sa_event.listens_for(db.session, 'after_commit')
    def _fire_sse_after_commit(session):
        # ── SSE ──────────────────────────────────────────────────────────────
        pending = session.info.pop('sse_pending', [])
        if pending:
            from app.sse import broadcast
            for item in pending:
                broadcast(item['user_id'], item['event'], item['data'])

        # ── Web Push ─────────────────────────────────────────────────────────
        wp_pending = session.info.pop('webpush_pending', [])
        if wp_pending:
            from app.utils.webpush import send_web_push
            for item in wp_pending:
                try:
                    send_web_push(
                        user_id=item['user_id'],
                        title=item['title'],
                        body=item.get('body', ''),
                        link=item.get('link', '/'),
                        icon=item.get('icon'),
                        unread_count=item.get('unread_count', 1),
                        subscriptions=item.get('subscriptions', []),
                    )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        'Web Push after_commit error for user %s: %s',
                        item['user_id'], e
                    )

    # Initialize Flask-Migrate for database migrations
    migrate.init_app(app, db)
    
    # Initialize Rate Limiter with default limits
    limiter.init_app(app)

    # Initialize Flask-Login
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = None

    @login_manager.user_loader
    def load_user(user_id):
        """Load a user by their ID."""
        # Import here to avoid circular dependency
        from app.models import User
        return db.session.get(User, int(user_id))

    # HTTPS redirection middleware
    @app.before_request
    def enforce_https():
        """Redirect HTTP requests to HTTPS"""
        # Skip healthcheck — Railway probes internally over HTTP
        if request.path == '/health':
            return
        # Only enforce in production
        if not app.debug and not app.testing:
            # Respect X-Forwarded-Proto set by Railway's proxy
            proto = request.headers.get('X-Forwarded-Proto', '')
            if proto == 'http':
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

        # Redirect clients who haven't completed onboarding.
        # Exception: secondary owners have shared access to another client's dog —
        # they don't need to onboard themselves.
        if current_user.role == 'client':
            if request.endpoint not in ['client.onboard', 'auth.logout', 'auth.change_password', 'static']:
                from app.models import Client, DogOwner
                client = Client.query.filter_by(user_id=current_user.id).first()
                if not client or not client.onboarding_completed:
                    has_secondary_dog = DogOwner.query.filter_by(
                        user_id=current_user.id, role='secondary'
                    ).first() is not None
                    if not has_secondary_dog:
                        from flask import url_for
                        return redirect(url_for('client.onboard'))

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
                                WalkerSchedule, WalkerAdHocAvailability, ServiceType, Booking,
                                BookingStatusChange, WalkEvent, Notification)

    # Custom error handler for rate limiting
    @app.errorhandler(413)
    def too_large(e):
        return render_template('error.html',
                              error_code=413,
                              error_message="File too large.",
                              error_description="Photos must be under 10 MB. Try reducing the image size before uploading."), 413

    @app.errorhandler(429)
    def ratelimit_handler(e):
        return render_template('error.html',
                              error_code=429,
                              error_message="Too many attempts. Please try again later.",
                              error_description="For security reasons, we limit the number of requests. Please wait a few minutes before trying again."), 429

    @app.errorhandler(404)
    def not_found(e):
        from flask import url_for
        from flask_login import current_user
        home_url = _home_url_for(current_user)
        return render_template('error.html',
                              error_code=404,
                              error_message="Page not found.",
                              error_description="The page you're looking for doesn't exist or has been moved.",
                              home_url=home_url), 404

    @app.errorhandler(403)
    def forbidden(e):
        from flask import url_for
        from flask_login import current_user
        home_url = _home_url_for(current_user)
        return render_template('error.html',
                              error_code=403,
                              error_message="Access denied.",
                              error_description="You don't have permission to view this page.",
                              home_url=home_url), 403

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        return render_template('500.html'), 500

    @app.context_processor
    def inject_csrf_token():
        from flask_wtf.csrf import generate_csrf
        return dict(csrf_token=generate_csrf)

    @app.template_filter('wa_number')
    def wa_number_filter(phone: str) -> str:
        """Format a phone number for use in a wa.me URL.

        Strips all non-digit characters, then converts a UK local number
        starting with 0 to the international 44 prefix.
        e.g. '+44 7700 900000' → '447700900000'
             '07700 900000'    → '447700900000'
        """
        import re
        digits = re.sub(r'\D', '', phone or '')
        if digits.startswith('0'):
            digits = '44' + digits[1:]
        return digits

    @app.context_processor
    def inject_device_info():
        """Expose UA device flags to all templates.

        Available in every template:
            {{ is_mobile }}   — True on Android / any mobile browser
            {{ is_desktop }}  — True on macOS / non-mobile
            {{ is_android }}  — True specifically on Android Chrome
            {{ is_macos }}    — True specifically on macOS Chrome
        """
        from app.utils.ua import get_device_info
        d = get_device_info()
        return dict(
            is_mobile=d.is_mobile,
            is_desktop=d.is_desktop,
            is_android=d.is_android,
            is_macos=d.is_macos,
        )

    @app.context_processor
    def inject_pending_counts():
        """Inject pending booking counts for sidebar badges (admin only)."""
        from flask_login import current_user
        if current_user.is_authenticated and current_user.is_admin:
            from app.models import Booking, ServiceType
            PENDING = ('requested', 'waitlisted')
            gw = ServiceType.query.filter_by(slug='group-walk').first()
            di = ServiceType.query.filter_by(slug='drop-in').first()
            pending_group_walks = (
                Booking.query
                .filter(Booking.status.in_(PENDING),
                        Booking.service_type_id == gw.id)
                .count()
            ) if gw else 0
            pending_drop_ins = (
                Booking.query
                .filter(Booking.status.in_(PENDING),
                        Booking.service_type_id == di.id)
                .count()
            ) if di else 0
            return dict(
                pending_group_walks=pending_group_walks,
                pending_drop_ins=pending_drop_ins,
            )
        return dict(pending_group_walks=0, pending_drop_ins=0)

    @app.context_processor
    def inject_notifications():
        """Inject unread notification count + recent notifications into all templates.
        Also exposes the VAPID public key for Web Push registration.
        """
        from flask_login import current_user
        from app.utils.notifications import get_unread_count, get_recent, get_meta
        vapid_public_key = app.config.get('VAPID_PUBLIC_KEY', '')
        if current_user.is_authenticated:
            unread_count = get_unread_count(current_user.id)
            recent_notifications = get_recent(current_user.id, limit=8)
            return dict(
                unread_notification_count=unread_count,
                recent_notifications=recent_notifications,
                notification_meta=get_meta,
                vapid_public_key=vapid_public_key,
            )
        return dict(
            unread_notification_count=0,
            recent_notifications=[],
            notification_meta=get_meta,
            vapid_public_key=vapid_public_key,
        )

    # Register blueprints for modular routing
    from app.blueprints.register import register_blueprints
    register_blueprints(app)

    # Serve the Service Worker from the root scope so it can control the
    # entire app (not just /static/js/).  Must be at /sw.js, not /static/…
    from flask import send_from_directory, make_response

    @app.route('/health')
    def health():
        return 'ok', 200

    @app.route('/sw.js')
    def service_worker():
        static_js = os.path.join(app.root_path, 'static', 'js')
        resp = make_response(send_from_directory(static_js, 'sw.js'))
        resp.headers['Content-Type'] = 'application/javascript'
        resp.headers['Service-Worker-Allowed'] = '/'
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    return app