/**
 * Route registry — single source of truth for all Clearledgr pages inside Gmail.
 * Each route renders as a full-page view in Gmail's content area via InboxSDK Router.
 *
 * Streak-style doctrine:
 * - Keep Home support cards intentionally small.
 * - Let the Gmail left nav expose every eligible page unless a user hides it.
 * - Let less-used pages stay accessible from Home without cluttering the nav.
 */

export const NAV_PREFS_STORAGE_KEY = 'clearledgr_nav_preferences_v1';

export const ROUTES = [
  {
    id: 'clearledgr/home',
    title: 'Home',
    subtitle: 'Your AP launch hub inside Gmail.',
    icon: 'home',
    navOrder: 10,
    defaultPinned: true,
    canHide: false,
  },
  {
    id: 'clearledgr/pipeline',
    title: 'Pipeline',
    subtitle: 'Work the AP queue.',
    icon: 'pipeline',
    navOrder: 20,
    defaultPinned: true,
    canHide: false,
  },
  {
    id: 'clearledgr/review',
    title: 'Review',
    subtitle: 'Resolve blocked fields, exceptions, and posting retries.',
    icon: 'review',
    navOrder: 22,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
  },
  {
    id: 'clearledgr/upcoming',
    title: 'Upcoming',
    subtitle: 'Due follow-ups across approvals, vendor replies, and posting.',
    icon: 'activity',
    navOrder: 25,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
  },
  {
    id: 'clearledgr/connections',
    title: 'Connections',
    subtitle: 'Fix Gmail, approval, or ERP setup when AP work is blocked.',
    icon: 'connections',
    navOrder: 30,
    defaultPinned: true,
    canHide: true,
    opsOnly: true,
    adminOnly: true,
  },
  {
    id: 'clearledgr/activity',
    title: 'Activity',
    subtitle: 'Recent AP record movement.',
    icon: 'activity',
    navOrder: 40,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
  },
  {
    id: 'clearledgr/vendors',
    title: 'Vendors',
    subtitle: 'Vendor context for AP follow-up.',
    icon: 'vendors',
    navOrder: 50,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
  },
  {
    id: 'clearledgr/templates',
    title: 'Templates',
    subtitle: 'Reusable AP reply templates for vendors and approvers.',
    icon: 'activity',
    navOrder: 55,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
  },
  {
    id: 'clearledgr/rules',
    title: 'Approval Rules',
    subtitle: 'Admin controls for approval routing.',
    icon: 'rules',
    navOrder: 60,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
    adminOnly: true,
  },
  {
    id: 'clearledgr/team',
    title: 'Team',
    subtitle: 'Invite and manage Gmail workspace access.',
    icon: 'team',
    navOrder: 70,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
    adminOnly: true,
  },
  {
    id: 'clearledgr/company',
    title: 'Company',
    subtitle: 'Workspace identity and AP defaults.',
    icon: 'company',
    navOrder: 80,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
    adminOnly: true,
  },
  {
    id: 'clearledgr/plan',
    title: 'Plan',
    subtitle: 'Workspace plan summary.',
    icon: 'plan',
    navOrder: 90,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
    adminOnly: true,
  },
  {
    id: 'clearledgr/reconciliation',
    title: 'Reconciliation',
    subtitle: 'Future reconciliation groundwork.',
    icon: 'recon',
    navOrder: 100,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
  },
  {
    id: 'clearledgr/health',
    title: 'System Status',
    subtitle: 'Admin diagnostics and status.',
    icon: 'health',
    navOrder: 110,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
    adminOnly: true,
  },
  {
    id: 'clearledgr/reports',
    title: 'Reports',
    subtitle: 'Lightweight AP reporting tied to queue views.',
    icon: 'activity',
    navOrder: 115,
    defaultPinned: false,
    canHide: true,
    opsOnly: true,
    adminOnly: true,
  },
];

// Dynamic routes (not in AppMenu nav, accessed via navigation)
export const DYNAMIC_ROUTES = [
  { id: 'clearledgr/invoice/:id', title: 'Invoice Detail', subtitle: '' },
  { id: 'clearledgr/vendor/:name', title: 'Vendor Detail', subtitle: '' },
  { id: 'clearledgr/pipeline-view/:ref', title: 'Pipeline View', subtitle: '' },
];

export const DEFAULT_ROUTE = 'clearledgr/home';

export function getRouteById(id) {
  return ROUTES.find((route) => route.id === id) || null;
}

export function getNavEligibleRoutes({ includeAdmin = false, includeOps = true } = {}) {
  return ROUTES
    .filter((route) => (includeOps || !route.opsOnly) && (includeAdmin || !route.adminOnly))
    .sort((left, right) => Number(left.navOrder || 0) - Number(right.navOrder || 0));
}

export function getDefaultPinnedRouteIds({ includeAdmin = false, includeOps = true } = {}) {
  return getNavEligibleRoutes({ includeAdmin, includeOps })
    .filter((route) => route.defaultPinned)
    .map((route) => route.id);
}

export function normalizeRoutePreferences(value = {}, { includeAdmin = false, includeOps = true } = {}) {
  const allowedRouteIds = new Set(getNavEligibleRoutes({ includeAdmin, includeOps }).map((route) => route.id));
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
      adminOnly: false,
      opsOnly: false,
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
    adminOnly: Boolean(route.adminOnly),
    opsOnly: Boolean(route.opsOnly),
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
  const prefs = normalizeRoutePreferences(preferences, options);
  return getNavEligibleRoutes(options).filter((route) => {
    const state = getRoutePreferenceState(route.id, prefs, options);
    return !state.hidden;
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
