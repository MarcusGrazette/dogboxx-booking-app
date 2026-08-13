"""Shared HTML sanitization for admin-authored rich text (Quill editor output).

Used everywhere an admin (or, for pickup notes, a client) submits HTML from
a Quill editor: Daily Messages, pickup notes, broadcasts, newsletter. One
allowlist for all of them so the risk surface — and the toolbar each editor
offers — stays consistent.
"""

import html as _html
import re as _re

import bleach
from bleach.css_sanitizer import CSSSanitizer
from bleach.html5lib_shim import Filter

RICH_TEXT_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    'p', 'br', 'h1', 'h2', 'h3', 'ul', 'ol', 'li', 'strong', 'em',
    'u', 's', 'blockquote', 'pre', 'code', 'a', 'span',
]
RICH_TEXT_ATTRS = {
    'a': ['href', 'target', 'rel'],
    'span': ['class', 'style'],
    '*': ['class'],
}
# Quill's color picker writes inline style="color:..." on a <span> — allow
# only that one property through, everything else in `style` is stripped.
_CSS_SANITIZER = CSSSanitizer(allowed_css_properties=['color'])


class _ForceNoopenerFilter(Filter):
    """Stamp rel="noopener noreferrer" on any <a target=...> — client-authored
    pickup_instructions render to admins/walkers, and a same-origin `rel` from
    the author shouldn't be trusted to keep window.opener locked down."""

    def __iter__(self):
        for token in Filter.__iter__(self):
            if token.get('type') in ('StartTag', 'EmptyTag') and token.get('name') == 'a':
                data = token.get('data', {})
                if (None, 'target') in data:
                    data[(None, 'rel')] = 'noopener noreferrer'
            yield token


_CLEANER = bleach.sanitizer.Cleaner(
    tags=RICH_TEXT_TAGS,
    attributes=RICH_TEXT_ATTRS,
    css_sanitizer=_CSS_SANITIZER,
    filters=[_ForceNoopenerFilter],
)


def sanitize_rich_text(html):
    """Clean Quill-authored HTML down to the shared rich-text allowlist."""
    return _CLEANER.clean(html or '')


def clean_rich_text_or_none(html):
    """Sanitize Quill HTML, collapsing an "empty" editor (Quill's empty
    state is markup like `<p><br></p>`, not an empty string) down to None."""
    cleaned = sanitize_rich_text(html)
    if not bleach.clean(cleaned, tags=[], strip=True).strip():
        return None
    return cleaned


_BLOCK_BOUNDARY_RE = _re.compile(r'</(p|div|h1|h2|h3|li|blockquote|pre)>|<br\s*/?>', _re.IGNORECASE)


def rich_text_to_plain(html):
    """Collapse sanitized rich-text HTML to a single-line plain-text summary —
    for surfaces that can't render markup (e.g. the notification bell).

    Unescapes entities after stripping: bleach.clean's output is HTML source
    (e.g. "Fish &amp; Chips"), but a plain-text summary should read as plain
    text ("Fish & Chips") — this also keeps a disallowed tag that
    sanitize_rich_text already escaped (e.g. a stray "&lt;script&gt;") from
    surfacing as literal entity gibberish instead of being dropped as text.
    """
    spaced = _BLOCK_BOUNDARY_RE.sub(' ', html or '')
    text = bleach.clean(spaced, tags=[], strip=True)
    return ' '.join(_html.unescape(text).split())
