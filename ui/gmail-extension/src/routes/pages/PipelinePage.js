/**
 * AP Pipeline View — Gmail-native queue surface.
 * Streak-style doctrine: queue slices first, detail second, no dashboard sprawl.
 */
import { h } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDate, fmtDateTime, useAction } from '../route-helpers.js';
import { formatAmount, openSourceEmail } from '../../utils/formatters.js';
import { navigateToRecordDetail } from '../../utils/record-route.js';
import {
  getDocumentReferenceText,
  getDocumentTypeLabel,
  isInvoiceDocumentType,
  normalizeDocumentType,
} from '../../utils/document-types.js';
import {
  canEscalateApproval,
  needsEntityRouting,
} from '../../utils/work-actions.js';
import {
  PIPELINE_BUILTIN_SLICES,
  activatePipelineSlice,
  buildPipelinePreferencePatch,
  buildPipelineSliceCounts,
  clearPipelineNavigation,
  createSavedPipelineView,
  filterPipelineItems,
  focusPipelineItem,
  getAllPipelineViews,
  getBootstrappedPipelinePreferences,
  getApprovalWaitMinutes,
  getErpStatus,
  getPipelineBlockers,
  getPersonalPipelineViews,
  getPinnedPipelineViews,
  getPipelineBlockerKinds,
  getPipelineViewRef,
  getQueueAgeMinutes,
  getStarterPipelineViews,
  getSuggestedPipelineSlice,
  hasMeaningfulPipelinePreferences,
  normalizePipelineState,
  normalizePipelinePreferences,
  pinPipelineView,
  pipelineSnapshotsEqual,
  pipelinePreferencesEqual,
  readPipelineNavigation,
  readPipelinePreferences,
  removeSavedPipelineView,
  unpinPipelineView,
  updateSavedPipelineView,
  writePipelinePreferences,
} from '../pipeline-views.js';

const html = htm.bind(h);
const ACTIVE_AP_ITEM_STORAGE_KEY = 'clearledgr_active_ap_item_id';

const STATE_STYLES = {
  needs_approval: { bg: '#FEFCE8', text: '#A16207', label: 'Needs approval' },
  needs_info: { bg: '#FEFCE8', text: '#A16207', label: 'Needs info' },
  validated: { bg: '#EFF6FF', text: '#1D4ED8', label: 'Validated' },
  received: { bg: '#F1F5F9', text: '#64748B', label: 'Received' },
  approved: { bg: '#ECFDF5', text: '#059669', label: 'Approved' },
  ready_to_post: { bg: '#DCFCE7', text: '#166534', label: 'Ready to post' },
  posted_to_erp: { bg: '#ECFDF5', text: '#10B981', label: 'Posted' },
  closed: { bg: '#F1F5F9', text: '#64748B', label: 'Closed' },
  rejected: { bg: '#FEF2F2', text: '#DC2626', label: 'Rejected' },
  failed_post: { bg: '#FEF2F2', text: '#DC2626', label: 'Failed post' },
};

const BLOCKER_LABELS = {
  entity: 'Entity review',
  approval: 'Waiting on approver',
  info: 'Needs info',
  erp: 'ERP issue',
  exception: 'Needs review',
  confidence: 'Field review',
  budget: 'Budget review',
  po: 'PO review',
  processing: 'Processing issue',
};

const ERP_STATUS_LABELS = {
  ready: 'Ready',
  failed: 'Failed',
  connected: 'Connected',
  posted: 'Posted',
  not_connected: 'Not connected',
};

function getPipelineScope(orgId, userEmail) {
  return { orgId, userEmail };
}

function formatDurationMinutes(value) {
  const minutes = Number(value || 0);
  if (!Number.isFinite(minutes) || minutes <= 0) return '0m';
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 1440) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / 1440)}d`;
}

function StatePill({ state }) {
  const normalized = normalizePipelineState(state);
  const tone = STATE_STYLES[normalized] || { bg: '#F1F5F9', text: '#64748B', label: normalized.replace(/_/g, ' ') };
  return html`<span style="
    font-size:11px;font-weight:700;padding:4px 10px;border-radius:999px;
    background:${tone.bg};color:${tone.text};letter-spacing:0.02em;text-transform:uppercase;
  ">${tone.label}</span>`;
}

function SliceChip({ slice, count, active, onClick }) {
  return html`<button
    onClick=${onClick}
    style="
      display:flex;align-items:center;gap:7px;padding:7px 10px;border-radius:10px;
      border:1px solid ${active ? 'var(--accent)' : 'var(--border)'};
      background:${active ? 'var(--accent-soft)' : 'var(--surface)'};
      color:${active ? 'var(--accent-ink)' : 'var(--ink)'};
      cursor:pointer;font-family:inherit;text-align:left;min-width:128px;
    "
  >
    <span style="font-size:12px;font-weight:700">${slice.label}</span>
    <span style="margin-left:auto;font-size:11px;font-weight:700;color:inherit">${count}</span>
  </button>`;
}

function BlockerChip({ blocker }) {
  const kind = String(blocker?.kind || '').trim().toLowerCase();
  const label = blocker?.chip_label || blocker?.title || BLOCKER_LABELS[kind] || kind;
  return html`<span style="
    font-size:11px;font-weight:600;padding:3px 8px;border-radius:999px;
    background:#FFF7ED;border:1px solid #FED7AA;color:#9A3412;
  ">${label}</span>`;
}

