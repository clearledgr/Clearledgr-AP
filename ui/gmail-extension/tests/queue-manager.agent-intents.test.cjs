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
  assert.equal(payload.input.email_id, 'ap-1');
  assert.equal(payload.idempotency_key, 'idem-1');
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
  assert.equal(payload.input.email_id, 'ap-retry-1');
});
