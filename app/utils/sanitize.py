"""Shared HTML sanitization for admin-authored rich text (Quill editor output).

Used everywhere an admin (or, for pickup notes, a client) submits HTML from
a Quill editor: Daily Messages, pickup notes, broadcasts, newsletter. One
allowlist for all of them so the risk surface — and the toolbar each editor
offers — stays consistent.
"""

import html as _html
import re as _re
from html.parser import HTMLParser

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


class _NoopenerRewriter(HTMLParser):
    """Re-serializes nh3's (already-sanitized, well-formed) output, stamping
    rel="noopener noreferrer" on any <a target=...> — client-authored
    pickup_instructions render to admins/walkers, and a same-origin `rel`
    from the author shouldn't be trusted to keep window.opener locked down.

    nh3 has no cross-attribute hook (its attribute_filter sees one attribute
    at a time, with no visibility into a sibling `target`), so this runs as
    a second pass over the parser's own token stream rather than a plain
    string replace — a regex over raw tag text would misparse an untouched
    `>` character inside an attribute value (nh3 doesn't escape it, and
    doesn't need to: it's unambiguous to a real tokenizer under a quoted
    attribute, just not to a naive regex).
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []

    def _emit_tag(self, tag, attrs, self_closing):
        if tag == 'a':
            attrs = dict(attrs)
            if 'target' in attrs:
                attrs['rel'] = 'noopener noreferrer'
            attrs = list(attrs.items())
        bits = [tag]
        for name, value in attrs:
            bits.append(name if value is None else f'{name}="{_html.escape(value, quote=True)}"')
        self.out.append(f"<{' '.join(bits)}{'/' if self_closing else ''}>")

    def handle_starttag(self, tag, attrs):
        self._emit_tag(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._emit_tag(tag, attrs, True)

    def handle_endtag(self, tag):
        self.out.append(f'</{tag}>')

    def handle_data(self, data):
        self.out.append(_html.escape(data, quote=False))

    def handle_entityref(self, name):
        self.out.append(f'&{name};')

    def handle_charref(self, name):
        self.out.append(f'&#{name};')


def _force_noopener(cleaned_html):
    parser = _NoopenerRewriter()
    parser.feed(cleaned_html)
    parser.close()
    return ''.join(parser.out)


def sanitize_rich_text(html):
    """Clean Quill-authored HTML down to the shared rich-text allowlist."""
    cleaned = nh3.clean(
        html or '',
        tags=RICH_TEXT_TAGS,
        attributes=RICH_TEXT_ATTRS,
        filter_style_properties=_ALLOWED_CSS_PROPERTIES,
        link_rel=None,
    )
    return _force_noopener(cleaned)


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
