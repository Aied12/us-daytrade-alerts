/* US Daytrade Alerts — PWA service worker (fresh HTML, fresh live JSON) */
const CACHE = "uda-shell-v2";
const SHELL_STATIC = [
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL_STATIC))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data && data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
  if (data && data.type === "CLEAR_CACHE") {
    event.waitUntil(
      caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
    );
  }
});

function isLiveData(url) {
  const path = url.pathname || "";
  return (
    path.endsWith(".json") ||
    path.includes("status.json") ||
    path.includes("live.json") ||
    path.includes("prices-live") ||
    path.includes("news-live")
  );
}

function isHtmlNav(req, url) {
  if (req.mode === "navigate") return true;
  const path = url.pathname || "";
  if (path.endsWith(".html") || path.endsWith("/") || path.endsWith("/us-daytrade-alerts")) {
    return true;
  }
  const accept = req.headers.get("accept") || "";
  return accept.includes("text/html");
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Live JSON + HTML: always network-first (never stick on a stale board/shell).
  if (isLiveData(url) || isHtmlNav(req, url)) {
    event.respondWith(
      fetch(req, { cache: "no-store" })
        .then((res) => res)
        .catch(() => caches.match(req).then((c) => c || caches.match("./index.html")))
    );
    return;
  }

  // Icons / manifest only: cache-first.
  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req)
        .then((res) => {
          if (res && res.ok && res.type === "basic") {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
