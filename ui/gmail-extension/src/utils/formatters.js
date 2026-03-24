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
  attachment: 'Invoice attachment',
  llm: 'Current invoice parse',
  parser: 'Current invoice parse',
  current_parse: 'Current invoice parse',
  ocr: 'Current invoice parse',
};

const FINANCE_EFFECT_REASON_LABELS = {
  linked_finance_target_amount_missing: 'Target amount missing',
  linked_finance_target_not_invoice: 'Linked target is not an invoice',
  linked_credit_adjustment_present: 'Linked credit changes payable amount',
  linked_cash_application_present: 'Linked cash activity changes settlement',
  linked_over_credit: 'Linked credits exceed invoice amount',
  linked_overpayment: 'Linked cash exceeds remaining balance',
  linked_refund_exceeds_cash_out: 'Refund exceeds linked cash out',
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

function getCurrentFieldReviewValue(item = {}, field = '') {
  const token = String(field || '').trim().toLowerCase();
  if (token === 'vendor') return item?.vendor_name || item?.vendor || null;
  if (token === 'document_type') return item?.document_type || null;
  return item?.[token] ?? null;
}

function inferFieldReviewSource(currentValue, emailValue, attachmentValue) {
  if (currentValue !== null && currentValue !== undefined && currentValue !== '') {
    if (attachmentValue !== null && attachmentValue !== undefined && attachmentValue !== '' && currentValue === attachmentValue) {
      return 'attachment';
    }
    if (emailValue !== null && emailValue !== undefined && emailValue !== '' && currentValue === emailValue) {
      return 'email';
    }
  }
  return '';
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
    let winnerReason = `${winnerLabel} is currently selected.`;
    if (winningSource === 'attachment' && attachmentName) {
      winnerReason = `${winnerLabel} is currently selected from ${attachmentName}.`;
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
      reason_label: 'Email and attachment do not match.',
      paused_reason: `Check ${fieldLabel.toLowerCase()} because the email and attachment do not match.`,
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
    const provenanceEntry = provenance[field] && typeof provenance[field] === 'object' ? provenance[field] : {};
    const evidenceEntry = evidence[field] && typeof evidence[field] === 'object' ? evidence[field] : {};
    const candidateValues = provenanceEntry.candidates && typeof provenanceEntry.candidates === 'object' ? provenanceEntry.candidates : {};
    const confidenceValue = typeof blocker === 'object' ? blocker?.confidence : null;
    const confidencePct = typeof blocker === 'object'
      ? (blocker?.confidence_pct ?? (confidenceValue !== null && confidenceValue !== undefined && confidenceValue !== '' ? Math.round(Number(confidenceValue) * 100) : null))
      : null;
    const thresholdPct = typeof blocker === 'object' ? blocker?.threshold_pct ?? 95 : 95;
    let currentSource = String(provenanceEntry.source || evidenceEntry.source || '').trim().toLowerCase();
    const currentValue = provenanceEntry.value ?? evidenceEntry.selected_value ?? getCurrentFieldReviewValue(item, field);
    const emailValue = candidateValues.email ?? evidenceEntry.email_value ?? null;
    const attachmentValue = candidateValues.attachment ?? evidenceEntry.attachment_value ?? null;
    if (!currentSource) currentSource = inferFieldReviewSource(currentValue, emailValue, attachmentValue);
    blockers.push({
      kind: 'confidence',
      field,
      field_label: fieldLabel,
      reason: String(typeof blocker === 'object' ? blocker?.reason || blocker?.code || 'critical_field_review_required' : 'critical_field_review_required'),
      reason_label: 'A person needs to confirm this field before the invoice can move forward.',
      paused_reason: confidencePct !== null && confidencePct !== undefined && thresholdPct !== null && thresholdPct !== undefined
        ? `Review ${fieldLabel.toLowerCase()} before this invoice moves forward.`
        : `Review ${fieldLabel.toLowerCase()} before this invoice moves forward.`,
      current_value: currentValue,
      current_value_display: formatFieldReviewValue(field, currentValue, currency),
      current_source: currentSource || null,
      current_source_label: currentSource ? getSourceLabel(currentSource) : '',
      email_value: emailValue,
      email_value_display: formatFieldReviewValue(field, emailValue, currency),
      attachment_value: attachmentValue,
      attachment_value_display: formatFieldReviewValue(field, attachmentValue, currency),
      confidence: confidenceValue,
      confidence_pct: confidencePct,
      threshold_pct: thresholdPct,
      winner_reason: confidencePct !== null && confidencePct !== undefined && thresholdPct !== null && thresholdPct !== undefined
        ? `Clearledgr read ${formatFieldReviewValue(field, currentValue, currency)}${currentSource ? ` from the ${getSourceLabel(currentSource).toLowerCase()}` : ''}. Because ${fieldLabel.toLowerCase()} is a critical field, a person needs to confirm it before approval continues.`
        : `Clearledgr needs the ${fieldLabel.toLowerCase()} confirmed before this invoice can continue.`,
      auto_check_note: confidencePct !== null && confidencePct !== undefined && thresholdPct !== null && thresholdPct !== undefined
        ? `Auto-pass rule: ${thresholdPct}% minimum. This read scored ${confidencePct}%.`
        : '',
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
  if (fieldLabels.length === 0) return 'Check the extracted fields before continuing.';
  if (fieldLabels.length === 1) {
    const hasSourceConflict = blockers.some((blocker) => blocker?.kind === 'source_conflict');
    return hasSourceConflict
      ? `Review ${fieldLabels[0]} before this invoice moves forward because the email and attachment do not match.`
      : `Review ${fieldLabels[0]} before this invoice moves forward.`;
  }
  const summary = `${fieldLabels.slice(0, -1).join(', ')} and ${fieldLabels[fieldLabels.length - 1]}`;
  const hasSourceConflict = blockers.some((blocker) => blocker?.kind === 'source_conflict');
  return hasSourceConflict
    ? `Review ${summary} before this invoice moves forward because the email and attachment do not match.`
    : `Review ${summary} before this invoice moves forward.`;
}

export function getFinanceEffectBlockers(item = {}) {
  const rawBlockers = Array.isArray(item?.finance_effect_blockers) ? item.finance_effect_blockers : [];
  return rawBlockers
    .map((blocker) => {
      if (!blocker || typeof blocker !== 'object') return null;
      const code = String(blocker.code || '').trim();
      if (!code) return null;
      return {
        code,
        label: FINANCE_EFFECT_REASON_LABELS[code] || humanizeSnakeText(code.replace(/^linked_/, '')),
        detail: String(blocker.detail || '').trim(),
      };
    })
    .filter(Boolean);
}

export function getFinanceEffectNotice(item = {}) {
  const summary = item?.finance_effect_summary && typeof item.finance_effect_summary === 'object'
    ? item.finance_effect_summary
    : {};
  const blockers = getFinanceEffectBlockers(item);
  if (Boolean(item?.finance_effect_review_required)) {
    return blockers[0]?.detail
      || 'Credits, payments, or refunds change the invoice balance. Review them before continuing.';
  }
  if (!summary || Object.keys(summary).length === 0) return '';

  const creditTotal = Number(summary.applied_credit_total || 0);
  const netCashTotal = Number(summary.net_cash_applied_total || 0);
  const remainingBalance = Number(summary.remaining_balance_amount || 0);
  if (!Number.isFinite(creditTotal) && !Number.isFinite(netCashTotal)) return '';
  if ((creditTotal > 0 || netCashTotal !== 0) && Number.isFinite(remainingBalance)) {
    return `Remaining balance after credits and payments: ${formatAmount(remainingBalance, summary.currency || item?.currency || 'USD')}.`;
  }
  return '';
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

export function getEvidenceChecklistEntries(item = {}, state = '', contextPayload = {}) {
  const approvals = contextPayload?.approvals || {};
  const erp = contextPayload?.erp || {};
  const hasEmail = Boolean(getSourceThreadId(item) || getSourceMessageId(item) || item?.subject);
  const attachmentCount = Number(item?.attachment_count || 0);
  const attachmentNames = Array.isArray(item?.attachment_names) ? item.attachment_names.filter(Boolean) : [];
  const hasAttachment = Boolean(item?.has_attachment || attachmentCount > 0 || attachmentNames.length > 0);
  const hasApproval = Boolean(
    Number(approvals.count || 0) > 0
    || ['needs_approval', 'approved', 'ready_to_post', 'posted_to_erp', 'closed'].includes(String(state || '').trim().toLowerCase())
  );
  const hasErpLink = Boolean(item?.erp_reference || item?.erp_bill_id || erp.erp_reference || erp.connector_available || erp.state);
  const attachmentLabel = hasAttachment
    ? (attachmentNames[0]
        ? trimText(attachmentNames[0], 42)
        : `${Math.max(attachmentCount, 1)} ${Math.max(attachmentCount, 1) === 1 ? 'file' : 'files'}`)
    : 'No file linked';

  return [
    {
      key: 'email',
      label: 'Email',
      status: hasEmail ? 'ok' : 'missing',
      text: hasEmail ? 'Linked' : 'Not linked',
      detail: hasEmail
        ? trimText(item?.subject || 'Gmail thread linked', 48)
        : 'No Gmail thread or source message is attached yet.',
    },
    {
      key: 'attachment',
      label: 'Attachment',
      status: hasAttachment ? 'ok' : 'missing',
      text: hasAttachment ? 'Attached' : 'No file',
      detail: attachmentLabel,
    },
    {
      key: 'approval',
      label: 'Approval',
      status: hasApproval ? 'ok' : 'missing',
      text: hasApproval ? (String(state || '').trim().toLowerCase() === 'needs_approval' ? 'Routed' : 'Available') : 'Not routed',
      detail: hasApproval
        ? (String(state || '').trim().toLowerCase() === 'needs_approval'
            ? 'Approval request is already in flight.'
            : 'Approval evidence is available on this record.')
        : 'No approval trail is attached yet.',
    },
    {
      key: 'erp',
      label: 'ERP',
      status: hasErpLink ? 'ok' : 'missing',
      text: hasErpLink
        ? (item?.erp_reference || erp.erp_reference ? 'Linked' : 'Connected')
        : 'Not connected',
      detail: item?.erp_reference || erp?.erp_reference || (erp?.connector_available ? 'Connector active, no posted reference yet.' : 'No ERP link on this record.'),
    },
  ];
}

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
  if (c === 'planner_failed') return 'Automatic review could not continue for this invoice';
  if (c === 'erp_post_failed') return 'Posting to the ERP failed and needs retry';
  return '';
}

export function getExceptionLabel(exceptionCode) {
  const c = String(exceptionCode || '').trim().toLowerCase();
  if (c === 'po_missing_reference') return 'PO required';
  if (c === 'po_amount_mismatch') return 'PO amount mismatch';
  if (c === 'receipt_missing') return 'Receipt missing';
  if (c === 'budget_overrun') return 'Budget overrun';
  if (c === 'missing_budget_context') return 'Missing budget context';
  if (c === 'policy_validation_failed') return 'Policy review';
  if (c === 'duplicate_invoice') return 'Duplicate invoice';
  if (c === 'confidence_low') return 'Low confidence';
  if (c === 'planner_failed') return 'Processing issue';
  if (c === 'erp_post_failed') return 'ERP post failed';
  return c ? humanizeSnakeText(c) : '';
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
