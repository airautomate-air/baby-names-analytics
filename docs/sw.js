// NameCharted service worker — minimal offline support.
// Cache-on-visit strategy: every successful HTML/JSON GET gets stashed so the
// next visit works offline. Network is tried first; on failure we serve from
// cache. Bump CACHE_VERSION whenever the on-disk format changes.
const CACHE_VERSION = 'nc-v1';
const CORE = ['/', '/names.html', '/favorites.html', '/manifest.webmanifest',
              '/favicon.svg', '/icon-192.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((c) => c.addAll(CORE).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Don't try to cache JSON data files when navigating — they're fetched by
  // tools at runtime and the cache-miss fallback should be the live network.
  event.respondWith(
    fetch(req)
      .then((res) => {
        const accept = req.headers.get('accept') || '';
        const isHtml = accept.includes('text/html');
        const isStatic = /\.(?:html|json|png|svg|webmanifest)$/.test(url.pathname);
        if (res.ok && (isHtml || isStatic)) {
          const copy = res.clone();
          caches.open(CACHE_VERSION).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match('/')))
  );
});
