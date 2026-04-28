/**
 * Audit Log Page — Module 7 v1 dashboard surface.
 *
 * Org-scoped, admin-gated audit search. Backed by:
 *   GET /api/workspace/audit/search?from_ts=&to_ts=&event_type=&actor_id=&box_type=&box_id=&limit=&cursor=
 *   GET /api/workspace/audit/event/{event_id}
 *
 * Append-only at the database level (Postgres triggers in
 * clearledgr/core/database.py:374). The dashboard is a pure read
 * surface — no mutations.
 *
 * Pass 1 ships search + filter + pagination + detail panel.
 * Pass 2 (separate commit) adds async CSV export.
 * Pass 3 (separate commit) adds SIEM webhook config + delivery log.
 */
import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';
import { fmtDateTime } from '../route-helpers.js';
import { EmptyState, ErrorRetry, LoadingSkeleton } from '../../components/StatePrimitives.js';

const html = htm.bind(h);

const PAGE_SIZE = 50;

// Curated event-type filter options. The full set of event_type tokens
// in audit_events is open-ended (any module can introduce new ones), so
// this list is a curated "common cases" picker — typing a custom value
// is supported via the free-text fallback.
const COMMON_EVENT_TYPES = [
  { value: '', label: 'All event types' },
  { value: 'state_transition', label: 'State transitions' },
  { value: 'invoice_approved,invoice_rejected', label: 'Approval decisions' },
  { value: 'erp_post_completed,erp_post_failed', label: 'ERP posts' },
  { value: 'organization_renamed,organization_domain_changed,organization_integration_mode_changed', label: 'Org config changes' },
  { value: 'plan_observed', label: 'Plan-observed (sync skill runs)' },
  { value: 'illegal_transition_blocked,invoice_reverse_blocked,invoice_snooze_blocked', label: 'Blocked actions' },
];

const COMMON_BOX_TYPES = [
  { value: '', label: 'All Box types' },
  { value: 'ap_item', label: 'AP item' },
  { value: 'organization', label: 'Organization' },
  { value: 'vendor_onboarding_session', label: 'Vendor onboarding' },
];


function FilterBar({ filters, setFilters, onApply, onReset, onExport, exportState, busy }) {
  const setField = (key, value) => setFilters({ ...filters, [key]: value });
  const exportLabel = (() => {
    if (!exportState) return 'Export CSV';
    switch (exportState.status) {
      case 'queued': return 'Queued…';
      case 'running': return 'Building…';
      case 'done': return 'Download CSV';
      case 'failed': return 'Export failed';
      default: return 'Export CSV';
    }
  })();
  const exportBusy = exportState && (exportState.status === 'queued' || exportState.status === 'running');

  return html`
    <div class="cl-audit-filters">
      <label class="cl-audit-filter-field">
        <span>From</span>
        <input
          type="datetime-local"
          value=${filters.from_ts}
          onChange=${(e) => setField('from_ts', e.target.value)}
          disabled=${busy} />
      </label>
      <label class="cl-audit-filter-field">
        <span>To</span>
        <input
          type="datetime-local"
          value=${filters.to_ts}
          onChange=${(e) => setField('to_ts', e.target.value)}
          disabled=${busy} />
      </label>
      <label class="cl-audit-filter-field">
        <span>Event type</span>
        <select
          value=${filters.event_type_preset}
          onChange=${(e) => setField('event_type_preset', e.target.value)}
          disabled=${busy}>
          ${COMMON_EVENT_TYPES.map((opt) => html`
            <option value=${opt.value}>${opt.label}</option>
          `)}
        </select>
      </label>
      <label class="cl-audit-filter-field">
        <span>Box type</span>
        <select
          value=${filters.box_type}
          onChange=${(e) => setField('box_type', e.target.value)}
          disabled=${busy}>
          ${COMMON_BOX_TYPES.map((opt) => html`
            <option value=${opt.value}>${opt.label}</option>
          `)}
        </select>
      </label>
      <label class="cl-audit-filter-field">
        <span>Actor (email)</span>
        <input
          type="text"
          placeholder="user@example.com"
          value=${filters.actor_id}
          onInput=${(e) => setField('actor_id', e.target.value)}
          disabled=${busy} />
      </label>
      <label class="cl-audit-filter-field">
        <span>Box ID</span>
        <input
          type="text"
          placeholder="ap-12345"
          value=${filters.box_id}
          onInput=${(e) => setField('box_id', e.target.value)}
          disabled=${busy} />
      </label>
      <div class="cl-audit-filter-actions">
        <button class="btn btn-sm btn-primary" onClick=${onApply} disabled=${busy}>
          ${busy ? 'Searching…' : 'Search'}
        </button>
        <button
          class=${`btn btn-sm ${exportState?.status === 'failed' ? 'btn-danger' : 'btn-secondary'}`}
          onClick=${onExport}
          disabled=${busy || exportBusy}
          title=${exportState?.status === 'failed' && exportState?.error_message
            ? exportState.error_message
            : 'Download the current filter set as CSV'}>
          ${exportLabel}
        </button>
        <button class="btn btn-sm btn-tertiary" onClick=${onReset} disabled=${busy}>
          Reset
        </button>
      </div>
    </div>`;
}


