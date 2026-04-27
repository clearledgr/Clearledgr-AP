import { useEffect, useMemo, useState } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { html } from '../../utils/htm.js';
import { api } from '../../api/client.js';
import { useBootstrap, useOrgId } from '../../shell/BootstrapContext.js';
import { formatAmount } from '../../utils/formatters.js';

/**
 * Workspace home — the dashboard customers see on first login.
 *
 * Modeled on the BILL.com / Mixmax / Ramp admin overview pattern:
 * KPI tile row → two-column primary content (activity + vendors) →
 * quick-actions row → optional setup-checklist banner if onboarding
 * isn't complete. Fits a 1200px+ desktop canvas; collapses cleanly
 * on tablet via grid-template-columns auto-fit.
 *
 * Data sources (all already in the api):
 *   /api/ap/items/upcoming                — recent + upcoming items
 *   /api/ap/items/metrics/aggregation     — counts, exception rate,
 *                                           top vendors, sums
 *   /api/ap/items/aging                   — past-due bucket totals
 *   /api/vendors/summary                  — vendor rollup (Gap 6)
 *
 * Each tile/panel renders a real loading state and a real empty
 * state — no blank panes. Failures show a small inline notice so
 * the rest of the dashboard still renders.
 */

function pickAccent(state) {
  const s = String(state || '').toLowerCase();
  if (['posted_to_erp', 'paid', 'closed', 'approved'].includes(s)) return 'good';
  if (['needs_info', 'failed_post', 'rejected'].includes(s)) return 'warn';
  if (['needs_approval', 'pending_approval'].includes(s)) return 'pending';
  return 'neutral';
}

function statePill(state) {
  const s = String(state || '').toLowerCase();
  const labels = {
    received: 'Received',
    validated: 'Validated',
    needs_approval: 'Needs approval',
    pending_approval: 'Pending approval',
    needs_info: 'Needs info',
    approved: 'Approved',
    ready_to_post: 'Ready to post',
    posted_to_erp: 'Posted',
    paid: 'Paid',
    rejected: 'Rejected',
    failed_post: 'Failed post',
    closed: 'Closed',
    snoozed: 'Snoozed',
  };
  const label = labels[s] || (s ? s.replace(/_/g, ' ') : 'Unknown');
  return html`<span class=${`cl-home-pill cl-home-pill-${pickAccent(s)}`}>${label}</span>`;
}

