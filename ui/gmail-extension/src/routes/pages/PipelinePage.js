/**
 * AP Pipeline View — Gmail-native queue surface.
 * Streak-style doctrine: queue slices first, detail second, no dashboard sprawl.
 */
import { h } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDate, fmtDateTime, useAction } from '../route-helpers.js';
import { getFieldReviewBlockers, getWorkflowPauseReason, openSourceEmail } from '../../utils/formatters.js';
import { navigateToRecordDetail } from '../../utils/record-route.js';
import {
  getDocumentReferenceText,
  getDocumentTypeLabel,
  isInvoiceDocumentType,
  normalizeDocumentType,
} from '../../utils/document-types.js';
import {
  PIPELINE_BUILTIN_SLICES,
  PIPELINE_STARTER_VIEWS,
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
  approval: 'Approval waiting',
  info: 'Needs info',
  erp: 'ERP retry',
  exception: 'Policy block',
  confidence: 'Field review',
  budget: 'Budget review',
  po: 'PO / GR issue',
};

const ERP_STATUS_LABELS = {
  ready: 'Ready',
  failed: 'Failed',
  connected: 'Connected',
  posted: 'Posted',
  not_connected: 'Not connected',
};

function isTypingTarget(target) {
  if (!target || typeof target !== 'object') return false;
  const tag = String(target.tagName || '').toUpperCase();
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || Boolean(target.isContentEditable);
}

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
      display:flex;align-items:center;gap:8px;padding:10px 12px;border-radius:12px;
      border:1px solid ${active ? 'var(--accent)' : 'var(--border)'};
      background:${active ? 'var(--accent-soft)' : 'var(--surface)'};
      color:${active ? 'var(--accent-ink)' : 'var(--ink)'};
      cursor:pointer;font-family:inherit;text-align:left;min-width:182px;
    "
  >
    <span style="font-size:13px;font-weight:700">${slice.label}</span>
    <span style="margin-left:auto;font-size:12px;font-weight:700;color:inherit">${count}</span>
  </button>`;
}

function BlockerChip({ kind }) {
  return html`<span style="
    font-size:11px;font-weight:600;padding:3px 8px;border-radius:999px;
    background:#FFF7ED;border:1px solid #FED7AA;color:#9A3412;
  ">${BLOCKER_LABELS[kind] || kind}</span>`;
}

