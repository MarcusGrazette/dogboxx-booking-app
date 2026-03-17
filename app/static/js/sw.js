/**
 * Dogboxx Service Worker — Web Push handler
 *
 * Responsibilities:
 *   - Receive push events and show native OS notifications
 *   - Handle notification clicks → focus/open the app at the right URL
 *   - Double-notify guard: skip the OS notification if a Dogboxx tab is
 *     already focused (SSE is already delivering the update in real time)
 *
 * Served from /sw.js (root scope) via a Flask route.
 */

const APP_NAME = 'Dogboxx';
const DEFAULT_ICON  = '/static/android-chrome-192x192.png';
const DEFAULT_BADGE = '/static/favicon-32x32.png';

// ── Push received ─────────────────────────────────────────────────────────────

self.addEventListener('push', function (event) {
    let payload = {};

    if (event.data) {
        try {
            payload = event.data.json();
        } catch (e) {
            // Plain-text fallback
            payload = { title: event.data.text() };
        }
    }

    const title   = payload.title  || APP_NAME;
    const options = {
        body:    payload.body   || '',
        icon:    payload.icon   || DEFAULT_ICON,
        badge:   DEFAULT_BADGE,
        data:    { link: payload.link || '/' },
        tag:     payload.tag    || 'dogboxx-notification',   // replaces stale notification of same type
        renotify: false,
    };

    // Double-notify guard: if a Dogboxx tab is already visible, skip the OS
    // notification — the user is watching the app and SSE will update the bell.
    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(function (clients) {
                const appIsVisible = clients.some(function (c) {
                    return c.visibilityState === 'visible';
                });

                if (appIsVisible) {
                    // App is open and focused — SSE handles this, skip OS push
                    return;
                }

                return self.registration.showNotification(title, options);
            })
    );
});

// ── Notification clicked ──────────────────────────────────────────────────────

self.addEventListener('notificationclick', function (event) {
    event.notification.close();

    const targetUrl = (event.notification.data && event.notification.data.link)
        ? event.notification.data.link
        : '/';

    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(function (clients) {
                // If a Dogboxx tab is already open, focus it and navigate
                for (const client of clients) {
                    if ('focus' in client) {
                        client.focus();
                        if ('navigate' in client) {
                            client.navigate(targetUrl);
                        }
                        return;
                    }
                }
                // No open tab — open a new one
                if (self.clients.openWindow) {
                    return self.clients.openWindow(targetUrl);
                }
            })
    );
});
