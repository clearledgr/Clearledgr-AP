#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const { computeSourceFingerprint } = require('./source-fingerprint.cjs');

const EXTENSION_ROOT = path.resolve(__dirname, '..');
const DIST_ROOT = path.join(EXTENSION_ROOT, 'dist');
const FINGERPRINT_PATH = path.join(DIST_ROOT, 'inboxsdk-layer.meta.json');

function main() {
  if (!fs.existsSync(DIST_ROOT)) {
    throw new Error(`Missing dist directory: ${DIST_ROOT}`);
  }
  const payload = {
    generated_at: new Date().toISOString(),
    ...computeSourceFingerprint(EXTENSION_ROOT),
  };
  fs.writeFileSync(FINGERPRINT_PATH, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  console.log(`[bundle-fingerprint] wrote ${path.relative(EXTENSION_ROOT, FINGERPRINT_PATH)}`);
}

main();
