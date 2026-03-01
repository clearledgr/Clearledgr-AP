const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const { loadInboxSdkLayerTestFns, SOURCE_PATH } = require('./inboxsdk-layer-harness.cjs');

const fns = loadInboxSdkLayerTestFns();

test('AX1 timeline groups render agent + audit entries into bucketed HTML', () => {
  const agentEvents = [
    {
      command_id: 'cmd-1',
      status: 'failed',
      tool_name: 'read_page',
      result_payload: { error: 'runtime_message_timeout' },
      updated_at: '2026-02-26T10:15:00Z',
      request_payload: { step: 'Read invoice email' },
    },
    {
      command_id: 'cmd-2',
      status: 'completed',
      tool_name: 'capture_evidence',
      updated_at: '2026-02-26T10:18:00Z',
      request_payload: { step: 'Capture fallback evidence' },
    },
  ];
  const auditEvents = [
    {
      id: 'audit-1',
      event_type: 'state_transition',
      from_state: 'approved',
      to_state: 'failed_post',
      created_at: '2026-02-26T10:16:00Z',
      decision_reason: 'erp_post_failed',
    },
    {
      id: 'audit-2',
      event_type: 'channel_action_processed',
      created_at: '2026-02-26T10:14:00Z',
      payload_json: JSON.stringify({ status: 'approved' }),
    },
  ];

  const entries = fns.buildAgentTimelineEntries(agentEvents, auditEvents, { maxEntries: 10 });
  const html = fns.renderAgentTimelineGroups(entries, { auditLoading: false });

  assert.ok(entries.length >= 3);
  assert.match(html, /Blocked \/ failed/);
  assert.match(html, /Executing|Completed/);
  assert.match(html, /data-source="agent"/);
  assert.match(html, /data-source="audit"/);
  assert.match(html, /Read invoice email|Read source email/);
  assert.match(html, /State: Approved -&gt; Failed post/);
});

test('AX5 fallback audit events render explicit timeline stages and fallback status banner', () => {
  const auditEvents = [
    {
      id: 'fallback-preview',
      event_type: 'erp_api_fallback_preview_created',
      created_at: '2026-02-26T10:20:00Z',
      payload_json: JSON.stringify({
        command_count: 3,
        requires_confirmation_count: 1,
        session_id: 'sess-fallback-1',
      }),
    },
    {
      id: 'fallback-confirm',
      event_type: 'erp_api_fallback_confirmation_captured',
      created_at: '2026-02-26T10:21:00Z',
      payload_json: JSON.stringify({
        required_count: 1,
        confirmed_count: 1,
        session_id: 'sess-fallback-1',
      }),
    },
    {
      id: 'fallback-requested',
      event_type: 'erp_api_fallback_requested',
      created_at: '2026-02-26T10:22:00Z',
      payload_json: JSON.stringify({
        api_status: 'error',
        api_reason: 'connector_timeout',
        fallback: {
          requested: true,
          reason: 'fallback_preview_confirmed_and_dispatched',
          session_id: 'sess-fallback-1',
          queued: 2,
          blocked: 0,
          denied: 0,
          dispatch_status: 'submitted',
        },
      }),
    },
    {
      id: 'fallback-complete',
      event_type: 'erp_browser_fallback_completed',
      created_at: '2026-02-26T10:23:00Z',
      payload_json: JSON.stringify({
        session_id: 'sess-fallback-1',
        erp_reference: 'ERP-123',
        evidence: { screenshot: true, page_url: true },
      }),
    },
  ];

  const entries = fns.buildAgentTimelineEntries([], auditEvents, { maxEntries: 10 });
  const html = fns.renderAgentTimelineGroups(entries, { auditLoading: false });
  assert.match(html, /Browser fallback preview generated/);
  assert.match(html, /Browser fallback confirmation captured/);
  assert.match(html, /Browser fallback runner executing/);
  assert.match(html, /Browser fallback completed/);
  assert.match(html, /S2\/5/);
  assert.match(html, /S5\/5/);

  const summary = fns.buildBrowserFallbackStatusSummary(
    { state: 'posted_to_erp', erp_reference: 'ERP-123' },
    { erp: { erp_reference: 'ERP-123' } },
    auditEvents
  );
  assert.ok(summary);
  assert.equal(summary.tone, 'success');
  assert.match(summary.title, /Browser fallback completed/i);
  assert.ok(summary.meta.some((line) => line.includes('ERP ref: ERP-123')));
  assert.equal(summary.stageIndex, 5);
  assert.equal(summary.stageTotal, 5);
  assert.match(summary.trustNote, /reconciled/i);

  const bannerHtml = fns.renderBrowserFallbackStatusBannerHtml(summary);
  assert.match(bannerHtml, /Browser fallback/);
  assert.match(bannerHtml, /Stage 5 of 5/);
  assert.match(bannerHtml, /ERP ref: ERP-123/);
  assert.match(bannerHtml, /Runner completion is reconciled/i);
});

