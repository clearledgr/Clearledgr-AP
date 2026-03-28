const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('navigateInboxRoute uses InboxSDK router when available', async () => {
  const { navigateInboxRoute } = await importModule('src/utils/inbox-route.js');
  const calls = [];
  const ok = navigateInboxRoute('clearledgr/vendor/:name', {
    Router: {
      goto(routeId, params) {
        calls.push({ routeId, params });
        return true;
      },
    },
  }, { name: 'Anysphere, Inc' });

  assert.equal(ok, true);
  assert.deepEqual(calls, [{
    routeId: 'clearledgr/vendor/:name',
    params: { name: 'Anysphere, Inc' },
  }]);
});

test('navigateInboxRoute falls back to window hash when router is unavailable', async () => {
  const { navigateInboxRoute } = await importModule('src/utils/inbox-route.js');
  global.window = {
    location: {
      hash: '',
    },
  };

  const ok = navigateInboxRoute('clearledgr/vendor/:name', null, { name: 'Google Cloud EMEA Limited' });

  assert.equal(ok, true);
  assert.equal(window.location.hash, '#clearledgr/vendor/Google%20Cloud%20EMEA%20Limited');

  delete global.window;
});
