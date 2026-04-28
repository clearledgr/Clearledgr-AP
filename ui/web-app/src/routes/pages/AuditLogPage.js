/**
 * Audit Log Page — Module 7 v1 dashboard surface.
 *
 * Org-scoped, admin-gated audit search. Backed by:
 *   GET /api/workspace/audit/search?from_ts=&to_ts=&event_type=&actor_id=&box_type=&box_id=&limit=&cursor=
 *   GET /api/workspace/audit/event/{event_id}
 *   GET/POST/DELETE /api/workspace/webhooks (Pass 3 SIEM panel)
 *   POST /api/workspace/webhooks/{id}/test (test ping)
 *   GET /api/workspace/webhooks/{id}/deliveries (per-webhook attempt log)
 *
 * Append-only at the database level (Postgres triggers in
 * clearledgr/core/database.py:374). The dashboard is a pure read
 * surface — no mutations of audit events themselves.
 *
 * Pass 1 ships search + filter + pagination + detail panel.
 * Pass 2 ships async CSV export.
 * Pass 3 ships SIEM webhook config + delivery log.
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


// ─── SIEM webhook section (Pass 3) ──────────────────────────────────
// Curated event-type bundles a SIEM operator typically wants to forward
// to a downstream collector (Splunk, Datadog Cloud SIEM, Sumo, etc).
// "*" means every audit event — useful for compliance ingestion that
// can't pre-filter at the source. Operators can also paste a custom
// comma-separated list via the form's free-text field.
const SIEM_EVENT_BUNDLES = [
  { value: '*', label: 'All audit events (compliance feed)' },
  {
    value: 'state_transition,invoice_approved,invoice_rejected,erp_post_completed,erp_post_failed',
    label: 'AP workflow (states + decisions + ERP posts)',
  },
  {
    value: 'organization_renamed,organization_domain_changed,organization_integration_mode_changed',
    label: 'Org config changes',
  },
  {
    value: 'illegal_transition_blocked,invoice_reverse_blocked,invoice_snooze_blocked',
    label: 'Blocked actions (security / governance)',
  },
];

const DELIVERY_STATUS_LABELS = {
  success: { label: 'Delivered', cls: 'cl-audit-delivery-ok' },
  failed: { label: 'Failed', cls: 'cl-audit-delivery-fail' },
  retrying: { label: 'Retrying', cls: 'cl-audit-delivery-retry' },
};


function SiemWebhookForm({ onSubmit, onCancel, busy }) {
  const [url, setUrl] = useState('');
  const [secret, setSecret] = useState('');
  const [bundlePreset, setBundlePreset] = useState(SIEM_EVENT_BUNDLES[0].value);
  const [customEvents, setCustomEvents] = useState('');
  const [description, setDescription] = useState('');
  const [err, setErr] = useState(null);

  const submit = useCallback(async (e) => {
    e?.preventDefault?.();
    setErr(null);
    const trimmedUrl = url.trim();
    if (!trimmedUrl.startsWith('https://') && !trimmedUrl.startsWith('http://')) {
      setErr('URL must start with https:// (or http:// for local testing).');
      return;
    }
    const eventTypesString = customEvents.trim() || bundlePreset;
    const eventTypes = eventTypesString
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    if (eventTypes.length === 0) {
      setErr('Pick a bundle or enter at least one event type.');
      return;
    }
    try {
      await onSubmit({
        url: trimmedUrl,
        secret: secret.trim(),
        event_types: eventTypes,
        description: description.trim(),
      });
    } catch (exc) {
      setErr(String(exc?.message || exc));
    }
  }, [url, secret, bundlePreset, customEvents, description, onSubmit]);

  return html`
    <form class="cl-siem-form" onSubmit=${submit}>
      <label class="cl-audit-filter-field">
        <span>Endpoint URL</span>
        <input
          type="url"
          placeholder="https://siem.example.com/clearledgr/ingest"
          value=${url}
          onInput=${(e) => setUrl(e.target.value)}
          disabled=${busy}
          required />
      </label>
      <label class="cl-audit-filter-field">
        <span>HMAC secret (optional)</span>
        <input
          type="text"
          placeholder="leave blank for no signing"
          value=${secret}
          onInput=${(e) => setSecret(e.target.value)}
          disabled=${busy} />
      </label>
      <label class="cl-audit-filter-field">
        <span>Event bundle</span>
        <select
          value=${bundlePreset}
          onChange=${(e) => setBundlePreset(e.target.value)}
          disabled=${busy || customEvents.trim().length > 0}>
          ${SIEM_EVENT_BUNDLES.map((opt) => html`
            <option value=${opt.value}>${opt.label}</option>
          `)}
        </select>
      </label>
      <label class="cl-audit-filter-field cl-siem-form-wide">
        <span>Custom event types (comma-separated, overrides bundle)</span>
        <input
          type="text"
          placeholder="state_transition, plan_observed, ..."
          value=${customEvents}
          onInput=${(e) => setCustomEvents(e.target.value)}
          disabled=${busy} />
      </label>
      <label class="cl-audit-filter-field cl-siem-form-wide">
        <span>Description (optional)</span>
        <input
          type="text"
          placeholder="Splunk HEC — production"
          value=${description}
          onInput=${(e) => setDescription(e.target.value)}
          disabled=${busy} />
      </label>
      ${err ? html`<div class="cl-siem-form-error">${err}</div>` : null}
      <div class="cl-siem-form-actions">
        <button type="submit" class="btn btn-sm btn-primary" disabled=${busy}>
          ${busy ? 'Saving…' : 'Register webhook'}
        </button>
        <button type="button" class="btn btn-sm btn-tertiary" onClick=${onCancel} disabled=${busy}>
          Cancel
        </button>
      </div>
    </form>`;
}


function SiemDeliveriesPanel({ api, orgId, webhook, onClose }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [statusFilter, setStatusFilter] = useState('');

  const load = useCallback(async () => {
    if (!api || !orgId || !webhook?.id) return;
    setLoading(true);
    setErr(null);
    try {
      const params = new URLSearchParams();
      params.set('organization_id', orgId);
      params.set('limit', '50');
      if (statusFilter) params.set('status', statusFilter);
      const resp = await api(
        `/api/workspace/webhooks/${encodeURIComponent(webhook.id)}/deliveries?${params.toString()}`
      );
      setRows(Array.isArray(resp?.deliveries) ? resp.deliveries : []);
    } catch (exc) {
      setErr(String(exc?.message || exc));
    } finally {
      setLoading(false);
    }
  }, [api, orgId, webhook?.id, statusFilter]);

  useEffect(() => { load(); }, [load]);

  // Failures-last-24h counter — derived from the rows we already have.
  // For >50 attempts this is a lower bound (newest 50 only); we annotate
  // the UI accordingly so an SRE doesn't read it as canonical.
  const failuresLast24h = (() => {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    return rows.filter((r) => {
      if (r.status !== 'failed') return false;
      const ts = r.attempted_at ? new Date(r.attempted_at).getTime() : 0;
      return ts >= cutoff;
    }).length;
  })();

  return html`
    <section class="cl-siem-deliveries">
      <header class="cl-siem-deliveries-head">
        <div>
          <h4>Deliveries — <code>${webhook.url}</code></h4>
          <p class="muted">
            Latest 50 attempts (newest first). Retries appear as separate rows.
            ${rows.length === 50 ? ' Showing the most recent 50; older attempts may exist.' : ''}
          </p>
        </div>
        <div class="cl-siem-deliveries-actions">
          <label class="cl-audit-filter-field">
            <span>Status</span>
            <select
              value=${statusFilter}
              onChange=${(e) => setStatusFilter(e.target.value)}
              disabled=${loading}>
              <option value="">All</option>
              <option value="success">Delivered</option>
              <option value="retrying">Retrying</option>
              <option value="failed">Failed</option>
            </select>
          </label>
          <button class="btn btn-sm btn-tertiary" onClick=${load} disabled=${loading}>
            ${loading ? 'Loading…' : 'Refresh'}
          </button>
          <button class="btn btn-sm btn-tertiary" onClick=${onClose}>Close</button>
        </div>
      </header>

      <div class="cl-siem-deliveries-summary">
        <span class=${`cl-audit-chip ${failuresLast24h > 0 ? 'cl-audit-delivery-fail' : 'cl-audit-delivery-ok'}`}>
          ${failuresLast24h} failure${failuresLast24h === 1 ? '' : 's'} (last 24h, in window)
        </span>
        <span class="muted">${rows.length} attempt${rows.length === 1 ? '' : 's'} loaded.</span>
      </div>

      ${err ? html`<${ErrorRetry} message=${err} onRetry=${load} />` : null}
      ${loading && rows.length === 0 ? html`<${LoadingSkeleton} rows=${5} />` : null}
      ${!loading && !err && rows.length === 0 ? html`
        <${EmptyState}
          title="No delivery attempts yet"
          body="Deliveries appear here once matching audit events are emitted." />
      ` : null}
      ${rows.length > 0 ? html`
        <table class="cl-audit-table cl-siem-deliveries-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Event</th>
              <th>Attempt</th>
              <th>Status</th>
              <th>HTTP</th>
              <th>Duration</th>
              <th>Error / signature</th>
            </tr>
          </thead>
          <tbody>
            ${rows.map((d) => {
              const meta = DELIVERY_STATUS_LABELS[d.status] || { label: d.status, cls: '' };
              return html`
                <tr key=${d.id}>
                  <td class="cl-audit-cell-ts">${fmtDateTime(d.attempted_at)}</td>
                  <td>
                    <span class="cl-audit-event-name">${d.event_type || '—'}</span>
                    ${d.audit_event_id
                      ? html`<div class="cl-audit-box-id">${d.audit_event_id}</div>`
                      : null}
                  </td>
                  <td class="cl-audit-confidence">#${d.attempt_number ?? 1}</td>
                  <td>
                    <span class=${`cl-audit-chip ${meta.cls}`}>${meta.label}</span>
                  </td>
                  <td class="cl-audit-confidence">${d.http_status_code ?? '—'}</td>
                  <td class="cl-audit-confidence">${d.duration_ms != null ? `${d.duration_ms}ms` : '—'}</td>
                  <td class="cl-audit-cell-error">
                    ${d.error_message
                      ? html`<span class="cl-siem-error">${d.error_message}</span>`
                      : (d.request_signature_prefix
                          ? html`<code class="cl-audit-box-id">${d.request_signature_prefix}…</code>`
                          : html`<span class="muted">—</span>`)}
                  </td>
                </tr>`;
            })}
          </tbody>
        </table>
      ` : null}
    </section>`;
}


function SiemSection({ api, orgId, refreshTrigger }) {
  const [expanded, setExpanded] = useState(false);
  const [webhooks, setWebhooks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [activeWebhookId, setActiveWebhookId] = useState(null);
  const [testStatus, setTestStatus] = useState({}); // { [webhookId]: 'ok' | 'failed' | 'sending' }

  const load = useCallback(async () => {
    if (!api || !orgId) return;
    setLoading(true);
    setErr(null);
    try {
      const resp = await api(
        `/api/workspace/webhooks?organization_id=${encodeURIComponent(orgId)}&active_only=false`
      );
      setWebhooks(Array.isArray(resp?.webhooks) ? resp.webhooks : []);
    } catch (exc) {
      setErr(String(exc?.message || exc));
    } finally {
      setLoading(false);
    }
  }, [api, orgId]);

  // Initial load only when expanded — keeps the audit page snappy for
  // the common case (operator opening to search, not to manage SIEM).
  useEffect(() => {
    if (expanded) load();
  }, [expanded, load, refreshTrigger]);

  const handleCreate = useCallback(async (payload) => {
    setSubmitting(true);
    try {
      await api(
        `/api/workspace/webhooks?organization_id=${encodeURIComponent(orgId)}`,
        { method: 'POST', body: JSON.stringify(payload) }
      );
      setShowForm(false);
      await load();
    } finally {
      setSubmitting(false);
    }
  }, [api, orgId, load]);

  const handleDelete = useCallback(async (webhook) => {
    if (!window.confirm(`Delete webhook ${webhook.url}?\n\nDelivery history is preserved.`)) return;
    try {
      await api(`/api/workspace/webhooks/${encodeURIComponent(webhook.id)}`, { method: 'DELETE' });
      if (activeWebhookId === webhook.id) setActiveWebhookId(null);
      await load();
    } catch (exc) {
      setErr(String(exc?.message || exc));
    }
  }, [api, activeWebhookId, load]);

  const handleTest = useCallback(async (webhook) => {
    setTestStatus((s) => ({ ...s, [webhook.id]: 'sending' }));
    try {
      const resp = await api(
        `/api/workspace/webhooks/${encodeURIComponent(webhook.id)}/test`,
        { method: 'POST' }
      );
      setTestStatus((s) => ({ ...s, [webhook.id]: resp?.delivered ? 'ok' : 'failed' }));
    } catch (exc) {
      setTestStatus((s) => ({ ...s, [webhook.id]: 'failed' }));
    } finally {
      // Auto-clear the chip after 6s so it doesn't linger.
      setTimeout(() => setTestStatus((s) => {
        const next = { ...s };
        delete next[webhook.id];
        return next;
      }), 6000);
    }
  }, [api]);

  const activeWebhook = webhooks.find((w) => w.id === activeWebhookId);

  return html`
    <section class=${`cl-siem-section${expanded ? ' is-expanded' : ''}`}>
      <header class="cl-siem-head">
        <button
          type="button"
          class="cl-siem-toggle"
          onClick=${() => setExpanded((v) => !v)}
          aria-expanded=${expanded}>
          <span class="cl-siem-chevron">${expanded ? '▾' : '▸'}</span>
          <span class="cl-siem-title">SIEM webhooks</span>
          <span class="muted">
            ${expanded ? '' : (webhooks.length > 0
                ? `${webhooks.length} configured`
                : 'forward audit events to Splunk, Datadog, Sumo, etc.')}
          </span>
        </button>
        ${expanded ? html`
          <div class="cl-siem-head-actions">
            <button
              class="btn btn-sm btn-primary"
              onClick=${() => setShowForm((v) => !v)}
              disabled=${submitting}>
              ${showForm ? 'Hide form' : '+ Add webhook'}
            </button>
            <button class="btn btn-sm btn-tertiary" onClick=${load} disabled=${loading}>
              ${loading ? 'Loading…' : 'Refresh'}
            </button>
          </div>
        ` : null}
      </header>

      ${expanded ? html`
        <div class="cl-siem-body">
          ${showForm ? html`
            <${SiemWebhookForm}
              onSubmit=${handleCreate}
              onCancel=${() => setShowForm(false)}
              busy=${submitting} />
          ` : null}

          ${err ? html`<${ErrorRetry} message=${err} onRetry=${load} />` : null}
          ${loading && webhooks.length === 0 ? html`<${LoadingSkeleton} rows=${3} />` : null}
          ${!loading && !err && webhooks.length === 0 ? html`
            <${EmptyState}
              title="No SIEM webhooks configured"
              body="Add one to forward audit events to your security pipeline." />
          ` : null}

          ${webhooks.length > 0 ? html`
            <table class="cl-audit-table cl-siem-table">
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Events</th>
                  <th>Status</th>
                  <th>Description</th>
                  <th class="cl-siem-actions-col">Actions</th>
                </tr>
              </thead>
              <tbody>
                ${webhooks.map((w) => {
                  const eventTypes = Array.isArray(w.event_types) ? w.event_types : [];
                  const eventLabel = eventTypes.includes('*')
                    ? 'All events'
                    : (eventTypes.length <= 2 ? eventTypes.join(', ') : `${eventTypes.length} types`);
                  const status = testStatus[w.id];
                  return html`
                    <tr key=${w.id} class=${activeWebhookId === w.id ? 'is-active' : ''}>
                      <td><code class="cl-siem-url">${w.url}</code></td>
                      <td title=${eventTypes.join(', ')}>${eventLabel}</td>
                      <td>
                        <span class=${`cl-audit-chip ${w.is_active ? 'cl-audit-delivery-ok' : ''}`}>
                          ${w.is_active ? 'Active' : 'Inactive'}
                        </span>
                        ${status === 'sending' ? html`<span class="cl-audit-chip">Testing…</span>` : null}
                        ${status === 'ok' ? html`<span class="cl-audit-chip cl-audit-delivery-ok">Test OK</span>` : null}
                        ${status === 'failed' ? html`<span class="cl-audit-chip cl-audit-delivery-fail">Test failed</span>` : null}
                      </td>
                      <td class="cl-audit-cell-actor">${w.description || '—'}</td>
                      <td class="cl-siem-actions-col">
                        <button
                          class="btn btn-sm btn-tertiary"
                          onClick=${() => handleTest(w)}
                          disabled=${status === 'sending'}>
                          Test
                        </button>
                        <button
                          class="btn btn-sm btn-tertiary"
                          onClick=${() => setActiveWebhookId(activeWebhookId === w.id ? null : w.id)}>
                          ${activeWebhookId === w.id ? 'Hide log' : 'View log'}
                        </button>
                        <button
                          class="btn btn-sm btn-danger"
                          onClick=${() => handleDelete(w)}>
                          Delete
                        </button>
                      </td>
                    </tr>`;
                })}
              </tbody>
            </table>
          ` : null}

          ${activeWebhook ? html`
            <${SiemDeliveriesPanel}
              api=${api}
              orgId=${orgId}
              webhook=${activeWebhook}
              onClose=${() => setActiveWebhookId(null)} />
          ` : null}
        </div>
      ` : null}
    </section>`;
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

      <${SiemSection} api=${api} orgId=${orgId} />

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
