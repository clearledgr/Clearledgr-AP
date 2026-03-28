#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const {
  BUNDLE_FINGERPRINT_BANNER_PREFIX,
  computeSourceFingerprint,
} = require('./source-fingerprint.cjs');
const { verifyBundleParity } = require('./verify-bundle-parity.cjs');

const EXTENSION_ROOT = path.resolve(__dirname, '..');
const DIST_ROOT = path.join(EXTENSION_ROOT, 'dist');
const FINGERPRINT_PATH = path.join(DIST_ROOT, 'inboxsdk-layer.meta.json');
const BUN_BIN = process.env.CLEARLEDGR_BUN_BIN || 'bun';
const BUILD_DEBUG = process.env.CLEARLEDGR_BUILD_DEBUG === '1';
const BUILD_DEBUG_FILE = String(process.env.CLEARLEDGR_BUILD_DEBUG_FILE || '').trim();

function parseArgs(argv) {
  const args = { config: 'webpack.dev.cjs', verify: false };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === '--config' && argv[i + 1]) {
      args.config = argv[i + 1];
      i += 1;
      continue;
    }
    if (token === '--verify') {
      args.verify = true;
    }
  }
  return args;
}

function debug(message) {
  if (!BUILD_DEBUG) return;
  const line = `[build-extension:debug] ${message}`;
  console.log(line);
  if (BUILD_DEBUG_FILE) {
    fs.appendFileSync(BUILD_DEBUG_FILE, `${line}\n`, 'utf8');
  }
}

function writeFingerprintMetadata(payload) {
  if (!fs.existsSync(DIST_ROOT)) {
    fs.mkdirSync(DIST_ROOT, { recursive: true });
  }
  fs.writeFileSync(FINGERPRINT_PATH, `${JSON.stringify({
    generated_at: new Date().toISOString(),
    ...payload,
  }, null, 2)}\n`, 'utf8');
}

function applyBundleFingerprintBanner(fingerprint) {
  const bundlePath = path.join(DIST_ROOT, 'inboxsdk-layer.js');
  if (!fs.existsSync(bundlePath)) {
    throw new Error(`Missing built bundle for fingerprint banner injection: ${bundlePath}`);
  }
  const bannerComment = `/* ${BUNDLE_FINGERPRINT_BANNER_PREFIX}${fingerprint} */`;
  const existing = fs.readFileSync(bundlePath, 'utf8');
  const normalized = existing.replace(
    new RegExp(`^/\\*\\s*${BUNDLE_FINGERPRINT_BANNER_PREFIX}[a-f0-9]+\\s*\\*/\\n?`),
    '',
  );
  fs.writeFileSync(bundlePath, `${bannerComment}\n${normalized}`, 'utf8');
}

function cleanDist() {
  fs.rmSync(DIST_ROOT, { recursive: true, force: true });
  fs.mkdirSync(DIST_ROOT, { recursive: true });
}

function runBundler(configFile) {
  const sourcemap = configFile.includes('prod') ? 'external' : 'none';
  const args = [
    'build',
    './src/inboxsdk-layer.js',
    './node_modules/@inboxsdk/core/pageWorld.js',
    '--target=browser',
    '--format=iife',
    `--sourcemap=${sourcemap}`,
    '--outdir',
    'dist',
    '--entry-naming=[name].js',
  ];
  debug(`bun:${BUN_BIN} ${args.join(' ')}`);
  const result = spawnSync(BUN_BIN, args, {
    cwd: EXTENSION_ROOT,
    encoding: 'utf8',
  });
  if (result.error) {
    if (result.error.code === 'ENOENT') {
      throw new Error(`Bun is required for extension builds. Install bun or set CLEARLEDGR_BUN_BIN to a valid Bun binary.`);
    }
    throw result.error;
  }
  if (result.status !== 0) {
    const stderr = String(result.stderr || '').trim();
    const stdout = String(result.stdout || '').trim();
    throw new Error(stderr || stdout || `bun build failed with exit code ${result.status}`);
  }
  return [result.stdout, result.stderr].filter(Boolean).join('');
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  debug(`args:${JSON.stringify(args)}`);
  console.log('[build-extension] computing source fingerprint...');
  const fingerprintStartedAt = Date.now();
  debug('fingerprint:start');
  const fingerprintPayload = computeSourceFingerprint(EXTENSION_ROOT);
  debug('fingerprint:done');
  debug(`fingerprint:${fingerprintPayload.fingerprint}`);
  console.log(`[build-extension] fingerprint ready in ${Date.now() - fingerprintStartedAt}ms`);
  cleanDist();
  debug('dist cleaned');
  console.log('[build-extension] bundling InboxSDK layer...');
  const summary = runBundler(args.config);
  debug('bun build complete');
  debug('injecting fingerprint banner');
  applyBundleFingerprintBanner(fingerprintPayload.fingerprint);
  debug('writing fingerprint metadata');
  writeFingerprintMetadata(fingerprintPayload);
  if (summary) {
    process.stdout.write(summary.endsWith('\n') ? summary : `${summary}\n`);
  }
  console.log(`[build-extension] wrote ${path.relative(EXTENSION_ROOT, FINGERPRINT_PATH)}`);

  if (args.verify) {
    debug('starting parity verification');
    console.log('[build-extension] verifying bundle parity...');
    verifyBundleParity();
  }

  await new Promise((resolve) => process.stdout.write('', resolve));
  await new Promise((resolve) => process.stderr.write('', resolve));
  process.exit(0);
}

main().catch((error) => {
  console.error('[build-extension] failed:', error?.message || error);
  process.exit(1);
});
