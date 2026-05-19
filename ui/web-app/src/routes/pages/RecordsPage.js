/**
 * Records — read-only directory of AP records.
 *
 * The workspace is the coordination-layer control center, not a workflow
 * desktop. This page lets the operator search, filter, and open any
 * record. Decisions don't happen here:
 *   - Approvals → Slack / Teams approval cards
 *   - Vendor follow-up → Gmail
 *   - Posting → the agent + ERP
 *   - Intervention on an escalated record → the record's detail page
 *
 * There are no bulk-action toolbars, no Kanban columns, no per-row
 * approve/reject. The earlier "Live AP queue" Kanban with BatchOps
 * was a Streak/BILL-shaped workflow desktop; the workspace is
 * Linear / Vercel / Datadog / Modal-shaped, where the list view is
 * a search-and-explore surface and the action surfaces are elsewhere.
 */
import { h } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import htm from 'htm';

import { useAction } from '../route-helpers.js';
import { formatAmount } from '../../utils/formatters.js';
import { navigateToRecordDetail } from '../../utils/record-route.js';
import {
  getDocumentReferenceText,
  getDocumentTypeLabel,
  normalizeDocumentType,
} from '../../utils/document-types.js';
import {
  PIPELINE_BUILTIN_SLICES,
  activatePipelineSlice,
  buildPipelinePreferencePatch,
  buildPipelineSliceCounts,
  clearPipelineNavigation,
  createSavedPipelineView,
  filterPipelineItems,
  getAllPipelineViews,
  getBootstrappedPipelinePreferences,
  getPersonalPipelineViews,
  getPipelineBlockers,
  getPipelineViewRef,
  getQueueAgeMinutes,
  getStarterPipelineViews,
  getSuggestedPipelineSlice,
  hasMeaningfulPipelinePreferences,
  normalizePipelinePreferences,
  pinPipelineView,
  pipelinePreferencesEqual,
  pipelineSnapshotsEqual,
  readPipelineNavigation,
  readPipelinePreferences,
  removeSavedPipelineView,
  unpinPipelineView,
  updateSavedPipelineView,
  writePipelinePreferences,
} from '../pipeline-views.js';

const html = htm.bind(h);

