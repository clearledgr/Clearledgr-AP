import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

export default function HealthPage({ bootstrap }) {
  const health = bootstrap?.health || {};
  const integrations = health.integrations || {};
  const actions = health.required_actions || [];

  return html`
    <div class=${`secondary-banner ${actions.length ? 'warning' : ''}`}>
      <div class="secondary-banner-copy">
        <h3>${actions.length ? 'Something needs attention' : 'Everything looks healthy'}</h3>
        <p class="muted">${actions.length ? 'Check the broken connection or missing setup before it blocks work.' : 'Clearledgr is ready to keep working in Gmail.'}</p>
      </div>
    </div>

    <div class="secondary-shell">
      <div class="secondary-main">
        <div class="panel">
          <h3 style="margin-top:0">Required actions</h3>
          ${actions.length > 0
            ? html`<div class="secondary-list" style="margin-top:12px">
                ${actions.map((a, i) => html`
                  <div key=${i} class="secondary-note" style="border-left:3px solid var(--amber)">
                    ${a.message}
                  </div>
                `)}
              </div>`
            : html`<div class="secondary-empty">Nothing needs manual attention right now.</div>`}
        </div>
      </div>

      <div class="secondary-side">
        <div class="panel">
          <h3 style="margin-top:0">Connection status</h3>
          ${Object.keys(integrations).length
            ? html`<div class="secondary-list" style="margin-top:12px">
                ${Object.entries(integrations).map(([name, status]) => {
                  const isOk = status === true || status === 'connected' || status?.connected === true;
                  return html`<div class="secondary-row">
                    <div class="secondary-row-copy">
                      <strong>${name.charAt(0).toUpperCase() + name.slice(1)}</strong>
                    </div>
                    <span class="status-badge ${isOk ? 'connected' : ''}">${isOk ? 'Connected' : 'Not connected'}</span>
                  </div>`;
                })}
              </div>`
            : html`<div class="secondary-empty">No integration data yet.</div>`}
        </div>
      </div>
    </div>
  `;
}
