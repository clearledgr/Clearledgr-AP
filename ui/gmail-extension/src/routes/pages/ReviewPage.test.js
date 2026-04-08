import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { buildEvidenceSummary, safeDisplayText } from './ReviewPage.js';

describe('ReviewPage helpers', () => {
  it('coerces structured display payloads into stable operator-facing text', () => {
    assert.equal(safeDisplayText({ text: 'Invoice attachment' }), 'Invoice attachment');
    assert.equal(safeDisplayText({ label: 'Amount' }), 'Amount');
    assert.equal(safeDisplayText(['USD 120.00', { text: 'USD 123.00' }]), 'USD 120.00, USD 123.00');
    assert.equal(safeDisplayText({ unexpected: true }, 'Fallback'), 'Fallback');
  });

  it('builds evidence summary without crashing on structured text values', () => {
    assert.equal(
      buildEvidenceSummary([
        { key: 'email', label: 'Email', text: { text: 'Linked' } },
        { key: 'attachment', label: 'Attachment', text: ['Attached'] },
        { key: 'approval', label: 'Approval', text: 'Available' },
      ]),
      'Email linked · Attachment attached',
    );
  });
});
