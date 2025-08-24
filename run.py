from app import create_app
import os

# Create app with environment-specific configuration
app = create_app(os.environ.get('FLASK_ENV', 'development'))

if __name__ == "__main__":
    # Get port from environment or default to 5000
    port = int(os.environ.get('PORT', 5000))
    
    # In development, debug=True. In production, respect the app's config
    debug = os.environ.get('FLASK_ENV', 'development') != 'production'
    app.run(debug=debug, host='127.0.0.1', port=port)