// SidebarApp.test — exercises the AP-first Gmail sidebar shell.
//
// History note: the original suite asserted on a richer in-sidebar
// layout (`WorkPanel` with agent-memory cards, related-records,
// task/notes/files, read-only viewer affordance, "Operator overrides"
// disclosure, in-line field-review buttons). Commit 281ee98
// ("fix: always render ThreadSidebar, never WorkPanel", 2026-04-11)
// removed `WorkPanel` from the mounted output — `ThreadSidebar` now
// owns the entire item view. The tests covering that removed UI were
// stale by construction (they tested code that's defined but never
// rendered). They were dropped in the harness commit when CI started
// running these tests on every PR; the deleted assertions are listed
// here so anyone re-introducing the richer in-sidebar layout knows
// what shape the previous spec expected:
//
//   - "What happens next" / "Before Clearledgr continues" agent-memory
//     section (AgentViewSection) — currently lives in dead WorkPanel
//   - Related-records / Tasks / Notes / Files sub-sections
//   - Read-only viewer copy ("Read-only view" + suppressed actions)
//   - Operator-override disclosure for routine pending approvals
//   - In-line field-review "Use email" / "Use attachment" resolution
//
// What this suite locks now: the shell renders header + auth gate +
// ThreadSidebar mount + queue navigator + unlinked-thread create-or-
// link flow.

import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it, mock } from 'node:test';
import { h } from 'preact';
import SidebarApp from './SidebarApp.js';
import store from '../utils/store.js';
import {
  click,
  flushTicks,
  getTextContent,
  inputValue,
  installDom,
  mount,
  uninstallDom,
} from '../test-utils/happy-dom-env.js';

function resetStore() {
  store.queueState = [];
  store.scanStatus = {};
  store.currentUserRole = null;
  store.gmailIntegration = null;
  store.selectedItemId = null;
  store.currentThreadId = null;
  store.agentSessionsState = new Map();
  store.browserTabContext = [];
  store.agentInsightsState = new Map();
  store.sourcesState = new Map();
  store.contextState = new Map();
  store.tasksState = new Map();
  store.notesState = new Map();
  store.commentsState = new Map();
  store.filesState = new Map();
  store.activeContextTab = 'email';
  store.contextUiState = { itemId: null, loading: false, error: '' };
  store.agentSummaryState = { itemId: null, mode: null, loading: false, error: '', data: null };
  store.agentPreviewState = { key: null, loading: false, error: '', data: null };
  store.batchOpsState = { mode: null, loading: false, error: '', data: null };
  store.batchOpsPolicyState = { maxItems: 5, amountThreshold: '', selectionPreset: 'queue_order' };
  store.auditState = { itemId: null, loading: false, events: [] };
  store.rowDecorated = new Set();
  store.openComposeWithPrefill = null;
  store.sdk = null;
}

