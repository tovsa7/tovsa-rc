const CACHE = 'tvrc-v1';
const ASSETS = [
  './',
  './index.html',
  './manifest.json',
  'https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js',
  'https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap',
];

// Install — cache все ресурсы
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

// Activate — удалить старые кэши
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch — cache-first для ассетов, network-first для WebSocket/API
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // WebSocket и signaling — не кэшируем
  if(url.protocol === 'wss:' || url.protocol === 'ws:') return;
  if(url.pathname.includes('/signal')) return;

  // Для навигации — network-first с fallback на кэш
  if(e.request.mode === 'navigate'){
    e.respondWith(
      fetch(e.request)
        .then(r => { const c = r.clone(); caches.open(CACHE).then(cache => cache.put(e.request, c)); return r; })
        .catch(() => caches.match('./index.html'))
    );
    return;
  }

  // Для остального — cache-first
  e.respondWith(
    caches.match(e.request).then(cached => {
      if(cached) return cached;
      return fetch(e.request).then(r => {
        if(r && r.status === 200 && r.type !== 'opaque'){
          const c = r.clone();
          caches.open(CACHE).then(cache => cache.put(e.request, c));
        }
        return r;
      });
    })
  );
});
