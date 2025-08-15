import re
from wtforms.validators import ValidationError

def password_strength_check(password: str) -> tuple[bool, str]:
    """Return (True, message) for valid, (False, reason) for invalid."""
    if not password:
        return False, "Password is required"
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r'\d', password):
        return False, "Password must contain at least one number"
    return True, "Valid password"

def wtforms_password_validator(form, field):
    """WTForms-style validator that raises ValidationError on failure."""
    ok, msg = password_strength_check(field.data or "")
    if not ok:
        raise ValidationError(msg)