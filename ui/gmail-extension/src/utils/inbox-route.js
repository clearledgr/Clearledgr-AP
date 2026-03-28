function populateRouteId(routeId, params = {}) {
  const normalized = String(routeId || '').trim().replace(/^#/, '');
  if (!normalized) return '';
  return normalized.replace(/:([^/]+)/g, (_whole, key) => encodeURIComponent(String(params?.[key] || '').trim()));
}

export function navigateInboxRoute(routeId, sdk, params = null) {
  const normalized = String(routeId || '').trim().replace(/^#/, '');
  if (!normalized) return false;

  const goto = sdk?.Router?.goto;
  if (typeof goto === 'function') {
    try {
      const result = goto(normalized, params || undefined);
      if (result !== false) return true;
    } catch {
      // Fall through to hash navigation.
    }
  }

  const fallbackRoute = populateRouteId(normalized, params || {});
  if (!fallbackRoute) return false;
  if (typeof window !== 'undefined' && window?.location) {
    window.location.hash = `#${fallbackRoute}`;
    return true;
  }

  return false;
}
