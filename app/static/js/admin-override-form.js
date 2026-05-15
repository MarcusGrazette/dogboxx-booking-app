/**
 * admin-override-form.js — schedule override form component.
 *
 * Usage:
 *   const form = createOverrideForm({
 *       batchUrl:        '/walker/schedule-changes/batch',
 *       batchDeleteUrl:  '/walker/schedule-changes/batch-delete',
 *       listUrl:         '/admin/api/schedule-changes',  // ?walker_id=N appended
 *       walkerId:        42,
 *       todayIso:        '2026-05-19',
 *       initialDate:     '2026-05-22',                   // optional
 *       onChange:        () => {},                       // optional — fires after every successful add/delete
 *   });
 *
 * The form is expected to be in the DOM at the time of this call, using the
 * IDs defined in partials/admin_walker_overrides_form.html.
 */
(function (global) {
    'use strict';

    function createOverrideForm(cfg) {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content;

        const $startDate    = document.getElementById('sched-start-date');
        const $endDateWrap  = document.getElementById('sched-end-date-wrap');
        const $endDate      = document.getElementById('sched-end-date');
        const $rangeToggle  = document.getElementById('sched-range-toggle');
        const $reason       = document.getElementById('sched-reason');
        const $addBtn       = document.getElementById('sched-add-btn');
        const $status       = document.getElementById('sched-status');
        const $listWrap     = document.getElementById('sched-list-container');

        // Set min/max bounds (today → today + 1 year) on both date inputs.
        // The standalone page used Jinja to inject these — the JS does it now
        // so the partial is purely structural.
        if (cfg.todayIso && $startDate) {
            const today    = new Date(cfg.todayIso + 'T00:00:00');
            const oneYear  = new Date(today); oneYear.setFullYear(today.getFullYear() + 1);
            const isoYear  = oneYear.toISOString().slice(0, 10);
            $startDate.min = cfg.todayIso;
            $startDate.max = isoYear;
            if ($endDate) { $endDate.min = cfg.todayIso; $endDate.max = isoYear; }
        }
        if (cfg.initialDate && $startDate && !$startDate.value) {
            $startDate.value = cfg.initialDate;
        }

        function setStatus(message, kind) {
            const classes = {
                success: 'small text-success',
                error:   'small text-danger',
                pending: 'small text-muted',
            };
            $status.className   = classes[kind] || 'small text-muted';
            $status.textContent = message || '';
        }

        function listUrlFor() {
            return `${cfg.listUrl}?walker_id=${cfg.walkerId}`;
        }

        function bindShowMore() {
            const btn = document.getElementById('sched-show-more-btn');
            if (!btn) return;
            btn.addEventListener('click', () => {
                document.querySelectorAll('.sched-change-extra').forEach(el => el.classList.remove('d-none'));
                document.getElementById('sched-show-more')?.remove();
            });
        }

        function bindDeleteButtons() {
            document.querySelectorAll('.sched-delete-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const row = btn.closest('.sched-change-row');
                    if (!row) return;
                    const summary    = row.dataset.summary;
                    const adhocIds   = (row.dataset.adhocIds   || '').split(',').filter(Boolean).map(Number);
                    const unavailIds = (row.dataset.unavailIds || '').split(',').filter(Boolean).map(Number);
                    const total = adhocIds.length + unavailIds.length;
                    if (!confirm(total > 1 ? `Delete ${total} entries (${summary})?` : `Delete this entry (${summary})?`)) return;

                    fetch(cfg.batchDeleteUrl, {
                        method:  'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
                        body:    JSON.stringify({ adhoc_ids: adhocIds, unavail_ids: unavailIds, walker_id: cfg.walkerId }),
                    })
                        .then(r => r.json())
                        .then(data => {
                            if (data.success) {
                                refreshList();
                                if (cfg.onChange) cfg.onChange();
                            } else {
                                setStatus('Failed to delete — please try again.', 'error');
                            }
                        })
                        .catch(() => setStatus('Network error.', 'error'));
                });
            });
        }

        function refreshList() {
            fetch(listUrlFor())
                .then(r => r.text())
                .then(html => {
                    $listWrap.innerHTML = html;
                    bindShowMore();
                    bindDeleteButtons();
                });
        }

        async function submit() {
            const startDate  = $startDate.value;
            const endDateRaw = $endDate ? $endDate.value : '';
            const endDate    = endDateRaw || startDate;
            const slotEl     = document.querySelector('input[name="sched-slot"]:checked');
            const typeEl     = document.querySelector('input[name="sched-type"]:checked');
            const reason     = $reason.value;

            if (!startDate) { setStatus('Pick a date.', 'error'); return; }
            if (!slotEl)    { setStatus('Pick a slot.', 'error'); return; }
            if (!typeEl)    { setStatus('Pick Available or Unavailable.', 'error'); return; }

            const slots = slotEl.value === 'Both' ? ['Morning', 'Afternoon'] : [slotEl.value];
            const type  = typeEl.value;

            $addBtn.disabled = true;
            setStatus('Saving…', 'pending');

            try {
                const resp = await fetch(cfg.batchUrl, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
                    body:    JSON.stringify({
                        start_date: startDate, end_date: endDate,
                        slots, type, reason,
                        walker_id: cfg.walkerId,
                    }),
                });
                const data = await resp.json();
                if (data.success) {
                    setStatus(data.message, 'success');
                    $startDate.value = '';
                    if ($endDate) $endDate.value = '';
                    $reason.value = '';
                    refreshList();
                    if (cfg.onChange) cfg.onChange();
                } else {
                    setStatus(data.message || 'Error saving changes.', 'error');
                }
            } catch (err) {
                setStatus(`Request failed: ${err.message}`, 'error');
            } finally {
                $addBtn.disabled = false;
            }
        }

        // Range toggle (single date ↔ date range)
        if ($rangeToggle && $endDateWrap) {
            $rangeToggle.addEventListener('click', e => {
                e.preventDefault();
                $endDateWrap.classList.toggle('d-none');
                if ($endDateWrap.classList.contains('d-none')) {
                    $rangeToggle.textContent = '+ or a date range';
                    if ($endDate) $endDate.value = '';
                } else {
                    $rangeToggle.textContent = '– single date';
                    if ($endDate && !$endDate.value && $startDate.value) $endDate.value = $startDate.value;
                }
            });
        }
        if ($startDate && $endDate) {
            $startDate.addEventListener('change', () => {
                if ($endDate.value && $endDate.value < $startDate.value) $endDate.value = $startDate.value;
                $endDate.min = $startDate.value || $endDate.min;
            });
        }
        $addBtn.addEventListener('click', submit);

        // First load
        refreshList();

        return { refresh: refreshList };
    }

    global.createOverrideForm = createOverrideForm;

}(window));