function EventRow({ event, isActive, onSelect }) {
  const ts = fmtDateTime(event.ts);
  const summary = event.event_type || 'audit_event';
  const actor = event.actor_id || event.actor_type || 'system';
  // Governance verdict + agent_confidence pulled from migration v50
  // columns. When present they're the harness's reasoning trail —
  // surfaced as a chip so the leader can spot vetoed actions in the
  // table without opening detail.
  const verdict = event.governance_verdict;
  const confidence = event.agent_confidence;

  return html`
    <tr class=${`cl-audit-row${isActive ? ' is-active' : ''}`} onClick=${() => onSelect(event)}>
      <td class="cl-audit-cell-ts">${ts}</td>
      <td class="cl-audit-cell-event">
        <span class="cl-audit-event-name">${summary}</span>
        ${verdict
          ? html`<span class=${`cl-audit-chip cl-audit-verdict-${verdict}`}>${verdict}</span>`
          : null}
      </td>
      <td class="cl-audit-cell-actor">${actor}</td>
      <td class="cl-audit-cell-box">
        <span class="cl-audit-box-type">${event.box_type || '—'}</span>
        <span class="cl-audit-box-id">${event.box_id || ''}</span>
      </td>
      <td class="cl-audit-cell-state">
        ${event.prev_state || event.new_state
          ? html`<span class="cl-audit-state-pair">
              <span>${event.prev_state || '—'}</span>
              <span class="cl-audit-state-arrow">→</span>
              <span>${event.new_state || '—'}</span>
            </span>`
          : null}
      </td>
      <td class="cl-audit-cell-confidence">
        ${typeof confidence === 'number'
          ? html`<span class="cl-audit-confidence">${(confidence * 100).toFixed(0)}%</span>`
          : null}
      </td>
    </tr>`;
}


function DetailPanel({ event, onClose, api, orgId }) {
  const [full, setFull] = useState(event);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  // Re-fetch the canonical detail when the row opens — the search
  // response already includes the full row, but a dedicated GET keeps
  // the URL bookmarkable and makes payload_json reliably present.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!event?.id) return;
      setLoading(true);
      setErr(null);
      try {
        const resp = await api(
          `/api/workspace/audit/event/${encodeURIComponent(event.id)}?organization_id=${encodeURIComponent(orgId)}`
        );
        if (!cancelled) setFull(resp?.event || event);
      } catch (exc) {
        if (!cancelled) setErr(String(exc?.message || exc));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [event?.id, api, orgId]);

  const payload = full?.payload_json || {};
  const payloadJson = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2);
  const externalRefs = full?.external_refs;
  const externalRefsJson = externalRefs
    ? (typeof externalRefs === 'string' ? externalRefs : JSON.stringify(externalRefs, null, 2))
    : null;

  return html`
    <aside class="cl-audit-detail">
      <div class="cl-audit-detail-head">
        <h3>${full?.event_type || 'audit_event'}</h3>
        <button class="btn btn-sm btn-tertiary" onClick=${onClose} aria-label="Close detail">Close</button>
      </div>
      ${err ? html`<${ErrorRetry} message=${err} onRetry=${() => setFull(event)} />` : null}
      ${loading ? html`<${LoadingSkeleton} rows=${4} />` : null}
      ${!loading && !err ? html`
        <dl class="cl-audit-detail-grid">
          <dt>Event ID</dt><dd><code>${full?.id}</code></dd>
          <dt>Timestamp</dt><dd>${fmtDateTime(full?.ts)}</dd>
          <dt>Box</dt><dd><code>${full?.box_type}/${full?.box_id}</code></dd>
          <dt>Actor</dt><dd>${full?.actor_id || full?.actor_type || '—'}</dd>
          ${full?.prev_state || full?.new_state ? html`
            <dt>State</dt>
            <dd>${full.prev_state || '—'} → ${full.new_state || '—'}</dd>
          ` : null}
          ${full?.governance_verdict ? html`
            <dt>Governance verdict</dt>
            <dd><span class=${`cl-audit-chip cl-audit-verdict-${full.governance_verdict}`}>${full.governance_verdict}</span></dd>
          ` : null}
          ${typeof full?.agent_confidence === 'number' ? html`
            <dt>Agent confidence</dt>
            <dd>${(full.agent_confidence * 100).toFixed(1)}%</dd>
          ` : null}
          ${full?.decision_reason ? html`
            <dt>Decision reason</dt><dd>${full.decision_reason}</dd>
          ` : null}
          ${full?.correlation_id ? html`
            <dt>Correlation</dt><dd><code>${full.correlation_id}</code></dd>
          ` : null}
          ${full?.source ? html`
            <dt>Source</dt><dd>${full.source}</dd>
          ` : null}
        </dl>
        ${payload && Object.keys(payload).length > 0 ? html`
          <details class="cl-audit-detail-payload" open>
            <summary>Payload</summary>
            <pre><code>${payloadJson}</code></pre>
          </details>
        ` : null}
        ${externalRefsJson && externalRefsJson !== '{}' ? html`
          <details class="cl-audit-detail-payload">
            <summary>External refs</summary>
            <pre><code>${externalRefsJson}</code></pre>
          </details>
        ` : null}
      ` : null}
    </aside>`;
}