function fmtRelative(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  const now = Date.now();
  const diff = now - d.getTime();
  const sec = Math.round(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

function fmtCurrency(amount, currency) {
  return formatAmount(amount, currency || 'USD');
}

export function HomePage() {
  const bootstrap = useBootstrap();
  const orgId = useOrgId();
  const [, navigate] = useLocation();

  const [upcoming, setUpcoming] = useState({ status: 'loading', data: null });
  const [metrics, setMetrics] = useState({ status: 'loading', data: null });
  const [aging, setAging] = useState({ status: 'loading', data: null });

  useEffect(() => {
    let cancelled = false;
    const orgQuery = `organization_id=${encodeURIComponent(orgId)}`;
    Promise.allSettled([
      api(`/api/ap/items/upcoming?${orgQuery}&limit=10`),
      api(`/api/ap/items/metrics/aggregation?${orgQuery}&vendor_limit=5`),
      api(`/api/ap/items/aging?${orgQuery}`),
    ]).then(([up, met, age]) => {
      if (cancelled) return;
      setUpcoming(
        up.status === 'fulfilled'
          ? { status: 'ready', data: up.value }
          : { status: 'error', data: null, error: up.reason?.message || 'load_failed' }
      );
      setMetrics(
        met.status === 'fulfilled'
          ? { status: 'ready', data: met.value }
          : { status: 'error', data: null, error: met.reason?.message || 'load_failed' }
      );
      setAging(
        age.status === 'fulfilled'
          ? { status: 'ready', data: age.value }
          : { status: 'error', data: null, error: age.reason?.message || 'load_failed' }
      );
    });
    return () => { cancelled = true; };
  }, [orgId]);

  const userName = bootstrap?.current_user?.name || bootstrap?.current_user?.email?.split('@')[0] || 'there';
  const orgName = bootstrap?.organization?.name || 'your workspace';
  const onboardingPending = bootstrap?.onboarding && bootstrap.onboarding.completed === false;

  const m = metrics.data?.metrics || metrics.data || {};
  const totalsByCurrency = m.outstanding_total_by_currency || m.totals_by_currency || {};
  const primaryCurrency = Object.keys(totalsByCurrency)[0] || 'USD';
  const outstandingTotal = totalsByCurrency[primaryCurrency] || 0;

  const exceptionCount = m.exceptions_count || m.exception_count || 0;
  const avgDpo = m.avg_days_to_pay ?? m.avg_dpo ?? null;
  const itemsTotal = m.total_items || m.count || 0;

  const items = upcoming.data?.items || upcoming.data?.upcoming || [];
  const topVendors = m.top_vendors || m.vendors || [];

  const now = useMemo(() => new Date(), []);
  const today = now.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });

  return html`
    <div class="cl-home">
      <header class="cl-home-header">
        <div>
          <div class="cl-home-eyebrow">${today}</div>
          <h1 class="cl-home-title">Welcome back, ${userName}.</h1>
          <p class="cl-home-sub">${orgName} · workspace overview</p>
        </div>
        <div class="cl-home-actions">
          <button class="cl-home-btn cl-home-btn-secondary" onClick=${() => navigate('/pipeline')}>
            Open pipeline
          </button>
          <button class="cl-home-btn cl-home-btn-primary" onClick=${() => navigate('/exceptions')}>
            Review exceptions
          </button>
        </div>
      </header>

      ${onboardingPending
        ? html`
            <aside class="cl-home-onboarding-banner">
              <div>
                <strong>Setup is in progress.</strong> Complete onboarding to start auto-routing AP.
              </div>
              <button class="cl-home-btn cl-home-btn-primary" onClick=${() => navigate('/onboarding')}>
                Resume setup
              </button>
            </aside>
          `
        : null}

      <section class="cl-home-tiles">
        <${KpiTile}
          label="Outstanding AP"
          value=${metrics.status === 'loading' ? '…' : fmtCurrency(outstandingTotal, primaryCurrency)}
          hint=${metrics.status === 'ready' && itemsTotal ? `${itemsTotal} open invoice${itemsTotal === 1 ? '' : 's'}` : ''}
          accent="primary"
        />
        <${KpiTile}
          label="Open exceptions"
          value=${metrics.status === 'loading' ? '…' : exceptionCount}
          hint=${exceptionCount > 0 ? 'Need attention' : 'Clean'}
          accent=${exceptionCount > 0 ? 'warn' : 'good'}
          onClick=${() => exceptionCount > 0 && navigate('/exceptions')}
        />
        <${KpiTile}
          label="Avg days to pay"
          value=${avgDpo == null ? '—' : `${Number(avgDpo).toFixed(1)}d`}
          hint="Last 30 days"
          accent="neutral"
        />
        <${KpiTile}
          label="Past due"
          value=${aging.status === 'loading'
            ? '…'
            : fmtCurrency(aging.data?.total_past_due || 0, primaryCurrency)}
          hint=${(aging.data?.bucket_30_60 || 0) > 0 ? '> 30 days bucket open' : 'Within tolerance'}
          accent=${(aging.data?.total_past_due || 0) > 0 ? 'warn' : 'good'}
        />
      </section>

      <section class="cl-home-grid">
        <div class="cl-home-panel">
          <header class="cl-home-panel-header">
            <h2>Recent activity</h2>
            <button class="cl-home-link" onClick=${() => navigate('/activity')}>View all →</button>
          </header>
          ${upcoming.status === 'loading'
            ? html`<div class="cl-home-skeleton">Loading…</div>`
            : items.length === 0
              ? html`
                  <div class="cl-home-empty">
                    <div class="cl-home-empty-title">No invoices yet.</div>
                    <div class="cl-home-empty-sub">
                      Connect Gmail or your ERP to start ingesting invoices automatically.
                    </div>
                    <button class="cl-home-btn cl-home-btn-secondary" onClick=${() => navigate('/connections')}>
                      Connect a source
                    </button>
                  </div>
                `
              : html`
                  <ul class="cl-home-list">
                    ${items.slice(0, 7).map((item) => html`
                      <li class="cl-home-row" key=${item.id} onClick=${() => navigate(`/items/${encodeURIComponent(item.id)}`)}>
                        <div class="cl-home-row-main">
                          <div class="cl-home-row-vendor">${item.vendor_name || item.vendor || 'Unknown vendor'}</div>
                          <div class="cl-home-row-meta">
                            ${item.invoice_number ? `#${item.invoice_number}` : ''}
                            ${item.invoice_number && item.updated_at ? ' · ' : ''}
                            ${fmtRelative(item.updated_at || item.created_at)}
                          </div>
                        </div>
                        <div class="cl-home-row-right">
                          <div class="cl-home-row-amount">${fmtCurrency(item.amount, item.currency)}</div>
                          ${statePill(item.state)}
                        </div>
                      </li>
                    `)}
                  </ul>
                `}
        </div>

        <div class="cl-home-panel">
          <header class="cl-home-panel-header">
            <h2>Top vendors</h2>
            <button class="cl-home-link" onClick=${() => navigate('/vendors')}>View all →</button>
          </header>
          ${metrics.status === 'loading'
            ? html`<div class="cl-home-skeleton">Loading…</div>`
            : topVendors.length === 0
              ? html`
                  <div class="cl-home-empty">
                    <div class="cl-home-empty-title">No vendor activity.</div>
                    <div class="cl-home-empty-sub">Vendor rollups appear once invoices flow through.</div>
                  </div>
                `
              : html`
                  <ul class="cl-home-list">
                    ${topVendors.slice(0, 5).map((v) => html`
                      <li class="cl-home-row" key=${v.vendor_name || v.name} onClick=${() => navigate(`/vendors/${encodeURIComponent(v.vendor_name || v.name || '')}`)}>
                        <div class="cl-home-row-main">
                          <div class="cl-home-row-vendor">${v.vendor_name || v.name || 'Unknown'}</div>
                          <div class="cl-home-row-meta">
                            ${v.invoice_count || v.total_bills || 0} invoice${(v.invoice_count || v.total_bills) === 1 ? '' : 's'}
                          </div>
                        </div>
                        <div class="cl-home-row-right">
                          <div class="cl-home-row-amount">${fmtCurrency(v.total_amount || v.outstanding_amount || 0, primaryCurrency)}</div>
                        </div>
                      </li>
                    `)}
                  </ul>
                `}
        </div>
      </section>

      <footer class="cl-home-quick-actions">
        <h3>Quick actions</h3>
        <div class="cl-home-quick-actions-row">
          <${QuickAction} label="Open pipeline" desc="See every AP item by stage" onClick=${() => navigate('/pipeline')} />
          <${QuickAction} label="Reconciliation" desc="Match bank ↔ ERP" onClick=${() => navigate('/reconciliation')} />
          <${QuickAction} label="Invite teammate" desc="Add an approver or AP clerk" onClick=${() => navigate('/settings')} />
          <${QuickAction} label="Connect an integration" desc="ERP, Slack, Teams, Gmail" onClick=${() => navigate('/connections')} />
        </div>
      </footer>
    </div>
  `;
}

function KpiTile({ label, value, hint, accent = 'neutral', onClick }) {
  const clickable = typeof onClick === 'function';
  return html`
    <div
      class=${`cl-home-tile cl-home-tile-${accent} ${clickable ? 'cl-home-tile-clickable' : ''}`}
      onClick=${clickable ? onClick : undefined}
      role=${clickable ? 'button' : undefined}
      tabindex=${clickable ? 0 : undefined}>
      <div class="cl-home-tile-label">${label}</div>
      <div class="cl-home-tile-value">${value}</div>
      ${hint ? html`<div class="cl-home-tile-hint">${hint}</div>` : null}
    </div>
  `;
}

function QuickAction({ label, desc, onClick }) {
  return html`
    <button class="cl-home-qa" onClick=${onClick}>
      <div class="cl-home-qa-label">${label}</div>
      <div class="cl-home-qa-desc">${desc}</div>
    </button>
  `;
}
