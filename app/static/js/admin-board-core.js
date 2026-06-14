/**
 * admin-board-core.js — calendar-agnostic assignment board component.
 *
 * Usage:
 *   const board = createBoard(config);
 *   board.setDate('2026-05-19');   // load a date
 *
 * The caller is responsible for picking a date (via its own calendar UI,
 * a URL param, or anything else). The Board does not own date-selection.
 *
 * config shape:
 *   assignUrl              {string}   URL for admin.assign_walker POST
 *   reorderUrl             {string}   URL for admin.reorder_pickups POST
 *   boardDataUrl           {string}   URL with 'DATE' placeholder
 *   emptyIcon              {string}   bi-* class for empty-board icon
 *   emptyText              {string}   Text for empty-board state
 *   declineLabel           {string}   'walk' | 'drop-in' in decline modal copy
 *   makePendingCardBadges  {fn(b)}    Returns extra badge HTML for pending cards
 *   makeAssignedCardBadges {fn(b)}    Returns extra content HTML for assigned cards
 *   initialDate            {string?}  ISO date to load immediately, optional
 *   onDateSelect           {fn(date)} Called after every successful setDate()
 *   onAfterAssign          {fn(date)} Called after a successful assign
 *   onAfterUnassign        {fn(date)} Called after a successful unassign
 */
