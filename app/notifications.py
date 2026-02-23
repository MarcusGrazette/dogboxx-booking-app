"""Channel-agnostic notification service.

Usage:
    from app.notifications import notify
    notify(user, 'booking_confirmed', {'booking': booking, 'walker': walker})
"""
import logging

log = logging.getLogger(__name__)


def notify(user, template_name, context=None, channels=None):
    """Send a notification to a user via their preferred channel(s).
    
    Args:
        user: User model instance
        template_name: str — notification template key
        context: dict — template variables
        channels: list — override channels (default: user's preference)
    """
    context = context or {}
    
    if channels is None:
        pref = getattr(user, 'notification_preference', 'email')
        if pref == 'both':
            channels = ['email', 'whatsapp']
        else:
            channels = [pref]
    
    results = {}
    for channel in channels:
        try:
            if channel == 'email':
                results['email'] = _send_email(user, template_name, context)
            elif channel == 'whatsapp':
                results['whatsapp'] = _send_whatsapp(user, template_name, context)
            else:
                log.warning(f"Unknown notification channel: {channel}")
        except Exception as e:
            log.error(f"Failed to send {template_name} via {channel} to {user.email}: {e}")
            results[channel] = False
    
    return results


def _send_email(user, template_name, context):
    """Send notification via email. TODO: implement with Flask-Mail."""
    log.info(f"[EMAIL STUB] To: {user.email} | Template: {template_name} | Context: {list(context.keys())}")
    return True


def _send_whatsapp(user, template_name, context):
    """Send notification via WhatsApp Business API. TODO: implement."""
    phone = getattr(user, 'phone', None)
    if not phone:
        log.warning(f"No phone number for user {user.email}, skipping WhatsApp")
        return False
    log.info(f"[WHATSAPP STUB] To: {phone} | Template: {template_name} | Context: {list(context.keys())}")
    return True
