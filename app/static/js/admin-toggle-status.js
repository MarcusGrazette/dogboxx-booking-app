/**
 * Toggle active/deactive status for any admin-managed entity.
 *
 * Fetch-only: POSTs the toggle and resolves with the parsed JSON. Does not
 * reload or alert — callers chain their own success UX (the shared success
 * modal — see docs/UX_GUIDE.md §1). Rejects on network error.
 *
 * @param {string} entityType - e.g. 'clients' or 'walkers'
 * @param {number} entityId - the record ID
 * @param {string} action - 'activate' or 'deactivate'
 */
async function toggleStatusRequest(entityType, entityId, action) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
    const response = await fetch(`/admin/${entityType}/${entityId}/${action}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {})
        },
        credentials: 'same-origin'
    });
    return response.json();
}
