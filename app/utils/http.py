"""Shared HTTP request helpers."""

from flask import request


def wants_json():
    """True if the current request expects a JSON response rather than HTML.

    Covers explicit JSON bodies, fetch()/XHR callers (X-Requested-With), and
    fetch()'s default `Accept: */*` — checking Accept for the *absence* of
    text/html rather than the *presence* of application/json, since
    `accept_mimetypes.best` resolves `*/*` to itself, not to 'application/json'.
    """
    accepts = request.headers.get('Accept') or ''
    return (
        request.is_json
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'text/html' not in accepts
    )
