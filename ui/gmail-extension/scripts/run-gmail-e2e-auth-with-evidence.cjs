#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const { generateEvidenceReport } = require('./gmail-e2e-evidence.cjs');

function parseArgs(argv = process.argv.slice(2)) {
  const options = {
    releaseId: process.env.GMAIL_E2E_RELEASE_ID || '',
    outputDir: '',
    profileDir: process.env.GMAIL_E2E_PROFILE_DIR || '',
    executablePath: process.env.GMAIL_E2E_EXECUTABLE_PATH || '',
    profileDirectory: process.env.GMAIL_E2E_PROFILE_DIRECTORY || '',
  };
  for (let i = 0; i < argv.length; i += 1) {
    const token = String(argv[i] || '');
    const next = argv[i + 1];
    if (token === '--release-id' || token === '--release') {
      options.releaseId = String(next || '').trim();
      i += 1;
      continue;
    }
    if (token === '--output-dir') {
      options.outputDir = String(next || '').trim();
      i += 1;
      continue;
    }
    if (token === '--profile-dir') {
      options.profileDir = String(next || '').trim();
      i += 1;
      continue;
    }
    if (token === '--executable-path') {
      options.executablePath = String(next || '').trim();
      i += 1;
      continue;
    }
    if (token === '--profile-directory') {
      options.profileDirectory = String(next || '').trim();
      i += 1;
      continue;
    }
  }
  return options;
}

function defaultReleaseOutputDir(releaseId) {
  const root = path.resolve(__dirname, '..', '..', '..');
  return path.join(root, 'docs', 'ga-evidence', 'releases', releaseId, 'artifacts');
}

function run() {
  const options = parseArgs();
  if (!options.releaseId) {
    process.stderr.write(
      'Missing release id. Use --release-id <release_id> or set GMAIL_E2E_RELEASE_ID.\n',
    );
    process.exitCode = 2;
    return;
  }

  const outputDir = path.resolve(options.outputDir || defaultReleaseOutputDir(options.releaseId));
  fs.mkdirSync(outputDir, { recursive: true });

  const evidencePath = path.join(outputDir, 'gmail-e2e-evidence.json');
  const screenshotPath = path.join(outputDir, 'gmail-e2e-screenshot.png');
  const profileDir = options.profileDir || path.resolve(path.join(outputDir, '.gmail-profile'));

  const env = { ...process.env };
  env.RUN_GMAIL_E2E = '1';
  env.GMAIL_E2E_ASSERT_AUTH = '1';
  env.GMAIL_E2E_EVIDENCE_JSON = evidencePath;
  env.GMAIL_E2E_CAPTURE_PATH = screenshotPath;
  env.GMAIL_E2E_PROFILE_DIR = profileDir;
  if (options.executablePath) {
    env.GMAIL_E2E_EXECUTABLE_PATH = path.resolve(options.executablePath);
  }
  if (options.profileDirectory) {
    env.GMAIL_E2E_PROFILE_DIRECTORY = options.profileDirectory;
  }

  process.stdout.write(`Running authenticated Gmail E2E for release ${options.releaseId}\n`);
  process.stdout.write(`Evidence JSON: ${evidencePath}\n`);
  process.stdout.write(`Screenshot: ${screenshotPath}\n`);
  if (env.GMAIL_E2E_EXECUTABLE_PATH) {
    process.stdout.write(`Browser executable: ${env.GMAIL_E2E_EXECUTABLE_PATH}\n`);
  }
  if (env.GMAIL_E2E_PROFILE_DIRECTORY) {
    process.stdout.write(`Browser profile directory: ${env.GMAIL_E2E_PROFILE_DIRECTORY}\n`);
  }

  const testResult = spawnSync(
    process.execPath,
    ['--test', 'tests/inboxsdk-layer.e2e-smoke.test.cjs'],
    {
      cwd: path.resolve(__dirname, '..'),
      env,
      stdio: 'inherit',
    },
  );

  let report;
  try {
    report = generateEvidenceReport({
      releaseId: options.releaseId,
      evidencePath,
      outputPath: path.resolve(path.join(outputDir, '..', 'GMAIL_RUNTIME_E2E.md')),
      screenshotPath,
      requireAuth: true,
    });
    process.stdout.write(`Evidence report written: ${report.outputPath}\n`);
  } catch (error) {
    process.stderr.write(`Could not generate evidence report: ${String(error && error.message ? error.message : error)}\n`);
    process.exitCode = 1;
    return;
  }

  if (report.validation.warnings.length) {
    process.stdout.write(`Evidence warnings: ${report.validation.warnings.join(', ')}\n`);
  }
  if (report.validation.errors.length) {
    process.stderr.write(`Evidence validation failed: ${report.validation.errors.join(', ')}\n`);
    process.exitCode = 1;
    return;
  }

  if (typeof testResult.status === 'number' && testResult.status !== 0) {
    process.exitCode = testResult.status;
    return;
  }

  process.stdout.write('Authenticated Gmail E2E evidence run completed successfully.\n');
}

if (require.main === module) {
  run();
}

module.exports = {
  parseArgs,
  defaultReleaseOutputDir,
  run,
};
