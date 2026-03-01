const test = require('node:test');
const assert = require('node:assert/strict');

const { createInboxSdkIntegrationRuntime } = require('./inboxsdk-integration-harness.cjs');

function getWorkSidebar(runtime) {
  return runtime.getState().workSidebarEl || runtime.getState().globalSidebarEl;
}

async function flushUntil(runtime, predicate, attempts = 8) {
  for (let index = 0; index < attempts; index += 1) {
    await runtime.flush();
    if (predicate()) return true;
  }
  return false;
}

function toBase64Url(value) {
  return Buffer.from(String(value), 'utf8')
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
}

function buildJwt(payload) {
  return `${toBase64Url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))}.${toBase64Url(JSON.stringify(payload))}.signature`;
}

async function renderThread(runtime, threadId) {
  const threadHandler = runtime.records.sdkHandlers.threadView;
  assert.ok(threadHandler);
  threadHandler(runtime.createThreadView(threadId));
  runtime.api.renderAllSidebars();
  await runtime.flush();
  await runtime.flush();
}

function emitSingleQueueItem(runtime, item, context = null) {
  const queueManager = runtime.getQueueManager();
  const contexts = new Map();
  if (item?.id && context) {
    contexts.set(item.id, context);
  }
  queueManager.emitQueueUpdated([item], { state: 'idle' }, new Map(), [], new Map(), new Map(), contexts);
}

test('bootstrap mounts only the Work sidebar panel (no in-Gmail Ops panel)', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: true } });
  const { records } = runtime;
  const workSidebar = getWorkSidebar(runtime);

  assert.equal(records.inboxSdkLoadCalls.length, 1);
  assert.equal(records.sidebarPanels.length, 1);
  assert.ok(records.sidebarPanels.find((panel) => panel.title === 'Clearledgr AP'));
  assert.equal(records.sidebarPanels.find((panel) => panel.title === 'Clearledgr Ops'), undefined);
  assert.ok(workSidebar);
  assert.ok(workSidebar.querySelector('#cl-thread-context'));
  assert.equal(workSidebar.querySelector('#cl-kpi-summary'), null);
  assert.equal(workSidebar.querySelector('#cl-batch-agent-ops'), null);
  assert.equal(workSidebar.querySelector('#cl-agent-actions'), null);
  assert.equal(workSidebar.querySelector('#cl-audit-trail'), null);
});

test('needs_approval state renders strict primary action (never Approve & Post)', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const sidebar = getWorkSidebar(runtime);

  const item = {
    id: 'needs-approval-1',
    thread_id: 'thread-needs-approval-1',
    state: 'needs_approval',
    vendor_name: 'Acme Supplies',
    invoice_number: 'INV-1001',
    amount: 842.19,
    currency: 'USD',
    subject: 'Invoice INV-1001',
    sender: 'billing@acme.example',
    confidence: 0.94,
    metadata: {},
  };
  emitSingleQueueItem(runtime, item);
  await renderThread(runtime, item.thread_id);

  const threadContext = sidebar.querySelector('#cl-thread-context');
  const primary = threadContext?.querySelector('#cl-primary-action');
  assert.ok(primary);
  assert.equal(primary.getAttribute('data-action'), 'send_approval_request');
  assert.match(primary.innerHTML || '', /send approval request/i);
  assert.doesNotMatch(threadContext?.innerHTML || '', /approve\s*&\s*post/i);
});

test('ready_to_post state uses Preview ERP post as primary and Post to ERP as secondary', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const sidebar = getWorkSidebar(runtime);

  const item = {
    id: 'ready-post-1',
    thread_id: 'thread-ready-post-1',
    state: 'ready_to_post',
    vendor_name: 'Ready Vendor',
    invoice_number: 'INV-READY-1',
    amount: 210,
    currency: 'USD',
    subject: 'Invoice INV-READY-1',
    sender: 'billing@ready.example',
    confidence: 0.99,
    metadata: {},
  };
  emitSingleQueueItem(runtime, item);
  await renderThread(runtime, item.thread_id);

  const threadContext = sidebar.querySelector('#cl-thread-context');
  const primary = threadContext?.querySelector('#cl-primary-action');
  const postSecondary = threadContext?.querySelector('#cl-secondary-post-now');
  assert.ok(primary);
  assert.ok(postSecondary);
  assert.equal(primary.getAttribute('data-action'), 'preview_erp_post');
  assert.match(primary.innerHTML || '', /preview erp post/i);
});

