const test = require('node:test');
const assert = require('node:assert/strict');

const { createInboxSdkIntegrationRuntime } = require('./inboxsdk-integration-harness.cjs');

test('bootstrap wires InboxSDK handlers and mounts sidebar panel with core sections', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: true } });
  const { records, getState } = runtime;

  assert.equal(records.inboxSdkLoadCalls.length, 1);
  assert.equal(records.sidebarPanels.length, 1);
  assert.ok(records.sdkHandlers.compose);
  assert.ok(records.sdkHandlers.threadView);
  assert.ok(records.sdkHandlers.threadRowView);

  const panel = records.sidebarPanels[0];
  assert.equal(panel.title, 'Clearledgr');
  assert.ok(panel.el);
  const sidebar = getState().globalSidebarEl;
  assert.ok(sidebar);
  assert.ok(sidebar.querySelector('#cl-thread-context'));
  assert.ok(sidebar.querySelector('#cl-agent-actions'));
  assert.ok(sidebar.querySelector('#cl-audit-trail'));
});

test('sidebar button event wiring works (authorize + debug refresh/scan)', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: true } });
  const queueManager = runtime.getQueueManager();
  const sidebar = runtime.getState().globalSidebarEl;

  const authorizeBtn = sidebar.querySelector('#cl-authorize-gmail');
  const debugRefreshBtn = sidebar.querySelector('#cl-debug-refresh');
  const debugScanBtn = sidebar.querySelector('#cl-debug-scan');
  const toast = sidebar.querySelector('#cl-toast');

  assert.ok(authorizeBtn);
  assert.ok(debugRefreshBtn);
  assert.ok(debugScanBtn);

  await authorizeBtn.click();
  await runtime.flush();
  assert.equal(queueManager.calls.authorizeGmailNow, 1);
  assert.equal(queueManager.calls.refreshQueue, 1);
  assert.match(toast.textContent, /Gmail authorized/i);

  await debugRefreshBtn.click();
  await runtime.flush();
  assert.ok(queueManager.calls.refreshQueue >= 2);

  await debugScanBtn.click();
  await runtime.flush();
  assert.equal(queueManager.calls.scanNow, 1);
});

test('queue updates rerender status and Gmail SDK thread row labeling + thread lifecycle handlers work', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const queueManager = runtime.getQueueManager();
  const sidebar = runtime.getState().globalSidebarEl;

  queueManager.emitQueueUpdated([], { state: 'auth_required' });
  await runtime.flush();
  const scanStatusEl = sidebar.querySelector('#cl-scan-status');
  const authActionsEl = sidebar.querySelector('#cl-auth-actions');
  assert.match(scanStatusEl.textContent, /Authorize Gmail/i);
  assert.equal(authActionsEl.style.display, 'block');

  const item = {
    thread_id: 'thread-abc',
    state: 'needs_approval',
    vendor_name: 'Acme Corp',
    invoice_number: 'INV-1001',
    amount: 1250.5,
    currency: 'USD',
    subject: 'Invoice INV-1001',
    sender: 'billing@acme.example',
    metadata: {},
    confidence: 0.96,
    next_action: 'approve_or_reject',
  };
  queueManager.emitQueueUpdated([item], { state: 'idle', lastScanAt: '2026-02-26T10:00:00Z' });
  await runtime.flush();

  const rowHandler = runtime.records.sdkHandlers.threadRowView;
  assert.ok(rowHandler);
  const rowView = runtime.createThreadRowView('thread-abc');
  rowHandler(rowView);
  await runtime.flush();
  assert.equal(rowView.labels.length, 1);
  assert.equal(rowView.labels[0].title, 'Needs approval');

  const threadHandler = runtime.records.sdkHandlers.threadView;
  assert.ok(threadHandler);
  const threadView = runtime.createThreadView('thread-abc');
  threadHandler(threadView);
  await runtime.flush();
  assert.equal(runtime.getState().currentThreadId, 'thread-abc');

  await threadView.destroy();
  await runtime.flush();
  assert.equal(runtime.getState().currentThreadId, null);
});

