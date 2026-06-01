import { useEffect, useMemo, useState } from 'preact/hooks';
// `api` + `html` are imported below; the ImplementationChecklist
// component uses both. No new imports needed beyond the existing
// header — pre-existing.
import { useLocation } from 'wouter-preact';
import { html } from '../../utils/htm.js';
import { api } from '../../api/client.js';
import { useBootstrap, useOrgId } from '../../shell/BootstrapContext.js';
import { formatAmount, formatRelative, displayOrgName } from '../../utils/formatters.js';
import { AgentActivityRibbon } from '../../components/AgentActivityRibbon.js';

/**
 * Workspace Home — work-in-progress control center.
 *
 * DESIGN.md §Workspace Surface Pattern: this is the leader's daily
 * landing page. It shows live work in progress —
 * what the agent is doing across surfaces right now, what needs
 * judgment, what just shipped to ERP. Reference hierarchy: Linear
 * (real-time activity, dense lists), Vercel deployments (live stream
 * is the page), Datadog overview (professional density), Modal jobs
 * (running work primary). NOT BILL.com / Ramp / Mixmax.
 *
 * Page order:
 *   1. Welcome header + primary actions
 *   2. Onboarding banner (only if onboarding incomplete)
 *   3. Agent activity ribbon — the hero. Live SSE stream of agent /
 *      operator actions across every render target. The whole page is
 *      organized around this primary surface; everything below is
 *      either context (stats) or follow-up work (exceptions, vendors).
 *   4. Compact stat strip (4 dense tiles, live-pulse indicators)
 *   5. 2-col panels: Exception queue + Top vendors
 *   6. Approver workload
 *   7. System status footer
 *
 * Each panel fetches independently; one slow endpoint never gates
 * the rest. SSE keeps stats / workload / activity live within ~15s.
 */

// formatRelative lives in utils/formatters.js so the shared
// AgentActivityRibbon component can reuse it. Kept as a local alias so
// the existing call sites in this file (status footer, agent last
// action) don't churn.
const fmtRelative = formatRelative;

function fmtCurrency(amount, currency) {
  // Don't default to USD when currency is missing — that misrepresents
  // non-USD records (a GHS invoice rendered as "USD 5,000.00" is worse
  // than "5,000.00" with no code). The org's functional currency is
  // the right fallback if we ever need one, not USD.
  return formatAmount(amount, currency);
}

