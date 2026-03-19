#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');

function parseArgs(argv = process.argv.slice(2)) {
  const options = {
    releaseId: '',
    evidencePath: '',
    outputPath: '',
    screenshotPath: '',
  };

  for (let i = 0; i < argv.length; i += 1) {
    const token = String(argv[i] || '');
    const next = argv[i + 1];
    if (token === '--release-id' || token === '--release') {
      options.releaseId = String(next || '').trim();
      i += 1;
      continue;
    }
    if (token === '--evidence' || token === '--evidence-json') {
      options.evidencePath = String(next || '').trim();
      i += 1;
      continue;
    }
    if (token === '--output') {
      options.outputPath = String(next || '').trim();
      i += 1;
      continue;
    }
    if (token === '--screenshot') {
      options.screenshotPath = String(next || '').trim();
      i += 1;
      continue;
    }
  }

  return options;
}

function readEvidenceFile(evidencePath) {
  const resolved = path.resolve(evidencePath);
  const raw = fs.readFileSync(resolved, 'utf8');
  const parsed = JSON.parse(raw);
  return { evidence: parsed, evidencePath: resolved };
}

function validateEvidence(evidence, { requireAuth = true } = {}) {
  const errors = [];
  const warnings = [];
  const payload = evidence && typeof evidence === 'object' ? evidence : {};

  if (!payload.status) errors.push('missing status');
  if (!payload.started_at) errors.push('missing started_at');
  if (!payload.finished_at) errors.push('missing finished_at');
  if (!payload.current_url) errors.push('missing current_url');
  if (!payload.page_title) warnings.push('missing page_title');

  if (String(payload.status || '').toLowerCase() !== 'passed') {
    errors.push(`status_not_passed:${payload.status || 'unknown'}`);
  }

  if (requireAuth || payload.assert_auth) {
    if (!payload.extension_worker_detected) {
      errors.push('extension_worker_not_detected');
    }
    const mountedSections = Number(payload.mounted_sections || 0);
    const entryPointsDetected = Number(payload.entry_points_detected || 0);
    if (
      (!Number.isFinite(mountedSections) || mountedSections < 2)
      && (!Number.isFinite(entryPointsDetected) || entryPointsDetected < 1)
    ) {
      errors.push(`insufficient_runtime_surface:${payload.mounted_sections || 0}:${payload.entry_points_detected || 0}`);
    }
  }

  const urlText = String(payload.current_url || '');
  if (urlText && !urlText.startsWith('https://mail.google.com/')) {
    warnings.push(`non_gmail_runtime_url:${urlText}`);
  }

  const missingSelectors = Array.isArray(payload.missing_selectors)
    ? payload.missing_selectors
    : [];
  if (missingSelectors.length > 0) {
    warnings.push(`missing_selectors:${missingSelectors.join(',')}`);
  }

  return { errors, warnings };
}

