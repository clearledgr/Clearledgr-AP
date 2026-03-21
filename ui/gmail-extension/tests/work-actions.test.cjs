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
    /non-invoice finance document/i,
  );
  assert.match(
    getWorkStateNotice('received', 'payment'),
    /money already moved/i,
  );
  assert.match(
    getWorkStateNotice('received', 'statement'),
    /reconciliation work/i,
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
    /related invoice/i,
  );
});
