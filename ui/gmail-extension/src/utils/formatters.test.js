import assert from 'node:assert/strict';
import { after, beforeEach, describe, it } from 'node:test';
import {
  buildAuditRow,
  getStateLabel,
  formatAmount,
  normalizeCurrencyCode,
  trimText,
  getAgentMemoryView,
  getIssueSummary,
  getExceptionReason,
  getDueRiskLabel,
  getDecisionSummary,
  getFieldReviewBlockers,
  getWorkflowPauseReason,
  normalizeBudgetContext,
  getReasonSheetDefaults,
  parseJsonObject,
  readLocalStorage,
  writeLocalStorage,
} from './formatters.js';

function createStorage() {
  const state = new Map();
  return {
    getItem(key) {
      return state.has(key) ? state.get(key) : null;
    },
    setItem(key, value) {
      state.set(key, String(value));
    },
    removeItem(key) {
      state.delete(key);
    },
    clear() {
      state.clear();
    },
  };
}

beforeEach(() => {
  globalThis.window = {
    localStorage: createStorage(),
    location: { hash: '' },
  };
});

after(() => {
  delete globalThis.window;
});

describe('getStateLabel', () => {
  it('returns label for known states', () => {
    assert.equal(getStateLabel('received'), 'Received');
    assert.equal(getStateLabel('needs_info'), 'Needs info');
    assert.equal(getStateLabel('posted_to_erp'), 'Posted to ERP');
  });

  it('returns Received for unknown state', () => {
    assert.equal(getStateLabel('bogus'), 'Received');
    assert.equal(getStateLabel(undefined), 'Received');
    assert.equal(getStateLabel(null), 'Received');
  });
});

describe('formatAmount', () => {
  it('formats numeric amounts', () => {
    assert.equal(formatAmount(1234.5, 'USD'), 'USD 1,234.50');
    assert.equal(formatAmount(0), '0.00');
  });

  it('handles null, undefined, and empty', () => {
    assert.equal(formatAmount(null), 'Amount unavailable');
    assert.equal(formatAmount(undefined), 'Amount unavailable');
    assert.equal(formatAmount(''), 'Amount unavailable');
  });

  it('handles non-numeric strings', () => {
    assert.equal(formatAmount('not a number'), 'Amount unavailable');
  });

  it('respects the currency parameter', () => {
    assert.equal(formatAmount(100, 'GBP'), 'GBP 100.00');
  });

  it('does not invent USD when currency is missing', () => {
    assert.equal(formatAmount(5000, null), '5,000.00');
    assert.equal(formatAmount(5000, ''), '5,000.00');
  });
});

describe('normalizeCurrencyCode', () => {
  it('normalizes currency codes without inventing them', () => {
    assert.equal(normalizeCurrencyCode('ghs'), 'GHS');
    assert.equal(normalizeCurrencyCode(''), '');
    assert.equal(normalizeCurrencyCode(null), '');
  });
});

describe('trimText', () => {
  it('returns short text unchanged', () => {
    assert.equal(trimText('hello'), 'hello');
  });

  it('truncates long text with an ellipsis', () => {
    const long = 'a'.repeat(200);
    const result = trimText(long, 10);
    assert.equal(result.length <= 10, true);
    assert.equal(result.endsWith('…'), true);
  });

  it('handles null and undefined', () => {
    assert.equal(trimText(null), '');
    assert.equal(trimText(undefined), '');
  });
});

describe('getIssueSummary', () => {
  it('returns exception-specific summary', () => {
    assert.equal(getIssueSummary({ exception_code: 'po_missing_reference' }), 'PO reference is required before processing');
    assert.equal(getIssueSummary({ exception_code: 'budget_overrun' }), 'Invoice exceeds available budget');
  });

  it('returns state-based summary when no exception exists', () => {
    assert.equal(getIssueSummary({ state: 'needs_info' }), 'Missing required invoice fields');
    assert.equal(getIssueSummary({ state: 'failed_post' }), 'ERP posting failed and needs retry');
    assert.equal(
      getIssueSummary({ state: 'failed_post', erp_connector_available: false, erp_status: 'not_connected' }),
      'ERP is not connected for posting',
    );
    assert.equal(
      getIssueSummary({ state: 'approved', erp_connector_available: false, erp_status: 'not_connected' }),
      'ERP is not connected for posting',
    );
  });

  it('returns the default summary for unknown state', () => {
    assert.equal(getIssueSummary({}), 'Under AP review');
  });
});

describe('getExceptionReason', () => {
  it('maps known codes', () => {
    assert.equal(getExceptionReason('po_amount_mismatch'), 'Invoice amount does not match approved PO');
    assert.equal(getExceptionReason('duplicate_invoice'), 'Duplicate invoice detected for this vendor');
    assert.equal(getExceptionReason('erp_not_connected'), 'Connect an ERP before posting this invoice');
  });

  it('returns an empty string for unknown codes', () => {
    assert.equal(getExceptionReason('unknown'), '');
    assert.equal(getExceptionReason(null), '');
  });
});