test('decision workspace renders operator brief with clear next-step guidance', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const queueManager = runtime.getQueueManager();
  const sidebar = runtime.getState().globalSidebarEl;

  const item = {
    id: 'item-brief-1',
    thread_id: 'thread-brief-1',
    state: 'needs_info',
    vendor_name: 'Acme Corp',
    invoice_number: 'INV-BRIEF-1',
    amount: 315.25,
    currency: 'USD',
    subject: 'Missing PO details',
    sender: 'billing@acme.example',
    confidence: 0.87,
    next_action: 'request_info',
    exception_code: 'po_missing_reference',
    metadata: {
      ap_decision_reasoning: 'Vendor invoice is missing PO reference and requires follow-up before posting.',
      ap_decision_recommendation: 'needs_info',
    },
  };
  const contexts = new Map([
    ['item-brief-1', {
      freshness: { is_stale: false, age_seconds: 20 },
      source_quality: { distribution: 'gmail_thread:1', total_sources: 1 },
      email: { source_count: 1, sources: [] },
      web: { browser_event_count: 0, recent_browser_events: [], related_portals: [], payment_portals: [], procurement: [], dms_documents: [], bank_transactions: [], spreadsheets: [], connector_coverage: {} },
      approvals: { count: 0, latest: null, slack: { thread_preview: [] }, teams: {} },
      erp: { connector_available: true, state: 'needs_info', erp_reference: null },
      po_match: { status: 'missing_po' },
      budget: { status: 'healthy', requires_decision: false, checks: [] },
    }],
  ]);

  queueManager.emitQueueUpdated([item], { state: 'idle' }, new Map(), [], new Map(), new Map(), contexts);
  await runtime.flush();

  const threadHandler = runtime.records.sdkHandlers.threadView;
  const threadView = runtime.createThreadView('thread-brief-1');
  threadHandler(threadView);
  runtime.api.renderSidebar();
  await runtime.flush();
  await runtime.flush();

  const threadContext = sidebar.querySelector('#cl-thread-context');
  assert.ok(threadContext);
  assert.match(threadContext.innerHTML, /What happened/i);
  assert.match(threadContext.innerHTML, /Why this needs attention/i);
  assert.match(threadContext.innerHTML, /Best next step/i);
  assert.match(threadContext.innerHTML, /Draft a vendor info request/i);
  assert.match(threadContext.innerHTML, /Expected outcome:/i);
});

test('A1 needs_info follow-up metadata renders attempt tracking and SLA next step', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const queueManager = runtime.getQueueManager();
  const sidebar = runtime.getState().globalSidebarEl;

  const item = {
    id: 'item-followup-1',
    thread_id: 'thread-followup-1',
    state: 'needs_info',
    vendor_name: 'Acme Corp',
    invoice_number: 'INV-FOLLOWUP-1',
    amount: 510.0,
    currency: 'USD',
    subject: 'PO clarification required',
    sender: 'billing@acme.example',
    confidence: 0.9,
    next_action: 'await_vendor_response',
    followup_attempt_count: 2,
    followup_last_sent_at: '2026-02-27T09:30:00Z',
    followup_sla_due_at: '2026-02-28T09:30:00Z',
    followup_next_action: 'await_vendor_response',
    needs_info_question: 'Please provide the PO number for this invoice.',
    needs_info_draft_id: 'draft-followup-1',
    metadata: {},
  };
  const contexts = new Map([
    ['item-followup-1', {
      freshness: { is_stale: false, age_seconds: 42 },
      source_quality: { distribution: 'gmail_thread:1', total_sources: 1 },
      email: { source_count: 1, sources: [] },
      web: { browser_event_count: 0, recent_browser_events: [], related_portals: [], payment_portals: [], procurement: [], dms_documents: [], bank_transactions: [], spreadsheets: [], connector_coverage: {} },
      approvals: { count: 0, latest: null, slack: { thread_preview: [] }, teams: {} },
      erp: { connector_available: true, state: 'needs_info', erp_reference: null },
      po_match: { status: 'missing_po' },
      budget: { status: 'healthy', requires_decision: false, checks: [] },
    }],
  ]);

  queueManager.emitQueueUpdated([item], { state: 'idle' }, new Map(), [], new Map(), new Map(), contexts);
  await runtime.flush();

  const threadHandler = runtime.records.sdkHandlers.threadView;
  const threadView = runtime.createThreadView('thread-followup-1');
  threadHandler(threadView);
  runtime.api.renderSidebar();
  await runtime.flush();
  await runtime.flush();

  const threadContext = sidebar.querySelector('#cl-thread-context');
  assert.ok(threadContext);
  assert.match(threadContext.innerHTML, /Follow-up attempts: 2/i);
  assert.match(threadContext.innerHTML, /Last draft:/i);
  assert.match(threadContext.innerHTML, /Await response until/i);
});

