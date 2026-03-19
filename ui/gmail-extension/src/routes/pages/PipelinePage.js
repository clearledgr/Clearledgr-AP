/**
 * AP Pipeline View — Gmail-native queue surface.
 * Streak-style doctrine: queue slices first, detail second, no dashboard sprawl.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDate, fmtDateTime, useAction } from '../route-helpers.js';
import { openSourceEmail } from '../../utils/formatters.js';
import store from '../../utils/store.js';
import {
  PIPELINE_BUILTIN_SLICES,
  activatePipelineSlice,
  buildPipelineSliceCounts,
  createSavedPipelineView,
  filterPipelineItems,
  getPipelineBlockerKinds,
  normalizePipelineState,
  readPipelinePreferences,
  removeSavedPipelineView,
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
  po: 'PO/GR issue',
};

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
      cursor:pointer;font-family:inherit;text-align:left;min-width:170px;
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

function saveActiveItemId(itemId) {
  if (typeof window === 'undefined' || !window?.localStorage) return;
  try {
    window.localStorage.setItem(ACTIVE_AP_ITEM_STORAGE_KEY, String(itemId || ''));
  } catch {
    /* best effort */
  }
  if (itemId) {
    store.setSelectedItem(String(itemId));
  }
}

function openItemDetail(navigate, item) {
  if (!item?.id) return;
  saveActiveItemId(item.id);
  navigate(`clearledgr/invoice/${item.id}`);
}

