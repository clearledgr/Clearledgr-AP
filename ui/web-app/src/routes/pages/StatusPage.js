import { useEffect, useState } from 'preact/hooks';
import { html } from '../../utils/htm.js';
import { api } from '../../api/client.js';

/**
 * /status — internal status page. Polls the api's /health endpoint
 * every 30s and renders the per-component check results
 * (database / metrics / event_queue) plus the runtime profile. First
 * thing enterprise security teams ask to see; also serves as a
 * customer-facing reassurance during incidents.
 *
 * If we later move to a third-party status service (Statuspage.io,
 * Atlassian, Better Uptime) this page swaps to embed that, but the
 * URL stays /status so external links stay stable.
 */

const COMPONENT_LABELS = {
  database: 'Database (Postgres)',
  metrics_backend: 'Metrics backend',
  event_queue: 'Event queue (Redis)',
};

function statusToTone(s) {
  const v = String(s || '').toLowerCase();
  if (['healthy', 'ok', 'ready'].includes(v)) return 'ok';
  if (['degraded', 'warning'].includes(v)) return 'warn';
  return 'down';
}

export function StatusPage() {
  const [state, setState] = useState({ status: 'loading', data: null, error: null, ts: null });

  useEffect(() => {
    let cancelled = false;
    const fetchHealth = async () => {
      try {
        const data = await api('/health', { retry: false });
        if (cancelled) return;
        setState({ status: 'ready', data, error: null, ts: new Date() });
      } catch (err) {
        if (cancelled) return;
        setState({ status: 'error', data: null, error: err?.message || 'unreachable', ts: new Date() });
      }
    };
    fetchHealth();
    const interval = setInterval(fetchHealth, 30_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const data = state.data;
  const overall = data?.status || (state.status === 'error' ? 'down' : '...');
  const overallTone = statusToTone(overall);
  const checks = data?.checks || {};
  const components = Object.keys(checks);

  return html`
    <div class="cl-status">
      <header class="cl-status-header">
        <div class="cl-status-eyebrow">Operations</div>
        <h1 class="cl-status-title">Workspace status</h1>
        <div class=${`cl-status-overall cl-status-overall-${overallTone}`}>
          <span class="cl-status-dot" aria-hidden="true"></span>
          <span>${overall === 'healthy' ? 'All systems operational' : overall === 'down' ? 'Service unreachable' : 'Status: ' + overall}</span>
        </div>
        <p class="cl-status-meta">
          ${state.ts ? `Last checked ${state.ts.toLocaleTimeString()}` : 'Checking…'} · auto-refreshes every 30s
        </p>
      </header>

      <section class="cl-status-components">
        <h2>Components</h2>
        ${components.length === 0 && state.status !== 'loading'
          ? html`<div class="cl-status-empty">No component data available.</div>`
          : html`
              <ul class="cl-status-list">
                ${components.map((key) => {
                  const c = checks[key] || {};
                  const tone = statusToTone(c.status);
                  return html`
                    <li class=${`cl-status-row cl-status-row-${tone}`} key=${key}>
                      <div class="cl-status-row-main">
                        <span class="cl-status-dot" aria-hidden="true"></span>
                        <span class="cl-status-row-label">${COMPONENT_LABELS[key] || key}</span>
                      </div>
                      <div class="cl-status-row-state">${c.status || '—'}</div>
                    </li>
                  `;
                })}
              </ul>
            `}
      </section>

      ${data?.runtime_surface_contract ? html`
        <section class="cl-status-runtime">
          <h2>Runtime profile</h2>
          <dl class="cl-status-dl">
            <dt>Environment</dt><dd>${data.runtime_surface_contract.environment || '—'}</dd>
            <dt>Profile</dt><dd>${data.runtime_surface_contract.profile || '—'}</dd>
            <dt>Process role</dt><dd>${data.runtime_surface_contract.process_role || '—'}</dd>
            <dt>Strict mode</dt><dd>${data.runtime_surface_contract.strict_effective ? 'Yes' : 'No'}</dd>
          </dl>
        </section>
      ` : null}

      <section class="cl-status-incident">
        <h2>Incidents</h2>
        <p class="cl-status-empty">
          No active or recent incidents. Subscribe at
          <a href="mailto:hello@clearledgr.com?subject=Status%20notifications">hello@clearledgr.com</a>
          to be notified about scheduled maintenance and incidents.
        </p>
      </section>
    </div>
  `;
}
