const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('pipeline slices classify AP queue items into finance-native views', async () => {
  const {
    countItemsForSlice,
    getPipelineBlockerKinds,
    matchesPipelineSlice,
  } = await importModule('src/routes/pipeline-views.js');

  const now = new Date('2026-03-19T09:00:00Z');
  const items = [
    { id: 'approval-1', state: 'needs_approval', due_date: '2026-03-21T00:00:00Z' },
    { id: 'post-1', state: 'ready_to_post', due_date: '2026-03-22T00:00:00Z' },
    { id: 'info-1', state: 'needs_info' },
    { id: 'exception-1', state: 'validated', exception_code: 'po_missing_reference' },
    { id: 'overdue-1', state: 'validated', due_date: '2026-03-18T00:00:00Z' },
    { id: 'posted-1', state: 'posted_to_erp', due_date: '2026-03-20T00:00:00Z' },
  ];

  assert.equal(countItemsForSlice(items, 'approval_backlog', now), 1);
  assert.equal(countItemsForSlice(items, 'ready_to_post', now), 1);
  assert.equal(countItemsForSlice(items, 'needs_info', now), 1);
  assert.equal(countItemsForSlice(items, 'exceptions', now), 1);
  assert.equal(countItemsForSlice(items, 'due_soon', now), 3);
  assert.equal(matchesPipelineSlice(items[5], 'all_open', now), false);
  assert.deepEqual(getPipelineBlockerKinds(items[3]), ['exception', 'po']);
});

test('pipeline preferences persist sparse saved views and slice activation', async () => {
  const {
    activatePipelineSlice,
    createSavedPipelineView,
    readPipelinePreferences,
    removeSavedPipelineView,
  } = await importModule('src/routes/pipeline-views.js');

  const storage = new Map();
  global.window = {
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
    },
  };

  const orgId = 'org-eu-1';
  const afterSlice = activatePipelineSlice(orgId, 'approval_backlog');
  assert.equal(afterSlice.activeSliceId, 'approval_backlog');

  const afterSave = createSavedPipelineView(orgId, {
    name: 'Exceptions this week',
    snapshot: {
      activeSliceId: 'exceptions',
      sortCol: 'due_date',
      sortDir: 'asc',
      filters: { state: 'all', due: 'due_7d', blocker: 'exception', amount: 'all' },
    },
  });
  assert.equal(afterSave.customViews.length, 1);
  assert.equal(afterSave.customViews[0].name, 'Exceptions this week');

  const reloaded = readPipelinePreferences(orgId);
  assert.equal(reloaded.customViews.length, 1);
  assert.equal(reloaded.customViews[0].snapshot.activeSliceId, 'exceptions');

  const afterRemove = removeSavedPipelineView(orgId, reloaded.customViews[0].id);
  assert.equal(afterRemove.customViews.length, 0);

  delete global.window;
});

test('pipeline saved views stay org-scoped and cap stored custom views', async () => {
  const {
    createSavedPipelineView,
    readPipelinePreferences,
  } = await importModule('src/routes/pipeline-views.js');

  const storage = new Map();
  global.window = {
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
    },
  };

  for (let index = 0; index < 10; index += 1) {
    createSavedPipelineView('org-eu-1', {
      name: `View ${index + 1}`,
      snapshot: {
        activeSliceId: index % 2 === 0 ? 'approval_backlog' : 'exceptions',
      },
    });
  }
  createSavedPipelineView('org-africa-1', {
    name: 'Africa approvals',
    snapshot: {
      activeSliceId: 'approval_backlog',
    },
  });

  const europeViews = readPipelinePreferences('org-eu-1').customViews;
  const africaViews = readPipelinePreferences('org-africa-1').customViews;

  assert.equal(europeViews.length, 8);
  assert.equal(africaViews.length, 1);
  assert.equal(africaViews[0].name, 'Africa approvals');
  assert.equal(europeViews.some((view) => view.name === 'Africa approvals'), false);

  delete global.window;
});
