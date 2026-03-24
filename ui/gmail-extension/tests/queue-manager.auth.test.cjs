const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function loadQueueManager() {
  const module = await import(pathToFileURL(path.resolve(__dirname, '../queue-manager.js')).href);
  return module.ClearledgrQueueManager;
}

test('interactive auth attempts are debounced by cooldown', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = new ClearledgrQueueManager();
  manager.runtimeConfig = { valid: true, organizationId: 'default' };
  manager.ensureGmailAuth = async () => ({ success: false, error: 'auth_required' });

  manager.lastInteractiveAuthAttemptAt = Date.now();
  const result = await manager.authorizeGmailNow();

  assert.equal(result.success, false);
  assert.equal(result.error, 'interactive_auth_cooldown');
  assert.ok(Number(result.retry_after_seconds || 0) >= 1);
});

test('silent backend auth retries are debounced after a failure', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = new ClearledgrQueueManager();
  manager.runtimeConfig = { valid: true, organizationId: 'default' };
  manager.lastBackendAuthFailureAt = Date.now();

  const result = await manager.ensureBackendAuthIfNeeded();

  assert.equal(result.success, false);
  assert.equal(result.error, 'backend_auth_cooldown');
  assert.ok(Number(result.retry_after_seconds || 0) >= 1);
});

test('interactive Gmail auth waits longer than the default runtime message timeout', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = new ClearledgrQueueManager();

  const calls = [];
  manager.safeSendMessage = async (message, options = {}) => {
    calls.push({ message, options });
    return { success: false, error: 'auth_required' };
  };

  await manager.ensureGmailAuth(true);
  await manager.ensureGmailAuth(false);

  assert.equal(calls.length, 2);
  assert.equal(calls[0].message.action, 'ensureGmailAuth');
  assert.equal(calls[0].message.interactive, true);
  assert.equal(calls[0].options.timeoutMs, 180000);
  assert.equal(calls[1].message.interactive, false);
  assert.equal(calls[1].options.timeoutMs, 30000);
});

test('ensureGmailAuth retries runtime message failures', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = new ClearledgrQueueManager();
  let attempts = 0;
  manager.safeSendMessage = async () => {
    attempts += 1;
    if (attempts < 2) return { success: false, error: 'runtime_message_failed:Could not establish connection. Receiving end does not exist.' };
    return { success: true };
  };

  const result = await manager.ensureGmailAuth(true);

  assert.equal(result.success, true);
  assert.equal(attempts, 2);
});

test('auth result mapper returns operator-safe copy', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = new ClearledgrQueueManager();

  const cooldown = manager.describeAuthResult({ error: 'interactive_auth_cooldown', retry_after_seconds: 27 });
  assert.match(cooldown.toast, /try again in 27s/i);

  const mismatch = manager.describeAuthResult({ error: 'redirect_uri_mismatch' });
  assert.match(mismatch.toast, /redirect uri mismatch/i);

  const generic = manager.describeAuthResult({ error: 'authorization_failed' });
  assert.match(generic.toast, /authorization failed/i);

  const backendCooldown = manager.describeAuthResult({ error: 'backend_auth_cooldown', retry_after_seconds: 19 });
  assert.match(backendCooldown.toast, /try again in 19s/i);
});

test('refreshQueue does not poll ops endpoints in Gmail Work runtime', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = new ClearledgrQueueManager();
  manager.runtimeConfig = {
    valid: true,
    backendUrl: 'https://api.clearledgr.test',
    organizationId: 'default',
  };
  manager.emitQueueUpdated = () => {};
  manager.syncAgentSessions = async () => {};
  manager.hasBackendCredential = () => true;
  manager.getBackendAuthHeaders = (headers = {}) => ({ ...headers });

  const originalFetch = global.fetch;
  const urls = [];
  global.fetch = async (url) => {
    urls.push(String(url));
    if (String(url).includes('/extension/worklist?')) {
      return {
        status: 200,
        ok: true,
        async json() {
          return { items: [] };
        },
      };
    }
    return {
      status: 404,
      ok: false,
      async json() {
        return {};
      },
    };
  };

  try {
    await manager.refreshQueue();
  } finally {
    global.fetch = originalFetch;
  }

  assert.ok(urls.some((url) => url.includes('/extension/worklist?')));
  assert.equal(urls.some((url) => url.includes('/api/ops/')), false);
});

test('normalizeWorklistItem keeps attachment evidence flags stable', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = new ClearledgrQueueManager();

  const normalized = manager.normalizeWorklistItem({
    id: 'ap-1',
    attachment_count: '2',
  });

  assert.equal(normalized.has_attachment, true);
  assert.equal(normalized.attachment_count, 2);
});
