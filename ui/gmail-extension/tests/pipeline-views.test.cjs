const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('pipeline slices classify AP queue items into AP-first queue views', async () => {
  const {
    countItemsForSlice,
    getPipelineBlockerKinds,
    getSuggestedPipelineSlice,
    matchesPipelineSlice,
  } = await importModule('src/routes/pipeline-views.js');

  const now = new Date('2026-03-19T09:00:00Z');
  const items = [
    { id: 'approval-1', state: 'needs_approval', due_date: '2026-03-21T00:00:00Z' },
    { id: 'post-1', state: 'ready_to_post', due_date: '2026-03-22T00:00:00Z' },
    { id: 'info-1', state: 'needs_info' },
    { id: 'failed-1', state: 'failed_post', due_date: '2026-03-22T00:00:00Z' },
    { id: 'exception-1', state: 'validated', exception_code: 'po_missing_reference' },
    { id: 'entity-1', state: 'validated', entity_routing_status: 'needs_review' },
    { id: 'due-soon-1', state: 'validated', due_date: '2026-03-24T00:00:00Z' },
    { id: 'overdue-1', state: 'validated', due_date: '2026-03-18T00:00:00Z' },
    { id: 'posted-1', state: 'posted_to_erp', due_date: '2026-03-20T00:00:00Z' },
  ];

  assert.equal(countItemsForSlice(items, 'waiting_on_approval', now), 1);
  assert.equal(countItemsForSlice(items, 'ready_to_post', now), 1);
  assert.equal(countItemsForSlice(items, 'needs_info', now), 1);
  assert.equal(countItemsForSlice(items, 'failed_post', now), 1);
  assert.equal(countItemsForSlice(items, 'blocked_exception', now), 3);
  assert.equal(countItemsForSlice(items, 'due_soon', now), 4);
  assert.equal(countItemsForSlice(items, 'overdue', now), 1);
  assert.equal(matchesPipelineSlice(items[8], 'all_open', now), false);
  assert.deepEqual(getPipelineBlockerKinds(items[4]), ['exception', 'po']);
  assert.deepEqual(getPipelineBlockerKinds(items[5]), ['entity']);
  assert.deepEqual(getPipelineBlockerKinds(null), []);
  assert.equal(getSuggestedPipelineSlice(items[5]), 'blocked_exception');
});

test('pipeline preferences persist pinned saved views and focused item state per user and org', async () => {
  const {
    activatePipelineSlice,
    createSavedPipelineView,
    focusPipelineItem,
    getPinnedPipelineViews,
    getStarterPipelineViews,
    readPipelineNavigation,
    readPipelinePreferences,
    removeSavedPipelineView,
    updateSavedPipelineView,
  } = await importModule('src/routes/pipeline-views.js');

  const storage = new Map();
  global.window = {
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
    },
  };

  const scope = { orgId: 'org-eu-1', userEmail: 'ops@clearledgr.com' };
  const otherUserScope = { orgId: 'org-eu-1', userEmail: 'backup@clearledgr.com' };

  const afterSlice = activatePipelineSlice(scope, 'waiting_on_approval');
  assert.equal(afterSlice.activeSliceId, 'waiting_on_approval');
  assert.deepEqual(afterSlice.pinnedViewRefs, ['starter:approval_chase', 'starter:urgent_due']);

  const afterSave = createSavedPipelineView(scope, {
    name: 'Exceptions this week',
    pinned: true,
    description: 'Blocked invoices due this week.',
    snapshot: {
      activeSliceId: 'blocked_exception',
      sortCol: 'due_date',
      sortDir: 'asc',
      filters: {
        due: 'due_7d',
        blocker: 'exception',
        amount: 'all',
        approvalAge: 'all',
        erpStatus: 'all',
      },
    },
  });
  assert.equal(afterSave.customViews.length, 1);
  assert.equal(afterSave.customViews[0].name, 'Exceptions this week');
  assert.equal(afterSave.customViews[0].description, 'Blocked invoices due this week.');
  assert.equal(afterSave.pinnedViewRefs.includes(`user:${afterSave.customViews[0].id}`), true);

  focusPipelineItem(scope, { id: 'inv-1', thread_id: 'thread-1', state: 'needs_approval' }, 'thread');
  const afterUpdate = updateSavedPipelineView(scope, afterSave.customViews[0].id, {
    name: 'Exception triage this week',
    snapshot: {
      activeSliceId: 'blocked_exception',
      sortCol: 'queue_age',
      sortDir: 'desc',
      filters: {
        due: 'due_7d',
        blocker: 'all',
        amount: 'all',
        approvalAge: 'all',
        erpStatus: 'all',
      },
    },
  });

  const reloaded = readPipelinePreferences(scope);
  const pinnedViews = getPinnedPipelineViews(reloaded);
  const starterViews = getStarterPipelineViews(reloaded);
  const navState = readPipelineNavigation(scope);
  const otherUserPrefs = readPipelinePreferences(otherUserScope);

  assert.equal(reloaded.customViews.length, 1);
  assert.equal(afterUpdate.customViews[0].name, 'Exception triage this week');
  assert.equal(reloaded.customViews[0].snapshot.sortCol, 'queue_age');
  assert.equal(pinnedViews.length, 3);
  assert.equal(pinnedViews.some((view) => view.scope === 'starter' && view.id === starterViews[0].id), true);
  assert.equal(pinnedViews.some((view) => view.scope === 'user' && view.id === reloaded.customViews[0].id), true);
  assert.equal(navState.focusItemId, 'inv-1');
  assert.equal(navState.focusThreadId, 'thread-1');
  assert.equal(navState.preferredSliceId, 'waiting_on_approval');
  assert.equal(otherUserPrefs.customViews.length, 0);

  const afterRemove = removeSavedPipelineView(scope, reloaded.customViews[0].id);
  assert.equal(afterRemove.customViews.length, 0);
  assert.equal(getPinnedPipelineViews(afterRemove).some((view) => view.scope === 'user'), false);

  delete global.window;
});

