/**
 * Activity Page — secondary AP support surface.
 * Keeps recent finance activity reachable without turning Gmail into a KPI dashboard.
 */
import { h } from 'preact';
import htm from 'htm';
import { eventBadge, fmtDateTime, useAction } from '../route-helpers.js';

const html = htm.bind(h);

function SnapshotCard({ label, value, tone = 'neutral' }) {
  const styles = {
    neutral: 'background:var(--surface);border:1px solid var(--border);color:var(--ink);',
    warning: 'background:#FFFBEB;border:1px solid #FCD34D;color:#92400E;',
    success: 'background:#ECFDF5;border:1px solid #A7F3D0;color:#065F46;',
    danger: 'background:#FEF2F2;border:1px solid #FECACA;color:#991B1B;',
  };
  return html`<div style="padding:14px 16px;border-radius:var(--radius-md);${styles[tone] || styles.neutral}">
    <div style="font-size:12px;font-weight:600;opacity:0.8">${label}</div>
    <div style="margin-top:4px;font-size:24px;font-weight:700;letter-spacing:-0.02em">${value}</div>
  </div>`;
}

export default function ActivityPage({ bootstrap, onRefresh }) {
  const dash = bootstrap?.dashboard || {};
  const events = Array.isArray(bootstrap?.recentActivity) ? bootstrap.recentActivity.slice(0, 12) : [];
  const [refresh, refreshing] = useAction(onRefresh);

  return html`
    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <div>
          <h3 style="margin:0 0 6px">AP activity</h3>
          <p class="muted" style="margin:0">Use this page to check recent movement and return to work quickly. Pipeline remains the main queue surface.</p>
        </div>
        <button class="alt" onClick=${refresh} disabled=${refreshing}>${refreshing ? 'Refreshing…' : 'Refresh'}</button>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px">
      <${SnapshotCard} label="Processed" value=${Number(dash.total_invoices || 0).toLocaleString()} />
      <${SnapshotCard} label="Awaiting approval" value=${Number(dash.pending_approval || 0).toLocaleString()} tone="warning" />
      <${SnapshotCard} label="Posted today" value=${Number(dash.posted_today || 0).toLocaleString()} tone="success" />
      <${SnapshotCard} label="Rejected today" value=${Number(dash.rejected_today || 0).toLocaleString()} tone="danger" />
    </div>

    <div class="panel">
      <h3 style="margin-top:0">Recent finance activity</h3>
      <p class="muted" style="margin-top:0">Recent events across approval, posting, and exception handling.</p>
      ${events.length === 0
        ? html`<p class="muted" style="margin:0">No recent activity yet.</p>`
        : html`<div style="display:flex;flex-direction:column;gap:10px">
            ${events.map((event, index) => {
              const badge = eventBadge(event.event_type || event.new_state || 'activity');
              const title = String(event.title || event.summary || badge.label || 'Activity recorded').trim() || 'Activity recorded';
              const subtitle = String(event.detail || event.message || '').trim();
              return html`
                <div key=${event.id || index} style="padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
                  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
                    <div style="min-width:0">
                      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                        <span class=${`status-badge ${badge.cls || ''}`}>${badge.label}</span>
                        <strong style="font-size:13px">${title}</strong>
                      </div>
                      ${subtitle && html`<div class="muted" style="margin-top:6px;font-size:12px;line-height:1.5">${subtitle}</div>`}
                    </div>
                    <span class="muted" style="font-size:12px;white-space:nowrap">${fmtDateTime(event.ts || event.timestamp || event.created_at)}</span>
                  </div>
                </div>
              `;
            })}
          </div>`}
    </div>

    <div class="panel">
      <h3 style="margin-top:0">Attention now</h3>
      <div style="display:flex;flex-direction:column;gap:10px">
        <div class="readiness-item">Invoices awaiting approval: <strong>${Number(dash.pending_approval || 0).toLocaleString()}</strong></div>
        <div class="readiness-item">Posted value today: <strong>${fmtDollar(dash.total_amount_posted_today)}</strong></div>
        <div class="readiness-item">Pending value: <strong>${fmtDollar(dash.total_amount_pending)}</strong></div>
      </div>
    </div>
  `;
}
