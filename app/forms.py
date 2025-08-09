from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, TextAreaField, HiddenField, SelectField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional
from flask_wtf.file import FileField, FileAllowed

class LoginForm(FlaskForm):
    email = StringField(
        'Email',
        validators=[
            DataRequired(message="Email is required"),
            Email(message="Invalid email address"),
            Length(max=120)
        ]
    )
    password = PasswordField(
        'Password',
        validators=[
            DataRequired(message="Password is required")
        ]
    )
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Log In')

class RegisterForm(FlaskForm):
    firstname = StringField(
        'First Name',
        validators=[
            DataRequired(message="First name is required"),
            Length(min=2, max=80, message="First name must be between 2 and 80 characters")
        ]
    )
    lastname = StringField(
        'Last Name',
        validators=[
            DataRequired(message="Last name is required"),
            Length(min=2, max=80, message="Last name must be between 2 and 80 characters")
        ]
    )
    email = StringField(
        'Email',
        validators=[
            DataRequired(message="Email is required"),
            Email(message="Invalid email address"),
            Length(max=120)
        ]
    )
    password = PasswordField(
        'Password',
        validators=[
            DataRequired(message="Password is required"),
            Length(min=8, message="Password must be at least 8 characters long")
        ]
    )
    confirmation = PasswordField(
        'Confirm Password',
        validators=[
            DataRequired(message="Password confirmation is required"),
            EqualTo('password', message="Passwords must match")
        ]
    )
    submit = SubmitField('Create Account')

class OnboardingForm(FlaskForm):
    place_id = HiddenField("Place ID")
    formatted_address = HiddenField("Formatted Address")
    display_name = HiddenField("Display Name")
    latitude = HiddenField("Latitude")
    longitude = HiddenField("Longitude")
    pickup_instructions = TextAreaField(
        "Access instructions (optional)",
        validators=[Length(max=500)],
        render_kw={
            "rows": 5,
            "placeholder": "Anything we need to know when accessing your home? Eg, door codes, notes on the concierge, fiddly keys... Or any special instructions for pickup and drop off?"},
    )
    dog_name = StringField(
        "Dog's Name",
        validators=[DataRequired()]
    )

    dog_gender = SelectField(
        'Gender',
        choices=[('', 'Select Gender'), ('male', 'Male'), ('female', 'Female')],
        validators=[DataRequired()]
    )

    dog_breed = StringField(
        'Breed',
        validators=[Optional()]
    )

    dog_allergies = StringField(
        'Allergies',
        validators=[Optional()]
    )

    dog_years = SelectField(
        'Years',
        choices=[('', 'Years')] + [(str(i), str(i)) for i in range(16)],
        validators=[DataRequired()],
    )

    dog_months = SelectField(
        'Months',
        choices=[('', 'Months')] + [(str(i), str(i)) for i in range(13)],
        validators=[DataRequired()]
    )

    dog_pic = FileField(
        validators=[
            Optional(),
            FileAllowed(['jpg', 'jpeg', 'png'], 'Images only!')
        ]
    )
    
    submit = SubmitField("Next")