// Minimální service worker: appku jde nainstalovat na plochu, data se berou vždy čerstvá ze sítě.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("fetch", e => {
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
