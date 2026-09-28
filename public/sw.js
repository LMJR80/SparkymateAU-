const CACHE_NAME = "sparkymateau-v2";

const APP_FILES = [
  "/",
  "/manifest.webmanifest",
  "/file_00000000440481fab52a2709d261388f.png"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(APP_FILES))
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_NAME)
          .map(key => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET" ||
      event.request.url.includes("/api/")) return;

  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, copy);
          });
        }
        return response;
      })
      .catch(() =>
        caches.match(event.request).then(cached =>
          cached ||
          (event.request.mode === "navigate"
            ? caches.match("/")
            : Response.error())
        )
      )
  );
});
