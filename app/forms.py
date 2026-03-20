from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, TextAreaField, HiddenField, SelectField, DateField, FieldList, FormField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional, URL
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
    remember_me = BooleanField('Remember Me', default=True)
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
    maps_url = StringField(
        "Google Maps pin URL (optional)",
        validators=[Optional(), URL(message="Please enter a valid URL"), Length(max=2048)],
        render_kw={"placeholder": "https://maps.app.goo.gl/..."}
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
    maps_url = StringField(
        "Google Maps pin URL (optional)",
        validators=[Optional(), URL(message="Please enter a valid URL"), Length(max=2048)],
        render_kw={"placeholder": "https://maps.app.goo.gl/..."}
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
    """Form for admin to create or edit a client account.

    All fields beyond name/email are optional — the admin fills in what they
    have; any remaining gaps are completed during client onboarding.
    When all three of (dog_name, dog_gender, dog_dob) are supplied the dog
    section is considered complete and will be saved.
    """

    # ── Account (required) ────────────────────────────────────────────────
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

    # ── Address & Access (optional) ───────────────────────────────────────
    address_line_1 = StringField(
        'Address Line 1',
        validators=[Optional(), Length(max=200)],
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
        validators=[Optional(), Length(max=20)],
        render_kw={"placeholder": "e.g. SE1 3QJ"}
    )
    pickup_instructions = TextAreaField(
        "Access instructions",
        validators=[Optional(), Length(max=500)],
        render_kw={"rows": 3, "placeholder": "Door codes, concierge notes, fiddly keys…"}
    )
    maps_url = StringField(
        "Google Maps pin URL",
        validators=[Optional(), URL(message="Please enter a valid URL"), Length(max=2048)],
        render_kw={"placeholder": "https://maps.app.goo.gl/…"}
    )

    # ── Contact & notifications (optional) ───────────────────────────────
    phone = StringField(
        'Phone Number',
        validators=[Optional(), Length(max=20)],
        render_kw={"placeholder": "+44 7700 900000"}
    )
    notify_email = BooleanField('Email', default=True)
    notify_whatsapp = BooleanField('WhatsApp')

    # ── Dog info (optional — all three core fields required together) ─────
    dog_name = StringField(
        "Dog's Name",
        validators=[Optional(), Length(max=50)]
    )
    dog_gender = SelectField(
        'Gender',
        choices=[('', 'Select Gender'), ('male', 'Male'), ('female', 'Female')],
        validators=[Optional()]
    )
    dog_breed = StringField('Breed', validators=[Optional()])
    dog_dob = DateField(
        'Date of Birth',
        validators=[Optional()]
    )
    dog_allergies = TextAreaField(
        'Allergies / health notes',
        validators=[Optional()],
        render_kw={"rows": 2, "placeholder": "Allergies, medical notes, special needs…"}
    )

    submit = SubmitField('Save Client')

    def validate(self, extra_validators=None):
        """If any dog field is provided, require name + gender + dob together."""
        rv = super().validate(extra_validators)
        dog_fields_provided = any([
            self.dog_name.data and self.dog_name.data.strip(),
            self.dog_gender.data,
            self.dog_dob.data,
        ])
        if dog_fields_provided:
            if not (self.dog_name.data and self.dog_name.data.strip()):
                self.dog_name.errors.append("Dog name is required when adding dog info.")
                rv = False
            if not self.dog_gender.data:
                self.dog_gender.errors.append("Gender is required when adding dog info.")
                rv = False
            if not self.dog_dob.data:
                self.dog_dob.errors.append("Date of birth is required when adding dog info.")
                rv = False
        return rv


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

class ForgotPasswordForm(FlaskForm):
    """Form for requesting a password reset email."""
    email = StringField(
        'Email address',
        validators=[DataRequired(), Email(), Length(max=120)],
        render_kw={"placeholder": "Your account email address", "autocomplete": "email"}
    )
    submit = SubmitField('Send reset link')


class ResetPasswordForm(FlaskForm):
    """Form for setting a new password via reset token."""
    password = PasswordField(
        'New password',
        validators=[DataRequired(), Length(min=8, max=128)],
        render_kw={"placeholder": "New password", "autocomplete": "new-password"}
    )
    confirm_password = PasswordField(
        'Confirm new password',
        validators=[DataRequired(), EqualTo('password', message='Passwords must match')],
        render_kw={"placeholder": "Confirm new password", "autocomplete": "new-password"}
    )
    submit = SubmitField('Reset password')
