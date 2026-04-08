from app import create_app
import os
import click

# Create app with environment-specific configuration
app = create_app(os.environ.get('FLASK_ENV', 'development'))


@app.cli.command("seed-service-types")
def seed_service_types_cmd():
    """Seed essential service types (group-walk, drop-in). Safe to run multiple times."""
    from app.seed_db.seeder import seed_service_types
    from app import db
    with app.app_context():
        seed_service_types()
        db.session.commit()
    click.echo("Service types seeded.")


@app.cli.command("create-admin")
@click.option("--email", prompt=True, help="Admin email address")
@click.option("--firstname", prompt=True, help="First name")
@click.option("--lastname", prompt=True, help="Last name")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Password")
def create_admin(email, firstname, lastname, password):
    """Create an admin user with a walker record. Safe to run on a live DB."""
    from app.models import User, Walker
    from app import db
    from werkzeug.security import generate_password_hash
    with app.app_context():
        existing = User.query.filter_by(email=email.lower()).first()
        if existing:
            click.echo(f"User {email} already exists.")
            return
        user = User(
            firstname=firstname.strip().title(),
            lastname=lastname.strip().title(),
            email=email.strip().lower(),
            role='walker',
            is_admin=True,
            hashed_password=generate_password_hash(password),
            must_change_password=False,
            active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(Walker(user_id=user.id))
        db.session.commit()
        click.echo(f"Admin user {email} created successfully (with walker record).")


@app.cli.command("make-walker")
@click.option("--email", prompt=True, help="Email of existing user to give a walker record")
def make_walker(email):
    """Add a Walker record to an existing user. Safe to run if record already exists."""
    from app.models import User, Walker
    from app import db
    with app.app_context():
        user = User.query.filter_by(email=email.lower()).first()
        if not user:
            click.echo(f"No user found with email {email}.")
            return
        if Walker.query.filter_by(user_id=user.id).first():
            click.echo(f"{email} already has a walker record.")
            return
        db.session.add(Walker(user_id=user.id))
        db.session.commit()
        click.echo(f"Walker record created for {email}.")


if __name__ == "__main__":
    # Get port from environment or default to 5000
    port = int(os.environ.get('PORT', 5000))
    
    # In development, debug=True. In production, respect the app's config
    debug = os.environ.get('FLASK_ENV', 'development') != 'production'
    app.run(debug=debug, host='127.0.0.1', port=port)