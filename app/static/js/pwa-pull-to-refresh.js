/**
 * Pull-to-refresh for installed PWAs (iOS + Android).
 *
 * iOS Safari strips the native gesture when launched from the home screen
 * (display-mode: standalone), and brand.css sets overscroll-behavior-y: contain
 * which also suppresses Chrome's native PTR on Android — so we render our own
 * indicator and reload the page when the user pulls past THRESHOLD.
 *
 * Detect standalone via both signals: navigator.standalone (iOS-only, legacy)
 * and matchMedia('(display-mode: standalone)') (Android Chrome + modern iOS).
 *
 * Opt out per-element with data-no-ptr (e.g. on horizontally-scrolling lists).
 * A pull never starts while a modal is open, or from inside an element that is
 * scrolled down (it scrolls itself back up instead).
 */
(function () {
    var isStandalone = window.navigator.standalone === true ||
                       window.matchMedia('(display-mode: standalone)').matches;
    if (!isStandalone) return;

    var THRESHOLD = 70;
    var MAX_PULL  = 120;
    var startY = 0;
    var pulling = false;
    var pullDistance = 0;
    var indicator = null;
    var icon = null;

    function ensureIndicator() {
        if (indicator) return;
        indicator = document.createElement('div');
        indicator.className = 'ptr-indicator';
        indicator.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
        document.body.appendChild(indicator);
        icon = indicator.querySelector('i');
    }

    function show(distance) {
        ensureIndicator();
        indicator.classList.remove('ptr-springback');
        indicator.style.transform = 'translateY(' + (distance - 60) + 'px)';
        indicator.style.opacity = Math.min(1, distance / THRESHOLD);
        icon.style.transform = 'rotate(' + (distance * 3) + 'deg)';
    }

    function hide() {
        if (!indicator) return;
        indicator.classList.add('ptr-springback');
        indicator.style.transform = 'translateY(-60px)';
        indicator.style.opacity = '0';
    }

    function startRefresh() {
        ensureIndicator();
        indicator.classList.remove('ptr-springback');
        indicator.classList.add('ptr-refreshing');
        indicator.style.transform = 'translateY(20px)';
        indicator.style.opacity = '1';
        window.location.reload();
    }

    // True when the touch starts inside an element that is itself scrolled
    // down (a scrollable modal body, the day-detail walker list, the bell
    // list…). Dragging down there should scroll that element back up, not
    // pull the page — window.scrollY alone can't tell, since the page behind
    // stays at the top.
    function insideScrolledElement(el) {
        for (; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
            if (el.scrollTop > 0) return true;
        }
        return false;
    }

    document.addEventListener('touchstart', function (e) {
        if (window.scrollY > 0) return;
        // Never reload behind an open modal — it would discard whatever the
        // user was doing in it (Bootstrap sets .modal-open on <body>).
        if (document.body.classList.contains('modal-open')) return;
        if (insideScrolledElement(e.target)) return;
        if (e.target.closest && e.target.closest('[data-no-ptr]')) return;
        startY = e.touches[0].clientY;
        pulling = true;
        pullDistance = 0;
    }, { passive: true });

    document.addEventListener('touchmove', function (e) {
        if (!pulling) return;
        if (window.scrollY > 0) { pulling = false; hide(); return; }
        var dy = e.touches[0].clientY - startY;
        if (dy <= 0) { pulling = false; hide(); return; }
        // Damped pull — feels like rubber band
        pullDistance = Math.min(MAX_PULL, dy * 0.5);
        show(pullDistance);
        if (e.cancelable) e.preventDefault();
    }, { passive: false });

    document.addEventListener('touchend', function () {
        if (!pulling) return;
        pulling = false;
        if (pullDistance >= THRESHOLD) {
            startRefresh();
        } else {
            hide();
        }
        pullDistance = 0;
    }, { passive: true });
}());