test('work sidebar renders evidence checklist and hides trust-killing diagnostics copy', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const sidebar = getWorkSidebar(runtime);

  const item = {
    id: 'evidence-1',
    thread_id: 'thread-evidence-1',
    state: 'failed_post',
    vendor_name: 'Evidence Vendor',
    invoice_number: 'INV-EVID-1',
    amount: 99.5,
    currency: 'USD',
    subject: 'Invoice INV-EVID-1',
    sender: 'billing@evidence.example',
    confidence: 0.92,
    metadata: {},
  };
  emitSingleQueueItem(runtime, item);
  await renderThread(runtime, item.thread_id);

  const html = sidebar.querySelector('#cl-thread-context')?.innerHTML || '';
  assert.match(html, /Evidence checklist/i);
  assert.match(html, /Email/i);
  assert.match(html, /Attachment/i);
  assert.match(html, /ERP link/i);
  assert.match(html, /Approval/i);
  assert.doesNotMatch(html, /source quality/i);
  assert.doesNotMatch(html, /stale context/i);
});

test('work audit feed renders operator language and hides raw reason codes', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({
    queueManager: {
      debugUiEnabled: false,
      async fetchAuditTrail() {
        return [
          {
            id: 'audit-1',
            event_type: 'deterministic_validation_failed',
            decision_reason: 'policy_requirement_amt_500,po_match_no_gr,confidence_field_review_required',
            operator_title: 'Validation checks failed',
            operator_message: 'Policy requires approval for invoices above $500. PO/GR check failed because goods receipt is missing.',
            ts: '2026-02-28T21:36:00Z',
          },
          {
            id: 'audit-2',
            event_type: 'approval_routed_from_extension',
            reason: 'route_for_approval',
            operator_title: 'Approval request sent',
            operator_message: 'Sent to approver in Slack or Teams.',
            ts: '2026-02-28T21:36:30Z',
          },
          {
            id: 'audit-3',
            event_type: 'browser_session_created',
            reason: 'browser_session_created',
            operator_title: 'Backup ERP route ready',
            operator_message: 'If direct posting fails, Clearledgr can use the backup ERP route.',
            ts: '2026-02-28T21:36:35Z',
          },
          {
            id: 'audit-4',
            event_type: 'approval_nudge_failed',
            reason: 'approval_nudge',
            operator_title: 'Reminder not sent',
            operator_message: 'Could not send the approver reminder. Try "Nudge approver" again.',
            ts: '2026-03-01T03:51:00Z',
          },
          {
            id: 'audit-5',
            event_type: 'state_transition_rejected',
            decision_reason: 'autonomous_retry_attempt',
            operator_title: 'Retry paused',
            operator_message: 'Auto-retry is paused until required steps are complete.',
            ts: '2026-03-01T03:55:00Z',
          },
          {
            id: 'audit-6',
            event_type: 'state_transition_rejected',
            decision_reason: 'illegal_transition',
            operator_title: 'Step blocked',
            operator_message: 'This action can run only after the invoice reaches the required status.',
            ts: '2026-03-01T04:26:00Z',
          },
        ];
      },
    },
  });
  const sidebar = getWorkSidebar(runtime);

  const item = {
    id: 'audit-language-1',
    thread_id: 'thread-audit-language-1',
    state: 'needs_approval',
    vendor_name: 'Acme Supplies',
    invoice_number: 'INV-1001',
    amount: 842.19,
    currency: 'USD',
    subject: 'Invoice INV-1001',
    sender: 'billing@acme.example',
    confidence: 0.94,
    metadata: {},
  };
  emitSingleQueueItem(runtime, item);
  await renderThread(runtime, item.thread_id);
  await flushUntil(runtime, () => {
    const html = sidebar.querySelector('#cl-thread-context')?.innerHTML || '';
    return /Validation checks failed/i.test(html);
  }, 12);

  const html = sidebar.querySelector('#cl-thread-context')?.innerHTML || '';
  assert.match(html, /Validation checks failed/i);
  assert.match(html, /Policy requires approval for invoices above \$500/i);
  assert.match(html, /PO\/GR check failed because goods receipt is missing/i);
  assert.match(html, /Approval request sent/i);
  assert.match(html, /Backup ERP route ready/i);
  assert.match(html, /Reminder not sent/i);
  assert.match(html, /Retry paused/i);
  assert.match(html, /Step blocked/i);

  assert.doesNotMatch(html, /policy_requirement_amt_500/i);
  assert.doesNotMatch(html, /po_match_no_gr/i);
  assert.doesNotMatch(html, /confidence_field_review_required/i);
  assert.doesNotMatch(html, /illegal_transition/i);
  assert.doesNotMatch(html, /autonomous_retry_attempt/i);
  assert.doesNotMatch(html, /browser_session_created/i);
});

