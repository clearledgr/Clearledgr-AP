(function captureSoldenRouteIntent() {
  var STORAGE_KEY = '__solden_pending_direct_route_v1';
  var ATTRIBUTE_NAME = 'data-solden-pending-direct-route';

  function writePendingRouteToExtensionStorage(payload) {
    try {
      if (!globalThis.chrome?.storage?.session?.set) return;
      globalThis.chrome.storage.session.set(payload);
    } catch (_) {
      /* best effort */
    }
  }

  function writePendingRoute() {
    try {
      var hash = String(window.location.hash || '').trim();
      // Capture the new solden/ routes; still accept legacy #clearledgr/
      // hashes so an in-flight deep link from before the rebrand resolves.
      if (!hash || !(hash.startsWith('#solden/') || hash.startsWith('#clearledgr/'))) return;
      var normalizedHash = hash.slice(1);
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
        hash: normalizedHash,
        ts: Date.now(),
      }));
      document.documentElement.setAttribute(ATTRIBUTE_NAME, normalizedHash);
      writePendingRouteToExtensionStorage({
        [STORAGE_KEY]: {
          hash: normalizedHash,
          ts: Date.now(),
          pathname: String(window.location.pathname || ''),
        },
      });
    } catch (_) {
      /* best effort */
    }
  }

  writePendingRoute();
  window.addEventListener('hashchange', writePendingRoute, true);
})();
