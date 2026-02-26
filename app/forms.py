from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, TextAreaField, HiddenField, SelectField, DateField, FieldList, FormField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional
from flask_wtf.file import FileField, FileAllowed
from .validators import wtforms_password_validator
from datetime import datetime, timezone, timedelta
from wtforms import ValidationError

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
            Length(min=8, message="Password must be at least 8 characters long"),
            wtforms_password_validator
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
    address_line_1 = StringField(
        'Address Line 1',
        validators=[DataRequired(), Length(max=200)],
        render_kw={"placeholder": "Street address"}
    )
    address_line_2 = StringField(
        'Address Line 2',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Flat, floor, etc. (optional)"}
    )
    address_line_3 = StringField(
        'Address Line 3',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Area / neighbourhood (optional)"}
    )
    postcode = StringField(
        'Postcode',
        validators=[DataRequired(), Length(max=20)],
        render_kw={"placeholder": "e.g. SE1 3QJ"}
    )
    pickup_instructions = TextAreaField(
        "Access instructions (optional)",
        validators=[Length(max=500)],
        render_kw={
            "rows": 5,
            "placeholder": "Anything we need to know when accessing your home? Eg, door codes, notes on the concierge, fiddly keys... Or any special instructions for pickup and drop off?"},
    )
    notify_email = BooleanField('Email', default=True)
    notify_whatsapp = BooleanField('WhatsApp')
    phone = StringField(
        'Phone Number',
        validators=[Optional(), Length(max=20)],
        render_kw={"placeholder": "e.g. +44 7700 900000"}
    )

    dog_name = StringField(
        "Dog's Name",
        validators=[DataRequired(), Length(max=50)]
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

    dog_dob = DateField(
        'Date of Birth',
        validators=[DataRequired(message="Please enter your dog's date of birth")]
    )
    
    submit = SubmitField("Next")

class BookingForm(FlaskForm):
    date = DateField(
        validators=[DataRequired()]
    )
    slot = SelectField(
        'Slot',
        choices=[('', 'Slot'), ('Morning', 'Morning'), ('Afternoon', 'Afternoon')],
        validators=[DataRequired()]
    )

    submit = SubmitField('Book')
    
    def validate_date(self, field):
        """Custom validator to ensure date is at least tomorrow"""
        if field.data:
            tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
            if field.data < tomorrow:
                raise ValidationError('Booking date must be at least tomorrow.')
            
            # Keep the 3-month limit validation here too
            max_date = (datetime.now(timezone.utc) + timedelta(days=90)).date()
            if field.data > max_date:
                raise ValidationError('Booking date cannot be more than 3 months in the future.')


class ProfileForm(FlaskForm):
    """Form for clients to edit their profile, address, notification prefs and dog info."""
    # Personal info
    firstname = StringField(
        'First Name',
        validators=[DataRequired(), Length(min=2, max=80)]
    )
    lastname = StringField(
        'Last Name',
        validators=[DataRequired(), Length(min=2, max=80)]
    )

    # Address
    address_line_1 = StringField(
        'Address Line 1',
        validators=[DataRequired(), Length(max=200)],
        render_kw={"placeholder": "Street address"}
    )
    address_line_2 = StringField(
        'Address Line 2',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Flat, floor, etc. (optional)"}
    )
    address_line_3 = StringField(
        'Address Line 3',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Area / neighbourhood (optional)"}
    )
    postcode = StringField(
        'Postcode',
        validators=[DataRequired(), Length(max=20)],
        render_kw={"placeholder": "e.g. SE1 3QJ"}
    )
    pickup_instructions = TextAreaField(
        "Access instructions (optional)",
        validators=[Length(max=500)],
        render_kw={"rows": 3, "placeholder": "Door codes, concierge notes, special instructions..."}
    )

    # Notifications
    notify_email = BooleanField('Email', default=True)
    notify_whatsapp = BooleanField('WhatsApp')
    phone = StringField(
        'Phone Number',
        validators=[Optional(), Length(max=20)],
        render_kw={"placeholder": "e.g. +44 7700 900000"}
    )

    # Dog info
    dog_name = StringField(
        "Dog's Name",
        validators=[DataRequired(), Length(max=50)]
    )
    dog_gender = SelectField(
        'Gender',
        choices=[('', 'Select Gender'), ('male', 'Male'), ('female', 'Female')],
        validators=[DataRequired()]
    )
    dog_breed = StringField('Breed', validators=[Optional()])
    dog_dob = DateField(
        'Date of Birth',
        validators=[DataRequired(message="Please enter your dog's date of birth")]
    )
    dog_allergies = StringField('Allergies', validators=[Optional()])

    submit = SubmitField("Save Changes")


class ClientCreateForm(FlaskForm):
    """Form for admin to create a new client account"""
    email = StringField(
        'Email',
        validators=[
            DataRequired(message="Email is required"),
            Email(message="Invalid email address"),
            Length(max=120)
        ]
    )
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
    submit = SubmitField('Create Client')


class WalkerCreateForm(FlaskForm):
    """Form for admin to create a new walker account"""
    email = StringField(
        'Email',
        validators=[
            DataRequired(message="Email is required"),
            Email(message="Invalid email address"),
            Length(max=120)
        ]
    )
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
    submit = SubmitField('Create Walker')


class PasswordChangeForm(FlaskForm):
    """Form for users to change their password"""
    current_password = PasswordField(
        'Current Password',
        validators=[
            DataRequired(message="Current password is required")
        ]
    )
    new_password = PasswordField(
        'New Password',
        validators=[
            DataRequired(message="New password is required"),
            Length(min=8, message="Password must be at least 8 characters long"),
            wtforms_password_validator
        ]
    )
    confirm_password = PasswordField(
        'Confirm New Password',
        validators=[
            DataRequired(message="Password confirmation is required"),
            EqualTo('new_password', message="Passwords must match")
        ]
    )
    submit = SubmitField('Change Password')


class WalkerScheduleSlotForm(FlaskForm):
    """Single day/slot form for walker schedule"""
    morning = BooleanField('Morning')
    afternoon = BooleanField('Afternoon')


class WalkerScheduleForm(FlaskForm):
    """Form for admin to manage walker's weekly schedule"""
    monday = FormField(WalkerScheduleSlotForm, label='Monday')
    tuesday = FormField(WalkerScheduleSlotForm, label='Tuesday')
    wednesday = FormField(WalkerScheduleSlotForm, label='Wednesday')
    thursday = FormField(WalkerScheduleSlotForm, label='Thursday')
    friday = FormField(WalkerScheduleSlotForm, label='Friday')
    saturday = FormField(WalkerScheduleSlotForm, label='Saturday')
    sunday = FormField(WalkerScheduleSlotForm, label='Sunday')
    submit = SubmitField('Update Schedule')