function openItemEmail(item) {
  if (!item?.id) return false;
  saveActiveItemId(item.id);
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

function getSavedViewLabel(view) {
  return String(view?.name || '').trim() || 'Saved view';
}

export default function PipelinePage({ api, toast, orgId, navigate }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [viewPrefs, setViewPrefs] = useState(() => readPipelinePreferences(orgId));
  const [savedViewName, setSavedViewName] = useState('');

  useEffect(() => {
    setViewPrefs(readPipelinePreferences(orgId));
  }, [orgId]);

  useEffect(() => {
    setLoading(true);
    api(`/extension/worklist?organization_id=${encodeURIComponent(orgId)}&limit=500`)
      .then((data) => setItems(Array.isArray(data?.items) ? data.items : []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [api, orgId]);

  const persistPrefs = (nextValue) => {
    const normalized = writePipelinePreferences(orgId, nextValue);
    setViewPrefs(normalized);
    return normalized;
  };

  const [doRefresh, refreshing] = useAction(async () => {
    setLoading(true);
    try {
      const data = await api(`/extension/worklist?organization_id=${encodeURIComponent(orgId)}&limit=500`);
      setItems(Array.isArray(data?.items) ? data.items : []);
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
      toast('Name the saved view first.', 'warning');
      return;
    }
    const next = createSavedPipelineView(orgId, {
      name,
      snapshot: {
        ...viewPrefs,
        filters: viewPrefs.filters,
      },
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

  const stats = useMemo(() => ({
    total: items.length,
    open: items.filter((item) => !['posted_to_erp', 'closed', 'rejected'].includes(normalizePipelineState(item.state))).length,
    waitingApproval: sliceCounts.approval_backlog || 0,
    readyToPost: sliceCounts.ready_to_post || 0,
  }), [items, sliceCounts]);

  const applySlice = (sliceId) => {
    const next = activatePipelineSlice(orgId, sliceId);
    setViewPrefs(next);
  };

  const applySavedView = (view) => {
    const next = persistPrefs(view.snapshot);
    setViewPrefs(next);
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
    const nextSortDir = viewPrefs.sortCol === nextSortCol && viewPrefs.sortDir === 'desc' ? 'asc' : 'desc';
    persistPrefs({
      ...viewPrefs,
      sortCol: nextSortCol,
      sortDir: nextSortDir,
    });
  };

  const removeSavedView = (viewId) => {
    const next = removeSavedPipelineView(orgId, viewId);
    setViewPrefs(next);
    toast('Saved view removed.', 'success');
  };

  if (loading) {
    return html`<div class="panel" style="padding:48px;text-align:center"><p class="muted">Loading AP pipeline…</p></div>`;
  }

  return html`
    <div class="kpi-row">
      <div class="kpi-card">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums">${stats.total}</strong>
        <span>Total invoices</span>
      </div>
      <div class="kpi-card">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums">${stats.open}</strong>
        <span>Open in AP</span>
      </div>
      <div class="kpi-card kpi-warning">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums">${stats.waitingApproval}</strong>
        <span>Waiting approval</span>
      </div>
      <div class="kpi-card" style="border-color:#A7F3D0">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums;color:var(--brand-muted)">${stats.readyToPost}</strong>
        <span>Ready to post</span>
      </div>
    </div>

    <div class="panel" style="padding:16px 18px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:12px">
        <div>
          <h3 style="margin:0 0 4px">Pipeline views</h3>
          <p class="muted" style="margin:0">Work AP by queue slice, then save the views you use repeatedly.</p>
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
      ${viewPrefs.customViews?.length
        ? html`
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
              ${viewPrefs.customViews.map((view) => html`
                <div key=${view.id} style="display:flex;align-items:center;gap:6px;padding:6px 8px;border-radius:999px;border:1px solid var(--border);background:var(--bg)">
                  <button class="alt" onClick=${() => applySavedView(view)} style="padding:6px 10px;font-size:12px">${getSavedViewLabel(view)}</button>
                  <button
                    aria-label="Delete saved view"
                    onClick=${() => removeSavedView(view.id)}
                    style="border:none;background:transparent;color:var(--ink-muted);cursor:pointer;padding:0 2px"
                  >×</button>
                </div>
              `)}
            </div>
          `
        : null}
    </div>

    <div class="panel" style="padding:16px 18px">
      <div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr auto;gap:10px;align-items:end">
        <label style="display:flex;flex-direction:column;gap:6px">
          <span class="muted" style="font-size:12px">Search</span>
          <input
            placeholder="Search vendors, invoices, PO, sender…"
            value=${searchQuery}
            onInput=${(event) => setSearchQuery(event.target.value)}
            style="padding:9px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;font-family:inherit"
          />
        </label>
        <label style="display:flex;flex-direction:column;gap:6px">
          <span class="muted" style="font-size:12px">State</span>
          <select value=${viewPrefs.filters.state} onChange=${(event) => updateFilters({ state: event.target.value })}>
            <option value="all">All</option>
            <option value="received">Received</option>
            <option value="validated">Validated</option>
            <option value="needs_info">Needs info</option>
            <option value="needs_approval">Needs approval</option>
            <option value="ready_to_post">Ready to post</option>
            <option value="failed_post">Failed post</option>
          </select>
        </label>
        <label style="display:flex;flex-direction:column;gap:6px">
          <span class="muted" style="font-size:12px">Due</span>
          <select value=${viewPrefs.filters.due} onChange=${(event) => updateFilters({ due: event.target.value })}>
            <option value="all">All</option>
            <option value="overdue">Overdue</option>
            <option value="due_7d">Due in 7 days</option>
            <option value="no_due">No due date</option>
          </select>
        </label>
        <label style="display:flex;flex-direction:column;gap:6px">
          <span class="muted" style="font-size:12px">Blocker</span>
          <select value=${viewPrefs.filters.blocker} onChange=${(event) => updateFilters({ blocker: event.target.value })}>
            <option value="all">All</option>
            <option value="approval">Approval</option>
            <option value="info">Needs info</option>
            <option value="erp">ERP</option>
            <option value="exception">Policy</option>
            <option value="confidence">Field review</option>
            <option value="budget">Budget</option>
            <option value="po">PO/GR</option>
          </select>
        </label>
        <label style="display:flex;flex-direction:column;gap:6px">
          <span class="muted" style="font-size:12px">Amount</span>
          <select value=${viewPrefs.filters.amount} onChange=${(event) => updateFilters({ amount: event.target.value })}>
            <option value="all">All</option>
            <option value="under_1k">Under 1k</option>
            <option value="1k_10k">1k - 10k</option>
            <option value="over_10k">Over 10k</option>
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
        <select value=${viewPrefs.sortCol} onChange=${(event) => updateSort(event.target.value)}>
          <option value="priority">Priority</option>
          <option value="due_date">Due date</option>
          <option value="amount">Amount</option>
          <option value="vendor">Vendor</option>
          <option value="updated_at">Last update</option>
          <option value="state">State</option>
        </select>
        <input
          value=${savedViewName}
          onInput=${(event) => setSavedViewName(event.target.value)}
          placeholder="Save current view as…"
          style="min-width:220px;padding:9px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;font-family:inherit"
        />
        <button class="alt" onClick=${saveView} disabled=${savingView}>${savingView ? 'Saving…' : 'Save view'}</button>
        <button class="alt" onClick=${() => {
          setSearchQuery('');
          setViewPrefs(writePipelinePreferences(orgId, {
            ...viewPrefs,
            activeSliceId: 'all_open',
            sortCol: 'priority',
            sortDir: 'desc',
            filters: { state: 'all', due: 'all', blocker: 'all', amount: 'all' },
          }));
        }}>Reset filters</button>
      </div>
    </div>

    ${viewPrefs.viewMode === 'cards'
      ? html`
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px">
            ${displayed.length === 0
              ? html`<div class="panel" style="grid-column:1/-1;text-align:center;padding:32px"><p class="muted">No invoices match this view.</p></div>`
              : displayed.map((item) => {
                  const blockers = getPipelineBlockerKinds(item);
                  return html`
                    <div key=${item.id} class="panel" style="padding:16px;margin-bottom:0">
                      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px">
                        <div>
                          <div style="font-size:15px;font-weight:700">${item.vendor_name || item.vendor || 'Unknown vendor'}</div>
                          <div class="muted" style="font-size:12px;margin-top:2px">${item.invoice_number || 'No invoice #'} · ${getAmountLabel(item)}</div>
                        </div>
                        <${StatePill} state=${item.state} />
                      </div>
                      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
                        ${blockers.length
                          ? blockers.slice(0, 3).map((kind) => html`<${BlockerChip} key=${kind} kind=${kind} />`)
                          : html`<span class="muted" style="font-size:12px">No blocking signals</span>`}
                      </div>
                      <div class="muted" style="font-size:12px;line-height:1.5;margin-bottom:12px">
                        Due ${item.due_date ? fmtDate(item.due_date) : '—'} · Updated ${fmtDateTime(item.updated_at || item.created_at)}
                      </div>
                      <div style="display:flex;gap:8px;flex-wrap:wrap">
                        <button class="alt" onClick=${() => openItemDetail(navigate, item)}>Open detail</button>
                        <button class="alt" onClick=${() => openItemEmail(item)} disabled=${!item.thread_id && !item.message_id}>Open email</button>
                      </div>
                    </div>
                  `;
                })}
          </div>
        `
      : html`
          <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-md);overflow-x:auto">
            <table class="table" style="min-width:980px">
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>Invoice</th>
                  <th style="text-align:right">Amount</th>
                  <th>Due</th>
                  <th>Status</th>
                  <th>Blockers</th>
                  <th>Updated</th>
                  <th style="text-align:right">Actions</th>
                </tr>
              </thead>
              <tbody>
                ${displayed.length === 0
                  ? html`<tr><td colspan="8" class="muted" style="text-align:center;padding:32px">No invoices match this view.</td></tr>`
                  : displayed.map((item) => {
                      const blockers = getPipelineBlockerKinds(item);
                      return html`
                        <tr key=${item.id}>
                          <td style="font-weight:600;cursor:pointer" onClick=${() => openItemDetail(navigate, item)}>${item.vendor_name || item.vendor || 'Unknown vendor'}</td>
                          <td style="font-family:var(--font-mono);font-size:12px">${item.invoice_number || '—'}</td>
                          <td style="text-align:right;font-family:var(--font-mono);font-variant-numeric:tabular-nums">${getAmountLabel(item)}</td>
                          <td>${item.due_date ? fmtDate(item.due_date) : '—'}</td>
                          <td><${StatePill} state=${item.state} /></td>
                          <td>
                            <div style="display:flex;gap:6px;flex-wrap:wrap">
                              ${blockers.length
                                ? blockers.slice(0, 2).map((kind) => html`<${BlockerChip} key=${kind} kind=${kind} />`)
                                : html`<span class="muted" style="font-size:12px">Clear</span>`}
                            </div>
                          </td>
                          <td class="muted" style="font-size:12px">${fmtDateTime(item.updated_at || item.created_at)}</td>
                          <td style="text-align:right">
                            <div style="display:flex;gap:8px;justify-content:flex-end">
                              <button class="alt" onClick=${() => openItemDetail(navigate, item)}>Detail</button>
                              <button class="alt" onClick=${() => openItemEmail(item)} disabled=${!item.thread_id && !item.message_id}>Email</button>
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
      Showing ${displayed.length} of ${items.length} invoices in ${PIPELINE_BUILTIN_SLICES.find((slice) => slice.id === viewPrefs.activeSliceId)?.label || 'this view'}.
    </div>
  `;
}
