/* Client home page — booking form, calendar, cancel, notes, pause walks.
 * Server data (URLs, user info) injected via <script id="page-config" type="application/json">.
 */
(function () {
    const PAGE_CONFIG = JSON.parse(document.getElementById('page-config').textContent);
    BookingsList.configure({ dayBookingsUrl: PAGE_CONFIG.dayBookingsUrl });

    // ─── Greeting ────────────────────────────────────────────────
    (function () {
        const h = new Date().getHours();
        const greeting = h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
        const el = document.getElementById('greeting');
        if (el) el.textContent = greeting + ', ' + PAGE_CONFIG.userFirstname + '.';
    })();

    // 'error' doesn't bubble, so this has to be a capture-phase listener on
    // document rather than delegated the usual way; registered outside the
    // DOMContentLoaded block below (but still inside this IIFE, so it runs as
    // soon as this script tag parses) so it's live before any dog photo's
    // network fetch resolves — see the matching listener + comment in
    // admin-dogs.js for the same pattern.
    document.addEventListener('error', function (e) {
        if (e.target.matches && e.target.matches('.img-fallback-default-dog')) {
            e.target.onerror = null;
            e.target.src = e.target.dataset.fallback;
        }
    }, true);

    document.addEventListener('DOMContentLoaded', function () {

        // ─── State ───────────────────────────────────────────────
        const dateHidden = document.getElementById('booking-date-hidden');
        const slotSelect = document.getElementById('booking-slot');
        const slotBothOption = document.getElementById('slot-both-option');
        const availHint = document.getElementById('slot-availability');
        const submitBtn = document.getElementById('booking-submit');
        const dateText = document.getElementById('selected-date-text');
        const serviceTypeSelect = document.getElementById('service-type-select'); // null when no drop-in walkers
        const walkInfoNote = document.getElementById('walk-info-note');
        const dropinInfoNote = document.getElementById('dropin-info-note');

        const recurringToggle = document.getElementById('recurring-toggle');
        const recurPanel = document.getElementById('recurring-panel');
        const recurFreq = document.getElementById('recurring-frequency');
        const recurEnd = document.getElementById('recurring-end-date');
        const recurPreview = document.getElementById('recurring-preview');

        let slotData = null;
        let selectedDate = null;   // 'YYYY-MM-DD'

        // ─── Calendar ────────────────────────────────────────────
        // loadCalendarData uses getCalendar() rather than the outer `cal` variable
        // so it's safe to call during createCalendar's synchronous init (when cal
        // is still undefined) — getCalendar returns null and we bail early,
        // then the manual call below fires once cal is properly assigned.
        let closedDatesSet = new Set();

        window.loadCalendarData = function loadCalendarData(year, month) {
            const instance = getCalendar('client-booking-cal');
            if (!instance) return;
            const url = PAGE_CONFIG.calendarDataUrl.replace('/0/0', `/${year}/${month}`);
            fetch(url)
                .then(r => r.json())
                .then(data => {
                    closedDatesSet = new Set(data.closed_dates || []);
                    const allDates = data.success ? { ...data.dates } : {};
                    closedDatesSet.forEach(d => { allDates[d] = 'closed'; });
                    instance.setHighlightedDates(allDates);
                })
                .catch(() => { });
        }

        const cal = createCalendar('client-booking-cal', {
            onDateClick(year, month, day, dateStr) {
                if (closedDatesSet.has(dateStr)) return;
                selectedDate = dateStr;
                dateHidden.value = dateStr;

                const d = new Date(dateStr + 'T00:00:00');
                dateText.textContent = d.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

                updateSubmitState();
                fetchAvailability();
                updateRecurringEndMax();
                renderRecurPreview();
                BookingsList.applyDateFilter(dateStr);
            },
            onMonthChange(year, month) {
                loadCalendarData(year, month);
            }
        });

        // Initial data load — now that cal is assigned and registered
        loadCalendarData(cal.year, cal.month);

        // ─── Service type helper ─────────────────────────────────
        function getServiceType() {
            return serviceTypeSelect ? serviceTypeSelect.value : 'walk';
        }

        // ─── Service type change ─────────────────────────────────
        function onServiceTypeChange() {
            const isDropIn = getServiceType() === 'drop-in';

            // Remove/restore "Both walks" — hidden attribute doesn't work in iOS Safari
            if (isDropIn) {
                if (slotBothOption && slotBothOption.parentNode) slotSelect.removeChild(slotBothOption);
                if (slotSelect.value === 'Both') slotSelect.value = '';
            } else {
                if (slotBothOption && !slotBothOption.parentNode) slotSelect.appendChild(slotBothOption);
            }

            // Re-fetch availability for the selected service type
            fetchAvailability();

            // Toggle info notes
            if (walkInfoNote) walkInfoNote.style.display = isDropIn ? 'none' : '';
            if (dropinInfoNote) dropinInfoNote.style.display = isDropIn ? '' : 'none';

            updateSubmitState();
            renderRecurPreview();
        }

        if (serviceTypeSelect) serviceTypeSelect.addEventListener('change', onServiceTypeChange);

        // ─── Slot availability ───────────────────────────────────
        function fetchAvailability() {
            if (!selectedDate) {
                slotData = null; updateAvailabilityHint(); return;
            }
            const svc = getServiceType() === 'drop-in' ? 'drop-in' : 'walk';
            fetch(`${PAGE_CONFIG.slotAvailabilityUrl}?date=${selectedDate}&service=${svc}`)
                .then(r => r.json())
                .then(data => { slotData = data.error ? null : data; updateAvailabilityHint(); })
                .catch(() => { slotData = null; updateAvailabilityHint(); });
        }

        function updateAvailabilityHint() {
            if (!slotData || !slotSelect.value || slotSelect.value === 'Both') {
                availHint.textContent = '';
                return;
            }
            const info = slotData[slotSelect.value];
            if (!info) { availHint.textContent = ''; return; }

            if (info.total === 0) {
                availHint.innerHTML = '<span class="text-muted"><i class="bi bi-slash-circle me-1"></i>Unavailable</span>';
            } else if (info.available <= 0) {
                availHint.innerHTML = '<span class="text-warning"><i class="bi bi-exclamation-triangle me-1"></i>Waitlist</span>';
            } else {
                availHint.innerHTML = '<span class="text-success"><i class="bi bi-check-circle me-1"></i>Available</span>';
            }
        }

        // ─── Derived booking type ────────────────────────────────
        function getBookingType() {
            const isDropIn = getServiceType() === 'drop-in';
            if (recurringToggle.checked) return 'recurring';
            if (!isDropIn && slotSelect.value === 'Both') return 'both';
            if (isDropIn) return 'single-dropin';
            return 'single';
        }

        // ─── Submit state ────────────────────────────────────────
        function updateSubmitState() {
            const hasDate = !!selectedDate;
            const type = getBookingType();
            const hasSlot = !!slotSelect.value;
            if (submitBtn) {
                submitBtn.disabled = !hasDate || !hasSlot;
                if (type === 'recurring') {
                    const svc = getServiceType();
                    submitBtn.textContent = svc === 'drop-in' ? 'Request recurring drop-ins' : 'Request recurring walks';
                } else if (type === 'both') {
                    submitBtn.textContent = 'Request both walks';
                } else if (type === 'single-dropin') {
                    submitBtn.textContent = 'Request drop-in';
                } else {
                    submitBtn.textContent = 'Request walk';
                }
            }
        }

        // ─── Recurring toggle ────────────────────────────────────
        recurringToggle.addEventListener('change', function () {
            recurPanel.style.display = this.checked ? '' : 'none';
            updateSubmitState();
            renderRecurPreview();
        });

        slotSelect.addEventListener('change', function () {
            updateAvailabilityHint();
            updateSubmitState();
            renderRecurPreview();
        });
        recurFreq.addEventListener('change', renderRecurPreview);
        recurEnd.addEventListener('change', renderRecurPreview);

        function updateRecurringEndMax() {
            if (!selectedDate) return;
            const max = DogBoxxDates.addYears(selectedDate, 1);
            recurEnd.min = selectedDate;
            recurEnd.max = max;
            // Default end date to 1 year out (user can shorten it)
            if (!recurEnd.value || recurEnd.value < selectedDate) {
                recurEnd.value = max;
            }
            renderRecurPreview();
        }

        function generateRecurDates(startStr, endStr, frequency) {
            if (!startStr || !endStr) return [];
            const start = new Date(startStr + 'T00:00:00');
            const end = new Date(endStr + 'T00:00:00');
            if (end < start) return [];
            const dates = [];
            const delta = frequency === 'daily' ? 1 : 7;
            let cur = new Date(start);
            while (cur <= end) {
                if (frequency !== 'daily' || cur.getDay() !== 0 && cur.getDay() !== 6) {
                    dates.push(new Date(cur));
                }
                cur.setDate(cur.getDate() + delta);
            }
            return dates;
        }

        function renderRecurPreview() {
            if (!recurringToggle.checked) {
                recurPreview.innerHTML = '';
                return;
            }
            const dates = generateRecurDates(selectedDate, recurEnd.value, recurFreq.value);
            if (!dates.length) {
                recurPreview.innerHTML = '<span class="text-muted small">No dates in range</span>';
                return;
            }
            const isBoth = slotSelect.value === 'Both';
            const chips = dates.map(d =>
                `<span class="recurring-chip">${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}${isBoth ? ' ×2' : ''}</span>`
            ).join('');
            const total = isBoth ? dates.length * 2 : dates.length;
            const unit = getServiceType() === 'drop-in' ? 'drop-in' : 'walk';
            recurPreview.innerHTML = chips + `<div class="text-muted small mt-1">${total} ${unit}${total !== 1 ? 's' : ''} total</div>`;
        }

        // ─── Same-day confirmation ───────────────────────────────
        // Same-day check against the London date (matches the server's
        // _is_same_day()) — decides whether the warning modal fires before
        // submitting a booking.
        function isSameDay(dateStr) {
            return dateStr === DogBoxxDates.today();
        }
        // Shows a confirm modal; resolves true if the client presses its
        // confirm button, false if they cancel or dismiss it. Used for the
        // same-day and frozen-slot warnings.
        function confirmModal(modalId, confirmBtnId) {
            return new Promise((resolve) => {
                const el = document.getElementById(modalId);
                const modal = bootstrap.Modal.getOrCreateInstance(el);
                const confirmBtn = document.getElementById(confirmBtnId);
                let settled = false;
                function cleanup() {
                    confirmBtn.removeEventListener('click', onConfirm);
                    el.removeEventListener('hidden.bs.modal', onHidden);
                }
                function onConfirm() {
                    settled = true;
                    cleanup();
                    modal.hide();
                    resolve(true);
                }
                function onHidden() {
                    if (!settled) {
                        cleanup();
                        resolve(false);
                    }
                }
                confirmBtn.addEventListener('click', onConfirm);
                el.addEventListener('hidden.bs.modal', onHidden);
                modal.show();
            });
        }
        const confirmSameDay = () => confirmModal('sameDayModal', 'sameday-confirm-btn');

        // ─── Frozen slot confirmation ─────────────────────────────
        // slotData is populated by fetchAvailability() and carries a `frozen`
        // flag per slot (GET /api/slot_availability) — admin-set DB state the
        // client can't compute locally, unlike isSameDay() above.
        function isFrozen(slotValue) {
            if (!slotData) return false;
            if (slotValue === 'Both') {
                return !!(slotData.Morning?.frozen || slotData.Afternoon?.frozen);
            }
            return !!slotData[slotValue]?.frozen;
        }
        const confirmFrozen = () => confirmModal('frozenModal', 'frozen-confirm-btn');

        // ─── Form submit — all booking types ─────────────────────
        const bookingForm = document.getElementById('BookingForm');
        bookingForm.addEventListener('submit', async function (e) {
            e.preventDefault(); // Always intercept — both paths are AJAX now

            const csrfToken = document.querySelector('input[name=csrf_token]')?.value;

            const bookingType = getBookingType();
            const serviceType = getServiceType();

            if (bookingType === 'recurring') {
                // ── Recurring: walk or drop-in ────────────────────
                if (!selectedDate) { showToast('Please select a start date.', 'warning'); return; }
                if (!recurEnd.value) { showToast('Please choose an end date.', 'warning'); return; }
                if (!slotSelect.value) { showToast('Please choose a slot.', 'warning'); return; }

                const recurLabel = serviceType === 'drop-in' ? 'Request recurring drop-ins' : 'Request recurring walks';
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Requesting…';

                fetch(PAGE_CONFIG.recurringBookingUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({
                        start_date: selectedDate,
                        end_date: recurEnd.value,
                        slot: slotSelect.value,
                        frequency: recurFreq.value,
                        dog_id: document.getElementById('dog-selector')?.value || null,
                        service_type: serviceType,
                    }),
                })
                    .then(r => r.json())
                    .then(data => {
                        if (data.success) {
                            const parts = [];
                            if (data.confirmed) parts.push(`${data.confirmed} confirmed`);
                            if (data.created) parts.push(`${data.created} requested`);
                            if (data.waitlisted) parts.push(`${data.waitlisted} waitlisted`);
                            if (data.skipped) parts.push(`${data.skipped} skipped (already booked)`);
                            showToast(`Done! ${parts.join(', ')}.`, 'success');
                            window.location.reload();
                        } else {
                            showToast(data.message || 'Something went wrong.', 'danger');
                            submitBtn.disabled = false;
                            submitBtn.textContent = recurLabel;
                        }
                    })
                    .catch((err) => {
                        console.error('recurring booking error:', err);
                        showToast('Network error — please try again.', 'danger');
                        submitBtn.disabled = false;
                        submitBtn.textContent = recurLabel;
                    });

            } else if (bookingType === 'single-dropin') {
                // ── Single drop-in ────────────────────────────────
                if (!selectedDate) { showToast('Please select a date.', 'warning'); return; }
                if (!slotSelect.value) { showToast('Please choose a time slot.', 'warning'); return; }

                if (isSameDay(selectedDate) && !(await confirmSameDay())) return;
                if (isFrozen(slotSelect.value) && !(await confirmFrozen())) return;

                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Requesting…';
                fetch(PAGE_CONFIG.bookDropInUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ date: selectedDate, slot: slotSelect.value, dog_id: document.getElementById('dog-selector')?.value || null }),
                })
                    .then(r => r.json())
                    .then(data => {
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Request drop-in';
                        if (data.success) {
                            showToast(data.message, data.status === 'waitlisted' ? 'info' : 'success');
                            if (data.booking) {
                                data.booking.is_drop_in = true;
                                injectBooking(data.booking);
                            }
                            loadCalendarData(cal.year, cal.month);
                            selectedDate = null;
                            dateHidden.value = '';
                            dateText.textContent = 'Select a date above';
                            slotSelect.value = '';
                            submitBtn.disabled = true;
                        } else {
                            showToast(data.message || 'Something went wrong.', 'danger');
                        }
                    })
                    .catch((err) => {
                        console.error('drop-in booking error:', err);
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Request drop-in';
                        showToast('Network error — please try again.', 'danger');
                    });

            } else if (bookingType === 'both') {
                // ── Book both walks: AM + PM in one request ────────
                if (!selectedDate) { showToast('Please select a date.', 'warning'); return; }

                if (isSameDay(selectedDate) && !(await confirmSameDay())) return;
                if (isFrozen('Both') && !(await confirmFrozen())) return;

                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Requesting…';
                fetch(PAGE_CONFIG.bookBothUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ date: selectedDate, dog_id: document.getElementById('dog-selector')?.value || null }),
                })
                    .then(r => r.json())
                    .then(data => {
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Request walk';
                        if (data.success) {
                            const hasWaitlist = data.bookings.some(b => b.status === 'waitlisted');
                            showToast(data.message, hasWaitlist ? 'info' : 'success');
                            data.bookings.forEach(b => injectBooking(b));
                            loadCalendarData(cal.year, cal.month);
                            selectedDate = null;
                            dateHidden.value = '';
                            dateText.textContent = 'Select a date above';
                            availHint.textContent = '';
                            slotSelect.value = '';
                            submitBtn.disabled = true;
                        } else {
                            showToast(data.message || 'Something went wrong.', 'danger');
                        }
                    })
                    .catch((err) => {
                        console.error('both-walks booking error:', err);
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Request walk';
                        showToast('Network error — please try again.', 'danger');
                    });

            } else {
                // ── Single booking: AJAX, no page reload ──────────
                if (!selectedDate) { showToast('Please select a date.', 'warning'); return; }
                if (!slotSelect.value) { showToast('Please choose a slot.', 'warning'); return; }

                if (isSameDay(selectedDate) && !(await confirmSameDay())) return;
                if (isFrozen(slotSelect.value) && !(await confirmFrozen())) return;

                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Requesting…';
                fetch(PAGE_CONFIG.bookUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ date: selectedDate, slot: slotSelect.value, dog_id: document.getElementById('dog-selector')?.value || null }),
                })
                    .then(r => r.json())
                    .then(data => {
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Request walk';
                        if (data.success) {
                            showToast(data.message, data.status === 'waitlisted' ? 'info' : 'success');
                            injectBooking(data.booking);
                            loadCalendarData(cal.year, cal.month);
                            selectedDate = null;
                            dateHidden.value = '';
                            dateText.textContent = 'Select a date above';
                            availHint.textContent = '';
                            slotSelect.value = '';
                            submitBtn.disabled = true;
                        } else {
                            showToast(data.message || 'Something went wrong.', 'danger');
                        }
                    })
                    .catch((err) => {
                        console.error('single-walk booking error:', err);
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Request walk';
                        showToast('Network error — please try again.', 'danger');
                    });
            }
        });

        // ─── Walk pagination + date filter (client-bookings-list.js) ─
        document.querySelectorAll('.walk-prev-btn, .walk-next-btn').forEach(btn => {
            btn.addEventListener('click', function () {
                const dogId = this.getAttribute('data-dog-id');
                BookingsList.turnPage(dogId, this.classList.contains('walk-prev-btn') ? -1 : 1);
                // Scroll back to the dog card header
                const card = document.getElementById(`bookings-list-${dogId}`);
                if (card) card.closest('.card').scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        });

        const clearDateFilter = BookingsList.clearDateFilter;
        const filterClear = document.getElementById('date-filter-clear');
        filterClear.addEventListener('click', clearDateFilter);
        document.addEventListener('click', e => {
            if (e.target.closest('.inline-filter-clear')) clearDateFilter();
        });

        // ─── Cancel booking — event delegation ──────────────────
        // Using delegation so dynamically-injected bookings get the handler too
        const cancelModal = new bootstrap.Modal(document.getElementById('cancelModal'));
        const cancelModalBody = document.getElementById('cancel-modal-body');
        const cancelLateWarn = document.getElementById('cancel-late-warning');
        const cancelConfirmBtn = document.getElementById('cancel-confirm-btn');
        let pendingCancelId = null;
        let pendingCancelBtn = null;

        document.addEventListener('click', function (e) {
            const btn = e.target.closest('.cancel-booking-btn');
            if (!btn) return;
            e.preventDefault();
            pendingCancelId = btn.getAttribute('data-booking-id');
            pendingCancelBtn = btn;
            const slot = btn.getAttribute('data-booking-slot');
            const date = btn.getAttribute('data-booking-date');
            const dateIso = btn.getAttribute('data-booking-date-iso');

            const isDropIn = btn.getAttribute('data-is-drop-in') === 'true';
            const serviceLabel = isDropIn ? 'drop-in' : 'walk';
            cancelModalBody.textContent = `Are you sure you want to cancel the ${slot} ${serviceLabel} on ${date}?`;

            // Show the late-cancel charge warning only for a CONFIRMED booking
            // inside the 5-day window. A requested/waitlisted booking was never
            // confirmed, so cancelling it is never billed (server rule:
            // bill_cancellation_for() in app/utils/invoicing.py) — warning
            // about a charge there was wrong.
            const status = btn.getAttribute('data-booking-status');
            const walkDate = new Date(dateIso);
            const today = new Date();
            today.setHours(0, 0, 0, 0);
            const noticeDays = Math.round((walkDate - today) / 86400000);
            const willBeCharged = status === 'confirmed' && noticeDays < 5;
            cancelLateWarn.classList.toggle('d-none', !willBeCharged);

            cancelModal.show();
        });

        cancelConfirmBtn.addEventListener('click', function () {
            cancelModal.hide();
            if (pendingCancelId && pendingCancelBtn) {
                cancelBooking(pendingCancelId, pendingCancelBtn);
            }
        });

    }); // DOMContentLoaded

    function injectBooking(booking) {
        if (!BookingsList.injectBooking(booking)) window.location.reload();
    }

    // ─── Booking notes ───────────────────────────────────────────────────────────
    (function () {
        const modal = new bootstrap.Modal(document.getElementById('noteModal'));
        const textarea = document.getElementById('note-textarea');
        const saveBtn = document.getElementById('note-save-btn');
        const subtitle = document.getElementById('note-modal-subtitle');
        const charCount = document.getElementById('note-char-count');
        const errorEl = document.getElementById('note-error');
        const cancelBtn = document.getElementById('note-cancel-btn');
        const titleIcon = document.getElementById('note-modal-icon');
        let activeBookingId = null;
        let activeNoteBtn = null;

        textarea.addEventListener('input', () => {
            charCount.textContent = `${textarea.value.length} / 500`;
        });

        // One modal, two modes: edit (pencil, upcoming walks) and view-only
        // (file icon, past walks — the server refuses edits to those).
        function openNote(btn, readOnly) {
            const slot = btn.getAttribute('data-booking-slot');
            const dateDisp = btn.getAttribute('data-booking-date');
            const current = btn.getAttribute('data-current-note') || '';

            subtitle.textContent = `${slot} · ${dateDisp}`;
            textarea.value = current;
            textarea.readOnly = readOnly;
            charCount.textContent = `${current.length} / 500`;
            charCount.classList.toggle('d-none', readOnly);
            saveBtn.classList.toggle('d-none', readOnly);
            cancelBtn.textContent = readOnly ? 'Close' : 'Cancel';
            titleIcon.className = `bi bi-${readOnly ? 'file-earmark' : 'pencil'} me-2 text-primary`;
            errorEl.textContent = '';
            modal.show();
            if (!readOnly) {
                document.getElementById('noteModal').addEventListener('shown.bs.modal', () => textarea.focus(), { once: true });
            }
        }

        document.addEventListener('click', function (e) {
            const viewBtn = e.target.closest('.note-view-btn');
            if (viewBtn) {
                activeBookingId = null;
                activeNoteBtn = null;
                openNote(viewBtn, true);
                return;
            }
            const btn = e.target.closest('.note-booking-btn');
            if (!btn) return;
            activeBookingId = btn.getAttribute('data-booking-id');
            activeNoteBtn = btn;
            openNote(btn, false);
        });

        // Quick save: just disable while in flight, toast on success (same as
        // /profile). Spinner + "…ing" labels are for the slow actions.
        saveBtn.addEventListener('click', function () {
            const note = textarea.value.trim();
            errorEl.textContent = '';
            saveBtn.disabled = true;

            const csrfToken = document.querySelector('input[name=csrf_token]')?.value;

            fetch(`/booking/${activeBookingId}/note`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ note })
            })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        // Update the button icon + data attr to reflect saved state
                        activeNoteBtn.setAttribute('data-current-note', note);
                        const icon = activeNoteBtn.querySelector('i');
                        if (note) {
                            icon.className = 'bi bi-pencil-square text-primary';
                            activeNoteBtn.title = 'Edit note';
                        } else {
                            icon.className = 'bi bi-pencil-square';
                            activeNoteBtn.title = 'Add note';
                        }
                        modal.hide();
                        showToast(note ? 'Note saved.' : 'Note removed.', 'success');
                    } else {
                        errorEl.textContent = data.message || 'Could not save note.';
                    }
                })
                .catch(() => {
                    errorEl.textContent = 'Network error — please try again.';
                })
                .finally(() => { saveBtn.disabled = false; });
        });
    })();

    function cancelBooking(bookingId, buttonElement) {
        buttonElement.disabled = true;
        buttonElement.innerHTML = '<i class="bi bi-hourglass-split"></i>';

        const csrfToken = document.querySelector('meta[name=csrf-token]')?.getAttribute('content') ||
            document.querySelector('input[name=csrf_token]')?.value;

        fetch(PAGE_CONFIG.cancelBookingUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify({ booking_id: bookingId })
        })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    const item = buttonElement.closest('li');
                    if (item) BookingsList.removeRow(item);
                    showToast('Booking cancelled.', 'info');
                } else {
                    buttonElement.disabled = false;
                    buttonElement.innerHTML = '<i class="bi bi-x-circle"></i>';
                    showToast(data.message || 'Could not cancel booking.', 'danger');
                }
            })
            .catch(() => {
                buttonElement.disabled = false;
                buttonElement.innerHTML = '<i class="bi bi-x-circle"></i>';
                showToast('Network error — could not cancel booking. Please try again.', 'danger');
            });
    }

    // ── Pause Walks ──────────────────────────────────────────────────────────────
    (function () {
        const startEl = document.getElementById('pause-start-date');
        const endEl = document.getElementById('pause-end-date');
        const previewBtn = document.getElementById('pause-preview-btn');
        const step1 = document.getElementById('pause-step-1');
        const step2 = document.getElementById('pause-step-2');
        const step3 = document.getElementById('pause-step-3');
        const step1Err = document.getElementById('pause-step1-error');
        const summary = document.getElementById('pause-preview-summary');
        const listEl = document.getElementById('pause-preview-list');
        const chargeWarning = document.getElementById('pause-charge-warning');
        const backBtn = document.getElementById('pause-back-btn');
        const confirmBtn = document.getElementById('pause-confirm-btn');
        const successMsg = document.getElementById('pause-success-msg');
        const resetBtn = document.getElementById('pause-reset-btn');

        if (!startEl) return;

        const PREVIEW_LABEL = 'Preview affected bookings<i class="bi bi-arrow-right ms-1"></i>';
        const plural = (n, word) => `${n} ${word}${n !== 1 ? 's' : ''}`;
        // Start date must be strictly tomorrow or later
        const tomorrowStr = DogBoxxDates.addDays(DogBoxxDates.today(), 1);
        startEl.min = tomorrowStr;
        endEl.min = tomorrowStr;

        function checkDates() {
            step1Err.style.display = 'none';
            previewBtn.disabled = !(startEl.value && endEl.value && endEl.value >= startEl.value);
        }

        startEl.addEventListener('change', function () {
            if (endEl.value && endEl.value < startEl.value) endEl.value = startEl.value;
            endEl.min = startEl.value || tomorrowStr;
            checkDates();
        });
        endEl.addEventListener('change', checkDates);

        // Returns array — empty for "Both" (server treats missing/empty as all slots).
        function selectedSlots() {
            const v = document.querySelector('input[name="pause-slot"]:checked')?.value;
            return (v === 'Morning' || v === 'Afternoon') ? [v] : [];
        }

        previewBtn.addEventListener('click', function () {
            const csrf = document.querySelector('input[name=csrf_token]')?.value;
            previewBtn.disabled = true;
            previewBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading…';
            step1Err.style.display = 'none';

            const slotQs = selectedSlots().map(s => `&slots=${encodeURIComponent(s)}`).join('');
            fetch(`${PAGE_CONFIG.pauseWalksPreviewUrl}?start=${startEl.value}&end=${endEl.value}${slotQs}`, {
                headers: { 'X-CSRFToken': csrf }
            })
                .then(r => r.json())
                .then(data => {
                    previewBtn.disabled = false;
                    previewBtn.innerHTML = PREVIEW_LABEL;
                    if (!data.success) {
                        step1Err.textContent = data.error || 'Something went wrong.';
                        step1Err.style.display = '';
                        return;
                    }
                    if (data.count === 0) {
                        step1Err.textContent = 'No active bookings found in that date range.';
                        step1Err.style.display = '';
                        return;
                    }
                    summary.textContent = `${plural(data.count, 'booking')} will be cancelled:`;
                    // `charged` is the server's billing decision for each row —
                    // the same rule the cancel applies — so this warning is
                    // exactly what will be invoiced.
                    const k = data.charged_count;
                    chargeWarning.style.display = k ? '' : 'none';
                    chargeWarning.querySelector('span').textContent = k
                        ? `${k === data.count ? (k === 1 ? 'This booking is' : 'All of these are') : `${k} of these ${k === 1 ? 'is' : 'are'}`} ` +
                          'inside the cancellation notice period and will be charged.'
                        : '';
                    listEl.innerHTML = data.bookings.map(b =>
                        `<div class="d-flex align-items-center gap-2 py-1 border-bottom small last-child-no-border">
                        <span class="text-muted" style="width:70px;">${BookingsList.escHtml(b.date)}</span>
                        <span class="badge bg-secondary fw-normal">${BookingsList.escHtml(b.slot)}</span>
                        ${b.is_drop_in ? '<span class="text-primary"><i class="bi bi-house-door-fill me-1"></i>Drop-in</span>' : ''}
                        ${b.dog ? `<span class="text-muted">· ${BookingsList.escHtml(b.dog)}</span>` : ''}
                        ${b.charged ? '<span class="badge fw-normal ms-auto" style="background:#fff3cd;color:#92400e;">Charged</span>' : ''}
                    </div>`
                    ).join('');
                    step1.style.display = 'none';
                    step2.style.display = '';
                })
                .catch(() => {
                    previewBtn.disabled = false;
                    previewBtn.innerHTML = PREVIEW_LABEL;
                    step1Err.textContent = 'Network error — please try again.';
                    step1Err.style.display = '';
                });
        });

        backBtn.addEventListener('click', function () {
            step2.style.display = 'none';
            step1.style.display = '';
        });

        confirmBtn.addEventListener('click', function () {
            const csrf = document.querySelector('input[name=csrf_token]')?.value;
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Cancelling…';

            fetch(PAGE_CONFIG.pauseWalksUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
                body: JSON.stringify({ start: startEl.value, end: endEl.value, slots: selectedSlots() })
            })
                .then(r => r.json())
                .then(data => {
                    confirmBtn.disabled = false;
                    confirmBtn.innerHTML = '<i class="bi bi-x-circle me-1"></i>Cancel all';
                    if (data.success) {
                        const n = data.cancelled_count;
                        successMsg.textContent = `${plural(n, 'booking')} cancelled successfully.`;
                        step2.style.display = 'none';
                        step3.style.display = '';
                    } else {
                        showToast(data.error || 'Could not pause walks.', 'danger');
                        step2.style.display = 'none';
                        step1.style.display = '';
                    }
                })
                .catch(() => {
                    confirmBtn.disabled = false;
                    confirmBtn.innerHTML = '<i class="bi bi-x-circle me-1"></i>Cancel all';
                    showToast('Network error — please try again.', 'danger');
                });
        });

        resetBtn.addEventListener('click', function () {
            startEl.value = '';
            endEl.value = '';
            previewBtn.disabled = true;
            step3.style.display = 'none';
            step1.style.display = '';
            window.location.reload();
        });
    })();
})();
