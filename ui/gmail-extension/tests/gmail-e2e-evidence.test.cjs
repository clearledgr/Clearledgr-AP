const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const {
  validateEvidence,
  generateEvidenceReport,
  parseArgs,
} = require('../scripts/gmail-e2e-evidence.cjs');

test('validateEvidence accepts a passing authenticated Gmail runtime payload', () => {
  const payload = {
    status: 'passed',
    started_at: '2026-02-28T10:00:00.000Z',
    finished_at: '2026-02-28T10:02:00.000Z',
    current_url: 'https://mail.google.com/mail/u/0/#inbox',
    page_title: 'Inbox - me@example.com - Gmail',
    assert_auth: true,
    extension_worker_detected: true,
    mounted_sections: 3,
    missing_selectors: [],
  };
  const result = validateEvidence(payload, { requireAuth: true });
  assert.deepEqual(result.errors, []);
  assert.deepEqual(result.warnings, []);
});

test('validateEvidence rejects failed/non-authenticated runtime payload', () => {
  const payload = {
    status: 'failed',
    started_at: '2026-02-28T10:00:00.000Z',
    finished_at: '2026-02-28T10:01:00.000Z',
    current_url: 'https://accounts.google.com/signin',
    page_title: 'Sign in - Google Accounts',
    assert_auth: true,
    extension_worker_detected: false,
    mounted_sections: 0,
    missing_selectors: ['#cl-thread-context'],
  };
  const result = validateEvidence(payload, { requireAuth: true });
  assert.ok(result.errors.includes('status_not_passed:failed'));
  assert.ok(result.errors.includes('extension_worker_not_detected'));
  assert.ok(result.errors.some((entry) => entry.startsWith('insufficient_mounted_sections:')));
});

test('generateEvidenceReport writes markdown summary in release folder shape', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'gmail-e2e-evidence-'));
  const evidencePath = path.join(tempRoot, 'gmail-e2e-evidence.json');
  const outputPath = path.join(tempRoot, 'GMAIL_RUNTIME_E2E.md');
  const payload = {
    status: 'passed',
    started_at: '2026-02-28T10:00:00.000Z',
    finished_at: '2026-02-28T10:02:00.000Z',
    current_url: 'https://mail.google.com/mail/u/0/#inbox',
    page_title: 'Inbox - me@example.com - Gmail',
    assert_auth: true,
    extension_worker_detected: true,
    mounted_sections: 3,
    missing_selectors: [],
  };
  fs.writeFileSync(evidencePath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');

  const report = generateEvidenceReport({
    releaseId: 'ap-v1-2026-03-01-pilot-rc1',
    evidencePath,
    outputPath,
    requireAuth: true,
  });

  assert.equal(report.validation.errors.length, 0);
  assert.ok(fs.existsSync(outputPath));
  const markdown = fs.readFileSync(outputPath, 'utf8');
  assert.match(markdown, /# Gmail Runtime E2E Evidence/);
  assert.match(markdown, /`PASSED` evidence validation/);
  assert.match(markdown, /ap-v1-2026-03-01-pilot-rc1/);
});

test('parseArgs maps cli flags into script options', () => {
  const parsed = parseArgs([
    '--release-id',
    'ap-v1-2026-03-02-pilot-rc1',
    '--evidence',
    './evidence.json',
    '--output',
    './report.md',
    '--screenshot',
    './capture.png',
  ]);
  assert.equal(parsed.releaseId, 'ap-v1-2026-03-02-pilot-rc1');
  assert.equal(parsed.evidencePath, './evidence.json');
  assert.equal(parsed.outputPath, './report.md');
  assert.equal(parsed.screenshotPath, './capture.png');
});
