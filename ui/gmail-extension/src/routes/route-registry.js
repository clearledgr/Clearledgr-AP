/**
 * Route registry — thesis-conformant §6.2.
 *
 * ROUTES = thesis-defined nav entries (Home, AP Invoices, Vendor Onboarding,
 * Agent Activity, Settings). These appear in the left nav.
 *
 * LEGACY_ROUTES = pre-thesis pages kept for direct-URL access only.
 * They render when navigated to but never appear in the nav.
 */

export const NAV_PREFS_STORAGE_KEY = 'clearledgr_nav_preferences_v1';

// §6.2 — thesis-defined nav routes only
export const ROUTES = [
  {
    id: 'clearledgr/home',
    title: 'Clearledgr Home',
    subtitle: 'Your daily AP briefing.',
    icon: 'home',
    navOrder: 1,
    defaultPinned: true,
    canHide: false,
    menuGroup: 'primary',
    hideTopbar: true,
    viewCapability: 'view_home',
  },
  {
    id: 'clearledgr/invoices',
    title: 'AP Invoices',
    subtitle: 'All invoice Boxes organised by stage.',
    icon: 'pipeline',
    navOrder: 2,
    defaultPinned: true,
    canHide: false,
    menuGroup: 'primary',
    hideTopbar: true,
    viewCapability: 'view_pipeline',
  },
  {
    id: 'clearledgr/vendor-onboarding',
    title: 'Vendor Onboarding',
    subtitle: 'All vendor Boxes from Invited to Active.',
    icon: 'vendors',
    navOrder: 3,
    defaultPinned: true,
    canHide: false,
    menuGroup: 'primary',
    hideTopbar: true,
    viewCapability: 'view_vendors',
  },
  {
    id: 'clearledgr/activity',
    title: 'Agent Activity',
    subtitle: 'All autonomous actions across both pipelines.',
    icon: 'activity',
    navOrder: 4,
    defaultPinned: true,
    canHide: false,
    menuGroup: 'primary',
    viewCapability: 'view_activity',
  },
  {
    id: 'clearledgr/settings',
    title: 'Settings',
    subtitle: 'ERP, policies, approvals, team, and billing.',
    icon: 'settings',
    navOrder: 5,
    defaultPinned: true,
    canHide: false,
    menuGroup: 'settings',
    viewCapability: 'view_settings',
  },
];

// Pre-thesis routes — still render via handleCustomRoute but never appear in nav.
// Some redirect to Settings sections; others render their legacy page.
export const LEGACY_ROUTES = [
  { id: 'clearledgr/review', title: 'Review', redirectTo: null },
  { id: 'clearledgr/upcoming', title: 'Upcoming', redirectTo: null },
  { id: 'clearledgr/connections', title: 'Connections', redirectTo: 'clearledgr/settings' },
  { id: 'clearledgr/rules', title: 'Approval Rules', redirectTo: 'clearledgr/settings' },
  { id: 'clearledgr/vendors', title: 'Vendors', redirectTo: null },
  { id: 'clearledgr/templates', title: 'Templates', redirectTo: null },
  { id: 'clearledgr/reconciliation', title: 'Reconciliation', redirectTo: null },
  { id: 'clearledgr/health', title: 'System Status', redirectTo: null },
  { id: 'clearledgr/reports', title: 'Reports', redirectTo: null },
  { id: 'clearledgr/plan', title: 'Plan', redirectTo: 'clearledgr/settings' },
  { id: 'clearledgr/company', title: 'Company', redirectTo: 'clearledgr/settings' },
  { id: 'clearledgr/team', title: 'Team', redirectTo: 'clearledgr/settings' },
];

// Dynamic routes (not in AppMenu nav, accessed via navigation)
export const DYNAMIC_ROUTES = [
  { id: 'clearledgr/invoice/:id', title: 'Invoice Detail', subtitle: '' },
  { id: 'clearledgr/vendor/:name', title: 'Vendor Detail', subtitle: '' },
  { id: 'clearledgr/invoices-view/:ref', title: 'Saved View', subtitle: '' },
];

export const DEFAULT_ROUTE = 'clearledgr/invoices';

export function getRouteById(id) {
  return ROUTES.find((route) => route.id === id) || null;
}

export function getLegacyRouteById(id) {
  return LEGACY_ROUTES.find((route) => route.id === id) || null;
}

function resolveCapabilities(options = {}) {
  if (options?.capabilities && typeof options.capabilities === 'object') {
    return options.capabilities;
  }
  if ('includeAdmin' in options || 'includeOps' in options) {
    const includeAdmin = Boolean(options.includeAdmin);
    return {
      view_home: true,
      view_pipeline: true,
      view_vendors: true,
      view_activity: true,
      view_settings: includeAdmin,
    };
  }
  return null;
}

export function canViewRoute(route, options = {}) {
  const capabilities = resolveCapabilities(options);
  if (!route) return false;
  if (!capabilities || !route.viewCapability) return true;
  return capabilities[route.viewCapability] !== false;
}

export function canManageRoute(route, options = {}) {
  const capabilities = resolveCapabilities(options);
  if (!route) return false;
  if (!route.manageCapability) return true;
  if (!capabilities) return false;
  return capabilities[route.manageCapability] !== false;
}

