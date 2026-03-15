/** Clearledgr design tokens — JS export for Preact components */

export const STATE_LABELS = {
  received: 'Received',
  validated: 'Validated',
  needs_info: 'Needs info',
  needs_approval: 'Needs approval',
  approved: 'Approved',
  ready_to_post: 'Ready to post',
  posted_to_erp: 'Posted to ERP',
  closed: 'Closed',
  rejected: 'Rejected',
  failed_post: 'Failed post',
};

export const STATE_CSS_CLASS = {
  received: 'cl-state-received',
  validated: 'cl-state-validated',
  needs_info: 'cl-state-needs-info',
  needs_approval: 'cl-state-needs-approval',
  approved: 'cl-state-approved',
  ready_to_post: 'cl-state-ready-to-post',
  posted_to_erp: 'cl-state-posted',
  closed: 'cl-state-closed',
  rejected: 'cl-state-rejected',
  failed_post: 'cl-state-failed',
};

export function getStateLabel(state) {
  return STATE_LABELS[state] || 'Received';
}

export function getStateCssClass(state) {
  return STATE_CSS_CLASS[state] || 'cl-state-received';
}

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
  try {
    return date.toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: 'Europe/London',
    });
  } catch (_) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
}

export function formatDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  try {
    return date.toLocaleString('en-GB', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: 'Europe/London',
    });
  } catch (_) {
    return date.toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
}

export function trimText(value, maxLength = 96) {
  const text = String(value ?? '').trim();
  if (!text) return '';
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(1, maxLength - 1)).trim()}…`;
}
