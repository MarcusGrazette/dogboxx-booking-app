"""
User-Agent detection utilities.

Provides lightweight UA parsing for the two known client environments:
Chrome on Android (walkers on the go) and Chrome on macOS (desktop users).

Usage:
    from app.utils.ua import get_device_info

    info = get_device_info()  # reads from current request
    if info.is_android:
        ...

Or in templates via the context processor (injected globally):
    {% if is_mobile %}...{% endif %}
"""

from flask import request


class DeviceInfo:
    """Parsed device information derived from the User-Agent string."""

    def __init__(self, ua_string: str):
        ua = ua_string or ""

        self.is_android = "Android" in ua
        self.is_macos = ("Macintosh" in ua or "Mac OS X" in ua) and "Android" not in ua
        # Treat anything with 'Mobile' or Android as mobile
        self.is_mobile = self.is_android or ("Mobile" in ua and "Android" not in ua)
        self.is_desktop = not self.is_mobile

    def __repr__(self):
        platform = "android" if self.is_android else ("macos" if self.is_macos else "unknown")
        form = "mobile" if self.is_mobile else "desktop"
        return f"<DeviceInfo platform={platform} form={form}>"


def get_device_info() -> DeviceInfo:
    """Return a DeviceInfo for the current request's User-Agent."""
    ua_string = request.headers.get("User-Agent", "")
    return DeviceInfo(ua_string)
