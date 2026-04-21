import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };
const SEVERITY_COLORS = {
  critical: '#B91C1C',
  high: '#DC2626',
  medium: '#A16207',
  low: '#6B7280',
};

export default function ExceptionsPage({ api }) {
  const [items, setItems] = useState(null);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const [resolvingId, setResolvingId] = useState(null);
  const [severityFilter, setSeverityFilter] = useState('');
  const [boxTypeFilter, setBoxTypeFilter] = useState('');

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

  const onResolve = async (exceptionId) => {
    if (!api) return;
    const note = window.prompt('Resolution note (optional):') || '';
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
                  ${sorted.map((row) => html`
                    <div key=${row.id} class="secondary-row" style="flex-direction:column;align-items:stretch;gap:6px;border-left:3px solid ${SEVERITY_COLORS[row.severity] || '#6B7280'};padding:10px 12px">
                      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
                        <div>
                          <strong>${row.exception_type}</strong>
                          <span class="muted" style="margin-left:8px;font-size:11px">${row.box_type} · ${row.box_id}</span>
                        </div>
                        <div style="display:flex;gap:10px;align-items:center">
                          <span class="status-badge" style="color:${SEVERITY_COLORS[row.severity] || '#6B7280'};font-weight:700">${row.severity}</span>
                          <button
                            disabled=${resolvingId === row.id}
                            onClick=${() => onResolve(row.id)}
                            class="cl-btn cl-btn-primary"
                            style="padding:4px 10px;font-size:12px"
                          >
                            ${resolvingId === row.id ? 'Resolving…' : 'Resolve'}
                          </button>
                        </div>
                      </div>
                      <div style="font-size:12px;line-height:1.4">${row.reason || '(no reason recorded)'}</div>
                      <div class="muted" style="font-size:11px">raised ${row.raised_at || 'unknown'} · by ${row.raised_by || 'system'}</div>
                    </div>
                  `)}
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
                    <span>${t}</span>
                    <strong>${n}</strong>
                  </div>
                `)}
              </div>`
            : html`<div class="secondary-empty">No data.</div>`}
        </div>
      </div>
    </div>
  `;
}
