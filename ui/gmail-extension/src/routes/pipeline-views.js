import { getExceptionLabel, getIssueSummary } from '../utils/formatters.js';
import { getWorkStateNotice, hasErpPostingConnection } from '../utils/work-actions.js';

const LEGACY_STORAGE_PREFIX = 'clearledgr_pipeline_view_preferences_v1';
const STORAGE_PREFIX = 'clearledgr_pipeline_view_preferences_v2';
const NAVIGATION_PREFIX = 'clearledgr_pipeline_navigation_v1';
const MAX_CUSTOM_VIEWS = 8;
const MAX_PINNED_VIEW_REFS = 6;

const SLICE_ALIASES = {
  approval_backlog: 'waiting_on_approval',
  exceptions: 'blocked_exception',
};

const SORT_COLUMNS = new Set([
  'queue_age',
  'due_date',
  'amount',
  'updated_at',
  'approval_wait',
  'priority',
  'vendor',
  'state',
  'invoice',
]);

export const PIPELINE_BUILTIN_SLICES = [
  { id: 'all_open', label: 'All open', description: 'Every invoice still moving through AP.' },
  { id: 'waiting_on_approval', label: 'Waiting on approval', description: 'Invoices routed to approvers and still waiting.' },
  { id: 'ready_to_post', label: 'Ready to post', description: 'Approved invoices ready for ERP posting.' },
  { id: 'needs_info', label: 'Needs info', description: 'Invoices blocked on vendor or field follow-up.' },
  { id: 'failed_post', label: 'Failed post', description: 'Invoices that need ERP recovery or connector setup before posting can continue.' },
  { id: 'blocked_exception', label: 'Review / exception', description: 'Invoices paused by entity, policy, budget, field, PO, or setup issues.' },
  { id: 'due_soon', label: 'Due soon', description: 'Open invoices due within the next 7 days.' },
  { id: 'overdue', label: 'Overdue', description: 'Open invoices already past due.' },
];

function buildDefaultFilters() {
  return {
    vendor: '',
    due: 'all',
    blocker: 'all',
    amount: 'all',
    approvalAge: 'all',
    erpStatus: 'all',
  };
}

export const PIPELINE_STARTER_VIEWS = [
  {
    id: 'approval_chase',
    name: 'Approval chase',
    description: 'Longest-waiting approvals first.',
    snapshot: {
      activeSliceId: 'waiting_on_approval',
      viewMode: 'table',
      sortCol: 'approval_wait',
      sortDir: 'desc',
      filters: buildDefaultFilters(),
    },
  },
  {
    id: 'urgent_due',
    name: 'Urgent due',
    description: 'Invoices due soon with nearest due dates first.',
    snapshot: {
      activeSliceId: 'due_soon',
      viewMode: 'table',
      sortCol: 'due_date',
      sortDir: 'asc',
      filters: buildDefaultFilters(),
    },
  },
  {
    id: 'posting_watch',
    name: 'Posting watch',
    description: 'Highest-value approved invoices ready for ERP posting.',
    snapshot: {
      activeSliceId: 'ready_to_post',
      viewMode: 'table',
      sortCol: 'amount',
      sortDir: 'desc',
      filters: buildDefaultFilters(),
    },
  },
  {
    id: 'exception_triage',
    name: 'Exception triage',
    description: 'Blocked invoices ordered by queue age.',
    snapshot: {
      activeSliceId: 'blocked_exception',
      viewMode: 'table',
      sortCol: 'queue_age',
      sortDir: 'desc',
      filters: {
        ...buildDefaultFilters(),
        blocker: 'all',
      },
    },
  },
];

function normalizeText(value, fallback = '') {
  return String(value || '').trim() || fallback;
}

function buildFallbackBlockerCopy(kind, type, source = {}) {
  const normalizedState = normalizePipelineState(source?.state);
  if (kind === 'approval') {
    return {
      chip_label: 'Waiting on approver',
      title: 'Waiting on approver',
      detail: getWorkStateNotice(normalizedState, 'invoice', source) || 'Approval is still pending.',
    };
  }
  if (kind === 'info') {
    return {
      chip_label: 'Waiting on vendor',
      title: 'Waiting on vendor',
      detail: getWorkStateNotice(normalizedState, 'invoice', source) || 'Vendor information is still missing.',
    };
  }
  if (kind === 'erp') {
    if (!hasErpPostingConnection(source)) {
      return {
        chip_label: 'ERP not connected',
        title: 'ERP is not connected',
        detail: 'Connect QuickBooks, Xero, NetSuite, or SAP before Clearledgr can post this invoice.',
      };
    }
    return {
      chip_label: 'ERP retry',
      title: 'ERP posting needs attention',
      detail: getIssueSummary(source) || 'ERP posting needs review before this invoice can continue.',
    };
  }
  if (kind === 'exception') {
    const label = getExceptionLabel(type) || 'Needs review';
    return {
      chip_label: label,
      title: label,
      detail: getIssueSummary(source) || 'Clearledgr needs review before this invoice can continue.',
    };
  }
  if (kind === 'confidence') {
    return {
      chip_label: 'Field review',
      title: 'Needs a field check',
      detail: normalizeText(source?.workflow_paused_reason) || 'Check the extracted fields before Clearledgr continues.',
    };
  }
  if (kind === 'budget') {
    return {
      chip_label: 'Budget review',
      title: 'Budget review required',
      detail: 'A budget decision is still required before this invoice can continue.',
    };
  }
  if (kind === 'entity') {
    return {
      chip_label: 'Entity review',
      title: 'Entity route needs review',
      detail: normalizeText(source?.entity_route_reason) || 'Choose the correct legal entity before this invoice can continue.',
    };
  }
  if (kind === 'po') {
    return {
      chip_label: 'PO review',
      title: 'PO review required',
      detail: getIssueSummary(source) || 'PO matching still needs review before this invoice can continue.',
    };
  }
  if (kind === 'processing') {
    return {
      chip_label: 'Processing issue',
      title: 'Processing issue',
      detail: 'Clearledgr needs another pass before this invoice can continue.',
    };
  }
  return { chip_label: '', title: '', detail: '' };
}

