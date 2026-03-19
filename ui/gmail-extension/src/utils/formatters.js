/** Pure utility functions extracted from the monolithic inboxsdk-layer.js */

export const STATE_LABELS = {
  received: 'Received', validated: 'Validated', needs_info: 'Needs info',
  needs_approval: 'Needs approval', pending_approval: 'Needs approval', approved: 'Approved', ready_to_post: 'Ready to post',
  posted_to_erp: 'Posted to ERP', closed: 'Closed', rejected: 'Rejected', failed_post: 'Failed post',
};

export const STATE_COLORS = {
  received: '#2563eb', validated: '#0f766e', needs_info: '#b45309',
  needs_approval: '#c2410c', pending_approval: '#c2410c', approved: '#15803d', ready_to_post: '#0f766e',
  posted_to_erp: '#7c3aed', closed: '#0f766e', rejected: '#b91c1c', failed_post: '#b91c1c',
};

export function getStateLabel(state) { return STATE_LABELS[state] || 'Received'; }

export function formatAmount(amount, currency = 'USD') {
  if (amount === null || amount === undefined || amount === '') return 'Amount unavailable';
  const numeric = Number(amount);
  if (!Number.isFinite(numeric)) return 'Amount unavailable';
  return `${currency} ${numeric.toFixed(2)}`;
}

