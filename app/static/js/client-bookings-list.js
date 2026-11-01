/* Client home — the per-dog bookings lists, their pager and the calendar date
 * filter. Used by client-home.js (booking forms, cancel, calendar clicks);
 * kept separate so it can be tested under Node (tests/test_client_bookings_list.py).
 *
 * Each dog card has:
 *   #bookings-list-<dog>       upcoming bookings (today onwards), server-rendered
 *   #past-bookings-list-<dog>  one past day's rows, fetched from /day_bookings/<date>
 *                              when a past calendar date is tapped
 *   #walk-pager-<dog>, #dog-empty-<dog>, #dog-filter-empty-<dog>
 *
 * refreshList() is the one place that orders the upcoming list and renumbers
 * it. Every change (book, cancel) goes through it, so data-walk-idx,
 * data-total, the pager and the empty state can't drift apart.
 */
window.BookingsList = (function () {
    const PAGE_SIZE = 10;
    const SLOT_RANK = { Morning: 0, Afternoon: 1 };  // pricing.SLOT_ORDER
    const ROW_CLASS = 'list-group-item px-0 py-3 border-bottom d-flex align-items-center justify-content-between gap-2';

    let dayBookingsUrl = null;  // '/day_bookings/0000-00-00'
    const walkPages = {};       // dogId → current page (0-indexed)
    let filterDate = null;      // 'YYYY-MM-DD' while the date filter is on
    let filterLabelText = '';
    let pastSeq = 0;            // latest past-day request; older replies are ignored

    const byId = id => document.getElementById(id);
    const escHtml = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function dogIds() {
        return Array.from(document.querySelectorAll('ul[data-bookings-list]'), ul => ul.dataset.dogId);
    }

    function upcomingRows(dogId) {
        const list = byId(`bookings-list-${dogId}`);
        return list ? Array.from(list.children) : [];
    }

    function compareRows(a, b) {
        if (a.dataset.date !== b.dataset.date) return a.dataset.date < b.dataset.date ? -1 : 1;
        return (SLOT_RANK[a.dataset.slot] ?? 9) - (SLOT_RANK[b.dataset.slot] ?? 9);
    }

    function setHidden(id, hidden) {
        const el = byId(id);
        if (el) el.classList.toggle('d-none', hidden);
    }

    // ─── Upcoming list ──────────────────────────────────────────────────────
    function refreshList(dogId) {
        const list = byId(`bookings-list-${dogId}`);
        if (!list) return;
        const rows = upcomingRows(dogId).sort(compareRows);
        rows.forEach((li, i) => {
            li.dataset.walkIdx = String(i);
            list.appendChild(li);  // re-appending in order sorts the DOM
        });
        list.dataset.total = String(rows.length);
        const lastPage = Math.max(0, Math.ceil(rows.length / PAGE_SIZE) - 1);
        walkPages[dogId] = Math.min(walkPages[dogId] || 0, lastPage);

        if (!filterDate) renderWalkPage(dogId);
        else if (filterDate >= DogBoxxDates.today()) filterUpcomingList(dogId);
    }

    function renderWalkPage(dogId) {
        if (filterDate) return; // the filter controls visibility instead
        const rows = upcomingRows(dogId);
        const total = rows.length;
        const page = walkPages[dogId] || 0;
        const start = page * PAGE_SIZE;
        const end = Math.min(start + PAGE_SIZE, total);

        rows.forEach(li => {
            const idx = parseInt(li.dataset.walkIdx, 10);
            li.classList.toggle('d-none', idx < start || idx >= end);
        });
        setHidden(`walk-pager-${dogId}`, total <= PAGE_SIZE);
        setHidden(`dog-empty-${dogId}`, total > 0);

        const label = byId(`walk-page-label-${dogId}`);
        if (label) label.textContent = `${start + 1} – ${end} of ${total}`;
        const prevBtn = byId(`walk-prev-${dogId}`);
        if (prevBtn) prevBtn.disabled = (page === 0);
        const nextBtn = byId(`walk-next-${dogId}`);
        if (nextBtn) nextBtn.disabled = (end >= total);
    }

    function turnPage(dogId, delta) {
        const lastPage = Math.max(0, Math.ceil(upcomingRows(dogId).length / PAGE_SIZE) - 1);
        walkPages[dogId] = Math.min(Math.max((walkPages[dogId] || 0) + delta, 0), lastPage);
        renderWalkPage(dogId);
    }

    // ─── Date filter ────────────────────────────────────────────────────────
    function applyDateFilter(isoDate) {
        filterDate = isoDate;
        const d = new Date(isoDate + 'T00:00:00');
        filterLabelText = d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
        const filterLabel = byId('date-filter-label');
        if (filterLabel) filterLabel.textContent = filterLabelText;
        const filterBar = byId('date-filter-bar');
        if (filterBar) filterBar.style.display = '';

        const isPast = isoDate < DogBoxxDates.today();
        dogIds().forEach(dogId => {
            const subtitle = byId(`dog-subtitle-${dogId}`);
            if (subtitle) subtitle.textContent = filterLabelText;
            setHidden(`walk-pager-${dogId}`, true);
            setHidden(`dog-empty-${dogId}`, true);
            clearPastRows(dogId);
            if (isPast) {
                // The page only carries upcoming bookings; the past day's rows
                // come from the server (loadPastDay below).
                upcomingRows(dogId).forEach(li => li.classList.add('d-none'));
                setFilterEmpty(dogId, false);
            } else {
                filterUpcomingList(dogId);
            }
        });
        if (isPast) loadPastDay(isoDate);
    }

    function clearDateFilter() {
        filterDate = null;
        pastSeq++; // drop any past-day reply still in flight
        const filterBar = byId('date-filter-bar');
        if (filterBar) filterBar.style.display = 'none';
        dogIds().forEach(dogId => {
            const subtitle = byId(`dog-subtitle-${dogId}`);
            if (subtitle) subtitle.textContent = 'Upcoming walks';
            setFilterEmpty(dogId, false);
            clearPastRows(dogId);
            renderWalkPage(dogId);
        });
    }

    // Filter on today or a future date: show only that day's upcoming rows.
    function filterUpcomingList(dogId) {
        let matches = 0;
        upcomingRows(dogId).forEach(li => {
            const match = li.dataset.date === filterDate;
            li.classList.toggle('d-none', !match);
            if (match) matches++;
        });
        setFilterEmpty(dogId, matches === 0);
    }

    function setFilterEmpty(dogId, show) {
        const box = byId(`dog-filter-empty-${dogId}`);
        if (!box) return;
        box.style.display = show ? '' : 'none';
        const text = byId(`dog-filter-empty-text-${dogId}`);
        if (show && text) {
            text.innerHTML =
                `<i class="bi bi-calendar2-x me-2"></i>No bookings on ${escHtml(filterLabelText)}. ` +
                `<button type="button" class="btn btn-link p-0 border-0 align-baseline inline-filter-clear" ` +
                `style="font-size:inherit;color:#E02FAC;vertical-align:baseline;">Clear the filter</button> to see all bookings.`;
        }
    }

    function clearPastRows(dogId) {
        const past = byId(`past-bookings-list-${dogId}`);
        if (past) past.innerHTML = '';
    }

    // Filter on a past date: fetch that day. Only the latest request's reply
    // is used — tapping two past dates quickly must not show the first day
    // under the second day's label.
    function loadPastDay(isoDate) {
        const seq = ++pastSeq;
        const stale = () => seq !== pastSeq || filterDate !== isoDate;
        fetch(dayBookingsUrl.replace('0000-00-00', isoDate))
            .then(r => r.json())
            .then(data => {
                if (stale()) return;
                if (!data.success) throw new Error(data.message);
                dogIds().forEach(dogId => {
                    const rows = data.bookings.filter(b => String(b.dog_id) === String(dogId));
                    const past = byId(`past-bookings-list-${dogId}`);
                    if (past) {
                        past.innerHTML = rows.map(b =>
                            `<li class="${ROW_CLASS}">${rowHtml(b, { editable: false })}</li>`).join('');
                    }
                    setFilterEmpty(dogId, rows.length === 0);
                });
            })
            .catch(() => {
                if (stale()) return;
                showToast('Could not load bookings for that day. Please try again.', 'danger');
            });
    }

    // ─── Rows ───────────────────────────────────────────────────────────────
    // Inner HTML of one booking row (the server renders the same markup in
    // index.html). Upcoming rows (editable: true) get the note pencil and the
    // cancel button. Past rows are view-only: a file icon opens the note
    // read-only, and there's no icon at all when the walk has no note.
    function rowHtml(b, { editable, isNew = false }) {
        const badgeHtml = b.status === 'waitlisted'
            ? `<span class="badge rounded-pill text-bg-info py-2 text-center" style="min-width:90px;">Waitlisted</span>`
            : b.status === 'confirmed'
                ? `<span class="badge rounded-pill text-bg-success py-2 text-center" style="min-width:90px;">Confirmed</span>`
                : `<span class="badge rounded-pill py-2 text-center" style="min-width:90px; background-color:#fff3cd; color:#856404;">Requested</span>`;
        const newBadge = isNew ? `<span class="badge bg-pink ms-1 new-booking-badge"
            style="background:#E02FAC;font-size:0.7em;vertical-align:middle;
                   transition:opacity 0.6s ease;">New</span>` : '';
        const walkerLabel = (b.status === 'confirmed' && b.walker_name)
            ? ` <span class="text-muted">(${escHtml(b.walker_name)})</span>` : '';
        const dropInLabel = b.is_drop_in
            ? `<i class="bi bi-house-door-fill text-primary me-1"></i>Drop-in · ` : '';
        const note = b.note || '';
        const actions = editable ? `
            <button class="btn btn-link p-1 note-booking-btn text-secondary"
                    data-booking-id="${escHtml(b.id)}"
                    data-booking-slot="${escHtml(b.slot)}"
                    data-booking-date="${escHtml(b.date_display)}"
                    data-current-note="${escHtml(note)}"
                    title="${note ? 'Edit note' : 'Add note'}"
                    style="line-height:1;font-size:1.1rem;">
                <i class="bi bi-pencil-square${note ? ' text-primary' : ''}"></i>
            </button>
            <button class="btn btn-link p-1 cancel-booking-btn text-danger"
                    data-booking-id="${escHtml(b.id)}"
                    data-booking-slot="${escHtml(b.slot)}"
                    data-booking-date="${escHtml(b.date_display)}"
                    data-booking-date-iso="${escHtml(b.date_iso)}"
                    data-booking-status="${escHtml(b.status)}"
                    data-is-drop-in="${b.is_drop_in ? 'true' : 'false'}"
                    title="Cancel booking"
                    style="line-height:1;font-size:1.1rem;">
                <i class="bi bi-x-circle"></i>
            </button>` : note ? `
            <button class="btn btn-link p-1 note-view-btn text-primary"
                    data-booking-slot="${escHtml(b.slot)}"
                    data-booking-date="${escHtml(b.date_display)}"
                    data-current-note="${escHtml(note)}"
                    title="View note"
                    style="line-height:1;font-size:1.1rem;">
                <i class="bi bi-file-earmark"></i>
            </button>` : '';
        return `
        <div class="d-flex align-items-center gap-3">
            ${badgeHtml}
            <div>
                <div class="fw-semibold small">${escHtml(b.date_display)}${newBadge}</div>
                <div class="text-muted" style="font-size:0.78rem;">${dropInLabel}${escHtml(b.slot)}${walkerLabel}</div>
            </div>
        </div>
        <div class="d-flex align-items-center gap-3">${actions}
        </div>`;
    }

    // A booking just made on this page. Returns false if its dog's list isn't
    // on the page (the caller reloads).
    function injectBooking(booking) {
        const list = byId(`bookings-list-${booking.dog_id}`);
        if (!list) return false;

        const li = document.createElement('li');
        li.className = ROW_CLASS;
        li.dataset.walkIdx = '0';  // renumbered by refreshList
        li.dataset.date = booking.date_iso;
        li.dataset.slot = booking.slot;
        li.innerHTML = rowHtml(booking, { editable: true, isNew: true });
        li.style.opacity = '0';
        li.style.transition = 'opacity 0.3s ease';
        list.appendChild(li);
        refreshList(String(booking.dog_id));
        requestAnimationFrame(() => { li.style.opacity = '1'; });

        // Fade out the "New" badge after 4s
        setTimeout(() => {
            const badge = li.querySelector('.new-booking-badge');
            if (badge) {
                badge.style.opacity = '0';
                setTimeout(() => badge.remove(), 600);
            }
        }, 4000);
        return true;
    }

    // A cancelled booking's row: fade it out, then remove it and refresh.
    function removeRow(li) {
        const list = li.parentNode;
        const dogId = list && list.dataset.dogId;
        li.style.transition = 'opacity 0.3s ease';
        li.style.opacity = '0';
        setTimeout(() => {
            if (li.parentNode) li.parentNode.removeChild(li);
            if (dogId) refreshList(dogId);
        }, 300);
    }

    return {
        configure(opts) { dayBookingsUrl = opts.dayBookingsUrl; },
        applyDateFilter, clearDateFilter, turnPage, refreshList, injectBooking, removeRow, escHtml,
    };
})();
