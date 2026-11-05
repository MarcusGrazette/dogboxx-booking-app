"""Shared HTML sanitization for admin-authored rich text (Quill editor output).

Used everywhere an admin (or, for pickup notes, a client) submits HTML from
a Quill editor: Daily Messages, pickup notes, broadcasts, newsletter. One
allowlist for all of them so the risk surface — and the toolbar each editor
offers — stays consistent.
"""

import html as _html
import re as _re

import nh3

RICH_TEXT_TAGS = {
    'a', 'abbr', 'acronym', 'b', 'blockquote', 'code', 'em', 'i', 'li', 'ol',
    'strong', 'ul', 'p', 'br', 'h1', 'h2', 'h3', 'u', 's', 'pre', 'span',
}
RICH_TEXT_ATTRS = {
    'a': {'href', 'target'},
    'span': {'class', 'style'},
    '*': {'class'},
}
# Quill's color picker writes inline style="color:..." on a <span> — allow
# only that one property through, everything else in `style` is stripped.
_ALLOWED_CSS_PROPERTIES = {'color'}


def sanitize_rich_text(html):
    """Clean Quill-authored HTML down to the shared rich-text allowlist.

    Every link gets rel="noopener noreferrer", replacing any author-supplied
    rel: client-authored pickup notes render to admins and walkers, and an
    author's rel shouldn't be trusted to keep window.opener locked down.
    """
    return nh3.clean(
        html or '',
        tags=RICH_TEXT_TAGS,
        attributes=RICH_TEXT_ATTRS,
        filter_style_properties=_ALLOWED_CSS_PROPERTIES,
        link_rel='noopener noreferrer',
    )


def clean_rich_text_or_none(html):
    """Sanitize Quill HTML, collapsing an "empty" editor (Quill's empty
    state is markup like `<p><br></p>`, not an empty string) down to None."""
    cleaned = sanitize_rich_text(html)
    if not nh3.clean(cleaned, tags=set()).strip():
        return None
    return cleaned


_BLOCK_BOUNDARY_RE = _re.compile(r'</(p|div|h1|h2|h3|li|blockquote|pre)>|<br\s*/?>', _re.IGNORECASE)


def rich_text_to_plain(html):
    """Collapse sanitized rich-text HTML to a single-line plain-text summary —
    for surfaces that can't render markup (e.g. the notification bell).

    Unescapes entities after stripping: nh3.clean's output is HTML source
    (e.g. "Fish &amp; Chips"), but a plain-text summary should read as plain
    text ("Fish & Chips").
    """
    spaced = _BLOCK_BOUNDARY_RE.sub(' ', html or '')
    text = nh3.clean(spaced, tags=set())
    return ' '.join(_html.unescape(text).split())