function QueueMetricPill({ label, value, tone = 'default' }) {
  const tones = {
    default: { bg: 'var(--bg)', border: 'var(--border)', text: 'var(--ink)' },
    warning: { bg: '#FFFBEB', border: '#FCD34D', text: '#92400E' },
    success: { bg: '#ECFDF5', border: '#A7F3D0', text: '#166534' },
    danger: { bg: '#FEF2F2', border: '#FECACA', text: '#B91C1C' },
  };
  const palette = tones[tone] || tones.default;
  return html`<span style="
    display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:9px 11px;border-radius:10px;
    border:1px solid ${palette.border};background:${palette.bg};color:${palette.text};
    font-size:11px;font-weight:700;min-width:88px;
  ">
    <span style="font-family:var(--font-display);font-variant-numeric:tabular-nums;font-size:15px;line-height:1">${value}</span>
    <span style="opacity:0.72;text-transform:uppercase;letter-spacing:0.04em;font-size:10px">${label}</span>
  </span>`;
}

function PipelineBlockerSummary({ item, compact = false }) {
  const blockers = getPipelineBlockers(item);
  const primary = blockers[0];
  if (!primary) return null;
  const primaryDetail = String(primary?.detail || '').trim();
  const extraCount = blockers.length - 1;
  const secondaryDetail = extraCount > 0
    ? (String(item?.workflow_paused_reason || '').trim() || `+${extraCount} more blocker${extraCount === 1 ? '' : 's'}.`)
    : '';

  return html`
    <div style="
      margin-top:${compact ? '6px' : '0'};
      padding:${compact ? '6px 0 0' : '10px 12px'};
      border:${compact ? 'none' : '1px solid #FED7AA'};
      border-radius:${compact ? '0' : '12px'};
      background:${compact ? 'transparent' : '#FFF7ED'};
      display:flex;
      flex-direction:column;
      gap:4px;
    ">
      ${primary.title && html`<div style="font-size:12px;font-weight:700;color:#9A3412">
        ${primary.title}
      </div>`}
      ${primaryDetail && html`<div class="muted" style="font-size:12px;line-height:1.45">
        ${primaryDetail}
      </div>`}
      ${secondaryDetail && secondaryDetail !== primaryDetail
        ? html`<div class="muted" style="font-size:12px;line-height:1.45">${secondaryDetail}</div>`
        : null}
    </div>
  `;
}

function SavedViewChip({ view, active, onOpen, onTogglePin, onDelete }) {
  const scopeLabel = view.scope === 'starter' ? 'Starter' : 'Personal';
  return html`
    <div style="
      display:flex;align-items:center;gap:6px;padding:6px 8px;border-radius:999px;
      border:1px solid ${active ? 'var(--accent)' : 'var(--border)'};
      background:${active ? 'var(--accent-soft)' : 'var(--bg)'};
    ">
      <button class="btn-secondary btn-xs" onClick=${onOpen}>${view.name}</button>
      <span class="muted" style="font-size:11px;font-weight:700">${scopeLabel}</span>
      <button
        class="btn-ghost btn-xs"
        aria-label=${view.pinned ? 'Unpin saved view' : 'Pin saved view'}
        onClick=${onTogglePin}
        style="color:${view.pinned ? 'var(--accent-ink)' : 'var(--ink-muted)'}"
      >${view.pinned ? 'Pinned' : 'Pin'}</button>
      ${typeof onDelete === 'function'
        ? html`<button
            class="btn-ghost btn-xs"
            aria-label="Delete saved view"
            onClick=${onDelete}
            style="color:var(--ink-muted)"
          >×</button>`
        : null}
    </div>
  `;
}

function saveActiveItemId(itemId) {
  if (typeof window === 'undefined' || !window?.localStorage) return;
  try {
    window.localStorage.setItem(ACTIVE_AP_ITEM_STORAGE_KEY, String(itemId || ''));
  } catch {
    /* best effort */
  }
}

function openItemDetail(navigate, pipelineScope, item) {
  if (!item?.id) return;
  saveActiveItemId(item.id);
  focusPipelineItem(pipelineScope, item, 'pipeline');
  navigateToRecordDetail(navigate, item.id);
}

function openItemEmail(pipelineScope, item) {
  if (!item?.id) return false;
  saveActiveItemId(item.id);
  focusPipelineItem(pipelineScope, item, 'pipeline');
  return openSourceEmail(item);
}

function getAmountLabel(item) {
  const amount = Number(item?.amount);
  if (!Number.isFinite(amount)) return '—';
  return formatAmount(amount, item?.currency);
}

function getDocumentSummary(item) {
  const documentType = normalizeDocumentType(item?.document_type);
  const reference = String(item?.invoice_number || '').trim();
  return reference ? getDocumentReferenceText(documentType, reference) : getDocumentTypeLabel(documentType);
}

function isRouteableInvoiceItem(item) {
  if (!item) return false;
  if (!isInvoiceDocumentType(item?.document_type)) return false;
  const state = normalizePipelineState(item?.state);
  if (state !== 'validated') return false;
  if (Boolean(item?.requires_field_review)) return false;
  const blockers = getPipelineBlockerKinds(item);
  return !blockers.some((kind) => ['entity', 'confidence', 'exception', 'budget', 'po', 'erp', 'processing'].includes(kind));
}

