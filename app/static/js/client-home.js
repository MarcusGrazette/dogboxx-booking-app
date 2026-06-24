/* Client home page — booking form, calendar, cancel, notes, pause walks.
 * Server data (URLs, user info) injected via <script id="page-config" type="application/json">.
 */
(function () {
    const PAGE_CONFIG = JSON.parse(document.getElementById('page-config').textContent);

    // ─── Greeting ────────────────────────────────────────────────
    (function () {
        const h = new Date().getHours();
        const greeting = h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
        const el = document.getElementById('greeting');
        if (el) el.textContent = greeting + ', ' + PAGE_CONFIG.userFirstname + '.';
    })();

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
                applyDateFilter(dateStr);
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
            const start = new Date(selectedDate + 'T00:00:00');
            const max = new Date(start);
            max.setFullYear(max.getFullYear() + 1);
            recurEnd.min = selectedDate;
            recurEnd.max = max.toISOString().slice(0, 10);
            // Default end date to 1 year out (user can shorten it)
            if (!recurEnd.value || recurEnd.value < selectedDate) {
                recurEnd.value = max.toISOString().slice(0, 10);
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
        // Today in UTC (matches server) — used to decide whether the
        // warning modal fires before submitting a booking.
        function todayUTC() {
            return new Date().toISOString().slice(0, 10);
        }
        function isSameDay(dateStr) {
            return dateStr === todayUTC();
        }
        // Returns a promise resolving to true if the client confirms,
        // false if they cancel/dismiss the modal.
        function confirmSameDay() {
            return new Promise((resolve) => {
                const el = document.getElementById('sameDayModal');
                const modal = bootstrap.Modal.getOrCreateInstance(el);
                const confirmBtn = document.getElementById('sameday-confirm-btn');
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

        // ─── Walk pagination ──────────────────────────────────────
        const PAGE_SIZE = 10;
        const walkPages = {}; // dogId → current page (0-indexed)

        function renderWalkPage(dogId) {
            if (filterDate) return; // filter controls visibility instead
            const list = document.getElementById(`bookings-list-${dogId}`);
            if (!list) return;
            const total = parseInt(list.dataset.total, 10);
            const page = walkPages[dogId] || 0;
            const start = page * PAGE_SIZE;
            const end = Math.min(start + PAGE_SIZE, total);

            list.querySelectorAll('li[data-walk-idx]').forEach(li => {
                const idx = parseInt(li.dataset.walkIdx, 10);
                li.classList.toggle('d-none', idx < start || idx >= end);
            });

            const label = document.getElementById(`walk-page-label-${dogId}`);
            const prevBtn = document.querySelector(`.walk-prev-btn[data-dog-id="${dogId}"]`);
            const nextBtn = document.querySelector(`.walk-next-btn[data-dog-id="${dogId}"]`);

            if (label) label.textContent = `${start + 1} – ${end} of ${total}`;
            if (prevBtn) prevBtn.disabled = (page === 0);
            if (nextBtn) nextBtn.disabled = (end >= total);
        }

        document.querySelectorAll('.walk-prev-btn, .walk-next-btn').forEach(btn => {
            btn.addEventListener('click', function () {
                const dogId = this.getAttribute('data-dog-id');
                const isPrev = this.classList.contains('walk-prev-btn');
                walkPages[dogId] = (walkPages[dogId] || 0) + (isPrev ? -1 : 1);
                renderWalkPage(dogId);
                // Scroll back to the dog card header
                const card = document.getElementById(`bookings-list-${dogId}`);
                if (card) card.closest('.card').scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        });

        // ─── Date filter ─────────────────────────────────────────
        const filterBar = document.getElementById('date-filter-bar');
        const filterLabel = document.getElementById('date-filter-label');
        const filterClear = document.getElementById('date-filter-clear');

        function applyDateFilter(isoDate) {
            filterDate = isoDate;
            const d = new Date(isoDate + 'T00:00:00');
            const label = d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
            filterLabel.textContent = label;
            filterBar.style.display = '';

            document.querySelectorAll('[id^="bookings-list-"]').forEach(list => {
                const dogId = list.dataset.dogId;
                let matchCount = 0;

                list.querySelectorAll('li[data-date]').forEach(li => {
                    const matches = li.dataset.date === isoDate;
                    li.classList.toggle('d-none', !matches);
                    if (matches) matchCount++;
                });

                const pager = document.getElementById(`walk-pager-${dogId}`);
                if (pager) pager.style.display = 'none';

                const subtitle = document.getElementById(`dog-subtitle-${dogId}`);
                if (subtitle) subtitle.textContent = label;

                const filterEmpty = document.getElementById(`dog-filter-empty-${dogId}`);
                if (filterEmpty) {
                    filterEmpty.style.display = matchCount === 0 ? '' : 'none';
                    if (matchCount === 0) {
                        filterEmpty.querySelector('p').innerHTML =
                            `<i class="bi bi-calendar2-x me-2"></i>No bookings on ${label}. ` +
                            `<button type="button" class="btn btn-link p-0 border-0 align-baseline inline-filter-clear" ` +
                            `style="font-size:inherit;color:#E02FAC;vertical-align:baseline;">Clear the filter</button> to see all bookings.`;
                    }
                }
            });
        }

        function clearDateFilter() {
            filterDate = null;
            filterBar.style.display = 'none';

            document.querySelectorAll('[id^="bookings-list-"]').forEach(list => {
                const dogId = list.dataset.dogId;

                const subtitle = document.getElementById(`dog-subtitle-${dogId}`);
                if (subtitle) subtitle.textContent = 'Upcoming walks';

                const filterEmpty = document.getElementById(`dog-filter-empty-${dogId}`);
                if (filterEmpty) filterEmpty.style.display = 'none';

                // Re-hide past bookings that may have been revealed by the filter
                list.querySelectorAll('li[data-past]').forEach(li => li.classList.add('d-none'));

                const pager = document.getElementById(`walk-pager-${dogId}`);
                if (pager) pager.style.display = '';

                renderWalkPage(dogId);
            });
        }

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

            // Show late-cancel warning if within 5 days
            const walkDate = new Date(dateIso);
            const today = new Date();
            today.setHours(0, 0, 0, 0);
            const noticeDays = Math.round((walkDate - today) / 86400000);
            cancelLateWarn.classList.toggle('d-none', noticeDays >= 5);

            cancelModal.show();
        });

        cancelConfirmBtn.addEventListener('click', function () {
            cancelModal.hide();
            if (pendingCancelId && pendingCancelBtn) {
                cancelBooking(pendingCancelId, pendingCancelBtn);
            }
        });

    }); // DOMContentLoaded

    // filterDate is script-level so injectBooking (outside DOMContentLoaded) can read it
    let filterDate = null;

    // ─── Inject a new booking row into the DOM ───────────────────────────────────
    function injectBooking(booking) {
        const container = document.getElementById(`dog-bookings-${booking.dog_id}`);
        if (!container) { window.location.reload(); return; }

        // If showing empty state, replace it with a fresh list
        const empty = document.getElementById(`dog-empty-${booking.dog_id}`);
        if (empty) {
            empty.remove();
            const ul = document.createElement('ul');
            ul.className = 'list-group list-group-flush';
            ul.id = `bookings-list-${booking.dog_id}`;
            ul.setAttribute('data-dog-id', booking.dog_id);
            ul.setAttribute('data-total', '0');
            container.appendChild(ul);
        }

        const list = document.getElementById(`bookings-list-${booking.dog_id}`);
        if (!list) { window.location.reload(); return; }

        // Build status badge
        const badgeHtml = booking.status === 'waitlisted'
            ? `<span class="badge rounded-pill text-bg-info py-2 text-center" style="min-width:90px;">Waitlisted</span>`
            : booking.status === 'confirmed'
                ? `<span class="badge rounded-pill text-bg-success py-2 text-center" style="min-width:90px;">Confirmed</span>`
                : `<span class="badge rounded-pill py-2 text-center" style="min-width:90px; background-color:#fff3cd; color:#856404;">Requested</span>`;

        // "New" badge — fades out after 4s
        const newBadge = `<span class="badge bg-pink ms-1 new-booking-badge"
            style="background:#E02FAC;font-size:0.7em;vertical-align:middle;
                   transition:opacity 0.6s ease;">New</span>`;

        // Walker name (confirmed bookings)
        const walkerLabel = (booking.status === 'confirmed' && booking.walker_name)
            ? ` <span class="text-muted">(${booking.walker_name})</span>` : '';

        // Drop-in label
        const dropInLabel = booking.is_drop_in
            ? `<i class="bi bi-house-door-fill text-primary me-1"></i>Drop-in · ` : '';

        // (pickup notes icon intentionally omitted — pencil button handles this)

        // Re-index existing items
        list.querySelectorAll('li[data-walk-idx]').forEach(el => {
            el.setAttribute('data-walk-idx', String(parseInt(el.getAttribute('data-walk-idx'), 10) + 1));
        });

        const li = document.createElement('li');
        li.className = 'list-group-item px-0 py-3 border-bottom d-flex align-items-center justify-content-between gap-2';
        li.setAttribute('data-walk-idx', '0');
        if (booking.date_iso) li.setAttribute('data-date', booking.date_iso);
        if (filterDate && booking.date_iso !== filterDate) li.classList.add('d-none');

        // If a filter is active for this exact date, dismiss the "no bookings" empty state
        if (filterDate && booking.date_iso === filterDate) {
            const filterEmpty = document.getElementById(`dog-filter-empty-${booking.dog_id}`);
            if (filterEmpty) filterEmpty.style.display = 'none';
        }

        li.innerHTML = `
        <div class="d-flex align-items-center gap-3">
            ${badgeHtml}
            <div>
                <div class="fw-semibold small">${booking.date_display}${newBadge}</div>
                <div class="text-muted" style="font-size:0.78rem;">${dropInLabel}${booking.slot}${walkerLabel}</div>
            </div>
        </div>
        <div class="d-flex align-items-center gap-3">
            <button class="btn btn-link p-1 note-booking-btn text-secondary"
                    data-booking-id="${booking.id}"
                    data-booking-slot="${booking.slot}"
                    data-booking-date="${booking.date_display}"
                    data-current-note=""
                    title="Add note"
                    style="line-height:1;font-size:1.1rem;">
                <i class="bi bi-pencil-square"></i>
            </button>
            <button class="btn btn-link p-1 cancel-booking-btn text-danger"
                    data-booking-id="${booking.id}"
                    data-booking-slot="${booking.slot}"
                    data-booking-date="${booking.date_display}"
                    data-booking-date-iso="${booking.date_iso}"
                    data-is-drop-in="${booking.is_drop_in ? 'true' : 'false'}"
                    title="Cancel booking"
                    style="line-height:1;font-size:1.1rem;">
                <i class="bi bi-x-circle"></i>
            </button>
        </div>`;

        // Animate in
        li.style.opacity = '0';
        li.style.transition = 'opacity 0.3s ease';
        list.insertBefore(li, list.firstChild);
        requestAnimationFrame(() => { li.style.opacity = '1'; });

        // Fade out "New" badge after 4s
        setTimeout(() => {
            const badge = li.querySelector('.new-booking-badge');
            if (badge) {
                badge.style.opacity = '0';
                setTimeout(() => badge.remove(), 600);
            }
        }, 4000);

        // Update total count
        list.setAttribute('data-total', String(parseInt(list.getAttribute('data-total'), 10) + 1));
    }

    // ─── Booking notes ───────────────────────────────────────────────────────────
    (function () {
        const modal = new bootstrap.Modal(document.getElementById('noteModal'));
        const textarea = document.getElementById('note-textarea');
        const saveBtn = document.getElementById('note-save-btn');
        const subtitle = document.getElementById('note-modal-subtitle');
        const charCount = document.getElementById('note-char-count');
        const errorEl = document.getElementById('note-error');
        let activeBookingId = null;
        let activeNoteBtn = null;

        textarea.addEventListener('input', () => {
            charCount.textContent = `${textarea.value.length} / 500`;
        });

        document.addEventListener('click', function (e) {
            const btn = e.target.closest('.note-booking-btn');
            if (!btn) return;
            activeBookingId = btn.getAttribute('data-booking-id');
            activeNoteBtn = btn;
            const slot = btn.getAttribute('data-booking-slot');
            const dateDisp = btn.getAttribute('data-booking-date');
            const current = btn.getAttribute('data-current-note') || '';

            subtitle.textContent = `${slot} · ${dateDisp}`;
            textarea.value = current;
            charCount.textContent = `${current.length} / 500`;
            errorEl.textContent = '';
            modal.show();
            document.getElementById('noteModal').addEventListener('shown.bs.modal', () => textarea.focus(), { once: true });
        });

        saveBtn.addEventListener('click', function () {
            const note = textarea.value.trim();
            errorEl.textContent = '';
            saveBtn.disabled = true;
            saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving…';

            const csrfToken = document.querySelector('input[name=csrf_token]')?.value;

            fetch(`/booking/${activeBookingId}/note`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ note })
            })
                .then(r => r.json())
                .then(data => {
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Save note';
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
                    } else {
                        errorEl.textContent = data.message || 'Could not save note.';
                    }
                })
                .catch(() => {
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Save note';
                    errorEl.textContent = 'Network error — please try again.';
                });
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
                    if (item) {
                        // Decrement data-total so pagination stays accurate
                        const list = item.closest('ul[data-total]');
                        if (list) {
                            const curr = parseInt(list.getAttribute('data-total'), 10);
                            if (curr > 0) list.setAttribute('data-total', String(curr - 1));
                        }
                        item.style.transition = 'opacity 0.3s ease';
                        item.style.opacity = '0';
                        setTimeout(() => item.remove(), 300);
                    }
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
        const backBtn = document.getElementById('pause-back-btn');
        const confirmBtn = document.getElementById('pause-confirm-btn');
        const successMsg = document.getElementById('pause-success-msg');
        const resetBtn = document.getElementById('pause-reset-btn');

        if (!startEl) return;

        // Start date must be strictly tomorrow or later
        const tomorrow = new Date();
        tomorrow.setDate(tomorrow.getDate() + 1);
        const tomorrowStr = tomorrow.toISOString().slice(0, 10);
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
                    previewBtn.innerHTML = 'Preview affected walks<i class="bi bi-arrow-right ms-1"></i>';
                    if (!data.success) {
                        step1Err.textContent = data.error || 'Something went wrong.';
                        step1Err.style.display = '';
                        return;
                    }
                    if (data.count === 0) {
                        step1Err.textContent = 'No active walks found in that date range.';
                        step1Err.style.display = '';
                        return;
                    }
                    const n = data.count;
                    summary.textContent = `${n} walk${n !== 1 ? 's' : ''} will be cancelled:`;
                    listEl.innerHTML = data.bookings.map(b =>
                        `<div class="d-flex align-items-center gap-2 py-1 border-bottom small last-child-no-border">
                        <span class="text-muted" style="width:70px;">${b.date}</span>
                        <span class="badge bg-secondary fw-normal">${b.slot}</span>
                        ${b.dog ? `<span class="text-muted">· ${b.dog}</span>` : ''}
                    </div>`
                    ).join('');
                    step1.style.display = 'none';
                    step2.style.display = '';
                })
                .catch(() => {
                    previewBtn.disabled = false;
                    previewBtn.innerHTML = 'Preview affected walks<i class="bi bi-arrow-right ms-1"></i>';
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
                        successMsg.textContent = `${n} walk${n !== 1 ? 's' : ''} cancelled successfully.`;
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
