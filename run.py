from app import create_app
import os
import click

# Create app with environment-specific configuration
app = create_app(os.environ.get('FLASK_ENV', 'development'))


@app.cli.command("seed-service-types")
def seed_service_types_cmd():
    """Seed essential service types (group-walk, drop-in). Safe to run multiple times."""
    from app.seed_db.seeder import seed_service_types
    with app.app_context():
        seed_service_types()
    click.echo("Service types seeded.")


if __name__ == "__main__":
    # Get port from environment or default to 5000
    port = int(os.environ.get('PORT', 5000))
    
    # In development, debug=True. In production, respect the app's config
    debug = os.environ.get('FLASK_ENV', 'development') != 'production'
    app.run(debug=debug, host='127.0.0.1', port=port)