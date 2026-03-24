/**
 * Activity Page — secondary AP support surface.
 * Keeps recent finance activity reachable without turning Gmail into a KPI dashboard.
 */
import { h } from 'preact';
import htm from 'htm';
import { eventBadge, fmtDateTime, useAction } from '../route-helpers.js';

const html = htm.bind(h);

export default function ActivityPage({ bootstrap, onRefresh, navigate }) {
  const dash = bootstrap?.dashboard || {};
  const events = Array.isArray(bootstrap?.recentActivity) ? bootstrap.recentActivity.slice(0, 12) : [];
  const [refresh, refreshing] = useAction(onRefresh);

  return html`
    <div class="secondary-banner">
      <div class="secondary-banner-copy">
        <h3>Recent activity</h3>
        <p class="muted">See what changed recently, then jump back into the queue when you need to act.</p>
      </div>
      <div class="secondary-banner-actions">
        <button class="btn-secondary btn-sm" onClick=${refresh} disabled=${refreshing}>${refreshing ? 'Refreshing…' : 'Refresh'}</button>
        <button class="btn-primary btn-sm" onClick=${() => navigate?.('clearledgr/pipeline')}>Open pipeline</button>
      </div>
    </div>

    <div class="secondary-chip-row" style="margin:0 0 18px">
      <span class="secondary-chip">Awaiting approval ${Number(dash.pending_approval || 0).toLocaleString()}</span>
      <span class="secondary-chip">Posted today ${Number(dash.posted_today || 0).toLocaleString()}</span>
      <span class="secondary-chip">Rejected today ${Number(dash.rejected_today || 0).toLocaleString()}</span>
      <span class="secondary-chip">Total processed ${Number(dash.total_invoices || 0).toLocaleString()}</span>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">Recent updates</h3>
      <p class="muted" style="margin-top:0">Recent changes across approvals, posting, and exceptions.</p>
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
  `;
}