function enrichPipelineBlocker(blocker = {}, source = {}) {
  const kind = normalizeText(blocker?.kind).toLowerCase();
  const type = normalizeText(blocker?.type).toLowerCase();
  const fallback = buildFallbackBlockerCopy(kind, type, source);
  return {
    ...blocker,
    kind,
    type,
    chip_label: normalizeText(blocker?.chip_label, fallback.chip_label),
    title: normalizeText(blocker?.title, fallback.title),
    detail: normalizeText(blocker?.detail, fallback.detail),
    field: normalizeText(blocker?.field).toLowerCase(),
    severity: normalizeText(blocker?.severity).toLowerCase(),
    code: normalizeText(blocker?.code).toLowerCase(),
  };
}

function normalizeUserEmail(value) {
  return normalizeText(value).toLowerCase();
}

function resolvePipelineScope(scopeOrOrgId, maybeUserEmail = '') {
  if (scopeOrOrgId && typeof scopeOrOrgId === 'object') {
    return {
      orgId: normalizeText(scopeOrOrgId.orgId || scopeOrOrgId.organizationId, 'default'),
      userEmail: normalizeUserEmail(scopeOrOrgId.userEmail || scopeOrOrgId.email || maybeUserEmail),
    };
  }
  return {
    orgId: normalizeText(scopeOrOrgId, 'default'),
    userEmail: normalizeUserEmail(maybeUserEmail),
  };
}

function getLegacyPipelinePreferenceKey(orgId) {
  return `${LEGACY_STORAGE_PREFIX}:${normalizeText(orgId, 'default')}`;
}

function getNavigationKey(scopeOrOrgId, maybeUserEmail = '') {
  const scope = resolvePipelineScope(scopeOrOrgId, maybeUserEmail);
  return `${NAVIGATION_PREFIX}:${scope.orgId}:${scope.userEmail || 'anonymous'}`;
}

function normalizePipelineSliceId(sliceId) {
  const normalized = normalizeText(sliceId, 'all_open');
  return SLICE_ALIASES[normalized] || normalized;
}

function normalizeSortColumn(sortCol, fallback = 'queue_age') {
  const normalized = normalizeText(sortCol, fallback);
  return SORT_COLUMNS.has(normalized) ? normalized : fallback;
}

function normalizeViewName(value) {
  return normalizeText(value).slice(0, 48);
}

function normalizeViewDescription(value) {
  return normalizeText(value).slice(0, 120);
}

function normalizeVendorFilter(value) {
  return normalizeText(value).slice(0, 80);
}

function normalizeSearchQuery(value) {
  return normalizeText(value).slice(0, 120);
}

function normalizeFilters(filters = {}) {
  return {
    vendor: normalizeVendorFilter(filters?.vendor),
    due: normalizeText(filters?.due, 'all'),
    blocker: normalizeText(filters?.blocker, 'all'),
    amount: normalizeText(filters?.amount, 'all'),
    approvalAge: normalizeText(filters?.approvalAge, 'all'),
    erpStatus: normalizeText(filters?.erpStatus, 'all'),
  };
}

function normalizeSnapshot(snapshot = {}) {
  return {
    activeSliceId: normalizePipelineSliceId(snapshot?.activeSliceId),
    viewMode: normalizeText(snapshot?.viewMode, 'table') === 'cards' ? 'cards' : 'table',
    sortCol: normalizeSortColumn(snapshot?.sortCol, 'queue_age'),
    sortDir: normalizeText(snapshot?.sortDir, 'desc') === 'asc' ? 'asc' : 'desc',
    filters: normalizeFilters(snapshot?.filters),
  };
}

function normalizePipelineViewRef(value) {
  const raw = normalizeText(value).toLowerCase();
  if (!raw) return '';
  if (raw.startsWith('starter:') || raw.startsWith('user:')) return raw;
  return `user:${raw}`;
}

