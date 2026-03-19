const STORAGE_PREFIX = 'clearledgr_pipeline_view_preferences_v1';

export const PIPELINE_BUILTIN_SLICES = [
  { id: 'all_open', label: 'All open', description: 'Every invoice still moving through AP.' },
  { id: 'approval_backlog', label: 'Approval backlog', description: 'Invoices waiting on approvers.' },
  { id: 'ready_to_post', label: 'Ready to post', description: 'Approved invoices ready for ERP posting.' },
  { id: 'needs_info', label: 'Needs info', description: 'Invoices missing required fields or vendor follow-up.' },
  { id: 'exceptions', label: 'Exceptions', description: 'Invoices blocked by policy, confidence, or posting issues.' },
  { id: 'due_soon', label: 'Due soon', description: 'Open invoices due within the next 7 days.' },
];

export function normalizePipelineState(state) {
  const normalized = String(state || '').trim().toLowerCase();
  if (!normalized) return 'received';
  if (normalized === 'pending_approval') return 'needs_approval';
  if (normalized === 'posted') return 'posted_to_erp';
  return normalized;
}

export function isClosedPipelineState(state) {
  return ['posted_to_erp', 'closed', 'rejected'].includes(normalizePipelineState(state));
}

export function getPipelinePreferenceKey(orgId) {
  return `${STORAGE_PREFIX}:${String(orgId || 'default').trim() || 'default'}`;
}

export function defaultPipelinePreferences() {
  return {
    activeSliceId: 'all_open',
    viewMode: 'table',
    sortCol: 'priority',
    sortDir: 'desc',
    filters: {
      state: 'all',
      due: 'all',
      blocker: 'all',
      amount: 'all',
    },
    customViews: [],
  };
}

function sanitizeCustomViews(customViews = []) {
  return (Array.isArray(customViews) ? customViews : [])
    .map((view) => ({
      id: String(view?.id || '').trim(),
      name: String(view?.name || '').trim(),
      snapshot: {
        activeSliceId: String(view?.snapshot?.activeSliceId || 'all_open').trim() || 'all_open',
        viewMode: String(view?.snapshot?.viewMode || 'table').trim() || 'table',
        sortCol: String(view?.snapshot?.sortCol || 'priority').trim() || 'priority',
        sortDir: String(view?.snapshot?.sortDir || 'desc').trim() === 'asc' ? 'asc' : 'desc',
        filters: {
          state: String(view?.snapshot?.filters?.state || 'all').trim() || 'all',
          due: String(view?.snapshot?.filters?.due || 'all').trim() || 'all',
          blocker: String(view?.snapshot?.filters?.blocker || 'all').trim() || 'all',
          amount: String(view?.snapshot?.filters?.amount || 'all').trim() || 'all',
        },
      },
    }))
    .filter((view) => view.id && view.name)
    .slice(0, 8);
}

export function normalizePipelinePreferences(value = {}) {
  const defaults = defaultPipelinePreferences();
  return {
    activeSliceId: String(value?.activeSliceId || defaults.activeSliceId).trim() || defaults.activeSliceId,
    viewMode: String(value?.viewMode || defaults.viewMode).trim() === 'cards' ? 'cards' : 'table',
    sortCol: String(value?.sortCol || defaults.sortCol).trim() || defaults.sortCol,
    sortDir: String(value?.sortDir || defaults.sortDir).trim() === 'asc' ? 'asc' : 'desc',
    filters: {
      state: String(value?.filters?.state || defaults.filters.state).trim() || defaults.filters.state,
      due: String(value?.filters?.due || defaults.filters.due).trim() || defaults.filters.due,
      blocker: String(value?.filters?.blocker || defaults.filters.blocker).trim() || defaults.filters.blocker,
      amount: String(value?.filters?.amount || defaults.filters.amount).trim() || defaults.filters.amount,
    },
    customViews: sanitizeCustomViews(value?.customViews),
  };
}

export function readPipelinePreferences(orgId) {
  if (typeof window === 'undefined' || !window?.localStorage) {
    return defaultPipelinePreferences();
  }
  try {
    const raw = window.localStorage.getItem(getPipelinePreferenceKey(orgId));
    if (!raw) return defaultPipelinePreferences();
    return normalizePipelinePreferences(JSON.parse(raw));
  } catch {
    return defaultPipelinePreferences();
  }
}

export function writePipelinePreferences(orgId, value = {}) {
  const normalized = normalizePipelinePreferences(value);
  if (typeof window !== 'undefined' && window?.localStorage) {
    try {
      window.localStorage.setItem(getPipelinePreferenceKey(orgId), JSON.stringify(normalized));
    } catch {
      /* best effort */
    }
  }
  return normalized;
}

