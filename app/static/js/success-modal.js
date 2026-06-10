/**
 * Shared success pop-over (stacked confirm modal). See docs/UX_GUIDE.md §1.
 *
 * Requires partials/success_modal.html (#successModal) on the page. On success,
 * dismiss the source modal and pop a green-check "Success" modal with a summary,
 * auto-closing after 2.5s. A "Done" button is the manual fallback.
 *
 * showConfirmed(summary, srcModal, srcEl[, onClose])
 *   summary  – text shown under the heading
 *   srcModal – the bootstrap.Modal instance to hide first
 *   srcEl    – its DOM element (we wait for its hidden.bs.modal before stacking)
 *   onClose  – optional callback fired once when the success modal closes
 *              (covers both auto-close and manual Done) — e.g. location.reload
 */
(function () {
    let confirmModal   = null;
    let confirmSummary = null;
    let confirmTimer   = null;
    let pendingOnClose = null;

    function ensureRefs() {
        if (confirmModal) return;
        const el = document.getElementById('successModal');
        confirmModal   = bootstrap.Modal.getOrCreateInstance(el);
        confirmSummary = document.getElementById('success-summary');
        // Fires on both auto-close and manual "Done". Clear the timer (so a
        // pending auto-close can't double-fire) and run the one-shot callback.
        el.addEventListener('hidden.bs.modal', function () {
            clearTimeout(confirmTimer);
            const cb = pendingOnClose;
            pendingOnClose = null;
            if (typeof cb === 'function') cb();
        });
    }

    window.showConfirmed = function (summary, srcModal, srcEl, onClose) {
        ensureRefs();
        confirmSummary.textContent = summary;
        pendingOnClose = onClose || null;
        // Wait for the source modal to fully close before stacking the success
        // modal — avoids Bootstrap backdrop/scroll-lock conflicts.
        srcEl.addEventListener('hidden.bs.modal', function handler() {
            srcEl.removeEventListener('hidden.bs.modal', handler);
            confirmModal.show();
            clearTimeout(confirmTimer);
            confirmTimer = setTimeout(() => confirmModal.hide(), 2500);
        });
        srcModal.hide();
    };
})();
