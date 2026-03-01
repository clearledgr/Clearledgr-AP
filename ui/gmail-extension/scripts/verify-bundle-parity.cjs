#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const { execSync } = require('node:child_process');

const EXTENSION_ROOT = path.resolve(__dirname, '..');
const MANIFEST_PATH = path.join(EXTENSION_ROOT, 'manifest.json');
const DIST_BUNDLE_PATH = path.join(EXTENSION_ROOT, 'dist', 'inboxsdk-layer.js');
const DIST_FILES = [
  path.join(EXTENSION_ROOT, 'dist', 'inboxsdk-layer.js'),
  path.join(EXTENSION_ROOT, 'dist', 'pageWorld.js'),
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
  if (!Array.isArray(scripts) || scripts.length === 0) {
    fail('manifest.content_scripts is missing.');
  }
  const contentScript = scripts[0];
  const jsList = Array.isArray(contentScript?.js) ? contentScript.js : [];
  if (!jsList.includes('dist/inboxsdk-layer.js')) {
    fail('Manifest content script must include dist/inboxsdk-layer.js');
  }
}

function verifyDistFilesExist() {
  DIST_FILES.forEach((filePath) => {
    if (!fs.existsSync(filePath)) {
      fail(`Missing dist artifact: ${path.relative(EXTENSION_ROOT, filePath)}`);
    }
  });
}

function verifyForbiddenContent() {
  const bundle = fs.readFileSync(DIST_BUNDLE_PATH, 'utf8');
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

function main() {
  verifyManifestContract();
  verifyDistFilesExist();
  verifyForbiddenContent();
  verifyNoLegacyRootSurfaces();
  verifyGitCleanDist();
  console.log('[bundle-verify] OK');
}

main();