function defaultPinnedViewRefs() {
  return ['starter:approval_chase', 'starter:urgent_due'];
}

function sanitizeCustomViews(customViews = []) {
  return (Array.isArray(customViews) ? customViews : [])
    .map((view) => ({
      id: normalizeText(view?.id),
      name: normalizeViewName(view?.name),
      description: normalizeViewDescription(view?.description),
      pinned: Boolean(view?.pinned),
      snapshot: normalizeSnapshot(view?.snapshot),
    }))
    .filter((view) => view.id && view.name)
    .slice(0, MAX_CUSTOM_VIEWS);
}

function normalizePinnedViewRefs(refs = [], customViews = []) {
  const explicitRefs = [...new Set((Array.isArray(refs) ? refs : [])
    .map((ref) => normalizePipelineViewRef(ref))
    .filter(Boolean))];
  if (explicitRefs.length > 0) return explicitRefs.slice(0, MAX_PINNED_VIEW_REFS);

  const legacyPinnedRefs = sanitizeCustomViews(customViews)
    .filter((view) => view.pinned)
    .map((view) => `user:${view.id}`);
  if (legacyPinnedRefs.length > 0) return legacyPinnedRefs.slice(0, MAX_PINNED_VIEW_REFS);

  return defaultPinnedViewRefs().slice(0, MAX_PINNED_VIEW_REFS);
}

function sanitizeStarterViews() {
  return (Array.isArray(PIPELINE_STARTER_VIEWS) ? PIPELINE_STARTER_VIEWS : []).map((view) => ({
    id: normalizeText(view?.id),
    name: normalizeViewName(view?.name),
    description: normalizeViewDescription(view?.description),
    scope: 'starter',
    snapshot: normalizeSnapshot(view?.snapshot),
  })).filter((view) => view.id && view.name);
}

function mergeCustomView(currentViews = [], incomingView = {}) {
  const existingViews = Array.isArray(currentViews) ? currentViews.filter((view) => view.id !== incomingView.id) : [];
  return sanitizeCustomViews([...existingViews, incomingView]);
}

function readStorageValue(key) {
  if (typeof window === 'undefined' || !window?.localStorage) {
    return null;
  }
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorageValue(key, value) {
  if (typeof window === 'undefined' || !window?.localStorage) {
    return;
  }
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* best effort */
  }
}

export function normalizePipelineState(state) {
  const normalized = normalizeText(state).toLowerCase();
  if (!normalized) return 'received';
  if (normalized === 'pending_approval') return 'needs_approval';
  if (normalized === 'posted') return 'posted_to_erp';
  return normalized;
}

export function isClosedPipelineState(state) {
  return ['posted_to_erp', 'closed', 'rejected'].includes(normalizePipelineState(state));
}

export function getPipelinePreferenceKey(scopeOrOrgId, maybeUserEmail = '') {
  const scope = resolvePipelineScope(scopeOrOrgId, maybeUserEmail);
  return `${STORAGE_PREFIX}:${scope.orgId}:${scope.userEmail || 'anonymous'}`;
}

export function defaultPipelinePreferences() {
  return {
    activeSliceId: 'all_open',
    viewMode: 'table',
    sortCol: 'queue_age',
    sortDir: 'desc',
    filters: buildDefaultFilters(),
    customViews: [],
    pinnedViewRefs: defaultPinnedViewRefs(),
  };
}

export function defaultPipelineNavigation() {
  return {
    focusItemId: '',
    focusThreadId: '',
    focusMessageId: '',
    preferredSliceId: '',
    source: '',
    requestedAt: '',
  };
}

export function normalizePipelinePreferences(value = {}) {
  const customViews = sanitizeCustomViews(value?.customViews);
  const defaults = defaultPipelinePreferences();
  return {
    activeSliceId: normalizePipelineSliceId(value?.activeSliceId || defaults.activeSliceId),
    viewMode: normalizeSnapshot(value).viewMode,
    sortCol: normalizeSortColumn(value?.sortCol || defaults.sortCol, defaults.sortCol),
    sortDir: normalizeText(value?.sortDir || defaults.sortDir, defaults.sortDir) === 'asc' ? 'asc' : 'desc',
    filters: normalizeFilters(value?.filters),
    customViews,
    pinnedViewRefs: normalizePinnedViewRefs(value?.pinnedViewRefs, customViews),
  };
}

export function pipelinePreferencesEqual(left = {}, right = {}) {
  return JSON.stringify(normalizePipelinePreferences(left)) === JSON.stringify(normalizePipelinePreferences(right));
}

export function hasMeaningfulPipelinePreferences(value = {}) {
  const normalized = normalizePipelinePreferences(value);
  const defaults = defaultPipelinePreferences();
  return (
    normalized.activeSliceId !== defaults.activeSliceId
    || normalized.viewMode !== defaults.viewMode
    || normalized.sortCol !== defaults.sortCol
    || normalized.sortDir !== defaults.sortDir
    || JSON.stringify(normalized.filters) !== JSON.stringify(defaults.filters)
    || normalized.customViews.length > 0
    || JSON.stringify(normalized.pinnedViewRefs || []) !== JSON.stringify(defaults.pinnedViewRefs || [])
  );
}

