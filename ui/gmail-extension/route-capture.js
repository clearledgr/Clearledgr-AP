(function captureClearledgrRouteIntent() {
  var STORAGE_KEY = '__clearledgr_pending_direct_route_v1';
  var ATTRIBUTE_NAME = 'data-clearledgr-pending-direct-route';

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
      if (!hash || !hash.startsWith('#clearledgr/')) return;
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
