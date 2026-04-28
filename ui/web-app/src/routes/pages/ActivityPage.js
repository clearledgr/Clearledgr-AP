/**
 * Activity Page — secondary AP support surface.
 * Keeps recent finance activity reachable without turning Gmail into a KPI dashboard.
 */
import { h } from 'preact';
import htm from 'htm';
import { eventBadge, fmtDateTime, useAction } from '../route-helpers.js';
import { EmptyState } from '../../components/StatePrimitives.js';

const html = htm.bind(h);

function SummaryCard({ label, value }) {
  return html`<div class="secondary-stat-card">
    <strong>${label}</strong>
    <span style="font-family:var(--font-display);font-size:22px;font-weight:700;color:var(--ink)">${Number(value || 0).toLocaleString()}</span>
  </div>`;
}

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
        <button class="btn-primary btn-sm" onClick=${() => navigate?.('clearledgr/invoices')}>Open invoices</button>
      </div>
    </div>

    <div class="secondary-stat-grid" style="margin:0 0 18px">
      <${SummaryCard} label="Awaiting approval" value=${dash.pending_approval || 0} />
      <${SummaryCard} label="Posted today" value=${dash.posted_today || 0} />
      <${SummaryCard} label="Rejected today" value=${dash.rejected_today || 0} />
      <${SummaryCard} label="Total processed" value=${dash.total_invoices || 0} />
    </div>

    <div class="panel">
      <div class="panel-head compact">
        <div>
          <h3 style="margin-top:0">Recent updates</h3>
          <p class="muted" style="margin:0">Recent changes across approvals, posting, and exceptions.</p>
        </div>
      </div>
      ${events.length === 0
        ? html`<${EmptyState}
            title="No recent activity yet."
            description="Approvals, ERP posts, and exception updates will appear here as your team works the queue."
            ctaLabel="Open invoices →"
            onCtaClick=${() => navigate?.('clearledgr/invoices')}
          />`
        : html`<div class="secondary-card-list">
            ${events.map((event, index) => {
              const badge = eventBadge(event.event_type || event.new_state || 'activity');
              const title = String(event.title || event.summary || badge.label || 'Activity recorded').trim() || 'Activity recorded';
              const subtitle = String(event.detail || event.message || '').trim();
              return html`
                <div key=${event.id || index} class="secondary-card">
                  <div class="secondary-card-head">
                    <div class="secondary-card-copy">
                      <div class="secondary-inline-actions" style="margin-bottom:0">
                        <span class=${`status-badge ${badge.cls || ''}`}>${badge.label}</span>
                        <strong class="secondary-card-title" style="font-size:13px">${title}</strong>
                      </div>
                      ${subtitle && html`<div class="secondary-card-meta" style="margin-top:6px">${subtitle}</div>`}
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