export function formatTimestamp(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  try { return date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Europe/London' }); }
  catch { return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
}

export function formatDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  try { return date.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Europe/London' }); }
  catch { return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
}

export function formatAgeSeconds(value) {
  const s = Number(value);
  if (!Number.isFinite(s) || s < 0) return '';
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export function trimText(value, maxLength = 96) {
  const text = String(value ?? '').trim();
  if (!text || text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(1, maxLength - 1)).trim()}…`;
}

export function prettifyEventType(value) {
  if (!value) return 'Event';
  return String(value).replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase());
}

export function humanizeSnakeText(value) {
  return String(value || '').replace(/_/g, ' ').trim().replace(/\b\w/g, ch => ch.toUpperCase());
}

export function readLocalStorage(key) {
  try { return String(window?.localStorage?.getItem(key) || '').trim(); } catch { return ''; }
}

export function writeLocalStorage(key, value) {
  try {
    if (value === null || value === undefined || String(value).trim() === '') window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, String(value).trim());
  } catch { /* best-effort */ }
}

export function getAssetUrl(path) {
  try { return chrome?.runtime?.getURL?.(path) || ''; } catch { return ''; }
}

export function parseJsonObject(value) {
  if (!value) return null;
  if (typeof value === 'object') return value;
  try { const p = JSON.parse(String(value)); return p && typeof p === 'object' ? p : null; } catch { return null; }
}

export function getSourceThreadId(item) { return String(item?.thread_id || item?.threadId || '').trim(); }
export function getSourceMessageId(item) { return String(item?.message_id || item?.messageId || '').trim(); }

export function openSourceEmail(item) {
  const threadId = getSourceThreadId(item);
  if (threadId) { window.location.hash = `#inbox/${encodeURIComponent(threadId)}`; return true; }
  const messageId = getSourceMessageId(item);
  if (messageId) { window.location.hash = `#search/${encodeURIComponent(messageId)}`; return true; }
  const subject = String(item?.subject || '').trim();
  if (subject) { window.location.hash = `#search/${encodeURIComponent(`subject:"${subject}"`)}`; return true; }
  return false;
}

export function normalizeBudgetContext(contextPayload, item = null) {
  const approvalsBudget = contextPayload?.approvals?.budget || {};
  const rootBudget = contextPayload?.budget || {};
  const candidate = approvalsBudget?.checks || approvalsBudget?.status ? approvalsBudget : rootBudget;
  const checks = Array.isArray(candidate?.checks) ? candidate.checks : [];
  const status = String(candidate?.status || item?.budget_status || '').trim().toLowerCase();
  const requiresDecision = Boolean(candidate?.requires_decision || item?.budget_requires_decision || status === 'critical' || status === 'exceeded');
  return { status, requiresDecision, checks, warningCount: Number(candidate?.warning_count || 0), criticalCount: Number(candidate?.critical_count || 0), exceededCount: Number(candidate?.exceeded_count || 0) };
}

export function budgetStatusTone(status) {
  const n = String(status || '').trim().toLowerCase();
  return (n === 'exceeded' || n === 'critical') ? 'cl-context-warning' : '';
}

export function getIssueSummary(item) {
  const ec = String(item?.exception_code || '').trim().toLowerCase();
  if (ec === 'po_missing_reference') return 'PO reference is required before processing';
  if (ec === 'po_amount_mismatch') return 'Invoice amount does not match PO amount';
  if (ec === 'receipt_missing') return 'Receipt confirmation is required';
  if (ec === 'budget_overrun') return 'Invoice exceeds available budget';
  if (ec === 'missing_budget_context') return 'Budget context is missing for this invoice';
  if (ec === 'policy_validation_failed') return 'Invoice violated AP policy checks';
  const state = String(item?.state || '');
  if (state === 'needs_info') return 'Missing required invoice fields';
  if (state === 'needs_approval' || state === 'pending_approval') return 'Pending human approval';
  if (state === 'failed_post') return 'ERP posting failed and needs retry';
  if (state === 'approved') return 'Approved and waiting for ERP posting';
  if (state === 'ready_to_post') return 'Ready to post to ERP';
  if (state === 'posted_to_erp' || state === 'closed') return 'Posted successfully';
  return 'Under AP review';
}

export function getExceptionReason(exceptionCode) {
  const c = String(exceptionCode || '').trim().toLowerCase();
  if (c === 'po_missing_reference') return 'PO reference required for this vendor/category';
  if (c === 'po_amount_mismatch') return 'Invoice amount does not match approved PO';
  if (c === 'receipt_missing') return 'Goods receipt confirmation pending';
  if (c === 'budget_overrun') return 'Invoice exceeds approved budget limit';
  if (c === 'missing_budget_context') return 'No budget context found for this cost center';
  if (c === 'policy_validation_failed') return 'AP policy check failed — review required';
  if (c === 'duplicate_invoice') return 'Duplicate invoice detected for this vendor';
  if (c === 'confidence_low') return 'Extraction confidence too low for auto-posting';
  return '';
}

export function getDueRiskLabel(dueDateValue) {
  if (!dueDateValue) return '';
  const due = new Date(dueDateValue);
  if (Number.isNaN(due.getTime())) return '';
  const diffDays = Math.ceil((due.getTime() - Date.now()) / 86400000);
  if (diffDays < 0) return `Past due ${Math.abs(diffDays)}d`;
  if (diffDays === 0) return 'Due today';
  if (diffDays <= 3) return `Due in ${diffDays}d`;
  return '';
}

export function getDecisionSummary(item, budgetContext) {
  const state = String(item?.state || 'received').toLowerCase();
  if (budgetContext?.requiresDecision) return { title: 'Budget review required', detail: 'Choose override, budget adjustment, or rejection.', tone: 'warning' };
  if (state === 'needs_info' || String(item?.exception_code || '').trim()) return { title: 'Needs review', detail: getIssueSummary(item), tone: 'warning' };
  if (state === 'needs_approval' || state === 'pending_approval') return { title: 'Approval required', detail: 'Route to approver with full context.', tone: 'neutral' };
  if (state === 'approved' || state === 'ready_to_post') return { title: 'Ready for posting', detail: 'Required checks are complete.', tone: 'good' };
  if (state === 'posted_to_erp' || state === 'closed') return { title: 'Completed', detail: 'Invoice has already been posted.', tone: 'good' };
  if (state === 'failed_post') return { title: 'Posting failed', detail: 'Retry posting or escalate this invoice.', tone: 'warning' };
  if (state === 'rejected') return { title: 'Rejected', detail: 'No further action required unless reopened.', tone: 'warning' };
  return { title: 'Under review', detail: getIssueSummary(item), tone: 'neutral' };
}

export function getAuditEventPayload(event) {
  return parseJsonObject(event?.payload_json || event?.payloadJson || event?.payload) || {};
}

export function getAuditEventTimestamp(event) {
  const raw = event?.ts || event?.created_at || event?.createdAt || event?.updated_at || event?.updatedAt || null;
  if (!raw) return 0;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function normalizeAuditEventType(value) {
  return String(value || '').trim().toLowerCase().replace(/[-\s]+/g, '_');
}

export function getReasonSheetDefaults(actionType = 'generic') {
  const n = String(actionType || '').trim().toLowerCase();
  if (n === 'reject' || n === 'budget_reject') return { chips: ['Duplicate invoice', 'Incorrect amount', 'Missing required docs', 'Out of policy'], required: true };
  if (n === 'approve_override' || n === 'budget_override') return { chips: ['Reviewed with approver', 'Urgent vendor payment', 'Policy exception approved', 'Business critical'], required: true };
  if (n === 'budget_adjustment') return { chips: ['Threshold update needed', 'Seasonal spend spike', 'Project budget exception', 'One-off adjustment'], required: false };
  if (n === 'approval_route' || n === 'approval_nudge') return { chips: ['Approver unavailable', 'SLA at risk', 'Waiting on budget owner', 'Escalation requested'], required: false };
  return { chips: ['Reviewed', 'Needs follow-up', 'Policy requirement', 'Other'], required: true };
}

export function formatPercentMetric(metric) {
  const raw = Number(metric?.value ?? metric?.rate);
  if (!Number.isFinite(raw)) return 'N/A';
  return `${(raw >= 0 && raw <= 1 ? raw * 100 : raw).toFixed(1)}%`;
}

export function formatHoursMetric(metric) {
  const value = Number(metric?.avg_hours ?? metric?.avg);
  if (!Number.isFinite(value)) return 'N/A';
  return `${value.toFixed(1)}h`;
}
