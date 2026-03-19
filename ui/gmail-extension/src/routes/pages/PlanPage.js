import { h } from 'preact';
import htm from 'htm';
import { useAction } from '../route-helpers.js';

const html = htm.bind(h);

export default function PlanPage({ bootstrap, api, toast, orgId, onRefresh }) {
  const sub = bootstrap?.subscription || {};
  const usage = sub.usage || {};
  const usageKeys = Object.keys(usage);
  const planName = (sub.plan || 'free').charAt(0).toUpperCase() + (sub.plan || 'free').slice(1);
  const [changePlan] = useAction(async (plan) => {
    await api('/api/admin/subscription/plan', { method: 'PATCH', body: JSON.stringify({ organization_id: orgId, plan }) });
    toast(`Plan updated to ${plan}.`); onRefresh();
  });

  return html`
    <div class="panel">
      <h3>Your plan</h3>
      <div style="display:flex;align-items:center;gap:12px;margin:12px 0 16px">
        <span style="font-size:28px;font-weight:700;letter-spacing:-0.02em">${planName}</span>
        <span class="status-badge connected">${sub.status || 'Active'}</span>
      </div>
      <p class="muted">Use this page to review the current workspace plan. Commercial billing changes are handled with your Clearledgr admin contact.</p>
      <div class="row" style="margin-top:12px">
        ${['free', 'trial', 'pro', 'enterprise'].map(p => html`<button class=${sub.plan === p ? '' : 'alt'} onClick=${() => changePlan(p)} disabled=${sub.plan === p}>${p.charAt(0).toUpperCase() + p.slice(1)}</button>`)}
      </div>
    </div>
    <div class="panel"><h3>Usage this period</h3>
      ${usageKeys.length ? html`<div class="kpi-row">${usageKeys.map(k => html`<div class="kpi-card"><strong>${typeof usage[k] === 'number' ? usage[k].toLocaleString() : usage[k]}</strong><span>${k.replace(/_/g, ' ')}</span></div>`)}</div>` : html`<p class="muted">Usage data will appear here once invoices are processed.</p>`}
    </div>
  `;
}
