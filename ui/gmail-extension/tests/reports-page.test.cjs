const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('reports helpers normalize pilot scorecard data from KPI payloads and bootstrap fallback', async () => {
  const { getPilotScorecardSummary } = await importModule('src/routes/pages/ReportsPage.js');

  const fromKpis = getPilotScorecardSummary(
    {
      pilot_scorecard: {
        summary: {
          touchless_rate_pct: 62.5,
          avg_cycle_time_hours: 18.2,
          on_time_approvals_pct: 88.4,
          avg_approval_wait_hours: 6.3,
          approval_sla_breached_open_count: 2,
          entity_route_needs_review_count: 3,
        },
        approval_workflow: {
          escalated_open_count: 4,
          reassigned_open_count: 1,
        },
        entity_routing: {
          manual_resolution_event_count_30d: 6,
        },
        highlights: ['2 approvals are currently beyond the 4-hour SLA.'],
      },
    },
    {},
  );

  assert.equal(fromKpis.touchlessRatePct, 62.5);
  assert.equal(fromKpis.avgCycleTimeHours, 18.2);
  assert.equal(fromKpis.onTimeApprovalsPct, 88.4);
  assert.equal(fromKpis.approvalEscalatedOpenCount, 4);
  assert.equal(fromKpis.entityRouteManualResolutionCount30d, 6);
  assert.deepEqual(fromKpis.highlights, ['2 approvals are currently beyond the 4-hour SLA.']);

  const fromBootstrap = getPilotScorecardSummary(
    {},
    {
      pilot_snapshot: {
        touchless_rate_pct: 41.0,
        avg_cycle_time_hours: 27.5,
        on_time_approvals_pct: 70.0,
        avg_approval_wait_hours: 11.5,
        approval_sla_breached_open_count: 5,
        approval_escalated_open_count: 2,
        approval_reassigned_open_count: 1,
        entity_route_needs_review_count: 4,
        entity_route_manual_resolution_count_30d: 3,
        highlights: ['4 invoices are waiting on manual entity routing review.'],
      },
    },
  );

  assert.equal(fromBootstrap.touchlessRatePct, 41.0);
  assert.equal(fromBootstrap.avgCycleTimeHours, 27.5);
  assert.equal(fromBootstrap.approvalEscalatedOpenCount, 2);
  assert.equal(fromBootstrap.approvalReassignedOpenCount, 1);
  assert.equal(fromBootstrap.entityRouteNeedsReviewCount, 4);
  assert.equal(fromBootstrap.entityRouteManualResolutionCount30d, 3);
  assert.deepEqual(fromBootstrap.highlights, ['4 invoices are waiting on manual entity routing review.']);
});

test('reports helpers normalize operator pressure metrics from KPI payloads', async () => {
  const { getOperatorPressureSummary } = await importModule('src/routes/pages/ReportsPage.js');

  const summary = getOperatorPressureSummary({
    operator_metrics: {
      activity_window_days: 30,
      live_queue: {
        approval_queue_count: 7,
        approval_sla_breached_open_count: 3,
        approval_escalated_open_count: 2,
        approval_reassigned_open_count: 1,
        entity_route_needs_review_count: 4,
        field_review_open_count: 5,
      },
      queue_rates: {
        approval_sla_breached_open_rate: 0.4286,
        entity_route_needs_review_rate: 0.25,
      },
      activity: {
        approval_escalation_event_count: 9,
        approval_reassignment_event_count: 6,
        entity_route_resolution_event_count: 8,
      },
    },
  });

  assert.equal(summary.approvalQueueCount, 7);
  assert.equal(summary.approvalSlaBreachedOpenCount, 3);
  assert.equal(summary.approvalEscalatedOpenCount, 2);
  assert.equal(summary.approvalReassignedOpenCount, 1);
  assert.equal(summary.entityRouteNeedsReviewCount, 4);
  assert.equal(summary.fieldReviewOpenCount, 5);
  assert.equal(summary.approvalSlaBreachedOpenRatePct, 42.86);
  assert.equal(summary.entityRouteNeedsReviewRatePct, 25);
  assert.equal(summary.approvalEscalationEventCount, 9);
  assert.equal(summary.approvalReassignmentEventCount, 6);
  assert.equal(summary.entityRouteResolutionEventCount, 8);
  assert.equal(summary.activityWindowDays, 30);
});
