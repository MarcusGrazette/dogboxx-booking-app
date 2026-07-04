/**
 * showToast(message, type)
 * Programmatic toast for AJAX responses — uses the same #db-toast-container
 * and CSS as flash_toasts.html so they look identical. Shared by both
 * layout.html (client) and admin_layout.html.
 * type: 'success' | 'info' | 'warning' | 'danger' | 'error'
 */
(function () {
    var ICONS = {
        success: 'bi-check-circle-fill',
        info: 'bi-info-circle-fill',
        warning: 'bi-exclamation-triangle-fill',
        error: 'bi-x-circle-fill',
        danger: 'bi-x-circle-fill',
    };
    var TIMEOUTS = { success: 3000, info: 4000, warning: 6000, error: 0, danger: 0 };

    function ensureContainer() {
        var c = document.getElementById('db-toast-container');
        if (c) return c;
        c = document.createElement('div');
        c.id = 'db-toast-container';
        c.setAttribute('aria-live', 'polite');
        c.setAttribute('aria-atomic', 'false');
        // No inline styles — brand.css handles all positioning (mobile + desktop
        // media query). Inline styles would override the media query and break
        // desktop centering.
        document.body.appendChild(c);
        return c;
    }

    function dismiss(toast) {
        if (toast._dismissed) return;
        toast._dismissed = true;
        toast.classList.add('db-toast-out');
        toast.addEventListener('animationend', function () {
            toast.remove();
        }, { once: true });
    }

    window.showToast = function (msg, type) {
        type = type || 'info';
        var cat = (type === 'danger') ? 'error' : type;
        var icon = ICONS[type] || ICONS.info;
        var ms = TIMEOUTS[type] !== undefined ? TIMEOUTS[type] : 4000;

        var toast = document.createElement('div');
        toast.className = 'db-toast db-toast-' + cat;
        toast.setAttribute('role', cat === 'error' ? 'alert' : 'status');
        toast.dataset.timeout = ms;
        toast.innerHTML =
            '<i class="bi ' + icon + ' db-toast-icon" aria-hidden="true"></i>' +
            '<span class="db-toast-msg"></span>' +
            (cat === 'error' ? '<button class="db-toast-close" aria-label="Dismiss">&times;</button>' : '');
        toast.querySelector('.db-toast-msg').textContent = msg;

        var container = ensureContainer();
        container.appendChild(toast);

        var timer = null;
        function startTimer(delay) {
            if (ms > 0) timer = setTimeout(function () { dismiss(toast); }, delay || ms);
        }
        startTimer();

        toast.addEventListener('mouseenter', function () { clearTimeout(timer); });
        toast.addEventListener('mouseleave', function () { startTimer(2000); });

        var closeBtn = toast.querySelector('.db-toast-close');
        if (closeBtn) closeBtn.addEventListener('click', function () { dismiss(toast); });
    };
}());