export function getBootstrappedPipelinePreferences(bootstrap = {}) {
  return (
    bootstrap?.current_user?.preferences?.gmail_extension?.pipeline_views
    || bootstrap?.current_user?.preferences?.pipeline_views
    || null
  );
}

export function buildPipelinePreferencePatch(preferences = {}) {
  return {
    gmail_extension: {
      pipeline_views: normalizePipelinePreferences(preferences),
    },
  };
}

export function getPipelineViewRef(view = {}) {
  const scope = normalizeText(view?.scope, 'user').toLowerCase() === 'starter' ? 'starter' : 'user';
  const id = normalizeText(view?.id);
  if (!id) return '';
  return `${scope}:${id.toLowerCase()}`;
}

export function pipelineSnapshotsEqual(left = {}, right = {}) {
  return JSON.stringify(normalizeSnapshot(left)) === JSON.stringify(normalizeSnapshot(right));
}

export function getStarterPipelineViews(preferences = {}) {
  const normalized = normalizePipelinePreferences(preferences);
  const pinnedRefs = new Set(normalized.pinnedViewRefs || []);
  return sanitizeStarterViews().map((view) => ({
    ...view,
    pinned: pinnedRefs.has(getPipelineViewRef(view)),
  }));
}

export function getPersonalPipelineViews(preferences = {}) {
  const normalized = normalizePipelinePreferences(preferences);
  const pinnedRefs = new Set(normalized.pinnedViewRefs || []);
  return sanitizeCustomViews(normalized.customViews).map((view) => ({
    ...view,
    scope: 'user',
    pinned: pinnedRefs.has(getPipelineViewRef({ ...view, scope: 'user' })),
  }));
}

export function getAllPipelineViews(preferences = {}, { includeStarter = true } = {}) {
  const views = [];
  if (includeStarter) views.push(...getStarterPipelineViews(preferences));
  views.push(...getPersonalPipelineViews(preferences));
  return views;
}

export function resolvePipelineViewByRef(preferences = {}, viewRef = '', options = {}) {
  const normalizedRef = normalizePipelineViewRef(viewRef);
  if (!normalizedRef) return null;
  return getAllPipelineViews(preferences, options).find((view) => getPipelineViewRef(view) === normalizedRef) || null;
}

export function normalizePipelineNavigation(value = {}) {
  return {
    focusItemId: normalizeText(value?.focusItemId),
    focusThreadId: normalizeText(value?.focusThreadId),
    focusMessageId: normalizeText(value?.focusMessageId),
    preferredSliceId: normalizePipelineSliceId(value?.preferredSliceId || ''),
    source: normalizeText(value?.source),
    requestedAt: normalizeText(value?.requestedAt),
  };
}

export function readPipelinePreferences(scopeOrOrgId, maybeUserEmail = '') {
  const key = getPipelinePreferenceKey(scopeOrOrgId, maybeUserEmail);
  const raw = readStorageValue(key);
  if (raw) {
    try {
      return normalizePipelinePreferences(JSON.parse(raw));
    } catch {
      return defaultPipelinePreferences();
    }
  }

  const scope = resolvePipelineScope(scopeOrOrgId, maybeUserEmail);
  const legacyRaw = readStorageValue(getLegacyPipelinePreferenceKey(scope.orgId));
  if (!legacyRaw) return defaultPipelinePreferences();
  try {
    return normalizePipelinePreferences(JSON.parse(legacyRaw));
  } catch {
    return defaultPipelinePreferences();
  }
}

export function writePipelinePreferences(scopeOrOrgId, maybeUserEmailOrValue = '', maybeValue = null) {
  const hasExplicitUserEmail = typeof maybeUserEmailOrValue === 'string' || maybeUserEmailOrValue == null;
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrValue : '';
  const value = hasExplicitUserEmail ? maybeValue : maybeUserEmailOrValue;
  const normalized = normalizePipelinePreferences(value || {});
  writeStorageValue(
    getPipelinePreferenceKey(scopeOrOrgId, userEmail),
    JSON.stringify(normalized),
  );
  return normalized;
}

export function readPipelineNavigation(scopeOrOrgId, maybeUserEmail = '') {
  const raw = readStorageValue(getNavigationKey(scopeOrOrgId, maybeUserEmail));
  if (!raw) return defaultPipelineNavigation();
  try {
    return normalizePipelineNavigation(JSON.parse(raw));
  } catch {
    return defaultPipelineNavigation();
  }
}

export function writePipelineNavigation(scopeOrOrgId, maybeUserEmailOrValue = '', maybeValue = null) {
  const hasExplicitUserEmail = typeof maybeUserEmailOrValue === 'string' || maybeUserEmailOrValue == null;
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrValue : '';
  const value = hasExplicitUserEmail ? maybeValue : maybeUserEmailOrValue;
  const normalized = normalizePipelineNavigation(value || {});
  writeStorageValue(
    getNavigationKey(scopeOrOrgId, userEmail),
    JSON.stringify(normalized),
  );
  return normalized;
}