export function activatePipelineSlice(orgId, sliceId) {
  const current = readPipelinePreferences(orgId);
  return writePipelinePreferences(orgId, {
    ...current,
    activeSliceId: sliceId,
  });
}

export function createSavedPipelineView(orgId, { name, snapshot }) {
  const current = readPipelinePreferences(orgId);
  const trimmedName = String(name || '').trim();
  if (!trimmedName) return current;
  const id = `view_${Date.now().toString(36)}`;
  const customViews = sanitizeCustomViews([
    ...current.customViews,
    {
      id,
      name: trimmedName,
      snapshot: normalizePipelinePreferences(snapshot || current),
    },
  ]);
  return writePipelinePreferences(orgId, {
    ...current,
    customViews,
  });
}

export function removeSavedPipelineView(orgId, viewId) {
  const current = readPipelinePreferences(orgId);
  return writePipelinePreferences(orgId, {
    ...current,
    customViews: current.customViews.filter((view) => view.id !== viewId),
  });
}

function parseDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function diffInDays(left, right) {
  const diffMs = left.getTime() - right.getTime();
  return Math.floor(diffMs / 86400000);
}

export function getPipelineBlockerKinds(item = {}) {
  const blockers = new Set();
  const state = normalizePipelineState(item.state);
  const exceptionCode = String(item?.exception_code || '').trim().toLowerCase();
  const budgetStatus = String(item?.budget_status || '').trim().toLowerCase();
  const confidence = Number(item?.confidence);

  if (state === 'needs_approval') blockers.add('approval');
  if (state === 'needs_info') blockers.add('info');
  if (state === 'failed_post') blockers.add('erp');
  if (exceptionCode) blockers.add('exception');
  if ((item?.requires_field_review || Number.isFinite(confidence) && confidence < 0.95)) blockers.add('confidence');
  if (item?.budget_requires_decision || ['critical', 'exceeded'].includes(budgetStatus)) blockers.add('budget');
  if (exceptionCode.includes('po') || (!item?.po_number && exceptionCode)) blockers.add('po');

  return Array.from(blockers);
}

export function matchesPipelineSlice(item = {}, sliceId = 'all_open', now = new Date()) {
  const state = normalizePipelineState(item.state);
  const dueDate = parseDate(item?.due_date);
  const blockers = getPipelineBlockerKinds(item);

  switch (sliceId) {
    case 'all':
      return true;
    case 'all_open':
      return !isClosedPipelineState(state);
    case 'approval_backlog':
      return state === 'needs_approval';
    case 'ready_to_post':
      return state === 'ready_to_post';
    case 'needs_info':
      return state === 'needs_info';
    case 'exceptions':
      return state === 'failed_post' || blockers.some((kind) => ['exception', 'confidence', 'budget', 'po', 'erp'].includes(kind));
    case 'due_soon':
      if (!dueDate || isClosedPipelineState(state)) return false;
      return diffInDays(dueDate, now) <= 7;
    default:
      return true;
  }
}

export function matchesPipelineFilters(item = {}, filters = {}, now = new Date()) {
  const state = normalizePipelineState(item.state);
  const dueDate = parseDate(item?.due_date);
  const blockers = getPipelineBlockerKinds(item);
  const amount = Number(item?.amount || 0);
  const normalizedFilters = normalizePipelinePreferences({ filters }).filters;

  if (normalizedFilters.state !== 'all' && state !== normalizedFilters.state) return false;

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

  return true;
}

export function itemMatchesSearch(item = {}, searchQuery = '') {
  const q = String(searchQuery || '').trim().toLowerCase();
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

export function sortPipelineItems(items = [], sortCol = 'priority', sortDir = 'desc') {
  const direction = sortDir === 'asc' ? 1 : -1;
  return [...items].sort((left, right) => {
    let leftValue;
    let rightValue;
    switch (sortCol) {
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
      case 'state':
        leftValue = normalizePipelineState(left.state);
        rightValue = normalizePipelineState(right.state);
        break;
      case 'priority':
      default:
        leftValue = Number(left.priority_score || 0);
        rightValue = Number(right.priority_score || 0);
        break;
    }
    if (leftValue < rightValue) return -1 * direction;
    if (leftValue > rightValue) return 1 * direction;
    return 0;
  });
}

export function filterPipelineItems(items = [], options = {}) {
  const {
    activeSliceId = 'all_open',
    filters = {},
    searchQuery = '',
    sortCol = 'priority',
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
