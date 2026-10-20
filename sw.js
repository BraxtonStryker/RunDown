/* RunDown service worker (v1.0). Keeps the app working offline once it has been opened. The same design as QuizQrusher's:
 *
 * - Every file in PRECACHE is cached together under one version; requests for them are answered from that cache.
 * - VERSION is a hash of those files' contents, written by `python -I tools/sw_version.py --sync`.
 *   When any of them changes, VERSION changes, so this file changes and browsers install the new version.
 * - A new version waits until the page asks it to take over (the "new version" toast's Reload),
 *   so a document is never swapped out from under the user.
 * - Only same-origin GETs inside this scope are touched. Everything else goes to the network.
 * - Cache names start with "runDown-": every GitHub Pages project of this user shares the origin.
 */
"use strict";
const VERSION = "ce61b61eea0e"; // generated: tools/sw_version.py --sync
const CACHE = "runDown-" + VERSION;
const PRECACHE = [
  "./",
  "index.html",
  "manifest.webmanifest",
  "icons/icon.svg",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/icon-maskable-512.png",
  "icons/apple-touch-icon.png",
  "vendor/katex.min.js"
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE.map((u) => new Request(u, { cache: "reload" })))));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.indexOf("runDown-") === 0 && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || !url.href.startsWith(self.registration.scope)) return;
  const precached = PRECACHE.some((p) => new URL(p, self.registration.scope).href === url.origin + url.pathname);
  if (!precached && req.mode !== "navigate") return;      // anything else: straight to the network
  event.respondWith(
    caches.open(CACHE).then((c) => c.match(req, { ignoreSearch: true })).then((hit) => hit || fetch(req))
  );
});