export function clearPipelineNavigation(scopeOrOrgId, maybeUserEmail = '') {
  return writePipelineNavigation(scopeOrOrgId, maybeUserEmail, defaultPipelineNavigation());
}

export function getSuggestedPipelineSlice(item = {}) {
  const state = normalizePipelineState(item.state);
  const dueDate = parseDate(item?.due_date);
  const blockers = getPipelineBlockerKinds(item);
  const now = new Date();

  if (state === 'needs_approval') return 'waiting_on_approval';
  if (state === 'ready_to_post') return 'ready_to_post';
  if (state === 'needs_info') return 'needs_info';
  if (state === 'failed_post') return 'failed_post';
  if (dueDate && !isClosedPipelineState(state) && diffInDays(dueDate, now) < 0) return 'overdue';
  if (blockers.some((kind) => ['entity', 'exception', 'confidence', 'budget', 'po', 'erp', 'processing'].includes(kind))) {
    return 'blocked_exception';
  }
  if (dueDate && !isClosedPipelineState(state) && diffInDays(dueDate, now) <= 7) return 'due_soon';
  return 'all_open';
}

export function focusPipelineItem(scopeOrOrgId, maybeUserEmailOrItem = '', maybeItem = null, maybeSource = '') {
  const hasExplicitUserEmail = typeof maybeUserEmailOrItem === 'string' || maybeUserEmailOrItem == null;
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrItem : '';
  const item = hasExplicitUserEmail ? maybeItem : maybeUserEmailOrItem;
  const source = hasExplicitUserEmail ? maybeSource : maybeItem;
  const normalizedItem = item && typeof item === 'object' ? item : {};
  return writePipelineNavigation(scopeOrOrgId, userEmail, {
    focusItemId: normalizeText(normalizedItem?.id),
    focusThreadId: normalizeText(normalizedItem?.thread_id || normalizedItem?.threadId),
    focusMessageId: normalizeText(normalizedItem?.message_id || normalizedItem?.messageId),
    preferredSliceId: getSuggestedPipelineSlice(normalizedItem),
    source: normalizeText(source),
    requestedAt: new Date().toISOString(),
  });
}

export function activatePipelineSlice(scopeOrOrgId, maybeUserEmailOrSliceId = '', maybeSliceId = null) {
  const hasExplicitUserEmail = typeof maybeSliceId === 'string';
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrSliceId : '';
  const sliceId = hasExplicitUserEmail ? maybeSliceId : maybeUserEmailOrSliceId;
  const current = readPipelinePreferences(scopeOrOrgId, userEmail);
  return writePipelinePreferences(scopeOrOrgId, userEmail, {
    ...current,
    activeSliceId: normalizePipelineSliceId(sliceId),
  });
}

