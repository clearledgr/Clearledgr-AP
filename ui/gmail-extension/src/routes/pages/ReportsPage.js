/**
 * Reports Page — lightweight AP reporting kept secondary to Pipeline.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDollar, useAction } from '../route-helpers.js';
import {
  clearPipelineNavigation,
  getStarterPipelineViews,
  writePipelinePreferences,
} from '../pipeline-views.js';

const html = htm.bind(h);

function MetricCard({ label, value, detail }) {
  return html`<div style="padding:18px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
    <div style="font-size:26px;font-weight:700;letter-spacing:-0.02em">${value}</div>
    <div style="font-size:13px;font-weight:600;margin-top:2px">${label}</div>
    ${detail ? html`<div class="muted" style="margin-top:6px;font-size:12px">${detail}</div>` : null}
  </div>`;
}

function ReportRow({ label, value, detail }) {
  return html`<div style="display:flex;justify-content:space-between;gap:16px;padding:10px 0;border-bottom:1px solid var(--border)">
    <div>
      <div style="font-weight:600">${label}</div>
      ${detail ? html`<div class="muted" style="font-size:12px;margin-top:3px">${detail}</div>` : null}
    </div>
    <div style="font-weight:700;text-align:right">${value}</div>
  </div>`;
}

function metricPercent(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(2)}%` : '0.00%';
}

function metricHours(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(1)}h` : '0.0h';
}

function toneForPercent(value, { watchBelow = 95, dangerBelow = 90 } = {}) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 'color:var(--text-muted);';
  if (numeric < dangerBelow) return 'color:#B91C1C;';
  if (numeric < watchBelow) return 'color:#A16207;';
  return 'color:#047857;';
}

function safeNumber(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

export function getPilotScorecardSummary(kpis = {}, dashboard = {}) {
  const pilot = kpis?.pilot_scorecard || {};
  const summary = pilot?.summary || {};
  const approval = pilot?.approval_workflow || {};
  const routing = pilot?.entity_routing || {};
  const bootstrap = dashboard?.pilot_snapshot || {};
  const highlights = Array.isArray(pilot?.highlights) && pilot.highlights.length
    ? pilot.highlights
    : (Array.isArray(bootstrap?.highlights) ? bootstrap.highlights : []);

  return {
    touchlessRatePct: safeNumber(summary?.touchless_rate_pct, safeNumber(bootstrap?.touchless_rate_pct)),
    avgCycleTimeHours: safeNumber(summary?.avg_cycle_time_hours, safeNumber(bootstrap?.avg_cycle_time_hours)),
    onTimeApprovalsPct: safeNumber(summary?.on_time_approvals_pct, safeNumber(bootstrap?.on_time_approvals_pct)),
    avgApprovalWaitHours: safeNumber(summary?.avg_approval_wait_hours, safeNumber(bootstrap?.avg_approval_wait_hours)),
    approvalSlaBreachedOpenCount: safeNumber(summary?.approval_sla_breached_open_count, safeNumber(bootstrap?.approval_sla_breached_open_count)),
    approvalEscalatedOpenCount: safeNumber(approval?.escalated_open_count, safeNumber(bootstrap?.approval_escalated_open_count)),
    approvalReassignedOpenCount: safeNumber(approval?.reassigned_open_count, safeNumber(bootstrap?.approval_reassigned_open_count)),
    entityRouteNeedsReviewCount: safeNumber(summary?.entity_route_needs_review_count, safeNumber(bootstrap?.entity_route_needs_review_count)),
    entityRouteManualResolutionCount30d: safeNumber(routing?.manual_resolution_event_count_30d, safeNumber(bootstrap?.entity_route_manual_resolution_count_30d)),
    highlights: highlights.map((entry) => String(entry || '').trim()).filter(Boolean).slice(0, 4),
  };
}

export function getOperatorPressureSummary(kpis = {}) {
  const operator = kpis?.operator_metrics || {};
  const liveQueue = operator?.live_queue || {};
  const queueRates = operator?.queue_rates || {};
  const activity = operator?.activity || {};
  return {
    approvalQueueCount: safeNumber(liveQueue?.approval_queue_count),
    approvalSlaBreachedOpenCount: safeNumber(liveQueue?.approval_sla_breached_open_count),
    approvalEscalatedOpenCount: safeNumber(liveQueue?.approval_escalated_open_count),
    approvalReassignedOpenCount: safeNumber(liveQueue?.approval_reassigned_open_count),
    entityRouteNeedsReviewCount: safeNumber(liveQueue?.entity_route_needs_review_count),
    fieldReviewOpenCount: safeNumber(liveQueue?.field_review_open_count),
    approvalSlaBreachedOpenRatePct: safeNumber(queueRates?.approval_sla_breached_open_rate) * 100,
    entityRouteNeedsReviewRatePct: safeNumber(queueRates?.entity_route_needs_review_rate) * 100,
    approvalEscalationEventCount: safeNumber(activity?.approval_escalation_event_count),
    approvalReassignmentEventCount: safeNumber(activity?.approval_reassignment_event_count),
    entityRouteResolutionEventCount: safeNumber(activity?.entity_route_resolution_event_count),
    activityWindowDays: safeNumber(operator?.activity_window_days, 30),
  };
}

export default function ReportsPage({ api, bootstrap, orgId, userEmail, navigate, toast }) {
  const pipelineScope = useMemo(() => ({ orgId, userEmail }), [orgId, userEmail]);
  const [reportData, setReportData] = useState({ aggregation: null, kpis: null });
  const [loading, setLoading] = useState(true);
  const starterViews = useMemo(() => getStarterPipelineViews({}), []);

  const loadMetrics = async ({ silent = false } = {}) => {
    setLoading(true);
    try {
      const [aggregationResult, kpiResult] = await Promise.allSettled([
        api(`/api/ap/items/metrics/aggregation?organization_id=${encodeURIComponent(orgId)}`, { silent }),
        api(`/api/ops/ap-kpis?organization_id=${encodeURIComponent(orgId)}`, { silent }),
      ]);

      const aggregation = aggregationResult.status === 'fulfilled'
        ? (aggregationResult.value?.metrics || null)
        : null;
      const kpis = kpiResult.status === 'fulfilled'
        ? (kpiResult.value?.kpis || null)
        : null;

      setReportData({ aggregation, kpis });
      if (!aggregation && !kpis && !silent) {
        toast?.('Could not load reports.', 'error');
      }
    } catch {
      setReportData({ aggregation: null, kpis: null });
      if (!silent) toast?.('Could not load reports.', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadMetrics({ silent: true });
  }, [api, orgId]);

  const [refresh, refreshing] = useAction(async () => {
    await loadMetrics();
    toast?.('Reports refreshed.', 'success');
  });

  const openStarterView = (view) => {
    if (!view?.snapshot) return;
    clearPipelineNavigation(pipelineScope);
    writePipelinePreferences(pipelineScope, view.snapshot);
    navigate('clearledgr/pipeline');
  };

  const dashboard = bootstrap?.dashboard || {};
  const agenticSnapshot = dashboard?.agentic_snapshot || {};
  const totals = reportData?.aggregation?.totals || {};
  const sources = reportData?.aggregation?.sources || {};
  const duplicates = reportData?.aggregation?.duplicates || {};
  const topVendors = Array.isArray(reportData?.aggregation?.spend_by_vendor) ? reportData.aggregation.spend_by_vendor.slice(0, 6) : [];
  const sourceTypes = Object.entries(sources.link_count_by_type || {}).sort((left, right) => right[1] - left[1]).slice(0, 6);
  const pilotSummary = getPilotScorecardSummary(reportData?.kpis || {}, dashboard);
  const operatorSummary = getOperatorPressureSummary(reportData?.kpis || {});

  if (loading) {
    return html`<div class="panel" style="text-align:center;padding:48px"><p class="muted">Loading reports…</p></div>`;
  }

  return html`
    <div class="secondary-banner">
      <div class="secondary-banner-copy">
        <h3>Reports</h3>
        <p class="muted">Get a quick view of queue health, spend, coverage, and duplicate risk, then jump back into the work.</p>
      </div>
      <div class="secondary-banner-actions">
        <button class="btn-secondary btn-sm" onClick=${refresh} disabled=${refreshing}>${refreshing ? 'Refreshing…' : 'Refresh'}</button>
        <button class="btn-primary btn-sm" onClick=${() => navigate('clearledgr/pipeline')}>Open pipeline</button>
      </div>
    </div>

    <div class="kpi-row">
      <${MetricCard} label="Tracked invoices" value=${Number(totals.items || dashboard.total_invoices || 0).toLocaleString()} />
      <${MetricCard} label="Open items" value=${Number(totals.open_items || 0).toLocaleString()} detail=${`${Number(dashboard.pending_approval || 0).toLocaleString()} waiting approval`} />
      <${MetricCard} label="Tracked spend" value=${fmtDollar(totals.total_amount || 0)} detail=${`${Number(totals.amount_unavailable_count || 0).toLocaleString()} without amount`} />
      <${MetricCard} label="Duplicate clusters" value=${Number(duplicates.cluster_count || 0).toLocaleString()} detail=${`${Number(duplicates.duplicate_invoice_count || 0).toLocaleString()} duplicate invoices`} />
    </div>

    <div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px">
      <div style="display:flex;flex-direction:column;gap:20px">
        <div class="panel">
          <h3 style="margin-top:0">Top vendors by tracked spend</h3>
          ${topVendors.length === 0
            ? html`<p class="muted" style="margin:0">No vendor spend data yet.</p>`
            : html`${topVendors.map((row) => html`
                <${ReportRow}
                  key=${row.vendor_name}
                  label=${row.vendor_name || 'Unknown vendor'}
                  value=${fmtDollar(row.total_amount || 0)}
                  detail=${`${Number(row.open_count || 0).toLocaleString()} open · ${Number(row.invoice_count || 0).toLocaleString()} tracked invoices`}
                />
              `)}`}
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Source coverage</h3>
          <div style="display:flex;flex-direction:column;gap:8px">
            <${ReportRow}
              label="Total linked sources"
              value=${Number(sources.total_links || 0).toLocaleString()}
              detail=${`${Number(sources.items_with_sources || 0).toLocaleString()} invoices have linked evidence`}
            />
            <${ReportRow}
              label="Average links per invoice"
              value=${Number(sources.avg_links_per_item || 0).toFixed(2)}
              detail="Across all tracked records"
            />
            <${ReportRow}
              label="Average links per linked invoice"
              value=${Number(sources.avg_links_per_linked_item || 0).toFixed(2)}
              detail="Only invoices with at least one linked source"
            />
          </div>

          ${sourceTypes.length > 0 && html`
            <div style="margin-top:14px">
              <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;margin-bottom:8px">Connected source types</div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                ${sourceTypes.map(([sourceType, count]) => html`
                  <span key=${sourceType} style="padding:5px 10px;border-radius:999px;border:1px solid var(--border);background:var(--bg);font-size:12px;font-weight:600">
                    ${sourceType} ${count}
                  </span>
                `)}
              </div>
            </div>
          `}
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:20px">
        <div class="panel">
          <h3 style="margin-top:0">Pilot scorecard</h3>
          <p class="muted" style="margin:0 0 12px">
            Track whether Clearledgr is actually removing approval chasing and manual routing work.
          </p>
          <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px">
            <${MetricCard}
              label="Touchless completed invoices"
              value=${html`<span style=${toneForPercent(pilotSummary.touchlessRatePct, { watchBelow: 70, dangerBelow: 50 })}>${metricPercent(pilotSummary.touchlessRatePct)}</span>`}
            />
            <${MetricCard}
              label="Average cycle time"
              value=${metricHours(pilotSummary.avgCycleTimeHours)}
            />
            <${MetricCard}
              label="On-time approvals"
              value=${html`<span style=${toneForPercent(pilotSummary.onTimeApprovalsPct, { watchBelow: 85, dangerBelow: 70 })}>${metricPercent(pilotSummary.onTimeApprovalsPct)}</span>`}
            />
            <${MetricCard}
              label="Average approval wait"
              value=${metricHours(pilotSummary.avgApprovalWaitHours)}
            />
          </div>

          ${pilotSummary.highlights.length > 0 && html`
            <div style="margin-top:14px">
              <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;margin-bottom:8px">Current readout</div>
              <div class="secondary-list">
                ${pilotSummary.highlights.map((line) => html`
                  <div key=${line} class="secondary-note">${line}</div>
                `)}
              </div>
            </div>
          `}
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Operator pressure</h3>
          <p class="muted" style="margin:0 0 12px">
            Watch the live queue pressure points that still create manual chasing or routing work.
          </p>
          <div style="display:flex;flex-direction:column;gap:8px">
            <${ReportRow}
              label="Open approvals waiting"
              value=${Number(operatorSummary.approvalQueueCount || 0).toLocaleString()}
              detail=${`${Number(operatorSummary.approvalSlaBreachedOpenCount || 0).toLocaleString()} beyond SLA`}
            />
            <${ReportRow}
              label="Escalated approvals"
              value=${Number(operatorSummary.approvalEscalatedOpenCount || 0).toLocaleString()}
              detail=${`${Number(operatorSummary.approvalEscalationEventCount || 0).toLocaleString()} escalations in the last ${Number(operatorSummary.activityWindowDays || 30).toLocaleString()} days`}
            />
            <${ReportRow}
              label="Reassigned approvals"
              value=${Number(operatorSummary.approvalReassignedOpenCount || 0).toLocaleString()}
              detail=${`${Number(operatorSummary.approvalReassignmentEventCount || 0).toLocaleString()} reassignments in the last ${Number(operatorSummary.activityWindowDays || 30).toLocaleString()} days`}
            />
            <${ReportRow}
              label="Entity routing review"
              value=${Number(operatorSummary.entityRouteNeedsReviewCount || 0).toLocaleString()}
              detail=${`${Number(pilotSummary.entityRouteManualResolutionCount30d || 0).toLocaleString()} manual resolutions in the last 30 days`}
            />
            <${ReportRow}
              label="Field review queue"
              value=${Number(operatorSummary.fieldReviewOpenCount || 0).toLocaleString()}
              detail=${`Approval SLA breach rate ${metricPercent(operatorSummary.approvalSlaBreachedOpenRatePct)}`}
            />
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Autonomy quality</h3>
          <p class="muted" style="margin:0 0 12px">
            See how often the final outcome matched what Clearledgr suggested.
          </p>
          <div style="display:flex;flex-direction:column;gap:8px">
            <${ReportRow}
              label="Shadow action match"
              value=${html`<span style=${toneForPercent(agenticSnapshot.shadow_action_match_pct, { watchBelow: 95, dangerBelow: 90 })}>${metricPercent(agenticSnapshot.shadow_action_match_pct)}</span>`}
              detail=${`${Number(agenticSnapshot.shadow_scored_items || 0).toLocaleString()} scored records · ${Number(agenticSnapshot.shadow_disagreement_count || 0).toLocaleString()} disagreements`}
            />
            <${ReportRow}
              label="Critical field match"
              value=${html`<span style=${toneForPercent(agenticSnapshot.shadow_critical_field_match_pct, { watchBelow: 97, dangerBelow: 92 })}>${metricPercent(agenticSnapshot.shadow_critical_field_match_pct)}</span>`}
              detail="Amount, currency, invoice #, vendor, and document type"
            />
            <${ReportRow}
              label="Post verification rate"
              value=${html`<span style=${toneForPercent(agenticSnapshot.post_verification_rate_pct, { watchBelow: 100, dangerBelow: 95 })}>${metricPercent(agenticSnapshot.post_verification_rate_pct)}</span>`}
              detail=${`${Number(agenticSnapshot.post_verification_attempted_count || 0).toLocaleString()} posted attempts · ${Number(agenticSnapshot.post_verification_mismatch_count || 0).toLocaleString()} mismatches`}
            />
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Start from the right view</h3>
          <p class="muted" style="margin:0 0 12px">Use reports to spot a problem, then jump into the matching queue view.</p>
          <div style="display:flex;flex-direction:column;gap:10px">
            ${starterViews.slice(0, 4).map((view) => html`
              <div key=${view.id} style="display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
                <div>
                  <strong style="display:block;font-size:13px">${view.name}</strong>
                  <span class="muted" style="font-size:12px">${view.description}</span>
                </div>
                <button class="btn-secondary btn-sm" onClick=${() => openStarterView(view)}>Open view</button>
              </div>
            `)}
          </div>
        </div>
      </div>
    </div>
  `;
}
