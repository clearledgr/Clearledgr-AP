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
  return html`<div class="reports-metric-card">
    <div class="reports-metric-value">${value}</div>
    <div class="reports-metric-label">${label}</div>
    ${detail ? html`<div class="reports-metric-detail">${detail}</div>` : null}
  </div>`;
}

function ReportRow({ label, value, detail }) {
  return html`<div class="reports-row">
    <div class="reports-row-copy">
      <strong>${label}</strong>
      ${detail ? html`<span>${detail}</span>` : null}
    </div>
    <div class="reports-row-value">${value}</div>
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

export function getProofScorecardSummary(kpis = {}, dashboard = {}) {
  const proof = kpis?.proof_scorecard || {};
  const summary = proof?.summary || {};
  const decisions = proof?.decisions || {};
  const followup = proof?.approval_followup || {};
  const posting = proof?.posting_reliability || {};
  const recovery = proof?.recovery || {};
  const bootstrap = dashboard?.proof_snapshot || {};
  const highlights = Array.isArray(proof?.highlights) && proof.highlights.length
    ? proof.highlights
    : (Array.isArray(bootstrap?.highlights) ? bootstrap.highlights : []);

  return {
    autoApprovedRatePct: safeNumber(summary?.auto_approved_rate_pct, safeNumber(bootstrap?.auto_approved_rate_pct)),
    humanOverrideRatePct: safeNumber(summary?.human_override_rate_pct, safeNumber(bootstrap?.human_override_rate_pct)),
    avgApprovalWaitHours: safeNumber(summary?.avg_approval_wait_hours, safeNumber(bootstrap?.avg_approval_wait_hours)),
    escalationRatePct: safeNumber(summary?.escalation_rate_pct, safeNumber(bootstrap?.escalation_rate_pct)),
    postingSuccessRatePct: safeNumber(summary?.posting_success_rate_pct, safeNumber(bootstrap?.posting_success_rate_pct)),
    recoverySuccessRatePct: safeNumber(summary?.recovery_success_rate_pct, safeNumber(bootstrap?.recovery_success_rate_pct)),
    humanOverrideCount: safeNumber(decisions?.human_override_count, safeNumber(bootstrap?.human_override_count)),
    decisionCount: safeNumber(decisions?.decision_count, safeNumber(bootstrap?.decision_count)),
    escalationEventCount30d: safeNumber(followup?.escalation_event_count_30d, safeNumber(bootstrap?.escalation_event_count_30d)),
    postingAttemptCount: safeNumber(posting?.attempted_count, safeNumber(bootstrap?.posting_attempt_count)),
    postingMismatchCount: safeNumber(posting?.mismatch_count, safeNumber(bootstrap?.posting_mismatch_count)),
    recoveryAttemptCount: safeNumber(recovery?.attempted_count, safeNumber(bootstrap?.recovery_attempt_count)),
    recoveredCount: safeNumber(recovery?.recovered_count, safeNumber(bootstrap?.recovered_count)),
    highlights: highlights.map((entry) => String(entry || '').trim()).filter(Boolean).slice(0, 4),
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
    navigate('clearledgr/invoices');
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
  const proofSummary = getProofScorecardSummary(reportData?.kpis || {}, dashboard);

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
        <button class="btn-primary btn-sm" onClick=${() => navigate('clearledgr/invoices')}>Open invoices</button>
      </div>
    </div>

    <div class="kpi-row">
      <${MetricCard} label="Tracked invoices" value=${Number(totals.items || dashboard.total_invoices || 0).toLocaleString()} />
      <${MetricCard} label="Open items" value=${Number(totals.open_items || 0).toLocaleString()} detail=${`${Number(dashboard.pending_approval || 0).toLocaleString()} waiting approval`} />
      <${MetricCard} label="Tracked spend" value=${fmtDollar(totals.total_amount || 0)} detail=${`${Number(totals.amount_unavailable_count || 0).toLocaleString()} without amount`} />
      <${MetricCard} label="Duplicate clusters" value=${Number(duplicates.cluster_count || 0).toLocaleString()} detail=${`${Number(duplicates.duplicate_invoice_count || 0).toLocaleString()} duplicate invoices`} />
    </div>

    <div class="reports-shell">
      <div class="reports-main-stack">
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
          <h3 style="margin-top:0">Pilot scorecard</h3>
          <p class="muted" style="margin:0 0 12px">
            Track whether Clearledgr is actually removing approval chasing and manual routing work.
          </p>
          <div class="secondary-stat-grid">
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
          <h3 style="margin-top:0">Proof scorecard</h3>
          <p class="muted" style="margin:0 0 12px">
            Show whether Clearledgr is becoming safer, faster, and more reliable under real AP work.
          </p>
          <div class="secondary-stat-grid">
            <${MetricCard}
              label="Auto-approved rate"
              value=${html`<span style=${toneForPercent(proofSummary.autoApprovedRatePct, { watchBelow: 70, dangerBelow: 50 })}>${metricPercent(proofSummary.autoApprovedRatePct)}</span>`}
            />
            <${MetricCard}
              label="Human override rate"
              value=${html`<span style=${toneForPercent(100 - proofSummary.humanOverrideRatePct, { watchBelow: 85, dangerBelow: 70 })}>${metricPercent(proofSummary.humanOverrideRatePct)}</span>`}
            />
            <${MetricCard}
              label="Approval latency"
              value=${metricHours(proofSummary.avgApprovalWaitHours)}
            />
            <${MetricCard}
              label="Escalation rate"
              value=${html`<span style=${toneForPercent(100 - proofSummary.escalationRatePct, { watchBelow: 88, dangerBelow: 75 })}>${metricPercent(proofSummary.escalationRatePct)}</span>`}
            />
            <${MetricCard}
              label="Posting success rate"
              value=${html`<span style=${toneForPercent(proofSummary.postingSuccessRatePct, { watchBelow: 98, dangerBelow: 92 })}>${metricPercent(proofSummary.postingSuccessRatePct)}</span>`}
            />
            <${MetricCard}
              label="Recovery success rate"
              value=${html`<span style=${toneForPercent(proofSummary.recoverySuccessRatePct, { watchBelow: 85, dangerBelow: 70 })}>${metricPercent(proofSummary.recoverySuccessRatePct)}</span>`}
            />
          </div>

          <div style="display:flex;flex-direction:column;gap:8px;margin-top:14px">
            <${ReportRow}
              label="Override evidence"
              value=${`${Number(proofSummary.humanOverrideCount || 0).toLocaleString()} / ${Number(proofSummary.decisionCount || 0).toLocaleString()}`}
              detail="Human overrides divided by decision windows with a persisted Claude recommendation"
            />
            <${ReportRow}
              label="Posting reliability"
              value=${`${Number(proofSummary.postingAttemptCount || 0).toLocaleString()} attempts`}
              detail=${`${Number(proofSummary.postingMismatchCount || 0).toLocaleString()} verification mismatches after posting`}
            />
            <${ReportRow}
              label="Recovery evidence"
              value=${`${Number(proofSummary.recoveredCount || 0).toLocaleString()} / ${Number(proofSummary.recoveryAttemptCount || 0).toLocaleString()}`}
              detail="Recovered ERP posting failures over total recovery attempts"
            />
            <${ReportRow}
              label="Escalation evidence"
              value=${Number(proofSummary.escalationEventCount30d || 0).toLocaleString()}
              detail="Approval escalation events in the last 30 days"
            />
          </div>

          ${proofSummary.highlights.length > 0 && html`
            <div style="margin-top:14px">
              <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;margin-bottom:8px">Current proof readout</div>
              <div class="secondary-list">
                ${proofSummary.highlights.map((line) => html`
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
          <h3 style="margin-top:0">Export reports</h3>
          <p class="muted" style="margin:0 0 12px">Download AP data as CSV for month-end close, audits, or reconciliation.</p>
          <div style="display:flex;flex-direction:column;gap:12px">
            <div>
              <${ExportButton} api=${api} reportType="ap_aging" label="AP Aging Report" description="Open payables by aging bucket (current, 30, 60, 90+ days)" />
              <${SheetsExportButton} api=${api} reportType="ap_aging" label="AP Aging" />
            </div>
            <div>
              <${ExportButton} api=${api} reportType="vendor_spend" label="Vendor Spend Report" description="Top vendors, GL categories, and monthly trends" />
              <${SheetsExportButton} api=${api} reportType="vendor_spend" label="Vendor Spend" />
            </div>
            <div>
              <${ExportButton} api=${api} reportType="posting_status" label="Posting Status Report" description="AP items with posting timing and ERP references" />
              <${SheetsExportButton} api=${api} reportType="posting_status" label="Posting Status" />
            </div>
          </div>
        </div>

        <${SpendAnalysisPanel} api=${api} />
      </div>

      <div class="reports-side-stack">
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
              <div class="reports-chip-wrap">
                ${sourceTypes.map(([sourceType, count]) => html`
                  <span key=${sourceType} class="secondary-chip">
                    ${sourceType} ${count}
                  </span>
                `)}
              </div>
            </div>
          `}
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
          <div class="secondary-card-list">
            ${starterViews.slice(0, 4).map((view) => html`
              <div key=${view.id} class="reports-export-row">
                <div>
                  <strong style="display:block;font-size:13px">${view.name}</strong>
                  <span class="muted" style="font-size:12px">${view.description}</span>
                </div>
                <button class="btn-secondary btn-sm" onClick=${() => openStarterView(view)}>Open view</button>
              </div>
            `)}
          </div>
        </div>

        <${AgingPanel} api=${api} />
        <${PeriodClosePanel} api=${api} />
        <${TaxCompliancePanel} api=${api} />
      </div>
    </div>
  `;
}

function SheetsExportButton({ api, reportType, label }) {
  const [sheetUrl, setSheetUrl] = useState('');
  const [exporting, setExporting] = useState(false);
  const [result, setResult] = useState(null);
  const doExport = async () => {
    if (!sheetUrl.trim()) return;
    setExporting(true);
    setResult(null);
    try {
      const res = await api('/api/workspace/reports/export-to-sheets', {
        method: 'POST',
        body: JSON.stringify({ spreadsheet_url: sheetUrl.trim(), report_type: reportType }),
      });
      setResult(res?.ok ? `${res.rows_written} rows written to "${res.sheet_name}"` : (res?.error || 'Export failed'));
    } catch (e) { setResult('Export failed'); }
    setExporting(false);
  };
  return html`
    <div class="secondary-inline-actions" style="margin-top:4px">
      <input type="text" placeholder="Paste Google Sheets URL..." value=${sheetUrl} onInput=${(e) => setSheetUrl(e.target.value)}
        style="flex:1;padding:5px 8px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:11px" />
      <button class="btn-secondary btn-sm" onClick=${doExport} disabled=${exporting || !sheetUrl.trim()}>
        ${exporting ? '...' : 'Push to Sheets'}
      </button>
    </div>
    ${result && html`<div class="reports-inline-result">${result}</div>`}
  `;
}

function ExportButton({ api, reportType, label, description }) {
  const [downloading, setDownloading] = useState(false);
  const doExport = async () => {
    setDownloading(true);
    try {
      const resp = await api(`/api/workspace/reports/export?report_type=${reportType}&format=csv`);
      if (resp && resp.ok !== false) {
        const blob = new Blob([typeof resp === 'string' ? resp : JSON.stringify(resp)], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${reportType}.csv`;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (e) { console.warn('Export failed:', e); }
    setDownloading(false);
  };
  return html`
    <div class="reports-export-row">
      <div>
        <strong style="display:block;font-size:13px">${label}</strong>
        <span class="muted" style="font-size:12px">${description}</span>
      </div>
      <button class="btn-secondary btn-sm" onClick=${doExport} disabled=${downloading}>${downloading ? 'Downloading...' : 'CSV'}</button>
    </div>
  `;
}

