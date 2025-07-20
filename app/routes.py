from flask import request, session, redirect, render_template
from werkzeug.security import check_password_hash, generate_password_hash
from app.models import User
from app import db

def register_routes(app):
    """Register all routes with the Flask app"""
    
    @app.route("/")
    def index():
        """Render the home page or redirect to login."""
        if "user_id" not in session:
            return redirect("/login")

        # Query the logged-in user
        user = User.query.get(session["user_id"])

        return render_template("index.html", username=user.username)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """Log user in"""

        # Forget any user_id
        session.clear()

        # User reached route via POST (as by submitting a form via POST)
        if request.method == "POST":
            # Ensure username was submitted
            if not request.form.get("username"):
                return apology("must provide username", 403)

            # Ensure password was submitted
            elif not request.form.get("password"):
                return apology("must provide password", 403)

            # Query database for username
            user = User.query.filter_by(username=request.form.get("username")).first()

            # Ensure username exists and password is correct
            if not user or not check_password_hash(user.hashed_password, request.form.get("password")):
                return apology("invalid username and/or password", 403)

            # Remember which user has logged in
            session["user_id"] = user.id

            # Redirect user to home page
            return redirect("/")

        # User reached route via GET (as by clicking a link or via redirect)
        else:
            return render_template("login.html")

    @app.route("/logout")
    def logout():
        """Log user out"""

        # Forget any user_id
        session.clear()

        # Redirect user to login form
        return redirect("/")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        """Register a new user"""
        # User reached route via POST (as by submitting a form via POST)
        if request.method == "POST":
            # Store form values
            username = request.form.get("username")
            password = request.form.get("password")
            confirmation = request.form.get("confirmation")

            # Validity checks
            if not username:
                return apology("missing username")

            if not password or not confirmation or password != confirmation:
                return apology("Missing password, or passwords don't match")
            else:
                pwhash = generate_password_hash(password)

            try:
                # Add user to the database
                new_user = User(username=username, hashed_password=pwhash, role="client")
                db.session.add(new_user)
                db.session.commit()
            except Exception as e:
                if "UNIQUE constraint failed" in str(e):
                    return apology("Username already exists")
                return apology("Error adding user")

            return redirect("/")
        # User reached route via GET (as by clicking a link or via redirect)
        else:
            return render_template("register.html")

    def apology(message, code=400):
        """Render message as an apology to user."""
        return render_template("apology.html", message=message), code