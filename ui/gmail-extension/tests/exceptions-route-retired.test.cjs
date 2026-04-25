/**
 * Phase 3.4 — Exceptions retired from the primary nav.
 *
 * The thread-top exception banner from Phase 3.1 surfaces the same
 * severity + blocker + Suggest-reply CTA inline above any Gmail
 * thread tied to an exception-state AP item. With that contextual
 * surface in place, the dedicated `clearledgr/exceptions` queue
 * route stops being load-bearing for daily use — it moves to
 * LEGACY_ROUTES (still navigable via direct URL, but absent from the
 * left-nav menu).
 *
 * Pin three properties so a future "let's add Exceptions back to the
 * nav" PR can't silently un-retire it:
 *
 *   1. ROUTES no longer contains `clearledgr/exceptions`.
 *   2. LEGACY_ROUTES does, with `redirectTo: null` (renders the page
 *      when navigated to directly, doesn't bounce to Settings).
 *   3. The PAGE_MAP entry for `clearledgr/exceptions` still points at
 *      ExceptionsPage so direct-URL access keeps working — only the
 *      menu entry is gone.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(rel) {
  const abs = path.resolve(__dirname, '..', rel);
  return import(`${pathToFileURL(abs).href}?t=${Date.now()}`);
}

test('clearledgr/exceptions is absent from the primary nav (ROUTES)', async () => {
  const { ROUTES } = await importModule('src/routes/route-registry.js');
  const ids = ROUTES.map((r) => r.id);
  assert.ok(
    !ids.includes('clearledgr/exceptions'),
    `clearledgr/exceptions must not appear in primary nav routes; got ${JSON.stringify(ids)}`,
  );
});

test('clearledgr/exceptions stays accessible via LEGACY_ROUTES (redirectTo: null)', async () => {
  const { LEGACY_ROUTES, getLegacyRouteById } = await importModule('src/routes/route-registry.js');
  const legacyIds = LEGACY_ROUTES.map((r) => r.id);
  assert.ok(
    legacyIds.includes('clearledgr/exceptions'),
    `clearledgr/exceptions must be in LEGACY_ROUTES so direct-URL access keeps working; got ${JSON.stringify(legacyIds)}`,
  );
  const entry = getLegacyRouteById('clearledgr/exceptions');
  assert.ok(entry, 'getLegacyRouteById should return the exceptions entry');
  // null = render the page; a string would bounce the user elsewhere.
  // Bouncing exceptions to settings would silently break power-user
  // direct-URL access to the cross-thread exception queue.
  assert.equal(entry.redirectTo, null,
    'exceptions must render in place, not redirect — keeps the queue accessible to admins');
});

test('Settings now sits at navOrder 5 (was 6)', async () => {
  const { ROUTES } = await importModule('src/routes/route-registry.js');
  const settings = ROUTES.find((r) => r.id === 'clearledgr/settings');
  assert.ok(settings, 'settings route missing');
  assert.equal(settings.navOrder, 5, 'settings should slot into position 5 after exceptions retires');
});

test('PAGE_MAP still mounts ExceptionsPage for clearledgr/exceptions', () => {
  // The legacy route renders the page on direct URL access; the
  // dispatch table in inboxsdk-layer.js needs to keep the entry, not
  // delete it. Verify by source inspection — importing the layer
  // module from a Node test runner is heavy (it pulls InboxSDK).
  const SOURCE = fs.readFileSync(
    path.resolve(__dirname, '..', 'src', 'inboxsdk-layer.js'),
    'utf8',
  );
  assert.match(SOURCE, /'clearledgr\/exceptions': ExceptionsPage,/);
  assert.match(SOURCE, /import ExceptionsPage from/);
});