test('AX6 debug KPI panel renders agentic telemetry metrics with ratio-to-percent formatting', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: true } });
  const queueManager = runtime.getQueueManager();
  const sidebar = runtime.getState().globalSidebarEl;

  queueManager.emitQueueUpdated(
    [],
    { state: 'idle' },
    new Map(),
    [],
    new Map(),
    new Map(),
    new Map(),
    {
      touchless_rate: { eligible_count: 20, touchless_count: 12, rate: 0.6 },
      exception_rate: { exception_count: 4, rate: 0.2 },
      on_time_approvals: { approved_count: 10, on_time_count: 8, rate: 0.8 },
      cycle_time_hours: { count: 12, avg: 3.4 },
      missed_discounts_baseline: { candidate_count: 2, missed_count: 1, missed_value: 50.0 },
      approval_friction: { sla_breach_rate: 0.25 },
      agentic_telemetry: {
        window_hours: 168,
        straight_through_rate: { eligible_count: 20, count: 12, rate: 0.6 },
        human_intervention_rate: { eligible_count: 20, count: 8, rate: 0.4 },
        awaiting_approval_time_hours: { population_count: 8, avg: 6.5, p95: 18.2 },
        erp_browser_fallback_rate: { attempt_count: 9, fallback_requested_count: 2, rate: 0.2222 },
        agent_suggestion_acceptance: { prompted_count: 5, accepted_count: 4, rate: 0.8 },
        agent_actions_requiring_manual_override: { total_actions: 12, count: 5, rate: 0.4167 },
        top_blocker_reasons: {
          open_population: 7,
          by_category: { confidence: 2, policy: 2, budget: 1, erp: 2, other: 0 },
          top_reasons: [
            { reason: 'confidence:amount', count: 2 },
            { reason: 'erp:erp_timeout', count: 2 },
            { reason: 'policy:policy_po_missing', count: 1 },
          ],
        },
      },
    },
  );
  await runtime.flush();

  const kpiSection = sidebar.querySelector('#cl-kpi-summary');
  assert.ok(kpiSection);
  assert.match(kpiSection.innerHTML, /Workflow health/i);
  assert.match(kpiSection.innerHTML, /Agentic telemetry/i);
  assert.match(kpiSection.innerHTML, /168h window/i);
  assert.match(kpiSection.innerHTML, /Touchless/i);
  assert.match(kpiSection.innerHTML, /60\.0%/i);
  assert.match(kpiSection.innerHTML, /SLA breach rate: 25\.0%/i);
  assert.match(kpiSection.innerHTML, /Browser fallback/i);
  assert.match(kpiSection.innerHTML, /22\.2%/i);
  assert.match(kpiSection.innerHTML, /Agent accepted/i);
  assert.match(kpiSection.innerHTML, /80\.0%/i);
  assert.match(kpiSection.innerHTML, /Manual override req\./i);
  assert.match(kpiSection.innerHTML, /41\.7%/i);
  assert.match(kpiSection.innerHTML, /Top blockers: .*confidence:amount/i);
});