(function (global) {
    'use strict';

    function createBoard(cfg) {
        const CSRF        = document.querySelector('meta[name="csrf-token"]')?.content;
        const DECLINE_URL = id => `/admin/booking/${id}/decline`;

        // ── State ─────────────────────────────────────────────────────────────
        const state = {
            date:       null,
            selectedId: null,
            pending:    [],
            assigned:   [],
            walkers:    [],
            maxCap:     null,
        };

        // ── Helpers ───────────────────────────────────────────────────────────
        function imgSrc(pic) {
            return pic
                ? `/static/uploads/dogs/${pic}`
                : '/static/uploads/dogs/default-dog.png';
        }

        function escHtml(s) {
            return String(s)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        }

        function getAssignedForLane(walkerId, slot) {
            return state.assigned
                .filter(b => b.walker_id === walkerId && b.slot === slot)
                .sort((a, b) => (a.pickup_order || 999) - (b.pickup_order || 999));
        }

        function getLaneCapacity(walkerId, slot) {
            return getAssignedForLane(walkerId, slot).length;
        }

        function findBooking(id) {
            return state.pending.find(b => b.id === id)
                || state.assigned.find(b => b.id === id);
        }

        function showToast(msg, type) {
            const el = document.createElement('div');
            el.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
            el.style.cssText = 'top:20px;right:20px;z-index:1055;max-width:380px;';
            el.innerHTML = `${msg}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
            document.body.appendChild(el);
            setTimeout(() => el.parentNode?.removeChild(el), 4000);
        }

        // ── Card rendering ────────────────────────────────────────────────────
        function makePendingCard(b) {
            const el = document.createElement('div');
            el.className = 'board-card'
                + (b.status === 'waitlisted' ? ' card-waitlisted' : '')
                + (state.selectedId === b.id  ? ' card-selected'  : '');
            el.dataset.id = b.id;

            const slotColor = b.slot === 'Morning' ? 'success' : 'danger';
            const extraBadges = cfg.makePendingCardBadges ? cfg.makePendingCardBadges(b) : '';
            el.innerHTML = `
                <img src="${imgSrc(b.dog_pic)}" class="board-dog-img"
                     onerror="this.src='/static/uploads/dogs/default-dog.png'">
                <div class="board-card-info">
                    <div class="board-card-name">${b.dog_name}</div>
                    <div class="board-card-owner">${b.owner_name}</div>
                </div>
                <div class="d-flex gap-1 align-items-center flex-shrink-0">
                    ${b.status === 'waitlisted' ? '<span class="modifier-pill modifier-waitlisted" title="Waitlisted"><i class="bi bi-clock-history"></i></span>' : ''}
                    ${extraBadges}
                    <span class="slot-pill bg-${slotColor} bg-opacity-10 text-${slotColor} border border-${slotColor}">${b.slot === 'Morning' ? 'AM' : 'PM'}</span>
                    <button class="btn btn-link p-0 ms-1 text-muted decline-btn"
                            data-id="${b.id}" data-dog="${escHtml(b.dog_name)}" data-slot="${b.slot}"
                            title="Decline booking"
                            style="font-size:0.9rem; opacity:0.55; line-height:1;"
                            onclick="event.stopPropagation(); openDeclineModal(this)">
                        <i class="bi bi-x-circle"></i>
                    </button>
                </div>`;

            el.addEventListener('click', () => selectBooking(b.id));
            return el;
        }

        function makeAssignedCard(b) {
            const el = document.createElement('div');
            el.className = 'board-card' + (state.selectedId === b.id ? ' card-selected' : '');
            el.dataset.id = b.id;

            const extraContent = cfg.makeAssignedCardBadges ? cfg.makeAssignedCardBadges(b) : '';
            el.innerHTML = `
                <span class="drag-handle bi bi-grip-vertical"></span>
                <img src="${imgSrc(b.dog_pic)}" class="board-dog-img"
                     onerror="this.src='/static/uploads/dogs/default-dog.png'">
                <div class="board-card-info">
                    <div class="board-card-name">${b.dog_name}</div>
                    <div class="board-card-owner">${b.owner_name}</div>
                </div>
                ${extraContent}`;

            el.addEventListener('click', () => selectBooking(b.id));
            return el;
        }

        // ── Render ────────────────────────────────────────────────────────────
        function render() {
            const wrap        = document.getElementById('board-columns');
            const placeholder = document.getElementById('board-placeholder');
            wrap.innerHTML = '';

            if (!state.walkers.length && !state.pending.length && !state.assigned.length) {
                placeholder.style.display = '';
                wrap.style.display = 'none';
                placeholder.innerHTML =
                    `<i class="bi ${cfg.emptyIcon} fs-3 d-block mb-2 text-muted"></i>${cfg.emptyText}`;
                return;
            }
            placeholder.style.display = 'none';
            wrap.style.display = 'flex';

            wrap.appendChild(makePendingColumn());
            state.walkers.forEach(w => {
                const col = makeWalkerColumn(w);
                if (col) wrap.appendChild(col);
            });

            document.querySelectorAll('.sortable-lane').forEach(el => {
                const { walkerId, slot } = el.dataset;
                Sortable.create(el, {
                    animation: 150,
                    handle: '.drag-handle',
                    ghostClass: 'sortable-ghost',
                    chosenClass: 'sortable-chosen',
                    onEnd() {
                        const ids = [...el.querySelectorAll('.board-card')]
                            .map(c => parseInt(c.dataset.id));
                        reorderLane(parseInt(walkerId), slot, ids);
                    }
                });
            });

            updateSelectionUI();
        }

        function makePendingColumn() {
            const col = document.createElement('div');
            col.className = 'board-column pending-column';

            const header = document.createElement('div');
            header.className = 'board-col-header pending-header';
            header.innerHTML = `<span><i class="bi bi-hourglass me-1"></i>Requested</span>
                                <span class="badge bg-white text-dark">${state.pending.length}</span>`;
            col.appendChild(header);

            ['Morning', 'Afternoon'].forEach(slot => {
                const slotPending = state.pending.filter(b => b.slot === slot);
                if (!state.pending.length || !slotPending.length) return;
                const lane = makeLane(null, slot, false);
                slotPending.forEach(b =>
                    lane.querySelector('.sortable-lane').appendChild(makePendingCard(b)));
                col.appendChild(lane);
            });

            if (!state.pending.length) {
                const empty = document.createElement('div');
                empty.className = 'text-muted text-center py-3 small';
                empty.innerHTML = '<i class="bi bi-check-circle text-success"></i> All assigned';
                col.appendChild(empty);
            }

            return col;
        }

        function makeWalkerColumn(walker) {
            const unavailSlots = walker.unavailable_slots || [];
            const availSlots   = walker.available_slots || [];

            // Hide the column when the walker has no actually-available slots
            // today — either because they're not scheduled at all, or because
            // every scheduled/ad-hoc slot has been overridden as unavailable.
            // (The backend computes available_slots as scheduled+adhoc MINUS
            // unavailable, so length === 0 catches both cases.)
            if (availSlots.length === 0) {
                return null;
            }

            const col = document.createElement('div');
            col.className = 'board-column';

            const header = document.createElement('div');
            header.className = 'board-col-header';
            // Partial unavailability — name the slot so the admin can see at a
            // glance that the walker is only available for one of their slots.
            const unavailBadge = unavailSlots.length > 0
                ? `<span class="badge bg-warning text-dark ms-1"
                        title="Marked unavailable — admin override active">
                        <i class="bi bi-exclamation-triangle-fill me-1"></i>Unavailable ${unavailSlots.join(', ')}</span>`
                : '';
            header.innerHTML = `<span><i class="bi bi-person-walking me-1"></i>${walker.name}</span>`
                + unavailBadge;
            col.appendChild(header);

            // Iterate the canonical slot order rather than available_slots so
            // every walker column has both Morning and Afternoon rows in the
            // same vertical position. Walkers not scheduled for a slot get a
            // non-interactive placeholder so their PM card can't visually
            // align with another walker's AM card (or vice versa).
            ['Morning', 'Afternoon'].forEach(slot => {
                if (availSlots.includes(slot)) {
                    const isUnavail = unavailSlots.includes(slot);
                    const lane = makeLane(walker.id, slot, true, isUnavail);
                    getAssignedForLane(walker.id, slot).forEach(b =>
                        lane.querySelector('.sortable-lane').appendChild(makeAssignedCard(b)));
                    col.appendChild(lane);
                } else {
                    col.appendChild(makeNotScheduledLane(slot));
                }
            });

            return col;
        }

        function makeNotScheduledLane(slot) {
            // Placeholder for a slot the walker isn't scheduled for today.
            // Not droppable, no walkerId dataset (so Sortable.create skips it),
            // no click handler so an assignment can't drop here. Visually
            // distinct from `lane-unavailable` (admin-override yellow) — this
            // grey signals "not on the schedule" rather than "blocked".
            const wrap = document.createElement('div');
            wrap.className = 'board-lane lane-not-scheduled';

            const hdr = document.createElement('div');
            hdr.className = 'lane-header';
            hdr.innerHTML = `<span class="lane-title text-muted">${slot}</span>`;
            wrap.appendChild(hdr);

            const body = document.createElement('div');
            body.className = 'lane-not-scheduled-body';
            body.textContent = 'Unavailable';
            wrap.appendChild(body);

            return wrap;
        }

        function makeLane(walkerId, slot, isWalkerLane, isUnavail = false) {
            const cap      = walkerId ? getLaneCapacity(walkerId, slot) : null;
            const isFull   = cap !== null && cap >= state.maxCap;
            const isTarget = state.selectedId !== null && isWalkerLane && !isFull;

            const wrap = document.createElement('div');
            wrap.className = 'board-lane'
                + (isFull    ? ' lane-full'        : '')
                + (isTarget  ? ' lane-target'      : '')
                + (isUnavail ? ' lane-unavailable' : '');

            const hdr = document.createElement('div');
            hdr.className = 'lane-header' + (isUnavail ? ' lane-header-unavailable' : '');
            hdr.innerHTML = `
                <span class="lane-title">${slot}${isUnavail
                    ? ' <i class="bi bi-exclamation-triangle-fill text-warning ms-1" title="Walker unavailable — override active"></i>'
                    : ''}</span>
                ${cap !== null
                    ? `<span class="capacity-badge${isFull ? ' cap-full' : ''}">${cap}/${state.maxCap}</span>`
                    : ''}`;
            wrap.appendChild(hdr);

            const cardsEl = document.createElement('div');
            cardsEl.className = 'sortable-lane';
            if (walkerId) {
                cardsEl.dataset.walkerId = walkerId;
                cardsEl.dataset.slot     = slot;
            }
            wrap.appendChild(cardsEl);

            if (isWalkerLane && cap === 0) {
                const hint = document.createElement('div');
                hint.className  = 'lane-empty';
                hint.textContent = 'Empty';
                cardsEl.appendChild(hint);
            }

            if (isWalkerLane && walkerId) {
                wrap.addEventListener('click', e => {
                    if (e.target.closest('.board-card')) return;
                    if (state.selectedId && !isFull) assignCard(state.selectedId, walkerId, slot);
                });
            }

            return wrap;
        }

        // ── Selection ─────────────────────────────────────────────────────────
        function selectBooking(id) {
            if (state.selectedId === id) {
                const booking = findBooking(id);
                if (booking && booking.walker_id) { unassignCard(id); return; }
                state.selectedId = null;
            } else {
                state.selectedId = id;
            }
            render();
            updateSelectionUI();
        }

        function updateSelectionUI() {
            const hint       = document.getElementById('selection-hint');
            const hintText   = document.getElementById('hint-text');
            const hintIcon   = document.getElementById('hint-icon');
            const cancelBtn  = document.getElementById('hint-deselect');
            if (state.selectedId) {
                const b = findBooking(state.selectedId);
                // Active state — brand pink
                hint.style.background   = '#fce8f6';
                hint.style.borderColor  = '#C0258F';
                hint.style.color        = '#7a0057';
                hintIcon.className      = 'bi bi-cursor-fill';
                hintIcon.style.color    = '#C0258F';
                hintText.textContent    = b
                    ? `${b.dog_name} selected — click a walker's ${b.slot} slot to assign${b.walker_id ? ', or click again to unassign' : ''}`
                    : 'Click a walker slot to assign';
                cancelBtn.classList.remove('d-none');
            } else {
                // Idle state — neutral grey
                hint.style.background   = '#f8f9fa';
                hint.style.borderColor  = '#dee2e6';
                hint.style.color        = '#6c757d';
                hintIcon.className      = 'bi bi-cursor';
                hintIcon.style.color    = '';
                hintText.textContent    = 'Select a dog to assign';
                cancelBtn.classList.add('d-none');
            }
        }

        document.getElementById('hint-deselect').addEventListener('click', () => {
            state.selectedId = null;
            render();
            updateSelectionUI();
        });

        // ── Slot override modal ───────────────────────────────────────────────
        let _slotOverride = null;
        const slotOverrideModal = new bootstrap.Modal(document.getElementById('slotOverrideModal'));

        document.getElementById('slot-override-confirm-btn').addEventListener('click', async () => {
            if (!_slotOverride) return;
            const { bookingId, walkerId, slot } = _slotOverride;
            _slotOverride = null;
            slotOverrideModal.hide();
            await doAssign(bookingId, walkerId, slot, true);
        });

        // ── API calls ──────────────────────────────────────────────────────────
        async function doAssign(bookingId, walkerId, slot, slotOverride = false) {
            const booking = findBooking(bookingId);
            if (!booking) return;

            const prev = JSON.parse(JSON.stringify(state));
            state.pending  = state.pending.filter(b => b.id !== bookingId);
            state.assigned = state.assigned.filter(b => b.id !== bookingId);
            booking.walker_id    = walkerId;
            booking.slot         = slot;
            booking.pickup_order = getLaneCapacity(walkerId, slot) + 1;
            state.assigned.push(booking);
            state.selectedId = null;
            render();

            try {
                const res  = await fetch(cfg.assignUrl, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
                    body:    JSON.stringify({ booking_id: bookingId, walker_id: walkerId, slot, slot_override: slotOverride }),
                });
                const data = await res.json();
                if (!data.success) throw new Error(data.message || 'Assignment failed');
                if (cfg.onAfterAssign) cfg.onAfterAssign(state.date);
                refreshPendingBadges();
            } catch (err) {
                console.error(err);
                Object.assign(state, prev);
                render();
                showToast(err.message || 'Could not assign — please try again.', 'danger');
            }
        }

        async function assignCard(bookingId, walkerId, slot) {
            const booking = findBooking(bookingId);
            if (!booking) return;

            if (booking.slot !== slot) {
                // Cross-slot assignment — confirm first
                _slotOverride = { bookingId, walkerId, slot };
                const dateLabel = state.date
                    ? new Date(state.date + 'T00:00:00').toLocaleDateString('en-GB', {
                        weekday: 'short', day: 'numeric', month: 'short'
                      })
                    : '';
                document.getElementById('slot-override-body').innerHTML =
                    `<strong>${escHtml(booking.dog_name)}</strong> was booked for `
                    + `<strong>${escHtml(booking.slot)}</strong> but you're assigning them to a `
                    + `<strong>${escHtml(slot)}</strong> slot on ${escHtml(dateLabel)}. `
                    + `The client will be notified of the change.`;
                slotOverrideModal.show();
                return;
            }

            await doAssign(bookingId, walkerId, slot);
        }

        async function unassignCard(bookingId) {
            const booking = findBooking(bookingId);
            if (!booking) return;

            const prev = JSON.parse(JSON.stringify(state));
            state.assigned       = state.assigned.filter(b => b.id !== bookingId);
            booking.walker_id    = null;
            booking.pickup_order = null;
            state.pending.push(booking);
            state.selectedId = null;
            render();

            try {
                const res  = await fetch(cfg.assignUrl, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
                    body:    JSON.stringify({ booking_id: bookingId, walker_id: null, slot: booking.slot }),
                });
                const data = await res.json();
                if (!data.success) throw new Error(data.message || 'Unassign failed');
                if (cfg.onAfterUnassign) cfg.onAfterUnassign(state.date);
                refreshPendingBadges();
            } catch (err) {
                console.error(err);
                Object.assign(state, prev);
                render();
                showToast(err.message || 'Could not unassign — please try again.', 'danger');
            }
        }

        async function reorderLane(walkerId, slot, newIds) {
            newIds.forEach((id, idx) => {
                const b = state.assigned.find(b => b.id === id);
                if (b) b.pickup_order = idx + 1;
            });
            try {
                await fetch(cfg.reorderUrl, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
                    body:    JSON.stringify({ walker_id: walkerId, slot, date: state.date, pickup_order: newIds }),
                });
            } catch (err) {
                console.error('Reorder failed:', err);
            }
        }

        // ── Board load ─────────────────────────────────────────────────────────
        async function loadBoard(dateStr) {
            state.date       = dateStr;
            state.selectedId = null;
            const placeholder = document.getElementById('board-placeholder');
            const columns     = document.getElementById('board-columns');
            placeholder.style.display = '';
            placeholder.innerHTML     = '<div class="spinner-border spinner-border-sm text-primary"></div>';
            columns.style.display     = 'none';

            try {
                const res  = await fetch(cfg.boardDataUrl.replace('DATE', dateStr));
                const data = await res.json();
                if (!data.success) throw new Error('Failed to load board');
                state.pending  = data.pending;
                state.assigned = data.assigned;
                state.walkers  = data.walkers;
                state.maxCap   = data.max_capacity;
                render();
            } catch (err) {
                console.error(err);
                placeholder.innerHTML = '<p class="text-danger">Could not load board. Please try again.</p>';
            }
        }

        // ── Decline ────────────────────────────────────────────────────────────
        let _declineBookingId = null;
        const declineModal    = new bootstrap.Modal(document.getElementById('declineModal'));

        function openDeclineModal(btn) {
            _declineBookingId = parseInt(btn.dataset.id, 10);
            const dog  = btn.dataset.dog;
            const slot = btn.dataset.slot;
            const date = state.date
                ? new Date(state.date + 'T00:00:00').toLocaleDateString('en-GB', {
                    weekday: 'short', day: 'numeric', month: 'short'
                  })
                : '';
            document.getElementById('decline-modal-body').textContent =
                `Decline ${dog}'s ${slot.toLowerCase()} ${cfg.declineLabel} on ${date}?`;
            declineModal.show();
        }

        // Expose for inline onclick attributes on dynamically generated cards.
        global.openDeclineModal = openDeclineModal;

        document.getElementById('decline-confirm-btn').addEventListener('click', async () => {
            if (!_declineBookingId) return;
            const id = _declineBookingId;
            declineModal.hide();
            try {
                const res  = await fetch(DECLINE_URL(id), {
                    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF }
                });
                const data = await res.json();
                if (!data.success) throw new Error(data.message || 'Could not decline');
                state.pending = state.pending.filter(b => b.id !== id);
                render();
                refreshPendingBadges();
                showToast('Booking declined.', 'success');
            } catch (err) {
                console.error(err);
                showToast(err.message || 'Could not decline — please try again.', 'danger');
            }
        });

        // ── Public API ─────────────────────────────────────────────────────────
        function setDate(dateStr) {
            loadBoard(dateStr);
            if (cfg.onDateSelect) cfg.onDateSelect(dateStr);
        }

        if (cfg.initialDate) setDate(cfg.initialDate);

        return { setDate };
    }

    global.createBoard = createBoard;

}(window));
