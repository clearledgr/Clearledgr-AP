/**
 * BatchOps — shared bulk action toolbar.
 *
 * Renders above a list of selectable AP items with Approve / Reject /
 * Snooze / Retry post actions. Keeps selection state local to the
 * parent (ReviewPage, PipelinePage) and only renders once ≥1 item is
 * selected, so it stays out of the way during single-item work.
 *
 * Design rules (DESIGN_THESIS.md §6.7):
 *   - Floats at the top of the list so it never scrolls out of view
 *     while the user is scanning selections.
 *   - Never all-or-nothing — every backend endpoint returns per-item
 *     results. The UI renders a succinct "N of M succeeded" summary
 *     toast and keeps failed IDs selected so the user can act on them.
 *   - Actions that require confirmation (Reject, Approve with override)
 *     open an ActionDialog for reason/justification capture.
 */
import { html } from 'htm/preact';
import { useState, useMemo } from 'preact/hooks';

export const BATCH_OPS_CSS = `
.cl-batch-ops {
  position: sticky; top: 0; z-index: 5;
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px;
  background: #0A1628; color: #FFFFFF;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(10, 22, 40, 0.2);
  margin-bottom: 12px;
  font-family: 'DM Sans', sans-serif;
}
.cl-batch-count {
  font-size: 13px; font-weight: 700;
  padding: 4px 10px; background: #00D67E; color: #0A1628; border-radius: 6px;
  flex-shrink: 0;
}
.cl-batch-hint {
  font-size: 12px; color: #94A3B8; flex: 1; min-width: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.cl-batch-actions {
  display: flex; gap: 6px; flex-shrink: 0;
}
.cl-batch-btn {
  padding: 6px 14px; border: 1px solid #334155; border-radius: 6px;
  background: #1F2937; color: #F1F5F9;
  font: 600 12px/1 'DM Sans', sans-serif; cursor: pointer;
}
.cl-batch-btn:hover:not(:disabled) { background: #334155; }
.cl-batch-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.cl-batch-btn.primary { background: #00D67E; color: #0A1628; border-color: #00D67E; }
.cl-batch-btn.primary:hover:not(:disabled) { background: #00C271; border-color: #00C271; }
.cl-batch-btn.danger { border-color: #DC2626; color: #FCA5A5; }
.cl-batch-btn.danger:hover:not(:disabled) { background: #DC2626; color: #FFFFFF; }
.cl-batch-btn.ghost { border-color: transparent; background: transparent; color: #94A3B8; }
.cl-batch-btn.ghost:hover:not(:disabled) { color: #FFFFFF; background: #1F2937; }

/* Secondary row shown while an action is running — status for the failures */
.cl-batch-failures {
  padding: 8px 16px; background: #FEF2F2; color: #991B1B;
  border-radius: 0 0 8px 8px; margin-top: -12px; margin-bottom: 12px;
  font-size: 12px;
}
.cl-batch-failures strong { font-weight: 700; }
.cl-batch-failures ul { margin: 4px 0 0; padding-left: 18px; }
`;

/**
 * summarizeResult — turn a bulk-endpoint payload into a toast message.
 * Returns { tone, message, failedIds }.
 */
export function summarizeBulkResult(result, verb) {
  if (!result || typeof result !== 'object') {
    return { tone: 'error', message: `${verb} failed`, failedIds: [] };
  }
  const total = Number(result.total ?? 0);
  const succeeded = Number(result.succeeded ?? 0);
  const failed = Number(result.failed ?? 0);
  const failedIds = Array.isArray(result.results)
    ? result.results.filter((r) => r && r.ok === false).map((r) => r.ap_item_id)
    : [];
  if (failed === 0 && succeeded === total && total > 0) {
    return { tone: 'success', message: `${verb} ${total} ${total === 1 ? 'item' : 'items'}`, failedIds: [] };
  }
  if (succeeded === 0) {
    return { tone: 'error', message: `${verb} failed for all ${total} items`, failedIds };
  }
  return {
    tone: 'warning',
    message: `${verb} ${succeeded} of ${total} — ${failed} failed`,
    failedIds,
  };
}