function FieldReviewSummary({ item, compact = false }) {
  const blockers = getFieldReviewBlockers(item);
  const pauseReason = getWorkflowPauseReason(item);
  const first = blockers[0];
  if (!first && !pauseReason) return null;

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
      ${first && html`<div style="font-size:12px;font-weight:700;color:#9A3412">
        ${first.field_label || 'Field'} blocked
        ${first.winning_source_label ? ` · ${first.winning_source_label} wins` : ''}
      </div>`}
      ${first && html`<div class="muted" style="font-size:12px;line-height:1.45">
        Email ${first.email_value_display || 'Not found'} · Attachment ${first.attachment_value_display || 'Not found'}
      </div>`}
      ${pauseReason && html`<div class="muted" style="font-size:12px;line-height:1.45">${pauseReason}</div>`}
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
      <button class="alt" onClick=${onOpen} style="padding:6px 10px;font-size:12px">${view.name}</button>
      <span class="muted" style="font-size:11px;font-weight:700">${scopeLabel}</span>
      <button
        aria-label=${view.pinned ? 'Unpin saved view' : 'Pin saved view'}
        onClick=${onTogglePin}
        style="border:none;background:transparent;color:${view.pinned ? 'var(--accent-ink)' : 'var(--ink-muted)'};cursor:pointer;padding:0 2px;font-size:12px;font-weight:700"
      >${view.pinned ? 'Pinned' : 'Pin'}</button>
      ${typeof onDelete === 'function'
        ? html`<button
            aria-label="Delete saved view"
            onClick=${onDelete}
            style="border:none;background:transparent;color:var(--ink-muted);cursor:pointer;padding:0 2px"
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
  const currency = String(item?.currency || 'USD');
  return `${currency} ${amount.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function getDocumentSummary(item) {
  const documentType = normalizeDocumentType(item?.document_type);
  const reference = String(item?.invoice_number || '').trim();
  return reference ? getDocumentReferenceText(documentType, reference) : getDocumentTypeLabel(documentType);
}

function getPipelineTimeline(item, erpStatus) {
  const documentType = normalizeDocumentType(item?.document_type);
  const parts = [];
  if (isInvoiceDocumentType(documentType)) {
    parts.push(`Due ${item.due_date ? fmtDate(item.due_date) : '—'}`);
    parts.push(`ERP ${ERP_STATUS_LABELS[erpStatus] || erpStatus}`);
  } else {
    parts.push(`Type ${getDocumentTypeLabel(documentType)}`);
  }
  parts.push(`Updated ${fmtDateTime(item.updated_at || item.created_at)}`);
  return parts.join(' · ');
}

function isRouteableInvoiceItem(item) {
  if (!isInvoiceDocumentType(item?.document_type)) return false;
  const state = normalizePipelineState(item?.state);
  if (!['received', 'validated'].includes(state)) return false;
  if (Boolean(item?.requires_field_review)) return false;
  const blockers = getPipelineBlockerKinds(item);
  return !blockers.some((kind) => ['confidence', 'exception', 'budget', 'po', 'erp'].includes(kind));
}

function getSavedViewLabel(view) {
  return String(view?.name || '').trim() || 'Saved view';
}

function getActiveSavedView(viewPrefs = {}) {
  return getAllPipelineViews(viewPrefs).find((view) => pipelineSnapshotsEqual(view.snapshot, viewPrefs)) || null;
}

function buildResetFilters() {
  return {
    state: 'all',
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
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState([]);
  const [activeItemId, setActiveItemId] = useState('');
  const [viewPrefs, setViewPrefs] = useState(() => readPipelinePreferences(pipelineScope));
  const [navState, setNavState] = useState(() => readPipelineNavigation(pipelineScope));
  const [savedViewName, setSavedViewName] = useState('');
  const bootstrapPipelinePrefs = getBootstrappedPipelinePreferences(bootstrap);
  const syncReadyRef = useRef(false);
  const syncTimerRef = useRef(null);
  const lastSyncedPrefsRef = useRef('');

  useEffect(() => {
    setViewPrefs(readPipelinePreferences(pipelineScope));
    setNavState(readPipelineNavigation(pipelineScope));
  }, [pipelineScope]);

  const syncServerPreferences = async (prefs, { silent = true } = {}) => {
    const normalized = normalizePipelinePreferences(prefs);
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
    let next = local;
    let syncedBaseline = '';

    if (remote && hasMeaningfulPipelinePreferences(remote)) {
      if (!pipelinePreferencesEqual(local, remote)) {
        next = writePipelinePreferences(pipelineScope, remote);
      } else {
        next = remote;
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
    const normalized = writePipelinePreferences(pipelineScope, nextValue);
    setViewPrefs(normalized);
    return normalized;
  };

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
      toast('Pipeline refreshed.', 'success');
    } catch {
      toast('Could not refresh the pipeline.', 'error');
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
        else failedCount += 1;
      } catch {
        failedCount += 1;
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
    toast(
      failedCount > 0
        ? `${successCount} invoice(s) routed, ${failedCount} failed.`
        : `${successCount} invoice(s) routed for approval.`,
      failedCount > 0 ? 'warning' : 'success',
    );
  });

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (!displayed.length || isTypingTarget(event.target)) return;
      const currentIndex = Math.max(0, displayed.findIndex((item) => String(item.id || '') === String(activeItemId || '')));
      const currentItem = displayed[currentIndex] || displayed[0];
      const lower = String(event.key || '').toLowerCase();
      let handled = false;

      if (lower === 'j' || event.key === 'ArrowDown') {
        setActiveItemId(String(displayed[Math.min(displayed.length - 1, currentIndex + 1)]?.id || ''));
        handled = true;
      } else if (lower === 'k' || event.key === 'ArrowUp') {
        setActiveItemId(String(displayed[Math.max(0, currentIndex - 1)]?.id || ''));
        handled = true;
      } else if (lower === 'x' && currentItem?.id) {
        toggleSelected(currentItem.id);
        handled = true;
      } else if (lower === 'o' && currentItem) {
        openItemDetail(navigate, pipelineScope, currentItem);
        handled = true;
      } else if (lower === 'e' && currentItem) {
        openItemEmail(pipelineScope, currentItem);
        handled = true;
      } else if (lower === 'a') {
        void routeSelected();
        handled = true;
      }

      if (handled) {
        event.preventDefault();
        event.stopPropagation();
      }
    };

    window.addEventListener('keydown', handleKeyDown, true);
    return () => window.removeEventListener('keydown', handleKeyDown, true);
  }, [activeItemId, displayed, navigate, pipelineScope, routeSelected, selectedItems.length]);

  if (loading) {
    return html`<div class="panel" style="padding:48px;text-align:center"><p class="muted">Loading AP pipeline…</p></div>`;
  }

  return html`
    <div class="kpi-row">
      <div class="kpi-card">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums">${stats.total}</strong>
        <span>Total records</span>
      </div>
      <div class="kpi-card">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums">${stats.open}</strong>
        <span>Open records</span>
      </div>
      <div class="kpi-card kpi-warning">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums">${stats.waitingApproval}</strong>
        <span>Waiting approval</span>
      </div>
      <div class="kpi-card" style="border-color:#A7F3D0">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums;color:var(--brand-muted)">${stats.readyToPost}</strong>
        <span>Ready to post</span>
      </div>
      <div class="kpi-card" style="border-color:#FCA5A5">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums;color:#B91C1C">${stats.overdue}</strong>
        <span>Overdue</span>
      </div>
    </div>

    ${focusedItem
      ? html`
          <div class="panel" style="padding:14px 16px;border-color:${focusedItemVisible ? '#A7F3D0' : '#FCD34D'}">
            <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap">
              <div>
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
                  <strong>Current thread item</strong>
                  <${StatePill} state=${focusedItem.state} />
                </div>
                <div class="muted" style="font-size:13px">
                  ${focusedItem.vendor_name || focusedItem.vendor || 'Unknown vendor'} · ${getDocumentSummary(focusedItem)} · ${getAmountLabel(focusedItem)}
                </div>
                <div class="muted" style="font-size:12px;margin-top:4px">
                  ${focusedItemVisible
                    ? 'The current item is visible in this pipeline view.'
                    : 'The current item is outside the active slice or filters. Open its AP slice to keep queue context intact.'}
                </div>
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                ${!focusedItemVisible
                  ? html`<button class="alt" onClick=${revealFocusedItem}>Show in pipeline</button>`
                  : null}
                <button class="alt" onClick=${() => openItemDetail(navigate, pipelineScope, focusedItem)}>Open record</button>
                <button class="alt" onClick=${clearFocus}>Clear focus</button>
              </div>
            </div>
          </div>
        `
      : null}

    <div class="panel" style="padding:16px 18px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:12px">
        <div>
          <h3 style="margin:0 0 4px">Pipeline views</h3>
          <p class="muted" style="margin:0">Work AP by queue slice, then save and pin the views you reopen every day.</p>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="alt" onClick=${() => navigate('clearledgr/home')}>Back to Home</button>
          <button class="alt" onClick=${doRefresh} disabled=${refreshing}>${refreshing ? 'Refreshing…' : 'Refresh'}</button>
        </div>
      </div>
      <div style="display:flex;gap:10px;overflow-x:auto;padding-bottom:4px">
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
      <div style="display:flex;flex-direction:column;gap:14px;margin-top:14px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
          <div>
            <strong style="font-size:13px">Starter views</strong>
            <div class="muted" style="font-size:12px">Finance-native defaults you can pin to Home.</div>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${starterViews.map((view) => html`
              <${SavedViewChip}
                key=${view.id}
                view=${view}
                active=${activeSavedView?.scope === view.scope && activeSavedView?.id === view.id}
                onOpen=${() => applySavedView(view)}
                onTogglePin=${() => toggleSavedViewPin(view)}
              />
            `)}
          </div>
        </div>
        ${(personalViews.length || 0) > 0
          ? html`
              <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
                <div>
                  <strong style="font-size:13px">Personal views</strong>
                  <div class="muted" style="font-size:12px">${pinnedViews.length} pinned for quick access from Home.</div>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap">
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
            `
          : null}
      </div>
    </div>

    <div class="panel" style="padding:16px 18px">
      <div style="display:grid;grid-template-columns:2fr 1.25fr 1fr 1fr;gap:10px;align-items:end">
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
          <span class="muted" style="font-size:12px">Amount band</span>
          <select value=${viewPrefs.filters.amount} onChange=${(event) => updateFilters({ amount: event.target.value })}>
            <option value="all">All</option>
            <option value="under_1k">Under 1k</option>
            <option value="1k_10k">1k - 10k</option>
            <option value="over_10k">Over 10k</option>
          </select>
        </label>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr auto;gap:10px;align-items:end;margin-top:12px">
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
          <span class="muted" style="font-size:12px">Blocker type</span>
          <select value=${viewPrefs.filters.blocker} onChange=${(event) => updateFilters({ blocker: event.target.value })}>
            <option value="all">All</option>
            <option value="approval">Approval</option>
            <option value="info">Needs info</option>
            <option value="erp">ERP</option>
            <option value="exception">Policy</option>
            <option value="confidence">Field review</option>
            <option value="budget">Budget</option>
            <option value="po">PO / GR</option>
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
        <div style="display:flex;gap:8px;align-items:center;justify-content:flex-end">
          <button
            class="alt"
            onClick=${() => persistPrefs({ ...viewPrefs, viewMode: viewPrefs.viewMode === 'table' ? 'cards' : 'table' })}
          >${viewPrefs.viewMode === 'table' ? 'Cards' : 'Table'}</button>
        </div>
      </div>

      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px">
        <input
          value=${savedViewName}
          onInput=${(event) => setSavedViewName(event.target.value)}
          placeholder="Save current view as a personal view…"
          style="min-width:220px;padding:9px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;font-family:inherit"
        />
        <button class="alt" onClick=${saveView} disabled=${savingView}>${savingView ? 'Saving…' : 'Save personal view'}</button>
        ${activeSavedView?.scope === 'user'
          ? html`<button class="alt" onClick=${updateView} disabled=${updatingView}>${updatingView ? 'Updating…' : 'Update active view'}</button>`
          : null}
        <button class="alt" onClick=${resetFiltersAndSearch}>Reset filters</button>
        <span class="muted" style="font-size:12px">
          Sort ${viewPrefs.sortDir === 'desc' ? 'descending' : 'ascending'} by ${viewPrefs.sortCol.replace(/_/g, ' ')}.
        </span>
      </div>

      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:12px">
        <div class="muted" style="font-size:12px">
          Keyboard: J/K move · X select · O open detail · E open thread · A route selected/current invoice
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="alt" onClick=${selectVisible}>Select visible</button>
          <button class="alt" onClick=${clearSelection} disabled=${selectedIds.length === 0}>Clear selection</button>
          <span class="muted" style="font-size:12px;align-self:center">
            ${selectedItems.length ? `${selectedItems.length} selected` : 'No selection'}
            ${routeableSelectedItems.length ? ` · ${routeableSelectedItems.length} routeable` : ''}
          </span>
          <button
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

    ${viewPrefs.viewMode === 'cards'
      ? html`
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px">
            ${displayed.length === 0
              ? html`<div class="panel" style="grid-column:1/-1;text-align:center;padding:32px"><p class="muted">No records match this view.</p></div>`
              : displayed.map((item) => {
                  const blockers = getPipelineBlockerKinds(item);
                  const focused = String(navState.focusItemId || '') === String(item.id || '');
                  const active = String(activeItemId || '') === String(item.id || '');
                  const approvalWait = getApprovalWaitMinutes(item);
                  const queueAge = getQueueAgeMinutes(item);
                  const erpStatus = getErpStatus(item);
                  const routeable = isRouteableInvoiceItem(item);
                  return html`
                    <div
                      key=${item.id}
                      class="panel"
                      style="padding:16px;margin-bottom:0;border-color:${active || focused ? 'var(--accent)' : 'var(--border)'};box-shadow:${active || focused ? '0 0 0 1px var(--accent-soft)' : 'none'}"
                      onClick=${() => setActiveItemId(String(item.id || ''))}
                    >
                      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px">
                        <div>
                          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                            <label style="display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:var(--ink-secondary)">
                              <input
                                type="checkbox"
                                checked=${selectedSet.has(String(item.id || ''))}
                                onClick=${(event) => event.stopPropagation()}
                                onChange=${() => toggleSelected(item.id)}
                              />
                              Select
                            </label>
                            <div style="font-size:15px;font-weight:700">${item.vendor_name || item.vendor || 'Unknown vendor'}</div>
                          </div>
                          <div class="muted" style="font-size:12px;margin-top:2px">${getDocumentSummary(item)} · ${getAmountLabel(item)}</div>
                        </div>
                        <${StatePill} state=${item.state} />
                      </div>
                      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
                        <div style="padding:10px 12px;border:1px solid var(--border);border-radius:12px;background:var(--bg)">
                          <div class="muted" style="font-size:11px">Queue age</div>
                          <strong style="font-size:13px">${formatDurationMinutes(queueAge)}</strong>
                        </div>
                        <div style="padding:10px 12px;border:1px solid var(--border);border-radius:12px;background:var(--bg)">
                          <div class="muted" style="font-size:11px">Approval wait</div>
                          <strong style="font-size:13px">${approvalWait ? formatDurationMinutes(approvalWait) : '—'}</strong>
                        </div>
                      </div>
                      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
                        ${blockers.length
                          ? blockers.slice(0, 3).map((kind) => html`<${BlockerChip} key=${kind} kind=${kind} />`)
                          : html`<span class="muted" style="font-size:12px">No blocking signals</span>`}
                      </div>
                      <${FieldReviewSummary} item=${item} />
                      <div class="muted" style="font-size:12px;line-height:1.5;margin-bottom:12px">
                        ${getPipelineTimeline(item, erpStatus)}
                      </div>
                      <div style="display:flex;gap:8px;flex-wrap:wrap">
                        ${routeable
                          ? html`<button onClick=${(event) => { event.stopPropagation(); routeSelected([item]); }} disabled=${routingSelected}>${routingSelected ? 'Routing…' : 'Route approval'}</button>`
                          : null}
                        <button class="alt" onClick=${(event) => { event.stopPropagation(); openItemDetail(navigate, pipelineScope, item); }}>Open record</button>
                        ${(item.thread_id || item.message_id) && html`
                          <button class="alt" onClick=${(event) => { event.stopPropagation(); openItemEmail(pipelineScope, item); }}>Open email</button>
                        `}
                      </div>
                    </div>
                  `;
                })}
          </div>
        `
      : html`
          <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-md);overflow-x:auto">
            <table class="table" style="min-width:1320px">
              <thead>
                <tr>
                  <th>Select</th>
                  <th>Vendor</th>
                  <th>Document</th>
                  <th style="text-align:right">Amount</th>
                  <th>Due</th>
                  <th>Status</th>
                  <th>Queue age</th>
                  <th>Approval wait</th>
                  <th>ERP</th>
                  <th>Blockers</th>
                  <th>Updated</th>
                  <th style="text-align:right">Actions</th>
                </tr>
              </thead>
              <tbody>
                ${displayed.length === 0
                  ? html`<tr><td colspan="12" class="muted" style="text-align:center;padding:32px">No records match this view.</td></tr>`
                  : displayed.map((item) => {
                      const blockers = getPipelineBlockerKinds(item);
                      const focused = String(navState.focusItemId || '') === String(item.id || '');
                      const active = String(activeItemId || '') === String(item.id || '');
                      const approvalWait = getApprovalWaitMinutes(item);
                      const queueAge = getQueueAgeMinutes(item);
                      const erpStatus = getErpStatus(item);
                      const isInvoiceDocument = isInvoiceDocumentType(item?.document_type);
                      const routeable = isRouteableInvoiceItem(item);
                      return html`
                        <tr
                          key=${item.id}
                          style=${active || focused ? 'background:rgba(14,165,233,0.07)' : ''}
                          onClick=${() => setActiveItemId(String(item.id || ''))}
                        >
                          <td>
                            <input
                              type="checkbox"
                              checked=${selectedSet.has(String(item.id || ''))}
                              onClick=${(event) => event.stopPropagation()}
                              onChange=${() => toggleSelected(item.id)}
                            />
                          </td>
                          <td style="font-weight:600;cursor:pointer" onClick=${(event) => { event.stopPropagation(); openItemDetail(navigate, pipelineScope, item); }}>${item.vendor_name || item.vendor || 'Unknown vendor'}</td>
                          <td style="font-family:var(--font-mono);font-size:12px">${getDocumentSummary(item)}</td>
                          <td style="text-align:right;font-family:var(--font-mono);font-variant-numeric:tabular-nums">${getAmountLabel(item)}</td>
                          <td>${isInvoiceDocument && item.due_date ? fmtDate(item.due_date) : '—'}</td>
                          <td><${StatePill} state=${item.state} /></td>
                          <td>${formatDurationMinutes(queueAge)}</td>
                          <td>${isInvoiceDocument && approvalWait ? formatDurationMinutes(approvalWait) : '—'}</td>
                          <td>${isInvoiceDocument ? (ERP_STATUS_LABELS[erpStatus] || erpStatus) : 'N/A'}</td>
                          <td>
                            <div style="display:flex;gap:6px;flex-wrap:wrap">
                              ${blockers.length
                                ? blockers.slice(0, 2).map((kind) => html`<${BlockerChip} key=${kind} kind=${kind} />`)
                                : html`<span class="muted" style="font-size:12px">Clear</span>`}
                            </div>
                            <${FieldReviewSummary} item=${item} compact=${true} />
                          </td>
                          <td class="muted" style="font-size:12px">${fmtDateTime(item.updated_at || item.created_at)}</td>
                          <td style="text-align:right">
                            <div style="display:flex;gap:8px;justify-content:flex-end">
                              ${routeable
                                ? html`<button onClick=${(event) => { event.stopPropagation(); routeSelected([item]); }} disabled=${routingSelected}>${routingSelected ? 'Routing…' : 'Route'}</button>`
                                : null}
                              <button class="alt" onClick=${(event) => { event.stopPropagation(); openItemDetail(navigate, pipelineScope, item); }}>Open record</button>
                              ${(item.thread_id || item.message_id) && html`
                                <button class="alt" onClick=${(event) => { event.stopPropagation(); openItemEmail(pipelineScope, item); }}>Open email</button>
                              `}
                            </div>
                          </td>
                        </tr>
                      `;
                    })}
              </tbody>
            </table>
          </div>
        `}

    <div class="muted" style="text-align:center;padding:12px 0;font-size:12px">
      Showing ${displayed.length} of ${items.length} records in ${PIPELINE_BUILTIN_SLICES.find((slice) => slice.id === viewPrefs.activeSliceId)?.label || 'this view'}.
    </div>
  `;
}