// Each panel resolves on its own timer; stuck panels fall through
// to a muted "Couldn't load — retry" instead of sitting on Loading.
function useEndpoint(path, deps = []) {
  const [state, setState] = useState({ status: 'loading', data: null, error: null });
  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading', data: null, error: null });
    api(path, { silent: true })
      .then((data) => { if (!cancelled) setState({ status: 'ready', data, error: null }); })
      .catch((err) => { if (!cancelled) setState({ status: 'error', data: null, error: err?.message || 'load_failed' }); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export function HomePage() {
  const bootstrap = useBootstrap();
  const orgId = useOrgId();
  const [, navigate] = useLocation();

  const orgQuery = `organization_id=${encodeURIComponent(orgId)}`;
  const metrics = useEndpoint(`/api/ap/items/metrics/aggregation?${orgQuery}&vendor_limit=5`, [orgId]);
  const upcoming = useEndpoint(`/api/ap/items/upcoming?${orgQuery}&limit=10`, [orgId]);
  const workload = useEndpoint('/api/workspace/dashboard/approver-workload', [orgId]);
  const exceptions = useEndpoint('/api/admin/box/exceptions?box_type=ap_item&limit=10', [orgId]);
  const activity = useEndpoint('/api/workspace/dashboard/recent-activity?limit=20', [orgId]);

  // SSE-pushed live updates: stats, workload, activity. Keeps the
  // control center honest within ~15s of the agent acting (per
  // Module 1 spec line 92, 30s ceiling).
  const [liveDashboard, setLiveDashboard] = useState(null);
  const [liveActivity, setLiveActivity] = useState(null);
  const [liveWorkload, setLiveWorkload] = useState(null);
  const [streamPulse, setStreamPulse] = useState(0);  // increments on every frame; powers the live-pulse dot

  useEffect(() => {
    if (typeof EventSource === 'undefined') return undefined;
    const source = new EventSource('/api/workspace/dashboard/stream', { withCredentials: true });
    source.onmessage = (event) => {
      try {
        const frame = JSON.parse(event.data);
        if (!frame?.type) return;
        if (frame.type === 'stats' && frame.data)    setLiveDashboard(frame.data);
        if (frame.type === 'workload' && frame.data) setLiveWorkload(frame.data);
        if (frame.type === 'activity' && frame.data) setLiveActivity(frame.data);
        setStreamPulse((p) => (p + 1) % 1_000_000);
      } catch { /* ignore bad frame */ }
    };
    source.onerror = () => { if (source.readyState === 2) source.close(); };
    return () => source.close();
  }, [orgId]);

  const userName = bootstrap?.current_user?.name || bootstrap?.current_user?.email?.split('@')[0] || 'there';
  const orgName = displayOrgName(bootstrap?.organization?.name) || 'your workspace';
  const onboardingPending = bootstrap?.onboarding && bootstrap.onboarding.completed === false;

  const m = metrics.data?.metrics || metrics.data || {};
  const totalsByCurrency = m.outstanding_total_by_currency || m.totals_by_currency || {};
  // No transactions yet → no currency to display. Don't fabricate USD.
  const primaryCurrency = Object.keys(totalsByCurrency)[0] || '';

  const dash = liveDashboard || bootstrap?.dashboard_stats || bootstrap?.dashboard || {};
  const inFlight = Number(dash.in_flight || 0);
  const awaitingApproval = Number(dash.pending_approval || 0);
  const processedWeek = Number(dash.processed_this_week || 0);
  const exceptionCount = Number(
    exceptions.data?.count
    ?? m.exceptions_count
    ?? m.exception_count
    ?? 0,
  );

  const exceptionItems = Array.isArray(exceptions.data?.items) ? exceptions.data.items : [];
  const upcomingItems = upcoming.data?.items || upcoming.data?.upcoming || [];
  const topVendors = m.top_vendors || m.vendors || [];

  // Activity ribbon: live SSE feed wins over the initial HTTP fetch.
  const activityItems = (liveActivity?.items)
    || (Array.isArray(activity.data?.items) ? activity.data.items : []);

  // Workload: live SSE wins.
  const workloadState = liveWorkload
    ? { status: 'ready', data: liveWorkload, error: null }
    : workload;

  const integrations = Array.isArray(bootstrap?.integrations) ? bootstrap.integrations : [];
  const agentLastAction = bootstrap?.dashboard_stats?.last_action_at
    || bootstrap?.dashboard?.last_action_at
    || dash.last_action_at
    || activityItems[0]?.ts
    || null;

  const now = useMemo(() => new Date(), []);
  const today = now.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });

  return html`
    <div class="cl-home">
      <header class="cl-home-header">
        <div class="cl-home-headline">
          <div class="cl-home-eyebrow">${today}</div>
          <h1 class="cl-home-title">Welcome back, ${userName}.</h1>
          <p class="cl-home-sub">${orgName} · work in progress</p>
        </div>
        <div class="cl-home-actions">
          <button class="cl-home-btn cl-home-btn-secondary" onClick=${() => navigate('/activity')}>
            Open activity
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

      <${ImplementationChecklist} orgId=${orgId} navigate=${navigate} />

      <${AgentActivityRibbon}
        state=${activity}
        items=${activityItems}
        live=${!!liveActivity}
        navigate=${navigate} />

      <section class="cl-home-stat-strip" aria-label="Work in progress at a glance">
        <${StatTile}
          label="In flight"
          value=${inFlight}
          sub=${inFlight === 0 ? 'No invoices in progress' : 'Across all open states'}
          tone="brand"
          live=${streamPulse > 0}
          onClick=${() => navigate('/records')}
        />
        <${StatTile}
          label="Awaiting approval"
          value=${awaitingApproval}
          sub=${awaitingApproval === 0 ? 'No bottleneck' : 'In approver queues'}
          tone=${awaitingApproval > 0 ? 'pending' : 'good'}
          live=${streamPulse > 0}
          onClick=${() => navigate('/records?scope=approvals')}
        />
        <${StatTile}
          label="Processed this week"
          value=${processedWeek}
          sub="Last 7 days · posted or closed"
          tone="neutral"
          live=${streamPulse > 0}
        />
        <${StatTile}
          label="Agent exceptions"
          value=${exceptionCount}
          sub=${exceptionCount > 0 ? 'Need judgment' : 'Clean'}
          tone=${exceptionCount > 0 ? 'warn' : 'good'}
          live=${streamPulse > 0}
          onClick=${() => exceptionCount > 0 && navigate('/exceptions')}
        />
      </section>

      <section class="cl-home-grid">
        <div class="cl-home-panel">
          <header class="cl-home-panel-header">
            <h2>Exception queue</h2>
            <button class="cl-home-link" onClick=${() => navigate('/exceptions')}>View all →</button>
          </header>
          ${renderPanelBody({
            state: exceptions,
            items: exceptionItems,
            renderEmpty: () => html`
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
            `,
            renderList: () => html`
              <ul class="cl-home-list">
                ${exceptionItems.slice(0, 8).map((row) => html`
                  <li class="cl-home-row cl-home-row-exception" key=${row.id || row.exception_id || row.box_id}
                    onClick=${() => navigate(`/exceptions/${encodeURIComponent(row.box_id || row.id || '')}`)}>
                    <div class="cl-home-row-main">
                      <div class="cl-home-row-vendor">
                        ${row.vendor_name || row.vendor || row.box_summary?.vendor_name || 'Vendor not extracted'}
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
            `,
          })}
        </div>

        <div class="cl-home-panel">
          <header class="cl-home-panel-header">
            <h2>Top vendors</h2>
            <button class="cl-home-link" onClick=${() => navigate('/vendors')}>View all →</button>
          </header>
          ${renderPanelBody({
            state: metrics,
            items: topVendors,
            renderEmpty: () => html`
              <div class="cl-home-empty">
                <div class="cl-home-empty-title">No vendor activity.</div>
                <div class="cl-home-empty-sub">Vendor rollups appear once invoices flow through.</div>
              </div>
            `,
            renderList: () => html`
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
            `,
          })}
        </div>
      </section>

      <${ApproverWorkloadStrip} state=${workloadState} navigate=${navigate} />

      <${SystemStatusFooter}
        integrations=${integrations}
        agentLastAction=${agentLastAction}
        navigate=${navigate} />
    </div>
  `;
}


// ─── Implementation checklist (moved here from SettingsPage) ─────
//
// First-time admins land on Home. Onboarding tasks belong here, not
// buried at the bottom of Settings where most users will never scroll.
// The component hides itself entirely once every step is complete so
// it stops taking visual real estate from veteran admins.

function ImplementationChecklist({ orgId, navigate }) {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    if (!orgId) return undefined;
    let cancelled = false;
    api(
      `/api/workspace/implementation/status?organization_id=${encodeURIComponent(orgId)}`,
      { silent: true },
    )
      .then((data) => { if (!cancelled) setStatus(data); })
      .catch(() => { /* hide quietly on error */ });
    return () => { cancelled = true; };
  }, [orgId]);

  if (!status || !Array.isArray(status.steps) || status.all_complete) {
    return null;
  }
  const steps = status.steps;
  const completedCount = steps.filter((s) => s.completed).length;
  const total = steps.length;

  return html`
    <section class="cl-home-checklist" aria-label="Implementation checklist">
      <header class="cl-home-checklist-head">
        <div>
          <strong>Finish setting up Solden</strong>
          <span class="cl-home-checklist-progress muted small">
            ${completedCount} of ${total} done
          </span>
        </div>
        <button class="cl-home-btn cl-home-btn-secondary" onClick=${() => navigate('/settings')}>
          Open settings
        </button>
      </header>
      <ol class="cl-home-checklist-list">
        ${steps.map((step) => html`
          <li
            key=${step.id}
            class=${`cl-home-checklist-item ${step.completed ? 'is-done' : ''}`}>
            <span class="cl-home-checklist-dot" aria-hidden="true">
              ${step.completed ? '✓' : ''}
            </span>
            <div class="cl-home-checklist-copy">
              <div class="cl-home-checklist-name">${step.name}</div>
              <div class="muted small">${step.description}</div>
            </div>
          </li>
        `)}
      </ol>
    </section>
  `;
}


// ─── Compact stat tile (control-center idiom) ─────────────────────
//
// Smaller / denser than a Bill / Ramp KPI tile. Live-pulse dot in the
// corner indicates the SSE stream is connected. Tabular numerals.
// Click navigates to the relevant slice.

function StatTile({ label, value, sub, tone = 'neutral', live = false, onClick }) {
  const clickable = typeof onClick === 'function';
  return html`
    <div
      class=${`cl-home-stat cl-home-stat-${tone} ${clickable ? 'cl-home-stat-clickable' : ''}`}
      onClick=${clickable ? onClick : undefined}
      role=${clickable ? 'button' : undefined}
      tabindex=${clickable ? 0 : undefined}>
      <div class="cl-home-stat-head">
        <span class="cl-home-stat-label">${label}</span>
        ${live ? html`<span class="cl-home-stat-pulse" aria-label="Live"></span>` : null}
      </div>
      <div class="cl-home-stat-value">${value}</div>
      ${sub ? html`<div class="cl-home-stat-sub">${sub}</div>` : null}
    </div>
  `;
}


// ─── Panel body renderer (loading / error / empty / list) ─────────

function renderPanelBody({ state, items, renderEmpty, renderList }) {
  if (state.status === 'loading') {
    return html`<div class="cl-home-skeleton">Loading…</div>`;
  }
  if (state.status === 'error') {
    return html`
      <div class="cl-home-empty">
        <div class="cl-home-empty-title cl-home-empty-error">Couldn't load this section.</div>
        <div class="cl-home-empty-sub">${state.error || 'Try again in a moment.'}</div>
      </div>
    `;
  }
  if (!items || items.length === 0) return renderEmpty();
  return renderList();
}


// ─── Module 1 — Approver workload strip ───────────────────────────

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
  if (state.status === 'error') {
    return html`
      <section class="cl-home-workload">
        <header class="cl-home-workload-head">
          <h2>Approver workload</h2>
        </header>
        <div class="cl-home-empty">
          <div class="cl-home-empty-title cl-home-empty-error">Couldn't load workload.</div>
          <div class="cl-home-empty-sub">${state.error || 'Try again in a moment.'}</div>
        </div>
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
            onClick=${() => navigate(`/records?approver=${encodeURIComponent(a.email || a.approver_id)}`)}>
            <div class="cl-home-workload-main">
              <div class="cl-home-workload-name">${a.name || a.email || a.approver_id}</div>
              ${a.email && a.email !== a.name ? html`
                <div class="cl-home-workload-email"><code>${a.email}</code></div>
              ` : null}
            </div>
            <div class="cl-home-workload-stats">
              <span class="cl-home-workload-count">${a.pending_count}</span>
              <span class="cl-home-workload-count-label">pending</span>
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
        <div class="cl-home-workload-more">+ ${approvers.length - 8} more approvers</div>
      ` : null}
    </section>
  `;
}

function ageTone(days) {
  if (days >= 5) return 'alert';
  if (days >= 2) return 'warn';
  return 'ok';
}


// ─── System status footer ─────────────────────────────────────────

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
        <${StatusCell} label="Approvals" integration=${slack.connected ? slack : teams} fallbackLabel="No Slack/Teams approval channel" />
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


// ─── Exception queue helpers ──────────────────────────────────────

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
