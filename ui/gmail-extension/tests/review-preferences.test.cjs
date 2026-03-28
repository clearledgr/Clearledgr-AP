const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('review preferences persist scoped vendor searches safely', async () => {
  const storage = new Map();
  global.window = {
    localStorage: {
      getItem(key) {
        return storage.has(key) ? storage.get(key) : null;
      },
      setItem(key, value) {
        storage.set(key, String(value));
      },
      removeItem(key) {
        storage.delete(key);
      },
    },
  };

  const {
    defaultReviewPreferences,
    getReviewPreferenceKey,
    readReviewPreferences,
    writeReviewPreferences,
    clearReviewPreferences,
  } = await importModule('src/routes/review-preferences.js');

  const scope = { orgId: 'default', userEmail: 'ops@example.com' };
  assert.deepEqual(defaultReviewPreferences(), { searchQuery: '' });
  assert.equal(
    getReviewPreferenceKey(scope),
    'clearledgr_review_preferences_v1:default:ops@example.com',
  );

  writeReviewPreferences(scope, { searchQuery: 'Acme Vendor' });
  assert.deepEqual(readReviewPreferences(scope), { searchQuery: 'Acme Vendor' });

  clearReviewPreferences(scope);
  assert.deepEqual(readReviewPreferences(scope), { searchQuery: '' });

  delete global.window;
});