const STATE_STYLES = {
  needs_approval: { bg: '#FEFCE8', text: '#A16207', label: 'Needs approval' },
  needs_second_approval: { bg: '#FEFCE8', text: '#A16207', label: 'Second approval' },
  needs_info: { bg: '#FEFCE8', text: '#A16207', label: 'Needs info' },
  validated: { bg: '#EFF6FF', text: '#1D4ED8', label: 'Validated' },
  received: { bg: '#F1F5F9', text: '#64748B', label: 'Received' },
  approved: { bg: '#ECFDF5', text: '#059669', label: 'Approved' },
  ready_to_post: { bg: '#DCFCE7', text: '#166534', label: 'Ready to post' },
  posted_to_erp: { bg: '#ECFDF5', text: '#10B981', label: 'Posted' },
  snoozed: { bg: '#EEF2FF', text: '#4338CA', label: 'Snoozed' },
  reversed: { bg: '#FEF2F2', text: '#DC2626', label: 'Reversed' },
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

function formatDurationMinutes(value) {
  const minutes = Number(value || 0);
  if (!Number.isFinite(minutes) || minutes <= 0) return '0m';
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 1440) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / 1440)}d`;
}

function StatePill({ state }) {
  const normalized = String(state || '').toLowerCase();
  const tone = STATE_STYLES[normalized] || {
    bg: '#F1F5F9', text: '#64748B', label: normalized.replace(/_/g, ' ') || 'unknown',
  };
  return html`<span class="cl-records-state" style=${`background:${tone.bg};color:${tone.text}`}>${tone.label}</span>`;
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

function dueBadge(dueDate) {
  if (!dueDate) return null;
  const ms = new Date(dueDate).getTime();
  if (!Number.isFinite(ms)) return null;
  const days = Math.round((ms - Date.now()) / 86400000);
  if (days < 0) {
    return { label: `${Math.abs(days)}d overdue`, bg: '#FEF2F2', color: '#B91C1C', border: '#FECACA' };
  }
  if (days <= 7) {
    return {
      label: days === 0 ? 'Due today' : `Due in ${days}d`,
      bg: '#FFFBEB', color: '#92400E', border: '#FDE68A',
    };
  }
  return null;
}

export default function RecordsPage({ api, bootstrap, toast, orgId, userEmail, navigate }) {
  const pipelineScope = useMemo(() => ({ orgId, userEmail }), [orgId, userEmail]);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [viewPrefs, setViewPrefs] = useState(() => normalizePipelinePreferences({
    ...readPipelinePreferences(pipelineScope),
    viewMode: 'table',
  }));
  const [navState, setNavState] = useState(() => readPipelineNavigation(pipelineScope));
  const [savedViewName, setSavedViewName] = useState('');
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [viewsOpen, setViewsOpen] = useState(false);

  const bootstrapPipelinePrefs = getBootstrappedPipelinePreferences(bootstrap);
  const syncReadyRef = useRef(false);
  const syncTimerRef = useRef(null);
  const lastSyncedPrefsRef = useRef('');

  useEffect(() => {
    setViewPrefs(normalizePipelinePreferences({
      ...readPipelinePreferences(pipelineScope),
      viewMode: 'table',
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
      toast('Records refreshed.', 'success');
    } catch {
      toast('Could not refresh records.', 'error');
    } finally {
      setLoading(false);
    }
  });

  const [saveView, savingView] = useAction(async () => {
    const name = String(savedViewName || '').trim();
    if (!name) {
      toast('Name the saved view first.', 'warning');
      return;
    }
    const next = createSavedPipelineView(pipelineScope, {
      name,
      snapshot: { ...viewPrefs, filters: viewPrefs.filters },
    });
    setViewPrefs(next);
    setSavedViewName('');
    toast(`Saved view "${name}" added.`, 'success');
  });

  const displayed = useMemo(() => filterPipelineItems(items, {
    activeSliceId: viewPrefs.activeSliceId,
    filters: viewPrefs.filters,
    searchQuery,
    sortCol: viewPrefs.sortCol,
    sortDir: viewPrefs.sortDir,
  }), [items, searchQuery, viewPrefs]);

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
    persistPrefs({ ...viewPrefs, sortCol: nextSortCol, sortDir: nextSortDir });
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
    setViewPrefs(persistPrefs({
      ...viewPrefs,
      activeSliceId: getSuggestedPipelineSlice(focusedItem),
      sortCol: 'queue_age',
      sortDir: 'desc',
      filters: buildResetFilters(),
    }));
  };

  const clearFocus = () => {
    clearPipelineNavigation(pipelineScope);
    setNavState(readPipelineNavigation(pipelineScope));
  };

  const openRecord = (item) => {
    if (!item?.id) return;
    navigateToRecordDetail(navigate, item.id);
  };

  const currentSliceLabel = PIPELINE_BUILTIN_SLICES
    .find((slice) => slice.id === viewPrefs.activeSliceId)?.label || 'All open';
  const currentViewLabel = activeSavedView ? getSavedViewLabel(activeSavedView) : currentSliceLabel;

  if (loading) {
    return html`<div class="panel" style="padding:48px;text-align:center"><p class="muted">Loading records…</p></div>`;
  }

  return html`
    <div class="cl-records">
      <header class="cl-records-head">
        <div>
          <h1 class="cl-records-title">Records</h1>
          <p class="cl-records-sub">
            Search, filter, and inspect AP records. Approvals happen in Slack and Teams;
            vendor follow-up in Gmail. Open a record to see the agent's reasoning, audit
            trail, and intervention options.
          </p>
        </div>
        <div class="cl-records-head-actions">
          <button
            class="btn-secondary btn-sm"
            onClick=${() => setViewsOpen((v) => !v)}>
            Views${activeSavedView ? ` · ${getSavedViewLabel(activeSavedView)}` : ''}
          </button>
          <button
            class="btn-secondary btn-sm"
            onClick=${() => setFiltersOpen(true)}>
            Filters${activeFilterCount > 0 ? ` (${activeFilterCount})` : ''}
          </button>
          <button
            class="btn-secondary btn-sm"
            onClick=${doRefresh}
            disabled=${refreshing}
            aria-label="Refresh records">
            ${refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </header>

      <div class="cl-records-search">
        <input
          placeholder="Search vendor, invoice number, PO, sender…"
          value=${searchQuery}
          onInput=${(event) => setSearchQuery(event.target.value)}
          aria-label="Search records"
        />
      </div>

      ${focusedItem ? html`
        <div class="cl-records-focus" role="region" aria-label="Linked thread">
          <div class="cl-records-focus-copy">
            <div class="cl-records-focus-head">
              <strong>Thread record</strong>
              <${StatePill} state=${focusedItem.state} />
            </div>
            <div class="cl-records-focus-meta">
              ${focusedItem.vendor_name || focusedItem.vendor || 'Vendor not extracted'} ·
              ${getDocumentSummary(focusedItem)} · ${getAmountLabel(focusedItem)}
            </div>
          </div>
          <div class="cl-records-focus-actions">
            ${!focusedItemVisible ? html`
              <button class="btn-primary btn-sm" onClick=${revealFocusedItem}>Show</button>
            ` : null}
            <button class="btn-secondary btn-sm" onClick=${() => openRecord(focusedItem)}>Open</button>
            <button class="btn-ghost btn-sm" onClick=${clearFocus} aria-label="Clear linked thread">×</button>
          </div>
        </div>
      ` : null}

      <div class="cl-records-scope" role="tablist" aria-label="Record scope">
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
              role="tab"
              aria-selected=${isActive}
              class=${`cl-records-scope-pill ${isActive ? 'is-active' : ''}`}
              onClick=${() => applySlice(scope.id)}>
              <span>${scope.label}</span>
              <span class="cl-records-scope-count">${count}</span>
            </button>
          `;
        })}
      </div>

      ${displayed.length === 0
        ? html`<div class="cl-records-empty">
            <strong>No records match the current view.</strong>
            <p class="muted">Try clearing filters or switching scope.</p>
            <button class="btn-secondary btn-sm" onClick=${resetFiltersAndSearch}>Reset</button>
          </div>`
        : html`<div class="cl-records-table" role="table" aria-label="Records">
            <div class="cl-records-row cl-records-row-header" role="row">
              <span role="columnheader">Vendor</span>
              <span role="columnheader">Reference</span>
              <span role="columnheader" class="cl-records-num">Amount</span>
              <span role="columnheader">State</span>
              <span role="columnheader">Blocker</span>
              <span role="columnheader">Age</span>
              <span role="columnheader">Due</span>
            </div>
            ${displayed.map((item) => {
              const blockers = getPipelineBlockers(item);
              const primaryBlocker = blockers[0];
              const extraBlockers = blockers.length - 1;
              const blockerKind = String(primaryBlocker?.kind || '').toLowerCase();
              const blockerLabel = primaryBlocker?.chip_label
                || primaryBlocker?.title
                || primaryBlocker?.label
                || BLOCKER_LABELS[blockerKind]
                || primaryBlocker?.kind
                || '';
              const due = dueBadge(item.due_date);
              return html`
                <button
                  type="button"
                  class="cl-records-row"
                  role="row"
                  key=${item.id}
                  onClick=${() => openRecord(item)}
                  aria-label=${`Open ${item.vendor_name || 'record'}`}>
                  <span class="cl-records-cell-vendor" role="cell">
                    ${item.vendor_name || item.vendor || 'Vendor not extracted'}
                  </span>
                  <span class="cl-records-cell-ref" role="cell">
                    <code>${String(item.invoice_number || '').trim() || '—'}</code>
                  </span>
                  <span class="cl-records-cell-amount cl-records-num" role="cell">
                    ${getAmountLabel(item)}
                  </span>
                  <span role="cell"><${StatePill} state=${item.state} /></span>
                  <span class="cl-records-cell-blocker" role="cell">
                    ${primaryBlocker ? html`
                      <span class="cl-records-blocker">${blockerLabel}</span>
                      ${extraBlockers > 0 ? html`<span class="cl-records-blocker-extra">+${extraBlockers}</span>` : null}
                    ` : html`<span class="muted">—</span>`}
                  </span>
                  <span class="cl-records-cell-age" role="cell">
                    ${formatDurationMinutes(getQueueAgeMinutes(item))}
                  </span>
                  <span class="cl-records-cell-due" role="cell">
                    ${due ? html`<span class="cl-records-due" style=${`background:${due.bg};color:${due.color};border-color:${due.border}`}>${due.label}</span>` : html`<span class="muted">—</span>`}
                  </span>
                </button>
              `;
            })}
          </div>`}

      ${viewsOpen ? html`
        <div class="cl-records-popover-backdrop" onClick=${() => setViewsOpen(false)}>
          <div class="cl-records-popover" onClick=${(e) => e.stopPropagation()}>
            <div class="cl-records-popover-head">
              <strong>Views</strong>
              <button class="btn-ghost btn-xs" onClick=${() => setViewsOpen(false)}>Close</button>
            </div>

            <div class="cl-records-popover-label">Slice</div>
            <div class="cl-records-popover-list">
              ${PIPELINE_BUILTIN_SLICES.map((slice) => html`
                <button
                  key=${slice.id}
                  class=${`cl-records-popover-row ${viewPrefs.activeSliceId === slice.id ? 'is-active' : ''}`}
                  onClick=${() => { applySlice(slice.id); setViewsOpen(false); }}>
                  <span>${slice.label}</span>
                  <span class="cl-records-popover-count">${sliceCounts[slice.id] || 0}</span>
                </button>
              `)}
            </div>

            ${(starterViews.length > 0 || personalViews.length > 0) ? html`
              <div class="cl-records-popover-label">Saved</div>
              <div class="cl-records-popover-list">
                ${[...starterViews, ...personalViews].map((view) => html`
                  <div key=${view.id} class=${`cl-records-popover-saved ${activeSavedView?.id === view.id ? 'is-active' : ''}`}>
                    <button
                      class="cl-records-popover-saved-name"
                      onClick=${() => { applySavedView(view); setViewsOpen(false); }}>
                      ${getSavedViewLabel(view)}
                    </button>
                    <button
                      class="btn-ghost btn-xs"
                      onClick=${() => toggleSavedViewPin(view)}
                      aria-label=${view.pinned ? 'Unpin' : 'Pin'}>
                      ${view.pinned ? '★' : '☆'}
                    </button>
                    ${view.scope === 'user' ? html`
                      <button class="btn-ghost btn-xs" onClick=${() => removeView(view.id)} aria-label="Remove">✕</button>
                    ` : null}
                  </div>
                `)}
              </div>
            ` : null}

            <div class="cl-records-popover-label">Save current</div>
            <div class="cl-records-popover-save">
              <input
                value=${savedViewName}
                onInput=${(event) => setSavedViewName(event.target.value)}
                placeholder="View name"
              />
              <button class="btn-primary btn-sm" onClick=${saveView} disabled=${savingView}>
                ${savingView ? '…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      ` : null}

      ${filtersOpen ? html`
        <div class="cl-records-popover-backdrop" onClick=${() => setFiltersOpen(false)}>
          <div class="cl-records-drawer" onClick=${(e) => e.stopPropagation()}>
            <div class="cl-records-popover-head">
              <strong>Filters</strong>
              <button class="btn-ghost btn-sm" onClick=${() => setFiltersOpen(false)}>Close</button>
            </div>

            <label class="cl-records-field">
              <span>Vendor</span>
              <input
                placeholder="Any vendor"
                value=${viewPrefs.filters.vendor}
                onInput=${(event) => updateFilters({ vendor: event.target.value })}
              />
            </label>

            <label class="cl-records-field">
              <span>Due date</span>
              <select value=${viewPrefs.filters.due} onChange=${(event) => updateFilters({ due: event.target.value })}>
                <option value="all">All</option>
                <option value="overdue">Overdue</option>
                <option value="due_7d">Due in 7 days</option>
                <option value="no_due">No due date</option>
              </select>
            </label>

            <label class="cl-records-field">
              <span>Blocker type</span>
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

            <label class="cl-records-field">
              <span>ERP status</span>
              <select value=${viewPrefs.filters.erpStatus} onChange=${(event) => updateFilters({ erpStatus: event.target.value })}>
                <option value="all">All</option>
                <option value="ready">Ready</option>
                <option value="failed">Failed</option>
                <option value="connected">Connected</option>
                <option value="posted">Posted</option>
                <option value="not_connected">Not connected</option>
              </select>
            </label>

            <label class="cl-records-field">
              <span>Amount band</span>
              <select value=${viewPrefs.filters.amount} onChange=${(event) => updateFilters({ amount: event.target.value })}>
                <option value="all">All</option>
                <option value="under_1k">Under 1k</option>
                <option value="1k_10k">1k - 10k</option>
                <option value="over_10k">Over 10k</option>
              </select>
            </label>

            <label class="cl-records-field">
              <span>Approval age</span>
              <select value=${viewPrefs.filters.approvalAge} onChange=${(event) => updateFilters({ approvalAge: event.target.value })}>
                <option value="all">All</option>
                <option value="under_24h">Under 24h</option>
                <option value="1d_3d">1-3 days</option>
                <option value="over_3d">Over 3 days</option>
              </select>
            </label>

            <label class="cl-records-field">
              <span>Sort by</span>
              <select value=${viewPrefs.sortCol} onChange=${(event) => updateSort(event.target.value)}>
                <option value="queue_age">Queue age</option>
                <option value="due_date">Due date</option>
                <option value="amount">Amount</option>
                <option value="updated_at">Last update</option>
                <option value="approval_wait">Approval wait</option>
              </select>
              <small class="muted">
                ${viewPrefs.sortDir === 'desc' ? 'Descending' : 'Ascending'} — pick the same option to flip.
              </small>
            </label>

            <div class="cl-records-drawer-actions">
              <button class="btn-ghost btn-sm" onClick=${() => { resetFiltersAndSearch(); setFiltersOpen(false); }}>Reset</button>
              <button class="btn-primary btn-sm" onClick=${() => setFiltersOpen(false)}>Done</button>
            </div>
          </div>
        </div>
      ` : null}
    </div>
  `;
}
