/**
 * walk-note-modal.js — the shared "Walk note" modal (partials/walk_note_modal.html).
 *
 * Edits a booking's client_notes via POST /admin/booking/<id>/note. Used by
 * the assignment board (admin-board-core.js) and Week view.
 *
 *   WalkNoteModal.open({
 *       bookingId: 123,
 *       subtitle:  'Rex · morning walk · Mon 19 Oct',
 *       note:      'current note text or empty',
 *       onSaved:   note => { ... },   // note is the saved text, or null when cleared
 *       onError:   message => { ... },
 *   });
 *
 * The modal element is looked up on each open: the dashboard's Assign modal
 * re-injects the board fragment (and this modal with it) every time it opens,
 * so a reference cached at load time would point at a detached element.
 */
(function (global) {
    'use strict';

    let el = null;
    let current = null;     // the open() options for the note being edited
    let parts = null;       // { modal, text, count, save, subtitle }

    function updateCount() {
        parts.count.textContent = `${parts.text.value.length}/${parts.text.maxLength}`;
    }

    async function save() {
        if (!current) return;
        const opts = current;
        parts.save.disabled = true;
        try {
            const res = await fetch(`/admin/booking/${opts.bookingId}/note`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content,
                },
                body: JSON.stringify({ note: parts.text.value }),
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.message || 'Could not save note');
            current = null;
            parts.modal.hide();
            if (opts.onSaved) opts.onSaved(data.note);
        } catch (err) {
            console.error(err);
            parts.save.disabled = false;
            if (opts.onError) opts.onError(err.message || 'Could not save note — please try again.');
        }
    }

    // Bind to the modal element currently in the page (see header comment).
    function bind() {
        const found = document.getElementById('walkNoteModal');
        if (!found) throw new Error('walkNoteModal is not in the page');
        if (found === el) return;
        el = found;
        parts = {
            modal:    bootstrap.Modal.getOrCreateInstance(el),
            text:     el.querySelector('#walk-note-text'),
            count:    el.querySelector('#walk-note-count'),
            save:     el.querySelector('#walk-note-save-btn'),
            subtitle: el.querySelector('#walk-note-subtitle'),
        };
        parts.text.addEventListener('input', updateCount);
        parts.save.addEventListener('click', save);
        el.addEventListener('shown.bs.modal', () => parts.text.focus());
    }

    function open(opts) {
        bind();
        current = opts;
        parts.subtitle.textContent = opts.subtitle || '';
        parts.text.value = opts.note || '';
        updateCount();
        parts.save.disabled = false;
        parts.modal.show();
    }

    global.WalkNoteModal = { open };
}(window));
