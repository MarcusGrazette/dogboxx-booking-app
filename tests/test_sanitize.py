"""
Tests for app.utils.sanitize — the shared bleach allowlist used by Daily
Messages, pickup notes, broadcasts, and the newsletter.

Covers:
- sanitize_rich_text: allowed formatting tags survive, disallowed tags/attrs
  (script, event handlers, javascript: hrefs) are stripped/escaped, and the
  Quill color picker's inline style survives via the scoped CSS sanitizer.
- clean_rich_text_or_none: collapses an "empty" Quill editor (whose innerHTML
  is markup like <p><br></p>, not an empty string) down to None.
- rich_text_to_plain: produces a readable, single-line summary for surfaces
  that can't render markup (the notification bell).
"""
from app.utils.sanitize import (
    sanitize_rich_text, clean_rich_text_or_none, rich_text_to_plain,
)


class TestSanitizeRichText:

    def test_allows_formatting_tags(self):
        html = '<h1>Heading</h1><p><strong>Bold</strong> and <em>italic</em></p>'
        assert sanitize_rich_text(html) == html

    def test_allows_lists_and_blockquote(self):
        html = '<ul><li>One</li><li>Two</li></ul><blockquote>Quoted</blockquote>'
        assert sanitize_rich_text(html) == html

    def test_strips_script_tag(self):
        result = sanitize_rich_text('<p>Hi</p><script>alert(1)</script>')
        assert '<script>' not in result
        assert 'alert' not in result or '&lt;script&gt;' in result

    def test_strips_event_handler_attribute(self):
        result = sanitize_rich_text('<p onclick="alert(1)">Click</p>')
        assert 'onclick' not in result

    def test_strips_javascript_href(self):
        result = sanitize_rich_text('<a href="javascript:alert(1)">link</a>')
        assert 'javascript:' not in result

    def test_allows_safe_href(self):
        result = sanitize_rich_text('<a href="https://dogboxx.org">link</a>')
        assert 'href="https://dogboxx.org"' in result

    def test_preserves_color_style_via_css_sanitizer(self):
        html = '<span style="color: rgb(230, 0, 0);">red text</span>'
        result = sanitize_rich_text(html)
        assert 'color: rgb(230, 0, 0)' in result or 'color:rgb(230, 0, 0)' in result

    def test_strips_disallowed_style_property(self):
        html = '<span style="color: red; position: fixed; top: 0;">x</span>'
        result = sanitize_rich_text(html)
        assert 'position' not in result
        assert 'color' in result

    def test_none_input_returns_empty_string(self):
        assert sanitize_rich_text(None) == ''

    def test_target_blank_link_gets_noopener_noreferrer(self):
        """L28: a target link without rel must not rely on the author to set it."""
        result = sanitize_rich_text('<a href="https://example.com" target="_blank">link</a>')
        assert 'rel="noopener noreferrer"' in result

    def test_target_blank_link_with_other_rel_is_overridden(self):
        """An author-supplied rel that omits noopener must still be forced."""
        result = sanitize_rich_text(
            '<a href="https://example.com" target="_blank" rel="nofollow">link</a>'
        )
        assert 'rel="noopener noreferrer"' in result
        assert 'nofollow' not in result

    def test_link_without_target_is_unaffected(self):
        result = sanitize_rich_text('<a href="https://example.com">link</a>')
        assert 'rel=' not in result


class TestCleanRichTextOrNone:

    def test_empty_quill_markup_collapses_to_none(self):
        assert clean_rich_text_or_none('<p><br></p>') is None

    def test_whitespace_only_collapses_to_none(self):
        assert clean_rich_text_or_none('   ') is None

    def test_none_input_returns_none(self):
        assert clean_rich_text_or_none(None) is None

    def test_real_content_is_preserved(self):
        result = clean_rich_text_or_none('<p>Key in the porch</p>')
        assert result == '<p>Key in the porch</p>'

    def test_plain_text_without_tags_is_preserved(self):
        assert clean_rich_text_or_none('Key in the porch') == 'Key in the porch'


class TestRichTextToPlain:

    def test_strips_tags_and_joins_paragraphs_with_space(self):
        html = '<p>Hi {{firstname}},</p><p>Walks <strong>cancelled</strong> today.</p>'
        assert rich_text_to_plain(html) == 'Hi {{firstname}}, Walks cancelled today.'

    def test_list_items_separated_by_space(self):
        html = '<h1>Heads up</h1><ul><li>Rain</li><li>Delay</li></ul>'
        assert rich_text_to_plain(html) == 'Heads up Rain Delay'

    def test_br_becomes_a_space(self):
        assert rich_text_to_plain('Line one<br>Line two') == 'Line one Line two'

    def test_none_input_returns_empty_string(self):
        assert rich_text_to_plain(None) == ''

    def test_plain_text_passes_through(self):
        assert rich_text_to_plain('Walks cancelled — heavy rain.') == \
            'Walks cancelled — heavy rain.'

    def test_unescapes_entities_from_sanitize_rich_text_output(self):
        """rich_text_to_plain's input is always sanitize_rich_text's output,
        which HTML-escapes special characters (e.g. "&amp;") — the plain-text
        summary should read as genuine plain text, not leftover HTML source."""
        sanitized = sanitize_rich_text('<p>Fish &amp; Chips</p>')
        assert rich_text_to_plain(sanitized) == 'Fish & Chips'
