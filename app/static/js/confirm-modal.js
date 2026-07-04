/**
 * Shared confirm modal — replaces native confirm() dialogs. See
 * partials/confirm_modal.html and docs/CODE_REVIEW_2026-07.md #15.
 *
 * Two ways to use it:
 *
 * 1. Programmatic (for confirms that guard a fetch() call):
 *      showConfirm('Remove this unavailability?', function () {
 *          // fires only if the user confirms
 *      }, { confirmLabel: 'Remove' });
 *
 * 2. Declarative (for confirms that guard a plain <form> submit):
 *      <form method="post" data-confirm="Delete this message?" data-confirm-label="Delete">
 *      No JS needed — a page-wide delegated listener intercepts the submit,
 *      shows the modal, and re-submits the form if confirmed.
 */
(function () {
    let modalEl, modal, iconEl, titleEl, bodyEl, confirmBtn;
    let pendingAction = null;
    let pendingCancel = null;
    let confirmed = false;

    function ensureRefs() {
        if (modal) return;
        modalEl = document.getElementById('dbConfirmModal');
        modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        iconEl = document.getElementById('db-confirm-icon');
        titleEl = document.getElementById('db-confirm-title');
        bodyEl = document.getElementById('db-confirm-body');
        confirmBtn = document.getElementById('db-confirm-btn');
        confirmBtn.addEventListener('click', function () {
            confirmed = true;
            modal.hide();
        });
        // Fires on both the confirm button (after the flag above) and Cancel /
        // Esc / backdrop click — only one of onConfirm/onCancel ever runs.
        modalEl.addEventListener('hidden.bs.modal', function () {
            const action = confirmed ? pendingAction : pendingCancel;
            pendingAction = null;
            pendingCancel = null;
            confirmed = false;
            if (typeof action === 'function') action();
        });
    }

    window.showConfirm = function (message, onConfirm, opts) {
        opts = opts || {};
        ensureRefs();
        const danger = opts.danger !== false;
        titleEl.textContent = opts.title || 'Are you sure?';
        bodyEl.textContent = message || '';
        confirmBtn.textContent = opts.confirmLabel || 'Confirm';
        confirmBtn.className = 'btn btn-sm px-4 ' + (danger ? 'btn-danger' : 'btn-primary');
        iconEl.className = 'bi ' + (opts.icon || (danger ? 'bi-trash' : 'bi-question-circle')) +
            ' ' + (danger ? 'text-danger' : 'text-primary') + ' mb-2';
        pendingAction = onConfirm;
        pendingCancel = opts.onCancel || null;
        modal.show();
    };

    // Delegated handler for <form data-confirm="..."> — lets a plain POST form
    // opt into the modal without any per-page JS. The re-submit sets a flag so
    // this same listener lets the second, confirmed submit through untouched.
    document.addEventListener('submit', function (e) {
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;
        const msg = form.dataset.confirm;
        if (!msg || form.dataset.confirmBypass === 'true') return;
        e.preventDefault();
        window.showConfirm(msg, function () {
            form.dataset.confirmBypass = 'true';
            (form.requestSubmit ? form.requestSubmit() : form.submit());
            delete form.dataset.confirmBypass;
        }, { confirmLabel: form.dataset.confirmLabel });
    });
}());