function createQueueManager(overrides = {}) {
  return {
    fetchItemContext: mock.fn(async () => ({})),
    fetchItemTasks: mock.fn(async () => []),
    fetchItemNotes: mock.fn(async () => []),
    fetchItemComments: mock.fn(async () => []),
    fetchItemFiles: mock.fn(async () => []),
    fetchAuditTrail: mock.fn(async () => []),
    refreshQueue: mock.fn(async () => {}),
    authorizeGmailNow: mock.fn(async () => ({ success: true })),
    describeAuthResult: mock.fn(() => ({ toast: 'Authorization failed', severity: 'error' })),
    requestApproval: mock.fn(async () => ({ status: 'needs_approval' })),
    nudgeApproval: mock.fn(async () => ({ status: 'nudged' })),
    escalateApproval: mock.fn(async () => ({ status: 'escalated' })),
    reassignApproval: mock.fn(async () => ({ status: 'reassigned' })),
    prepareVendorFollowup: mock.fn(async () => ({ status: 'prepared' })),
    retryFailedPost: mock.fn(async () => ({ status: 'ready_to_post' })),
    retryRecoverableFailure: mock.fn(async () => ({ status: 'recovered' })),
    postToErp: mock.fn(async () => ({ status: 'posted' })),
    rejectInvoice: mock.fn(async () => ({ status: 'rejected' })),
    resolveFieldReview: mock.fn(async () => ({ status: 'resolved', auto_resumed: true })),
    resolveEntityRoute: mock.fn(async () => ({ status: 'resolved' })),
    updateRecordFields: mock.fn(async () => ({ status: 'updated' })),
    createTask: mock.fn(async () => ({ status: 'created' })),
    updateTaskStatus: mock.fn(async () => ({ status: 'updated' })),
    assignTask: mock.fn(async () => ({ status: 'updated' })),
    addTaskComment: mock.fn(async () => ({ status: 'created' })),
    addItemNote: mock.fn(async () => ({ status: 'created' })),
    addItemComment: mock.fn(async () => ({ status: 'created' })),
    addItemFileLink: mock.fn(async () => ({ status: 'created' })),
    createRecordFromComposeDraft: mock.fn(async () => ({ status: 'created', ap_item: { id: 'compose-1' } })),
    linkComposeDraftToItem: mock.fn(async () => ({ status: 'linked', ap_item: { id: 'compose-1' } })),
    recoverCurrentThread: mock.fn(async () => ({ found: false, recovered: false, item: null })),
    searchRecordCandidates: mock.fn(async () => []),
    linkCurrentThreadToItem: mock.fn(async () => ({ status: 'linked' })),
    runtimeConfig: { organizationId: 'org-123', userEmail: 'ops@clearledgr.com' },
    currentUserRole: 'operator',
    ...overrides,
  };
}

function buildItem(overrides = {}) {
  return {
    id: 'item-1',
    thread_id: 'thread-1',
    state: 'needs_approval',
    vendor_name: 'Acme Supplies',
    amount: 1234.5,
    currency: 'USD',
    invoice_number: 'INV-100',
    due_date: '2026-04-10',
    subject: 'Invoice INV-100',
    has_attachment: true,
    attachment_count: 1,
    attachment_names: ['invoice.pdf'],
    entity_code: 'US-01',
    approval_followup: {
      pending_assignees: ['ap-approver@clearledgr.com'],
      wait_minutes: 42,
    },
    ...overrides,
  };
}

function findButton(container, label) {
  return [...container.querySelectorAll('button')].find((button) => button.textContent.trim() === label) || null;
}

beforeEach(() => {
  installDom();
  resetStore();
});

afterEach(async () => {
  await flushTicks(2);
  await uninstallDom();
});

