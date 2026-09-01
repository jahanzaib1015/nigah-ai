var CACHE_NAME = 'nigah-ai-v2';
var CORE_ASSETS = [
  '/',
  '/index.html',
  '/scan.html',
  '/medicine.html',
  '/list.html',
  '/style.css',
  '/tts.js',
  '/care.js',
  '/pwa.js',
  '/manifest.json',
  '/favicon.svg',
  '/icon-192.png',
  '/icon-512.png',
  '/icon-maskable-512.png'
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(CORE_ASSETS);
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (key) {
          return key !== CACHE_NAME;
        }).map(function (key) {
          return caches.delete(key);
        })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') return;

  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never intercept dynamic APIs: scans and speech must always be live.
  if (
    url.pathname.indexOf('/detect-') === 0 ||
    url.pathname.indexOf('/meri-list') === 0 ||
    url.pathname.indexOf('/generate-speech') === 0
  ) {
    return;
  }

  event.respondWith(
    fetch(request).then(function (response) {
      if (response && response.ok) {
        var copy = response.clone();
        caches.open(CACHE_NAME).then(function (cache) {
          cache.put(request, copy);
        });
      }
      return response;
    }).catch(function () {
      return caches.match(request).then(function (cached) {
        return cached || caches.match('/index.html');
      });
    })
  );
});
