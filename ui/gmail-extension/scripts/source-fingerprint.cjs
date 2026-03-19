#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const FINGERPRINT_INPUTS = [
  'src',
  'queue-manager.js',
  'background.js',
  'manifest.json',
  'webpack.common.cjs',
  'webpack.dev.cjs',
  'webpack.prod.cjs',
  'package.json',
];

const BUNDLE_FINGERPRINT_BANNER_PREFIX = 'clearledgr-source-fingerprint:';

function walkFiles(targetPath) {
  const stat = fs.statSync(targetPath);
  if (stat.isFile()) return [targetPath];
  if (!stat.isDirectory()) return [];
  return fs.readdirSync(targetPath)
    .sort((left, right) => left.localeCompare(right))
    .flatMap((entry) => walkFiles(path.join(targetPath, entry)));
}

function getFingerprintInputFiles(extensionRoot) {
  return FINGERPRINT_INPUTS
    .map((entry) => path.join(extensionRoot, entry))
    .filter((entry) => fs.existsSync(entry))
    .flatMap((entry) => walkFiles(entry))
    .filter((filePath) => !filePath.includes(`${path.sep}__tests__${path.sep}`))
    .filter((filePath) => !filePath.endsWith('.test.js'))
    .filter((filePath) => !filePath.endsWith('.test.cjs'))
    .sort((left, right) => left.localeCompare(right));
}

function computeSourceFingerprint(extensionRoot) {
  const hash = crypto.createHash('sha256');
  const files = getFingerprintInputFiles(extensionRoot);

  files.forEach((filePath) => {
    hash.update(path.relative(extensionRoot, filePath));
    hash.update('\n');
    hash.update(fs.readFileSync(filePath));
    hash.update('\n');
  });

  return {
    fingerprint: hash.digest('hex'),
    inputs: files.map((filePath) => path.relative(extensionRoot, filePath)),
  };
}

module.exports = {
  BUNDLE_FINGERPRINT_BANNER_PREFIX,
  FINGERPRINT_INPUTS,
  computeSourceFingerprint,
  getFingerprintInputFiles,
};