function humanizeRouteFailure(reason, detail = '') {
  const token = String(reason || '').trim().toLowerCase();
  const safeDetail = String(detail || '').trim();
  if (token === 'autonomy_gate_blocked' && safeDetail) return safeDetail;
  const mapping = {
    state_not_validated: 'Only validated invoices can be routed for approval.',
    entity_route_review_required: 'Resolve the legal entity before routing this invoice for approval.',
    field_review_required: 'Finish the required field checks before routing this invoice for approval.',
    budget_decision_required: 'Record the budget decision before routing this invoice for approval.',
    exception_present: 'Resolve the blocking exception before routing this invoice for approval.',
    non_invoice_document: 'Only invoice records can be routed for approval.',
    merged_source: 'This record is part of a merged source and cannot be routed directly.',
    autonomy_gate_blocked: 'Autonomy policy blocked approval routing for this invoice.',
    policy_precheck_failed: 'This invoice is not ready for approval routing yet.',
    network_error: 'Clearledgr could not reach the backend to route this invoice.',
  };
  return mapping[token] || safeDetail || token.replace(/_/g, ' ');
}

function getSavedViewLabel(view) {
  return String(view?.name || '').trim() || 'Saved view';
}

function getActiveSavedView(viewPrefs = {}) {
  return getAllPipelineViews(viewPrefs).find((view) => pipelineSnapshotsEqual(view.snapshot, viewPrefs)) || null;
}

function buildResetFilters() {
  return {
    vendor: '',
    due: 'all',
    blocker: 'all',
    amount: 'all',
    approvalAge: 'all',
    erpStatus: 'all',
  };
}

