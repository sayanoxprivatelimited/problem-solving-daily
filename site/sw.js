/**
 * Offline shell for the catalogue.
 *
 * Strategy, and the reason for each half:
 *
 *   navigation  -> network first. A deploy must be visible on the next load, and
 *                  a stale HTML page would also pin a stale data file.
 *   everything else -> stale-while-revalidate. Instant from cache, corrected in
 *                  the background, so a CSS tweak lands one load later but the
 *                  page never waits for the network.
 *
 * The cache is versioned: bump CACHE_VERSION when the shell changes shape, and
 * the activate handler drops the previous one.
 */
const CACHE_VERSION = 'v1';
const CACHE = `psd-shell-${CACHE_VERSION}`;

const PRECACHE = [
  'index.html',
  'offline.html',
  'css/style.css',
  'js/main.js',
  'js/layout.js',
  'js/palette.js',
  'js/progress.js',
  'data/catalog.js',
  'data/problems.json',
  'assets/icon.svg',
  'assets/apple-touch-icon.png',
  'assets/manifest.webmanifest',
  'robots.txt',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      // One missing file must not fail the install: offline support is a bonus,
      // not a requirement.
      await Promise.allSettled(PRECACHE.map((url) => cache.add(new Request(url, { cache: 'reload' }))));
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)));
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request);
          const cache = await caches.open(CACHE);
          cache.put('index.html', response.clone());
          return response;
        } catch {
          const cache = await caches.open(CACHE);
          return (
            (await cache.match('index.html'))
            || (await cache.match('offline.html'))
            || new Response('You are offline.', { status: 503, headers: { 'content-type': 'text/plain' } })
          );
        }
      })(),
    );
    return;
  }

  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE);
      const cached = await cache.match(request);
      const network = fetch(request)
        .then((response) => {
          if (response.ok) cache.put(request, response.clone());
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })(),
  );
});
