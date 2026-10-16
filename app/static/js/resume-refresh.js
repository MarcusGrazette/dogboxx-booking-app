/**
 * Background refresh on resume.
 *
 * A PWA (or a browser tab) switched away from is frozen in memory, and on
 * return shows the page exactly as it was — a walker can come back hours
 * later to a pickup list that no longer matches the board. The service
 * worker's network-first HTML strategy can't help: no request is made.
 *
 * So: when the page has been hidden for AWAY_MS or longer, reload it on
 * return to the foreground. One rule for installed PWAs and ordinary tabs.
 *
 * Skipped (no reload) when:
 *   - a Bootstrap modal is open — same rule as pull-to-refresh
 *   - an editable field has focus (input, textarea, select, Quill editor)
 *   - the device is offline — the reload would only serve the cached copy
 *
 * Reloads via location.replace() rather than reload(): a page rendered as a
 * POST response would otherwise prompt to resubmit the form.
 */
(function () {
    var AWAY_MS = 60 * 60 * 1000;
    var hiddenAt = null;

    function isEditing() {
        var el = document.activeElement;
        if (!el || el === document.body) return false;
        if (el.isContentEditable) return true;
        var tag = el.tagName;
        return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
    }

    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') {
            hiddenAt = Date.now();
            return;
        }
        if (hiddenAt === null) return;
        var away = Date.now() - hiddenAt;
        hiddenAt = null;
        if (away < AWAY_MS) return;
        if (document.body.classList.contains('modal-open')) return;
        if (isEditing()) return;
        if (navigator.onLine === false) return;
        window.location.replace(window.location.href.split('#')[0]);
    });
}());
