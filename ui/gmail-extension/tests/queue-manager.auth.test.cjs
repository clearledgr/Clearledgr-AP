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

test('auth result mapper returns operator-safe copy', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = new ClearledgrQueueManager();

  const cooldown = manager.describeAuthResult({ error: 'interactive_auth_cooldown', retry_after_seconds: 27 });
  assert.match(cooldown.toast, /try again in 27s/i);

  const mismatch = manager.describeAuthResult({ error: 'redirect_uri_mismatch' });
  assert.match(mismatch.toast, /redirect uri mismatch/i);

  const generic = manager.describeAuthResult({ error: 'authorization_failed' });
  assert.match(generic.toast, /authorization failed/i);
});
