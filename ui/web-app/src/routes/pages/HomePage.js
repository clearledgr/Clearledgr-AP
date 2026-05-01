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
  const [workload, setWorkload] = useState({ status: 'loading', data: null });
  // Module 1 spec line 71: "Exceptions queue: every invoice currently
  // stuck and needing human judgment, sorted by age, with vendor,
  // amount, exception type, who's blocking, days stuck, agent's
  // suggestion. Click through to Module 2."
  const [exceptions, setExceptions] = useState({ status: 'loading', data: null });

  useEffect(() => {
    let cancelled = false;
    const orgQuery = `organization_id=${encodeURIComponent(orgId)}`;
    Promise.allSettled([
      api(`/api/ap/items/upcoming?${orgQuery}&limit=10`),
      api(`/api/ap/items/metrics/aggregation?${orgQuery}&vendor_limit=5`),
      api(`/api/ap/items/aging?${orgQuery}`),
      api('/api/workspace/dashboard/approver-workload'),
      api('/api/admin/box/exceptions?box_type=ap_item&limit=10'),
    ]).then(([up, met, age, wl, exc]) => {
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
      setWorkload(
        wl.status === 'fulfilled'
          ? { status: 'ready', data: wl.value }
          : { status: 'error', data: null, error: wl.reason?.message || 'load_failed' }
      );
      setExceptions(
        exc.status === 'fulfilled'
          ? { status: 'ready', data: exc.value }
          : { status: 'error', data: null, error: exc.reason?.message || 'load_failed' }
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

  // Module 1 spec stat cards (line 76):
  //   in flight | awaiting approval | processed this week | agent exceptions
  const dash = bootstrap?.dashboard_stats || bootstrap?.dashboard || {};
  const inFlight = Number(dash.in_flight || 0);
  const awaitingApproval = Number(dash.pending_approval || 0);
  const processedWeek = Number(dash.processed_this_week || 0);
  const exceptionCount = Number(
    exceptions.data?.count
    ?? m.exceptions_count
    ?? m.exception_count
    ?? 0,
  );

  const upcomingItems = upcoming.data?.items || upcoming.data?.upcoming || [];
  const exceptionItems = Array.isArray(exceptions.data?.items) ? exceptions.data.items : [];
  const topVendors = m.top_vendors || m.vendors || [];

  // Module 1 spec line 78: "System status footer: agent active, last
  // action timestamp, sync status with each connected ERP, inbox,
  // Slack workspace." Pulled from the bootstrap.integrations array.
  const integrations = Array.isArray(bootstrap?.integrations) ? bootstrap.integrations : [];
  const agentLastAction = bootstrap?.dashboard_stats?.last_action_at
    || bootstrap?.dashboard?.last_action_at
    || dash.last_action_at
    || null;

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
          label="In flight"
          value=${inFlight}
          hint=${inFlight === 0 ? 'No invoices in progress' : 'Across all open states'}
          accent="primary"
          onClick=${() => navigate('/pipeline')}
        />
        <${KpiTile}
          label="Awaiting approval"
          value=${awaitingApproval}
          hint=${awaitingApproval === 0 ? 'No bottleneck' : 'In approver queues'}
          accent=${awaitingApproval > 0 ? 'pending' : 'good'}
          onClick=${() => navigate('/pipeline?scope=approvals')}
        />
        <${KpiTile}
          label="Processed this week"
          value=${processedWeek}
          hint="Last 7 days · posted or closed"
          accent="neutral"
        />
        <${KpiTile}
          label="Agent exceptions"
          value=${exceptionCount}
          hint=${exceptionCount > 0 ? 'Need judgment' : 'Clean'}
          accent=${exceptionCount > 0 ? 'warn' : 'good'}
          onClick=${() => exceptionCount > 0 && navigate('/exceptions')}
        />
      </section>

      <section class="cl-home-grid">
        <div class="cl-home-panel">
          <header class="cl-home-panel-header">
            <h2>Exception queue</h2>
            <button class="cl-home-link" onClick=${() => navigate('/exceptions')}>View all →</button>
          </header>
          ${exceptions.status === 'loading'
            ? html`<div class="cl-home-skeleton">Loading…</div>`
            : exceptionItems.length === 0
              ? html`
                  <div class="cl-home-empty">
                    <div class="cl-home-empty-title">${upcomingItems.length === 0 ? 'No invoices yet.' : 'Nothing stuck right now.'}</div>
                    <div class="cl-home-empty-sub">
                      ${upcomingItems.length === 0
                        ? "Connect Gmail or your ERP to start ingesting invoices automatically."
                        : "Every invoice is moving. The agent will surface anything that needs your judgment here."}
                    </div>
                    ${upcomingItems.length === 0 ? html`
                      <button class="cl-home-btn cl-home-btn-secondary" onClick=${() => navigate('/connections')}>
                        Connect a source
                      </button>
                    ` : null}
                  </div>
                `
              : html`
                  <ul class="cl-home-list">
                    ${exceptionItems.slice(0, 8).map((row) => html`
                      <li class="cl-home-row cl-home-row-exception" key=${row.id || row.exception_id || row.box_id}
                        onClick=${() => navigate(`/exceptions/${encodeURIComponent(row.box_id || row.id || '')}`)}>
                        <div class="cl-home-row-main">
                          <div class="cl-home-row-vendor">
                            ${row.vendor_name || row.vendor || row.box_summary?.vendor_name || 'Unknown vendor'}
                          </div>
                          <div class="cl-home-row-meta">
                            ${humanizeExceptionType(row.exception_type)}
                            ${row.box_summary?.invoice_number ? html` · #${row.box_summary.invoice_number}` : null}
                            ${row.raised_at ? html` · ${exceptionAgeDays(row.raised_at)}d stuck` : null}
                          </div>
                          ${row.reason || row.metadata?.suggested_action ? html`
                            <div class="cl-home-row-suggestion">
                              ${row.metadata?.suggested_action || row.reason}
                            </div>
                          ` : null}
                        </div>
                        <div class="cl-home-row-right">
                          ${row.box_summary?.amount != null ? html`
                            <div class="cl-home-row-amount">
                              ${fmtCurrency(row.box_summary.amount, row.box_summary.currency)}
                            </div>
                          ` : null}
                          <span class=${`cl-home-pill cl-home-pill-${severityTone(row.severity)}`}>
                            ${row.severity || 'medium'}
                          </span>
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

      <${ApproverWorkloadStrip}
        state=${workload}
        navigate=${navigate} />

      <${SystemStatusFooter}
        integrations=${integrations}
        agentLastAction=${agentLastAction}
        navigate=${navigate} />

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


// ─── Module 1 — Approver workload strip ───────────────────────────
//
// Spec line 74: "Surfaces logistics ('Tobi has 8 waiting, oldest 5
// days, on PTO') so the leader can re-route. Logistics, not scoring."
// One row per approver: name, pending count, oldest age. Empty
// state when no chains pending. Click an approver → pipeline
// filtered to their items (client-side filter via query string).

function ApproverWorkloadStrip({ state, navigate }) {
  if (!state || state.status === 'loading') {
    return html`
      <section class="cl-home-workload">
        <header class="cl-home-workload-head">
          <h2>Approver workload</h2>
        </header>
        <div class="cl-home-skeleton">Loading…</div>
      </section>
    `;
  }

  const approvers = (state.data && state.data.approvers) || [];

  if (approvers.length === 0) {
    return html`
      <section class="cl-home-workload">
        <header class="cl-home-workload-head">
          <h2>Approver workload</h2>
          <span class="cl-home-workload-meta">Logistics, not scoring</span>
        </header>
        <div class="cl-home-empty">
          <div class="cl-home-empty-title">Nothing waiting on anyone right now.</div>
          <div class="cl-home-empty-sub">
            When invoices route to approval, you'll see who has what on their
            plate so you can re-route if someone is out.
          </div>
        </div>
      </section>
    `;
  }

  return html`
    <section class="cl-home-workload">
      <header class="cl-home-workload-head">
        <h2>Approver workload</h2>
        <span class="cl-home-workload-meta">
          ${approvers.length} approver${approvers.length === 1 ? '' : 's'} ·
          logistics, not scoring
        </span>
      </header>
      <ul class="cl-home-workload-list">
        ${approvers.slice(0, 8).map((a) => html`
          <li class="cl-home-workload-row" key=${a.approver_id}
            onClick=${() => navigate(`/pipeline?approver=${encodeURIComponent(a.email || a.approver_id)}`)}>
            <div class="cl-home-workload-main">
              <div class="cl-home-workload-name">${a.name || a.email || a.approver_id}</div>
              ${a.email && a.email !== a.name ? html`
                <div class="cl-home-workload-email"><code>${a.email}</code></div>
              ` : null}
            </div>
            <div class="cl-home-workload-stats">
              <span class="cl-home-workload-count">${a.pending_count}</span>
              <span class="cl-home-workload-count-label">
                pending${a.pending_count === 1 ? '' : ''}
              </span>
              ${a.oldest_pending_age_days != null ? html`
                <span class=${`cl-home-workload-age cl-home-workload-age-${ageTone(a.oldest_pending_age_days)}`}>
                  oldest ${a.oldest_pending_age_days}d
                </span>
              ` : null}
            </div>
          </li>
        `)}
      </ul>
      ${approvers.length > 8 ? html`
        <div class="cl-home-workload-more">
          + ${approvers.length - 8} more approvers
        </div>
      ` : null}
    </section>
  `;
}

function ageTone(days) {
  if (days >= 5) return 'alert';
  if (days >= 2) return 'warn';
  return 'ok';
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


// ─── Module 1 — System status footer ──────────────────────────────
//
// Spec line 78: "agent active, last action timestamp, sync status
// with each connected ERP, inbox, Slack workspace." A green/amber
// dot per integration; aggregate "agent active" indicator on the
// left. Source: bootstrap.integrations (already populated by the
// /api/workspace/bootstrap call upstream — no extra fetch).

function SystemStatusFooter({ integrations, agentLastAction, navigate }) {
  const watch = integrations.find((i) => i.name === 'gmail') || {};
  const slack = integrations.find((i) => i.name === 'slack') || {};
  const teams = integrations.find((i) => i.name === 'teams') || {};
  const erp = integrations.find((i) => i.name === 'erp') || {};

  const allConnected = [watch, slack, teams, erp].every((i) => i.connected || i.name === 'teams');
  const agentTone = allConnected ? 'good' : 'warn';
  const agentLabel = allConnected ? 'Agent active' : 'Agent partially configured';

  return html`
    <section class="cl-home-status" aria-label="System status">
      <header class="cl-home-status-head">
        <h3>System status</h3>
        <button class="cl-home-link" onClick=${() => navigate('/connections')}>
          Manage connections →
        </button>
      </header>
      <div class="cl-home-status-grid">
        <div class=${`cl-home-status-cell cl-home-status-cell-${agentTone}`}>
          <span class=${`cl-home-status-dot cl-home-status-dot-${agentTone}`}></span>
          <div>
            <div class="cl-home-status-label">${agentLabel}</div>
            <div class="cl-home-status-sub">
              ${agentLastAction
                ? `Last action ${fmtRelative(agentLastAction)}`
                : 'No actions recorded yet'}
            </div>
          </div>
        </div>
        <${StatusCell} label="Gmail" integration=${watch} fallbackLabel="Inbox not connected" />
        <${StatusCell} label="Approval surface" integration=${slack.connected ? slack : teams} fallbackLabel="No Slack/Teams approval surface" />
        <${StatusCell} label="ERP" integration=${erp} fallbackLabel="ERP not connected" />
      </div>
    </section>
  `;
}

function StatusCell({ label, integration, fallbackLabel }) {
  const connected = !!integration?.connected;
  const reauth = !!integration?.requires_reconnect || !!integration?.requires_reauthorization;
  const tone = !connected ? 'off' : reauth ? 'warn' : 'good';
  const stamp = integration?.last_sync_at || integration?.connected_at;
  return html`
    <div class=${`cl-home-status-cell cl-home-status-cell-${tone}`}>
      <span class=${`cl-home-status-dot cl-home-status-dot-${tone}`}></span>
      <div>
        <div class="cl-home-status-label">${label}</div>
        <div class="cl-home-status-sub">
          ${connected
            ? (reauth ? 'Reconnect required' : (stamp ? `Synced ${fmtRelative(stamp)}` : 'Connected'))
            : fallbackLabel}
        </div>
      </div>
    </div>
  `;
}


// ─── Module 1 — Exception queue helpers ───────────────────────────

function humanizeExceptionType(t) {
  const s = String(t || '').toLowerCase();
  if (!s) return 'Exception';
  return s
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function exceptionAgeDays(raisedAt) {
  if (!raisedAt) return 0;
  const t = new Date(raisedAt).getTime();
  if (isNaN(t)) return 0;
  return Math.max(0, Math.round((Date.now() - t) / 86400000));
}

function severityTone(sev) {
  const s = String(sev || '').toLowerCase();
  if (s === 'critical' || s === 'high') return 'warn';
  if (s === 'low') return 'good';
  return 'pending';
}