test('AX6 non-debug KPI panel renders compact agentic snapshot', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const queueManager = runtime.getQueueManager();
  const sidebar = runtime.getState().globalSidebarEl;

  queueManager.emitQueueUpdated(
    [],
    { state: 'idle' },
    new Map(),
    [],
    new Map(),
    new Map(),
    new Map(),
    {
      touchless_rate: { eligible_count: 10, touchless_count: 7, rate: 0.7 },
      exception_rate: { exception_count: 3, rate: 0.3 },
      on_time_approvals: { approved_count: 8, on_time_count: 7, rate: 0.875 },
      cycle_time_hours: { count: 8, avg: 4.2 },
      agentic_telemetry: {
        window_hours: 168,
        straight_through_rate: { eligible_count: 10, count: 7, rate: 0.7 },
        erp_browser_fallback_rate: { attempt_count: 6, fallback_requested_count: 1, rate: 0.1667 },
        agent_suggestion_acceptance: { prompted_count: 4, accepted_count: 3, rate: 0.75 },
        top_blocker_reasons: {
          open_population: 4,
          by_category: { confidence: 1, policy: 1, budget: 1, erp: 1, other: 0 },
          top_reasons: [{ reason: 'erp:connector_timeout', count: 1 }],
        },
      },
    },
  );
  await runtime.flush();

  const kpiSection = sidebar.querySelector('#cl-kpi-summary');
  assert.ok(kpiSection);
  assert.match(kpiSection.innerHTML, /Agentic snapshot/i);
  assert.match(kpiSection.innerHTML, /Straight-through/i);
  assert.match(kpiSection.innerHTML, /70\.0%/i);
  assert.match(kpiSection.innerHTML, /Browser fallback/i);
  assert.match(kpiSection.innerHTML, /16\.7%/i);
  assert.match(kpiSection.innerHTML, /Agent accepted/i);
  assert.match(kpiSection.innerHTML, /75\.0%/i);
  assert.match(kpiSection.innerHTML, /Top blockers: .*erp:connector timeout/i);
});

test('AX4 batch agent ops section renders and preview action updates batch summary card', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const queueManager = runtime.getQueueManager();
  const sidebar = runtime.getState().globalSidebarEl;

  queueManager.emitQueueUpdated([
    {
      id: 'ready-1',
      thread_id: 't-ready-1',
      state: 'ready_to_post',
      vendor_name: 'Acme Corp',
      invoice_number: 'INV-1001',
      amount: 125,
      currency: 'USD',
      updated_at: '2026-02-24T10:00:00Z',
      next_action: 'post_to_erp',
      metadata: {},
      confidence: 0.99,
    },
    {
      id: 'approval-1',
      thread_id: 't-approval-1',
      state: 'needs_approval',
      vendor_name: 'Pending Co',
      invoice_number: 'INV-3001',
      amount: 75,
      currency: 'USD',
      updated_at: '2026-02-24T05:00:00Z',
      next_action: 'approve_or_reject',
      metadata: {},
      confidence: 0.98,
    },
  ], { state: 'idle' }, new Map([
    ['ready-1', { session: { id: 'sess-ready-1', metadata: {} }, events: [], queued_commands: [] }],
  ]));
  await runtime.flush();

  const batchSection = sidebar.querySelector('#cl-batch-agent-ops');
  assert.ok(batchSection);
  assert.match(batchSection.innerHTML, /Batch agent ops|low-risk ready|approval nudges/i);

  const previewButtons = batchSection.querySelectorAll('.cl-batch-op-action');
  const previewReady = previewButtons.find((btn) =>
    btn.getAttribute('data-batch-op') === 'process_low_risk_ready'
    && btn.getAttribute('data-dry-run') === '1'
  );
  assert.ok(previewReady);

  await previewReady.click();
  await runtime.flush();

  const rerenderedBatch = sidebar.querySelector('#cl-batch-agent-ops');
  assert.match(rerenderedBatch.innerHTML, /Preview batch: process low-risk ready items/);
});