function classifyCapabilities(selectedItems) {
  // Each action has a state gate. We compute how many selected items
  // are eligible for each so the buttons can disable / show counts.
  const canApprove = selectedItems.filter((it) => {
    const s = String(it?.state || '').toLowerCase();
    return s === 'needs_approval' || s === 'pending_approval' || s === 'validated';
  });
  const canReject = selectedItems.filter((it) => {
    const s = String(it?.state || '').toLowerCase();
    return s === 'needs_approval' || s === 'pending_approval'
        || s === 'validated' || s === 'needs_info';
  });
  const canSnooze = selectedItems.filter((it) => {
    const s = String(it?.state || '').toLowerCase();
    return ['needs_approval', 'pending_approval', 'needs_info', 'validated', 'failed_post'].includes(s);
  });
  const canRetryPost = selectedItems.filter((it) => {
    const s = String(it?.state || '').toLowerCase();
    return s === 'failed_post';
  });
  return { canApprove, canReject, canSnooze, canRetryPost };
}

/**
 * BatchOps — the toolbar itself.
 *
 * Props:
 *   selectedItems  — array of full item dicts (not just IDs)
 *   onClear()      — clear the selection
 *   onApprove(ids) / onReject(ids, reason) / onSnooze(ids, minutes)
 *     / onRetryPost(ids) — parent-provided action handlers that call
 *     the bulk endpoints. Each returns a bulk result payload.
 *   toast(message, tone)  — emit a toast
 *   openDialog(config)    — open an ActionDialog for reason capture
 *                           (optional — required for Reject flow)
 */