test('AX5 web-context browser events use human-readable labels and detail text', () => {
  const event = {
    tool_name: 'capture_evidence',
    status: 'completed',
    ts: '2026-02-26T11:11:00Z',
    result: {
      summary: 'Captured fallback confirmation screenshot for ERP posting',
      erp_reference: 'ERP-XYZ',
    },
  };
  const view = fns.describeBrowserContextEvent(event);
  assert.equal(view.title, 'Capture evidence');
  assert.equal(view.status, 'Completed');
  assert.match(view.detail, /fallback confirmation screenshot/i);
  assert.match(view.timeLabel, /\d/);
  assert.equal(view.fallbackRelated, true);
  assert.equal(view.statusTone, 'success');
});

test('AX2 recommendations include preview-first finance summary share and command parser maps phrase', () => {
  const item = {
    state: 'failed_post',
    next_action: 'retry_posting',
    exception_code: 'erp_post_failed',
    requires_field_review: false,
    confidence_blockers: [],
  };

  const recs = fns.buildAgentIntentRecommendations(item, {
    canRetryPostMacro: true,
    canRunCollectW9: true,
    canRouteApproval: false,
    canNudgeApprovers: false,
    canSummarizeBlockers: true,
    canDraftVendorReply: false,
    canSummarizeFinanceLead: true,
    canShareFinanceSummary: true,
  });

  const intents = new Set(recs.actions.map((a) => a.intent));
  assert.ok(intents.has('preview_finance_summary_share'));
  assert.ok(intents.has('share_finance_summary'));

  const parsed = fns.parseAgentIntentCommand('preview finance summary for finance lead', {
    availableIntents: intents,
  });
  assert.equal(parsed.intent, 'preview_finance_summary_share');
});

test('AX3 finance summary preview card builds target-specific payload metadata and summary HTML renders preview payload', () => {
  const result = {
    status: 'preview',
    target: 'slack_thread',
    summary: {
      title: 'Finance lead exception summary',
      lines: [
        'Acme Corp · Invoice INV-123 · USD 125.00',
        'Current state: failed post · Next action: retry posting',
      ],
    },
    preview: {
      kind: 'slack_thread',
      channel_id: 'C123',
      thread_ts: '123.456',
      text: '*Finance lead exception summary*\n• Current state: failed post',
    },
    audit_event_id: 'audit-99',
  };

  const card = fns.buildFinanceSummarySharePreviewCard(result, 'slack_thread');
  assert.equal(card.kind, 'finance_share_preview');
  assert.match(card.title, /slack thread/);
  assert.ok(card.lines.some((line) => line.includes('Slack thread target: C123')));
  assert.match(card.previewText, /Finance lead exception summary/);

  const html = fns.renderAgentSummaryCardHtml(card);
  assert.match(html, /cl-agent-preview-payload/);
  assert.match(html, /Slack thread target: C123/);
  assert.match(html, /Finance lead exception summary/);
});

test('timeline renderer shows loading and empty states', () => {
  assert.match(
    fns.renderAgentTimelineGroups([], { auditLoading: true }),
    /Loading timeline breadcrumbs/
  );
  assert.match(
    fns.renderAgentTimelineGroups([], { auditLoading: false }),
    /No agent timeline events yet/
  );
});

