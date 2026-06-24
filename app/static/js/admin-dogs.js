/* Admin dogs page — booking, edit, cancel modals + live filter.
 * URL endpoints are injected by the template via <script id="page-config" type="application/json">.
 */
document.addEventListener('DOMContentLoaded', function () {
    const PAGE_URLS = JSON.parse(document.getElementById('page-config').textContent);

    const modal         = new bootstrap.Modal(document.getElementById('bookingModal'));
    const modalDogName  = document.getElementById('modal-dog-name');
    const modalOwner    = document.getElementById('modal-owner-name');
    const serviceSel    = document.getElementById('modal-service-type');
    const slotSel       = document.getElementById('modal-slot');
    const slotBothOpt   = document.getElementById('modal-slot-both-opt');
    const dateInput     = document.getElementById('modal-date');
    const recurToggle   = document.getElementById('modal-recur-toggle');
    const recurRow      = document.getElementById('modal-recur-row');
    const recurPanel    = document.getElementById('recurring-panel');
    const startInput    = document.getElementById('modal-start-date');
    const endInput      = document.getElementById('modal-end-date');
    const singleGroup     = document.getElementById('single-date-group');
    const singleSlotGroup = document.getElementById('single-slot-group');
    const submitBtn       = document.getElementById('modal-submit-btn');
    const resultDiv       = document.getElementById('modal-result');
    const recurCount      = document.getElementById('recurring-count');

    // Success pop-over (showConfirmed) is provided by success-modal.js +
    // partials/success_modal.html. See docs/UX_GUIDE.md §1.
    function fmtBookingDate(iso) {
        const d = new Date(iso + 'T00:00:00');
        return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
    }

    // Adjust modal affordances for the selected service. Drop-ins don't
    // support "Both" slots (server-side rejected) and aren't available as
    // recurring bookings.
    function selectedServiceSlug() {
        const opt = serviceSel.options[serviceSel.selectedIndex];
        return opt ? opt.dataset.slug : '';
    }
    function syncServiceUI() {
        const isDropIn = selectedServiceSlug() === 'drop-in';
        if (isDropIn) {
            if (slotSel.value === 'Both') slotSel.value = '';
            slotBothOpt.hidden   = true;
            slotBothOpt.disabled = true;
            recurToggle.checked = false;
            recurRow.style.display     = 'none';
            recurPanel.style.display   = 'none';
            singleGroup.style.display     = 'block';
            singleSlotGroup.style.display = 'block';
            submitBtn.innerHTML = '<i class="bi bi-calendar-plus me-1"></i>Book';
        } else {
            slotBothOpt.hidden   = false;
            slotBothOpt.disabled = false;
            recurRow.style.display = '';
            submitBtn.innerHTML = recurToggle.checked
                ? '<i class="bi bi-calendar-plus me-1"></i>Book Recurring'
                : '<i class="bi bi-calendar-plus me-1"></i>Book';
        }
    }
    serviceSel.addEventListener('change', syncServiceUI);

    let activeDogId  = null;
    let activeUserId = null;

    // Single-booking date has no min — admin can record past or same-day bookings.
    // Recurring start stays future-only (no use case for past-dated recurring sets).
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    startInput.min = tomorrow.toISOString().slice(0, 10);

    // ── Open modal ──────────────────────────────────────────────────────────
    document.querySelectorAll('.book-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            activeDogId  = this.dataset.dogId;
            activeUserId = this.dataset.userId;
            modalDogName.textContent = this.dataset.dogName;
            modalOwner.textContent   = this.dataset.ownerName;

            // Reset form
            // Default service to Group Walk on open
            for (const opt of serviceSel.options) {
                if (opt.dataset.slug === 'group-walk') { serviceSel.value = opt.value; break; }
            }
            slotSel.value       = '';
            dateInput.value     = '';
            startInput.value    = '';
            endInput.value      = '';
            recurToggle.checked = false;
            recurPanel.style.display      = 'none';
            singleGroup.style.display     = 'block';
            singleSlotGroup.style.display = 'block';
            recurPanel.querySelectorAll('select[data-day]').forEach(s => s.value = '');
            recurCount.style.display = 'none';
            resultDiv.style.display = 'none';
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="bi bi-calendar-plus me-1"></i>Book';
            syncServiceUI();

            modal.show();
        });
    });

    // ── Recurring toggle ────────────────────────────────────────────────────
    recurToggle.addEventListener('change', function () {
        if (this.checked) {
            singleGroup.style.display     = 'none';
            singleSlotGroup.style.display = 'none';
            recurPanel.style.display      = 'block';
            submitBtn.innerHTML = '<i class="bi bi-calendar-plus me-1"></i>Book Recurring';
            updateCount();
        } else {
            singleGroup.style.display     = 'block';
            singleSlotGroup.style.display = 'block';
            recurPanel.style.display      = 'none';
            recurCount.style.display      = 'none';
            submitBtn.innerHTML = '<i class="bi bi-calendar-plus me-1"></i>Book';
        }
    });

    // ── Auto-fill end date to start + 1 year ───────────────────────────────
    startInput.addEventListener('change', function () {
        if (!this.value) return;
        const start = new Date(this.value + 'T00:00:00');
        start.setFullYear(start.getFullYear() + 1);
        endInput.value = start.toISOString().slice(0, 10);
        endInput.min   = this.value;
        updateCount();
    });
    endInput.addEventListener('change', updateCount);
    recurPanel.querySelectorAll('select[data-day]').forEach(s => s.addEventListener('change', updateCount));

    // ── Booking count preview ───────────────────────────────────────────────
    function updateCount() {
        if (!recurToggle.checked || !startInput.value || !endInput.value) {
            recurCount.style.display = 'none';
            return;
        }
        const start = new Date(startInput.value + 'T00:00:00');
        const end   = new Date(endInput.value   + 'T00:00:00');
        if (end < start) { recurCount.style.display = 'none'; return; }

        // Build selected (weekday → slot count) map — Both counts as 2
        const slotsByDay = {};
        recurPanel.querySelectorAll('select[data-day]').forEach(sel => {
            if (!sel.value) return;
            slotsByDay[parseInt(sel.dataset.day)] = sel.value === 'Both' ? 2 : 1;
        });
        if (!Object.keys(slotsByDay).length) { recurCount.style.display = 'none'; return; }

        let total = 0;
        let cur = new Date(start);
        while (cur <= end) {
            const wday = (cur.getDay() + 6) % 7; // JS Sunday=0 → Mon=0 mapping
            if (slotsByDay[wday]) total += slotsByDay[wday];
            cur.setDate(cur.getDate() + 1);
        }

        // Count whole weeks spanned for the summary line
        const weeks = Math.round((end - start) / (7 * 86400000));
        recurCount.textContent = `${total} booking${total !== 1 ? 's' : ''} across ${weeks} week${weeks !== 1 ? 's' : ''}`;
        recurCount.style.display = '';
    }

    // ── Submit ──────────────────────────────────────────────────────────────
    submitBtn.addEventListener('click', function () {
        resultDiv.style.display = 'none';

        const isRecurring = recurToggle.checked;

        if (!isRecurring) {
            // One-off
            const slot = slotSel.value;
            const date = dateInput.value;
            if (!slot) { showResult('danger', 'Please select a slot.'); return; }
            if (!date) { showResult('danger', 'Please select a date.'); return; }

            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Requesting…';

            fetch(PAGE_URLS.bookForDog, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
                body: JSON.stringify({
                    dog_id:          parseInt(activeDogId),
                    user_id:         parseInt(activeUserId),
                    service_type_id: parseInt(serviceSel.value),
                    date, slot,
                }),
            })
            .then(r => r.json())
            .then(data => {
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="bi bi-calendar-plus me-1"></i>Book';
                if (data.success) {
                    const serviceLabel = serviceSel.options[serviceSel.selectedIndex].text;
                    const slotLabel = slot === 'Both' ? 'AM + PM' : slot;
                    showConfirmed(`${serviceLabel} for ${modalDogName.textContent} — ${fmtBookingDate(date)}, ${slotLabel}`,
                                  modal, document.getElementById('bookingModal'));
                } else {
                    showResult('danger', data.message || 'Something went wrong');
                }
            })
            .catch(() => {
                showResult('danger', 'Network error — please try again.');
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="bi bi-calendar-plus me-1"></i>Book';
            });

        } else {
            // Recurring
            const startDate = startInput.value;
            const endDate   = endInput.value;
            if (!startDate) { showResult('danger', 'Please select a start date.'); return; }
            if (!endDate)   { showResult('danger', 'Please select an end date.');   return; }

            // Build day_slots — expand "Both" into two entries
            const daySlots = [];
            recurPanel.querySelectorAll('select[data-day]').forEach(sel => {
                if (!sel.value) return;
                const day = parseInt(sel.dataset.day);
                if (sel.value === 'Both') {
                    daySlots.push({ day, slot: 'Morning' });
                    daySlots.push({ day, slot: 'Afternoon' });
                } else {
                    daySlots.push({ day, slot: sel.value });
                }
            });
            if (!daySlots.length) { showResult('danger', 'Please select at least one day.'); return; }

            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Requesting…';
            recurCount.style.display = 'none';

            fetch(PAGE_URLS.recurringForDog, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
                body: JSON.stringify({
                    dog_id:          parseInt(activeDogId),
                    user_id:         parseInt(activeUserId),
                    service_type_id: parseInt(serviceSel.value),
                    start_date:      startDate,
                    end_date:        endDate,
                    day_slots:       daySlots,
                }),
            })
            .then(r => r.json())
            .then(data => {
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="bi bi-calendar-plus me-1"></i>Book Recurring';
                if (data.success) {
                    const parts = [];
                    if (data.confirmed)  parts.push(`${data.confirmed} confirmed`);
                    if (data.requested)  parts.push(`${data.requested} requested`);
                    if (data.waitlisted) parts.push(`${data.waitlisted} waitlisted`);
                    if (data.skipped)    parts.push(`${data.skipped} skipped`);
                    showConfirmed(`Recurring walks for ${modalDogName.textContent} — ${parts.join(', ')}.`,
                                  modal, document.getElementById('bookingModal'));
                } else {
                    showResult('danger', data.message || 'Something went wrong');
                }
            })
            .catch(() => {
                showResult('danger', 'Network error — please try again.');
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="bi bi-calendar-plus me-1"></i>Book Recurring';
            });
        }
    });

    function showResult(type, msg) {
        resultDiv.className = `alert alert-${type} py-2 mb-0`;
        resultDiv.textContent = msg;
        resultDiv.style.display = 'block';
    }

    function getCsrf() {
        return document.querySelector('meta[name=csrf-token]')?.content ||
               document.querySelector('input[name=csrf_token]')?.value || '';
    }

    // ── Row click → view modal ──────────────────────────────────────────────
    document.querySelectorAll('tr.dog-row').forEach(row => {
        row.addEventListener('click', function () {
            const target = this.dataset.viewModal;
            if (target) bootstrap.Modal.getOrCreateInstance(document.querySelector(target)).show();
        });
    });

    // ── Edit dog modal ──────────────────────────────────────────────────────
    const editModal      = new bootstrap.Modal(document.getElementById('editDogModal'));
    const editDogName    = document.getElementById('edit-modal-dog-name');
    const editNameInp    = document.getElementById('edit-dog-name');
    const editGenderSel  = document.getElementById('edit-dog-gender');
    const editBreedInp   = document.getElementById('edit-dog-breed');
    const editDobInp     = document.getElementById('edit-dog-dob');
    const editAllergyInp = document.getElementById('edit-dog-allergies');
    const editPickupTA   = document.getElementById('edit-dog-pickup');
    const editWaInp      = document.getElementById('edit-dog-whatsapp');
    const editHoldKey    = document.getElementById('edit-dog-hold-key');
    const editResult     = document.getElementById('edit-dog-result');
    const editSubmitBtn  = document.getElementById('edit-dog-submit-btn');

    let editActiveDogId  = null;
    let editActiveRow    = null;

    document.querySelectorAll('.edit-dog-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            editActiveDogId  = this.dataset.dogId;
            editActiveRow    = this.closest('tr');

            editDogName.textContent   = this.dataset.dogName;
            editNameInp.value         = this.dataset.dogName;
            editGenderSel.value       = this.dataset.dogGender;
            editBreedInp.value        = this.dataset.dogBreed;
            editDobInp.value          = this.dataset.dogDob;
            editAllergyInp.value      = this.dataset.dogAllergies;
            editPickupTA.value        = this.dataset.dogPickup;
            editWaInp.value           = this.dataset.dogWhatsapp;
            editHoldKey.checked       = this.dataset.dogHoldKey === 'true';
            editResult.style.display  = 'none';
            editSubmitBtn.disabled    = false;
            editSubmitBtn.innerHTML   = '<i class="bi bi-check-lg me-1"></i>Save changes';

            editModal.show();
        });
    });

    editSubmitBtn.addEventListener('click', function () {
        const name = editNameInp.value.trim();
        if (!name) { showEditResult('danger', 'Name is required.'); return; }

        editSubmitBtn.disabled = true;
        editSubmitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving…';

        fetch(`/admin/dogs/${editActiveDogId}/update`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrf(),
            },
            body: JSON.stringify({
                name:                 name,
                gender:               editGenderSel.value,
                breed:                editBreedInp.value.trim(),
                date_of_birth:        editDobInp.value,
                allergies:            editAllergyInp.value.trim(),
                pickup_instructions:  editPickupTA.value.trim(),
                whatsapp_group_url:   editWaInp.value.trim(),
                hold_key:             editHoldKey.checked,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                // Update the row's name and breed cells in place
                if (editActiveRow) {
                    const nameCell = editActiveRow.querySelector('td:first-child');
                    if (nameCell) {
                        const img = nameCell.querySelector('img');
                        nameCell.textContent = data.name;
                        if (img) nameCell.prepend(img);
                    }
                    const breedCell = editActiveRow.querySelectorAll('td')[1];
                    if (breedCell) breedCell.textContent = data.breed;
                    // Rebuild data-search with updated name/breed, preserving owner suffix
                    const ownerText = editActiveRow.dataset.ownerSearch || '';
                    editActiveRow.dataset.search = `${data.name.toLowerCase()} ${(editBreedInp.value.trim()).toLowerCase()} ${ownerText}`;
                    // Update the button data-* so re-opening modal shows fresh values
                    const editBtn = editActiveRow.querySelector('.edit-dog-btn');
                    if (editBtn) {
                        editBtn.dataset.dogName       = data.name;
                        editBtn.dataset.dogBreed      = editBreedInp.value.trim();
                        editBtn.dataset.dogGender     = editGenderSel.value;
                        editBtn.dataset.dogDob        = editDobInp.value;
                        editBtn.dataset.dogAllergies  = editAllergyInp.value.trim();
                        editBtn.dataset.dogPickup     = editPickupTA.value.trim();
                        editBtn.dataset.dogWhatsapp   = editWaInp.value.trim();
                        editBtn.dataset.dogHoldKey    = editHoldKey.checked ? 'true' : 'false';
                    }
                    // Update book-btn dog name too
                    const bookBtn = editActiveRow.querySelector('.book-btn');
                    if (bookBtn) bookBtn.dataset.dogName = data.name;
                }
                editModal.hide();
            } else {
                showEditResult('danger', data.message || 'Something went wrong');
                editSubmitBtn.disabled = false;
                editSubmitBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Save changes';
            }
        })
        .catch(() => {
            showEditResult('danger', 'Network error — please try again.');
            editSubmitBtn.disabled = false;
            editSubmitBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Save changes';
        });
    });

    function showEditResult(type, msg) {
        editResult.className = `alert alert-${type} py-2 mb-0`;
        editResult.textContent = msg;
        editResult.style.display = 'block';
    }

    // ── Live filter ─────────────────────────────────────────────────────────
    const searchInput   = document.getElementById('dog-search');
    const searchClear   = document.getElementById('dog-search-clear');
    const countEl       = document.getElementById('dog-count');
    const countLabel    = document.getElementById('dog-count-label');
    const noResultsRow  = document.getElementById('no-results-row');
    const allRows       = document.querySelectorAll('tbody tr[data-search]');

    function applyFilter() {
        const q = searchInput.value.trim().toLowerCase();
        searchClear.style.display = q ? 'inline-flex' : 'none';
        let visible = 0;
        allRows.forEach(row => {
            const match = !q || row.dataset.search.includes(q);
            row.style.display = match ? '' : 'none';
            if (match) visible++;
        });
        if (countEl) countEl.textContent = visible;
        if (countLabel) countLabel.textContent = visible === 1 ? 'dog' : 'dogs';
        if (noResultsRow) noResultsRow.style.display = (visible === 0 && q) ? '' : 'none';
    }

    if (searchInput) {
        searchInput.addEventListener('input', applyFilter);
        searchClear.addEventListener('click', () => {
            searchInput.value = '';
            applyFilter();
            searchInput.focus();
        });
    }

    // ── View modal → Edit / View Upcoming transition ───────────────────────
    // Close the view modal first, then open the target modal once the
    // fade-out completes (hidden.bs.modal).
    var pendingViewAction = null;

    document.querySelectorAll('.view-edit-btn, .view-bookings-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var viewEl   = document.getElementById(this.dataset.viewModal);
            var viewInst = bootstrap.Modal.getInstance(viewEl);
            var d        = this.dataset;
            var isEdit   = this.classList.contains('view-edit-btn');

            pendingViewAction = function () {
                if (isEdit) {
                    editActiveDogId          = d.dogId;
                    editActiveRow            = document.querySelector('button.edit-dog-btn[data-dog-id="' + d.dogId + '"]')?.closest('tr') || null;
                    editDogName.textContent  = d.dogName;
                    editNameInp.value        = d.dogName;
                    editGenderSel.value      = d.dogGender;
                    editBreedInp.value       = d.dogBreed;
                    editDobInp.value         = d.dogDob;
                    editAllergyInp.value     = d.dogAllergies;
                    editPickupTA.value       = d.dogPickup;
                    editWaInp.value          = d.dogWhatsapp;
                    editHoldKey.checked      = d.dogHoldKey === 'true';
                    editResult.style.display = 'none';
                    editSubmitBtn.disabled   = false;
                    editSubmitBtn.innerHTML  = '<i class="bi bi-check-lg me-1"></i>Save changes';
                    editModal.show();
                } else {
                    dbActiveDogId        = d.dogId;
                    dbDogName.textContent = d.dogName;
                    dbFromDate.value     = new Date().toISOString().slice(0, 10);
                    dbResults.innerHTML  = '';
                    dbPagination.style.display = 'none';
                    dbModal.show();
                    dbLoad(1);
                }
            };

            viewInst.hide();
        });
    });

    // Fire the pending action once the view modal has fully faded out
    document.querySelectorAll('[id^="dog-view-"]').forEach(function (el) {
        el.addEventListener('hidden.bs.modal', function () {
            if (pendingViewAction) {
                var fn = pendingViewAction;
                pendingViewAction = null;
                fn();
            }
        });
    });

    // ── Upcoming bookings modal ─────────────────────────────────────────────
    const dbModal      = new bootstrap.Modal(document.getElementById('dogBookingsModal'));
    const dbDogName    = document.getElementById('db-dog-name');
    const dbFromDate   = document.getElementById('db-from-date');
    const dbService    = document.getElementById('db-service');
    const dbPills      = document.getElementById('db-pills');
    const dbLoading    = document.getElementById('db-loading');
    const dbResults    = document.getElementById('db-results');
    const dbPagination = document.getElementById('db-pagination');
    const dbPrevBtn    = document.getElementById('db-prev-btn');
    const dbNextBtn    = document.getElementById('db-next-btn');
    const dbPageInfo   = document.getElementById('db-page-info');

    let dbActiveDogId = null;
    let dbCurrentPage = 1;

    const STATUS_BADGE = {
        confirmed:  'bg-success',
        requested:  'bg-warning text-dark',
        waitlisted: 'bg-secondary',
        modified:   'bg-info text-dark',
    };

    // Service shown as an icon: walk = person-walking, drop-in = house-door.
    // Default (grey) colour — it sits next to the walker name and only denotes
    // service type, so colour would convey no extra meaning. Title gives the
    // full name for hover/accessibility; unknown services fall back to text.
    function serviceIcon(slug, name) {
        if (slug === 'group-walk') return `<i class="bi bi-person-walking fs-6" title="Walk"></i>`;
        if (slug === 'drop-in')    return `<i class="bi bi-house-door fs-6" title="Drop-in"></i>`;
        return `<span class="text-muted small">${name || ''}</span>`;
    }

    // Service slug → short label + pill icon for the active-filter pill.
    const DB_SERVICE_LABEL = { 'group-walk': 'Walk', 'drop-in': 'Drop-in' };
    const DB_SERVICE_ICON  = {
        'group-walk': '<i class="bi bi-person-walking"></i>',
        'drop-in':    '<i class="bi bi-house-door"></i>',
    };

    // A brand-pink filter pill — mirrors the client-facing date pill styling.
    function dbPill(iconHtml, label, clearKey) {
        return `<span class="d-inline-flex align-items-center gap-2 px-3 py-2 fw-semibold"`
            + ` style="background:#fce8f6;color:#E02FAC;border-radius:2rem;font-size:0.82rem;">`
            + iconHtml
            + `<span>${label}</span>`
            + `<button type="button" class="btn p-0 border-0 lh-1 ms-1" data-clear="${clearKey}"`
            + ` style="background:none;color:#E02FAC;" aria-label="Clear filter">`
            + `<i class="bi bi-x-lg" style="font-size:0.75rem;"></i></button>`
            + `</span>`;
    }

    // Rebuild the active-filter pill row from the current control values.
    // Each pill's ✕ carries a data-clear hook handled by delegation below.
    function dbRenderPills() {
        const pills = [];
        if (dbFromDate.value) {
            pills.push(dbPill('<i class="bi bi-calendar3"></i>',
                              `From ${formatBcDate(dbFromDate.value)}`, 'date'));
        }
        if (dbService.value !== 'all') {
            pills.push(dbPill(DB_SERVICE_ICON[dbService.value] || '',
                              DB_SERVICE_LABEL[dbService.value] || dbService.value, 'service'));
        }
        dbPills.innerHTML = pills.join('');
        dbPills.style.display = pills.length ? 'flex' : 'none';
    }

    function dbLoad(page) {
        dbCurrentPage = page;
        const params  = new URLSearchParams({ page });
        if (dbFromDate.value) params.set('from', dbFromDate.value);
        if (dbService.value !== 'all') params.set('service', dbService.value);
        dbRenderPills();

        // First load shows the spinner; subsequent filter/page loads keep the
        // existing table mounted and just dim it, so the modal height stays
        // stable (a centered modal re-centres on height change — that jump is
        // what read as a jarring refresh).
        const firstLoad = !dbResults.querySelector('table');
        if (firstLoad) {
            dbLoading.style.display = '';
            dbResults.innerHTML = '';
            dbPagination.style.display = 'none';
        } else {
            dbResults.style.opacity = '0.4';
            dbResults.style.pointerEvents = 'none';
        }

        fetch(`/admin/dogs/${dbActiveDogId}/upcoming-bookings?${params}`, {
            headers: { 'X-CSRFToken': getCsrf() },
        })
        .then(r => r.json())
        .then(data => {
            dbLoading.style.display = 'none';
            dbResults.style.opacity = '';
            dbResults.style.pointerEvents = '';
            dbPagination.style.display = 'none';
            if (!data.success) {
                dbResults.innerHTML = `<div class="alert alert-danger py-2">${data.message || 'Error loading bookings'}</div>`;
                return;
            }
            if (!data.bookings.length) {
                dbResults.innerHTML = '<p class="text-muted small mb-0">No upcoming bookings found.</p>';
                return;
            }
            let html = '<table class="table table-sm mb-0"><thead class="table-light"><tr>'
                     + '<th>Date</th><th>Slot</th><th>Status</th><th>Service</th>'
                     + '</tr></thead><tbody>';
            data.bookings.forEach(b => {
                const badgeCls   = STATUS_BADGE[b.status] || 'bg-secondary';
                const statusInit = b.status.charAt(0).toUpperCase();
                html += `<tr>
                    <td class="align-middle">${b.date}</td>
                    <td class="align-middle">${b.slot}</td>
                    <td class="align-middle"><span class="badge ${badgeCls}" title="${b.status}">${statusInit}</span></td>
                    <td class="align-middle text-muted small">${serviceIcon(b.service_slug, b.service)} ${b.walker || '—'}</td>
                </tr>`;
            });
            html += '</tbody></table>';
            dbResults.innerHTML = html;

            if (data.total_pages > 1) {
                dbPageInfo.textContent = `Page ${data.page} of ${data.total_pages} (${data.total} total)`;
                dbPrevBtn.disabled = data.page <= 1;
                dbNextBtn.disabled = data.page >= data.total_pages;
                dbPagination.style.display = '';
            }
        })
        .catch(() => {
            dbLoading.style.display = 'none';
            dbResults.style.opacity = '';
            dbResults.style.pointerEvents = '';
            dbPagination.style.display = 'none';
            dbResults.innerHTML = '<div class="alert alert-danger py-2">Network error — please try again.</div>';
        });
    }

    document.querySelectorAll('.bookings-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            dbActiveDogId = this.dataset.dogId;
            dbDogName.textContent = this.dataset.dogName;
            // Open unfiltered — empty date (server defaults to today = all
            // upcoming) and All services. Pills appear only once narrowed.
            dbFromDate.value = '';
            dbService.value  = 'all';
            dbResults.innerHTML = '';
            dbPagination.style.display = 'none';
            dbModal.show();
            dbLoad(1);
        });
    });

    // Filter immediately on change — no explicit Filter button.
    dbFromDate.addEventListener('change', () => dbLoad(1));
    dbService.addEventListener('change', () => dbLoad(1));
    dbPrevBtn.addEventListener('click', () => dbLoad(dbCurrentPage - 1));
    dbNextBtn.addEventListener('click', () => dbLoad(dbCurrentPage + 1));

    // Clear a single filter via its pill ✕ (event delegation).
    dbPills.addEventListener('click', e => {
        const x = e.target.closest('[data-clear]');
        if (!x) return;
        if (x.dataset.clear === 'date')    dbFromDate.value = '';
        if (x.dataset.clear === 'service') dbService.value  = 'all';
        dbLoad(1);
    });

    // ── Bulk cancel modal ───────────────────────────────────────────────────
    const bcModal       = new bootstrap.Modal(document.getElementById('bulkCancelModal'));
    const bcDogName     = document.getElementById('bc-dog-name');
    const bcStart       = document.getElementById('bc-start');
    const bcEnd         = document.getElementById('bc-end');
    const bcPreviewBtn  = document.getElementById('bc-preview-btn');
    const bcLoading     = document.getElementById('bc-loading');
    const bcPreviewRes  = document.getElementById('bc-preview-results');
    const bcConfirmBtn  = document.getElementById('bc-confirm-btn');
    let   bcConfirmLbl  = document.getElementById('bc-confirm-label');
    const bcBilling     = document.getElementById('bc-billing-section');
    const bcBillingText = document.getElementById('bc-billing-text');
    const bcWaiveFee    = document.getElementById('bc-waive-fee');
    const bcRecurToggle = document.getElementById('bc-recur-toggle');
    const bcRecurPanel  = document.getElementById('bc-recurring-panel');
    const bcDateLabel   = document.getElementById('bc-date-label');
    const bcService     = document.getElementById('bc-service');
    const bcCancelNoun  = document.getElementById('bc-cancel-noun');
    const bcDayNoun     = document.getElementById('bc-day-noun');
    const bcPreviewNoun = document.getElementById('bc-preview-noun');

    let bcActiveDogId   = null;
    let bcPreviewCount  = 0;

    // Service-aware noun for the title, button, preview and success text.
    // 'all' (and any future/unknown service) falls back to "booking(s)".
    function bcNounParts() {
        if (bcService.value === 'group-walk') return ['walk', 'walks'];
        if (bcService.value === 'drop-in')    return ['drop-in', 'drop-ins'];
        return ['booking', 'bookings'];
    }
    function bcNoun(count) {
        const [singular, plural] = bcNounParts();
        return count === 1 ? singular : plural;
    }

    // Relabel the title + day-filter noun to match the selected service, and
    // invalidate any prior preview (the scope changed, so the shown count is
    // stale until the admin previews again).
    function bcSyncServiceUI() {
        const plural = bcNounParts()[1];
        bcCancelNoun.textContent  = plural;
        bcDayNoun.textContent     = plural;
        bcPreviewNoun.textContent = plural;
        bcConfirmBtn.disabled  = true;
        bcPreviewRes.innerHTML = '';
        bcPreviewCount = 0;
        resetBcBilling();
        resetBcConfirmBtn();
    }
    bcService.addEventListener('change', bcSyncServiceUI);

    // Recurring off → cancel a single walk on bc-start (end = start, no day
    // filter). Recurring on → range from bc-start to bc-end on the ticked days.
    // Mirrors the book modal's one-off vs recurring split.
    function bcIsRecurring() { return bcRecurToggle.checked; }
    function bcEffectiveRange() {
        if (bcIsRecurring()) {
            return { start: bcStart.value, end: bcEnd.value, days: bcSelectedDays() };
        }
        return { start: bcStart.value, end: bcStart.value, days: [] };
    }

    // Show/hide the end-date + day checkboxes and relabel the date field so the
    // single date reads as the range start when recurring.
    function bcSyncRecurUI() {
        const on = bcIsRecurring();
        bcRecurPanel.style.display = on ? '' : 'none';
        bcDateLabel.textContent = on ? 'Start date' : 'Date';
    }
    bcRecurToggle.addEventListener('change', function () {
        bcSyncRecurUI();
        // Switching to recurring with a date already chosen: seed the end date
        // (start + 1 year) so the range is valid without an extra click.
        if (bcIsRecurring() && bcStart.value && !bcEnd.value) {
            const d = new Date(bcStart.value + 'T00:00:00');
            d.setFullYear(d.getFullYear() + 1);
            bcEnd.value = d.toISOString().slice(0, 10);
            bcEnd.min   = bcStart.value;
        }
        // A range/day change invalidates a prior preview — force a re-preview.
        bcConfirmBtn.disabled = true;
        bcPreviewRes.innerHTML = '';
        resetBcBilling();
    });

    // Hide + reset the late-fee notice. Called when (re)opening the modal and
    // whenever a fresh preview is requested so a stale late-count can't leak
    // into the next cancel.
    function resetBcBilling() {
        bcBilling.style.display = 'none';
        bcWaiveFee.checked = false;
    }

    // Restore the confirm button to its idle markup and re-acquire the label
    // node. Setting innerHTML (for the "Cancelling…" spinner) destroys the
    // original #bc-confirm-label span, so the cached reference goes stale —
    // resetting here keeps it valid for the next preview/cancel cycle.
    function resetBcConfirmBtn() {
        bcConfirmBtn.innerHTML = '<i class="bi bi-x-square me-1"></i><span id="bc-confirm-label">Cancel ' + bcNounParts()[1] + '</span>';
        bcConfirmLbl = document.getElementById('bc-confirm-label');
    }

    // Returns array — empty for "Both" (server treats missing/empty as all slots).
    function bcSelectedSlots() {
        const v = document.querySelector('input[name="bc-slot"]:checked')?.value;
        return (v === 'Morning' || v === 'Afternoon') ? [v] : [];
    }

    // Returns array of ints 0..4 for the checked day boxes. Server treats
    // empty as no filter (matches slots) — UI defaults to all 5 checked.
    function bcSelectedDays() {
        return Array.from(document.querySelectorAll('.bc-day:checked'))
            .map(el => parseInt(el.value, 10));
    }

    document.querySelectorAll('.cancel-range-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            bcActiveDogId = this.dataset.dogId;
            bcDogName.textContent = this.dataset.dogName;
            bcStart.value = '';
            bcEnd.value   = '';
            bcService.value = 'all';
            document.getElementById('bc-slot-both').checked = true;
            document.querySelectorAll('.bc-day').forEach(el => { el.checked = true; });
            bcRecurToggle.checked = false;
            bcSyncRecurUI();
            bcSyncServiceUI();              // sets nouns + clears any prior preview
            bcConfirmBtn.disabled = true;   // always visible, enabled only after a preview
            bcModal.show();
        });
    });

    // When recurring, auto-fill end date to start + 1 year (mirrors the booking
    // modal). In one-off mode there is no end field to fill.
    bcStart.addEventListener('change', function () {
        if (!this.value || !bcIsRecurring()) return;
        const d = new Date(this.value + 'T00:00:00');
        d.setFullYear(d.getFullYear() + 1);
        bcEnd.value = d.toISOString().slice(0, 10);
        bcEnd.min   = this.value;
    });

    function formatBcDate(iso) {
        const [y, m, d] = iso.split('-').map(Number);
        const dt = new Date(y, m - 1, d);
        const day = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][dt.getDay()];
        const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][dt.getMonth()];
        return `${day}, ${d} ${mon}`;
    }

    bcPreviewBtn.addEventListener('click', function () {
        if (!bcStart.value) {
            bcPreviewRes.innerHTML = '<div class="alert alert-warning py-2">Please pick a date.</div>';
            return;
        }
        if (bcIsRecurring() && !bcEnd.value) {
            bcPreviewRes.innerHTML = '<div class="alert alert-warning py-2">Please enter an end date.</div>';
            return;
        }
        const range  = bcEffectiveRange();
        const params = new URLSearchParams({ start: range.start, end: range.end });
        bcSelectedSlots().forEach(s => params.append('slots', s));
        range.days.forEach(d => params.append('days', d));
        params.set('service', bcService.value);
        bcLoading.style.display = '';
        bcPreviewRes.innerHTML  = '';
        bcConfirmBtn.disabled = true;
        resetBcBilling();

        fetch(`/admin/dogs/${bcActiveDogId}/cancel-preview?${params}`, {
            headers: { 'X-CSRFToken': getCsrf() },
        })
        .then(r => r.json())
        .then(data => {
            bcLoading.style.display = 'none';
            if (!data.success) {
                bcPreviewRes.innerHTML = `<div class="alert alert-danger py-2">${data.message || 'Error'}</div>`;
                return;
            }
            bcPreviewCount = data.count;
            if (data.count === 0) {
                bcPreviewRes.innerHTML = `<p class="text-muted small mb-0">No active ${bcNounParts()[1]} found in that date range.</p>`;
                return;
            }
            // The payload is capped server-side; if more in scope than we
            // received, flag that this is a preview of the first N shown.
            const previewNote = data.count > data.bookings.length
                ? `, here is a preview of the first ${data.bookings.length}`
                : '';
            let html = `<p class="fw-semibold mb-2 text-danger"><i class="bi bi-exclamation-triangle me-1"></i>${data.count} ${bcNoun(data.count)} will be cancelled${previewNote}:</p>`;
            html += '<table class="table table-sm mb-0"><thead class="table-light"><tr>'
                  + '<th>Date</th><th>Slot</th><th>Status</th></tr></thead><tbody>';
            data.bookings.forEach(b => {
                const badgeCls = STATUS_BADGE[b.status] || 'bg-secondary';
                html += `<tr>
                    <td>${formatBcDate(b.date)}</td>
                    <td>${b.slot}</td>
                    <td><span class="badge ${badgeCls}">${b.status}</span></td>
                </tr>`;
            });
            html += '</tbody></table>';
            bcPreviewRes.innerHTML = html;
            bcConfirmLbl.textContent = `Cancel ${data.count} ${bcNoun(data.count)}`;
            bcConfirmBtn.disabled = false;

            // Late-cancel billing: walks inside the notice window bill the client
            // by default. Offer a waive checkbox only when some are late.
            if (data.late_count > 0) {
                const n = data.late_count;
                bcBillingText.textContent =
                    `${n} of these ${n === 1 ? 'is' : 'are'} within the late-cancellation window `
                    + `and will be billed. Tick to waive the late fee for ${n === 1 ? 'it' : 'them'}.`;
                bcBilling.style.display = '';
            }
        })
        .catch(() => {
            bcLoading.style.display = 'none';
            bcPreviewRes.innerHTML = '<div class="alert alert-danger py-2">Network error — please try again.</div>';
        });
    });

    bcConfirmBtn.addEventListener('click', function () {
        if (!bcActiveDogId || !bcStart.value) return;
        if (bcIsRecurring() && !bcEnd.value) return;
        bcConfirmBtn.disabled = true;
        bcConfirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Cancelling…';

        const range = bcEffectiveRange();
        fetch(`/admin/dogs/${bcActiveDogId}/bulk-cancel`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
            body: JSON.stringify({
                start: range.start,
                end: range.end,
                slots: bcSelectedSlots(),
                days: range.days,
                service: bcService.value,
                waive_late_fee: bcWaiveFee.checked,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                const n = data.cancelled_count;
                showConfirmed(`${n} ${bcNoun(n)} cancelled for ${bcDogName.textContent}.`,
                              bcModal, document.getElementById('bulkCancelModal'));
            } else {
                bcPreviewRes.innerHTML = `<div class="alert alert-danger py-2">${data.message || 'Something went wrong'}</div>`;
                bcConfirmBtn.disabled = false;
                resetBcConfirmBtn();
            }
        })
        .catch(() => {
            bcPreviewRes.innerHTML = '<div class="alert alert-danger py-2">Network error — please try again.</div>';
            bcConfirmBtn.disabled = false;
            resetBcConfirmBtn();
        });
    });
});