test('AX4 batch retry run uses canonical retry path and renders per-item outcomes', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({
    queueManager: {
      debugUiEnabled: false,
      retryFailedPost: async (item, qm) => {
        if (item.id === 'fail-1') {
          qm.queue = qm.queue.map((entry) => entry.id === item.id ? { ...entry, state: 'posted_to_erp', erp_reference: 'ERP-FAIL-1' } : entry);
          return { status: 'posted', erp_reference: 'ERP-FAIL-1' };
        }
        qm.queue = qm.queue.map((entry) => entry.id === item.id ? { ...entry, state: 'ready_to_post' } : entry);
        return { status: 'ready_to_post', message: 'ERP adapter unavailable' };
      },
    },
  });
  const queueManager = runtime.getQueueManager();
  const sidebar = runtime.getState().globalSidebarEl;

  queueManager.emitQueueUpdated([
    {
      id: 'fail-1',
      thread_id: 't-fail-1',
      state: 'failed_post',
      vendor_name: 'ERP Fail Co',
      invoice_number: 'INV-2001',
      amount: 120,
      currency: 'USD',
      updated_at: '2026-02-24T10:00:00Z',
      next_action: 'retry_posting',
      metadata: {},
      confidence: 0.99,
    },
    {
      id: 'fail-2',
      thread_id: 't-fail-2',
      state: 'failed_post',
      vendor_name: 'ERP Hold Co',
      invoice_number: 'INV-2002',
      amount: 130,
      currency: 'USD',
      updated_at: '2026-02-24T09:00:00Z',
      next_action: 'retry_posting',
      metadata: {},
      confidence: 0.98,
    },
  ], { state: 'idle' });
  await runtime.flush();

  const batchSection = sidebar.querySelector('#cl-batch-agent-ops');
  const maxItemsSelect = batchSection.querySelector('#cl-batch-max-items');
  const amountCapInput = batchSection.querySelector('#cl-batch-amount-cap');
  assert.ok(maxItemsSelect);
  assert.ok(amountCapInput);
  maxItemsSelect.value = '10';
  await maxItemsSelect.dispatchEvent({ type: 'change' });
  amountCapInput.value = '200';
  await amountCapInput.dispatchEvent({ type: 'change' });
  await runtime.flush();

  const runButtons = batchSection.querySelectorAll('.cl-batch-op-action');
  const retryRunBtn = runButtons.find((btn) =>
    btn.getAttribute('data-batch-op') === 'retry_failed_posts_preview'
    && btn.getAttribute('data-dry-run') === '0'
  );
  assert.ok(retryRunBtn);

  await retryRunBtn.click();
  await runtime.flush();

  assert.equal(queueManager.calls.retryFailedPost, 2);
  assert.ok(queueManager.calls.syncQueueWithBackend >= 1);
  const rerenderedBatch = sidebar.querySelector('#cl-batch-agent-ops');
  assert.match(rerenderedBatch.innerHTML, /Batch run completed: failed post retries/i);
  assert.match(rerenderedBatch.innerHTML, /ERP ref ERP-FAIL-1/i);
  assert.match(rerenderedBatch.innerHTML, /re-queued/i);
  assert.match(rerenderedBatch.innerHTML, /Refresh check: 1 posted, 1 ready_to_post/i);
  assert.match(rerenderedBatch.innerHTML, /Show item results/i);
});

