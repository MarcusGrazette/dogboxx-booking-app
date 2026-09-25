/**
 * pickupQuillToolbar(el)
 * Toolbar config for the client-side pickup-notes Quill editors (/profile,
 * /onboard). Quill only reads its toolbar at construction, so the set is
 * chosen once from the editor's width at load: the full toolbar needs ~415px,
 * below NARROW_PX it wraps (phones, and /profile's two-column tablet layout)
 * so the minimal set drops the header picker, blockquote and clear-formatting.
 * No live switch on resize/rotation — the choice holds until reload.
 * Formatting from the full set still renders and edits in the minimal one.
 */
(function () {
    var NARROW_PX = 440;

    var FULL = [
        [{ header: [1, 2, 3, false] }],
        ['bold', 'italic', 'underline'],
        [{ list: 'ordered' }, { list: 'bullet' }],
        ['blockquote', 'link'],
        ['clean'],
    ];

    var MINIMAL = [
        ['bold', 'italic', 'underline'],
        [{ list: 'ordered' }, { list: 'bullet' }],
        ['link'],
    ];

    window.pickupQuillToolbar = function (el) {
        // An editor that isn't laid out yet reports 0 — fall back to the viewport.
        var width = el.parentElement.clientWidth || window.innerWidth;
        return width < NARROW_PX ? MINIMAL : FULL;
    };
}());
