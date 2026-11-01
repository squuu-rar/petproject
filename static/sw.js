const CACHE_NAME = "echo-pwa-v7";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Всегда загружаем актуальные данные с сервера (Network-First),
// чтобы после перезапуска сервиса телефон никогда не зависал на старом кэше.
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/stream/")) return;

  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
