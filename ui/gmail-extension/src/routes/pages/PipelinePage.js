/**
 * AP Pipeline View — Gmail-native queue surface.
 * Streak-style doctrine: queue slices first, detail second, no dashboard sprawl.
 */
import { h } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDate, fmtDateTime, useAction } from '../route-helpers.js';
import ActionDialog, { useActionDialog } from '../../components/ActionDialog.js';
import { BatchOps, BATCH_OPS_CSS } from '../../components/BatchOps.js';
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

function BlockerChip({ blocker }) {
  const kind = String(blocker?.kind || '').trim().toLowerCase();
  const label = blocker?.chip_label || blocker?.title || BLOCKER_LABELS[kind] || kind;
  return html`<span style="
    font-size:11px;font-weight:600;padding:3px 8px;border-radius:999px;
    background:#FFF7ED;border:1px solid #FED7AA;color:#9A3412;
  ">${label}</span>`;
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
  // Filters and Saved Views are modal surfaces, not permanent chrome — thesis
  // §6.7 (Kanban is the interface) + §8 (invisibility). They open on demand
  // and close on dismiss.
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [viewsOpen, setViewsOpen] = useState(false);
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
    await api('/api/user/preferences', {
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
  const activeSavedView = useMemo(() => getActiveSavedView(viewPrefs), [viewPrefs]);
  const focusedItem = useMemo(() => {
    const focusItemId = String(navState?.focusItemId || '').trim();
    if (!focusItemId) return null;
    return items.find((item) => String(item.id || '') === focusItemId) || null;
  }, [items, navState]);
  const focusedItemVisible = Boolean(
    focusedItem && displayed.some((item) => String(item.id || '') === String(focusedItem.id || ''))
  );

  // Count non-default filter values — powers the badge on the Filters button
  // so the user can see at a glance that the queue is being narrowed without
  // opening the sheet. 'all' or empty strings are the defaults.
  const activeFilterCount = useMemo(() => {
    const f = viewPrefs?.filters || {};
    let n = 0;
    if (f.vendor && String(f.vendor).trim()) n += 1;
    if (f.due && f.due !== 'all') n += 1;
    if (f.blocker && f.blocker !== 'all') n += 1;
    if (f.erpStatus && f.erpStatus !== 'all') n += 1;
    if (f.amount && f.amount !== 'all') n += 1;
    if (f.approvalAge && f.approvalAge !== 'all') n += 1;
    if (viewPrefs.activeSliceId && viewPrefs.activeSliceId !== 'all_open') n += 1;
    return n;
  }, [viewPrefs]);

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

  const [dialog, openDialog] = useActionDialog();

  // BatchOps handlers — each calls the matching bulk endpoint.
  const _bulkOrgQuery = `organization_id=${encodeURIComponent(orgId)}`;
  const batchApprove = async (ids) => {
    const result = await api(`/api/ap/items/bulk-approve?${_bulkOrgQuery}`, {
      method: 'POST',
      body: JSON.stringify({ ap_item_ids: ids }),
    });
    await doRefresh();
    return result;
  };
  const batchReject = async (ids, reason) => {
    const result = await api(`/api/ap/items/bulk-reject?${_bulkOrgQuery}`, {
      method: 'POST',
      body: JSON.stringify({ ap_item_ids: ids, reason }),
    });
    await doRefresh();
    return result;
  };
  const batchSnooze = async (ids, minutes) => {
    const result = await api(`/api/ap/items/bulk-snooze?${_bulkOrgQuery}`, {
      method: 'POST',
      body: JSON.stringify({ ap_item_ids: ids, duration_minutes: minutes }),
    });
    await doRefresh();
    return result;
  };
  const batchRetryPost = async (ids) => {
    const result = await api(`/api/ap/items/bulk-retry-post?${_bulkOrgQuery}`, {
      method: 'POST',
      body: JSON.stringify({ ap_item_ids: ids }),
    });
    await doRefresh();
    return result;
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
      <!-- §6.7 header — minimal chrome above the Kanban.
           Title, saved view toggle, search, filters button, refresh.
           Everything else lives in the board or in on-demand sheets. -->
      <div class="pipeline-topbar" style="
        display:flex;align-items:center;gap:10px;padding:10px 14px 6px;flex-wrap:wrap;
      ">
        <div style="display:flex;align-items:baseline;gap:10px;flex:0 0 auto">
          <h3 style="margin:0;font:700 18px/1.2 var(--font-display),'DM Sans',sans-serif;color:#0A1628">
            Live AP queue
          </h3>
          ${activeSavedView || viewPrefs.activeSliceId !== 'all_open'
            ? html`<span class="muted" style="font-size:12px">
                ${currentViewLabel}
              </span>`
            : null}
        </div>

        <div style="flex:1 1 260px;min-width:220px;max-width:560px">
          <input
            placeholder="Search vendor, invoice #, PO, sender…"
            value=${searchQuery}
            onInput=${(event) => setSearchQuery(event.target.value)}
            style="
              width:100%;padding:8px 12px;border:1px solid var(--border);
              border-radius:8px;font-size:13px;font-family:inherit;background:#fff;
            "
          />
        </div>

        <div style="display:flex;align-items:center;gap:6px;margin-left:auto">
          <button
            class="btn-secondary btn-sm"
            onClick=${() => setViewsOpen((v) => !v)}
            style="display:flex;align-items:center;gap:6px"
          >
            Views
            ${activeSavedView
              ? html`<span style="
                  font-size:10px;font-weight:700;padding:1px 6px;border-radius:999px;
                  background:#ECFDF5;color:#059669;
                ">●</span>`
              : null}
          </button>
          <button
            class="btn-secondary btn-sm"
            onClick=${() => setFiltersOpen(true)}
            style="display:flex;align-items:center;gap:6px"
          >
            Filters
            ${activeFilterCount > 0
              ? html`<span style="
                  font-size:10px;font-weight:700;padding:1px 6px;border-radius:999px;
                  background:#0A1628;color:#fff;min-width:16px;text-align:center;
                ">${activeFilterCount}</span>`
              : null}
          </button>
          <button
            class="btn-secondary btn-sm"
            onClick=${doRefresh}
            disabled=${refreshing}
            aria-label="Refresh queue"
          >${refreshing ? 'Refreshing…' : 'Refresh'}</button>
        </div>
      </div>

      <!-- §7 focus row — keep: if the user opened the Kanban from a thread,
           show that record's location. This is Streak's "context always
           visible" principle, not dashboard sprawl. -->
      ${focusedItem
        ? html`
            <div class="pipeline-focus-row" style="
              margin:4px 14px 8px;padding:10px 12px;border-radius:10px;
              background:#FFFBEB;border:1px solid #FDE68A;
              display:flex;align-items:center;gap:12px;flex-wrap:wrap;
            ">
              <div style="flex:1;min-width:200px">
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:2px">
                  <strong style="font-size:13px;color:#92400E">Thread record</strong>
                  <${StatePill} state=${focusedItem.state} />
                </div>
                <div class="muted" style="font-size:12px">
                  ${focusedItem.vendor_name || focusedItem.vendor || 'Unknown vendor'} ·
                  ${getDocumentSummary(focusedItem)} · ${getAmountLabel(focusedItem)}
                </div>
              </div>
              <div style="display:flex;gap:6px;flex-wrap:wrap">
                ${!focusedItemVisible
                  ? html`<button class="btn-primary btn-sm" onClick=${revealFocusedItem}>Show</button>`
                  : null}
                <button class="btn-secondary btn-sm" onClick=${() => openItemDetail(navigate, pipelineScope, focusedItem)}>Open</button>
                <button class="btn-ghost btn-sm" onClick=${clearFocus}>×</button>
              </div>
            </div>
          `
        : null}

      <!-- Views popover: slices + starter/personal saved views + save current.
           Only visible when the user taps Views — no permanent chrome. -->
      ${viewsOpen
        ? html`
            <div style="
              position:fixed;inset:0;background:rgba(10,22,40,0.3);z-index:9500;
            " onClick=${() => setViewsOpen(false)}>
              <div
                onClick=${(e) => e.stopPropagation()}
                style="
                  position:absolute;top:56px;right:14px;width:340px;max-width:calc(100vw - 28px);
                  max-height:calc(100vh - 80px);overflow:auto;
                  background:#fff;border:1px solid var(--border);border-radius:12px;
                  box-shadow:0 12px 32px rgba(10,22,40,0.2);padding:14px;
                "
              >
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
                  <strong style="font-size:14px">Views</strong>
                  <button class="btn-ghost btn-xs" onClick=${() => setViewsOpen(false)}>Close</button>
                </div>

                <div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">Slice</div>
                <div style="display:flex;flex-direction:column;gap:4px;margin-bottom:12px">
                  ${PIPELINE_BUILTIN_SLICES.map((slice) => html`
                    <button
                      key=${slice.id}
                      onClick=${() => { applySlice(slice.id); setViewsOpen(false); }}
                      style="
                        display:flex;align-items:center;justify-content:space-between;
                        padding:8px 10px;border-radius:8px;border:0;cursor:pointer;
                        background:${viewPrefs.activeSliceId === slice.id ? '#ECFDF5' : 'transparent'};
                        color:${viewPrefs.activeSliceId === slice.id ? '#059669' : '#0A1628'};
                        text-align:left;font-family:inherit;font-size:13px;
                      "
                    >
                      <span>${slice.label}</span>
                      <span class="muted" style="font-size:11px;font-weight:700">${sliceCounts[slice.id] || 0}</span>
                    </button>
                  `)}
                </div>

                ${starterViews.length > 0 ? html`
                  <div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">Saved</div>
                  <div style="display:flex;flex-direction:column;gap:4px;margin-bottom:12px">
                    ${[...starterViews, ...personalViews].map((view) => html`
                      <div key=${view.id} style="
                        display:flex;align-items:center;gap:4px;
                        padding:6px 8px;border-radius:8px;
                        background:${activeSavedView?.id === view.id ? '#ECFDF5' : 'transparent'};
                      ">
                        <button
                          onClick=${() => { applySavedView(view); setViewsOpen(false); }}
                          style="flex:1;border:0;background:transparent;cursor:pointer;text-align:left;font-family:inherit;font-size:13px;padding:0"
                        >${getSavedViewLabel(view)}</button>
                        <button
                          class="btn-ghost btn-xs"
                          onClick=${() => toggleSavedViewPin(view)}
                          aria-label=${view.pinned ? 'Unpin' : 'Pin'}
                        >${view.pinned ? '★' : '☆'}</button>
                        ${view.scope === 'user'
                          ? html`<button class="btn-ghost btn-xs" onClick=${() => removeView(view.id)}>✕</button>`
                          : null}
                      </div>
                    `)}
                  </div>
                ` : null}

                <div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">Save current</div>
                <div style="display:flex;gap:6px">
                  <input
                    value=${savedViewName}
                    onInput=${(event) => setSavedViewName(event.target.value)}
                    placeholder="View name"
                    style="flex:1;padding:7px 10px;border:1px solid var(--border);border-radius:6px;font-size:13px;font-family:inherit"
                  />
                  <button class="btn-primary btn-sm" onClick=${saveView} disabled=${savingView}>${savingView ? '…' : 'Save'}</button>
                </div>
                ${activeSavedView?.scope === 'user'
                  ? html`<button class="btn-secondary btn-sm" onClick=${updateView} disabled=${updatingView} style="margin-top:6px;width:100%">
                      ${updatingView ? 'Updating…' : `Update "${activeSavedView.name}"`}
                    </button>`
                  : null}
              </div>
            </div>
          `
        : null}

      <!-- Filters sheet: slides in from the right when Filters is tapped.
           All the filter controls from the old permanent panel live here. -->
      ${filtersOpen
        ? html`
            <div style="
              position:fixed;inset:0;background:rgba(10,22,40,0.3);z-index:9500;
            " onClick=${() => setFiltersOpen(false)}>
              <div
                onClick=${(e) => e.stopPropagation()}
                style="
                  position:absolute;top:0;right:0;bottom:0;width:380px;max-width:100vw;
                  background:#fff;border-left:1px solid var(--border);
                  box-shadow:-12px 0 32px rgba(10,22,40,0.12);
                  padding:16px;overflow-y:auto;
                  display:flex;flex-direction:column;gap:12px;
                "
              >
                <div style="display:flex;align-items:center;justify-content:space-between">
                  <strong style="font-size:15px">Filters</strong>
                  <button class="btn-ghost btn-sm" onClick=${() => setFiltersOpen(false)}>Close</button>
                </div>

                <label style="display:flex;flex-direction:column;gap:6px">
                  <span class="muted" style="font-size:12px">Vendor</span>
                  <input
                    placeholder="Any vendor"
                    value=${viewPrefs.filters.vendor}
                    onInput=${(event) => updateFilters({ vendor: event.target.value })}
                    style="padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:13px;font-family:inherit"
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
                  <span class="muted" style="font-size:12px">Amount band</span>
                  <select value=${viewPrefs.filters.amount} onChange=${(event) => updateFilters({ amount: event.target.value })}>
                    <option value="all">All</option>
                    <option value="under_1k">Under 1k</option>
                    <option value="1k_10k">1k - 10k</option>
                    <option value="over_10k">Over 10k</option>
                  </select>
                </label>

                <label style="display:flex;flex-direction:column;gap:6px">
                  <span class="muted" style="font-size:12px">Approval age</span>
                  <select value=${viewPrefs.filters.approvalAge} onChange=${(event) => updateFilters({ approvalAge: event.target.value })}>
                    <option value="all">All</option>
                    <option value="under_24h">Under 24h</option>
                    <option value="1d_3d">1-3 days</option>
                    <option value="over_3d">Over 3 days</option>
                  </select>
                </label>

                <label style="display:flex;flex-direction:column;gap:6px">
                  <span class="muted" style="font-size:12px">Sort by</span>
                  <select value=${viewPrefs.sortCol} onChange=${(event) => updateSort(event.target.value)}>
                    <option value="queue_age">Queue age</option>
                    <option value="due_date">Due date</option>
                    <option value="amount">Amount</option>
                    <option value="updated_at">Last update</option>
                    <option value="approval_wait">Approval wait</option>
                  </select>
                  <span class="muted" style="font-size:11px">
                    ${viewPrefs.sortDir === 'desc' ? 'Descending' : 'Ascending'} — tap the sort option again to flip.
                  </span>
                </label>

                <div style="display:flex;gap:6px;margin-top:8px">
                  <button class="btn-ghost btn-sm" onClick=${() => { resetFiltersAndSearch(); setFiltersOpen(false); }} style="flex:1">Reset</button>
                  <button class="btn-primary btn-sm" onClick=${() => setFiltersOpen(false)} style="flex:1">Done</button>
                </div>
              </div>
            </div>
          `
        : null}

      <style>${BATCH_OPS_CSS}</style>
      <${BatchOps}
        selectedItems=${selectedItems}
        onClear=${clearSelection}
        onApprove=${batchApprove}
        onReject=${batchReject}
        onSnooze=${batchSnooze}
        onRetryPost=${batchRetryPost}
        toast=${toast}
        openDialog=${openDialog}
      />
      <${ActionDialog} ...${dialog} />

      <!-- Scope toggle — one-click narrowing without opening Views.
           All open / Exceptions / Overdue. Maps to existing slice IDs so
           saved-view persistence keeps working. Depth (per-blocker type,
           vendor-specific, etc.) still lives in the Views popover. -->
      <div style="
        display:flex;align-items:center;gap:6px;padding:0 14px 8px;flex-wrap:wrap;
      ">
        ${[
          { id: 'all_open', label: 'All open' },
          { id: 'blocked_exception', label: 'Exceptions' },
          { id: 'overdue', label: 'Overdue' },
        ].map((scope) => {
          const isActive = viewPrefs.activeSliceId === scope.id;
          const count = sliceCounts[scope.id] || 0;
          return html`
            <button
              key=${scope.id}
              onClick=${() => applySlice(scope.id)}
              style="
                display:inline-flex;align-items:center;gap:6px;
                padding:6px 10px;border-radius:999px;cursor:pointer;
                font-family:inherit;font-size:12px;font-weight:600;
                border:1px solid ${isActive ? '#0A1628' : '#E2E8F0'};
                background:${isActive ? '#0A1628' : '#fff'};
                color:${isActive ? '#fff' : '#0A1628'};
              "
            >
              <span>${scope.label}</span>
              <span style="
                font-size:10px;font-weight:700;padding:1px 6px;border-radius:999px;
                background:${isActive ? 'rgba(255,255,255,0.18)' : '#F1F5F9'};
                color:${isActive ? '#fff' : '#64748B'};
                min-width:16px;text-align:center;
              ">${count}</span>
            </button>
          `;
        })}
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
                        // §6.7 card content: vendor, amount, reference, age.
                        // Due date is surfaced ONLY when it matters — overdue
                        // (red) or due within 7 days (amber). Otherwise we
                        // keep the card clean (thesis §7 density without
                        // ambiguity, §8 invisibility).
                        const reference = String(item.invoice_number || item.reference || '').trim();
                        let dueBadge = null;
                        if (item.due_date) {
                          const dueMs = new Date(item.due_date).getTime();
                          if (Number.isFinite(dueMs)) {
                            const days = Math.round((dueMs - Date.now()) / 86400000);
                            if (days < 0) {
                              dueBadge = {
                                label: `${Math.abs(days)}d overdue`,
                                bg: '#FEF2F2', color: '#B91C1C', border: '#FECACA',
                              };
                            } else if (days <= 7) {
                              dueBadge = {
                                label: days === 0 ? 'Due today' : `Due in ${days}d`,
                                bg: '#FFFBEB', color: '#92400E', border: '#FDE68A',
                              };
                            }
                          }
                        }
                        // Exception cards surface the specific flag inline
                        // (§6.7). Show primary + '+N' if multiple.
                        const primaryBlocker = pipelineBlockers[0];
                        const extraBlockers = pipelineBlockers.length - 1;
                        return html`
                          <div
                            key=${item.id}
                            class="kanban-card"
                            style="
                              background:#fff;border:1px solid ${active ? '#00D67E' : '#E2E8F0'};
                              border-radius:8px;padding:10px 12px;cursor:pointer;
                              ${active ? 'box-shadow:0 0 0 2px rgba(0,214,126,0.2);' : ''}
                              display:flex;flex-direction:column;gap:6px;
                            "
                            onClick=${() => { setActiveItemId(String(item.id || '')); openItemDetail(navigate, pipelineScope, item); }}
                          >
                            <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">
                              <strong style="font-size:13px;color:#0A1628;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1">
                                ${item.vendor_name || item.vendor || 'Unknown'}
                              </strong>
                              <span style="font-size:12px;font-family:var(--font-mono);color:#0A1628;font-weight:600;white-space:nowrap">
                                ${getAmountLabel(item)}
                              </span>
                            </div>
                            ${reference
                              ? html`<div class="muted" style="font-size:11px;font-family:var(--font-mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                                  ${reference}
                                </div>`
                              : null}
                            ${primaryBlocker
                              ? html`<div style="display:flex;align-items:center;gap:4px;flex-wrap:wrap">
                                  <span style="
                                    font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;
                                    background:#FFF7ED;border:1px solid #FED7AA;color:#9A3412;
                                  ">${primaryBlocker.chip_label || primaryBlocker.title || primaryBlocker.label || BLOCKER_LABELS[String(primaryBlocker.kind || '').toLowerCase()] || primaryBlocker.kind || 'Blocker'}</span>
                                  ${extraBlockers > 0
                                    ? html`<span class="muted" style="font-size:10px;font-weight:600">+${extraBlockers}</span>`
                                    : null}
                                </div>`
                              : null}
                            <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;margin-top:2px">
                              <span class="muted" style="font-size:10px">
                                ${formatDurationMinutes(getQueueAgeMinutes(item))} in queue
                              </span>
                              ${dueBadge
                                ? html`<span style="
                                    font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;
                                    background:${dueBadge.bg};color:${dueBadge.color};
                                    border:1px solid ${dueBadge.border};
                                  ">${dueBadge.label}</span>`
                                : null}
                            </div>
                          </div>
                        `;
                      })}
                </div>
              </div>
            `;
          });
        })()}
      </div>

    </div>
  `;
}
