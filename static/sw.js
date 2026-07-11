// TGDownloader service worker (v1.10.0).
// Its only job is to exist so the app satisfies PWA install criteria and can be
// added to a phone home screen. It deliberately does NO caching: the app is a
// live localhost server that talks to a local backend, so a cached shell could
// only ever be stale (and stale UI shipped real bugs before). Always network.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (event) => {
  // Pass-through: never intercept with a cache. If the server is down there is
  // nothing useful to serve offline anyway.
  event.respondWith(fetch(event.request));
});
