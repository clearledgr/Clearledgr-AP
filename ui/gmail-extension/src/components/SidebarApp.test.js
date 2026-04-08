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
    approveAndPost: mock.fn(async () => ({ status: 'posted' })),
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
  it('renders the canonical agent-memory view on the mounted thread surface', async () => {
    const item = buildItem({
      agent_memory: {
        profile: {
          mission: 'Protect cash before it leaves the company.',
          autonomy_level: 'human_supervised',
        },
        next_action: {
          label: 'Wait for approval decision',
          owner: 'approver',
        },
        summary: {
          reason: 'Awaiting approval response.',
        },
        uncertainties: {
          reason_codes: ['vendor_unscored'],
        },
        current_state: 'needs_approval',
      },
    });
    const queueManager = createQueueManager({
      fetchAuditTrail: mock.fn(async () => ([
        {
          id: 'audit-1',
          event_type: 'state_transition',
          operator_title: 'Approval requested',
          operator_message: 'Invoice routed to the approver.',
          operator_importance: 'high',
          created_at: '2026-04-05T10:00:00Z',
        },
      ])),
    });

    store.queueState = [item];
    store.selectedItemId = item.id;
    store.currentUserRole = 'operator';

    const view = mount(h(SidebarApp, { queueManager }));
    await flushTicks(3);

    const text = getTextContent(view.container);
    assert.match(text, /What happens next/);
    assert.match(text, /Waiting for approval/);
    assert.match(text, /Awaiting approval response\./);
    assert.match(text, /Vendor details need review/);
    assert.match(text, /Evidence checklist/);
    assert.match(text, /View audit/);
  });

  it('replaces internal agent-memory copy with operator-facing language', async () => {
    const item = buildItem({
      state: 'received',
      requires_field_review: true,
      workflow_paused_reason: 'ap_invoice_processing_field_review_required',
      confidence_blockers: [
        {
          field: 'due_date',
          confidence: 0.62,
          review_threshold: 0.95,
          source: 'attachment',
          values: { attachment: '2026-04-16', email: '2026-04-18' },
        },
      ],
      agent_memory: {
        current_state: 'received',
        status: 'received',
        next_action: {
          type: 'human_field_review',
          label: 'Resolve field blockers before workflow execution',
          owner: 'operator',
        },
        summary: {
          reason: 'ap_invoice_processing_field_review_required',
        },
        uncertainties: {
          reason_codes: ['ap_skill_not_ready', 'gate:legal_transition_correctness'],
        },
      },
    });

    store.queueState = [item];
    store.selectedItemId = item.id;
    store.currentUserRole = 'operator';

    const view = mount(h(SidebarApp, { queueManager: createQueueManager() }));
    await flushTicks(3);

    const text = getTextContent(view.container);
    assert.match(text, /Before Clearledgr continues/);
    assert.match(text, /Next step/);
    assert.match(text, /Confirm the due date/);
    assert.match(text, /Needs your review/);
    assert.match(text, /Why it paused/);
    assert.match(text, /Review due date before this invoice moves forward\./);
    assert.doesNotMatch(text, /ap_invoice_processing_field_review_required/i);
    assert.doesNotMatch(text, /Resolve field blockers before workflow execution/i);
    assert.doesNotMatch(text, /Legal Transition Correctness/i);
    assert.doesNotMatch(text, /Ap Skill Not Ready/i);
  });

  it('renders tasks, notes, related records, and files on the mounted thread surface', async () => {
    const item = buildItem({
      linked_finance_documents: [
        {
          source_ap_item_id: 'credit-1',
          document_type: 'credit_note',
          vendor_name: 'Acme Supplies',
          invoice_number: 'CN-10',
          amount: 120,
          currency: 'USD',
          outcome: 'applied',
        },
      ],
    });
    const queueManager = createQueueManager({
      fetchItemContext: mock.fn(async () => ({})),
    });

    store.queueState = [item];
    store.selectedItemId = item.id;
    store.currentUserRole = 'operator';
    store.contextState = new Map([
      [item.id, {
        related_records: {
          same_invoice_number_items: [
            {
              id: 'item-duplicate',
              vendor_name: 'Acme Supplies',
              invoice_number: 'INV-100',
              amount: 1234.5,
              currency: 'USD',
              state: 'needs_info',
            },
          ],
        },
        web: {
          dms_documents: [{ subject: 'Invoice packet.pdf' }],
        },
      }],
    ]);
    store.tasksState = new Map([
      [item.id, [
        {
          task_id: 'task-1',
          title: 'Call vendor about missing PO',
          status: 'open',
          due_date: '2026-04-10',
          comments: [{ comment_id: 'comment-1', user_email: 'ops@clearledgr.com', comment: 'Waiting on callback.' }],
        },
      ]],
    ]);
    store.notesState = new Map([
      [item.id, [
        {
          id: 'note-1',
          author: 'ops@clearledgr.com',
          body: 'Vendor asked for Friday follow-up.',
          created_at: '2026-04-06T08:30:00Z',
        },
      ]],
    ]);
    store.commentsState = new Map([
      [item.id, [
        {
          id: 'comment-1',
          author: 'controller@clearledgr.com',
          body: 'Approved the response language.',
          created_at: '2026-04-06T08:45:00Z',
        },
      ]],
    ]);
    store.filesState = new Map([
      [item.id, [
        {
          id: 'file-1',
          label: 'Shared quote',
          url: 'https://docs.example.com/quote',
          file_type: 'drive_link',
          note: 'Latest vendor quote',
        },
      ]],
    ]);

    const view = mount(h(SidebarApp, { queueManager }));
    await flushTicks(3);

    const text = getTextContent(view.container);
    assert.match(text, /Related records/);
    assert.match(text, /Call vendor about missing PO/);
    assert.match(text, /Approved the response language\./);
    assert.match(text, /Vendor asked for Friday follow-up\./);
    assert.match(text, /Files and evidence/);
    assert.match(text, /Shared quote/);
    assert.match(text, /Invoice packet\.pdf/);
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

    assert.match(getTextContent(view.container), /1 of 2/);
    assert.match(getTextContent(view.container), /Acme Supplies/);

    click(view.container.querySelector('[aria-label="Next record"]'));
    await flushTicks(2);

    assert.equal(store.selectedItemId, 'item-2');
    assert.match(getTextContent(view.container), /2 of 2/);
    assert.match(getTextContent(view.container), /Little Learners Nursery and Preschool/);

    click(view.container.querySelector('[aria-label="Previous record"]'));
    await flushTicks(2);

    assert.equal(store.selectedItemId, 'item-1');
    assert.match(getTextContent(view.container), /1 of 2/);
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

  it('keeps read-only viewers out of mutation actions while preserving context', async () => {
    const item = buildItem({
      agent_memory: {
        next_action: { label: 'Wait for approval decision', owner: 'approver' },
        summary: { reason: 'Awaiting approval response.' },
        uncertainties: { reason_codes: ['vendor_unscored'] },
      },
    });
    const queueManager = createQueueManager();

    store.queueState = [item];
    store.selectedItemId = item.id;
    store.currentUserRole = 'viewer';

    const view = mount(h(SidebarApp, { queueManager }));
    await flushTicks(3);

    const text = getTextContent(view.container);
    assert.match(text, /Read-only view/);
    assert.match(text, /Open in invoices/);
    assert.doesNotMatch(text, /Record 1 of/);
    assert.equal(findButton(view.container, 'Reject'), null);
    assert.equal(findButton(view.container, 'Nudge approver'), null);
    assert.equal(findButton(view.container, 'Reassign approver'), null);
  });

  it('treats routine pending approvals as agent-monitored states with overrides collapsed below the fold', async () => {
    const item = buildItem({
      approval_followup: {
        pending_assignees: ['ap-approver@clearledgr.com'],
        wait_minutes: 42,
      },
      agent_memory: {
        summary: { reason: 'Clearledgr is monitoring the active approval request.' },
      },
    });
    const queueManager = createQueueManager();

    store.queueState = [item];
    store.selectedItemId = item.id;
    store.currentUserRole = 'operator';

    const view = mount(h(SidebarApp, { queueManager }));
    await flushTicks(3);

    assert.equal(view.container.querySelector('.cl-primary-cta'), null);
    const overrides = view.container.querySelector('.cl-operator-overrides');
    assert.ok(overrides);
    assert.match(getTextContent(overrides.querySelector('summary')), /Operator overrides/);
    assert.match(getTextContent(view.container), /monitoring this approval/i);
  });

  it('routes field-review resolution through the queue manager from the mounted panel', async () => {
    const item = buildItem({
      state: 'validated',
      requires_field_review: true,
      field_provenance: {
        amount: {
          source: 'attachment',
          value: 440,
        },
      },
      field_evidence: {
        amount: {
          source: 'attachment',
          selected_value: 440,
          email_value: 400,
          attachment_value: 440,
          attachment_name: 'invoice.pdf',
        },
      },
      source_conflicts: [
        {
          field: 'amount',
          blocking: true,
          reason: 'source_value_mismatch',
          preferred_source: 'attachment',
          values: { email: 400, attachment: 440 },
        },
      ],
    });
    const queueManager = createQueueManager();

    store.queueState = [item];
    store.selectedItemId = item.id;
    store.currentUserRole = 'operator';

    const view = mount(h(SidebarApp, { queueManager }));
    await flushTicks(3);

    const useEmailButton = findButton(view.container, 'Use email');
    assert.ok(useEmailButton);

    click(useEmailButton);
    await flushTicks(3);

    assert.equal(queueManager.resolveFieldReview.mock.calls.length, 1);
    assert.deepEqual(queueManager.resolveFieldReview.mock.calls[0].arguments[1], {
      field: 'amount',
      source: 'email',
      manualValue: undefined,
      autoResume: true,
    });
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
