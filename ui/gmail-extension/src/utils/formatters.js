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

const FIELD_LABELS = {
  amount: 'Amount',
  currency: 'Currency',
  invoice_number: 'Invoice number',
  vendor: 'Vendor',
  invoice_date: 'Invoice date',
  due_date: 'Due date',
  document_type: 'Document type',
};

const SOURCE_LABELS = {
  email: 'Email',
  attachment: 'Attachment',
  llm: 'Model',
};

function getFieldLabel(field) {
  const token = String(field || '').trim().toLowerCase();
  if (!token) return 'Field';
  return FIELD_LABELS[token] || humanizeSnakeText(token);
}

function getSourceLabel(source) {
  const token = String(source || '').trim().toLowerCase();
  if (!token) return 'Source';
  return SOURCE_LABELS[token] || humanizeSnakeText(token);
}

function formatFieldReviewValue(field, value, currency = 'USD') {
  if (value === null || value === undefined || value === '') return 'Not found';
  if (String(field || '').trim().toLowerCase() === 'amount') {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return `${currency} ${numeric.toFixed(2)}`;
  }
  return String(value);
}

export function getFieldReviewBlockers(item = {}) {
  const existing = Array.isArray(item?.field_review_blockers) ? item.field_review_blockers : [];
  if (existing.length > 0) return existing;

  const provenance = item?.field_provenance && typeof item.field_provenance === 'object' ? item.field_provenance : {};
  const evidence = item?.field_evidence && typeof item.field_evidence === 'object' ? item.field_evidence : {};
  const conflicts = Array.isArray(item?.source_conflicts) ? item.source_conflicts : [];
  const blockers = [];
  const seen = new Set();
  const currency = String(item?.currency || 'USD').trim().toUpperCase() || 'USD';

  for (const conflict of conflicts) {
    if (!conflict || typeof conflict !== 'object' || !conflict.blocking) continue;
    const field = String(conflict.field || '').trim().toLowerCase();
    if (!field) continue;

    const provenanceEntry = provenance[field] && typeof provenance[field] === 'object' ? provenance[field] : {};
    const evidenceEntry = evidence[field] && typeof evidence[field] === 'object' ? evidence[field] : {};
    const values = conflict.values && typeof conflict.values === 'object' ? conflict.values : {};
    const winningSource = String(
      provenanceEntry.source
      || conflict.preferred_source
      || evidenceEntry.source
      || 'attachment'
    ).trim().toLowerCase() || 'attachment';
    const winningValue = provenanceEntry.value ?? evidenceEntry.selected_value ?? values[winningSource];
    const fieldLabel = getFieldLabel(field);
    const winnerLabel = getSourceLabel(winningSource);
    const attachmentName = String(evidenceEntry.attachment_name || '').trim();
    let winnerReason = `${winnerLabel} currently wins because Clearledgr selected that value as canonical.`;
    if (winningSource === 'attachment' && attachmentName) {
      winnerReason = `${winnerLabel} currently wins because Clearledgr selected the value from ${attachmentName} as canonical.`;
    }

    blockers.push({
      kind: 'source_conflict',
      field,
      field_label: fieldLabel,
      email_value: values.email ?? evidenceEntry.email_value ?? null,
      email_value_display: formatFieldReviewValue(field, values.email ?? evidenceEntry.email_value, currency),
      attachment_value: values.attachment ?? evidenceEntry.attachment_value ?? null,
      attachment_value_display: formatFieldReviewValue(field, values.attachment ?? evidenceEntry.attachment_value, currency),
      winning_source: winningSource,
      winning_source_label: winnerLabel,
      winning_value: winningValue,
      winning_value_display: formatFieldReviewValue(field, winningValue, currency),
      reason: String(conflict.reason || 'source_value_mismatch'),
      reason_label: 'Email and attachment disagree.',
      paused_reason: `Workflow paused until ${fieldLabel.toLowerCase()} is confirmed because the email and attachment disagree.`,
      winner_reason: winnerReason,
    });
    seen.add(field);
  }

  const confidenceBlockers = Array.isArray(item?.confidence_blockers) ? item.confidence_blockers : [];
  for (const blocker of confidenceBlockers) {
    const field = String(
      typeof blocker === 'string'
        ? blocker
        : blocker?.field || blocker?.code || ''
    ).trim().toLowerCase();
    if (!field || seen.has(field)) continue;
    const fieldLabel = getFieldLabel(field);
    blockers.push({
      kind: 'confidence',
      field,
      field_label: fieldLabel,
      reason: String(typeof blocker === 'object' ? blocker?.reason || blocker?.code || 'critical_field_review_required' : 'critical_field_review_required'),
      reason_label: 'Critical extracted field needs review.',
      paused_reason: `Workflow paused until ${fieldLabel.toLowerCase()} is reviewed.`,
    });
    seen.add(field);
  }

  return blockers;
}

