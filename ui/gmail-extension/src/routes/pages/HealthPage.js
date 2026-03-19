import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

export default function HealthPage({ bootstrap }) {
  const health = bootstrap?.health || {};
  const integrations = health.integrations || {};
  const actions = health.required_actions || [];

  return html`
    <div class="panel">
      <h3>${actions.length ? 'Action required' : 'All systems go'}</h3>
      <p class="muted">${actions.length ? 'Complete these items before going live.' : 'Everything looks good. Your system is ready.'}</p>
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
      <table class="table">
        <thead><tr><th>Service</th><th>Status</th></tr></thead>
        <tbody>
          ${Object.entries(integrations).map(([name, status]) => {
            const isOk = status === true || status === 'connected' || status?.connected === true;
            return html`<tr>
              <td style="font-weight:500">${name.charAt(0).toUpperCase() + name.slice(1)}</td>
              <td>${html`<span class="status-badge ${isOk ? 'connected' : ''}">${isOk ? 'Connected' : 'Not connected'}</span>`}</td>
            </tr>`;
          })}
          ${!Object.keys(integrations).length && html`<tr><td colspan="2" class="muted">No integration data yet.</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}
