import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

export default function HealthPage({ bootstrap, api }) {
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
        <${MonitoringPanel} api=${api} />
      </div>
    </div>
  `;
}

function MonitoringPanel({ api }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    if (!api) return;
    api.fetch('/api/ops/monitoring-health').then(setData).catch(() => {});
  }, []);
  if (!data) return null;
  return html`
    <div class="panel">
      <h3 style="margin-top:0">System monitoring</h3>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <span class="status-badge ${data.healthy ? 'connected' : ''}">${data.healthy ? 'Healthy' : `${data.alert_count} alert${data.alert_count !== 1 ? 's' : ''}`}</span>
        <span class="muted" style="font-size:11px">${data.check_count} checks run</span>
      </div>
      ${(data.checks || []).map((check) => html`
        <div key=${check.check} style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);font-size:12px">
          <div>
            <div style="font-weight:${check.alert ? '700' : '400'};color:${check.alert ? (check.severity === 'critical' ? '#B91C1C' : '#A16207') : 'inherit'}">
              ${check.check.replace(/_/g, ' ')}
            </div>
          </div>
          <div style="font-weight:600">${check.value}${typeof check.threshold === 'number' ? ` / ${check.threshold}` : ''}</div>
        </div>
      `)}
    </div>
  `;
}
