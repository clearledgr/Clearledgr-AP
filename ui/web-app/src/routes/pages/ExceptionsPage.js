import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';
import { formatAmount } from '../../utils/formatters.js';

const html = htm.bind(h);

const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };
const SEVERITY_COLORS = {
  critical: '#B91C1C',
  high: '#DC2626',
  medium: '#A16207',
  low: '#6B7280',
};

function humanizeExceptionType(code) {
  if (!code) return 'Exception raised';
  return String(code).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatTimeAgo(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const hours = Math.floor((Date.now() - d.getTime()) / 3600000);
    if (hours < 1) return 'just now';
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  } catch { return ''; }
}

function rowHeadline(row) {
  const summary = row.box_summary || {};
  const vendor = row.vendor_name || row.vendor || summary.vendor_name;
  const invoice = summary.invoice_number;
  if (vendor && invoice) return `${vendor} — ${invoice}`;
  if (vendor) return vendor;
  if (invoice) return invoice;
  // No enrichable signal — fall back to a short id rather than the
  // full UUID so the row stays scannable.
  const id = String(row.box_id || '');
  return id.length > 14 ? `${row.box_type || 'Box'} · ${id.slice(0, 14)}…` : `${row.box_type || 'Box'} · ${id}`;
}

export default function ExceptionsPage({ api }) {
  const [items, setItems] = useState(null);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const [resolvingId, setResolvingId] = useState(null);
  const [severityFilter, setSeverityFilter] = useState('');
  const [boxTypeFilter, setBoxTypeFilter] = useState('');
  const [resolveDialog, setResolveDialog] = useState(null); // { id, note } | null

  const load = useCallback(async () => {
    if (!api) return;
    try {
      const params = new URLSearchParams();
      if (severityFilter) params.set('severity', severityFilter);
      if (boxTypeFilter) params.set('box_type', boxTypeFilter);
      const query = params.toString();
      const [listRes, statsRes] = await Promise.all([
        api(`/api/admin/box/exceptions${query ? `?${query}` : ''}`),
        api('/api/admin/box/exceptions/stats'),
      ]);
      setItems(listRes?.items || []);
      setStats(statsRes || null);
      setError(null);
    } catch (exc) {
      setError(String(exc?.message || exc));
    }
  }, [api, severityFilter, boxTypeFilter]);

  useEffect(() => { load(); }, [load]);

  const openResolveDialog = (exceptionId) => {
    setResolveDialog({ id: exceptionId, note: '' });
  };

  const cancelResolveDialog = () => setResolveDialog(null);

  const submitResolveDialog = async () => {
    if (!api || !resolveDialog?.id) return;
    const exceptionId = resolveDialog.id;
    const note = String(resolveDialog.note || '').trim();
    setResolveDialog(null);
    setResolvingId(exceptionId);
    try {
      await api(`/api/admin/box/exceptions/${exceptionId}/resolve`, {
        method: 'POST',
        body: JSON.stringify({ resolution_note: note }),
        headers: { 'Content-Type': 'application/json' },
      });
      await load();
    } catch (exc) {
      setError(String(exc?.message || exc));
    } finally {
      setResolvingId(null);
    }
  };

  const sorted = (items || []).slice().sort((a, b) => {
    const sa = SEVERITY_ORDER[a.severity] ?? 99;
    const sb = SEVERITY_ORDER[b.severity] ?? 99;
    if (sa !== sb) return sa - sb;
    return String(a.raised_at || '').localeCompare(String(b.raised_at || ''));
  });

  return html`
    <div class="secondary-banner ${(stats?.total_unresolved || 0) > 0 ? 'warning' : ''}">
      <div class="secondary-banner-copy">
        <h3>${stats?.total_unresolved ? `${stats.total_unresolved} unresolved exception${stats.total_unresolved === 1 ? '' : 's'}` : 'No unresolved exceptions'}</h3>
        <p class="muted">${stats?.total_unresolved ? 'These Boxes need a human decision before the agent can move them forward.' : 'Every Box is moving through its lifecycle cleanly.'}</p>
      </div>
    </div>

    ${error ? html`<div class="secondary-note" style="border-left:3px solid var(--red);margin:12px 0">${error}</div>` : null}

    <div class="secondary-shell">
      <div class="secondary-main">
        <div class="panel">
          <div style="display:flex;gap:10px;align-items:center;margin-bottom:14px">
            <label class="muted" style="font-size:12px">Severity</label>
            <select value=${severityFilter} onChange=${(e) => setSeverityFilter(e.target.value)} style="padding:4px 6px">
              <option value="">all</option>
              <option value="critical">critical</option>
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
            </select>
            <label class="muted" style="font-size:12px">Box type</label>
            <select value=${boxTypeFilter} onChange=${(e) => setBoxTypeFilter(e.target.value)} style="padding:4px 6px">
              <option value="">all</option>
              <option value="ap_item">ap_item</option>
              <option value="vendor_onboarding_session">vendor_onboarding_session</option>
            </select>
          </div>
          ${items === null
            ? html`<div class="secondary-empty">Loading…</div>`
            : sorted.length === 0
              ? html`<div class="secondary-empty">No exceptions match the current filters.</div>`
              : html`<div class="secondary-list" style="margin-top:4px">
                  ${sorted.map((row) => {
                    const summary = row.box_summary || {};
                    const headline = rowHeadline(row);
                    const typeLabel = humanizeExceptionType(row.exception_type);
                    // Hide the reason if it's just the code repeated —
                    // backend falls back to ``reason = exception_code``
                    // when no human reason was supplied. Showing both
                    // is noise, so collapse the duplicate.
                    const reason = String(row.reason || '').trim();
                    const showReason = reason && reason.toLowerCase() !== String(row.exception_type || '').toLowerCase();
                    const amountLabel = (summary.amount != null && summary.amount !== '')
                      ? formatAmount(summary.amount, summary.currency)
                      : '';
                    const ageLabel = formatTimeAgo(row.raised_at);
                    const openRecord = () => {
                      if (row.synthetic) {
                        const v = row.metadata?.vendor_name;
                        if (v) window.location.hash = `#/vendors/${encodeURIComponent(v)}`;
                        return;
                      }
                      if (row.box_type === 'ap_item' && row.box_id) {
                        window.location.hash = `#/items/${encodeURIComponent(row.box_id)}`;
                      }
                    };
                    return html`
                    <div key=${row.id} class="secondary-row" style="flex-direction:column;align-items:stretch;gap:6px;border-left:3px solid ${SEVERITY_COLORS[row.severity] || '#6B7280'};padding:10px 12px;cursor:pointer"
                      onClick=${openRecord}>
                      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
                        <div style="min-width:0">
                          <strong style="font-size:14px">${headline}</strong>
                          <div class="muted" style="font-size:11px;margin-top:2px">${typeLabel}${amountLabel ? ` · ${amountLabel}` : ''}${ageLabel ? ` · ${ageLabel}` : ''}</div>
                        </div>
                        <div style="display:flex;gap:10px;align-items:center;flex-shrink:0" onClick=${(e) => e.stopPropagation()}>
                          <span class="status-badge" style="color:${SEVERITY_COLORS[row.severity] || '#6B7280'};font-weight:700">${row.severity}</span>
                          ${row.synthetic
                            ? html`<button
                                onClick=${() => {
                                  const v = row.metadata?.vendor_name;
                                  if (v) window.location.hash = `#/vendors/${encodeURIComponent(v)}`;
                                }}
                                class="btn-secondary btn-sm"
                                title="Resolve this signal by advancing the underlying onboarding session.">
                                View vendor
                              </button>`
                            : html`<button
                                disabled=${resolvingId === row.id}
                                onClick=${() => openResolveDialog(row.id)}
                                class="btn-primary btn-sm">
                                ${resolvingId === row.id ? 'Resolving…' : 'Resolve'}
                              </button>`}
                        </div>
                      </div>
                      ${showReason ? html`<div style="font-size:12px;line-height:1.4">${reason}</div>` : null}
                    </div>
                  `;
                  })}
                </div>`}
        </div>
      </div>

      <div class="secondary-side">
        <div class="panel">
          <h3 style="margin-top:0">By severity</h3>
          ${stats && stats.by_severity
            ? html`<div class="secondary-list" style="margin-top:10px">
                ${['critical', 'high', 'medium', 'low'].map((sev) => html`
                  <div key=${sev} class="secondary-row" style="justify-content:space-between">
                    <span style="color:${SEVERITY_COLORS[sev]};font-weight:600;text-transform:capitalize">${sev}</span>
                    <strong>${stats.by_severity[sev] || 0}</strong>
                  </div>
                `)}
              </div>`
            : html`<div class="secondary-empty">No data.</div>`}
        </div>

        <div class="panel">
          <h3 style="margin-top:0">By type</h3>
          ${stats && Object.keys(stats.by_type || {}).length
            ? html`<div class="secondary-list" style="margin-top:10px">
                ${Object.entries(stats.by_type).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([t, n]) => html`
                  <div key=${t} class="secondary-row" style="justify-content:space-between">
                    <span>${humanizeExceptionType(t)}</span>
                    <strong>${n}</strong>
                  </div>
                `)}
              </div>`
            : html`<div class="secondary-empty">No data.</div>`}
        </div>
      </div>
    </div>

    ${resolveDialog ? html`
      <div class="cl-modal-overlay" onClick=${cancelResolveDialog}>
        <div
          class="cl-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="cl-resolve-title"
          onClick=${(e) => e.stopPropagation()}
        >
          <h3 id="cl-resolve-title" class="cl-modal-title">Resolve exception</h3>
          <div class="cl-modal-body">
            Add an optional note explaining how this was resolved. The note is
            written to the audit log and visible to other admins.
          </div>
          <div class="field-row">
            <label for="cl-resolve-note">Resolution note (optional)</label>
            <textarea
              id="cl-resolve-note"
              autofocus
              value=${resolveDialog.note}
              onInput=${(e) => setResolveDialog((prev) => ({ ...prev, note: e.target.value }))}
              placeholder="e.g. Vendor confirmed the corrected IBAN; resubmitted invoice."
            ></textarea>
          </div>
          <div class="cl-modal-actions">
            <button class="btn-secondary btn-sm" onClick=${cancelResolveDialog}>Cancel</button>
            <button class="btn-primary btn-sm" onClick=${submitResolveDialog}>Resolve</button>
          </div>
        </div>
      </div>
    ` : null}
  `;
}
