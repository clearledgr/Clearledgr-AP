/**
 * Route registry — single source of truth for all Clearledgr pages inside Gmail.
 * Each route renders as a full-page view in Gmail's content area via InboxSDK Router.
 *
 * Streak-style doctrine:
 * - Keep the always-visible left nav sparse and work-first.
 * - Let AppMenu expose the broader Gmail route catalog.
 * - Keep the Gmail surface work-first instead of settings-first.
 */

export const NAV_PREFS_STORAGE_KEY = 'clearledgr_nav_preferences_v1';

export const ROUTES = [
  {
    id: 'clearledgr/invoices',
    title: 'Invoices',
    subtitle: 'All invoices and finance documents across states.',
    icon: 'pipeline',
    navOrder: 10,
    defaultPinned: true,
    canHide: false,
    menuGroup: 'primary',
    hideTopbar: true,
    viewCapability: 'view_pipeline',
  },
  {
    id: 'clearledgr/home',
    title: 'Home',
    subtitle: 'Quick access, recent work, and secondary tools.',
    icon: 'home',
    navOrder: 20,
    defaultPinned: true,
    canHide: false,
    menuGroup: 'primary',
    hideTopbar: true,
    viewCapability: 'view_home',
  },
  {
    id: 'clearledgr/review',
    title: 'Review',
    subtitle: 'Handle records that need a closer look.',
    icon: 'review',
    navOrder: 22,
    defaultPinned: false,
    canHide: true,
    menuGroup: 'primary',
    viewCapability: 'view_review',
  },
  {
    id: 'clearledgr/upcoming',
    title: 'Upcoming',
    subtitle: 'See what needs attention next.',
    icon: 'upcoming',
    navOrder: 25,
    defaultPinned: false,
    canHide: true,
    menuGroup: 'primary',
    viewCapability: 'view_upcoming',
  },
  {
    id: 'clearledgr/connections',
    title: 'Connections',
    subtitle: 'Connect Gmail, approvals, and your ERP.',
    icon: 'connections',
    navOrder: 30,
    defaultPinned: false,
    canHide: true,
    menuGroup: 'secondary',
    viewCapability: 'view_connections',
    manageCapability: 'manage_connections',
  },
  {
    id: 'clearledgr/activity',
    title: 'Activity',
    subtitle: 'See recent changes.',
    icon: 'activity',
    navOrder: 40,
    defaultPinned: false,
    canHide: true,
    menuGroup: 'secondary',
    viewCapability: 'view_activity',
  },
  {
    id: 'clearledgr/vendor-onboarding',
    title: 'Vendor Onboarding',
    subtitle: 'Track vendor onboarding from invite to activation.',
    icon: 'vendors',
    navOrder: 15,
    defaultPinned: true,
    canHide: false,
    menuGroup: 'primary',
    hideTopbar: true,
    viewCapability: 'view_vendors',
  },
  {
    id: 'clearledgr/vendors',
    title: 'Vendors',
    subtitle: 'Vendor history and context.',
    icon: 'vendors',
    navOrder: 50,
    defaultPinned: false,
    canHide: true,
    menuGroup: 'secondary',
    viewCapability: 'view_vendors',
  },
  {
    id: 'clearledgr/templates',
    title: 'Templates',
    subtitle: 'Reusable email drafts.',
    icon: 'templates',
    navOrder: 55,
    defaultPinned: false,
    canHide: true,
    menuGroup: 'secondary',
    viewCapability: 'view_templates',
  },
  {
    id: 'clearledgr/rules',
    title: 'Approval Rules',
    subtitle: 'Rules for when invoices auto-approve or wait.',
    icon: 'rules',
    navOrder: 60,
    defaultPinned: false,
    canHide: true,
    menuGroup: 'secondary',
    viewCapability: 'view_rules',
    manageCapability: 'manage_rules',
  },
  {
    id: 'clearledgr/settings',
    title: 'Settings',
    subtitle: 'Team, workspace, and billing.',
    icon: 'settings',
    navOrder: 70,
    defaultPinned: false,
    canHide: true,
    menuGroup: 'secondary',
    viewCapability: 'view_settings',
  },
  {
    id: 'clearledgr/reconciliation',
    title: 'Reconciliation',
    subtitle: 'Early reconciliation tools.',
    icon: 'recon',
    navOrder: 100,
    defaultPinned: false,
    canHide: true,
    menuGroup: 'secondary',
    viewCapability: 'view_reconciliation',
  },
  {
    id: 'clearledgr/health',
    title: 'System Status',
    subtitle: 'Check what is connected and what needs attention.',
    icon: 'health',
    navOrder: 110,
    defaultPinned: false,
    canHide: true,
    menuGroup: 'hidden',
    viewCapability: 'view_system_status',
  },
  {
    id: 'clearledgr/reports',
    title: 'Reports',
    subtitle: 'A quick view of volume, spend, and risk.',
    icon: 'reports',
    navOrder: 115,
    defaultPinned: false,
    canHide: true,
    menuGroup: 'secondary',
    viewCapability: 'view_reports',
  },
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

function resolveCapabilities(options = {}) {
  if (options?.capabilities && typeof options.capabilities === 'object') {
    return options.capabilities;
  }
  if ('includeAdmin' in options || 'includeOps' in options) {
    const includeAdmin = Boolean(options.includeAdmin);
    const includeOps = options.includeOps !== false;
    return {
      view_home: true,
      view_pipeline: true,
      view_review: includeOps,
      view_upcoming: includeOps,
      view_connections: includeAdmin,
      view_activity: includeOps,
      view_vendors: includeOps,
      view_templates: includeOps,
      view_rules: includeAdmin,
      view_settings: includeAdmin,
      view_reconciliation: includeOps,
      view_system_status: includeAdmin,
      view_reports: includeAdmin,
      manage_connections: includeAdmin,
      manage_rules: includeAdmin,
      manage_admin_pages: includeAdmin,
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
