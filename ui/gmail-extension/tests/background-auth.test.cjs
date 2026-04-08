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
    /const stored = forceFresh[\s\S]*await clearCachedAuthToken\(\);/m,
    'forceFresh auth should bypass stored Gmail tokens and clear cached Gmail tokens before relaunching OAuth'
  );
  assert.match(
    backgroundSource,
    /getAuthToken\(wantsInteractive, \{ forceFresh: wantsInteractive \}\)/,
    'manual interactive auth should force a fresh code exchange'
  );
});

test('background auth bridge forwards backend credentials from either payload shape', () => {
  assert.match(
    backgroundSource,
    /backendAccessToken:\s*payload\?\.backend_access_token\s*\|\|\s*payload\?\.backendAccessToken\s*\|\|\s*null/,
    'message bridge should forward backend tokens from snake_case or camelCase payloads'
  );
  assert.match(
    backgroundSource,
    /organizationId:\s*payload\?\.organization_id\s*\|\|\s*payload\?\.organizationId\s*\|\|\s*'default'/,
    'message bridge should forward organization ids from snake_case or camelCase payloads'
  );
  assert.match(
    backgroundSource,
    /backendRegistration\?\.backend_access_token[\s\S]*backendRegistration\?\.backendAccessToken[\s\S]*\|\|\s*token/,
    'auth flow should prefer backend-issued tokens and only fall back to the Google token'
  );
});
