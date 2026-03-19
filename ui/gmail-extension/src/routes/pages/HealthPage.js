import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

export default function HealthPage({ bootstrap }) {
  const health = bootstrap?.health || {};
  const integrations = health.integrations || {};
  const actions = health.required_actions || [];

  return html`
    <div class="panel">
      <h3>${actions.length ? 'Admin follow-up required' : 'No active workspace issues'}</h3>
      <p class="muted">${actions.length ? 'Use this page to resolve blockers, then leave it. Pipeline and the thread card remain the daily work surfaces.' : 'Everything looks healthy enough to keep AP work inside Gmail.'}</p>
      ${actions.length > 0 && html`
        <div style="display:flex;flex-direction:column;gap:10px;margin-top:16px">
          ${actions.map((a, i) => html`
            <div key=${i} class="readiness-item" style="border-left:3px solid var(--amber)">
              ${a.message}
            </div>
          `)}
        </div>
      `}
    </div>
    <div class="panel">
      <h3>Connection status</h3>
      ${Object.keys(integrations).length
        ? html`<div style="display:grid;gap:10px">
            ${Object.entries(integrations).map(([name, status]) => {
              const isOk = status === true || status === 'connected' || status?.connected === true;
              return html`<div class="readiness-item" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
                <strong>${name.charAt(0).toUpperCase() + name.slice(1)}</strong>
                <span class="status-badge ${isOk ? 'connected' : ''}">${isOk ? 'Connected' : 'Not connected'}</span>
              </div>`;
            })}
          </div>`
        : html`<div class="muted">No integration data yet.</div>`}
    </div>
  `;
}