test('pipeline saved views stay scoped by org and user and cap stored custom views', async () => {
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
    createSavedPipelineView({ orgId: 'org-eu-1', userEmail: 'ops@clearledgr.com' }, {
      name: `View ${index + 1}`,
      snapshot: {
        activeSliceId: index % 2 === 0 ? 'waiting_on_approval' : 'blocked_exception',
      },
    });
  }
  createSavedPipelineView({ orgId: 'org-eu-1', userEmail: 'backup@clearledgr.com' }, {
    name: 'Backup approvals',
    snapshot: {
      activeSliceId: 'waiting_on_approval',
    },
  });
  createSavedPipelineView({ orgId: 'org-africa-1', userEmail: 'ops@clearledgr.com' }, {
    name: 'Africa approvals',
    snapshot: {
      activeSliceId: 'waiting_on_approval',
    },
  });

  const europeViews = readPipelinePreferences({ orgId: 'org-eu-1', userEmail: 'ops@clearledgr.com' }).customViews;
  const backupViews = readPipelinePreferences({ orgId: 'org-eu-1', userEmail: 'backup@clearledgr.com' }).customViews;
  const africaViews = readPipelinePreferences({ orgId: 'org-africa-1', userEmail: 'ops@clearledgr.com' }).customViews;

  assert.equal(europeViews.length, 8);
  assert.equal(backupViews.length, 1);
  assert.equal(africaViews.length, 1);
  assert.equal(backupViews[0].name, 'Backup approvals');
  assert.equal(africaViews[0].name, 'Africa approvals');
  assert.equal(europeViews.some((view) => view.name === 'Backup approvals'), false);
  assert.equal(europeViews.some((view) => view.name === 'Africa approvals'), false);

  delete global.window;
});

test('starter pipeline views ship by default and can be pinned without cloning', async () => {
  const {
    getPinnedPipelineViews,
    getStarterPipelineViews,
    pinPipelineView,
    readPipelinePreferences,
    unpinPipelineView,
  } = await importModule('src/routes/pipeline-views.js');

  const storage = new Map();
  global.window = {
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
    },
  };

  const scope = { orgId: 'org-eu-1', userEmail: 'ops@clearledgr.com' };
  const defaults = readPipelinePreferences(scope);
  const starterViews = getStarterPipelineViews(defaults);

  assert.equal(starterViews.length >= 4, true);
  assert.deepEqual(
    getPinnedPipelineViews(defaults).map((view) => `${view.scope}:${view.id}`),
    ['starter:approval_chase', 'starter:urgent_due'],
  );

  const pinned = pinPipelineView(scope, 'starter:posting_watch');
  assert.equal(getPinnedPipelineViews(pinned).map((view) => `${view.scope}:${view.id}`)[0], 'starter:posting_watch');

  const unpinned = unpinPipelineView(scope, 'starter:approval_chase');
  assert.equal(getPinnedPipelineViews(unpinned).some((view) => `${view.scope}:${view.id}` === 'starter:approval_chase'), false);

  delete global.window;
});

test('bootstrapped pipeline preferences normalize into the server persistence patch contract', async () => {
  const {
    buildPipelinePreferencePatch,
    getBootstrappedPipelinePreferences,
  } = await importModule('src/routes/pipeline-views.js');

  const bootstrap = {
    current_user: {
      preferences: {
        gmail_extension: {
          pipeline_views: {
            activeSliceId: 'approval_backlog',
            sortCol: 'approval_wait',
            sortDir: 'desc',
            filters: {
              vendor: 'Acme',
              due: 'overdue',
              blocker: 'exception',
              amount: 'gt_10000',
              approvalAge: 'gt_7d',
              erpStatus: 'ready',
            },
            customViews: [
              {
                id: 'month-end',
                name: 'Month end chase',
                snapshot: {
                  activeSliceId: 'approval_backlog',
                  sortCol: 'approval_wait',
                  sortDir: 'desc',
                },
              },
            ],
            pinnedViewRefs: ['starter:posting_watch', 'user:month-end'],
          },
        },
      },
    },
  };

  const remote = getBootstrappedPipelinePreferences(bootstrap);
  const patch = buildPipelinePreferencePatch(remote);

  assert.deepEqual(patch, {
    gmail_extension: {
      pipeline_views: {
        activeSliceId: 'waiting_on_approval',
        viewMode: 'table',
        sortCol: 'approval_wait',
        sortDir: 'desc',
        filters: {
          state: 'all',
          vendor: 'Acme',
          due: 'overdue',
          blocker: 'exception',
          amount: 'gt_10000',
          approvalAge: 'gt_7d',
          erpStatus: 'ready',
        },
        customViews: [
          {
            id: 'month-end',
            name: 'Month end chase',
            description: '',
            pinned: false,
            snapshot: {
              activeSliceId: 'waiting_on_approval',
              viewMode: 'table',
              sortCol: 'approval_wait',
              sortDir: 'desc',
              filters: {
                state: 'all',
                vendor: '',
                due: 'all',
                blocker: 'all',
                amount: 'all',
                approvalAge: 'all',
                erpStatus: 'all',
              },
            },
          },
        ],
        pinnedViewRefs: ['starter:posting_watch', 'user:month-end'],
      },
    },
  });
});
