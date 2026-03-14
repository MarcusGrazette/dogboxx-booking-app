/**
 * Toggle active/deactive status for any admin-managed entity.
 * @param {string} entityType - e.g. 'clients' or 'walkers'
 * @param {number} entityId - the record ID
 * @param {string} action - 'activate' or 'deactivate'
 */
async function toggleStatus(entityType, entityId, action) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;

    try {
        const response = await fetch(`/admin/${entityType}/${entityId}/${action}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {})
            },
            credentials: 'same-origin'
        });

        const data = await response.json();

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