test('AX4 batch snapshot filters low-risk ready, failed-post previews, and aging approval nudges', () => {
  const items = [
    {
      id: 'ready-1',
      state: 'ready_to_post',
      vendor_name: 'Acme Corp',
      invoice_number: 'INV-1001',
      amount: 200,
      currency: 'USD',
      updated_at: '2026-02-24T10:00:00Z',
    },
    {
      id: 'ready-2',
      state: 'ready_to_post',
      vendor_name: 'Risky Vendor',
      invoice_number: 'INV-1002',
      amount: 500,
      currency: 'USD',
      requires_field_review: true,
      confidence_blockers: [{ field: 'amount' }],
      updated_at: '2026-02-24T11:00:00Z',
    },
    {
      id: 'fail-1',
      state: 'failed_post',
      vendor_name: 'ERP Fail Co',
      invoice_number: 'INV-2001',
      amount: 120,
      currency: 'USD',
      updated_at: '2026-02-25T12:00:00Z',
    },
    {
      id: 'approval-1',
      state: 'needs_approval',
      vendor_name: 'Pending Co',
      invoice_number: 'INV-3001',
      amount: 90,
      currency: 'USD',
      updated_at: '2026-02-24T06:00:00Z',
    },
    {
      id: 'approval-2',
      state: 'pending_approval',
      vendor_name: 'Unknown Age Co',
      invoice_number: 'INV-3002',
      amount: 190,
      currency: 'USD',
    },
  ];
  const sessions = new Map([
    ['ready-1', { session: { id: 'sess-ready-1' } }],
    ['fail-1', { session: { id: 'sess-fail-1' } }],
    ['approval-1', { session: { id: 'sess-approval-1' } }],
  ]);

  const snapshot = fns.buildBatchAgentOpsSnapshot(items, sessions, {
    nowMs: Date.parse('2026-02-26T12:00:00Z'),
    agingApprovalHours: 24,
    previewLimit: 3,
  });

  assert.equal(snapshot.lowRiskReady.count, 2);
  assert.equal(snapshot.lowRiskReady.runnableCount, 1);
  assert.equal(snapshot.lowRiskReady.blockedCount, 1);
  assert.equal(snapshot.lowRiskReady.withSessionCount, 1);

  assert.equal(snapshot.failedPostRetryPreview.count, 1);
  assert.equal(snapshot.failedPostRetryPreview.withSessionCount, 1);

  assert.equal(snapshot.nudgeAgingApprovals.count, 2);
  assert.ok(snapshot.nudgeAgingApprovals.previewItems.length >= 1);

  const previewCard = fns.buildBatchOpsPreviewCard('process_low_risk_ready', {
    ...snapshot.lowRiskReady,
    agingApprovalHours: snapshot.agingApprovalHours,
  });
  assert.match(previewCard.title, /low-risk ready items/i);
  assert.ok(previewCard.lines.some((line) => line.includes('ready-to-post item')));

  const policy = fns.normalizeBatchOpsPolicyConfig({ maxItems: 3, amountThreshold: '150' });
  const filteredRetry = fns.applyBatchPolicyToGroup(snapshot.failedPostRetryPreview, policy, { previewLimit: 2 });
  assert.equal(filteredRetry.selectedCount, 1);
  assert.equal(filteredRetry.policyAmountExcludedCount, 0);
  assert.ok(filteredRetry.policySummary.includes('Policy: top 3'));

  const oldestPolicy = fns.normalizeBatchOpsPolicyConfig({ maxItems: 3, selectionPreset: 'oldest_first' });
  const filteredApprovalsOldest = fns.applyBatchPolicyToGroup(snapshot.nudgeAgingApprovals, oldestPolicy, { previewLimit: 2 });
  assert.equal(filteredApprovalsOldest.selectedItems[0].id, 'approval-1');
  assert.ok(filteredApprovalsOldest.policySummary.includes('preset oldest first'));

  const lowRiskPolicy = fns.normalizeBatchOpsPolicyConfig({ maxItems: 3, selectionPreset: 'lowest_risk_first' });
  const filteredReadyLowRisk = fns.applyBatchPolicyToGroup(snapshot.lowRiskReady, lowRiskPolicy, { previewLimit: 2 });
  assert.equal(filteredReadyLowRisk.selectedItems[0].id, 'ready-1');
  assert.ok(filteredReadyLowRisk.policySummary.includes('preset lowest risk first'));

  const refreshSummary = fns.buildBatchRefreshIndicator('retry_failed_posts_preview', ['fail-1'], [
    { id: 'fail-1', state: 'posted_to_erp' },
  ]);
  assert.match(refreshSummary, /1 posted/);

  const retryRunCard = fns.buildBatchOpsRunResultCard('retry_failed_posts_preview', {
    attempted: 3,
    successCount: 1,
    partialCount: 1,
    failureCount: 1,
    skippedCount: 0,
    items: [
      { itemId: 'fail-1', ok: true, status: 'posted', label: 'ERP Fail Co · INV-2001', detail: 'ERP ref ERP-1', retryable: false },
      { itemId: 'fail-2', partial: true, status: 'ready_to_post', label: 'ERP Hold Co · INV-2002', detail: 'Adapter unavailable', retryable: false },
      { itemId: 'fail-3', ok: false, status: 'error', label: 'ERP Hard Fail · INV-2003', detail: 'connector timeout', retryable: true },
    ],
  });
  assert.match(retryRunCard.title, /failed post retries/i);
  assert.ok(retryRunCard.lines.some((line) => line.includes('1 posted, 1 re-queued, 1 failed')));
  assert.equal(retryRunCard.kind, 'batch_run_result');
  assert.ok(Array.isArray(retryRunCard.detailItems));
  assert.ok(retryRunCard.detailItems.some((item) => String(item.detail).includes('ERP ref ERP-1')));
  assert.ok(Array.isArray(retryRunCard.actions));
  assert.ok(retryRunCard.actions.some((action) => action.id === 'rerun_failed_subset'));

  const retryRunHtml = fns.renderAgentSummaryCardHtml(retryRunCard);
  assert.match(retryRunHtml, /Show item results/);
  assert.match(retryRunHtml, /cl-batch-result-status-success/);
  assert.match(retryRunHtml, /Successful \(1\)/);
  assert.match(retryRunHtml, /Needs follow-up \(1\)/);
  assert.match(retryRunHtml, /Failed \(1\)/);
  assert.match(retryRunHtml, /Rerun failed subset/);
});

