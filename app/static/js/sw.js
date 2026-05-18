/**
 * Dogboxx Service Worker
 *
 * Responsibilities:
 *   1. App-shell caching — static assets served cache-first for fast PWA restores
 *   2. HTML pages — network-first with cache fallback (graceful offline experience)
 *   3. Web Push — receive push events and show OS notifications
 *   4. Notification clicks — focus/open the app at the right URL
 *   5. Badge updates — proxy setAppBadge/clearAppBadge from page context (iOS fix)
 *
 * Cache strategy summary:
 *   /static/* + cdn.jsdelivr.net  →  cache-first  (assets rarely change)
 *   text/html requests            →  network-first (dynamic, CSRF tokens)
 *   POST / SSE / auth flows       →  never cached
 *
 * ⚠️  When deploying CSS/JS changes, bump CACHE_VERSION below.
 *     The activate handler will delete the old cache and force clients to
 *     re-fetch updated assets.
 */

// ── Cache config ──────────────────────────────────────────────────────────────

const CACHE_VERSION = 'v25';
const CACHE_NAME    = `dogboxx-${CACHE_VERSION}`;

/**
 * Assets pre-fetched and cached at install time (the "app shell").
 * Keep this list lean — only what every page needs on first paint.
 * Everything else (dog photos, page-specific JS) is cached on first use.
 */
const PRECACHE_ASSETS = [
  // ── Local CSS ─────────────────────────────────────────────────────────────
  '/static/css/brand.css',
  '/static/css/reusable-calendar.css',

  // ── Local JS ──────────────────────────────────────────────────────────────
  '/static/js/reusable-calendar.js',

  // ── Key images ────────────────────────────────────────────────────────────
  '/static/logo-white-on-black.png',
  '/static/android-chrome-192x192.png',
  '/static/uploads/dogs/default-dog.png',

  // ── Bootstrap CSS + JS (CDN) ──────────────────────────────────────────────
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js',

  // ── Bootstrap Icons CSS (font files cached on first use via fetch handler) ─
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.1/font/bootstrap-icons.min.css',
];

// ── Lifecycle ─────────────────────────────────────────────────────────────────
//
// skipWaiting: activate the new SW immediately instead of waiting for all
//   tabs to close. Without this, a SW update sits in "waiting" state and
//   the old version keeps running.
//
// clients.claim(): take control of all open tabs immediately after activation.
//   Without this, navigator.serviceWorker.controller is null in any tab that
//   was open before the SW activated, breaking postMessage (page → SW).

self.addEventListener('install', function (event) {
  self.skipWaiting();

  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      // Use Promise.allSettled so a single CDN hiccup at install time
      // doesn't abort the whole install — the asset will be cached on first use.
      return Promise.allSettled(
        PRECACHE_ASSETS.map(function (url) {
          return cache.add(url).catch(function (err) {
            console.warn('[SW] Precache skipped:', url, err.message);
          });
        })
      );
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    Promise.all([
      // Delete any caches from previous SW versions
      caches.keys()
        .then(function (keys) {
          return Promise.all(
            keys
              .filter(function (key) { return key !== CACHE_NAME; })
              .map(function (key) {
                console.log('[SW] Removing old cache:', key);
                return caches.delete(key);
              })
          );
        }),
      // Enable Navigation Preload — fires the network request while the SW
      // is booting, eliminating the SW startup latency on every page navigation
      // (Chrome on Android can add 100-300ms without this)
      self.registration.navigationPreload
        ? self.registration.navigationPreload.enable()
        : Promise.resolve(),
    ])
    .then(function () {
      return self.clients.claim();
    })
  );
});

// ── Fetch — caching strategies ────────────────────────────────────────────────