export default function PipelinePage({ api, bootstrap, toast, orgId, userEmail, navigate }) {
  const pipelineScope = useMemo(() => getPipelineScope(orgId, userEmail), [orgId, userEmail]);
  const actorRole = bootstrap?.current_user?.role || 'operator';
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState([]);
  const [activeItemId, setActiveItemId] = useState('');
  const [viewPrefs, setViewPrefs] = useState(() => normalizePipelinePreferences({
    ...readPipelinePreferences(pipelineScope),
    viewMode: 'kanban',
  }));
  const [navState, setNavState] = useState(() => readPipelineNavigation(pipelineScope));
  const [savedViewName, setSavedViewName] = useState('');
  const [pipelineStages, setPipelineStages] = useState([]);
  const bootstrapPipelinePrefs = getBootstrappedPipelinePreferences(bootstrap);

  // §5.1: Fetch pipeline stage config from the object model API
  useEffect(() => {
    api(`/api/pipelines/ap-invoices?organization_id=${encodeURIComponent(orgId)}`, { silent: true })
      .then((data) => {
        if (Array.isArray(data?.stages) && data.stages.length > 0) {
          setPipelineStages(data.stages);
        }
      })
      .catch(() => {});
  }, [api, orgId]);
  const syncReadyRef = useRef(false);
  const syncTimerRef = useRef(null);
  const lastSyncedPrefsRef = useRef('');

  useEffect(() => {
    setViewPrefs(normalizePipelinePreferences({
      ...readPipelinePreferences(pipelineScope),
      viewMode: 'kanban',
    }));
    setNavState(readPipelineNavigation(pipelineScope));
  }, [pipelineScope]);

  const syncServerPreferences = async (prefs, { silent = true } = {}) => {
    const normalized = normalizePipelinePreferences({ ...(prefs || {}), viewMode: 'table' });
    await api('/api/workspace/user/preferences', {
      method: 'PATCH',
      body: JSON.stringify({
        organization_id: orgId,
        patch: buildPipelinePreferencePatch(normalized),
      }),
      silent,
    });
    lastSyncedPrefsRef.current = JSON.stringify(normalized);
  };

  useEffect(() => {
    const local = readPipelinePreferences(pipelineScope);
    const remote = bootstrapPipelinePrefs ? normalizePipelinePreferences(bootstrapPipelinePrefs) : null;
    let next = normalizePipelinePreferences({ ...local, viewMode: 'table' });
    let syncedBaseline = '';

    if (remote && hasMeaningfulPipelinePreferences(remote)) {
      if (!pipelinePreferencesEqual(local, remote)) {
        next = writePipelinePreferences(pipelineScope, { ...remote, viewMode: 'table' });
      } else {
        next = normalizePipelinePreferences({ ...remote, viewMode: 'table' });
      }
      syncedBaseline = JSON.stringify(normalizePipelinePreferences(next));
    } else if (!hasMeaningfulPipelinePreferences(local)) {
      syncedBaseline = JSON.stringify(normalizePipelinePreferences(next));
    }

    setViewPrefs(next);
    lastSyncedPrefsRef.current = syncedBaseline;
    syncReadyRef.current = true;
  }, [bootstrapPipelinePrefs, pipelineScope]);

  useEffect(() => {
    if (!syncReadyRef.current) return undefined;
    const serialized = JSON.stringify(normalizePipelinePreferences(viewPrefs));
    if (serialized === lastSyncedPrefsRef.current) return undefined;
    if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    syncTimerRef.current = setTimeout(() => {
      void syncServerPreferences(viewPrefs).catch(() => {});
    }, 500);
    return () => {
      if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    };
  }, [viewPrefs, pipelineScope]);

  useEffect(() => {
    setLoading(true);
    api(`/extension/worklist?organization_id=${encodeURIComponent(orgId)}&limit=500`)
      .then((data) => setItems(Array.isArray(data?.items) ? data.items : []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [api, orgId]);

  const persistPrefs = (nextValue) => {
    const normalized = writePipelinePreferences(pipelineScope, { ...(nextValue || {}), viewMode: 'table' });
    setViewPrefs(normalized);
    return normalized;
  };

  useEffect(() => {
    if (viewPrefs.viewMode === 'table') return;
    const normalized = writePipelinePreferences(pipelineScope, { ...viewPrefs, viewMode: 'table' });
    setViewPrefs(normalized);
  }, [pipelineScope, viewPrefs.viewMode]);

  const resetFiltersAndSearch = () => {
    setSearchQuery('');
    setViewPrefs(persistPrefs({
      ...viewPrefs,
      activeSliceId: 'all_open',
      sortCol: 'queue_age',
      sortDir: 'desc',
      filters: buildResetFilters(),
    }));
  };

  const [doRefresh, refreshing] = useAction(async () => {
    setLoading(true);
    try {
      const data = await api(`/extension/worklist?organization_id=${encodeURIComponent(orgId)}&limit=500`);
      setItems(Array.isArray(data?.items) ? data.items : []);
      setNavState(readPipelineNavigation(pipelineScope));
      toast('Invoices refreshed.', 'success');
    } catch {
      toast('Could not refresh invoices.', 'error');
    } finally {
      setLoading(false);
    }
  });

  const [saveView, savingView] = useAction(async () => {
    const name = String(savedViewName || '').trim();
    if (!name) {
      toast('Name the personal view first.', 'warning');
      return;
    }
    const next = createSavedPipelineView(pipelineScope, {
      name,
      snapshot: {
        ...viewPrefs,
        filters: viewPrefs.filters,
      },
    });
    setViewPrefs(next);
    setSavedViewName('');
    toast(`Personal view "${name}" added.`, 'success');
  });

  const [updateView, updatingView] = useAction(async () => {
    if (!activeSavedView || activeSavedView.scope !== 'user') {
      toast('Only personal views can be updated.', 'warning');
      return;
    }
    const nextName = String(savedViewName || activeSavedView.name || '').trim();
    if (!nextName) {
      toast('Name the personal view first.', 'warning');
      return;
    }
    const next = updateSavedPipelineView(pipelineScope, activeSavedView.id, {
      name: nextName,
      snapshot: {
        ...viewPrefs,
        filters: viewPrefs.filters,
      },
    });
    setViewPrefs(next);
    setSavedViewName(nextName);
    toast(`Personal view "${nextName}" updated.`, 'success');
  });

  const displayed = useMemo(() => filterPipelineItems(items, {
    activeSliceId: viewPrefs.activeSliceId,
    filters: viewPrefs.filters,
    searchQuery,
    sortCol: viewPrefs.sortCol,
    sortDir: viewPrefs.sortDir,
  }), [items, searchQuery, viewPrefs]);
  const selectedSet = useMemo(() => new Set(selectedIds.map((itemId) => String(itemId || ''))), [selectedIds]);
  const selectedItems = useMemo(
    () => displayed.filter((item) => selectedSet.has(String(item.id || ''))),
    [displayed, selectedSet],
  );
  const activeItem = useMemo(
    () => displayed.find((item) => String(item.id || '') === String(activeItemId || '')) || null,
    [activeItemId, displayed],
  );
  const routeableSelectedItems = useMemo(
    () => selectedItems.filter((item) => isRouteableInvoiceItem(item)),
    [selectedItems],
  );

  const sliceCounts = useMemo(() => buildPipelineSliceCounts(items), [items]);
  const starterViews = useMemo(() => getStarterPipelineViews(viewPrefs), [viewPrefs]);
  const personalViews = useMemo(() => getPersonalPipelineViews(viewPrefs), [viewPrefs]);
  const pinnedViews = useMemo(() => getPinnedPipelineViews(viewPrefs), [viewPrefs]);
  const activeSavedView = useMemo(() => getActiveSavedView(viewPrefs), [viewPrefs]);
  const focusedItem = useMemo(() => {
    const focusItemId = String(navState?.focusItemId || '').trim();
    if (!focusItemId) return null;
    return items.find((item) => String(item.id || '') === focusItemId) || null;
  }, [items, navState]);
  const focusedItemVisible = Boolean(
    focusedItem && displayed.some((item) => String(item.id || '') === String(focusedItem.id || ''))
  );

  const stats = useMemo(() => ({
    total: items.length,
    open: items.filter((item) => !['posted_to_erp', 'closed', 'rejected'].includes(normalizePipelineState(item.state))).length,
    waitingApproval: sliceCounts.waiting_on_approval || 0,
    readyToPost: sliceCounts.ready_to_post || 0,
    overdue: sliceCounts.overdue || 0,
  }), [items, sliceCounts]);

  useEffect(() => {
    if (activeSavedView?.scope === 'user' && !String(savedViewName || '').trim()) {
      setSavedViewName(getSavedViewLabel(activeSavedView));
    }
  }, [activeSavedView, savedViewName]);

  useEffect(() => {
    const validIds = new Set(items.map((item) => String(item.id || '')));
    setSelectedIds((prev) => prev.filter((itemId) => validIds.has(String(itemId || ''))));
  }, [items]);

  useEffect(() => {
    if (!displayed.length) {
      if (activeItemId) setActiveItemId('');
      return;
    }
    if (!displayed.some((item) => String(item.id || '') === String(activeItemId || ''))) {
      setActiveItemId(String(displayed[0]?.id || ''));
    }
  }, [displayed, activeItemId]);

  const applySlice = (sliceId) => {
    clearPipelineNavigation(pipelineScope);
    setNavState(readPipelineNavigation(pipelineScope));
    setViewPrefs(activatePipelineSlice(pipelineScope, sliceId));
  };

  const applySavedView = (view) => {
    clearPipelineNavigation(pipelineScope);
    setNavState(readPipelineNavigation(pipelineScope));
    const next = persistPrefs(view.snapshot);
    setViewPrefs(next);
    setSavedViewName(view.scope === 'user' ? getSavedViewLabel(view) : '');
    toast(`Loaded "${getSavedViewLabel(view)}".`, 'success');
  };

  const updateFilters = (patch) => persistPrefs({
    ...viewPrefs,
    filters: {
      ...viewPrefs.filters,
      ...(patch || {}),
    },
  });

  const updateSort = (nextSortCol) => {
    const nextSortDir = viewPrefs.sortCol === nextSortCol
      ? (viewPrefs.sortDir === 'desc' ? 'asc' : 'desc')
      : (nextSortCol === 'due_date' ? 'asc' : 'desc');
    persistPrefs({
      ...viewPrefs,
      sortCol: nextSortCol,
      sortDir: nextSortDir,
    });
  };

  const toggleSavedViewPin = (view) => {
    const next = view?.pinned
      ? unpinPipelineView(pipelineScope, getPipelineViewRef(view))
      : pinPipelineView(pipelineScope, getPipelineViewRef(view));
    setViewPrefs(next);
    toast(view?.pinned ? 'Saved view unpinned.' : 'Saved view pinned.', 'success');
  };

  const removeView = (viewId) => {
    const next = removeSavedPipelineView(pipelineScope, viewId);
    setViewPrefs(next);
    toast('Saved view removed.', 'success');
  };

  const revealFocusedItem = () => {
    if (!focusedItem) return;
    setSearchQuery('');
    const next = persistPrefs({
      ...viewPrefs,
      activeSliceId: getSuggestedPipelineSlice(focusedItem),
      sortCol: 'queue_age',
      sortDir: 'desc',
      filters: buildResetFilters(),
    });
    setViewPrefs(next);
  };

  const clearFocus = () => {
    clearPipelineNavigation(pipelineScope);
    setNavState(readPipelineNavigation(pipelineScope));
  };

  const toggleSelected = (itemId) => {
    const normalizedId = String(itemId || '').trim();
    if (!normalizedId) return;
    setSelectedIds((prev) => (
      prev.includes(normalizedId)
        ? prev.filter((value) => value !== normalizedId)
        : [...prev, normalizedId]
    ));
  };

  const selectVisible = () => {
    setSelectedIds(displayed.map((item) => String(item.id || '')));
  };

  const clearSelection = () => {
    setSelectedIds([]);
  };

  const [routeSelected, routingSelected] = useAction(async (explicitItems = null) => {
    const targetItems = Array.isArray(explicitItems)
      ? explicitItems
      : (selectedItems.length ? selectedItems : (activeItem ? [activeItem] : []));
    const routeableItems = targetItems.filter((item) => isRouteableInvoiceItem(item)).slice(0, 25);
    if (!routeableItems.length) {
      toast('No selected invoices are ready for approval routing.', 'warning');
      return;
    }

    let successCount = 0;
    let failedCount = 0;
    const failures = [];
    for (const item of routeableItems) {
      try {
        const result = await api('/extension/route-low-risk-approval', {
          method: 'POST',
          body: JSON.stringify({
            ap_item_id: item.id,
            email_id: item.thread_id || item.message_id || item.id,
            organization_id: orgId,
            reason: selectedItems.length > 1 ? 'bulk_pipeline_route' : 'pipeline_route',
          }),
        });
        const status = String(result?.status || '').toLowerCase();
        if (['pending_approval', 'needs_approval'].includes(status)) successCount += 1;
        else {
          failedCount += 1;
          failures.push(humanizeRouteFailure(result?.reason, result?.detail));
        }
      } catch {
        failedCount += 1;
        failures.push(humanizeRouteFailure('network_error'));
      }
    }

    setLoading(true);
    try {
      const data = await api(`/extension/worklist?organization_id=${encodeURIComponent(orgId)}&limit=500`, { silent: true });
      setItems(Array.isArray(data?.items) ? data.items : []);
    } finally {
      setLoading(false);
    }
    setSelectedIds((prev) => prev.filter((itemId) => !routeableItems.some((item) => String(item.id || '') === String(itemId || ''))));
    const firstFailure = failures.find(Boolean) || '';
    if (failedCount > 0) {
      toast(
        successCount > 0
          ? `${successCount} invoice(s) routed. ${failedCount} still need review. First issue: ${firstFailure || 'This invoice is not ready for approval routing yet.'}`
          : (firstFailure || 'No selected invoices are ready for approval routing.'),
        'warning',
      );
      return;
    }
    toast(`${successCount} invoice(s) routed for approval.`, 'success');
  });

  const [escalateApprovalItem, escalatingApproval] = useAction(async (targetItem) => {
    if (!targetItem?.id) return;
    try {
      const result = await api('/api/agent/intents/execute', {
        method: 'POST',
        body: JSON.stringify({
          intent: 'escalate_approval',
          input: {
            ap_item_id: targetItem.id,
            email_id: targetItem.thread_id || targetItem.message_id || targetItem.id,
            source_channel: 'pipeline',
            source_channel_id: 'pipeline',
            source_message_ref: targetItem.thread_id || targetItem.message_id || targetItem.id,
          },
          organization_id: orgId,
        }),
      });
      const ok = String(result?.status || '').toLowerCase() === 'escalated';
      toast(ok ? 'Approval escalated.' : (result?.reason || 'Could not escalate approval.'), ok ? 'success' : 'error');
      if (ok) {
        const data = await api(`/extension/worklist?organization_id=${encodeURIComponent(orgId)}&limit=500`, { silent: true });
        setItems(Array.isArray(data?.items) ? data.items : []);
      }
    } catch {
      toast('Could not escalate approval.', 'error');
    }
  });

  const currentSliceLabel = PIPELINE_BUILTIN_SLICES.find((slice) => slice.id === viewPrefs.activeSliceId)?.label || 'All open';
  const currentViewLabel = activeSavedView ? getSavedViewLabel(activeSavedView) : currentSliceLabel;

  if (loading) {
    return html`<div class="panel" style="padding:48px;text-align:center"><p class="muted">Loading queue…</p></div>`;
  }

  return html`
    <div class="pipeline-shell">
      <div class="panel pipeline-hero-panel" style="padding:12px 14px">
        <div class="pipeline-hero-head">
          <div class="pipeline-hero-copy">
            <div>
              <h3 style="margin:0 0 4px">Live AP queue</h3>
              <p class="muted" style="margin:0">Filter, route, and reopen records without leaving Gmail.</p>
            </div>
            <div class="pipeline-metric-row">
              <${QueueMetricPill} label="Open" value=${stats.open} />
              <${QueueMetricPill} label="Waiting approval" value=${stats.waitingApproval} tone="warning" />
              <${QueueMetricPill} label="Ready to post" value=${stats.readyToPost} tone="success" />
              <${QueueMetricPill} label="Overdue" value=${stats.overdue} tone="danger" />
              <${QueueMetricPill} label="Total" value=${stats.total} />
            </div>
          </div>
          <div class="toolbar-actions">
            <button class="btn-secondary btn-sm" onClick=${() => navigate('clearledgr/home')}>Home</button>
            <button class="btn-secondary btn-sm" onClick=${doRefresh} disabled=${refreshing}>${refreshing ? 'Refreshing…' : 'Refresh'}</button>
          </div>
        </div>

        ${focusedItem
          ? html`
              <div class="pipeline-focus-row">
                <div>
                  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
                    <strong style="font-size:13px">Current thread record</strong>
                    <${StatePill} state=${focusedItem.state} />
                  </div>
                  <div class="muted" style="font-size:13px">
                    ${focusedItem.vendor_name || focusedItem.vendor || 'Unknown vendor'} · ${getDocumentSummary(focusedItem)} · ${getAmountLabel(focusedItem)}
                  </div>
                  <div class="muted" style="font-size:12px;margin-top:4px">
                    ${focusedItemVisible
                      ? 'This record is already visible in the active invoices view.'
                      : 'This record is outside the current slice or filters. Jump back to its matching queue to keep thread context intact.'}
                  </div>
                </div>
                <div class="pipeline-focus-actions">
                  ${!focusedItemVisible
                    ? html`<button class="btn-primary btn-sm" onClick=${revealFocusedItem}>Show in invoices</button>`
                    : null}
                  <button class="btn-secondary btn-sm" onClick=${() => openItemDetail(navigate, pipelineScope, focusedItem)}>Open record</button>
                  <button class="btn-ghost btn-sm" onClick=${clearFocus}>Clear focus</button>
                </div>
              </div>
            `
          : null}
      </div>

      <div class="panel pipeline-view-panel" style="padding:12px 14px">
        <div class="pipeline-view-head" style="margin-bottom:10px">
          <div>
            <strong style="font-size:13px">Views</strong>
            <div class="muted" style="font-size:12px">
              ${activeSavedView ? `Current saved view: ${currentViewLabel}.` : `Current slice: ${currentSliceLabel}.`}
            </div>
          </div>
          <div class="muted" style="font-size:12px">${pinnedViews.length} pinned · ${personalViews.length} personal · ${displayed.length} visible</div>
        </div>

        <div class="pipeline-view-band">
          <span class="pipeline-view-label">Slices</span>
          <div class="pipeline-chip-strip" style="overflow-x:auto;padding-bottom:2px">
            ${PIPELINE_BUILTIN_SLICES.map((slice) => html`
              <${SliceChip}
                key=${slice.id}
                slice=${slice}
                count=${sliceCounts[slice.id] || 0}
                active=${viewPrefs.activeSliceId === slice.id}
                onClick=${() => applySlice(slice.id)}
              />
            `)}
          </div>
        </div>

        <div class="pipeline-view-band" style="margin-top:10px">
          <span class="pipeline-view-label">Saved</span>
          <div class="pipeline-chip-strip">
            ${starterViews.map((view) => html`
              <${SavedViewChip}
                key=${view.id}
                view=${view}
                active=${activeSavedView?.scope === view.scope && activeSavedView?.id === view.id}
                onOpen=${() => applySavedView(view)}
                onTogglePin=${() => toggleSavedViewPin(view)}
              />
            `)}
            ${personalViews.map((view) => html`
              <${SavedViewChip}
                key=${view.id}
                view=${view}
                active=${activeSavedView?.scope === view.scope && activeSavedView?.id === view.id}
                onOpen=${() => applySavedView(view)}
                onTogglePin=${() => toggleSavedViewPin(view)}
                onDelete=${() => removeView(view.id)}
              />
            `)}
          </div>
        </div>

        <div class="pipeline-saved-input-row">
          <input
            value=${savedViewName}
            onInput=${(event) => setSavedViewName(event.target.value)}
            placeholder="Save current view…"
            style="min-width:220px;padding:9px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;font-family:inherit"
          />
          <button class="btn-secondary btn-sm" onClick=${saveView} disabled=${savingView}>${savingView ? 'Saving…' : 'Save current view'}</button>
          ${activeSavedView?.scope === 'user'
            ? html`<button class="btn-secondary btn-sm" onClick=${updateView} disabled=${updatingView}>${updatingView ? 'Updating…' : 'Update active view'}</button>`
            : null}
          <button class="btn-ghost btn-sm" onClick=${resetFiltersAndSearch}>Reset filters</button>
          <span class="muted" style="font-size:12px">Sorted ${viewPrefs.sortDir === 'desc' ? 'descending' : 'ascending'} by ${viewPrefs.sortCol.replace(/_/g, ' ')}.</span>
        </div>
      </div>

      <div class="panel pipeline-filter-panel" style="padding:12px 14px">
        <div class="pipeline-filter-grid" style="display:grid;grid-template-columns:minmax(0,1.8fr) minmax(0,1.2fr) repeat(4,minmax(0,1fr));gap:10px;align-items:end">
          <label style="display:flex;flex-direction:column;gap:6px">
            <span class="muted" style="font-size:12px">Search</span>
            <input
              placeholder="Search vendors, references, PO, sender…"
              value=${searchQuery}
              onInput=${(event) => setSearchQuery(event.target.value)}
              style="padding:9px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;font-family:inherit"
            />
          </label>
          <label style="display:flex;flex-direction:column;gap:6px">
            <span class="muted" style="font-size:12px">Vendor</span>
            <input
              placeholder="Filter vendor…"
              value=${viewPrefs.filters.vendor}
              onInput=${(event) => updateFilters({ vendor: event.target.value })}
              style="padding:9px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;font-family:inherit"
            />
          </label>
          <label style="display:flex;flex-direction:column;gap:6px">
            <span class="muted" style="font-size:12px">Due date</span>
            <select value=${viewPrefs.filters.due} onChange=${(event) => updateFilters({ due: event.target.value })}>
              <option value="all">All</option>
              <option value="overdue">Overdue</option>
              <option value="due_7d">Due in 7 days</option>
              <option value="no_due">No due date</option>
            </select>
          </label>
          <label style="display:flex;flex-direction:column;gap:6px">
            <span class="muted" style="font-size:12px">Blocker type</span>
            <select value=${viewPrefs.filters.blocker} onChange=${(event) => updateFilters({ blocker: event.target.value })}>
              <option value="all">All</option>
              <option value="entity">Entity</option>
              <option value="approval">Approval</option>
              <option value="info">Needs info</option>
              <option value="erp">ERP</option>
              <option value="exception">Policy</option>
              <option value="confidence">Field review</option>
              <option value="budget">Budget</option>
              <option value="po">PO / GR</option>
              <option value="processing">Processing</option>
            </select>
          </label>
          <label style="display:flex;flex-direction:column;gap:6px">
            <span class="muted" style="font-size:12px">ERP status</span>
            <select value=${viewPrefs.filters.erpStatus} onChange=${(event) => updateFilters({ erpStatus: event.target.value })}>
              <option value="all">All</option>
              <option value="ready">Ready</option>
              <option value="failed">Failed</option>
              <option value="connected">Connected</option>
              <option value="posted">Posted</option>
              <option value="not_connected">Not connected</option>
            </select>
          </label>
          <label style="display:flex;flex-direction:column;gap:6px">
            <span class="muted" style="font-size:12px">Sort</span>
            <select value=${viewPrefs.sortCol} onChange=${(event) => updateSort(event.target.value)}>
              <option value="queue_age">Queue age</option>
              <option value="due_date">Due date</option>
              <option value="amount">Amount</option>
              <option value="updated_at">Last update</option>
              <option value="approval_wait">Approval waiting time</option>
            </select>
          </label>
        </div>

        <div class="pipeline-filter-footer">
          <div class="pipeline-filter-aux" style="align-items:flex-end">
            <label style="display:flex;flex-direction:column;gap:6px;min-width:160px">
              <span class="muted" style="font-size:12px">Amount band</span>
              <select value=${viewPrefs.filters.amount} onChange=${(event) => updateFilters({ amount: event.target.value })}>
                <option value="all">All</option>
                <option value="under_1k">Under 1k</option>
                <option value="1k_10k">1k - 10k</option>
                <option value="over_10k">Over 10k</option>
              </select>
            </label>
            <label style="display:flex;flex-direction:column;gap:6px;min-width:160px">
              <span class="muted" style="font-size:12px">Approval age</span>
              <select value=${viewPrefs.filters.approvalAge} onChange=${(event) => updateFilters({ approvalAge: event.target.value })}>
                <option value="all">All</option>
                <option value="under_24h">Under 24h</option>
                <option value="1d_3d">1-3 days</option>
                <option value="over_3d">Over 3 days</option>
              </select>
            </label>
          </div>
          <div class="pipeline-filter-actions" style="justify-content:flex-end">
            <span class="muted" style="font-size:12px">
              ${selectedItems.length ? `${selectedItems.length} selected` : 'No selection'}
              ${routeableSelectedItems.length ? ` · ${routeableSelectedItems.length} routeable` : ''}
            </span>
            <button class="btn-secondary btn-sm" onClick=${selectVisible}>Select visible</button>
            <button class="btn-ghost btn-sm" onClick=${clearSelection} disabled=${selectedIds.length === 0}>Clear selection</button>
            <button
              class="btn-primary btn-sm"
              onClick=${() => routeSelected()}
              disabled=${routingSelected || (!routeableSelectedItems.length && !isRouteableInvoiceItem(activeItem))}
            >
              ${routingSelected
                ? 'Routing…'
                : (routeableSelectedItems.length > 0 ? 'Route selected' : 'Route current')}
            </button>
          </div>
        </div>
      </div>

      <!-- §6.7 Kanban board — stage columns with cards -->
      <div class="pipeline-kanban" style="display:flex;gap:12px;overflow-x:auto;padding:0 0 16px;min-height:400px">
        ${(() => {
          // §5.1: Kanban stages come from the Pipeline object model API.
          // pipelineStages is fetched on mount from /api/pipelines/ap-invoices.
          // Fallback to hardcoded thesis stages if API not available yet.
          const FALLBACK_STAGES = [
            { slug: 'received',  label: 'Received',  source_states: ['received', 'validated'], color: '#94A3B8' },
            { slug: 'matching',  label: 'Matching',   source_states: ['needs_approval', 'pending_approval'], color: '#CA8A04' },
            { slug: 'exception', label: 'Exception',  source_states: ['needs_info', 'failed_post', 'reversed'], color: '#DC2626' },
            { slug: 'approved',  label: 'Approved',   source_states: ['approved', 'ready_to_post'], color: '#2563EB' },
            { slug: 'paid',      label: 'Paid',       source_states: ['posted_to_erp', 'closed'], color: '#16A34A' },
          ];
          const KANBAN_STAGES = (pipelineStages && pipelineStages.length > 0)
            ? pipelineStages.map((s) => ({
                key: s.slug,
                label: s.label,
                states: Array.isArray(s.source_states) ? s.source_states : [],
                color: s.color || '#94A3B8',
              }))
            : FALLBACK_STAGES.map((s) => ({ key: s.slug, label: s.label, states: s.source_states, color: s.color }));
          return KANBAN_STAGES.map((stage) => {
            const stageItems = displayed.filter((item) =>
              stage.states.includes(String(item.state || '').toLowerCase())
            );
            return html`
              <div key=${stage.key} class="kanban-column" style="
                min-width:240px;max-width:280px;flex:1;
                background:#F7F9FB;border-radius:10px;padding:0;
                display:flex;flex-direction:column;
              ">
                <div style="
                  padding:10px 14px;border-bottom:2px solid ${stage.color || '#E2E8F0'};
                  display:flex;align-items:center;justify-content:space-between;
                ">
                  <strong style="font-size:13px;color:#0A1628">${stage.label}</strong>
                  <span style="
                    font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;
                    background:${stage.color || '#94A3B8'}20;color:${stage.color || '#94A3B8'};
                  ">${stageItems.length}</span>
                </div>
                <div style="padding:8px;flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:8px">
                  ${stageItems.length === 0
                    ? html`<div class="muted" style="font-size:12px;text-align:center;padding:24px 8px">No invoices</div>`
                    : stageItems.map((item) => {
                        const pipelineBlockers = getPipelineBlockers(item);
                        const active = String(activeItemId || '') === String(item.id || '');
                        return html`
                          <div
                            key=${item.id}
                            class="kanban-card"
                            style="
                              background:#fff;border:1px solid ${active ? '#00D67E' : '#E2E8F0'};
                              border-radius:8px;padding:10px 12px;cursor:pointer;
                              ${active ? 'box-shadow:0 0 0 2px rgba(0,214,126,0.2);' : ''}
                            "
                            onClick=${() => { setActiveItemId(String(item.id || '')); openItemDetail(navigate, pipelineScope, item); }}
                          >
                            <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
                              <strong style="font-size:13px;color:#0A1628;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:160px">
                                ${item.vendor_name || item.vendor || 'Unknown'}
                              </strong>
                              <span style="font-size:12px;font-family:var(--font-mono);color:#0A1628;font-weight:500">
                                ${getAmountLabel(item)}
                              </span>
                            </div>
                            <div class="muted" style="font-size:11px;margin-bottom:4px">
                              ${item.invoice_number || item.reference || ''}
                              ${item.due_date ? ` · Due ${fmtDate(item.due_date)}` : ''}
                            </div>
                            ${pipelineBlockers.length > 0
                              ? html`<div style="font-size:11px;color:#92400E;margin-top:2px">
                                  ${pipelineBlockers[0]?.label || pipelineBlockers[0]?.kind || 'Blocker'}
                                </div>`
                              : ''}
                            <div class="muted" style="font-size:10px;margin-top:4px">${formatDurationMinutes(getQueueAgeMinutes(item))} in queue</div>
                          </div>
                        `;
                      })}
                </div>
              </div>
            `;
          });
        })()}
      </div>

      <div class="muted" style="text-align:center;padding:2px 0 0;font-size:12px">
        ${displayed.length} of ${items.length} records in ${currentSliceLabel}.
      </div>
    </div>
  `;
}
