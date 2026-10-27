/**
 * Super-admin client & dog deletion, on the admin client detail page
 * (FEATURES #100). Dog-first:
 *
 *   "Delete" on a dog card → GET the preview. Upcoming bookings → list them
 *   and stop. Otherwise step 1: one unticked box per owner (greyed, with the
 *   reason, when they can't be deleted) → step 2: "This is permanent", type
 *   the dog's name → POST. A client with no dogs gets "Delete client" →
 *   type their name → POST.
 *
 * The server repeats every check; the greyed boxes are only a hint. Names are
 * written with textContent, never innerHTML.
 */
(function () {
    const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function namesMatch(typed, expected) {
        return typed.trim().toLowerCase() === expected.trim().toLowerCase();
    }

    function setBusy(btn, busy, label, icon) {
        btn.disabled = busy;
        btn.innerHTML = busy
            ? '<span class="spinner-border spinner-border-sm me-1"></span>Deleting…'
            : '<i class="bi ' + icon + ' me-1"></i>';
        if (!busy) btn.appendChild(document.createTextNode(label));
    }

    function postJson(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify(body),
        }).then(r => r.json());
    }

    function showError(box, msg) {
        box.textContent = msg;
        box.classList.remove('d-none');
    }

    // ── Delete dog ──────────────────────────────────────────────────────────
    const dogModalEl = document.getElementById('deleteDogModal');
    if (dogModalEl) {
        const dogModal = bootstrap.Modal.getOrCreateInstance(dogModalEl);
        const title = document.getElementById('dd-title');
        const panes = {
            loading: document.getElementById('dd-loading'),
            blocked: document.getElementById('dd-blocked'),
            owners: document.getElementById('dd-owners'),
            confirm: document.getElementById('dd-confirm'),
        };
        const blockedList = document.getElementById('dd-blocked-list');
        const ownerList = document.getElementById('dd-owner-list');
        const summary = document.getElementById('dd-summary');
        const typedInput = document.getElementById('dd-typed-name');
        const typedLabel = document.getElementById('dd-typed-label');
        const errorBox = document.getElementById('dd-error');
        const actionBtn = document.getElementById('dd-action-btn');

        let state = null;  // {dogId, dogName, deleteUrl, step}

        function show(pane) {
            Object.keys(panes).forEach(k => panes[k].classList.toggle('d-none', k !== pane));
        }

        function setAction(step) {
            state.step = step;
            if (step === 'confirm') {
                setBusy(actionBtn, false, 'Delete permanently', 'bi-trash');
                actionBtn.className = 'btn btn-danger fw-semibold';
                actionBtn.disabled = !namesMatch(typedInput.value, state.dogName);
            } else {
                actionBtn.className = 'btn btn-primary fw-semibold';
                actionBtn.innerHTML = 'Continue';
                actionBtn.disabled = step !== 'owners';
            }
        }

        function tickedOwners() {
            return Array.from(ownerList.querySelectorAll('input[type=checkbox]:checked'))
                .map(cb => ({ id: parseInt(cb.value, 10), name: cb.dataset.name }));
        }

        function renderOwners(owners) {
            ownerList.replaceChildren();
            owners.forEach(o => {
                const wrap = el('div', 'form-check mb-2');
                const cb = el('input', 'form-check-input');
                cb.type = 'checkbox';
                cb.id = 'dd-owner-' + o.id;
                cb.value = o.id;
                cb.dataset.name = o.name;
                cb.disabled = !o.can_delete;
                const label = el('label', 'form-check-label' + (o.can_delete ? '' : ' text-muted'));
                label.htmlFor = cb.id;
                label.appendChild(el('span', '', 'Also delete ' + o.name + ' from the client list'));
                if (o.role === 'secondary') label.appendChild(el('span', 'text-muted small ms-1', '(co-owner)'));
                if (!o.can_delete) label.appendChild(el('div', 'small', o.reason));
                wrap.append(cb, label);
                ownerList.appendChild(wrap);
            });
            if (!owners.length) ownerList.appendChild(el('p', 'text-muted small mb-0', 'This dog has no owners on record.'));
        }

        function renderSummary() {
            const people = tickedOwners().map(o => o.name);
            summary.replaceChildren();
            summary.appendChild(el('p', 'mb-2',
                "This can't be undone. " + state.dogName + "'s details, photos and walk notes will be erased" +
                (people.length ? ', along with the personal details of ' + people.join(' and ') +
                    ', who will no longer be able to log in.' : '.')));
            summary.appendChild(el('p', 'mb-0 text-muted small',
                'Past walks and invoices stay, shown as [Deleted dog]' +
                (people.length ? ' / [Deleted client]' : '') + '.'));
        }

        function open(btn) {
            state = { dogName: btn.dataset.dogName, previewUrl: btn.dataset.previewUrl, deleteUrl: btn.dataset.deleteUrl };
            title.textContent = 'Delete ' + state.dogName + '?';
            errorBox.classList.add('d-none');
            typedInput.value = '';
            typedLabel.textContent = 'Type ' + state.dogName + ' to confirm';
            show('loading');
            setAction('loading');
            dogModal.show();

            fetch(state.previewUrl, { headers: { 'Accept': 'application/json' } })
                .then(r => r.json())
                .then(data => {
                    if (!data.success) {
                        show(null);
                        showError(errorBox, data.message || 'Could not load the dog.');
                        return;
                    }
                    state.dogName = data.dog.name;
                    if (data.future_bookings.length) {
                        blockedList.replaceChildren();
                        data.future_bookings.forEach(b => {
                            blockedList.appendChild(el('li', 'list-group-item px-0 small',
                                b.date + ' · ' + b.slot + ' · ' + b.service + ' (' + b.status + ')'));
                        });
                        show('blocked');
                        setAction('blocked');
                        return;
                    }
                    renderOwners(data.owners);
                    show('owners');
                    setAction('owners');
                })
                .catch(() => { show(null); showError(errorBox, 'Network error — please try again.'); });
        }

        document.querySelectorAll('.js-delete-dog').forEach(btn => {
            btn.addEventListener('click', function () { open(this); });
        });

        typedInput.addEventListener('input', () => {
            if (state && state.step === 'confirm') {
                actionBtn.disabled = !namesMatch(typedInput.value, state.dogName);
            }
        });

        actionBtn.addEventListener('click', () => {
            if (!state) return;
            errorBox.classList.add('d-none');
            if (state.step === 'owners') {
                renderSummary();
                show('confirm');
                setAction('confirm');
                typedInput.focus();
                return;
            }
            if (state.step !== 'confirm' || !namesMatch(typedInput.value, state.dogName)) return;
            setBusy(actionBtn, true);
            postJson(state.deleteUrl, {
                owner_ids: tickedOwners().map(o => o.id),
                typed_name: typedInput.value,
            })
                .then(data => {
                    if (data.success) {
                        showConfirmed(data.message, dogModal, dogModalEl, () => window.location.reload());
                    } else {
                        setAction('confirm');
                        showError(errorBox, data.message || 'Could not delete.');
                    }
                })
                .catch(() => { setAction('confirm'); showError(errorBox, 'Network error — please try again.'); });
        });
    }

    // ── Delete client (no dogs) ─────────────────────────────────────────────
    const clientModalEl = document.getElementById('deleteClientModal');
    const clientBtn = document.querySelector('.js-delete-client');
    if (clientModalEl && clientBtn) {
        const clientModal = bootstrap.Modal.getOrCreateInstance(clientModalEl);
        const input = document.getElementById('dc-typed-name');
        const errorBox = document.getElementById('dc-error');
        const actionBtn = document.getElementById('dc-action-btn');
        const name = clientBtn.dataset.clientName;

        clientBtn.addEventListener('click', () => {
            input.value = '';
            errorBox.classList.add('d-none');
            setBusy(actionBtn, false, 'Delete permanently', 'bi-trash');
            actionBtn.disabled = true;
            clientModal.show();
        });
        input.addEventListener('input', () => { actionBtn.disabled = !namesMatch(input.value, name); });
        actionBtn.addEventListener('click', () => {
            if (!namesMatch(input.value, name)) return;
            errorBox.classList.add('d-none');
            setBusy(actionBtn, true);
            postJson(clientBtn.dataset.deleteUrl, { typed_name: input.value })
                .then(data => {
                    if (data.success) {
                        showConfirmed(data.message, clientModal, clientModalEl, () => window.location.reload());
                    } else {
                        setBusy(actionBtn, false, 'Delete permanently', 'bi-trash');
                        showError(errorBox, data.message || 'Could not delete.');
                    }
                })
                .catch(() => {
                    setBusy(actionBtn, false, 'Delete permanently', 'bi-trash');
                    showError(errorBox, 'Network error — please try again.');
                });
        });
    }
})();
