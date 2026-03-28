import { hasOpsAccessRole } from './roles.js';
import {
  getDocumentTypeLabel,
  isInvoiceDocumentType,
} from './document-types.js';
import { getFinanceEffectNotice, parseJsonObject } from './formatters.js';

const RESUME_WORKFLOW_REASON_CODES = new Set([
  'field_review_required',
  'blocking_source_conflicts',
  'confidence_field_review_required',
]);

function normalizeAuditToken(value) {
  return String(value || '').trim().toLowerCase().replace(/[-\s]+/g, '_');
}

function addReasonTokens(target, value) {
  if (!value) return;
  if (Array.isArray(value)) {
    value.forEach((entry) => addReasonTokens(target, entry));
    return;
  }
  String(value)
    .split(',')
    .map((entry) => normalizeAuditToken(entry))
    .filter(Boolean)
    .forEach((entry) => target.add(entry));
}

function humanizeToken(value) {
  return String(value || '')
    .trim()
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function getAuditReasonTokens(event) {
  const payload = parseJsonObject(event?.payload_json || event?.payloadJson || event?.payload) || {};
  const response = payload?.response && typeof payload.response === 'object' ? payload.response : {};
  const target = new Set();

  addReasonTokens(target, event?.reason);
  addReasonTokens(target, event?.operator_reason);
  addReasonTokens(target, payload?.reason);
  addReasonTokens(target, payload?.reason_code);
  addReasonTokens(target, payload?.reason_codes);
  addReasonTokens(target, response?.reason);
  addReasonTokens(target, response?.reason_code);
  addReasonTokens(target, response?.reason_codes);

  return target;
}

export function shouldOfferResumeWorkflow(item, auditEvents = [], documentType = 'invoice') {
  if (!isInvoiceDocumentType(documentType)) return false;

  const normalizedState = normalizeWorkState(item?.state || '');
  if (!['ready_to_post', 'failed_post'].includes(normalizedState)) return false;
  if (Boolean(item?.requires_field_review)) return false;

  const sourceConflicts = Array.isArray(item?.source_conflicts) ? item.source_conflicts : [];
  if (sourceConflicts.some((conflict) => Boolean(conflict?.blocking))) return false;

  return (Array.isArray(auditEvents) ? auditEvents : []).some((event) => {
    const eventType = normalizeAuditToken(event?.event_type || event?.eventType);
    const reasons = getAuditReasonTokens(event);
    if ([...reasons].some((reason) => RESUME_WORKFLOW_REASON_CODES.has(reason))) {
      return true;
    }
    return eventType === 'retry_recoverable_failure_blocked';
  });
}

export function normalizeWorkState(state) {
  const normalized = String(state || '').trim().toLowerCase();
  if (!normalized) return 'received';
  if (normalized === 'pending_approval') return 'needs_approval';
  if (normalized === 'posted') return 'posted_to_erp';
  return normalized;
}

export function needsEntityRouting(item = null, state = '', documentType = 'invoice') {
  if (!isInvoiceDocumentType(documentType)) return false;
  const normalizedState = normalizeWorkState(state || item?.state || '');
  if (!['received', 'validated'].includes(normalizedState)) return false;
  const status = String(
    item?.entity_routing_status
    || item?.entity_routing?.status
    || ''
  ).trim().toLowerCase();
  if (status) return status === 'needs_review';
  const candidates = Array.isArray(item?.entity_candidates)
    ? item.entity_candidates
    : (Array.isArray(item?.entity_routing?.candidates) ? item.entity_routing.candidates : []);
  return candidates.length > 1;
}

export function canEscalateApproval(item = null, state = '', actorRole = 'operator', documentType = 'invoice') {
  if (!hasOpsAccessRole(actorRole)) return false;
  if (!isInvoiceDocumentType(documentType)) return false;
  if (normalizeWorkState(state || item?.state || '') !== 'needs_approval') return false;
  return Boolean(
    item?.approval_followup?.escalation_due
    || item?.approval_followup?.next_action === 'escalate_approval'
  );
}

export function canReassignApproval(item = null, state = '', actorRole = 'operator', documentType = 'invoice') {
  if (!hasOpsAccessRole(actorRole)) return false;
  if (!isInvoiceDocumentType(documentType)) return false;
  return normalizeWorkState(state || item?.state || '') === 'needs_approval';
}

export function getPrimaryActionConfig(state, actorRole = 'operator', documentType = 'invoice', item = null) {
  if (!hasOpsAccessRole(actorRole)) return null;
  if (!isInvoiceDocumentType(documentType)) return null;
  const normalized = normalizeWorkState(state);
  if (normalized === 'received' || normalized === 'validated') {
    if (needsEntityRouting(item, normalized, documentType)) {
      return { id: 'resolve_entity_route', label: 'Resolve entity' };
    }
    return { id: 'request_approval', label: 'Request approval' };
  }
  if (normalized === 'needs_info') {
    const followupNextAction = String(item?.followup_next_action || '').trim().toLowerCase();
    if (followupNextAction === 'await_vendor_response') return null;
    if (followupNextAction === 'manual_vendor_escalation') return null;
    return { id: 'prepare_info_request', label: 'Prepare info request' };
  }
  if (normalized === 'needs_approval') {
    if (canEscalateApproval(item, normalized, actorRole, documentType)) {
      return { id: 'escalate_approval', label: 'Escalate approval' };
    }
    return { id: 'nudge_approver', label: 'Nudge approver' };
  }
  if (normalized === 'ready_to_post') {
    return { id: 'preview_erp_post', label: 'Preview ERP post' };
  }
  if (normalized === 'failed_post') {
    return { id: 'retry_erp_post', label: 'Retry ERP post' };
  }
  return null;
}

export function getWorkStateNotice(state, documentType = 'invoice', item = null) {
  const normalized = normalizeWorkState(state);
  const financeEffectNotice = getFinanceEffectNotice(item);
  if (!isInvoiceDocumentType(documentType)) {
    const documentLabel = getDocumentTypeLabel(documentType, { lowercase: true });
    const resolution = item && typeof item === 'object' && item.non_invoice_resolution && typeof item.non_invoice_resolution === 'object'
      ? item.non_invoice_resolution
      : {};
    const accountingTreatment = String(
      item?.non_invoice_accounting_treatment
      || resolution?.accounting_treatment
      || ''
    ).trim();
    const downstreamQueue = String(
      item?.non_invoice_downstream_queue
      || resolution?.downstream_queue
      || ''
    ).trim();
    const resolved = Boolean(resolution?.resolved_at);
    if (resolved && accountingTreatment) {
      const treatmentText = humanizeToken(accountingTreatment).replace(/^Finance Document Reviewed$/i, 'Review recorded');
      const queueText = downstreamQueue ? ` Next queue: ${humanizeToken(downstreamQueue).toLowerCase()}.` : '';
      return `This ${documentLabel} has been resolved. ${treatmentText}.${queueText}`;
    }
    if (normalized === 'rejected') {
      return `This ${documentLabel} has been rejected.`;
    }
    if (normalized === 'closed') {
      return `This ${documentLabel} has been closed.`;
    }
    if (documentType === 'statement') {
      return 'This bank statement goes to reconciliation, not invoice approval or ERP posting.';
    }
    if (documentType === 'payment_request') {
      return 'This payment request is handled outside the invoice flow. Approval and ERP posting are not available here.';
    }
    if (documentType === 'payment') {
      return 'This payment confirmation shows money already moved. It is tracked outside the invoice flow.';
    }
    if (documentType === 'receipt') {
      return 'This receipt is supporting evidence for a completed payment, not an open invoice.';
    }
    return `This ${documentLabel} is tracked as a non-invoice record. Invoice approval and ERP posting are not available here.`;
  }
  if (financeEffectNotice) {
    return financeEffectNotice;
  }
  if (normalized === 'needs_info') {
    const followupNextAction = String(item?.followup_next_action || '').trim().toLowerCase();
    if (followupNextAction === 'await_vendor_response') {
      return 'Waiting for the vendor response. Clearledgr already prepared the follow-up.';
    }
    if (followupNextAction === 'manual_vendor_escalation') {
      return 'Vendor follow-up reached the retry limit and now needs manual escalation.';
    }
    if (followupNextAction === 'nudge_vendor_followup') {
      return 'The vendor has not replied yet. Send the next follow-up when you are ready.';
    }
  }
  if (normalized === 'approved') {
    return 'Approval received. Clearledgr is preparing the posting step.';
  }
  if (normalized === 'posted_to_erp' || normalized === 'closed') {
    return 'Invoice has already been posted to the ERP.';
  }
  if (normalized === 'rejected') {
    return 'Invoice has been rejected.';
  }
  return '';
}

export function canRejectWorkItem(state, actorRole = 'operator', documentType = 'invoice') {
  if (!hasOpsAccessRole(actorRole)) return false;
  if (!isInvoiceDocumentType(documentType)) return false;
  const normalized = normalizeWorkState(state);
  return ['received', 'validated', 'needs_approval', 'needs_info'].includes(normalized);
}

export function canNudgeApprover(state, actorRole = 'operator', documentType = 'invoice') {
  if (!hasOpsAccessRole(actorRole)) return false;
  if (!isInvoiceDocumentType(documentType)) return false;
  return normalizeWorkState(state) === 'needs_approval';
}
