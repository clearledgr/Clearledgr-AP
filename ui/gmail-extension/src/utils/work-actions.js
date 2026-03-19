import { hasOpsAccessRole } from './roles.js';

export function normalizeWorkState(state) {
  const normalized = String(state || '').trim().toLowerCase();
  if (!normalized) return 'received';
  if (normalized === 'pending_approval') return 'needs_approval';
  if (normalized === 'posted') return 'posted_to_erp';
  return normalized;
}

export function getPrimaryActionConfig(state, actorRole = 'operator') {
  if (!hasOpsAccessRole(actorRole)) return null;
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

export function getWorkStateNotice(state) {
  const normalized = normalizeWorkState(state);
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

export function canRejectWorkItem(state, actorRole = 'operator') {
  if (!hasOpsAccessRole(actorRole)) return false;
  const normalized = normalizeWorkState(state);
  return ['received', 'validated', 'needs_approval', 'needs_info'].includes(normalized);
}

export function canNudgeApprover(state, actorRole = 'operator') {
  if (!hasOpsAccessRole(actorRole)) return false;
  return normalizeWorkState(state) === 'needs_approval';
}