export function getNavEligibleRoutes(options = {}) {
  return ROUTES
    .filter((route) => canViewRoute(route, options))
    .sort((left, right) => Number(left.navOrder || 0) - Number(right.navOrder || 0));
}

export function getDefaultPinnedRouteIds(options = {}) {
  return getNavEligibleRoutes(options)
    .filter((route) => route.defaultPinned)
    .map((route) => route.id);
}

export function normalizeRoutePreferences(value = {}, options = {}) {
  const allowedRouteIds = new Set(getNavEligibleRoutes(options).map((route) => route.id));
  const routeMap = new Map(ROUTES.map((route) => [route.id, route]));

  const normalizeList = (items) => [...new Set(
    (Array.isArray(items) ? items : [])
      .map((item) => String(item || '').trim())
      .filter((item) => item && allowedRouteIds.has(item))
  )];

  const pinned = normalizeList(value?.pinned)
    .filter((routeId) => !routeMap.get(routeId)?.defaultPinned);
  const hidden = normalizeList(value?.hidden)
    .filter((routeId) => routeMap.get(routeId)?.canHide !== false);

  return { pinned, hidden };
}

export function readRoutePreferences(options = {}) {
  if (typeof window === 'undefined' || !window?.localStorage) {
    return normalizeRoutePreferences({}, options);
  }
  try {
    const raw = window.localStorage.getItem(NAV_PREFS_STORAGE_KEY);
    if (!raw) return normalizeRoutePreferences({}, options);
    return normalizeRoutePreferences(JSON.parse(raw), options);
  } catch {
    return normalizeRoutePreferences({}, options);
  }
}

export function writeRoutePreferences(value = {}, options = {}) {
  const normalized = normalizeRoutePreferences(value, options);
  if (typeof window !== 'undefined' && window?.localStorage) {
    try {
      window.localStorage.setItem(NAV_PREFS_STORAGE_KEY, JSON.stringify(normalized));
    } catch {
      /* best-effort */
    }
  }
  return normalized;
}

export function resetRoutePreferences(options = {}) {
  return writeRoutePreferences({}, options);
}

export function getRoutePreferenceState(routeId, preferences = {}, options = {}) {
  const route = getRouteById(routeId);
  if (!route) {
    return {
      visible: false,
      pinned: false,
      hidden: false,
      defaultPinned: false,
      canHide: false,
      viewCapability: '',
      manageCapability: '',
      canManage: false,
    };
  }

  const prefs = normalizeRoutePreferences(preferences, options);
  const hidden = prefs.hidden.includes(route.id);
  const pinned = prefs.pinned.includes(route.id);
  const defaultPinned = Boolean(route.defaultPinned);
  const visible = !hidden && (defaultPinned || pinned);

  return {
    visible,
    pinned,
    hidden,
    defaultPinned,
    canHide: route.canHide !== false,
    viewCapability: route.viewCapability || '',
    manageCapability: route.manageCapability || '',
    canManage: canManageRoute(route, options),
  };
}

export function getVisibleNavRoutes(preferences = {}, options = {}) {
  const prefs = normalizeRoutePreferences(preferences, options);
  return getNavEligibleRoutes(options).filter((route) => {
    const state = getRoutePreferenceState(route.id, prefs, options);
    return state.visible;
  });
}

export function getMenuNavRoutes(preferences = {}, options = {}) {
  const groupWeight = { primary: 0, secondary: 1 };
  return getNavEligibleRoutes(options)
    .filter((route) => route.menuGroup !== 'hidden')
    .slice().sort((left, right) => {
      const leftWeight = groupWeight[left.menuGroup] ?? 9;
      const rightWeight = groupWeight[right.menuGroup] ?? 9;
      if (leftWeight !== rightWeight) return leftWeight - rightWeight;
      return Number(left.navOrder || 0) - Number(right.navOrder || 0);
    });
}

export function pinRoute(routeId, preferences = {}, options = {}) {
  const route = getRouteById(routeId);
  const prefs = normalizeRoutePreferences(preferences, options);
  if (!route) return prefs;
  return normalizeRoutePreferences({
    pinned: route.defaultPinned ? prefs.pinned : [...prefs.pinned, route.id],
    hidden: prefs.hidden.filter((id) => id !== route.id),
  }, options);
}

export function unpinRoute(routeId, preferences = {}, options = {}) {
  const prefs = normalizeRoutePreferences(preferences, options);
  return normalizeRoutePreferences({
    pinned: prefs.pinned.filter((id) => id !== routeId),
    hidden: prefs.hidden.filter((id) => id !== routeId),
  }, options);
}

export function hideRoute(routeId, preferences = {}, options = {}) {
  const route = getRouteById(routeId);
  const prefs = normalizeRoutePreferences(preferences, options);
  if (!route || route.canHide === false) return prefs;
  return normalizeRoutePreferences({
    pinned: prefs.pinned.filter((id) => id !== route.id),
    hidden: [...prefs.hidden, route.id],
  }, options);
}

export function showRoute(routeId, preferences = {}, options = {}) {
  const route = getRouteById(routeId);
  const prefs = normalizeRoutePreferences(preferences, options);
  if (!route) return prefs;
  return normalizeRoutePreferences({
    pinned: route.defaultPinned ? prefs.pinned : [...prefs.pinned, route.id],
    hidden: prefs.hidden.filter((id) => id !== route.id),
  }, options);
}
