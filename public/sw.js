const CACHE_NAME = "sparkymateau-v3";
const OFFLINE_FILES = [
  "/manifest.webmanifest",
  "/file_00000000440481fab52a2709d261388f.png"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(OFFLINE_FILES))
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
  const request = event.request;

  if (request.method !== "GET" || request.url.includes("/api/")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request, { cache: "no-store" })
        .catch(() => caches.match("/"))
    );
    return;
  }

  event.respondWith(
    fetch(request)
      .then(response => {
        if (
          response.ok &&
          new URL(request.url).origin === self.location.origin
        ) {
          const copy = response.clone();
          caches.open(CACHE_NAME)
            .then(cache => cache.put(request, copy));
        }
        return response;
      })
      .catch(() =>
        caches.match(request)
          .then(cached => cached || Response.error())
      )
  );
});
