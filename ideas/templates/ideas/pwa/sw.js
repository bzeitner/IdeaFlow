{% load static %}// IdeaFlow service worker. Served from / so its scope is the whole site.
const CACHE = "ideaflow-v1";
const ASSETS = [
  "/",
  "{% static 'ideas/app.css' %}",
  "{% static 'ideas/favicon.svg' %}",
  "{% static 'ideas/icon-192.png' %}"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // Never cache dynamic/authenticated endpoints.
  if (url.pathname.startsWith("/api/") ||
      url.pathname.startsWith("/admin/") ||
      url.pathname.startsWith("/accounts/")) {
    return;
  }

  // Page loads (incl. shared/prefilled /new/) go network-first so they're fresh,
  // falling back to the cached home shell when offline.
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/")));
    return;
  }

  // Static assets: cache-first, then network.
  event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
