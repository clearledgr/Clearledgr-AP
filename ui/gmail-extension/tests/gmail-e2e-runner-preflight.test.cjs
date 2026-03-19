const test = require('node:test');
const assert = require('node:assert/strict');

const {
  parseArgs,
} = require('../scripts/gmail-e2e-runner-preflight.cjs');

test('preflight parseArgs resolves profile, executable, and skip flags', () => {
  const parsed = parseArgs([
    '--profile-dir',
    '/tmp/gmail-profile',
    '--executable-path',
    '/Applications/Comet.app/Contents/MacOS/Comet',
    '--profile-directory',
    'Default',
    '--skip-browser-launch',
  ]);
  assert.equal(parsed.profileDir, '/tmp/gmail-profile');
  assert.equal(parsed.executablePath, '/Applications/Comet.app/Contents/MacOS/Comet');
  assert.equal(parsed.profileDirectory, 'Default');
  assert.equal(parsed.skipBrowserLaunch, true);
});