export function createSavedPipelineView(scopeOrOrgId, maybeUserEmailOrValue = '', maybeValue = null) {
  const hasExplicitUserEmail = typeof maybeUserEmailOrValue === 'string' || maybeUserEmailOrValue == null;
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrValue : '';
  const value = hasExplicitUserEmail ? maybeValue : maybeUserEmailOrValue;
  const current = readPipelinePreferences(scopeOrOrgId, userEmail);
  const trimmedName = normalizeViewName(value?.name);
  if (!trimmedName) return current;
  const id = `view_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  const customViews = mergeCustomView(current.customViews, {
    id,
    name: trimmedName,
    description: normalizeViewDescription(value?.description),
    pinned: Boolean(value?.pinned),
    snapshot: normalizeSnapshot(value?.snapshot || current),
  });
  const pinnedViewRefs = Boolean(value?.pinned)
    ? [`user:${id}`, ...(current.pinnedViewRefs || []).filter((ref) => ref !== `user:${id}`)].slice(0, MAX_PINNED_VIEW_REFS)
    : current.pinnedViewRefs;
  return writePipelinePreferences(scopeOrOrgId, userEmail, {
    ...current,
    customViews,
    pinnedViewRefs,
  });
}

export function updateSavedPipelineView(scopeOrOrgId, maybeUserEmailOrViewId = '', maybeViewIdOrPatch = null, maybePatch = null) {
  const hasExplicitUserEmail = typeof maybeUserEmailOrViewId === 'string' && typeof maybeViewIdOrPatch === 'string';
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrViewId : '';
  const viewId = hasExplicitUserEmail ? maybeViewIdOrPatch : maybeUserEmailOrViewId;
  const patch = hasExplicitUserEmail ? maybePatch : maybeViewIdOrPatch;
  const current = readPipelinePreferences(scopeOrOrgId, userEmail);
  const customViews = sanitizeCustomViews(
    current.customViews.map((view) => (
      view.id === viewId
        ? {
            ...view,
            name: normalizeViewName(patch?.name || view.name),
            description: normalizeViewDescription(patch?.description ?? view.description),
            snapshot: normalizeSnapshot(patch?.snapshot || view.snapshot),
          }
        : view
    ))
  );
  return writePipelinePreferences(scopeOrOrgId, userEmail, {
    ...current,
    customViews,
  });
}

export function pinPipelineView(scopeOrOrgId, maybeUserEmailOrViewRef = '', maybeViewRef = null) {
  const hasExplicitUserEmail = typeof maybeViewRef === 'string';
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrViewRef : '';
  const viewRef = hasExplicitUserEmail ? maybeViewRef : maybeUserEmailOrViewRef;
  const normalizedRef = normalizePipelineViewRef(viewRef);
  if (!normalizedRef) return readPipelinePreferences(scopeOrOrgId, userEmail);
  const current = readPipelinePreferences(scopeOrOrgId, userEmail);
  return writePipelinePreferences(scopeOrOrgId, userEmail, {
    ...current,
    pinnedViewRefs: [normalizedRef, ...(current.pinnedViewRefs || []).filter((ref) => ref !== normalizedRef)].slice(0, MAX_PINNED_VIEW_REFS),
  });
}

export function unpinPipelineView(scopeOrOrgId, maybeUserEmailOrViewRef = '', maybeViewRef = null) {
  const hasExplicitUserEmail = typeof maybeViewRef === 'string';
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrViewRef : '';
  const viewRef = hasExplicitUserEmail ? maybeViewRef : maybeUserEmailOrViewRef;
  const normalizedRef = normalizePipelineViewRef(viewRef);
  const current = readPipelinePreferences(scopeOrOrgId, userEmail);
  return writePipelinePreferences(scopeOrOrgId, userEmail, {
    ...current,
    pinnedViewRefs: (current.pinnedViewRefs || []).filter((ref) => ref !== normalizedRef),
  });
}

export function pinSavedPipelineView(scopeOrOrgId, maybeUserEmailOrViewId = '', maybeViewId = null) {
  const hasExplicitUserEmail = typeof maybeViewId === 'string';
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrViewId : '';
  const viewId = hasExplicitUserEmail ? maybeViewId : maybeUserEmailOrViewId;
  return pinPipelineView(scopeOrOrgId, userEmail, `user:${viewId}`);
}

export function unpinSavedPipelineView(scopeOrOrgId, maybeUserEmailOrViewId = '', maybeViewId = null) {
  const hasExplicitUserEmail = typeof maybeViewId === 'string';
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrViewId : '';
  const viewId = hasExplicitUserEmail ? maybeViewId : maybeUserEmailOrViewId;
  return unpinPipelineView(scopeOrOrgId, userEmail, `user:${viewId}`);
}

export function removeSavedPipelineView(scopeOrOrgId, maybeUserEmailOrViewId = '', maybeViewId = null) {
  const hasExplicitUserEmail = typeof maybeViewId === 'string';
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrViewId : '';
  const viewId = hasExplicitUserEmail ? maybeViewId : maybeUserEmailOrViewId;
  const current = readPipelinePreferences(scopeOrOrgId, userEmail);
  const viewRef = `user:${normalizeText(viewId).toLowerCase()}`;
  return writePipelinePreferences(scopeOrOrgId, userEmail, {
    ...current,
    customViews: current.customViews.filter((view) => view.id !== viewId),
    pinnedViewRefs: (current.pinnedViewRefs || []).filter((ref) => ref !== viewRef),
  });
}

export function getPinnedPipelineViews(preferences = {}) {
  const normalized = normalizePipelinePreferences(preferences);
  return (normalized.pinnedViewRefs || [])
    .map((viewRef) => resolvePipelineViewByRef(normalized, viewRef))
    .filter(Boolean);
}

function parseDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function diffInDays(left, right) {
  const diffMs = left.getTime() - right.getTime();
  return Math.floor(diffMs / 86400000);
}

function diffInMinutes(left, right) {
  const diffMs = right.getTime() - left.getTime();
  return Math.max(0, Math.floor(diffMs / 60000));
}

export function getQueueEnteredAt(item = {}) {
  return parseDate(item?.queue_entered_at || item?.received_at || item?.created_at || item?.updated_at);
}

export function getQueueAgeMinutes(item = {}, now = new Date()) {
  const startedAt = getQueueEnteredAt(item);
  if (!startedAt) return 0;
  return diffInMinutes(startedAt, now);
}

export function getApprovalRequestedAt(item = {}) {
  const state = normalizePipelineState(item.state);
  if (state !== 'needs_approval') return null;
  return parseDate(item?.approval_requested_at || item?.updated_at || item?.created_at);
}

export function getApprovalWaitMinutes(item = {}, now = new Date()) {
  const startedAt = getApprovalRequestedAt(item);
  if (!startedAt) return 0;
  return diffInMinutes(startedAt, now);
}

export function getErpStatus(item = {}) {
  const source = item && typeof item === 'object' ? item : {};
  const state = normalizePipelineState(source.state);
  const normalizedStatus = normalizeText(source?.erp_status).toLowerCase();
  if (normalizedStatus) return normalizedStatus;
  const hasConnection = hasErpPostingConnection(source);
  if (state === 'posted_to_erp' || state === 'closed' || source?.erp_reference || source?.erp_bill_id) return 'posted';
  if (!hasConnection && ['approved', 'ready_to_post', 'failed_post'].includes(state)) return 'not_connected';
  if (state === 'failed_post') return 'failed';
  if (state === 'ready_to_post' || state === 'approved') return 'ready';
  if (hasConnection) return 'connected';
  return 'not_connected';
}

export function getPipelineBlockers(item = {}) {
  const source = item && typeof item === 'object' ? item : {};
  const existing = Array.isArray(source?.pipeline_blockers) ? source.pipeline_blockers : [];
  if (existing.length > 0) {
    return existing
      .map((blocker) => enrichPipelineBlocker(blocker, source))
      .filter((blocker) => blocker.kind && blocker.type);
  }

  const blockers = [];
  const state = normalizePipelineState(source.state);
  const exceptionCode = normalizeText(source?.exception_code).toLowerCase();
  const budgetStatus = normalizeText(source?.budget_status).toLowerCase();
  const confidence = Number(source?.confidence);

  if (state === 'needs_approval') {
    blockers.push({ kind: 'approval', type: 'approval_waiting' });
  }
  if (state === 'needs_info') {
    blockers.push({ kind: 'info', type: 'needs_info' });
  }
  if (state === 'failed_post') {
    blockers.push({
      kind: 'erp',
      type: hasErpPostingConnection(source) ? 'posting_failed' : 'erp_not_connected',
    });
  }
  if (String(source?.entity_routing_status || source?.entity_routing?.status || '').trim().toLowerCase() === 'needs_review') {
    blockers.push({ kind: 'entity', type: 'entity_review' });
  }
  if (exceptionCode === 'planner_failed' && !source?.requires_field_review) {
    blockers.push({ kind: 'processing', type: 'processing_issue' });
  } else if (exceptionCode && exceptionCode !== 'planner_failed') {
    blockers.push({ kind: 'exception', type: exceptionCode });
  }
  if (source?.requires_field_review || (Number.isFinite(confidence) && confidence < 0.95)) {
    blockers.push({ kind: 'confidence', type: 'confidence_review' });
  }
  if (source?.budget_requires_decision || ['critical', 'exceeded'].includes(budgetStatus)) {
    blockers.push({ kind: 'budget', type: 'budget_review' });
  }
  if (exceptionCode && exceptionCode !== 'planner_failed' && (exceptionCode.includes('po') || (!source?.po_number && exceptionCode))) {
    blockers.push({ kind: 'po', type: exceptionCode });
  }

  return blockers.map((blocker) => enrichPipelineBlocker(blocker, source));
}

export function getPipelineBlockerKinds(item = {}) {
  return [...new Set(getPipelineBlockers(item).map((blocker) => blocker.kind).filter(Boolean))];
}

export function matchesPipelineSlice(item = {}, sliceId = 'all_open', now = new Date()) {
  const state = normalizePipelineState(item.state);
  const dueDate = parseDate(item?.due_date);
  const blockers = getPipelineBlockerKinds(item);
  const normalizedSlice = normalizePipelineSliceId(sliceId);

  switch (normalizedSlice) {
    case 'all':
      return true;
    case 'all_open':
      return !isClosedPipelineState(state);
    case 'waiting_on_approval':
      return state === 'needs_approval';
    case 'ready_to_post':
      return state === 'ready_to_post';
    case 'needs_info':
      return state === 'needs_info';
    case 'failed_post':
      return state === 'failed_post';
    case 'blocked_exception':
      if (isClosedPipelineState(state)) return false;
      return blockers.some((kind) => ['entity', 'exception', 'confidence', 'budget', 'po', 'erp', 'processing'].includes(kind));
    case 'due_soon':
      if (!dueDate || isClosedPipelineState(state)) return false;
      return diffInDays(dueDate, now) >= 0 && diffInDays(dueDate, now) <= 7;
    case 'overdue':
      if (!dueDate || isClosedPipelineState(state)) return false;
      return diffInDays(dueDate, now) < 0;
    default:
      return true;
  }
}

export function matchesPipelineFilters(item = {}, filters = {}, now = new Date()) {
  const state = normalizePipelineState(item.state);
  const dueDate = parseDate(item?.due_date);
  const blockers = getPipelineBlockerKinds(item);
  const amount = Number(item?.amount || 0);
  const vendor = normalizeText(item?.vendor_name || item?.vendor || '').toLowerCase();
  const approvalWaitMinutes = getApprovalWaitMinutes(item, now);
  const erpStatus = getErpStatus(item);
  const normalizedFilters = normalizeFilters(filters);

  if (normalizedFilters.vendor && !vendor.includes(normalizedFilters.vendor.toLowerCase())) return false;

  if (normalizedFilters.due === 'overdue') {
    if (!dueDate || diffInDays(dueDate, now) >= 0) return false;
  } else if (normalizedFilters.due === 'due_7d') {
    if (!dueDate) return false;
    const days = diffInDays(dueDate, now);
    if (days < 0 || days > 7) return false;
  } else if (normalizedFilters.due === 'no_due' && dueDate) {
    return false;
  }

  if (normalizedFilters.blocker !== 'all' && !blockers.includes(normalizedFilters.blocker)) return false;

  if (normalizedFilters.amount === 'under_1k' && amount >= 1000) return false;
  if (normalizedFilters.amount === '1k_10k' && (amount < 1000 || amount > 10000)) return false;
  if (normalizedFilters.amount === 'over_10k' && amount <= 10000) return false;

  if (normalizedFilters.approvalAge !== 'all') {
    if (state !== 'needs_approval') return false;
    if (normalizedFilters.approvalAge === 'under_24h' && approvalWaitMinutes >= 1440) return false;
    if (normalizedFilters.approvalAge === '1d_3d' && (approvalWaitMinutes < 1440 || approvalWaitMinutes > 4320)) return false;
    if (normalizedFilters.approvalAge === 'over_3d' && approvalWaitMinutes <= 4320) return false;
  }

  if (normalizedFilters.erpStatus !== 'all' && erpStatus !== normalizedFilters.erpStatus) return false;

  return true;
}

export function itemMatchesSearch(item = {}, searchQuery = '') {
  const q = normalizeSearchQuery(searchQuery).toLowerCase();
  if (!q) return true;
  return [
    item.vendor_name,
    item.vendor,
    item.invoice_number,
    item.subject,
    item.po_number,
    item.sender,
  ].some((value) => String(value || '').toLowerCase().includes(q));
}

export function sortPipelineItems(items = [], sortCol = 'queue_age', sortDir = 'desc', now = new Date()) {
  const direction = sortDir === 'asc' ? 1 : -1;
  const normalizedSortCol = normalizeSortColumn(sortCol, 'queue_age');

  return [...items].sort((left, right) => {
    let leftValue;
    let rightValue;
    switch (normalizedSortCol) {
      case 'vendor':
        leftValue = String(left.vendor_name || left.vendor || '').toLowerCase();
        rightValue = String(right.vendor_name || right.vendor || '').toLowerCase();
        break;
      case 'amount':
        leftValue = Number(left.amount || 0);
        rightValue = Number(right.amount || 0);
        break;
      case 'invoice':
        leftValue = String(left.invoice_number || '').toLowerCase();
        rightValue = String(right.invoice_number || '').toLowerCase();
        break;
      case 'due_date':
        leftValue = parseDate(left.due_date || left.created_at)?.getTime() || 0;
        rightValue = parseDate(right.due_date || right.created_at)?.getTime() || 0;
        break;
      case 'updated_at':
        leftValue = parseDate(left.updated_at || left.created_at)?.getTime() || 0;
        rightValue = parseDate(right.updated_at || right.created_at)?.getTime() || 0;
        break;
      case 'approval_wait':
        leftValue = getApprovalWaitMinutes(left, now);
        rightValue = getApprovalWaitMinutes(right, now);
        break;
      case 'state':
        leftValue = normalizePipelineState(left.state);
        rightValue = normalizePipelineState(right.state);
        break;
      case 'priority':
        leftValue = Number(left.priority_score || 0);
        rightValue = Number(right.priority_score || 0);
        break;
      case 'queue_age':
      default:
        leftValue = getQueueAgeMinutes(left, now);
        rightValue = getQueueAgeMinutes(right, now);
        break;
    }

    if (leftValue < rightValue) return -1 * direction;
    if (leftValue > rightValue) return 1 * direction;

    const leftUpdatedAt = parseDate(left.updated_at || left.created_at)?.getTime() || 0;
    const rightUpdatedAt = parseDate(right.updated_at || right.created_at)?.getTime() || 0;
    if (leftUpdatedAt < rightUpdatedAt) return 1;
    if (leftUpdatedAt > rightUpdatedAt) return -1;
    return 0;
  });
}

export function filterPipelineItems(items = [], options = {}) {
  const {
    activeSliceId = 'all_open',
    filters = {},
    searchQuery = '',
    sortCol = 'queue_age',
    sortDir = 'desc',
    now = new Date(),
  } = options;

  return sortPipelineItems(
    items
      .filter((item) => matchesPipelineSlice(item, activeSliceId, now))
      .filter((item) => matchesPipelineFilters(item, filters, now))
      .filter((item) => itemMatchesSearch(item, searchQuery)),
    sortCol,
    sortDir,
    now,
  );
}

export function countItemsForSlice(items = [], sliceId = 'all_open', now = new Date()) {
  return items.filter((item) => matchesPipelineSlice(item, sliceId, now)).length;
}

export function buildPipelineSliceCounts(items = [], now = new Date()) {
  return Object.fromEntries(
    PIPELINE_BUILTIN_SLICES.map((slice) => [slice.id, countItemsForSlice(items, slice.id, now)])
  );
}