export function getWorkflowPauseReason(item = {}) {
  const explicit = String(item?.workflow_paused_reason || '').trim();
  if (explicit) return explicit;
  const blockers = getFieldReviewBlockers(item);
  if (blockers.length === 0 && !item?.requires_field_review) return '';
  const fieldLabels = blockers.map((blocker) => String(blocker?.field_label || '').trim().toLowerCase()).filter(Boolean);
  if (fieldLabels.length === 0) return 'Workflow paused until extracted fields are reviewed.';
  if (fieldLabels.length === 1) {
    const hasSourceConflict = blockers.some((blocker) => blocker?.kind === 'source_conflict');
    return hasSourceConflict
      ? `Workflow paused until ${fieldLabels[0]} is confirmed because the email and attachment disagree.`
      : `Workflow paused until ${fieldLabels[0]} is reviewed.`;
  }
  const summary = `${fieldLabels.slice(0, -1).join(', ')} and ${fieldLabels[fieldLabels.length - 1]}`;
  const hasSourceConflict = blockers.some((blocker) => blocker?.kind === 'source_conflict');
  return hasSourceConflict
    ? `Workflow paused until ${summary} are confirmed because the email and attachment disagree.`
    : `Workflow paused until ${summary} are reviewed.`;
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

const AUDIT_IMPORTANCE_RANK = { high: 0, medium: 1, low: 2 };
const AUDIT_SEVERITY_RANK = { error: 0, warning: 1, success: 2, info: 3 };

export function normalizeAuditImportance(value) {
  const token = String(value || '').trim().toLowerCase();
  if (token === 'high' || token === 'medium' || token === 'low') return token;
  return 'medium';
}

export function getAuditImportanceLabel(value) {
  const importance = normalizeAuditImportance(value);
  if (importance === 'high') return 'Key';
  if (importance === 'low') return 'Background';
  return 'Notable';
}

export function buildAuditRow(event) {
  const payload = getAuditEventPayload(event);
  const eventType = normalizeAuditEventType(
    event?.event_type || event?.eventType || payload?.event_type || event?.action || 'action_recorded',
  );
  const safeTitle = eventType === 'state_transition' ? 'Status updated' : 'Action recorded';
  let safeDetail = 'Action recorded for this invoice.';
  if (eventType === 'state_transition') safeDetail = 'Invoice status changed.';
  else if (eventType === 'erp_post_completed') safeDetail = 'Invoice posting completed successfully.';
  else if (eventType === 'erp_post_failed') safeDetail = 'Clearledgr could not complete ERP posting.';
  const importance = normalizeAuditImportance(event?.operator_importance || event?.operator?.importance);
  const severity = String(event?.operator_severity || event?.operator?.severity || 'info').trim().toLowerCase() || 'info';
  const evidenceLabel = trimText(String(
    event?.operator_evidence_label
      || event?.operator?.evidence_label
      || event?.operator?.evidence?.label
      || '',
  ).trim(), 48);
  const evidenceDetail = trimText(String(
    event?.operator_evidence_detail
      || event?.operator?.evidence_detail
      || event?.operator?.evidence?.detail
      || '',
  ).trim(), 180);
  const actionHint = trimText(String(
    event?.operator_action_hint
      || event?.operator_next_action
      || event?.operator?.next_action
      || event?.operator?.action_hint
      || '',
  ).trim(), 160);
  const timestampRaw = getAuditEventTimestamp(event);

  return {
    event,
    eventType,
    title: trimText(String(event?.operator_title || safeTitle), 72),
    detail: trimText(String(event?.operator_message || safeDetail).trim(), 160),
    timestampRaw,
    timestamp: formatDateTime(event?.ts || event?.created_at || event?.createdAt || event?.updated_at || event?.updatedAt || event?.timestamp),
    severity,
    importance,
    importanceLabel: getAuditImportanceLabel(importance),
    category: String(event?.operator_category || event?.operator?.category || '').trim().toLowerCase(),
    evidenceLabel,
    evidenceDetail,
    actionHint,
    isBackground: importance === 'low',
  };
}

export function partitionAuditEvents(events, options = {}) {
  const primaryLimit = Number.isFinite(Number(options.primaryLimit)) ? Math.max(0, Number(options.primaryLimit)) : Number.POSITIVE_INFINITY;
  const secondaryLimit = Number.isFinite(Number(options.secondaryLimit)) ? Math.max(0, Number(options.secondaryLimit)) : Number.POSITIVE_INFINITY;
  const rows = (Array.isArray(events) ? events : [])
    .map((event) => buildAuditRow(event))
    .sort((left, right) => {
      const importanceDelta = (AUDIT_IMPORTANCE_RANK[left.importance] ?? 1) - (AUDIT_IMPORTANCE_RANK[right.importance] ?? 1);
      if (importanceDelta !== 0) return importanceDelta;
      const severityDelta = (AUDIT_SEVERITY_RANK[left.severity] ?? 3) - (AUDIT_SEVERITY_RANK[right.severity] ?? 3);
      if (severityDelta !== 0) return severityDelta;
      return right.timestampRaw - left.timestampRaw;
    });

  const primaryRows = [];
  const secondaryRows = [];
  rows.forEach((row) => {
    if (row.isBackground) secondaryRows.push(row);
    else primaryRows.push(row);
  });

  return {
    rows,
    primaryRows: primaryRows.slice(0, primaryLimit),
    secondaryRows: secondaryRows.slice(0, secondaryLimit),
    primaryHiddenCount: Math.max(0, primaryRows.length - Math.min(primaryRows.length, primaryLimit)),
    secondaryHiddenCount: Math.max(0, secondaryRows.length - Math.min(secondaryRows.length, secondaryLimit)),
  };
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
