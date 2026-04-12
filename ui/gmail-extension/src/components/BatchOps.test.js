import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import fs from 'node:fs';

// Structural contract tests. The component itself uses htm/preact which
// we avoid rendering in this env; we lock in the promises the component
// makes to its callers.

const source = fs.readFileSync(new URL('./BatchOps.js', import.meta.url), 'utf8');

describe('BatchOps contract', () => {
  it('renders only when the selection is non-empty', () => {
    assert.match(source, /if \(items\.length === 0\) return null;/);
  });

  it('sticks to the top of the scroll container', () => {
    assert.match(source, /position: sticky; top: 0; z-index: 5;/);
  });

  it('surfaces per-action eligibility counts via classifyCapabilities', () => {
    assert.match(source, /function classifyCapabilities/);
    assert.match(source, /canApprove = selectedItems\.filter/);
    assert.match(source, /canReject = selectedItems\.filter/);
    assert.match(source, /canSnooze = selectedItems\.filter/);
    assert.match(source, /canRetryPost = selectedItems\.filter/);
    // Approve gate: needs_approval / pending_approval / validated
    assert.match(source, /'needs_approval'|'pending_approval'/);
    // Retry-post gate: failed_post only
    assert.match(source, /s === 'failed_post'/);
  });

  it('captures reject reason via ActionDialog before calling onReject', () => {
    // handleReject must open a dialog with required=true, chips set,
    // and only call onReject after a non-null reason is returned.
    assert.match(source, /async function handleReject/);
    assert.match(source, /actionType: 'bulk_reject'/);
    assert.match(source, /required: true/);
    assert.match(source, /if \(reason == null\) return;/);
    assert.match(source, /onReject\?\.\(xs, reason\)/);
  });

  it('defaults snooze to 4 hours and supports a chip picker', () => {
    assert.match(source, /let minutes = 240;/);
    assert.match(source, /chips: \['1h', '4h', 'Tomorrow', 'Next Monday'\]/);
    assert.match(source, /if \(pick === '1h'\) minutes = 60;/);
    assert.match(source, /if \(pick === 'Tomorrow'\) minutes = 24 \* 60;/);
  });

  it('shows the in-flight verb in the hint while an action is running', () => {
    assert.match(source, /\$\{running\}…/);
    assert.match(source, /const isRunning = running !== '';/);
  });

  it('reports per-item failures without aborting the batch', () => {
    // Must render the failed IDs from the bulk payload when present
    assert.match(source, /function summarizeBulkResult/);
    assert.match(source, /\.filter\(\(r\) => r && r\.ok === false\)/);
    assert.match(source, /if \(summary\.failedIds\.length > 0\) setLastFailures/);
    assert.match(source, /cl-batch-failures/);
  });

  it('disables every action button while a run is in flight', () => {
    // All 4 action buttons must have disabled=${isRunning || ...}
    const buttons = (source.match(/disabled=\$\{isRunning/g) || []).length;
    assert.ok(buttons >= 4, `expected ≥4 action buttons gated by isRunning, found ${buttons}`);
  });

  it('summarizeBulkResult returns success tone when everything passes', () => {
    // Import + exercise the pure helper. We skip the preact-dependent
    // BatchOps component itself but this helper has no preact imports.
    // Rather than importing via ESM (which would pull htm/preact), we
    // evaluate it in isolation via Function() on the exported string.
    const fnBody = source.match(/export function summarizeBulkResult[^]*?\n\}/)[0]
      .replace(/^export /, '');
    const fn = new Function(`${fnBody}; return summarizeBulkResult;`)();
    assert.equal(
      fn({ total: 3, succeeded: 3, failed: 0, results: [] }, 'Approved').tone,
      'success',
    );
    const partial = fn(
      { total: 3, succeeded: 2, failed: 1, results: [
        { ap_item_id: 'A', ok: true },
        { ap_item_id: 'B', ok: true },
        { ap_item_id: 'C', ok: false },
      ]},
      'Approved',
    );
    assert.equal(partial.tone, 'warning');
    assert.deepEqual(partial.failedIds, ['C']);
    const none = fn({ total: 2, succeeded: 0, failed: 2, results: [
      { ap_item_id: 'A', ok: false },
      { ap_item_id: 'B', ok: false },
    ]}, 'Approved');
    assert.equal(none.tone, 'error');
    assert.deepEqual(none.failedIds, ['A', 'B']);
  });

  it('exports the CSS const so consumer pages can inject it once', () => {
    assert.match(source, /export const BATCH_OPS_CSS =/);
  });
});
