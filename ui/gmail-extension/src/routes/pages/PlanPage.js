import { h } from 'preact';
import htm from 'htm';
import { hasCapability, useAction } from '../route-helpers.js';

const html = htm.bind(h);

export default function PlanPage({ bootstrap, api, toast, orgId, onRefresh }) {
  const sub = bootstrap?.subscription || {};
  const usage = sub.usage || {};
  const usageKeys = Object.keys(usage);
  const planName = (sub.plan || 'free').charAt(0).toUpperCase() + (sub.plan || 'free').slice(1);
  const canManagePlan = hasCapability(bootstrap, 'manage_plan');
  const [changePlan] = useAction(async (plan) => {
    if (!canManagePlan) return;
    await api('/api/workspace/subscription/plan', { method: 'PATCH', body: JSON.stringify({ organization_id: orgId, plan }) });
    toast(`Plan updated to ${plan}.`); onRefresh();
  });

  return html`
    <div class=${`secondary-banner ${canManagePlan ? '' : 'warning'}`}>
      <div class="secondary-banner-copy">
        <h3>${canManagePlan ? 'Plan and usage' : 'Plan details are visible here'}</h3>
        <p class="muted">${canManagePlan ? 'Check the current workspace plan, then change it if billing or usage has shifted.' : 'You can review plan details here, but only admins can change the workspace plan.'}</p>
      </div>
    </div>

    <div class="secondary-shell">
      <div class="secondary-main">
        <div class="panel">
          <h3 style="margin-top:0">Workspace plan</h3>
          <div style="display:flex;align-items:center;gap:12px;margin:12px 0 16px">
            <span style="font-size:28px;font-weight:700;letter-spacing:-0.02em">${planName}</span>
            <span class="status-badge connected">${sub.status || 'Active'}</span>
          </div>
          <div class="segmented-actions">
            ${['free', 'trial', 'pro', 'enterprise'].map((p) => html`
              <button
                class=${`segmented-button btn-sm ${sub.plan === p ? 'is-active' : ''}`}
                onClick=${() => changePlan(p)}
                disabled=${sub.plan === p || !canManagePlan}
              >
                ${p.charAt(0).toUpperCase() + p.slice(1)}
              </button>
            `)}
          </div>
        </div>
      </div>

      <div class="secondary-side">
        <div class="panel">
          <h3 style="margin-top:0">Usage this period</h3>
          ${usageKeys.length
            ? html`<div class="secondary-list" style="margin-top:12px">
                ${usageKeys.map((key) => html`
                  <div class="secondary-row">
                    <div class="secondary-row-copy">
                      <strong>${key.replace(/_/g, ' ')}</strong>
                    </div>
                    <strong>${typeof usage[key] === 'number' ? usage[key].toLocaleString() : usage[key]}</strong>
                  </div>
                `)}
              </div>`
            : html`<p class="secondary-empty">Usage data will appear here once invoices are processed.</p>`}
        </div>
      </div>
    </div>
  `;
}
