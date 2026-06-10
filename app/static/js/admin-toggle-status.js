/**
 * Toggle active/deactive status for any admin-managed entity.
 * @param {string} entityType - e.g. 'clients' or 'walkers'
 * @param {number} entityId - the record ID
 * @param {string} action - 'activate' or 'deactivate'
 */

/**
 * Fetch-only variant: POSTs the toggle and resolves with the parsed JSON.
 * Does not reload or alert — lets callers chain their own success UX (e.g. the
 * shared success modal). Rejects on network error.
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

/**
 * Convenience wrapper: toggle then reload on success, alert on failure.
 * Used by pages that don't show their own success feedback.
 */
async function toggleStatus(entityType, entityId, action) {
    try {
        const data = await toggleStatusRequest(entityType, entityId, action);

        if (data.success) {
            location.reload();
        } else {
            alert('Error: ' + data.message);
        }
    } catch (error) {
        console.error('Error:', error);
        alert('An error occurred. Please try again.');
    }
}
