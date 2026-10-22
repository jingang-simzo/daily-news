const SHELL = 'daily-brief-v1';
const SHELL_FILES = ['./', './index.html', './manifest.webmanifest', './icon-192.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL).then((cache) => cache.addAll(SHELL_FILES)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // Audio: cache-first so a downloaded brief can replay without signal.
  if (url.pathname.endsWith('.mp3')) {
    event.respondWith(
      caches.match(req).then((hit) =>
        hit || fetch(req).then((res) => {
          const copy = res.clone();
          caches.open(SHELL).then((cache) => cache.put(req, copy));
          return res;
        })
      )
    );
    return;
  }

  // Everything else: network first, fall back to cache.
  event.respondWith(
    fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(SHELL).then((cache) => cache.put(req, copy));
      return res;
    }).catch(() => caches.match(req))
  );
});
