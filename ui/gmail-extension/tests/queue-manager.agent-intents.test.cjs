const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE_PATH = path.resolve(__dirname, '../queue-manager.js');

function createResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return payload;
    },
  };
}

function loadQueueManager(fetchImpl) {
  const source = fs.readFileSync(SOURCE_PATH, 'utf8');
  const transformed = source.replace(
    /export\s*\{\s*ClearledgrQueueManager\s*\};?\s*$/m,
    'module.exports = { ClearledgrQueueManager };'
  );

  const context = {
    module: { exports: {} },
    exports: {},
    console,
    Date,
    JSON,
    Math,
    Intl,
    String,
    Number,
    Array,
    Object,
    Set,
    Map,
    URL,
    fetch: fetchImpl,
    chrome: {
      storage: {
        sync: {
          get(_keys, callback) {
            callback({});
          },
        },
      },
    },
  };
  context.exports = context.module.exports;
  vm.runInNewContext(transformed, context, { filename: 'queue-manager.vm.js' });
  return context.module.exports.ClearledgrQueueManager;
}

function createManager(fetchImpl) {
  const ClearledgrQueueManager = loadQueueManager(fetchImpl);
  const manager = new ClearledgrQueueManager();
  manager.runtimeConfig = {
    backendUrl: 'https://api.clearledgr.test',
    organizationId: 'default',
    userEmail: 'agent@example.com',
  };
  manager.syncQueueWithBackend = async () => true;
  manager.emitQueueUpdated = () => {};
  return manager;
}

test('prepareVendorFollowup uses canonical agent intent execute endpoint', async () => {
  const calls = [];
  const manager = createManager(async (url, options) => {
    calls.push({ url, options });
    return createResponse(200, { status: 'prepared', draft_id: 'draft-1' });
  });

  const result = await manager.prepareVendorFollowup(
    { id: 'ap-1' },
    { reason: 'batch_prepare_vendor_followups', idempotencyKey: 'idem-1' }
  );

  assert.equal(result.status, 'prepared');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://api.clearledgr.test/api/agent/intents/execute');
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.intent, 'prepare_vendor_followups');
  assert.equal(payload.input.ap_item_id, 'ap-1');
  assert.equal(payload.input.email_id, 'ap-1');
  assert.equal(payload.idempotency_key, 'idem-1');
});

test('prepareVendorFollowup preserves waiting_sla responses for the Gmail surface', async () => {
  const manager = createManager(async () => (
    createResponse(200, {
      status: 'waiting_sla',
      reason: 'waiting_for_sla_window',
      followup_next_action: 'await_vendor_response',
    })
  ));

  const result = await manager.prepareVendorFollowup(
    { id: 'ap-1' },
    { idempotencyKey: 'idem-followup-wait-1' }
  );

  assert.equal(result.status, 'waiting_sla');
  assert.equal(result.reason, 'waiting_for_sla_window');
  assert.equal(result.followup_next_action, 'await_vendor_response');
});

test('requestApproval uses canonical approval intent', async () => {
  const calls = [];
  const manager = createManager(async (url, options) => {
    calls.push({ url, options });
    return createResponse(200, { status: 'pending_approval', audit_event_id: 'audit-approval-1' });
  });

  const result = await manager.requestApproval(
    { id: 'ap-approval-1' },
    { reason: 'route_for_approval', idempotencyKey: 'idem-approval-1' }
  );

  assert.equal(result.status, 'pending_approval');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://api.clearledgr.test/api/agent/intents/execute');
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.intent, 'request_approval');
  assert.equal(payload.input.ap_item_id, 'ap-approval-1');
  assert.equal(payload.input.email_id, 'ap-approval-1');
  assert.equal(payload.input.reason, 'route_for_approval');
  assert.equal(payload.idempotency_key, 'idem-approval-1');
});

test('requestApproval uses primary source gmail reference when top-level ids are missing', async () => {
  const calls = [];
  const manager = createManager(async (url, options) => {
    calls.push({ url, options });
    return createResponse(200, { status: 'pending_approval', audit_event_id: 'audit-approval-2' });
  });

  const result = await manager.requestApproval(
    {
      id: 'ap-approval-2',
      primary_source: {
        thread_id: 'gmail-thread-primary-2',
      },
    },
    { idempotencyKey: 'idem-approval-2' }
  );

  assert.equal(result.status, 'pending_approval');
  assert.equal(calls.length, 1);
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.intent, 'request_approval');
  assert.equal(payload.input.ap_item_id, 'ap-approval-2');
  assert.equal(payload.input.email_id, 'gmail-thread-primary-2');
  assert.equal(payload.idempotency_key, 'idem-approval-2');
});

test('nudgeApproval uses canonical nudge intent', async () => {
  const calls = [];
  const manager = createManager(async (url, options) => {
    calls.push({ url, options });
    return createResponse(200, { status: 'nudged' });
  });

  const result = await manager.nudgeApproval(
    { id: 'ap-nudge-1' },
    { message: 'Reminder from finance ops', idempotencyKey: 'idem-nudge-1' }
  );

  assert.equal(result.status, 'nudged');
  assert.equal(calls.length, 1);
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.intent, 'nudge_approval');
  assert.equal(payload.input.ap_item_id, 'ap-nudge-1');
  assert.equal(payload.input.email_id, 'ap-nudge-1');
  assert.equal(payload.input.message, 'Reminder from finance ops');
});