export function BatchOps({
  selectedItems,
  onClear,
  onApprove,
  onReject,
  onSnooze,
  onRetryPost,
  toast,
  openDialog,
}) {
  const [running, setRunning] = useState('');
  const [lastFailures, setLastFailures] = useState([]);
  const items = Array.isArray(selectedItems) ? selectedItems : [];
  const caps = useMemo(() => classifyCapabilities(items), [items]);
  const totalIds = useMemo(() => items.map((it) => it?.id).filter(Boolean), [items]);

  if (items.length === 0) return null;

  async function run(verb, ids, callback) {
    if (!ids || ids.length === 0) return;
    setRunning(verb);
    setLastFailures([]);
    try {
      const result = await callback(ids);
      const summary = summarizeBulkResult(result, verb);
      toast?.(summary.message, summary.tone);
      if (summary.failedIds.length > 0) setLastFailures(summary.failedIds);
    } catch (err) {
      toast?.(`${verb} failed: ${err?.message || err}`, 'error');
    } finally {
      setRunning('');
    }
  }

  async function handleApprove() {
    const ids = caps.canApprove.map((it) => it.id).filter(Boolean);
    if (ids.length === 0) { toast?.('No selected items can be approved', 'warning'); return; }
    await run('Approved', ids, (xs) => onApprove?.(xs));
  }

  async function handleReject() {
    const ids = caps.canReject.map((it) => it.id).filter(Boolean);
    if (ids.length === 0) { toast?.('No selected items can be rejected', 'warning'); return; }
    if (!openDialog) { toast?.('Reject dialog not available', 'error'); return; }
    const reason = await openDialog({
      actionType: 'bulk_reject',
      title: `Reject ${ids.length} ${ids.length === 1 ? 'invoice' : 'invoices'}?`,
      label: 'Reason',
      message: 'The reason is attached to every rejection.',
      confirmLabel: 'Reject all',
      cancelLabel: 'Cancel',
      required: true,
      chips: ['Duplicate', 'Wrong vendor', 'Amount dispute', 'Not our invoice'],
    });
    if (reason == null) return;
    await run('Rejected', ids, (xs) => onReject?.(xs, reason));
  }

  async function handleSnooze() {
    const ids = caps.canSnooze.map((it) => it.id).filter(Boolean);
    if (ids.length === 0) { toast?.('No selected items can be snoozed', 'warning'); return; }
    // Default 4 hours; surface a chip picker via openDialog if present.
    let minutes = 240;
    if (openDialog) {
      const pick = await openDialog({
        actionType: 'bulk_snooze',
        title: `Snooze ${ids.length} ${ids.length === 1 ? 'item' : 'items'}`,
        label: 'Snooze for',
        message: 'Items return to the queue when the snooze expires.',
        confirmLabel: 'Snooze',
        cancelLabel: 'Cancel',
        required: false,
        chips: ['1h', '4h', 'Tomorrow', 'Next Monday'],
      });
      if (pick == null) return;
      if (typeof pick === 'string') {
        if (pick === '1h') minutes = 60;
        else if (pick === '4h') minutes = 240;
        else if (pick === 'Tomorrow') minutes = 24 * 60;
        else if (pick === 'Next Monday') minutes = 7 * 24 * 60;
      }
    }
    await run('Snoozed', ids, (xs) => onSnooze?.(xs, minutes));
  }

  async function handleRetry() {
    const ids = caps.canRetryPost.map((it) => it.id).filter(Boolean);
    if (ids.length === 0) { toast?.('No selected items are in failed_post', 'warning'); return; }
    await run('Retried', ids, (xs) => onRetryPost?.(xs));
  }

  const isRunning = running !== '';

  return html`
    <div>
      <div class="cl-batch-ops" role="toolbar" aria-label="Bulk actions">
        <span class="cl-batch-count">${items.length} selected</span>
        <span class="cl-batch-hint">
          ${isRunning ? `${running}…` : 'Actions run per-item; failures stay selected.'}
        </span>
        <div class="cl-batch-actions">
          ${onApprove ? html`
            <button
              class="cl-batch-btn primary"
              disabled=${isRunning || caps.canApprove.length === 0}
              onClick=${handleApprove}
              aria-label="Approve selected"
            >Approve${caps.canApprove.length !== items.length
              ? html` (${caps.canApprove.length})`
              : ''}</button>
          ` : ''}
          ${onReject ? html`
            <button
              class="cl-batch-btn danger"
              disabled=${isRunning || caps.canReject.length === 0}
              onClick=${handleReject}
              aria-label="Reject selected"
            >Reject${caps.canReject.length !== items.length
              ? html` (${caps.canReject.length})`
              : ''}</button>
          ` : ''}
          ${onSnooze ? html`
            <button
              class="cl-batch-btn"
              disabled=${isRunning || caps.canSnooze.length === 0}
              onClick=${handleSnooze}
              aria-label="Snooze selected"
            >Snooze${caps.canSnooze.length !== items.length
              ? html` (${caps.canSnooze.length})`
              : ''}</button>
          ` : ''}
          ${onRetryPost ? html`
            <button
              class="cl-batch-btn"
              disabled=${isRunning || caps.canRetryPost.length === 0}
              onClick=${handleRetry}
              aria-label="Retry posting"
            >Retry post${caps.canRetryPost.length > 0
              ? html` (${caps.canRetryPost.length})`
              : ''}</button>
          ` : ''}
          ${onClear ? html`
            <button class="cl-batch-btn ghost" disabled=${isRunning} onClick=${onClear}>
              Clear
            </button>
          ` : ''}
        </div>
      </div>
      ${lastFailures.length > 0 ? html`
        <div class="cl-batch-failures" role="status">
          <strong>${lastFailures.length} ${lastFailures.length === 1 ? 'item' : 'items'} still selected after failure.</strong>
          ${lastFailures.length <= 5 ? html`
            <ul>${lastFailures.map((id) => html`<li key=${id}>${id}</li>`)}</ul>
          ` : ''}
        </div>
      ` : ''}
    </div>
  `;
}