function SpendAnalysisPanel({ api }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    api('/api/workspace/spend-analysis?period_days=30').then(setData).catch(() => {});
  }, []);
  if (!data || !data.top_vendors?.length) return null;
  return html`
    <div class="panel">
      <h3 style="margin-top:0">Spend analysis (30 days)</h3>
      <div>
        ${data.top_vendors.slice(0, 6).map((v) => html`
          <${ReportRow} key=${v.vendor_name} label=${v.vendor_name} value=${fmtDollar(v.total_spend)} detail=${`${v.invoice_count} invoice${v.invoice_count !== 1 ? 's' : ''}`} />
        `)}
      </div>
      ${data.anomalies?.length > 0 && html`
        <div class="secondary-callout warning" style="margin-top:12px">
          ${data.anomalies.slice(0, 3).map((a) => html`<div key=${a.vendor}>${a.message}</div>`)}
        </div>
      `}
    </div>
  `;
}

function AgingPanel({ api }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    api('/api/ap/items/aging').then(setData).catch(() => {});
  }, []);
  if (!data || !data.summary) return null;
  const s = data.summary;
  return html`
    <div class="panel">
      <h3 style="margin-top:0">AP aging</h3>
      <div class="secondary-stat-grid" style="margin-bottom:8px">
        <${MetricCard} label="Open invoices" value=${s.total_open_count || 0} />
        <${MetricCard} label="Overdue" value=${s.overdue_count || 0} detail=${s.overdue_pct ? `${s.overdue_pct}% of open` : ''} />
      </div>
      ${s.weighted_avg_days_past_due != null && html`
        <div class="muted" style="font-size:12px;text-align:center">Weighted avg days past due: ${s.weighted_avg_days_past_due}</div>
      `}
    </div>
  `;
}