test('nudgeApproval normalizes fallback delivery into nudged status', async () => {
  const manager = createManager(async () => createResponse(200, {
    status: 'error',
    fallback: {
      status: 'sent',
      delivery: 'approval_reminder_fallback',
      channel: 'cl-finance-ap',
    },
  }));
  manager.syncQueueWithBackend = async () => false;

  const result = await manager.nudgeApproval(
    { id: 'ap-nudge-fallback-1' },
    { idempotencyKey: 'idem-nudge-fallback-1' }
  );

  assert.equal(result.status, 'nudged');
  assert.equal(result.fallback.status, 'sent');
});

test('rejectInvoice uses canonical reject intent', async () => {
  const calls = [];
  const manager = createManager(async (url, options) => {
    calls.push({ url, options });
    return createResponse(200, { status: 'rejected' });
  });

  const result = await manager.rejectInvoice(
    { id: 'ap-reject-1' },
    { reason: 'Duplicate invoice', idempotencyKey: 'idem-reject-1' }
  );

  assert.equal(result.status, 'rejected');
  assert.equal(calls.length, 1);
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.intent, 'reject_invoice');
  assert.equal(payload.input.ap_item_id, 'ap-reject-1');
  assert.equal(payload.input.email_id, 'ap-reject-1');
  assert.equal(payload.input.reason, 'Duplicate invoice');
});

test('approveAndPost uses canonical ERP posting intent', async () => {
  const calls = [];
  const manager = createManager(async (url, options) => {
    calls.push({ url, options });
    return createResponse(200, { status: 'posted_to_erp', erp_reference: 'ERP-POST-1' });
  });

  const result = await manager.approveAndPost(
    { id: 'ap-post-1', field_confidences: { amount: 0.98 } },
    { override: false, idempotencyKey: 'idem-post-1' }
  );

  assert.equal(result.status, 'posted_to_erp');
  assert.equal(calls.length, 1);
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.intent, 'post_to_erp');
  assert.equal(payload.input.ap_item_id, 'ap-post-1');
  assert.equal(payload.input.email_id, 'ap-post-1');
  assert.deepEqual(payload.input.field_confidences, { amount: 0.98 });
  assert.equal(payload.idempotency_key, 'idem-post-1');
});

test('prepareVendorFollowup returns an error when canonical intent is unsupported', async () => {
  const calls = [];
  const manager = createManager(async (url, options) => {
    calls.push({ url, options });
    return createResponse(400, { detail: 'unsupported_intent:prepare_vendor_followups' });
  });

  const result = await manager.prepareVendorFollowup(
    { id: 'ap-2' },
    { reason: 'batch_prepare_vendor_followups', force: false, idempotencyKey: 'idem-2' }
  );

  assert.equal(result.status, 'error');
  assert.equal(result.reason, 'unsupported_intent:prepare_vendor_followups');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://api.clearledgr.test/api/agent/intents/execute');
});

test('routeLowRiskForApproval uses canonical route intent', async () => {
  const calls = [];
  const manager = createManager(async (url, options) => {
    calls.push({ url, options });
    return createResponse(200, { status: 'pending_approval' });
  });

  const result = await manager.routeLowRiskForApproval(
    { id: 'ap-route-1' },
    { reason: 'batch_route_low_risk_for_approval', idempotencyKey: 'idem-route-1' }
  );

  assert.equal(result.status, 'pending_approval');
  assert.equal(calls.length, 1);
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.intent, 'route_low_risk_for_approval');
  assert.equal(payload.input.ap_item_id, 'ap-route-1');
  assert.equal(payload.input.email_id, 'ap-route-1');
});

test('retryRecoverableFailure uses canonical retry intent', async () => {
  const calls = [];
  const manager = createManager(async (url, options) => {
    calls.push({ url, options });
    return createResponse(200, { status: 'posted', erp_reference: 'ERP-1' });
  });

  const result = await manager.retryRecoverableFailure(
    { id: 'ap-retry-1' },
    { reason: 'batch_retry_recoverable_failures', idempotencyKey: 'idem-retry-1' }
  );

  assert.equal(result.status, 'posted');
  assert.equal(calls.length, 1);
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.intent, 'retry_recoverable_failures');
  assert.equal(payload.input.ap_item_id, 'ap-retry-1');
  assert.equal(payload.input.email_id, 'ap-retry-1');
});

test('shareFinanceSummary posts canonical ap_item_id alongside thread reference', async () => {
  const calls = [];
  const manager = createManager(async (url, options) => {
    calls.push({ url, options });
    return createResponse(200, { status: 'preview_ready', draft: { subject: 'Summary' } });
  });

  const result = await manager.shareFinanceSummary(
    { id: 'ap-share-1', thread_id: 'thread-share-1' },
    { target: 'email_draft', previewOnly: true }
  );

  assert.equal(result.status, 'preview_ready');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://api.clearledgr.test/extension/finance-summary-share');
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.ap_item_id, 'ap-share-1');
  assert.equal(payload.email_id, 'thread-share-1');
});

test('submitBudgetDecision posts canonical ap_item_id alongside thread reference', async () => {
  const calls = [];
  const manager = createManager(async (url, options) => {
    calls.push({ url, options });
    return createResponse(200, { status: 'needs_info' });
  });
  manager.fetchItemContext = async () => ({});

  const result = await manager.submitBudgetDecision(
    { id: 'ap-budget-1', thread_id: 'thread-budget-1' },
    'request_budget_adjustment',
    'Need updated budget sign-off'
  );

  assert.equal(result.status, 'needs_info');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://api.clearledgr.test/extension/budget-decision');
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.ap_item_id, 'ap-budget-1');
  assert.equal(payload.email_id, 'thread-budget-1');
  assert.equal(payload.justification, 'Need updated budget sign-off');
});