test('reject action uses inline reason sheet and never uses native prompt/confirm', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const sidebar = getWorkSidebar(runtime);
  let promptCalls = 0;
  let confirmCalls = 0;
  runtime.window.prompt = () => {
    promptCalls += 1;
    return 'native-prompt';
  };
  runtime.window.confirm = () => {
    confirmCalls += 1;
    return true;
  };

  const item = {
    id: 'reject-inline-1',
    thread_id: 'thread-reject-inline-1',
    state: 'needs_approval',
    vendor_name: 'Reject Vendor',
    invoice_number: 'INV-REJECT-1',
    amount: 420,
    currency: 'USD',
    subject: 'Invoice INV-REJECT-1',
    sender: 'billing@reject.example',
    confidence: 0.97,
    metadata: {},
  };
  emitSingleQueueItem(runtime, item);
  await renderThread(runtime, item.thread_id);

  const threadContext = sidebar.querySelector('#cl-thread-context');
  const rejectBtn = threadContext?.querySelector('#cl-secondary-reject');
  assert.ok(rejectBtn);
  await rejectBtn.click();
  await runtime.flush();

  const dialog = sidebar.querySelector('#cl-action-dialog');
  const input = dialog?.querySelector('.cl-action-dialog-input');
  const confirmBtn = dialog?.querySelector('.cl-action-dialog-confirm');
  assert.ok(dialog);
  assert.ok(input);
  assert.ok(confirmBtn);
  assert.equal(dialog.style.display, 'flex');
  assert.equal(promptCalls, 0);
  assert.equal(confirmCalls, 0);

  await confirmBtn.click();
  await runtime.flush();
  assert.equal(dialog.style.display, 'flex');

  input.value = 'Duplicate invoice';
  await confirmBtn.click();
  await runtime.flush();
  assert.equal(dialog.style.display, 'none');
});

test('admin/operator role gets Open Ops Console link with /console?page=ops deep-link', async () => {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const queueManager = runtime.getQueueManager();
  const sidebar = getWorkSidebar(runtime);
  let openedUrl = '';
  runtime.window.open = (url) => {
    openedUrl = String(url || '');
    return null;
  };
  runtime.context.Buffer = Buffer;
  queueManager.backendAuthToken = buildJwt({ role: 'admin', organization_id: 'default' });

  const item = {
    id: 'ops-link-1',
    thread_id: 'thread-ops-link-1',
    state: 'validated',
    vendor_name: 'Ops Link Vendor',
    invoice_number: 'INV-OPS-LINK-1',
    amount: 125,
    currency: 'USD',
    subject: 'Invoice INV-OPS-LINK-1',
    sender: 'billing@opslink.example',
    confidence: 0.96,
    metadata: {},
  };
  emitSingleQueueItem(runtime, item);
  await renderThread(runtime, item.thread_id);

  const threadContext = sidebar.querySelector('#cl-thread-context');
  const openOpsBtn = threadContext?.querySelector('#cl-open-ops-console');
  assert.ok(openOpsBtn);
  await openOpsBtn.click();
  await runtime.flush();
  assert.match(openedUrl, /\/console\?org=default&page=ops/i);

  const runtimeNoOps = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const sidebarNoOps = getWorkSidebar(runtimeNoOps);
  emitSingleQueueItem(runtimeNoOps, item);
  await renderThread(runtimeNoOps, item.thread_id);
  const noOpsContext = sidebarNoOps.querySelector('#cl-thread-context');
  assert.equal(noOpsContext?.querySelector('#cl-open-ops-console'), null);
});