test('A2 batch intents expose deterministic selected/excluded reason sets', () => {
  const items = [
    {
      id: 'needs-info-eligible',
      state: 'needs_info',
      vendor_name: 'Vendor One',
      invoice_number: 'INV-N1',
      amount: 100,
      followup_next_action: 'prepare_vendor_followup_draft',
      next_action: 'prepare_vendor_followup_draft',
    },
    {
      id: 'needs-info-waiting',
      state: 'needs_info',
      vendor_name: 'Vendor Two',
      invoice_number: 'INV-N2',
      amount: 120,
      followup_next_action: 'await_vendor_response',
      next_action: 'await_vendor_response',
    },
    {
      id: 'validated-eligible',
      state: 'validated',
      vendor_name: 'Vendor Three',
      invoice_number: 'INV-V1',
      amount: 130,
      document_type: 'invoice',
      next_action: 'route_for_approval',
    },
    {
      id: 'validated-blocked',
      state: 'validated',
      vendor_name: 'Vendor Four',
      invoice_number: 'INV-V2',
      amount: 140,
      exception_code: 'policy_validation_failed',
      next_action: 'review_exception',
    },
    {
      id: 'failed-recoverable',
      state: 'failed_post',
      vendor_name: 'Vendor Five',
      invoice_number: 'INV-F1',
      amount: 150,
      last_error: 'connector timeout while posting',
      next_action: 'retry_post',
    },
    {
      id: 'failed-blocked',
      state: 'failed_post',
      vendor_name: 'Vendor Six',
      invoice_number: 'INV-F2',
      amount: 160,
      last_error: 'duplicate invoice already posted',
      next_action: 'retry_post',
    },
  ];

  const snapshot = fns.buildBatchAgentOpsSnapshot(items, new Map(), {
    nowMs: Date.parse('2026-02-27T12:00:00Z'),
    agingApprovalHours: 24,
    previewLimit: 4,
  });
  const policy = fns.normalizeBatchOpsPolicyConfig({ maxItems: 5, selectionPreset: 'queue_order' });

  const followup = fns.applyBatchPolicyToGroup(snapshot.prepareVendorFollowups, policy, { previewLimit: 4 });
  assert.equal(followup.selectedCount, 1);
  assert.equal(followup.excludedDetails.length, 1);
  assert.ok(followup.excludedDetails[0].reasons.some((reason) => String(reason).includes('precheck')));

  const routeApproval = fns.applyBatchPolicyToGroup(snapshot.routeLowRiskForApproval, policy, { previewLimit: 4 });
  assert.equal(routeApproval.selectedCount, 1);
  assert.equal(routeApproval.excludedDetails.length, 1);
  assert.ok(routeApproval.excludedReasonCounts['precheck:policy_validation_failed'] >= 1);

  const retryRecoverable = fns.applyBatchPolicyToGroup(snapshot.retryRecoverableFailures, policy, { previewLimit: 4 });
  assert.equal(retryRecoverable.selectedCount, 1);
  assert.equal(retryRecoverable.excludedDetails.length, 1);
  assert.ok(Object.keys(retryRecoverable.excludedReasonCounts).some((key) => key.startsWith('precheck:')));

  const previewCard = fns.buildBatchOpsPreviewCard('retry_recoverable_failures', retryRecoverable);
  assert.match(previewCard.title, /retry recoverable failures/i);
  assert.ok(previewCard.lines.some((line) => /Selected reason:/i.test(line)));
  assert.ok(previewCard.lines.some((line) => /Excluded reason:/i.test(line)));
});

