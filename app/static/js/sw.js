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

// ── Lifecycle ─────────────────────────────────────────────────────────────────
//
// skipWaiting: activate the new SW immediately instead of waiting for all
//   tabs to close. Without this, a SW update sits in "waiting" state and
//   the old version keeps handling events (including push + badge calls).
//
// clients.claim(): take control of all open tabs immediately after activation.
//   Without this, navigator.serviceWorker.controller is null in any tab that
//   was open before the SW activated, breaking postMessage from page → SW.

self.addEventListener('install', function (event) {
    self.skipWaiting();
});

self.addEventListener('activate', function (event) {
    event.waitUntil(self.clients.claim());
});

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

    const unreadCount = payload.unread_count || 1;

    // Double-notify guard: if a Dogboxx tab is already visible, skip the OS
    // notification — the user is watching the app and SSE will update the bell.
    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(function (clients) {
                const appIsVisible = clients.some(function (c) {
                    return c.visibilityState === 'visible';
                });

                // Always update the home screen badge count regardless of visibility
                if ('setAppBadge' in navigator) {
                    navigator.setAppBadge(unreadCount).catch(function () {});
                }

                if (appIsVisible) {
                    // App is open and focused — SSE handles this, skip OS push
                    return;
                }

                return self.registration.showNotification(title, options);
            })
    );
});

// ── Badge updates from page context ──────────────────────────────────────────
//
// iOS only honours setAppBadge/clearAppBadge when called from the service
// worker context, not from a page window. Page JS posts a SET_BADGE message
// here so all badge API calls go through SW, where they're known to work.

self.addEventListener('message', function (event) {
    if (!event.data || event.data.type !== 'SET_BADGE') return;
    const count = parseInt(event.data.count, 10) || 0;
    if (count <= 0) {
        if ('clearAppBadge' in navigator) {
            navigator.clearAppBadge().catch(function () {});
        }
    } else {
        if ('setAppBadge' in navigator) {
            navigator.setAppBadge(count).catch(function () {});
        }
    }
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
                // Clear the home screen badge — user is opening the app
                if ('clearAppBadge' in navigator) {
                    navigator.clearAppBadge().catch(function () {});
                }

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
