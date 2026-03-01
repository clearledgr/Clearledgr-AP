const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const EXTENSION_ROOT = path.resolve(__dirname, '..');
const MANIFEST_PATH = path.join(EXTENSION_ROOT, 'manifest.json');
const DIST_BUNDLE_PATH = path.join(EXTENSION_ROOT, 'dist', 'inboxsdk-layer.js');

const FORBIDDEN_SNIPPETS = [
  'Agentic snapshot',
  'Source quality:',
  'Source quality',
  'Stale context',
  'Batch operations',
  'View raw agent events',
  'Ops mode:',
];

test('manifest content script is pinned to audited dist bundle', () => {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  const contentScripts = Array.isArray(manifest.content_scripts) ? manifest.content_scripts : [];
  assert.ok(contentScripts.length > 0, 'manifest.content_scripts should be present');
  const jsFiles = Array.isArray(contentScripts[0]?.js) ? contentScripts[0].js : [];
  assert.ok(jsFiles.includes('dist/inboxsdk-layer.js'));
  assert.equal(Boolean(manifest.action?.default_popup), false, 'default popup must be removed from shipped UX');
  assert.equal(Boolean(manifest.options_ui), false, 'options_ui must be removed from shipped UX');
});

test('dist bundle excludes forbidden legacy Gmail ops strings', () => {
  assert.ok(fs.existsSync(DIST_BUNDLE_PATH), 'dist/inboxsdk-layer.js should exist');
  const bundle = fs.readFileSync(DIST_BUNDLE_PATH, 'utf8');
  FORBIDDEN_SNIPPETS.forEach((snippet) => {
    assert.equal(
      bundle.includes(snippet),
      false,
      `dist bundle still contains forbidden snippet: ${snippet}`
    );
  });
});

test('legacy popup/options/demo surfaces are removed from extension package root', () => {
  const legacyFiles = [
    'popup.html',
    'popup.js',
    'options.html',
    'options.js',
    'demo-standalone.html',
    'demo-mode.js',
  ];
  legacyFiles.forEach((fileName) => {
    assert.equal(
      fs.existsSync(path.join(EXTENSION_ROOT, fileName)),
      false,
      `legacy extension surface should not exist in shipped root: ${fileName}`
    );
  });
});