export default function AuditLogPage({ api, orgId, bootstrap }) {
  // Filter state. ``event_type_preset`` is the dropdown value (which
  // is a comma-separated string per COMMON_EVENT_TYPES); the API
  // accepts ``event_type=a,b,c`` directly so we forward verbatim.
  const [filters, setFilters] = useState({
    from_ts: '',
    to_ts: '',
    event_type_preset: '',
    box_type: '',
    actor_id: '',
    box_id: '',
  });
  const [pages, setPages] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [selected, setSelected] = useState(null);
  // Export state machine: null (idle) | {job_id, status, error_message?}.
  // The poll loop below rewrites this whenever the server status
  // changes; the FilterBar's button reads it to render the right
  // label (Queued… / Building… / Download CSV / Export failed).
  const [exportState, setExportState] = useState(null);

  const buildQuery = useCallback((cur) => {
    const params = new URLSearchParams();
    params.set('organization_id', orgId);
    params.set('limit', String(PAGE_SIZE));
    if (filters.from_ts) {
      // datetime-local emits "YYYY-MM-DDTHH:mm" with no timezone; pin
      // it to the user's local time by appending the browser's offset.
      params.set('from_ts', new Date(filters.from_ts).toISOString());
    }
    if (filters.to_ts) {
      params.set('to_ts', new Date(filters.to_ts).toISOString());
    }
    if (filters.event_type_preset) {
      params.set('event_type', filters.event_type_preset);
    }
    if (filters.actor_id) params.set('actor_id', filters.actor_id.trim());
    if (filters.box_type) params.set('box_type', filters.box_type);
    if (filters.box_id) params.set('box_id', filters.box_id.trim());
    if (cur) params.set('cursor', cur);
    return params.toString();
  }, [filters, orgId]);

  const fetchPage = useCallback(async ({ append = false, useCursor = null } = {}) => {
    if (!api || !orgId) return;
    setLoading(true);
    setErr(null);
    try {
      const resp = await api(`/api/workspace/audit/search?${buildQuery(useCursor)}`);
      const events = Array.isArray(resp?.events) ? resp.events : [];
      setPages((prev) => append ? [...prev, ...events] : events);
      setCursor(resp?.next_cursor || null);
    } catch (exc) {
      setErr(String(exc?.message || exc));
    } finally {
      setLoading(false);
    }
  }, [api, orgId, buildQuery]);

  const onApply = useCallback(() => {
    setSelected(null);
    setCursor(null);
    fetchPage({ append: false, useCursor: null });
  }, [fetchPage]);

  const onReset = useCallback(() => {
    setFilters({
      from_ts: '',
      to_ts: '',
      event_type_preset: '',
      box_type: '',
      actor_id: '',
      box_id: '',
    });
    setSelected(null);
    setCursor(null);
  }, []);

  const onLoadMore = useCallback(() => {
    if (!cursor || loading) return;
    fetchPage({ append: true, useCursor: cursor });
  }, [cursor, loading, fetchPage]);

  // Initial load on mount.
  useEffect(() => {
    fetchPage({ append: false });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Export flow ────────────────────────────────────────────────
  // Click → POST starts a job. While the job runs, poll status every
  // 2s. When status flips to 'done', the next button click (label
  // becomes "Download CSV") triggers a fresh GET ?download=true that
  // the browser handles as a file download via Content-Disposition.
  const onExport = useCallback(async () => {
    if (!api || !orgId) return;

    // Already done? Trigger the download.
    if (exportState && exportState.status === 'done' && exportState.job_id) {
      const url = `/api/workspace/audit/exports/${encodeURIComponent(exportState.job_id)}?organization_id=${encodeURIComponent(orgId)}&download=true`;
      // Same-origin fetch + manual blob handoff so cookies + the
      // download attribute work uniformly. Plain <a href> would
      // navigate; we want a download trigger.
      try {
        const resp = await fetch(url, { credentials: 'same-origin' });
        if (!resp.ok) {
          throw new Error(`download failed: ${resp.status}`);
        }
        const blob = await resp.blob();
        const objUrl = URL.createObjectURL(blob);
        const filename = exportState.content_filename || `audit-${orgId}-${Date.now()}.csv`;
        const a = document.createElement('a');
        a.href = objUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(objUrl);
      } catch (exc) {
        setExportState({ ...exportState, status: 'failed', error_message: String(exc?.message || exc) });
      }
      return;
    }

    // Otherwise kick off a new job.
    const filtersPayload = {
      organization_id: orgId,
      from_ts: filters.from_ts ? new Date(filters.from_ts).toISOString() : null,
      to_ts: filters.to_ts ? new Date(filters.to_ts).toISOString() : null,
      event_types: filters.event_type_preset
        ? filters.event_type_preset.split(',').map((s) => s.trim()).filter(Boolean)
        : null,
      actor_id: filters.actor_id?.trim() || null,
      box_type: filters.box_type || null,
      box_id: filters.box_id?.trim() || null,
    };
    try {
      const resp = await api('/api/workspace/audit/export', {
        method: 'POST',
        body: JSON.stringify(filtersPayload),
      });
      setExportState({
        job_id: resp.job_id,
        status: resp.status || 'queued',
      });
    } catch (exc) {
      setExportState({ status: 'failed', error_message: String(exc?.message || exc) });
    }
  }, [api, orgId, exportState, filters]);

  // Poll loop. Runs while the export is queued/running; cleans up
  // the timer on every state change so a finished/failed job stops
  // hammering the server. No polling = no timer.
  useEffect(() => {
    if (!exportState || !exportState.job_id) return undefined;
    if (exportState.status !== 'queued' && exportState.status !== 'running') return undefined;

    let cancelled = false;
    const tick = async () => {
      try {
        const resp = await api(
          `/api/workspace/audit/exports/${encodeURIComponent(exportState.job_id)}?organization_id=${encodeURIComponent(orgId)}`
        );
        if (cancelled) return;
        setExportState((prev) => {
          if (!prev || prev.job_id !== exportState.job_id) return prev;
          return { ...prev, ...resp };
        });
      } catch (exc) {
        if (cancelled) return;
        setExportState((prev) => prev && prev.job_id === exportState.job_id
          ? { ...prev, status: 'failed', error_message: String(exc?.message || exc) }
          : prev,
        );
      }
    };
    const id = setInterval(tick, 2000);
    // Fire once immediately so a fast 'done' flip isn't waiting 2s.
    tick();
    return () => { cancelled = true; clearInterval(id); };
  }, [api, orgId, exportState?.job_id, exportState?.status]);

  const empty = !loading && !err && pages.length === 0;

  return html`
    <div class="cl-audit-page">
      <div class="secondary-banner">
        <div class="secondary-banner-copy">
          <h3>Audit log</h3>
          <p class="muted">
            Append-only record of every workflow action. Search, filter, and inspect.
          </p>
        </div>
      </div>

      <${FilterBar}
        filters=${filters}
        setFilters=${setFilters}
        onApply=${onApply}
        onReset=${onReset}
        onExport=${onExport}
        exportState=${exportState}
        busy=${loading} />

      <div class=${`cl-audit-layout${selected ? ' has-detail' : ''}`}>
        <div class="cl-audit-table-wrap">
          ${err ? html`<${ErrorRetry} message=${err} onRetry=${onApply} />` : null}
          ${loading && pages.length === 0 ? html`<${LoadingSkeleton} rows=${10} />` : null}
          ${empty ? html`
            <${EmptyState}
              title="No matching events"
              body="Adjust filters or expand the date range." />
          ` : null}
          ${pages.length > 0 ? html`
            <table class="cl-audit-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Event</th>
                  <th>Actor</th>
                  <th>Box</th>
                  <th>State</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                ${pages.map((event) => html`
                  <${EventRow}
                    key=${event.id}
                    event=${event}
                    isActive=${selected?.id === event.id}
                    onSelect=${setSelected} />
                `)}
              </tbody>
            </table>
            <div class="cl-audit-pagination">
              <span class="muted">${pages.length} event${pages.length === 1 ? '' : 's'} loaded.</span>
              ${cursor ? html`
                <button class="btn btn-sm btn-tertiary" onClick=${onLoadMore} disabled=${loading}>
                  ${loading ? 'Loading…' : 'Load more'}
                </button>
              ` : html`<span class="muted">End of results.</span>`}
            </div>
          ` : null}
        </div>
        ${selected ? html`
          <${DetailPanel}
            event=${selected}
            api=${api}
            orgId=${orgId}
            onClose=${() => setSelected(null)} />
        ` : null}
      </div>
    </div>`;
}
