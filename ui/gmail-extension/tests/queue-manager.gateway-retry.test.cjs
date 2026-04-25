/**
 * `backendFetch` retries transient gateway failures.
 *
 * During Railway deploy rollovers the edge briefly returns 502/503/504
 * from the time gunicorn workers restart until the new ones accept
 * traffic — typically 1-3s. Without a retry, the extension loses
 * bootstrap data for the entire session because the failed promise
 * never re-fires (the auth-401 retry path doesn't apply to 5xx).
 *
 * This file pins:
 *   1. 502/503/504 trigger up to 2 retries with short spacing.
 *   2. The retry path stops as soon as a non-5xx response lands.
 *   3. A 200 on the first try takes the fast path (no retries).
 *   4. The auth-401 retry path is preserved (regression check —
 *      the new gateway-retry must not interfere with auth-driven
 *      retries that other tests depend on).
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

// queue-manager touches chrome.storage when it clears the cached
// backend token on a 401. Node has no chrome global; stub it with a
// noop in-memory store so the auth path can run end-to-end.
if (typeof globalThis.chrome === 'undefined') {
  const _store = {};
  globalThis.chrome = {
    storage: {
      local: {
        get: async (keys) => {
          if (Array.isArray(keys)) {
            const out = {};
            for (const k of keys) out[k] = _store[k];
            return out;
          }
          return { ..._store };
        },
        set: async (entries) => { Object.assign(_store, entries || {}); },
        remove: async (keys) => {
          const list = Array.isArray(keys) ? keys : [keys];
          for (const k of list) delete _store[k];
        },
      },
    },
  };
}

async function loadQueueManager() {
  const module = await import(pathToFileURL(path.resolve(__dirname, '../queue-manager.js')).href);
  return module.ClearledgrQueueManager;
}

function makeManagerWithToken(ClearledgrQueueManager) {
  const manager = new ClearledgrQueueManager();
  manager.runtimeConfig = { valid: true, organizationId: 'default', backendUrl: 'https://api.example.test' };
  // Bypass the persisted-token round trip so backendFetch goes
  // straight to fetch() and we can assert call ordering precisely.
  manager.backendAuthToken = 'token-abc';
  manager.backendAuthTokenExpiry = Date.now() + 600_000;
  manager.ensureBackendAuth = async () => ({ success: true });
  return manager;
}

function patchFetchSequence(responses) {
  const calls = [];
  const seq = [...responses];
  const originalFetch = global.fetch;
  global.fetch = async (url, init) => {
    calls.push({ url, init });
    const next = seq.shift();
    if (typeof next === 'function') return next();
    if (!next) return new Response('exhausted', { status: 500 });
    return new Response(next.body || '', { status: next.status });
  };
  return {
    calls,
    restore() { global.fetch = originalFetch; },
  };
}

test('backendFetch retries 502 up to twice and returns the eventual non-5xx response', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = makeManagerWithToken(ClearledgrQueueManager);
  const sequence = patchFetchSequence([
    { status: 502 },
    { status: 503 },
    { status: 200, body: '{"ok":true}' },
  ]);
  try {
    const response = await manager.backendFetch('https://api.example.test/api/saved-views');
    assert.equal(response.status, 200);
    assert.equal(sequence.calls.length, 3, 'expected initial + 2 retries');
  } finally {
    sequence.restore();
  }
});

test('backendFetch stops retrying as soon as a non-5xx lands', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = makeManagerWithToken(ClearledgrQueueManager);
  // 502 → 200 — should NOT proceed to a third call.
  const sequence = patchFetchSequence([
    { status: 502 },
    { status: 200, body: '{"ok":true}' },
    { status: 500, body: 'should not be reached' },
  ]);
  try {
    const response = await manager.backendFetch('https://api.example.test/api/workspace/bootstrap');
    assert.equal(response.status, 200);
    assert.equal(sequence.calls.length, 2, 'must stop at first non-5xx');
  } finally {
    sequence.restore();
  }
});

test('backendFetch does not retry on a 200', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = makeManagerWithToken(ClearledgrQueueManager);
  const sequence = patchFetchSequence([
    { status: 200, body: '{}' },
    { status: 500, body: 'should not be reached' },
  ]);
  try {
    const response = await manager.backendFetch('https://api.example.test/api/workspace/bootstrap');
    assert.equal(response.status, 200);
    assert.equal(sequence.calls.length, 1, 'happy path must not retry');
  } finally {
    sequence.restore();
  }
});

test('backendFetch still triggers the auth-401 retry path after the gateway retries (regression)', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = makeManagerWithToken(ClearledgrQueueManager);

  let authRefreshCalls = 0;
  manager.ensureBackendAuth = async () => {
    authRefreshCalls += 1;
    manager.backendAuthToken = 'token-fresh';
    return { success: true };
  };

  // Initial 401 should consume the auth-refresh + retry path. The
  // new gateway retry must not eat the 401-driven retry slot.
  const sequence = patchFetchSequence([
    { status: 401 },
    { status: 200, body: '{"ok":true}' },
  ]);
  try {
    const response = await manager.backendFetch('https://api.example.test/api/saved-views');
    assert.equal(response.status, 200);
    assert.equal(authRefreshCalls, 1, 'auth refresh must be triggered exactly once on 401');
    assert.equal(sequence.calls.length, 2, '401 + 1 retry = 2 calls total');
  } finally {
    sequence.restore();
  }
});

test('backendFetch returns the final 5xx if all retries also fail', async () => {
  const ClearledgrQueueManager = await loadQueueManager();
  const manager = makeManagerWithToken(ClearledgrQueueManager);
  const sequence = patchFetchSequence([
    { status: 502 },
    { status: 503 },
    { status: 504 },
  ]);
  try {
    const response = await manager.backendFetch('https://api.example.test/api/workspace/bootstrap');
    // The eventual response is the LAST one received — pinned so a
    // future change can't quietly swallow the upstream error code.
    assert.equal(response.status, 504);
    assert.equal(sequence.calls.length, 3);
  } finally {
    sequence.restore();
  }
});
