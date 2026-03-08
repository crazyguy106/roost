const CACHE_NAME = 'roost-v6';
const SHELL_ASSETS = [
  '/static/style.css?v=6',
  '/static/mobile.css?v=6',
  '/static/mobile.js',
  '/static/manifest.json',
  '/static/icons/icon.svg',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png',
  '/favicon.ico',
];

// Install: cache shell assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch: network-first for HTML, cache-first for static assets
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Static assets: cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
    return;
  }

  // HTML pages: network-first with offline fallback
  if (event.request.mode === 'navigate') {
    // PWA runs in standalone mode — force mobile routes
    const isStandalone = self.registration.scope &&
      (event.request.headers.get('sec-fetch-dest') === 'document');
    const pathname = url.pathname;
    const desktopRoutes = ['/', '/tasks', '/projects', '/calendar', '/contacts', '/inbox'];
    const desktopPattern = /^\/(?:tasks|projects|contacts)\/\d+$/;

    if (isStandalone && !pathname.startsWith('/m/') &&
        !pathname.startsWith('/api/') && !pathname.startsWith('/static') &&
        !pathname.startsWith('/auth/') && !pathname.startsWith('/shared/') &&
        (desktopRoutes.includes(pathname) || desktopPattern.test(pathname))) {
      const mobilePath = pathname === '/' ? '/m/' : '/m' + pathname;
      event.respondWith(Response.redirect(mobilePath, 302));
      return;
    }

    event.respondWith(
      fetch(event.request)
        .then((response) => {
          // Cache successful page loads
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Everything else: network-first
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
