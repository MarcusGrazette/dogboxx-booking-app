"""User-Agent detection — just "is this a phone?".

Exposed to every template as {{ is_mobile }} by app/__init__.py's
inject_device_info context processor.
"""

from flask import request


def is_mobile_request() -> bool:
    """True when the current request's User-Agent is a phone browser: any
    Android, or anything else that says 'Mobile' (e.g. iPhone Safari)."""
    ua = request.headers.get("User-Agent", "")
    return "Android" in ua or "Mobile" in ua
