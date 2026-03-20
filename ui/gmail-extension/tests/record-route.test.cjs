const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('record route remembers the selected AP item and resolves it from storage', async () => {
  const storage = new Map();
  global.window = {
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); },
    },
  };

  const {
    ACTIVE_RECORD_ID_STORAGE_KEY,
    navigateToRecordDetail,
    resolveRecordRouteId,
  } = await importModule('src/utils/record-route.js');

  let navigatedTo = '';
  const ok = navigateToRecordDetail((routeId) => { navigatedTo = routeId; }, 'ap-item-123');

  assert.equal(ok, true);
  assert.equal(navigatedTo, 'clearledgr/invoice');
  assert.equal(storage.get(ACTIVE_RECORD_ID_STORAGE_KEY), 'ap-item-123');
  assert.equal(resolveRecordRouteId({}, ''), 'ap-item-123');

  delete global.window;
});