describe('SidebarApp', () => {
  it('renders the header and mounts ThreadSidebar for the selected item', async () => {
    const item = buildItem();
    store.queueState = [item];
    store.selectedItemId = item.id;
    store.currentUserRole = 'operator';

    const view = mount(h(SidebarApp, { queueManager: createQueueManager() }));
    await flushTicks(3);

    // Shell: branded header + ThreadSidebar surface with the item's
    // vendor name visible.
    assert.match(getTextContent(view.container), /Clearledgr AP/);
    assert.ok(view.container.querySelector('.cl-thread-sidebar'),
      'ThreadSidebar should mount when an item is selected');
    assert.match(getTextContent(view.container), /Acme Supplies/);
  });

  it('lets operators move through the queue from the header navigator', async () => {
    const first = buildItem({
      id: 'item-1',
      thread_id: 'thread-1',
      vendor_name: 'Acme Supplies',
      invoice_number: 'INV-100',
    });
    const second = buildItem({
      id: 'item-2',
      thread_id: 'thread-2',
      vendor_name: 'Little Learners Nursery and Preschool',
      invoice_number: '000127',
    });

    store.queueState = [first, second];
    store.selectedItemId = first.id;
    store.currentUserRole = 'operator';

    const view = mount(h(SidebarApp, { queueManager: createQueueManager() }));
    await flushTicks(3);

    // Preact's effect queue can take several macrotasks to settle in
    // node:test + happy-dom (the subscription useEffect doesn't run
    // until after a render commit, and forceUpdate is itself batched).
    // We poll up to ~12 cumulative ticks rather than assume a fixed
    // count — keeps the test robust without hiding real regressions.
    const waitForText = async (regex, maxTicks = 12) => {
      for (let i = 0; i < maxTicks; i += 1) {
        if (regex.test(getTextContent(view.container))) return true;
        await flushTicks(1);
      }
      return false;
    };

    assert.ok(await waitForText(/1 of 2/), 'expected "1 of 2" in header');
    assert.match(getTextContent(view.container), /Acme Supplies/);

    click(view.container.querySelector('[aria-label="Next record"]'));
    assert.equal(store.selectedItemId, 'item-2');
    assert.ok(await waitForText(/2 of 2/), 'expected "2 of 2" after Next click');
    assert.ok(await waitForText(/Little Learners Nursery/), 'expected new vendor in body');

    click(view.container.querySelector('[aria-label="Previous record"]'));
    assert.equal(store.selectedItemId, 'item-1');
    assert.ok(await waitForText(/1 of 2/), 'expected "1 of 2" after Previous click');
  });

  it('shows the Gmail auth prompt and starts authorization from the mounted view', async () => {
    const queueManager = createQueueManager();
    store.scanStatus = { state: 'auth_required' };
    store.gmailIntegration = { requires_reconnect: false };
    store.currentUserRole = 'admin';

    const view = mount(h(SidebarApp, { queueManager }));
    await flushTicks(2);

    assert.ok(findButton(view.container, 'Connect Gmail'));
    assert.ok(findButton(view.container, 'Connections'));

    click(findButton(view.container, 'Connect Gmail'));
    await flushTicks(3);

    assert.equal(queueManager.authorizeGmailNow.mock.calls.length, 1);
    assert.equal(queueManager.refreshQueue.mock.calls.length, 1);
  });

  it('turns an unlinked thread into a create-or-link finance record flow', async () => {
    const candidate = buildItem({
      id: 'item-linked',
      vendor_name: 'Northwind',
      invoice_number: 'INV-404',
      amount: 404,
    });
    const queueManager = createQueueManager({
      recoverCurrentThread: mock.fn(async () => ({ found: true, recovered: true, item: { id: 'item-created', vendor_name: 'Recovered Co' } })),
      searchRecordCandidates: mock.fn(async () => [candidate]),
      linkCurrentThreadToItem: mock.fn(async () => ({ status: 'linked', ap_item: candidate })),
    });

    store.currentThreadId = 'thread-unlinked';
    store.currentUserRole = 'operator';

    const view = mount(h(SidebarApp, { queueManager }));
    await flushTicks(3);

    assert.match(getTextContent(view.container), /Create record from email/);

    click(findButton(view.container, 'Create record from email'));
    await flushTicks(3);
    assert.equal(queueManager.recoverCurrentThread.mock.calls.length, 1);
    assert.equal(queueManager.recoverCurrentThread.mock.calls[0].arguments[0], 'thread-unlinked');

    const searchInput = view.container.querySelector('input[placeholder="Search existing records by vendor, invoice, or email"]');
    assert.ok(searchInput);
    inputValue(searchInput, 'northwind');
    await flushTicks(1);
    click(findButton(view.container, 'Find record'));
    await flushTicks(3);

    assert.equal(queueManager.searchRecordCandidates.mock.calls.length, 1);
    assert.match(getTextContent(view.container), /Northwind/);

    click(findButton(view.container, 'Link email'));
    await flushTicks(3);

    assert.equal(queueManager.linkCurrentThreadToItem.mock.calls.length, 1);
    assert.equal(queueManager.linkCurrentThreadToItem.mock.calls[0].arguments[0].id, 'item-linked');
    assert.deepEqual(queueManager.linkCurrentThreadToItem.mock.calls[0].arguments[1], {
      thread_id: 'thread-unlinked',
    });
  });
});