test('AX4 rerun failed subset action re-executes only failed retry items', async () => {
  const retryAttempts = new Map();
  const runtime = await createInboxSdkIntegrationRuntime({
    queueManager: {
      debugUiEnabled: false,
      retryFailedPost: async (item, qm) => {
        const count = (retryAttempts.get(item.id) || 0) + 1;
        retryAttempts.set(item.id, count);
        if (item.id === 'fail-a' && count === 1) {
          qm.queue = qm.queue.map((entry) => entry.id === item.id ? { ...entry, state: 'failed_post' } : entry);
          return { status: 'error', reason: 'transient_erp_timeout' };
        }
        qm.queue = qm.queue.map((entry) => entry.id === item.id ? { ...entry, state: 'posted_to_erp', erp_reference: `ERP-${item.id}-${count}` } : entry);
        return { status: 'posted', erp_reference: `ERP-${item.id}-${count}` };
      },
    },
  });
  const queueManager = runtime.getQueueManager();
  const sidebar = runtime.getState().globalSidebarEl;

  queueManager.emitQueueUpdated([
    {
      id: 'fail-a',
      thread_id: 't-fail-a',
      state: 'failed_post',
      vendor_name: 'Retry Me LLC',
      invoice_number: 'INV-R1',
      amount: 110,
      currency: 'USD',
      updated_at: '2026-02-24T09:00:00Z',
      next_action: 'retry_posting',
      metadata: {},
      confidence: 0.98,
    },
    {
      id: 'fail-b',
      thread_id: 't-fail-b',
      state: 'failed_post',
      vendor_name: 'Will Pass Inc',
      invoice_number: 'INV-R2',
      amount: 90,
      currency: 'USD',
      updated_at: '2026-02-24T08:00:00Z',
      next_action: 'retry_posting',
      metadata: {},
      confidence: 0.99,
    },
  ], { state: 'idle' });
  await runtime.flush();

  let batchSection = sidebar.querySelector('#cl-batch-agent-ops');
  const presetSelect = batchSection.querySelector('#cl-batch-preset');
  assert.ok(presetSelect);
  presetSelect.value = 'oldest_first';
  await presetSelect.dispatchEvent({ type: 'change' });
  await runtime.flush();

  batchSection = sidebar.querySelector('#cl-batch-agent-ops');
  const runButtons = batchSection.querySelectorAll('.cl-batch-op-action');
  const retryRunBtn = runButtons.find((btn) =>
    btn.getAttribute('data-batch-op') === 'retry_failed_posts_preview'
    && btn.getAttribute('data-dry-run') === '0'
  );
  assert.ok(retryRunBtn);
  await retryRunBtn.click();
  await runtime.flush();

  assert.equal(queueManager.calls.retryFailedPost, 2);
  batchSection = sidebar.querySelector('#cl-batch-agent-ops');
  assert.match(batchSection.innerHTML, /Rerun failed subset \(1\)/i);
  assert.match(batchSection.innerHTML, /preset oldest first/i);

  const rerunBtn = batchSection.querySelectorAll('.cl-batch-summary-action').find((btn) =>
    btn.getAttribute('data-action-id') === 'rerun_failed_subset'
  );
  assert.ok(rerunBtn);
  await rerunBtn.click();
  await runtime.flush();

  assert.equal(queueManager.calls.retryFailedPost, 3);
  assert.equal(retryAttempts.get('fail-a'), 2);
  assert.equal(retryAttempts.get('fail-b'), 1);

  batchSection = sidebar.querySelector('#cl-batch-agent-ops');
  assert.ok(batchSection);
  if (batchSection.innerHTML) {
    assert.match(batchSection.innerHTML, /Batch run completed: failed post retries/i);
    assert.doesNotMatch(batchSection.innerHTML, /Rerun failed subset \(1\)/i);
  } else {
    assert.equal(batchSection.innerHTML, '');
  }
});

