const test = require('node:test');
const assert = require('node:assert/strict');

const {
  parseArgs,
  defaultReleaseOutputDir,
} = require('../scripts/run-gmail-e2e-auth-with-evidence.cjs');

test('parseArgs resolves release/output/profile flags', () => {
  const parsed = parseArgs([
    '--release-id',
    'ap-v1-2026-03-04-pilot-rc1',
    '--output-dir',
    '/tmp/evidence',
    '--profile-dir',
    '/tmp/gmail-profile',
    '--executable-path',
    '/Applications/Comet.app/Contents/MacOS/Comet',
    '--profile-directory',
    'Default',
  ]);
  assert.equal(parsed.releaseId, 'ap-v1-2026-03-04-pilot-rc1');
  assert.equal(parsed.outputDir, '/tmp/evidence');
  assert.equal(parsed.profileDir, '/tmp/gmail-profile');
  assert.equal(parsed.executablePath, '/Applications/Comet.app/Contents/MacOS/Comet');
  assert.equal(parsed.profileDirectory, 'Default');
});

test('defaultReleaseOutputDir points to release artifacts path', () => {
  const outputDir = defaultReleaseOutputDir('ap-v1-2026-03-04-pilot-rc1');
  assert.match(
    outputDir,
    /docs\/ga-evidence\/releases\/ap-v1-2026-03-04-pilot-rc1\/artifacts$/,
  );
});
