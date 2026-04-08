#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const { execSync } = require('node:child_process');
const {
  BUNDLE_FINGERPRINT_BANNER_PREFIX,
  computeSourceFingerprint,
} = require('./source-fingerprint.cjs');

const EXTENSION_ROOT = path.resolve(__dirname, '..');
const MANIFEST_PATH = path.join(EXTENSION_ROOT, 'manifest.json');
const DIST_BUNDLE_PATH = path.join(EXTENSION_ROOT, 'dist', 'inboxsdk-layer.js');
const FINGERPRINT_PATH = path.join(EXTENSION_ROOT, 'dist', 'inboxsdk-layer.meta.json');
const DIST_FILES = [
  path.join(EXTENSION_ROOT, 'dist', 'inboxsdk-layer.js'),
  path.join(EXTENSION_ROOT, 'dist', 'pageWorld.js'),
  FINGERPRINT_PATH,
];
const FORBIDDEN_ROOT_FILES = [
  'popup.html',
  'popup.js',
  'options.html',
  'options.js',
  'demo-standalone.html',
  'demo-mode.js',
];

const FORBIDDEN_SNIPPETS = [
  'Agentic snapshot',
  'Source quality:',
  'Source quality',
  'Stale context',
  'Batch operations',
  'View raw agent events',
  'Ops mode:',
];

function fail(message) {
  console.error(`[bundle-verify] ${message}`);
  process.exit(1);
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (error) {
    fail(`Unable to parse JSON from ${filePath}: ${error.message}`);
  }
}

function verifyManifestContract() {
  if (!fs.existsSync(MANIFEST_PATH)) fail(`Missing manifest: ${MANIFEST_PATH}`);
  const manifest = readJson(MANIFEST_PATH);
  const scripts = manifest?.content_scripts;
  if (!Array.isArray(scripts) || scripts.length < 2) {
    fail('manifest.content_scripts must include the route capture script and the audited Gmail bundle.');
  }
  const earlyScript = scripts[0];
  const bundleScript = scripts[1];
  const earlyJsList = Array.isArray(earlyScript?.js) ? earlyScript.js : [];
  const bundleJsList = Array.isArray(bundleScript?.js) ? bundleScript.js : [];
  if (!earlyJsList.includes('route-capture.js') || earlyScript?.run_at !== 'document_start') {
    fail('Manifest must capture Clearledgr direct-route intent at document_start before Gmail rewrites the hash.');
  }
  if (!bundleJsList.includes('dist/inboxsdk-layer.js') || bundleScript?.run_at !== 'document_idle') {
    fail('Manifest content script must include dist/inboxsdk-layer.js at document_idle.');
  }
}

function verifyDistFilesExist() {
  DIST_FILES.forEach((filePath) => {
    if (!fs.existsSync(filePath)) {
      fail(`Missing dist artifact: ${path.relative(EXTENSION_ROOT, filePath)}`);
    }
  });
}

function readBundle() {
  return fs.readFileSync(DIST_BUNDLE_PATH, 'utf8');
}

function verifyForbiddenContent(bundle) {
  for (const snippet of FORBIDDEN_SNIPPETS) {
    if (bundle.includes(snippet)) {
      fail(
        `Forbidden legacy snippet found in dist bundle: "${snippet}". Rebuild and remove legacy UI content from src.`
      );
    }
  }
}

function verifyNoLegacyRootSurfaces() {
  for (const fileName of FORBIDDEN_ROOT_FILES) {
    const filePath = path.join(EXTENSION_ROOT, fileName);
    if (fs.existsSync(filePath)) {
      fail(`Legacy extension UI surface must not exist in shipped root: ${fileName}`);
    }
  }
}

function verifyFingerprintParity() {
  if (!fs.existsSync(FINGERPRINT_PATH)) {
    fail(`Missing bundle fingerprint artifact: ${path.relative(EXTENSION_ROOT, FINGERPRINT_PATH)}`);
  }
  const metadata = readJson(FINGERPRINT_PATH);
  const expected = computeSourceFingerprint(EXTENSION_ROOT).fingerprint;
  const actual = String(metadata?.fingerprint || '').trim();
  if (!actual) {
    fail('Bundle fingerprint metadata is missing the fingerprint value.');
  }
  if (actual !== expected) {
    fail('dist bundle fingerprint is stale versus current source inputs. Run npm run build and commit dist changes.');
  }
  const bundle = readBundle();
  const expectedBanner = `${BUNDLE_FINGERPRINT_BANNER_PREFIX}${actual}`;
  if (!bundle.includes(expectedBanner)) {
    fail('dist bundle is missing the source fingerprint banner. Run npm run build to regenerate the audited bundle.');
  }
}

function verifyGitCleanDist() {
  if (!process.argv.includes('--check-git-clean')) return;
  try {
    execSync('git rev-parse --is-inside-work-tree', {
      cwd: EXTENSION_ROOT,
      stdio: 'ignore',
    });
  } catch (_) {
    return;
  }
  const diffTargets = DIST_FILES.map((filePath) => path.relative(EXTENSION_ROOT, filePath)).join(' ');
  try {
    execSync(`git diff --exit-code -- ${diffTargets}`, {
      cwd: EXTENSION_ROOT,
      stdio: 'pipe',
    });
  } catch (_) {
    fail('dist artifacts are stale versus source build output. Run npm run build and commit dist changes.');
  }
}

function verifyBundleParity() {
  verifyManifestContract();
  verifyDistFilesExist();
  verifyForbiddenContent(readBundle());
  verifyNoLegacyRootSurfaces();
  verifyFingerprintParity();
  verifyGitCleanDist();
  console.log('[bundle-verify] OK');
}

if (require.main === module) {
  verifyBundleParity();
}

module.exports = { verifyBundleParity };