describe('getDueRiskLabel', () => {
  it('returns past due for past dates', () => {
    const past = new Date(Date.now() - 3 * 86400000).toISOString();
    assert.match(getDueRiskLabel(past), /Past due/);
  });

  it('returns due today', () => {
    const today = `${new Date().toISOString().split('T')[0]}T23:59:59Z`;
    const label = getDueRiskLabel(today);
    assert.equal(label === 'Due today' || label.includes('Due in'), true);
  });

  it('returns empty for far-future dates', () => {
    const future = new Date(Date.now() + 30 * 86400000).toISOString();
    assert.equal(getDueRiskLabel(future), '');
  });

  it('returns empty for null and undefined', () => {
    assert.equal(getDueRiskLabel(null), '');
    assert.equal(getDueRiskLabel(undefined), '');
  });
});

describe('getDecisionSummary', () => {
  it('returns budget review for budget decisions', () => {
    const result = getDecisionSummary({}, { requiresDecision: true });
    assert.equal(result.title, 'Budget review required');
    assert.equal(result.tone, 'warning');
  });

  it('returns waiting on approver for needs approval', () => {
    const result = getDecisionSummary({ state: 'needs_approval' }, {});
    assert.equal(result.title, 'Waiting on approver');
    assert.equal(result.detail, 'Waiting on approver decision');
  });

  it('returns waiting on vendor when vendor follow-up is already in flight', () => {
    const result = getDecisionSummary({ state: 'needs_info', followup_next_action: 'await_vendor_response' }, {});
    assert.equal(result.title, 'Waiting on vendor');
    assert.equal(result.detail, 'Waiting on vendor reply');
  });

  it('returns completed for posted items', () => {
    const result = getDecisionSummary({ state: 'posted_to_erp' }, {});
    assert.equal(result.title, 'Completed');
    assert.equal(result.tone, 'good');
  });

  it('returns ERP not connected when posting is not available', () => {
    const result = getDecisionSummary({ state: 'ready_to_post', erp_connector_available: false, erp_status: 'not_connected' }, {});
    assert.equal(result.title, 'ERP not connected');
    assert.equal(result.tone, 'warning');
  });
});

describe('normalizeBudgetContext', () => {
  it('extracts budget from approvals path', () => {
    const ctx = { approvals: { budget: { status: 'exceeded', checks: [{ name: 'Monthly' }], requires_decision: true } } };
    const result = normalizeBudgetContext(ctx);
    assert.equal(result.status, 'exceeded');
    assert.equal(result.requiresDecision, true);
    assert.equal(result.checks.length, 1);
  });

  it('falls back to root budget', () => {
    const ctx = { budget: { status: 'ok', checks: [] } };
    const result = normalizeBudgetContext(ctx);
    assert.equal(result.status, 'ok');
  });

  it('handles an empty payload', () => {
    const result = normalizeBudgetContext({});
    assert.equal(result.status, '');
    assert.equal(result.requiresDecision, false);
    assert.deepEqual(result.checks, []);
  });
});

describe('field review summaries', () => {
  it('builds source conflict summaries from raw extraction evidence', () => {
    const blockers = getFieldReviewBlockers({
      currency: 'USD',
      requires_field_review: true,
      field_provenance: {
        amount: {
          source: 'attachment',
          value: 440.0,
        },
      },
      field_evidence: {
        amount: {
          source: 'attachment',
          selected_value: 440.0,
          email_value: 400.0,
          attachment_value: 440.0,
          attachment_name: 'invoice.pdf',
        },
      },
      source_conflicts: [
        {
          field: 'amount',
          blocking: true,
          reason: 'source_value_mismatch',
          preferred_source: 'attachment',
          values: { email: 400.0, attachment: 440.0 },
        },
      ],
    });

    assert.equal(blockers.length, 1);
    assert.equal(blockers[0].field_label, 'Amount');
    assert.equal(blockers[0].email_value_display, 'USD 400.00');
    assert.equal(blockers[0].attachment_value_display, 'USD 440.00');
    assert.equal(blockers[0].winning_source_label, 'Invoice attachment');
  });

  it('prefers explicit workflow pause copy when present', () => {
    assert.equal(getWorkflowPauseReason({ workflow_paused_reason: 'Workflow paused for review.' }), 'Workflow paused for review.');
  });

  it('ignores internal pause reason codes and derives operator-facing copy', () => {
    assert.equal(
      getWorkflowPauseReason({
        workflow_paused_reason: 'ap_invoice_processing_field_review_required',
        requires_field_review: true,
        confidence_blockers: [
          {
            field: 'due_date',
            confidence: 0.62,
            review_threshold: 0.95,
            source: 'attachment',
            values: { attachment: '2026-04-16' },
          },
        ],
      }),
      'Review due date before this invoice moves forward.',
    );
  });
});