test('A2 batch intents execute vendor follow-up, low-risk approval routing, and recoverable retries', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const queueManager = runtime.getQueueManager();
  const sidebar = runtime.getState().globalSidebarEl;

  queueManager.emitQueueUpdated([
    {
      id: 'needs-info-a2',
      thread_id: 't-needs-info-a2',
      state: 'needs_info',
      vendor_name: 'Need Info Co',
      invoice_number: 'INV-N-A2',
      amount: 95,
      currency: 'USD',
      followup_next_action: 'prepare_vendor_followup_draft',
      next_action: 'prepare_vendor_followup_draft',
      updated_at: '2026-02-26T09:00:00Z',
      metadata: {},
      confidence: 0.94,
    },
    {
      id: 'validated-a2',
      thread_id: 't-validated-a2',
      state: 'validated',
      vendor_name: 'Validated Co',
      invoice_number: 'INV-V-A2',
      amount: 110,
      currency: 'USD',
      document_type: 'invoice',
      next_action: 'route_for_approval',
      updated_at: '2026-02-26T10:00:00Z',
      metadata: {},
      confidence: 0.97,
    },
    {
      id: 'failed-a2',
      thread_id: 't-failed-a2',
      state: 'failed_post',
      vendor_name: 'Recoverable Co',
      invoice_number: 'INV-F-A2',
      amount: 120,
      currency: 'USD',
      last_error: 'connector timeout',
      next_action: 'retry_post',
      updated_at: '2026-02-26T11:00:00Z',
      metadata: {},
      confidence: 0.98,
    },
  ], { state: 'idle' });
  await runtime.flush();

  let batchSection = sidebar.querySelector('#cl-batch-agent-ops');
  assert.ok(batchSection);
  assert.match(batchSection.innerHTML, /prepare vendor follow-ups/i);
  assert.match(batchSection.innerHTML, /route low-risk for approval/i);
  assert.match(batchSection.innerHTML, /retry recoverable failures/i);

  let runButtons = batchSection.querySelectorAll('.cl-batch-op-action');
  const runFollowups = runButtons.find((btn) =>
    btn.getAttribute('data-batch-op') === 'prepare_vendor_followups'
    && btn.getAttribute('data-dry-run') === '0'
  );
  assert.ok(runFollowups);
  await runFollowups.click();
  await runtime.flush();
  assert.equal(queueManager.calls.prepareVendorFollowup, 1);
  batchSection = sidebar.querySelector('#cl-batch-agent-ops');
  assert.match(batchSection.innerHTML, /Batch run completed: vendor follow-ups/i);

  runButtons = batchSection.querySelectorAll('.cl-batch-op-action');
  const runRoute = runButtons.find((btn) =>
    btn.getAttribute('data-batch-op') === 'route_low_risk_for_approval'
    && btn.getAttribute('data-dry-run') === '0'
  );
  assert.ok(runRoute);
  await runRoute.click();
  await runtime.flush();
  assert.equal(queueManager.calls.routeLowRiskForApproval, 1);
  batchSection = sidebar.querySelector('#cl-batch-agent-ops');
  assert.match(batchSection.innerHTML, /Batch run completed: route low-risk for approval/i);

  runButtons = batchSection.querySelectorAll('.cl-batch-op-action');
  const runRetryRecoverable = runButtons.find((btn) =>
    btn.getAttribute('data-batch-op') === 'retry_recoverable_failures'
    && btn.getAttribute('data-dry-run') === '0'
  );
  assert.ok(runRetryRecoverable);
  await runRetryRecoverable.click();
  await runtime.flush();
  assert.equal(queueManager.calls.retryRecoverableFailure, 1);
  batchSection = sidebar.querySelector('#cl-batch-agent-ops');
  assert.match(batchSection.innerHTML, /Batch run completed: retry recoverable failures/i);
});