test('reason sheet defaults enforce required reasons for reject/override and optional notes for routing', () => {
  const rejectDefaults = fns.getReasonSheetDefaults('reject');
  assert.equal(rejectDefaults.required, true);
  assert.ok(Array.isArray(rejectDefaults.chips));
  assert.ok(rejectDefaults.chips.length >= 3);

  const overrideDefaults = fns.getReasonSheetDefaults('approve_override');
  assert.equal(overrideDefaults.required, true);
  assert.ok(overrideDefaults.chips.some((chip) => /Policy exception approved/i.test(String(chip))));

  const routeDefaults = fns.getReasonSheetDefaults('approval_route');
  assert.equal(routeDefaults.required, false);
  assert.ok(routeDefaults.chips.some((chip) => /SLA/i.test(String(chip))));
});

test('reason capture path contains no native prompt/confirm calls', () => {
  const source = fs.readFileSync(SOURCE_PATH, 'utf8');
  assert.doesNotMatch(source, /\bprompt\s*\(/);
  assert.doesNotMatch(source, /\bconfirm\s*\(/);
});

test('work audit presentation maps validation failures to safe operator fallback copy', () => {
  const view = fns.getWorkAuditPresentation(
    {
      event_type: 'deterministic_validation_failed',
      decision_reason: 'policy_requirement_amt_500,po_match_no_gr,confidence_field_review_required',
      created_at: '2026-03-01T01:00:00Z',
    },
    { state: 'needs_approval' }
  );
  assert.equal(view.title, 'Validation checks failed');
  assert.match(view.detail, /require review before continuing/i);
  assert.doesNotMatch(view.detail, /policy_requirement_amt_500/i);
});

test('work audit presentation maps blocked retry/transition events to plain safety copy', () => {
  const blockedRetry = fns.getWorkAuditPresentation(
    {
      event_type: 'state_transition_rejected',
      decision_reason: 'autonomous_retry_attempt',
      created_at: '2026-03-01T01:05:00Z',
    },
    { state: 'needs_approval' }
  );
  const blockedIllegal = fns.getWorkAuditPresentation(
    {
      event_type: 'state_transition_rejected',
      decision_reason: 'illegal_transition',
      created_at: '2026-03-01T01:06:00Z',
    },
    { state: 'needs_approval' }
  );
  assert.equal(blockedRetry.title, 'Action blocked for safety');
  assert.match(blockedRetry.detail, /requested action is not allowed from the current invoice status/i);
  assert.equal(blockedIllegal.title, 'Action blocked for safety');
  assert.match(blockedIllegal.detail, /not allowed from the current invoice status/i);
});

test('work audit list contract does not enforce nested max-height/overflow viewport', () => {
  const source = fs.readFileSync(SOURCE_PATH, 'utf8');
  assert.match(source, /\.cl-audit-list\s*\{[^}]*display:\s*flex;/);
  assert.doesNotMatch(source, /\.cl-audit-list\s*\{[^}]*max-height\s*:/);
  assert.doesNotMatch(source, /\.cl-audit-list\s*\{[^}]*overflow(?:-y)?\s*:/);
});
