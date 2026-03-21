import { hasOpsAccessRole } from './roles.js';
import {
  getDocumentTypeLabel,
  isInvoiceDocumentType,
} from './document-types.js';
import { parseJsonObject } from './formatters.js';

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

export function getPrimaryActionConfig(state, actorRole = 'operator', documentType = 'invoice') {
  if (!hasOpsAccessRole(actorRole)) return null;
  if (!isInvoiceDocumentType(documentType)) return null;
  const normalized = normalizeWorkState(state);
  if (normalized === 'received' || normalized === 'validated') {
    return { id: 'request_approval', label: 'Request approval' };
  }
  if (normalized === 'needs_info') {
    return { id: 'prepare_info_request', label: 'Prepare info request' };
  }
  if (normalized === 'needs_approval') {
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
      return 'This bank statement is routed to reconciliation work, not AP approval or ERP posting.';
    }
    if (documentType === 'payment_request') {
      return 'This payment request is routed outside the invoice workflow. AP approval and ERP posting are disabled.';
    }
    if (documentType === 'payment') {
      return 'This payment confirmation proves money already moved. It is tracked outside the AP payable workflow.';
    }
    if (documentType === 'receipt') {
      return 'This receipt is supporting evidence for a completed payment, not an open payable.';
    }
    return `This ${documentLabel} is tracked as a non-invoice finance document. Invoice approval and ERP posting are disabled.`;
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
