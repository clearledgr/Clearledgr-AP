import { describe, it, expect } from 'vitest';
import {
  getStateLabel, formatAmount, trimText, getIssueSummary,
  getExceptionReason, getDueRiskLabel, getDecisionSummary,
  normalizeBudgetContext, getReasonSheetDefaults, parseJsonObject,
  readLocalStorage, writeLocalStorage,
} from './formatters.js';

describe('getStateLabel', () => {
  it('returns label for known states', () => {
    expect(getStateLabel('received')).toBe('Received');
    expect(getStateLabel('needs_info')).toBe('Needs info');
    expect(getStateLabel('posted_to_erp')).toBe('Posted to ERP');
  });
  it('returns Received for unknown state', () => {
    expect(getStateLabel('bogus')).toBe('Received');
    expect(getStateLabel(undefined)).toBe('Received');
    expect(getStateLabel(null)).toBe('Received');
  });
});

describe('formatAmount', () => {
  it('formats numeric amounts', () => {
    expect(formatAmount(1234.5, 'USD')).toBe('USD 1234.50');
    expect(formatAmount(0)).toBe('USD 0.00');
  });
  it('handles null/undefined/empty', () => {
    expect(formatAmount(null)).toBe('Amount unavailable');
    expect(formatAmount(undefined)).toBe('Amount unavailable');
    expect(formatAmount('')).toBe('Amount unavailable');
  });
  it('handles non-numeric strings', () => {
    expect(formatAmount('not a number')).toBe('Amount unavailable');
  });
  it('respects currency param', () => {
    expect(formatAmount(100, 'GBP')).toBe('GBP 100.00');
  });
});

describe('trimText', () => {
  it('returns short text unchanged', () => {
    expect(trimText('hello')).toBe('hello');
  });
  it('truncates long text with ellipsis', () => {
    const long = 'a'.repeat(200);
    const result = trimText(long, 10);
    expect(result.length).toBeLessThanOrEqual(10);
    expect(result.endsWith('…')).toBe(true);
  });
  it('handles null/undefined', () => {
    expect(trimText(null)).toBe('');
    expect(trimText(undefined)).toBe('');
  });
});

describe('getIssueSummary', () => {
  it('returns exception-specific summary', () => {
    expect(getIssueSummary({ exception_code: 'po_missing_reference' })).toBe('PO reference is required before processing');
    expect(getIssueSummary({ exception_code: 'budget_overrun' })).toBe('Invoice exceeds available budget');
  });
  it('returns state-based summary when no exception', () => {
    expect(getIssueSummary({ state: 'needs_info' })).toBe('Missing required invoice fields');
    expect(getIssueSummary({ state: 'failed_post' })).toBe('ERP posting failed and needs retry');
  });
  it('returns default for unknown state', () => {
    expect(getIssueSummary({})).toBe('Under AP review');
  });
});

describe('getExceptionReason', () => {
  it('maps known codes', () => {
    expect(getExceptionReason('po_amount_mismatch')).toBe('Invoice amount does not match approved PO');
    expect(getExceptionReason('duplicate_invoice')).toBe('Duplicate invoice detected for this vendor');
  });
  it('returns empty for unknown code', () => {
    expect(getExceptionReason('unknown')).toBe('');
    expect(getExceptionReason(null)).toBe('');
  });
});

describe('getDueRiskLabel', () => {
  it('returns past due for past dates', () => {
    const past = new Date(Date.now() - 3 * 86400000).toISOString();
    expect(getDueRiskLabel(past)).toMatch(/Past due/);
  });
  it('returns due today', () => {
    const today = new Date().toISOString().split('T')[0] + 'T23:59:59Z';
    const label = getDueRiskLabel(today);
    expect(label === 'Due today' || label.includes('Due in')).toBe(true);
  });
  it('returns empty for far-future dates', () => {
    const future = new Date(Date.now() + 30 * 86400000).toISOString();
    expect(getDueRiskLabel(future)).toBe('');
  });
  it('returns empty for null/undefined', () => {
    expect(getDueRiskLabel(null)).toBe('');
    expect(getDueRiskLabel(undefined)).toBe('');
  });
});

describe('getDecisionSummary', () => {
  it('returns budget review for budget decisions', () => {
    const result = getDecisionSummary({}, { requiresDecision: true });
    expect(result.title).toBe('Budget review required');
    expect(result.tone).toBe('warning');
  });
  it('returns approval required for needs_approval', () => {
    const result = getDecisionSummary({ state: 'needs_approval' }, {});
    expect(result.title).toBe('Approval required');
  });
  it('returns completed for posted items', () => {
    const result = getDecisionSummary({ state: 'posted_to_erp' }, {});
    expect(result.title).toBe('Completed');
    expect(result.tone).toBe('good');
  });
});

describe('normalizeBudgetContext', () => {
  it('extracts budget from approvals path', () => {
    const ctx = { approvals: { budget: { status: 'exceeded', checks: [{ name: 'Monthly' }], requires_decision: true } } };
    const result = normalizeBudgetContext(ctx);
    expect(result.status).toBe('exceeded');
    expect(result.requiresDecision).toBe(true);
    expect(result.checks).toHaveLength(1);
  });
  it('falls back to root budget', () => {
    const ctx = { budget: { status: 'ok', checks: [] } };
    const result = normalizeBudgetContext(ctx);
    expect(result.status).toBe('ok');
  });
  it('handles empty payload', () => {
    const result = normalizeBudgetContext({});
    expect(result.status).toBe('');
    expect(result.requiresDecision).toBe(false);
    expect(result.checks).toEqual([]);
  });
});

describe('getReasonSheetDefaults', () => {
  it('returns reject chips', () => {
    const result = getReasonSheetDefaults('reject');
    expect(result.required).toBe(true);
    expect(result.chips).toContain('Duplicate invoice');
  });
  it('returns override chips', () => {
    const result = getReasonSheetDefaults('approve_override');
    expect(result.required).toBe(true);
    expect(result.chips).toContain('Urgent vendor payment');
  });
  it('returns generic defaults for unknown type', () => {
    const result = getReasonSheetDefaults('unknown');
    expect(result.chips.length).toBeGreaterThan(0);
  });
});

describe('parseJsonObject', () => {
  it('parses valid JSON string', () => {
    expect(parseJsonObject('{"a":1}')).toEqual({ a: 1 });
  });
  it('returns object as-is', () => {
    const obj = { x: 1 };
    expect(parseJsonObject(obj)).toBe(obj);
  });
  it('returns null for invalid input', () => {
    expect(parseJsonObject(null)).toBeNull();
    expect(parseJsonObject('not json')).toBeNull();
    expect(parseJsonObject(42)).toBeNull();
  });
});

describe('localStorage helpers', () => {
  it('reads and writes', () => {
    writeLocalStorage('test_key', 'test_value');
    expect(readLocalStorage('test_key')).toBe('test_value');
  });
  it('removes on null/empty', () => {
    writeLocalStorage('test_key', 'something');
    writeLocalStorage('test_key', null);
    expect(readLocalStorage('test_key')).toBe('');
  });
});
