/**
 * Canonical intent vocabulary — shared between workspace SPA and the
 * Gmail extension. The backend's intent contract lives at
 * clearledgr/api/ap_item_detail.py (available_intents / primary_intent)
 * and clearledgr/services/finance_skills/ap_skill.py (the executor).
 * This map only carries the operator-facing label per intent so both
 * surfaces render the same button text.
 *
 * Adding a new intent: update available_intents on the backend, then
 * add the label here. Both surfaces pick it up automatically.
 */

export const INTENT_LABELS = {
  approve_invoice: 'Approve',
  reject_invoice: 'Reject',
  request_info: 'Send back for info',
  escalate_approval: 'Escalate to controller',
  reassign_approval: 'Send to person',
  request_approval: 'Send for approval',
  snooze_invoice: 'Snooze',
  unsnooze_invoice: 'Unsnooze',
  post_to_erp: 'Post to ERP',
  reverse_invoice_post: 'Reverse posting',
  manually_classify_invoice: 'Reclassify',
  resubmit_invoice: 'Resubmit',
};

/**
 * Intents that need a reason-sheet dialog before dispatch. Direct-fire
 * intents (approve_invoice, snooze_invoice, etc.) skip the dialog.
 */
export const INTENTS_REQUIRING_REASON = new Set([
  'reject_invoice',
  'request_info',
  'escalate_approval',
]);

/**
 * Intents that need a target email picked before dispatch.
 */
export const INTENTS_REQUIRING_ASSIGNEE = new Set([
  'reassign_approval',
]);

export function labelForIntent(intent) {
  return INTENT_LABELS[intent] || intent;
}