test('AX5 shows browser fallback trust state in thread context and agent timeline, with readable web events', async () => {
  const fallbackAuditEvents = [
    {
      id: 'audit-fb-1',
      event_type: 'erp_api_fallback_preview_created',
      created_at: '2026-02-26T10:20:00Z',
      payload_json: {
        session_id: 'sess-ax5',
        command_count: 3,
        requires_confirmation_count: 1,
      },
    },
    {
      id: 'audit-fb-2',
      event_type: 'erp_api_fallback_confirmation_captured',
      created_at: '2026-02-26T10:21:00Z',
      payload_json: {
        session_id: 'sess-ax5',
        required_count: 1,
        confirmed_count: 1,
      },
    },
    {
      id: 'audit-fb-3',
      event_type: 'erp_api_fallback_requested',
      created_at: '2026-02-26T10:22:00Z',
      payload_json: {
        api_status: 'error',
        api_reason: 'connector_timeout',
        fallback: {
          requested: true,
          reason: 'fallback_preview_confirmed_and_dispatched',
          session_id: 'sess-ax5',
          queued: 2,
          blocked: 0,
          denied: 0,
          dispatch_status: 'submitted',
        },
      },
    },
    {
      id: 'audit-fb-4',
      event_type: 'erp_browser_fallback_completed',
      created_at: '2026-02-26T10:23:00Z',
      payload_json: {
        session_id: 'sess-ax5',
        erp_reference: 'ERP-AX5-1',
        evidence: { screenshot: true, page_url: true },
      },
    },
  ];

  const runtime = await createInboxSdkIntegrationRuntime({
    queueManager: {
      debugUiEnabled: false,
      fetchAuditTrail: async () => fallbackAuditEvents,
    },
  });
  const queueManager = runtime.getQueueManager();
  const sidebar = runtime.getState().globalSidebarEl;

  const item = {
    id: 'ax5-1',
    thread_id: 'thread-ax5-1',
    state: 'posted_to_erp',
    vendor_name: 'Fallback Verified Co',
    invoice_number: 'INV-AX5',
    amount: 410.25,
    currency: 'USD',
    subject: 'Invoice INV-AX5',
    sender: 'ap@fallback.example',
    metadata: {},
    confidence: 0.99,
    next_action: 'none',
    erp_reference: 'ERP-AX5-1',
  };
  const agentSessions = new Map([
    ['ax5-1', {
      session: { id: 'sess-ax5', state: 'running', metadata: {} },
      events: [],
      queued_commands: [],
      pending_approvals: [],
    }],
  ]);
  const contexts = new Map([
    ['ax5-1', {
      freshness: { age_seconds: 12, is_stale: false },
      source_quality: { distribution: 'gmail_thread:1', total_sources: 1 },
      email: { source_count: 1, sources: [] },
      web: {
        browser_event_count: 2,
        recent_browser_events: [
          {
            ts: '2026-02-26T10:22:30Z',
            status: 'completed',
            tool_name: 'capture_evidence',
            result: { summary: 'Captured fallback confirmation screenshot for ERP posting' },
          },
          {
            ts: '2026-02-26T10:22:40Z',
            status: 'completed',
            tool_name: 'find_element',
            result: { summary: 'Located submit button in ERP portal form' },
          },
        ],
        related_portals: [],
        payment_portals: [],
        procurement: [],
        dms_documents: [],
        bank_transactions: [],
        spreadsheets: [],
        connector_coverage: { payment_portal: true, procurement: false, bank: false, sheets: false, dms: false },
      },
      approvals: { count: 0, latest: null, slack: { thread_preview: [] }, teams: {} },
      erp: {
        connector_available: true,
        state: 'posted_to_erp',
        erp_reference: 'ERP-AX5-1',
        erp_posted_at: '2026-02-26T10:23:00Z',
      },
      po_match: {},
      budget: {},
    }],
  ]);

  queueManager.emitQueueUpdated([item], { state: 'idle' }, agentSessions, [], new Map(), new Map(), contexts);
  await runtime.flush();

  const threadHandler = runtime.records.sdkHandlers.threadView;
  const threadView = runtime.createThreadView('thread-ax5-1');
  threadHandler(threadView);
  runtime.api.renderSidebar();
  await runtime.flush();
  await runtime.flush();

  const threadContext = sidebar.querySelector('#cl-thread-context');
  assert.ok(threadContext);
  assert.match(threadContext.innerHTML, /Browser fallback/i);
  assert.match(threadContext.innerHTML, /Stage 5 of 5/i);
  assert.match(threadContext.innerHTML, /ERP ref: ERP-AX5-1/i);
  assert.match(threadContext.innerHTML, /Browser fallback completed/i);
  assert.match(threadContext.innerHTML, /Runner completion is reconciled/i);

  const agentSection = sidebar.querySelector('#cl-agent-actions');
  assert.ok(agentSection);
  assert.match(agentSection.innerHTML, /Browser fallback preview generated/i);
  assert.match(agentSection.innerHTML, /Browser fallback runner executing/i);
  assert.match(agentSection.innerHTML, /Browser fallback completed/i);
  assert.match(agentSection.innerHTML, /S4\/5|S5\/5/i);

  const webTab = threadContext.querySelectorAll('.cl-context-tab').find(
    (button) => button.getAttribute('data-tab') === 'web'
  );
  assert.ok(webTab);
  await webTab.click();
  await runtime.flush();

  const rerenderedContext = sidebar.querySelector('#cl-thread-context');
  assert.match(rerenderedContext.innerHTML, /Capture evidence/i);
  assert.match(rerenderedContext.innerHTML, /Fallback evidence/i);
  assert.match(rerenderedContext.innerHTML, /fallback confirmation screenshot/i);
});
