const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const EXTENSION_ROOT = path.resolve(__dirname, '..');
const INBOXSDK_SOURCE = path.join(EXTENSION_ROOT, 'src', 'inboxsdk-layer.js');
const LEGACY_SOURCE = path.join(EXTENSION_ROOT, 'src', 'inboxsdk-layer.legacy.js');
const SIDEBAR_SOURCE = path.join(EXTENSION_ROOT, 'src', 'components', 'SidebarApp.js');
const MANIFEST_PATH = path.join(EXTENSION_ROOT, 'manifest.json');
const BACKGROUND_SOURCE = path.join(EXTENSION_ROOT, 'background.js');

function read(filePath) {
  return fs.readFileSync(filePath, 'utf8');
}

test('gmail entrypoint mounts a single AP sidebar and keeps page navigation in Gmail routes', () => {
  const source = read(INBOXSDK_SOURCE);

  assert.match(source, /title:\s*'Clearledgr AP'/);
  assert.doesNotMatch(source, /Clearledgr Ops/);
  assert.match(source, /registerAppMenuAndRoutes\(\)/);
  assert.match(source, /Router\.handleCustomRoute/);
  assert.doesNotMatch(source, /triggerAutoAuth\(/);
});

test('work sidebar source no longer uses the legacy reject event bridge', () => {
  const source = read(SIDEBAR_SOURCE);

  assert.match(source, /queueManager\.rejectInvoice\(item,\s*\{\s*reason\s*\}\)/);
  assert.doesNotMatch(source, /clearledgr:reject-invoice/);
});

test('work sidebar source does not render illegal Approve & Post actions for approved or needs_approval items', () => {
  const source = read(SIDEBAR_SOURCE);

  assert.doesNotMatch(source, /Approve\s*&\s*Post/);
  assert.match(source, /preview_erp_post/);
  assert.match(source, /prepare_info_request/);
  assert.match(source, /nudge_approver/);
});

test('manifest ships the audited Gmail bundle as the only inbox content script', () => {
  const manifest = JSON.parse(read(MANIFEST_PATH));
  const earlyContentScriptJs = manifest.content_scripts?.[0]?.js || [];
  const contentScriptJs = manifest.content_scripts?.[1]?.js || [];

  assert.deepEqual(earlyContentScriptJs, ['route-capture.js']);
  assert.deepEqual(contentScriptJs, ['config.js', 'dist/inboxsdk-layer.js']);
});

test('background worker preserves fresh-tab Clearledgr route intent before Gmail rewrites the hash', () => {
  const source = read(BACKGROUND_SOURCE);

  assert.match(source, /TAB_PENDING_DIRECT_ROUTE_PREFIX/);
  assert.match(source, /chrome\.tabs\.onUpdated\.addListener/);
  assert.match(source, /storePendingDirectRouteForTab/);
  assert.match(source, /getPendingDirectRouteForTab/);
  assert.match(source, /clearPendingDirectRouteForTab/);
});

test('dead legacy Gmail renderer source is removed from the active codebase', () => {
  assert.equal(fs.existsSync(LEGACY_SOURCE), false);
});