function PeriodClosePanel({ api }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    api('/api/workspace/period-close/current').then(setData).catch(() => {});
  }, []);
  if (!data) return null;
  return html`
    <div class="panel">
      <h3 style="margin-top:0">Period close</h3>
      <${ReportRow} label="Current period" value=${data.period} detail=${data.is_locked ? 'LOCKED' : `Closes ${data.closes_on}`} />
      ${data.in_closing_window && html`
        <div class="secondary-callout warning" style="margin-top:6px">
          Closing window — ${data.days_until_close} day${data.days_until_close !== 1 ? 's' : ''} until cutoff
        </div>
      `}
    </div>
  `;
}

function TaxCompliancePanel({ api }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    api('/api/workspace/tax-compliance/summary').then(setData).catch(() => {});
  }, []);
  if (!data || !data.vendor_count) return null;
  return html`
    <div class="panel">
      <h3 style="margin-top:0">Tax compliance</h3>
      <${ReportRow} label="Vendors tracked" value=${data.vendor_count} />
      ${data.missing_tax_id_count > 0 && html`
        <${ReportRow} label="Missing tax ID" value=${data.missing_tax_id_count} detail="Vendors without a validated tax ID" />
      `}
      ${data.invalid_tax_id_count > 0 && html`
        <${ReportRow} label="Invalid tax ID format" value=${data.invalid_tax_id_count} />
      `}
      ${data.reverse_charge_applicable?.length > 0 && html`
        <${ReportRow} label="Reverse charge applicable" value=${data.reverse_charge_applicable.length} detail="Intra-EU B2B transactions" />
      `}
      ${data.wht_applicable?.length > 0 && html`
        <${ReportRow} label="WHT applicable" value=${data.wht_applicable.length} detail="Withholding tax required" />
      `}
    </div>
  `;
}