function buildMarkdownReport({
  releaseId,
  evidencePath,
  screenshotPath = '',
  evidence,
  validation,
  generatedAt = new Date().toISOString(),
}) {
  const status = String(evidence.status || 'unknown');
  const assertAuth = Boolean(evidence.assert_auth);
  const workerDetected = Boolean(evidence.extension_worker_detected);
  const mounted = Number(evidence.mounted_sections || 0);
  const entryPointsDetected = Number(evidence.entry_points_detected || 0);
  const missingSelectors = Array.isArray(evidence.missing_selectors)
    ? evidence.missing_selectors
    : [];

  const lines = [
    '# Gmail Runtime E2E Evidence',
    '',
    `- Release ID: \`${releaseId}\``,
    `- Generated at: \`${generatedAt}\``,
    `- Evidence JSON: \`${evidencePath}\``,
    screenshotPath ? `- Screenshot: \`${screenshotPath}\`` : '- Screenshot: `not_provided`',
    '',
    '## Result Snapshot',
    '',
    '| Field | Value |',
    '|---|---|',
    `| status | \`${status}\` |`,
    `| assert_auth | \`${assertAuth}\` |`,
    `| current_url | \`${String(evidence.current_url || '')}\` |`,
    `| page_title | \`${String(evidence.page_title || '').replace(/\|/g, '\\|')}\` |`,
    `| extension_worker_detected | \`${workerDetected}\` |`,
    `| mounted_sections | \`${mounted}\` |`,
    `| entry_points_detected | \`${entryPointsDetected}\` |`,
    `| missing_selectors | \`${missingSelectors.join(', ') || 'none'}\` |`,
    `| started_at | \`${String(evidence.started_at || '')}\` |`,
    `| finished_at | \`${String(evidence.finished_at || '')}\` |`,
    '',
    '## Validation',
    '',
    `- Errors: ${validation.errors.length ? validation.errors.map((e) => `\`${e}\``).join(', ') : '`none`'}`,
    `- Warnings: ${validation.warnings.length ? validation.warnings.map((w) => `\`${w}\``).join(', ') : '`none`'}`,
    '',
  ];

  if (validation.errors.length) {
    lines.push('## Outcome', '', '- `FAILED` evidence validation');
  } else {
    lines.push('## Outcome', '', '- `PASSED` evidence validation');
  }

  return `${lines.join('\n')}\n`;
}

function defaultOutputPathForRelease(releaseId) {
  const root = path.resolve(__dirname, '..', '..', '..');
  return path.join(root, 'docs', 'ga-evidence', 'releases', releaseId, 'GMAIL_RUNTIME_E2E.md');
}

function generateEvidenceReport({
  releaseId,
  evidencePath,
  outputPath = '',
  screenshotPath = '',
  requireAuth = true,
}) {
  if (!releaseId) throw new Error('missing_release_id');
  if (!evidencePath) throw new Error('missing_evidence_path');

  const { evidence, evidencePath: resolvedEvidencePath } = readEvidenceFile(evidencePath);
  const validation = validateEvidence(evidence, { requireAuth });
  const resolvedOutputPath = path.resolve(outputPath || defaultOutputPathForRelease(releaseId));
  fs.mkdirSync(path.dirname(resolvedOutputPath), { recursive: true });

  const resolvedScreenshot = screenshotPath ? path.resolve(screenshotPath) : '';
  const markdown = buildMarkdownReport({
    releaseId,
    evidencePath: resolvedEvidencePath,
    screenshotPath: resolvedScreenshot,
    evidence,
    validation,
  });
  fs.writeFileSync(resolvedOutputPath, markdown, 'utf8');

  return {
    outputPath: resolvedOutputPath,
    validation,
  };
}

function printUsage() {
  const usage = [
    'Usage:',
    '  node scripts/gmail-e2e-evidence.cjs --release-id <release_id> --evidence <path-to-evidence-json> [--output <path>] [--screenshot <path>]',
    '',
    'Example:',
    '  node scripts/gmail-e2e-evidence.cjs --release-id ap-v1-2026-03-01-pilot-rc1 --evidence ../../docs/ga-evidence/releases/ap-v1-2026-03-01-pilot-rc1/artifacts/gmail-e2e-evidence.json',
  ].join('\n');
  process.stderr.write(`${usage}\n`);
}

if (require.main === module) {
  try {
    const args = parseArgs();
    if (!args.releaseId || !args.evidencePath) {
      printUsage();
      process.exitCode = 2;
    } else {
      const report = generateEvidenceReport({
        releaseId: args.releaseId,
        evidencePath: args.evidencePath,
        outputPath: args.outputPath,
        screenshotPath: args.screenshotPath,
        requireAuth: true,
      });
      process.stdout.write(`Gmail E2E evidence report: ${report.outputPath}\n`);
      if (report.validation.warnings.length) {
        process.stdout.write(`Warnings: ${report.validation.warnings.join(', ')}\n`);
      }
      if (report.validation.errors.length) {
        process.stderr.write(`Validation errors: ${report.validation.errors.join(', ')}\n`);
        process.exitCode = 1;
      }
    }
  } catch (error) {
    process.stderr.write(`${String(error && error.message ? error.message : error)}\n`);
    process.exitCode = 1;
  }
}

module.exports = {
  parseArgs,
  validateEvidence,
  buildMarkdownReport,
  generateEvidenceReport,
  defaultOutputPathForRelease,
};
