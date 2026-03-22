const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const backgroundPath = path.resolve(__dirname, '../background.js');
const backgroundSource = fs.readFileSync(backgroundPath, 'utf8');

test('interactive Gmail auth forces a fresh OAuth exchange', () => {
  assert.match(
    backgroundSource,
    /async function getAuthToken\(interactive = true, options = \{\}\)/,
    'getAuthToken should accept forceFresh options'
  );
  assert.match(
    backgroundSource,
    /const forceFresh = options\?\.forceFresh === true;/,
    'getAuthToken should detect forceFresh requests'
  );
  assert.match(
    backgroundSource,
    /if \(forceFresh\)\s*\{\s*await clearCachedAuthToken\(\);/m,
    'forceFresh auth should clear cached Gmail tokens before relaunching OAuth'
  );
  assert.match(
    backgroundSource,
    /getAuthToken\(wantsInteractive, \{ forceFresh: wantsInteractive \}\)/,
    'manual interactive auth should force a fresh code exchange'
  );
});
