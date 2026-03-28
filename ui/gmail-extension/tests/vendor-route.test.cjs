const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('vendor route navigates with the explicit vendor name and keeps storage as fallback', async () => {
  const storage = new Map();
  global.window = {
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); },
    },
  };

  const {
    ACTIVE_VENDOR_NAME_STORAGE_KEY,
    navigateToVendorRecord,
    resolveVendorRouteName,
  } = await importModule('src/utils/vendor-route.js');

  const navigations = [];
  const ok = navigateToVendorRecord((routeId, params) => {
    navigations.push({ routeId, params });
  }, 'Google Cloud EMEA Limited');

  assert.equal(ok, true);
  assert.deepEqual(navigations, [{
    routeId: 'clearledgr/vendor/:name',
    params: { name: 'Google Cloud EMEA Limited' },
  }]);
  assert.equal(storage.get(ACTIVE_VENDOR_NAME_STORAGE_KEY), 'Google Cloud EMEA Limited');
  assert.equal(resolveVendorRouteName({}, ''), 'Google Cloud EMEA Limited');
  assert.equal(resolveVendorRouteName({}, '#clearledgr/vendor/Google%20Cloud%20EMEA%20Limited'), 'Google Cloud EMEA Limited');

  delete global.window;
});
