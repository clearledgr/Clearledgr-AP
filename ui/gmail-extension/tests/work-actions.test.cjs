const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('non-invoice finance documents do not expose invoice-only actions', async () => {
  const {
    canNudgeApprover,
    canRejectWorkItem,
    getPrimaryActionConfig,
    getWorkStateNotice,
  } = await importModule('src/utils/work-actions.js');

  assert.equal(getPrimaryActionConfig('received', 'operator', 'credit_note'), null);
  assert.equal(canRejectWorkItem('received', 'operator', 'credit_note'), false);
  assert.equal(canNudgeApprover('needs_approval', 'operator', 'credit_note'), false);
  assert.match(
    getWorkStateNotice('received', 'credit_note'),
    /non-invoice record/i,
  );
  assert.match(
    getWorkStateNotice('received', 'payment'),
    /money already moved/i,
  );
  assert.match(
    getWorkStateNotice('received', 'statement'),
    /reconciliation/i,
  );
});

test('resume workflow is only offered for invoice posting states with prior field-review blockers', async () => {
  const { shouldOfferResumeWorkflow } = await importModule('src/utils/work-actions.js');

  assert.equal(
    shouldOfferResumeWorkflow(
      {
        state: 'ready_to_post',
        requires_field_review: false,
      },
      [
        {
          event_type: 'erp_post_blocked',
          reason: 'field_review_required',
        },
      ],
      'invoice',
    ),
    true,
  );

  assert.equal(
    shouldOfferResumeWorkflow(
      {
        state: 'received',
        requires_field_review: false,
      },
      [
        {
          event_type: 'erp_post_blocked',
          reason: 'field_review_required',
        },
      ],
      'invoice',
    ),
    false,
  );

  assert.equal(
    shouldOfferResumeWorkflow(
      {
        state: 'ready_to_post',
        requires_field_review: true,
      },
      [
        {
          event_type: 'erp_post_blocked',
          reason: 'field_review_required',
        },
      ],
      'invoice',
    ),
    false,
  );
});

test('approval waiting states default to agent monitoring instead of an operator nudge CTA', async () => {
  const {
    getAgentExecutionMode,
    getDefaultNextMoveLabel,
    getOperatorOverrideCopy,
    getPrimaryActionConfig,
    getWorkStateNotice,
  } = await importModule('src/utils/work-actions.js');

  assert.equal(
    getPrimaryActionConfig('needs_approval', 'operator', 'invoice', {
      approval_followup: { pending_assignees: ['ap@clearledgr.com'] },
    }),
    null,
  );
  assert.equal(
    getDefaultNextMoveLabel('needs_approval', {
      approval_followup: { pending_assignees: ['ap@clearledgr.com'] },
    }),
    'Wait for approval decision',
  );
  assert.equal(
    getAgentExecutionMode('needs_approval', {
      approval_followup: { pending_assignees: ['ap@clearledgr.com'] },
    }),
    'agent_monitoring',
  );
  assert.match(
    getWorkStateNotice('needs_approval', 'invoice', {
      approval_followup: { pending_assignees: ['ap@clearledgr.com'] },
    }),
    /monitoring this approval/i,
  );
  assert.match(
    getOperatorOverrideCopy('needs_approval', {
      approval_followup: { pending_assignees: ['ap@clearledgr.com'] },
    }).detail,
    /monitoring this approval/i,
  );
  assert.deepEqual(
    getPrimaryActionConfig('needs_approval', 'operator', 'invoice', {
      approval_followup: { sla_breached: true },
    }),
    {
      id: 'nudge_approver',
      label: 'Nudge approver',
    },
  );
});

test('approved or ready-to-post invoices without an ERP connection do not expose posting actions', async () => {
  const {
    getAgentExecutionMode,
    getDefaultNextMoveLabel,
    getPrimaryActionConfig,
    getWorkStateNotice,
    hasErpPostingConnection,
  } = await importModule('src/utils/work-actions.js');

  const disconnectedApproved = {
    state: 'approved',
    erp_connector_available: false,
    erp_status: 'not_connected',
  };
  const disconnectedReady = {
    state: 'ready_to_post',
    erp_connector_available: false,
    erp_status: 'not_connected',
  };

  assert.equal(hasErpPostingConnection(disconnectedApproved), false);
  assert.equal(getPrimaryActionConfig('ready_to_post', 'operator', 'invoice', disconnectedReady), null);
  assert.equal(getDefaultNextMoveLabel('approved', disconnectedApproved), 'Connect ERP');
  assert.equal(getAgentExecutionMode('approved', disconnectedApproved), 'operator_attention');
  assert.match(
    getWorkStateNotice('approved', 'invoice', disconnectedApproved),
    /connect quickbooks, xero, netsuite, or sap/i,
  );
});

test('document type helpers normalize finance document labels', async () => {
  const {
    getDocumentReferenceLabel,
    getDocumentReferenceText,
    getDocumentTypeLabel,
    getNonInvoiceWorkflowGuidance,
    normalizeDocumentType,
  } = await importModule('src/utils/document-types.js');

  assert.equal(normalizeDocumentType('credit memo'), 'credit_note');
  assert.equal(getDocumentTypeLabel('credit_note'), 'Credit note');
  assert.equal(getDocumentReferenceLabel('credit_note'), 'Reference #');
  assert.equal(
    getDocumentReferenceText('credit_note', 'AW63GKYA-0003'),
    'Credit note · Ref AW63GKYA-0003',
  );
  assert.match(
    getNonInvoiceWorkflowGuidance('credit_note'),
    /original invoice/i,
  );
});