describe('getAgentMemoryView', () => {
  it('normalizes internal agent-memory strings into operator-facing copy', () => {
    const view = getAgentMemoryView({
      state: 'received',
      requires_field_review: true,
      workflow_paused_reason: 'ap_invoice_processing_field_review_required',
      confidence_blockers: [
        {
          field: 'due_date',
          confidence: 0.62,
          review_threshold: 0.95,
          source: 'attachment',
          values: { attachment: '2026-04-16' },
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

    assert.equal(view.nextActionLabel, 'Confirm the due date');
    assert.equal(view.beliefReason, 'Review due date before this invoice moves forward.');
    assert.equal(view.nextActionResponsibility, 'Needs your review');
    assert.equal(view.nextActionActorLabel, 'You');
    assert.equal(view.stateSummaryLabel, 'Received');
    assert.deepEqual(view.reasonCodes, []);
    assert.equal(view.highlights.includes('Due Date still needs confirmation'), true);
  });

  it('dedupes duplicate approval states and translates raw approval belief tokens', () => {
    const view = getAgentMemoryView({
      state: 'needs_approval',
      agent_memory: {
        current_state: 'needs_approval',
        status: 'pending_approval',
        next_action: {
          type: 'await_approval',
          owner: 'approver',
        },
        summary: {
          reason: 'pending_approval',
        },
        uncertainties: {
          reason_codes: ['gate:operator_acceptance', 'gate:enabled_connector_readiness'],
        },
      },
    });

    assert.equal(view.stateSummaryLabel, 'Needs approval');
    assert.equal(view.beliefReason, 'Approval has already been requested. Clearledgr is waiting for the approver response.');
    assert.equal(view.nextActionActorLabel, 'Approver');
    assert.deepEqual(view.reasonCodes, []);
  });
});

describe('getReasonSheetDefaults', () => {
  it('returns reject chips', () => {
    const result = getReasonSheetDefaults('reject');
    assert.equal(result.required, true);
    assert.equal(result.chips.includes('Duplicate invoice'), true);
  });

  it('returns override chips', () => {
    const result = getReasonSheetDefaults('approve_override');
    assert.equal(result.required, true);
    assert.equal(result.chips.includes('Urgent vendor payment'), true);
  });

  it('returns generic defaults for unknown type', () => {
    const result = getReasonSheetDefaults('unknown');
    assert.equal(result.chips.length > 0, true);
  });
});

describe('parseJsonObject', () => {
  it('parses a valid JSON string', () => {
    assert.deepEqual(parseJsonObject('{"a":1}'), { a: 1 });
  });

  it('returns an object as-is', () => {
    const obj = { x: 1 };
    assert.equal(parseJsonObject(obj), obj);
  });

  it('returns null for invalid input', () => {
    assert.equal(parseJsonObject(null), null);
    assert.equal(parseJsonObject('not json'), null);
    assert.equal(parseJsonObject(42), null);
  });
});

describe('buildAuditRow', () => {
  it('replaces generic state transition titles with operator-facing copy', () => {
    const row = buildAuditRow({
      event_type: 'state_transition',
      operator_title: 'Updated',
      operator_message: 'Invoice status changed.',
      updated_at: '2026-04-07T12:00:00Z',
    });

    assert.equal(row.title, 'Status updated');
    assert.equal(row.detail, 'Invoice status changed.');
  });

  it('normalizes generic colon-form state titles back to operator-facing copy', () => {
    const row = buildAuditRow({
      event_type: 'state_transition',
      operator_title: 'Status updated: Updated',
      operator_message: 'Invoice status changed.',
      updated_at: '2026-04-07T12:00:00Z',
    });

    assert.equal(row.title, 'Status updated');
    assert.equal(row.detail, 'Invoice status changed.');
  });

  it('prefers status copy even when the event type is a generic updated token', () => {
    const row = buildAuditRow({
      event_type: 'updated',
      operator_title: 'Status updated: Updated',
      operator_message: 'Invoice status changed.',
      updated_at: '2026-04-07T12:00:00Z',
    });

    assert.equal(row.title, 'Status updated');
    assert.equal(row.detail, 'Invoice status changed.');
  });
});

describe('localStorage helpers', () => {
  it('reads and writes values', () => {
    writeLocalStorage('test_key', 'test_value');
    assert.equal(readLocalStorage('test_key'), 'test_value');
  });

  it('removes values on null and empty', () => {
    writeLocalStorage('test_key', 'something');
    writeLocalStorage('test_key', null);
    assert.equal(readLocalStorage('test_key'), '');
  });
});