self.addEventListener('fetch', function (event) {
  var req = event.request;
  var url = new URL(req.url);

  // Only intercept GET requests — POST/PUT/DELETE are never cached
  if (req.method !== 'GET') return;

  // Paths that must always go to the network uncached:
  //   /api/stream  — SSE long-poll connections
  //   /auth/logout — must invalidate server session
  //   /push/       — push subscription management
  var BYPASS_PREFIXES = ['/api/stream', '/auth/logout', '/push/'];
  if (BYPASS_PREFIXES.some(function (p) { return url.pathname.startsWith(p); })) {
    return; // Let browser handle it normally
  }

  // ── Strategy 1: Cache-first for static assets (local + CDN) ──────────────
  //
  // Bootstrap, our CSS/JS, and images change rarely (or are content-addressed).
  // Serving them from cache means they're available in <10 ms instead of
  // waiting on the network — this is the main fix for the black-screen delay.
  //
  // Cache miss path: fetch → store → return response.
  // This also catches Bootstrap Icons font files (.woff2) on first load.

  var isLocalStatic = url.pathname.startsWith('/static/');
  var isCDN         = url.hostname === 'cdn.jsdelivr.net';
  var isLocalJS     = url.pathname.startsWith('/static/js/');

  // ── Strategy 1a: Network-first for local JS ──────────────────────────────
  //
  // JS files at stable URLs (no content hash in the path) change content as
  // we ship updates. Cache-first traps users on stale code — and our auto
  // CACHE_VERSION bump hook only fires for CSS, missing JS edits entirely.
  // Network-first means a refactored file is picked up immediately; the
  // cache is only consulted when offline.
  if (isLocalJS) {
    event.respondWith(
      fetch(req)
        .then(function (response) {
          if (response.ok) {
            var clone = response.clone();
            caches.open(CACHE_NAME).then(function (cache) { cache.put(req, clone); });
          }
          return response;
        })
        .catch(function () { return caches.match(req); })
    );
    return;
  }

  // ── Strategy 1b: Cache-first for other static assets (CSS, images, CDN) ──
  //
  // These either change rarely or are content-addressed at the CDN. Serving
  // them from cache keeps page loads snappy.
  if (isLocalStatic || isCDN) {
    event.respondWith(
      caches.open(CACHE_NAME).then(function (cache) {
        return cache.match(req).then(function (cached) {
          if (cached) {
            return cached;
          }
          return fetch(req).then(function (response) {
            if (response.ok) {
              cache.put(req, response.clone());
            }
            return response;
          });
        });
      })
    );
    return;
  }

  // ── Strategy 2: Network-first for HTML pages ──────────────────────────────
  //
  // Flask renders pages server-side with live data and per-request CSRF tokens,
  // so we always try the network first. The cached copy is a fallback for when
  // the user is offline — better to see a (possibly slightly stale) page than
  // a browser error.
  //
  // Note: stale cached pages are only served when the network is unreachable,
  // so CSRF tokens in forms won't be a problem under normal conditions.

  var acceptsHTML = req.headers.get('accept') || '';
  if (acceptsHTML.indexOf('text/html') !== -1) {
    event.respondWith(
      // Use the preloaded response if Navigation Preload fired it — this
      // means the network request was already in-flight while the SW booted,
      // so we get the response immediately with zero extra latency.
      Promise.resolve(event.preloadResponse || null)
        .then(function (preloaded) {
          if (preloaded) {
            // Cache the preloaded response for offline fallback
            var clone = preloaded.clone();
            caches.open(CACHE_NAME).then(function (cache) { cache.put(req, clone); });
            return preloaded;
          }
          // No preload — fall back to normal fetch
          return fetch(req)
            .then(function (response) {
              if (response.ok) {
                var clone = response.clone();
                caches.open(CACHE_NAME).then(function (cache) { cache.put(req, clone); });
              }
              return response;
            });
        })
        .catch(function () {
          // Network unavailable — serve cached page if we have one
          return caches.match(req).then(function (cached) {
            return cached || caches.match('/'); // last resort: cached home page
          });
        })
    );
    return;
  }
  // All other requests (XHR, fetch API calls, etc.) fall through to browser default
});

// ── Push received ─────────────────────────────────────────────────────────────

self.addEventListener('push', function (event) {
  var payload = {};

  if (event.data) {
    try {
      payload = event.data.json();
    } catch (e) {
      payload = { title: event.data.text() };
    }
  }

  var APP_NAME    = 'Dogboxx';
  var DEFAULT_ICON  = '/static/android-chrome-192x192.png';
  var DEFAULT_BADGE = '/static/badge-mono.png';

  var title   = payload.title  || APP_NAME;
  var options = {
    body:     payload.body   || '',
    icon:     payload.icon   || DEFAULT_ICON,
    badge:    payload.badge  || DEFAULT_BADGE,
    data:     { link: payload.link || '/' },
    tag:      payload.tag    || 'dogboxx-notification', // replaces stale notification of same type
    renotify: false,
    vibrate:  [200, 100, 200],  // short-pause-short pulse
  };

  var unreadCount = payload.unread_count || 1;

  // Double-notify guard: if a Dogboxx tab is already visible, skip the OS
  // notification — the user is watching the app and SSE will update the bell.
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then(function (clients) {
        var appIsVisible = clients.some(function (c) {
          return c.visibilityState === 'visible';
        });

        // Always update the home screen badge count regardless of visibility
        if ('setAppBadge' in navigator) {
          navigator.setAppBadge(unreadCount).catch(function () {});
        }

        if (appIsVisible) {
          return; // App is open and focused — SSE handles this, skip OS push
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
  var count = parseInt(event.data.count, 10) || 0;
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

  var targetUrl = (event.notification.data && event.notification.data.link)
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
        for (var i = 0; i < clients.length; i++) {
          var client = clients[i];
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
