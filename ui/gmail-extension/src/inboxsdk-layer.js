/**
 * Clearledgr AP v1 InboxSDK Layer
 * Embedded only. No dashboard. No navigation.
 */
import * as InboxSDK from '@inboxsdk/core';
import { ClearledgrQueueManager } from '../queue-manager.js';

const APP_ID = 'sdk_Clearledgr2026_dc12c60472';
const INIT_KEY = '__clearledgr_ap_v1_inboxsdk_initialized';
const LOGO_PATH = 'icons/icon48.png';
const STORAGE_ACTIVE_AP_ITEM_ID = 'clearledgr_active_ap_item_id';

const STATE_LABELS = {
  received: 'Received',
  validated: 'Validated',
  needs_info: 'Needs info',
  needs_approval: 'Needs approval',
  approved: 'Approved',
  ready_to_post: 'Ready to post',
  posted_to_erp: 'Posted to ERP',
  closed: 'Closed',
  rejected: 'Rejected',
  failed_post: 'Failed post'
};

const STATE_COLORS = {
  received: '#2563eb',
  validated: '#0f766e',
  needs_info: '#b45309',
  needs_approval: '#b45309',
  approved: '#15803d',
  ready_to_post: '#0f766e',
  posted_to_erp: '#7c3aed',
  closed: '#0f766e',
  rejected: '#b91c1c',
  failed_post: '#b91c1c'
};

let sdk = null;
let queueManager = null;
let globalSidebarEl = null;
let workSidebarEl = null;
// Legacy test harness still introspects this symbol; runtime keeps it null because
// Gmail ships a single Work surface and does not render an in-panel Ops sidebar.
let opsSidebarEl = null;
let currentThreadId = null;
let selectedItemId = null;
let queueState = [];
let scanStatus = {};
let agentSessionsState = new Map();
let browserTabContext = [];
let agentInsightsState = new Map();
let sourcesState = new Map();
let contextState = new Map();
let activeContextTab = 'email';
let contextUiState = {
  itemId: null,
  loading: false,
  error: ''
};
let agentSummaryState = {
  itemId: null,
  mode: null,
  loading: false,
  error: '',
  data: null
};
let agentPreviewState = {
  key: null,
  loading: false,
  error: '',
  data: null
};
let batchOpsState = {
  mode: null,
  loading: false,
  error: '',
  data: null
};
let batchOpsPolicyState = {
  maxItems: 5,
  amountThreshold: '',
  selectionPreset: 'queue_order'
};
let toastTimer = null;
let rowDecorated = new Set();
// Holds { to, subject, body } when a draft-reply is initiated; consumed by the compose handler.
let _pendingComposePrefill = null;
let auditState = {
  itemId: null,
  loading: false,
  events: []
};

/**
 * @typedef {Object} ReasonSheetState
 * @property {string} actionType
 * @property {boolean} required
 * @property {string[]} chips
 * @property {string} defaultValue
 */

/**
 * @typedef {Object} WorkPanelViewModel
 * @property {string} statusLabel
 * @property {string} subtitle
 * @property {string | null} activeItemId
 * @property {string} decisionSummary
 * @property {string[]} quickActions
 */

function activateSidebarContext(sidebarEl) {
  globalSidebarEl = sidebarEl || null;
}

function bindSidebarContext(sidebarEl) {
  if (!sidebarEl || sidebarEl.__clContextBound) return;
  const activate = () => activateSidebarContext(sidebarEl);
  ['click', 'input', 'change', 'focusin', 'keydown'].forEach((eventName) => {
    sidebarEl.addEventListener(eventName, activate, true);
  });
  sidebarEl.__clContextBound = true;
}

function readLocalStorage(key) {
  try {
    if (typeof window !== 'undefined' && window?.localStorage) {
      return String(window.localStorage.getItem(key) || '').trim();
    }
  } catch (_) {
    return '';
  }
  return '';
}

function writeLocalStorage(key, value) {
  try {
    if (typeof window !== 'undefined' && window?.localStorage) {
      if (value === null || value === undefined || String(value).trim() === '') {
        window.localStorage.removeItem(key);
      } else {
        window.localStorage.setItem(key, String(value).trim());
      }
    }
  } catch (_) {
    // best-effort local persistence only
  }
}

function getAssetUrl(path) {
  try {
    if (typeof chrome !== 'undefined' && chrome?.runtime?.getURL) {
      return chrome.runtime.getURL(path);
    }
  } catch (error) {
    return '';
  }
  return '';
}

function formatAmount(amount, currency = 'USD') {
  if (amount === null || amount === undefined || amount === '') return 'Amount unavailable';
  const numeric = Number(amount);
  if (!Number.isFinite(numeric)) return 'Amount unavailable';
  const value = numeric.toFixed(2);
  return `${currency} ${value}`;
}

function getStateLabel(state) {
  return STATE_LABELS[state] || 'Received';
}

function formatTimestamp(value) {
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

function formatDateTime(value) {
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

function formatAgeSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return '';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

function normalizeBudgetContext(contextPayload, item = null) {
  const approvalsBudget = contextPayload?.approvals?.budget || {};
  const rootBudget = contextPayload?.budget || {};
  const candidate = approvalsBudget?.checks || approvalsBudget?.status ? approvalsBudget : rootBudget;
  const checks = Array.isArray(candidate?.checks) ? candidate.checks : [];
  const status = String(candidate?.status || item?.budget_status || '').trim().toLowerCase();
  const requiresDecision = Boolean(
    candidate?.requires_decision
    || item?.budget_requires_decision
    || status === 'critical'
    || status === 'exceeded'
  );
  return {
    status,
    requiresDecision,
    checks,
    warningCount: Number(candidate?.warning_count || 0),
    criticalCount: Number(candidate?.critical_count || 0),
    exceededCount: Number(candidate?.exceeded_count || 0)
  };
}

function budgetStatusTone(status) {
  const normalized = String(status || '').trim().toLowerCase();
  if (normalized === 'exceeded') return 'cl-context-warning';
  if (normalized === 'critical') return 'cl-context-warning';
  return '';
}

function formatPercentMetric(metric) {
  const raw = Number(metric?.value ?? metric?.rate);
  if (!Number.isFinite(raw)) return 'N/A';
  const value = raw >= 0 && raw <= 1 ? raw * 100 : raw;
  return `${value.toFixed(1)}%`;
}

function formatHoursMetric(metric) {
  const value = Number(metric?.avg_hours ?? metric?.avg);
  if (!Number.isFinite(value)) return 'N/A';
  return `${value.toFixed(1)}h`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function trimText(value, maxLength = 96) {
  const text = String(value ?? '').trim();
  if (!text) return '';
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(1, maxLength - 1)).trim()}…`;
}

function getSourceThreadId(item) {
  return String(item?.thread_id || item?.threadId || '').trim();
}

function getSourceMessageId(item) {
  return String(item?.message_id || item?.messageId || '').trim();
}

function openSourceEmail(item) {
  const threadId = getSourceThreadId(item);
  if (threadId) {
    window.location.hash = `#inbox/${encodeURIComponent(threadId)}`;
    return true;
  }

  const messageId = getSourceMessageId(item);
  if (messageId) {
    window.location.hash = `#search/${encodeURIComponent(messageId)}`;
    return true;
  }

  const subject = String(item?.subject || '').trim();
  if (subject) {
    window.location.hash = `#search/${encodeURIComponent(`subject:"${subject}"`)}`;
    return true;
  }

  return false;
}

function prettifyEventType(value) {
  if (!value) return 'Event';
  return String(value)
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function normalizeAuditEventType(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[-\s]+/g, '_');
}

function isAuditReasonCode(value) {
  const text = String(value || '').trim().toLowerCase();
  if (!text) return false;
  return /^[a-z0-9_-]+$/.test(text);
}

function parseAuditReasonCodes(value) {
  const text = String(value || '').trim();
  if (!text) return [];
  const parts = text.split(',').map((part) => part.trim().toLowerCase()).filter(Boolean);
  if (!parts.length || !parts.every((part) => isAuditReasonCode(part))) return [];
  return parts;
}

function formatAuditReasonText(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const codes = parseAuditReasonCodes(raw);
  if (!codes.length) return raw;
  // Never render raw reason-code payloads in the Work sidebar.
  return '';
}

function getWorkAuditFallbackPresentation(event, item) {
  const eventType = normalizeAuditEventType(event?.event_type || event?.eventType || '');
  const payload = getAuditEventPayload(event);
  const reasonRaw = String(
    event?.decision_reason
    || event?.reason
    || payload?.reason
    || payload?.error_message_redacted
    || payload?.error_message
    || ''
  ).trim();
  const reasonText = formatAuditReasonText(reasonRaw);
  if (eventType === 'deterministic_validation_failed') {
    return {
      title: 'Validation checks failed',
      detail: reasonText || 'Clearledgr found policy or field checks that require review before continuing.',
    };
  }
  if (
    eventType === 'browser_session_created'
    || eventType === 'erp_api_fallback_preview_created'
    || eventType === 'erp_api_fallback_confirmation_captured'
    || eventType === 'erp_api_fallback_requested'
  ) {
    return {
      title: 'ERP fallback prepared',
      detail: reasonText || 'Prepared secure ERP browser fallback session.',
    };
  }
  if (eventType === 'approval_routed_from_extension' || eventType === 'route_for_approval') {
    return {
      title: 'Approval request sent',
      detail: reasonText || 'Approval was sent to the configured approver channel.',
    };
  }
  if (eventType === 'approval_nudge_failed') {
    return {
      title: 'Approval reminder failed',
      detail: reasonText || 'Could not send reminder to approver. Try "Nudge approver" again.',
    };
  }
  if (eventType === 'approval_nudge' || eventType === 'approval_nudge_sent') {
    return {
      title: 'Reminder sent',
      detail: reasonText || 'A reminder was sent to the approver channel.',
    };
  }
  if (eventType === 'state_transition_rejected') {
    return {
      title: 'Action blocked for safety',
      detail: reasonText || 'Requested action is not allowed from the current invoice status.',
    };
  }
  if (eventType === 'state_transition') {
    const fromState = String(event?.from_state || payload?.from_state || payload?.fromState || '').trim();
    const toState = String(event?.to_state || payload?.to_state || payload?.toState || '').trim();
    const statusTarget = toState ? getStateLabel(toState) : 'new status';
    return {
      title: `Status updated: ${statusTarget}`,
      detail: fromState && toState
        ? `Moved from ${getStateLabel(fromState)} to ${getStateLabel(toState)}.`
        : (reasonText || getIssueSummary(item)),
    };
  }
  if (eventType === 'erp_api_failed' || eventType === 'erp_browser_fallback_failed') {
    return {
      title: 'ERP posting failed',
      detail: reasonText || 'Posting did not complete. Retry or escalate for review.',
    };
  }
  if (eventType === 'erp_api_success' || eventType === 'erp_browser_fallback_completed') {
    return {
      title: 'Posted to ERP',
      detail: reasonText || 'Invoice posting completed successfully.',
    };
  }
  return null;
}

function resolveOperatorAuditPresentation(event) {
  const operator = event && typeof event.operator === 'object' ? event.operator : {};
  const title = String(event?.operator_title || operator?.title || '').trim();
  const detail = String(event?.operator_message || operator?.message || '').trim();
  if (!title && !detail) return null;
  return {
    title: title || prettifyEventType(event?.event_type || event?.eventType || 'event'),
    detail: formatAuditReasonText(detail),
  };
}

function getWorkAuditPresentation(event, item) {
  const operatorPresentation = resolveOperatorAuditPresentation(event);
  const fallback = getWorkAuditFallbackPresentation(event, item);
  if (operatorPresentation) {
    const eventTypeRaw = normalizeAuditEventType(event?.event_type || event?.eventType || '');
    const operatorTitle = String(operatorPresentation.title || '').trim().toLowerCase();
    const rawTitle = prettifyEventType(eventTypeRaw).trim().toLowerCase();
    const operatorLooksRaw = Boolean(operatorTitle && rawTitle && operatorTitle === rawTitle);
    if (fallback && operatorLooksRaw) return fallback;
    if (fallback && !operatorPresentation.detail) {
      return { title: operatorPresentation.title, detail: fallback.detail };
    }
    return operatorPresentation;
  }
  if (fallback) return fallback;

  const payload = getAuditEventPayload(event);
  const eventType = normalizeAuditEventType(event?.event_type || event?.eventType || '');
  const fromState = String(event?.from_state || payload?.from_state || payload?.fromState || '').trim();
  const toState = String(event?.to_state || payload?.to_state || payload?.toState || '').trim();
  const reason = formatAuditReasonText(String(
    event?.decision_reason
    || event?.reason
    || payload?.reason
    || payload?.error_message_redacted
    || payload?.error_message
    || ''
  ).trim());

  if (eventType === 'state_transition') {
    const statusTarget = toState ? getStateLabel(toState) : 'new status';
    return {
      title: `Status updated: ${statusTarget}`,
      detail: fromState && toState
        ? `Moved from ${getStateLabel(fromState)} to ${getStateLabel(toState)}.`
        : (reason || getIssueSummary(item)),
    };
  }

  if (eventType.includes('failed') || eventType.includes('rejected') || eventType.includes('error')) {
    return {
      title: 'Could not complete action',
      detail: reason || '',
    };
  }

  if (eventType.includes('state_transition')) {
    return {
      title: 'Status updated',
      detail: reason || getIssueSummary(item),
    };
  }

  return {
    title: 'Action recorded',
    detail: reason,
  };
}

function describeAgentEvent(event) {
  const status = String(event?.status || '');
  const tool = String(event?.tool_name || '').toLowerCase();
  if (status === 'completed') {
    if (tool === 'read_page') {
      return 'Source page was analyzed for invoice details and validation evidence.';
    }
    if (tool === 'extract_table') return 'Structured table rows were extracted for invoice field matching.';
    if (tool === 'find_element') return 'Relevant UI element was discovered for the current task.';
    if (tool === 'capture_evidence') return 'Audit evidence was captured for this step.';
    return '';
  }

  if (status !== 'failed') return '';
  const payload = event?.result_payload || event?.resultPayload || {};
  const errorCode = String(payload?.error || 'execution_failed');
  if (errorCode.includes('runtime_message_failed') || errorCode.includes('runtime_message_timeout') || errorCode.includes('runtime_unavailable')) {
    return 'Extension bridge was reconnecting; auto-retry will run.';
  }
  if (errorCode === 'execution_failed') {
    return 'Browser command did not return a result; auto-retry will run.';
  }
  return `Error: ${errorCode}`;
}

function getAgentEventTimestamp(event) {
  const raw = event?.updated_at || event?.updatedAt || event?.created_at || event?.createdAt || null;
  if (!raw) return 0;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

function summarizeAgentEvents(events, limit = 5) {
  if (!Array.isArray(events) || events.length === 0) {
    return { events: [], recoveredFailures: 0 };
  }

  const latestCompletedByTool = new Map();
  events.forEach((event) => {
    if (String(event?.status || '') !== 'completed') return;
    const tool = String(event?.tool_name || '').toLowerCase();
    if (!tool) return;
    const ts = getAgentEventTimestamp(event);
    const previous = latestCompletedByTool.get(tool) || 0;
    if (ts >= previous) {
      latestCompletedByTool.set(tool, ts);
    }
  });

  const filtered = [...events].reverse().filter((event) => {
    if (String(event?.status || '') !== 'failed') return true;
    const tool = String(event?.tool_name || '').toLowerCase();
    if (!tool) return true;
    const completedTs = latestCompletedByTool.get(tool);
    if (!completedTs) return true;
    return getAgentEventTimestamp(event) >= completedTs;
  });

  return {
    events: filtered.slice(0, limit).reverse(),
    recoveredFailures: Math.max(0, events.length - filtered.length)
  };
}

function parseJsonObject(value) {
  if (!value) return null;
  if (typeof value === 'object') return value;
  try {
    const parsed = JSON.parse(String(value));
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch (_) {
    return null;
  }
}

function getAuditEventPayload(event) {
  return parseJsonObject(event?.payload_json || event?.payloadJson || event?.payload) || {};
}

function getAuditEventTimestamp(event) {
  const raw = event?.ts || event?.created_at || event?.createdAt || event?.updated_at || event?.updatedAt || null;
  if (!raw) return 0;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

function humanizeSnakeText(value) {
  return String(value || '')
    .replace(/_/g, ' ')
    .trim()
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function getBrowserFallbackStageMeta(eventType) {
  const normalized = String(eventType || '').trim().toLowerCase();
  const total = 5;
  if (normalized === 'erp_api_failed') {
    return { index: 1, total, label: 'API post failed', key: 'api_failed' };
  }
  if (normalized === 'erp_api_fallback_preview_created') {
    return { index: 2, total, label: 'Fallback preview ready', key: 'preview' };
  }
  if (normalized === 'erp_api_fallback_confirmation_captured') {
    return { index: 3, total, label: 'Confirmation captured', key: 'confirmation' };
  }
  if (normalized === 'erp_api_fallback_requested') {
    return { index: 4, total, label: 'Runner executing', key: 'runner' };
  }
  if (normalized === 'erp_browser_fallback_completed') {
    return { index: 5, total, label: 'Result reconciled (success)', key: 'reconciled_success' };
  }
  if (normalized === 'erp_browser_fallback_failed') {
    return { index: 5, total, label: 'Result reconciled (failed)', key: 'reconciled_failed' };
  }
  return null;
}

function getBrowserFallbackAuditPresentation(event) {
  const eventType = String(event?.event_type || event?.eventType || '').toLowerCase();
  if (!eventType) return null;
  const payload = getAuditEventPayload(event);
  const stage = getBrowserFallbackStageMeta(eventType);

  if (eventType === 'erp_api_failed') {
    const fallback = payload?.fallback && typeof payload.fallback === 'object' ? payload.fallback : {};
    const fallbackRequested = Boolean(fallback?.requested);
    const fallbackEligible = fallback?.eligible;
    const apiReason = String(payload?.api_reason || payload?.reason || event?.decision_reason || event?.reason || '').trim();
    const fallbackReason = String(fallback?.control_reason || fallback?.reason || '').trim();
    const detailParts = [];
    if (apiReason) detailParts.push(`API failure: ${apiReason.replace(/_/g, ' ')}`);
    if (fallbackReason) {
      detailParts.push(
        fallbackEligible === false
          ? `Fallback unavailable: ${fallbackReason.replace(/_/g, ' ')}`
          : `Fallback: ${fallbackReason.replace(/_/g, ' ')}`
      );
    }
    return {
      kind: 'browser_fallback',
      bucket: 'blocked',
      stage,
      title: fallbackRequested
        ? 'ERP API post failed; browser fallback required'
        : fallbackEligible === false
          ? 'ERP API post failed; browser fallback unavailable'
          : 'ERP API post failed',
      status: fallbackRequested ? 'API failed' : 'Blocked',
      detail: detailParts.join(' · '),
    };
  }

  if (eventType === 'erp_api_fallback_preview_created') {
    const commandCount = Number(payload?.command_count || 0);
    const confirmCount = Number(payload?.requires_confirmation_count || 0);
    const detailParts = [];
    if (Number.isFinite(commandCount) && commandCount > 0) detailParts.push(`${commandCount} command${commandCount === 1 ? '' : 's'} prepared`);
    if (Number.isFinite(confirmCount)) detailParts.push(`${confirmCount} confirmation${confirmCount === 1 ? '' : 's'} required`);
    return {
      kind: 'browser_fallback',
      bucket: 'executing',
      stage,
      title: 'Browser fallback preview generated',
      status: 'Preview ready',
      detail: detailParts.join(' · '),
    };
  }

  if (eventType === 'erp_api_fallback_confirmation_captured') {
    const requiredCount = Number(payload?.required_count || payload?.requires_confirmation_count || 0);
    const confirmedCount = Number(payload?.confirmed_count || 0);
    const pendingCount = Math.max(0, requiredCount - confirmedCount);
    return {
      kind: 'browser_fallback',
      bucket: pendingCount > 0 ? 'blocked' : 'executing',
      stage,
      title: 'Browser fallback confirmation captured',
      status: pendingCount > 0 ? 'Awaiting approval' : 'Confirmed',
      detail: `${confirmedCount}/${requiredCount} confirmations captured${pendingCount > 0 ? ` · ${pendingCount} pending` : ''}`,
    };
  }

  if (eventType === 'erp_api_fallback_requested') {
    const fallback = payload?.fallback && typeof payload.fallback === 'object' ? payload.fallback : {};
    const queued = Number(fallback?.queued || 0);
    const blocked = Number(fallback?.blocked || 0);
    const denied = Number(fallback?.denied || 0);
    const parts = [];
    if (Number.isFinite(queued)) parts.push(`${queued} queued`);
    if (Number.isFinite(blocked) && blocked > 0) parts.push(`${blocked} awaiting approval`);
    if (Number.isFinite(denied) && denied > 0) parts.push(`${denied} denied`);
    if (fallback?.dispatch_status) parts.push(`dispatch ${String(fallback.dispatch_status).replace(/_/g, ' ')}`);
    return {
      kind: 'browser_fallback',
      bucket: blocked > 0 ? 'awaiting_approval' : 'executing',
      stage,
      title: 'Browser fallback runner executing',
      status: blocked > 0 ? 'Awaiting approval' : 'Runner queued',
      detail: parts.join(' · '),
    };
  }

  if (eventType === 'erp_browser_fallback_completed') {
    const erpReference = String(payload?.erp_reference || '').trim();
    const evidence = payload?.evidence && typeof payload.evidence === 'object' ? payload.evidence : {};
    const evidenceKeys = Object.keys(evidence).filter(Boolean);
    return {
      kind: 'browser_fallback',
      bucket: 'completed',
      stage,
      title: 'Browser fallback completed (result reconciled)',
      status: 'Completed',
      detail: [
        erpReference ? `ERP ref ${erpReference}` : '',
        evidenceKeys.length ? `Evidence: ${trimText(evidenceKeys.join(', '), 60)}` : '',
      ].filter(Boolean).join(' · '),
    };
  }

  if (eventType === 'erp_browser_fallback_failed') {
    const errorCode = String(payload?.error_code || '').trim();
    const errorMsg = String(payload?.error_message_redacted || '').trim();
    return {
      kind: 'browser_fallback',
      bucket: 'blocked',
      stage,
      title: 'Browser fallback failed (result reconciled)',
      status: 'Failed',
      detail: [
        errorCode ? `Code: ${errorCode}` : '',
        errorMsg || String(event?.decision_reason || event?.reason || '').trim(),
      ].filter(Boolean).join(' · '),
    };
  }

  return null;
}

function buildBrowserFallbackStatusSummary(item, contextPayload, auditEvents) {
  const events = Array.isArray(auditEvents) ? auditEvents : [];
  const fallbackEvents = events
    .map((event) => ({
      event,
      presentation: getBrowserFallbackAuditPresentation(event),
      ts: getAuditEventTimestamp(event),
    }))
    .filter((entry) => entry.presentation)
    .sort((a, b) => (b.ts || 0) - (a.ts || 0));

  if (!fallbackEvents.length) return null;

  const latest = fallbackEvents[0];
  const latestEvent = latest.event;
  const presentation = latest.presentation || {};
  const latestStage = presentation.stage || getBrowserFallbackStageMeta(latestEvent?.event_type || latestEvent?.eventType) || null;
  const payload = getAuditEventPayload(latestEvent);
  const itemState = String(item?.state || '').trim().toLowerCase();
  const erp = contextPayload?.erp || {};
  const erpReference = String(
    payload?.erp_reference
    || (payload?.fallback && typeof payload.fallback === 'object' ? payload.fallback.erp_reference : '')
    || erp?.erp_reference
    || item?.erp_reference
    || ''
  ).trim();
  const fallback = payload?.fallback && typeof payload.fallback === 'object' ? payload.fallback : {};
  const sessionId = String(payload?.session_id || fallback?.session_id || '').trim();
  const errorCode = String(payload?.error_code || '').trim();
  const errorMsg = String(payload?.error_message_redacted || '').trim();
  const apiReason = String(payload?.api_reason || '').trim();
  const fallbackReason = String(fallback?.control_reason || fallback?.reason || '').trim();
  const stateLabel = getStateLabel(itemState || 'received');
  const timeLabel = formatTimestamp(latestEvent?.ts || latestEvent?.created_at || latestEvent?.createdAt);
  const reachedStageLabels = [];
  const seenStageKeys = new Set();
  for (const entry of fallbackEvents) {
    const stage = entry.presentation?.stage;
    if (!stage || !stage.key || seenStageKeys.has(stage.key)) continue;
    seenStageKeys.add(stage.key);
    reachedStageLabels.push({ key: stage.key, index: stage.index, total: stage.total, label: stage.label });
  }
  reachedStageLabels.sort((a, b) => (a.index || 0) - (b.index || 0));
  const meta = [
    `AP state: ${stateLabel}`,
    sessionId ? `Session: ${trimText(sessionId, 28)}` : '',
    erpReference ? `ERP ref: ${erpReference}` : '',
    timeLabel ? `Updated ${timeLabel}` : '',
  ].filter(Boolean);

  const detailParts = [
    presentation.detail || '',
    apiReason && !String(presentation.detail || '').toLowerCase().includes(String(apiReason).toLowerCase())
      ? `API reason: ${apiReason.replace(/_/g, ' ')}`
      : '',
    fallbackReason && !String(presentation.detail || '').toLowerCase().includes(String(fallbackReason).toLowerCase())
      ? `Fallback reason: ${fallbackReason.replace(/_/g, ' ')}`
      : '',
    errorCode ? `Error code: ${errorCode}` : '',
    errorMsg || '',
  ].filter(Boolean);

  let tone = 'info';
  const latestType = String(latestEvent?.event_type || latestEvent?.eventType || '').toLowerCase();
  if (latestType === 'erp_browser_fallback_completed') tone = 'success';
  else if (latestType === 'erp_browser_fallback_failed' || latestType === 'erp_api_failed') tone = 'error';
  else if (latestType === 'erp_api_fallback_confirmation_captured' && String(presentation.status || '').toLowerCase().includes('awaiting')) tone = 'warning';
  else if (itemState === 'failed_post') tone = 'warning';

  let trustNote = '';
  if (latestType === 'erp_browser_fallback_completed') {
    trustNote = 'Runner completion is reconciled and the AP item is updated to posted_to_erp.';
  } else if (latestType === 'erp_browser_fallback_failed') {
    trustNote = 'Runner failure is reconciled and the invoice remains in failed_post for retry or review.';
  } else if (latestType === 'erp_api_fallback_requested') {
    trustNote = 'Fallback is in progress. Clearledgr will not mark posting done until the runner completion callback is reconciled.';
  } else if (latestType === 'erp_api_fallback_confirmation_captured') {
    trustNote = String(presentation.status || '').toLowerCase().includes('awaiting')
      ? 'Fallback is paused pending required command approval.'
      : 'Fallback confirmation was captured and commands can execute.';
  } else if (latestType === 'erp_api_fallback_preview_created') {
    trustNote = 'Fallback plan is prepared for review before browser execution.';
  } else if (latestType === 'erp_api_failed') {
    trustNote = String(fallback?.requested || '').toLowerCase() === 'true'
      ? 'API posting failed and fallback is required before this invoice can be marked posted.'
      : 'API posting failed. Browser fallback did not start for this attempt.';
  }

  return {
    kind: 'browser_fallback_status',
    tone,
    title: presentation.title || 'Browser fallback status',
    stage: presentation.status || 'Status update',
    stageLabel: latestStage?.label || '',
    stageIndex: latestStage?.index || null,
    stageTotal: latestStage?.total || null,
    detail: detailParts.join(' · '),
    meta,
    trustNote,
    reachedStages: reachedStageLabels,
  };
}

function renderBrowserFallbackStatusBannerHtml(summary) {
  if (!summary || typeof summary !== 'object') return '';
  const meta = Array.isArray(summary.meta) ? summary.meta : [];
  const reachedStages = Array.isArray(summary.reachedStages) ? summary.reachedStages : [];
  const progressLabel = Number.isFinite(Number(summary.stageIndex)) && Number.isFinite(Number(summary.stageTotal))
    ? `Stage ${Number(summary.stageIndex)} of ${Number(summary.stageTotal)}`
    : '';
  return `
    <div class="cl-fallback-banner" data-tone="${escapeHtml(String(summary.tone || 'info'))}">
      <div class="cl-fallback-header">
        <span class="cl-fallback-badge">Browser fallback</span>
        <span class="cl-fallback-stage">${escapeHtml(String(summary.stage || 'Status update'))}</span>
      </div>
      ${
        progressLabel || summary.stageLabel
          ? `<div class="cl-fallback-progress">${escapeHtml([progressLabel, String(summary.stageLabel || '').trim()].filter(Boolean).join(' · '))}</div>`
          : ''
      }
      <div class="cl-fallback-title">${escapeHtml(String(summary.title || 'Browser fallback status'))}</div>
      ${summary.detail ? `<div class="cl-fallback-detail">${escapeHtml(String(summary.detail))}</div>` : ''}
      ${summary.trustNote ? `<div class="cl-fallback-trust-note">${escapeHtml(String(summary.trustNote))}</div>` : ''}
      ${
        reachedStages.length
          ? `<div class="cl-fallback-stage-list">${reachedStages.map((stage) => `<span class="cl-fallback-stage-chip">${escapeHtml(`S${stage.index}`)} ${escapeHtml(String(stage.label || ''))}</span>`).join('')}</div>`
          : ''
      }
      ${meta.length ? `<div class="cl-fallback-meta">${meta.map((value) => `<span>${escapeHtml(String(value))}</span>`).join('')}</div>` : ''}
    </div>
  `;
}

function describeBrowserContextEvent(event) {
  const statusRaw = String(event?.status || '').trim().toLowerCase();
  const status = (statusRaw || 'unknown')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
  const result = event?.result && typeof event.result === 'object' ? event.result : {};
  const title = getAgentToolLabel(event?.tool_name || 'browser_action') || 'Browser action';
  const detail = trimText(
    result?.summary
    || result?.error_message_redacted
    || result?.error
    || result?.message
    || (result?.erp_reference ? `ERP ref ${result.erp_reference}` : '')
    || '',
    110
  );
  const detailText = String(detail || '').toLowerCase();
  const fallbackRelated = Boolean(
    result?.erp_reference
    || detailText.includes('fallback')
    || detailText.includes('erp posting')
    || detailText.includes('erp portal')
  );
  const statusTone = statusRaw === 'failed' ? 'error' : statusRaw === 'completed' ? 'success' : 'info';
  return {
    title,
    status,
    detail,
    timeLabel: formatTimestamp(event?.ts),
    fallbackRelated,
    statusTone,
  };
}

function classifyTimelineBucketFromState(state) {
  const normalized = String(state || '').toLowerCase();
  if (!normalized) return 'completed';
  if (normalized === 'received') return 'planned';
  if (normalized === 'needs_approval') return 'awaiting_approval';
  if (normalized === 'needs_info' || normalized === 'failed_post' || normalized === 'rejected') return 'blocked';
  if (normalized === 'validated' || normalized === 'approved' || normalized === 'ready_to_post') return 'executing';
  if (normalized === 'posted_to_erp' || normalized === 'closed') return 'completed';
  return 'completed';
}

function classifyAgentTimelineBucket(event) {
  const status = String(event?.status || '').toLowerCase();
  if (status === 'blocked_for_approval') return 'awaiting_approval';
  if (status === 'queued' || status === 'preview') return 'planned';
  if (status === 'running' || status === 'submitted') return 'executing';
  if (status === 'failed' || status === 'denied') return 'blocked';
  if (status === 'completed') return 'completed';
  return 'executing';
}

function classifyAuditTimelineBucket(event) {
  const eventType = String(event?.event_type || event?.eventType || '').toLowerCase();
  const fallbackPresentation = getBrowserFallbackAuditPresentation(event);
  if (fallbackPresentation?.bucket) {
    return fallbackPresentation.bucket;
  }
  const payload = getAuditEventPayload(event);
  const toState = event?.to_state || payload?.to_state || payload?.toState;
  if (toState) {
    return classifyTimelineBucketFromState(toState);
  }

  if (eventType.includes('unauthorized') || eventType.includes('invalid') || eventType.includes('stale') || eventType.includes('failed')) {
    return 'blocked';
  }
  if (eventType.includes('approval') || eventType.includes('channel_action')) {
    return eventType.includes('processed') || eventType.includes('approved') || eventType.includes('rejected')
      ? 'completed'
      : 'awaiting_approval';
  }
  if (eventType.includes('preview') || eventType.includes('confirmation') || eventType.includes('dispatch') || eventType.includes('retry')) {
    return 'executing';
  }
  if (eventType.includes('posted') || eventType.includes('completed') || eventType.includes('success')) {
    return 'completed';
  }
  return 'completed';
}

function buildAgentTimelineEntries(agentEvents, auditEvents, options = {}) {
  const maxEntries = Number(options.maxEntries || 14);
  const entries = [];

  (Array.isArray(agentEvents) ? agentEvents : []).forEach((event, index) => {
    const requestPayload = event?.request_payload || event?.requestPayload || {};
    const title = trimText(
      requestPayload?.step
      || getAgentToolLabel(event?.tool_name || requestPayload?.tool_name || 'agent action'),
      72
    );
    const status = String(event?.status || 'queued').replace(/_/g, ' ');
    const detail = trimText(
      describeAgentEvent(event)
      || requestPayload?.detail
      || requestPayload?.summary
      || event?.result_payload?.summary
      || event?.resultPayload?.summary
      || '',
      120
    );
    entries.push({
      key: `agent:${event?.command_id || index}:${event?.status || 'unknown'}`,
      source: 'Agent',
      bucket: classifyAgentTimelineBucket(event),
      title,
      status,
      detail,
      ts: getAgentEventTimestamp(event),
      timeLabel: formatTimestamp(event?.updated_at || event?.updatedAt || event?.created_at || event?.createdAt),
      sortOrder: 0
    });
  });

  (Array.isArray(auditEvents) ? auditEvents : []).forEach((event, index) => {
    const payload = getAuditEventPayload(event);
    const fallbackPresentation = getBrowserFallbackAuditPresentation(event);
    const eventTypeRaw = String(event?.event_type || event?.eventType || '').toLowerCase();
    const fromState = String(event?.from_state || payload?.from_state || payload?.fromState || '').trim();
    const toState = String(event?.to_state || payload?.to_state || payload?.toState || '').trim();
    const title = fallbackPresentation?.title || ((fromState || toState)
      ? `State: ${getStateLabel(fromState || 'received')} -> ${getStateLabel(toState || fromState || 'received')}`
      : prettifyEventType(event?.event_type || event?.eventType || 'audit_event'));
    const detail = trimText(
      fallbackPresentation?.detail
      || event?.decision_reason
      || event?.reason
      || payload?.reason
      || payload?.error_message
      || payload?.error_message_redacted
      || payload?.status
      || '',
      120
    );
    const statusLabel = String(fallbackPresentation?.status || (toState || eventTypeRaw || 'audit'))
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (ch) => ch.toUpperCase());

    entries.push({
      key: `audit:${event?.id || index}:${eventTypeRaw}`,
      source: 'Audit',
      bucket: classifyAuditTimelineBucket(event),
      kind: fallbackPresentation?.kind || '',
      stage: fallbackPresentation?.stage || null,
      title: trimText(title, 72),
      status: trimText(statusLabel, 36),
      detail,
      ts: getAuditEventTimestamp(event),
      timeLabel: formatTimestamp(event?.ts || event?.created_at || event?.createdAt),
      sortOrder: 1
    });
  });

  return entries
    .sort((a, b) => {
      if ((b.ts || 0) !== (a.ts || 0)) return (b.ts || 0) - (a.ts || 0);
      return (a.sortOrder || 0) - (b.sortOrder || 0);
    })
    .slice(0, maxEntries);
}

function renderAgentTimelineGroups(entries, options = {}) {
  const auditLoading = Boolean(options.auditLoading);
  const buckets = [
    { id: 'blocked', label: 'Blocked / failed' },
    { id: 'awaiting_approval', label: 'Awaiting approval' },
    { id: 'executing', label: 'Executing' },
    { id: 'planned', label: 'Planned' },
    { id: 'completed', label: 'Completed' }
  ];

  const grouped = new Map();
  buckets.forEach((bucket) => grouped.set(bucket.id, []));
  (Array.isArray(entries) ? entries : []).forEach((entry) => {
    const list = grouped.get(entry.bucket) || grouped.get('completed');
    list.push(entry);
  });

  const groupsHtml = buckets
    .map((bucket) => {
      const items = grouped.get(bucket.id) || [];
      if (!items.length) return '';
      const rows = items.slice(0, 3).map((entry) => `
        <div class="cl-agent-row cl-agent-row-timeline" data-source="${escapeHtml(entry.source.toLowerCase())}" ${entry.kind ? `data-kind="${escapeHtml(String(entry.kind))}"` : ''}>
          <div class="cl-agent-row-main">
            <span class="cl-agent-tool">${escapeHtml(entry.title || entry.source)}</span>
            ${
              entry.stage?.index && entry.stage?.total
                ? `<span class="cl-agent-stage-chip">S${escapeHtml(String(entry.stage.index))}/${escapeHtml(String(entry.stage.total))}</span>`
                : ''
            }
            <span class="cl-agent-status">${escapeHtml(entry.status || bucket.label)}</span>
          </div>
          <div class="cl-agent-timeline-meta">
            <span class="cl-agent-source">${escapeHtml(entry.source)}</span>
            ${entry.timeLabel ? `<span class="cl-agent-time">${escapeHtml(entry.timeLabel)}</span>` : ''}
          </div>
          ${entry.detail ? `<div class="cl-agent-detail">${escapeHtml(entry.detail)}</div>` : ''}
        </div>
      `).join('');
      return `
        <div class="cl-agent-group">
          <div class="cl-agent-group-title">${escapeHtml(bucket.label)}</div>
          <div class="cl-agent-list">${rows}</div>
        </div>
      `;
    })
    .join('');

  if (groupsHtml) return groupsHtml;
  if (auditLoading) {
    return '<div class="cl-agent-timeline-empty">Loading timeline breadcrumbs…</div>';
  }
  return '<div class="cl-agent-timeline-empty">No agent timeline events yet.</div>';
}

function getAgentToolLabel(toolName) {
  const tool = String(toolName || '').toLowerCase();
  if (tool === 'read_page') return 'Read source email';
  if (tool === 'extract_table') return 'Extract table';
  if (tool === 'find_element') return 'Find page element';
  if (tool === 'capture_evidence') return 'Capture evidence';
  return String(toolName || '').replace(/_/g, ' ');
}

function showToast(message, tone = 'info') {
  const hosts = [workSidebarEl, globalSidebarEl].filter(Boolean);
  if (!hosts.length) return;
  const rendered = new Set();
  hosts.forEach((host) => {
    if (!host || rendered.has(host)) return;
    rendered.add(host);
    const toast = host.querySelector('#cl-toast');
    if (!toast) return;
    toast.textContent = message;
    toast.dataset.tone = tone;
    toast.style.display = 'block';
  });
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    rendered.forEach((host) => {
      const toast = host.querySelector('#cl-toast');
      if (toast) toast.style.display = 'none';
    });
  }, 3000);
}

function getReasonSheetDefaults(actionType = 'generic') {
  const normalized = String(actionType || '').trim().toLowerCase();
  if (normalized === 'reject' || normalized === 'budget_reject') {
    return {
      chips: ['Duplicate invoice', 'Incorrect amount', 'Missing required docs', 'Out of policy'],
      required: true,
    };
  }
  if (normalized === 'approve_override' || normalized === 'budget_override') {
    return {
      chips: ['Reviewed with approver', 'Urgent vendor payment', 'Policy exception approved', 'Business critical'],
      required: true,
    };
  }
  if (normalized === 'budget_adjustment') {
    return {
      chips: ['Threshold update needed', 'Seasonal spend spike', 'Project budget exception', 'One-off adjustment'],
      required: false,
    };
  }
  if (normalized === 'approval_route' || normalized === 'approval_nudge') {
    return {
      chips: ['Approver unavailable', 'SLA at risk', 'Waiting on budget owner', 'Escalation requested'],
      required: false,
    };
  }
  return {
    chips: ['Reviewed', 'Needs follow-up', 'Policy requirement', 'Other'],
    required: true,
  };
}

function getReasonSheetHost() {
  return workSidebarEl || globalSidebarEl;
}

function requestActionInput({
  title = 'Add context',
  label = 'Reason',
  placeholder = '',
  defaultValue = '',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  required = true,
  actionType = 'generic',
  chips = null,
} = {}) {
  const sidebarHost = getReasonSheetHost();
  if (!sidebarHost) return Promise.resolve(null);
  const host = sidebarHost.querySelector('#cl-action-dialog');
  if (!host) return Promise.resolve(null);

  const titleEl = host.querySelector('.cl-action-dialog-title');
  const labelEl = host.querySelector('.cl-action-dialog-label');
  const inputEl = host.querySelector('.cl-action-dialog-input');
  const chipsEl = host.querySelector('.cl-action-dialog-chips');
  const hintEl = host.querySelector('.cl-action-dialog-hint');
  const cardEl = host.querySelector('.cl-action-dialog-card');
  const cancelEl = host.querySelector('.cl-action-dialog-cancel');
  const confirmEl = host.querySelector('.cl-action-dialog-confirm');

  if (!titleEl || !labelEl || !inputEl || !cancelEl || !confirmEl) {
    return Promise.resolve(null);
  }

  const defaults = getReasonSheetDefaults(actionType);
  const chipList = Array.isArray(chips) && chips.length ? chips : defaults.chips;
  const isRequired = required !== undefined ? Boolean(required) : Boolean(defaults.required);

  titleEl.textContent = title;
  titleEl.id = 'cl-action-dialog-title';
  if (cardEl) {
    cardEl.setAttribute('aria-labelledby', 'cl-action-dialog-title');
  }
  labelEl.textContent = label;
  labelEl.id = 'cl-action-dialog-label';
  inputEl.setAttribute('aria-labelledby', 'cl-action-dialog-label');
  inputEl.value = String(defaultValue || '');
  inputEl.placeholder = String(placeholder || '');
  cancelEl.textContent = cancelLabel;
  confirmEl.textContent = confirmLabel;
  if (hintEl) {
    hintEl.textContent = isRequired
      ? 'A reason is required for this action.'
      : 'Optional note. Choose a quick reason or write your own.';
  }
  if (chipsEl) {
    chipsEl.innerHTML = (chipList || [])
      .map((chip) => `<button type="button" class="cl-action-chip" data-reason-chip="${escapeHtml(chip)}">${escapeHtml(chip)}</button>`)
      .join('');
  }
  host.style.display = 'flex';
  host.setAttribute('aria-hidden', 'false');
  const activeElement = document?.activeElement || null;
  const previousFocus = (typeof HTMLElement !== 'undefined' && activeElement instanceof HTMLElement)
    ? activeElement
    : (activeElement && typeof activeElement.focus === 'function' ? activeElement : null);

  return new Promise((resolve) => {
    let done = false;
    const chipButtons = chipsEl ? Array.from(chipsEl.querySelectorAll('.cl-action-chip')) : [];
    const getFocusableNodes = () => {
      const nodes = [inputEl, ...chipButtons, cancelEl, confirmEl];
      return nodes.filter((node) => node && !node.disabled && node.getAttribute('aria-hidden') !== 'true');
    };
    const cleanup = () => {
      cancelEl.removeEventListener('click', onCancel);
      confirmEl.removeEventListener('click', onConfirm);
      inputEl.removeEventListener('keydown', onKeyDown);
      host.removeEventListener('keydown', onDialogKeyDown);
      host.removeEventListener('click', onBackdropClick);
      chipButtons.forEach((button) => button.removeEventListener('click', onChip));
      host.style.display = 'none';
      host.setAttribute('aria-hidden', 'true');
      inputEl.value = '';
      if (chipsEl) chipsEl.innerHTML = '';
      if (previousFocus && typeof previousFocus.focus === 'function') {
        setTimeout(() => {
          try {
            previousFocus.focus();
          } catch (_) {
            // no-op
          }
        }, 0);
      }
    };
    const finish = (value) => {
      if (done) return;
      done = true;
      cleanup();
      resolve(value);
    };
    const onCancel = () => finish(null);
    const onConfirm = () => {
      const value = String(inputEl.value || '').trim();
      if (isRequired && !value) {
        showToast(`${label} is required`, 'error');
        inputEl.focus();
        return;
      }
      finish(value);
    };
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCancel();
      } else if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        onConfirm();
      }
    };
    const onBackdropClick = (event) => {
      if (event.target === host) onCancel();
    };
    const onDialogKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = getFocusableNodes();
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey) {
        if (active === first || !focusable.includes(active)) {
          event.preventDefault();
          last.focus();
        }
      } else if (active === last || !focusable.includes(active)) {
        event.preventDefault();
        first.focus();
      }
    };
    const onChip = (event) => {
      const chipValue = String(event?.currentTarget?.getAttribute('data-reason-chip') || '').trim();
      if (!chipValue) return;
      const existing = String(inputEl.value || '').trim();
      inputEl.value = existing ? `${existing}; ${chipValue}` : chipValue;
      inputEl.focus();
      inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
    };
    cancelEl.addEventListener('click', onCancel);
    confirmEl.addEventListener('click', onConfirm);
    inputEl.addEventListener('keydown', onKeyDown);
    host.addEventListener('keydown', onDialogKeyDown);
    host.addEventListener('click', onBackdropClick);
    chipButtons.forEach((button) => button.addEventListener('click', onChip));
    setTimeout(() => inputEl.focus(), 0);
  });
}

function openReasonSheet(actionType = 'generic', options = {}) {
  return requestActionInput({
    actionType,
    ...options,
  });
}

function findItemByThreadId(threadId) {
  if (!threadId) return null;
  return queueState.find((item) => item.thread_id === threadId || item.threadId === threadId) || null;
}

function findItemById(itemId) {
  if (!itemId) return null;
  return queueState.find((item) => item.id === itemId || item.invoice_key === itemId) || null;
}

function getPrimaryItem() {
  const selectedItem = findItemById(selectedItemId);
  if (selectedItem) return selectedItem;

  const threadItem = findItemByThreadId(currentThreadId);
  if (threadItem) return threadItem;

  if (!Array.isArray(queueState) || queueState.length === 0) return null;
  return queueState[0];
}

function getPrimaryItemIndex() {
  const item = getPrimaryItem();
  if (!item || !Array.isArray(queueState)) return -1;
  return queueState.findIndex((entry) => (entry.id || entry.invoice_key) === (item.id || item.invoice_key));
}

function selectItemByOffset(offset) {
  if (!Array.isArray(queueState) || queueState.length === 0) return;
  const currentIndex = getPrimaryItemIndex();
  const safeCurrent = currentIndex >= 0 ? currentIndex : 0;
  const nextIndex = Math.max(0, Math.min(queueState.length - 1, safeCurrent + offset));
  const nextItem = queueState[nextIndex];
  if (!nextItem) return;
  selectedItemId = nextItem.id || nextItem.invoice_key || null;
  writeLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID, selectedItemId);
  activeContextTab = 'email';
  auditState = { itemId: null, loading: false, events: [] };
  contextUiState = { itemId: null, loading: false, error: '' };
  renderAllSidebars();
}

function getIssueSummary(item) {
  const exceptionCode = String(item?.exception_code || '').trim().toLowerCase();
  if (exceptionCode === 'po_missing_reference') return 'PO reference is required before processing';
  if (exceptionCode === 'po_amount_mismatch') return 'Invoice amount does not match PO amount';
  if (exceptionCode === 'receipt_missing') return 'Receipt confirmation is required';
  if (exceptionCode === 'budget_overrun') return 'Invoice exceeds available budget';
  if (exceptionCode === 'missing_budget_context') return 'Budget context is missing for this invoice';
  if (exceptionCode === 'policy_validation_failed') return 'Invoice violated AP policy checks';

  const state = String(item?.state || '');
  if (state === 'needs_info') return 'Missing required invoice fields';
  if (state === 'needs_approval') return 'Pending human approval';
  if (state === 'failed_post') return 'ERP posting failed and needs retry';
  if (state === 'approved') return 'Approved and waiting for ERP posting';
  if (state === 'ready_to_post') return 'Ready to post to ERP';
  if (state === 'posted_to_erp' || state === 'closed') return 'Posted successfully';
  return 'Under AP review';
}

function getExceptionReason(exceptionCode) {
  const code = String(exceptionCode || '').trim().toLowerCase();
  if (code === 'po_missing_reference') return 'PO reference required for this vendor/category';
  if (code === 'po_amount_mismatch') return 'Invoice amount does not match approved PO';
  if (code === 'receipt_missing') return 'Goods receipt confirmation pending';
  if (code === 'budget_overrun') return 'Invoice exceeds approved budget limit';
  if (code === 'missing_budget_context') return 'No budget context found for this cost center';
  if (code === 'policy_validation_failed') return 'AP policy check failed — review required';
  if (code === 'duplicate_invoice') return 'Duplicate invoice detected for this vendor';
  if (code === 'confidence_low') return 'Extraction confidence too low for auto-posting';
  return '';
}

function getDueRiskLabel(dueDateValue) {
  if (!dueDateValue) return '';
  const due = new Date(dueDateValue);
  if (Number.isNaN(due.getTime())) return '';
  const now = new Date();
  const diffDays = Math.ceil((due.getTime() - now.getTime()) / 86400000);
  if (diffDays < 0) return `Past due ${Math.abs(diffDays)}d`;
  if (diffDays === 0) return 'Due today';
  if (diffDays <= 3) return `Due in ${diffDays}d`;
  return '';
}

function getDecisionSummary(item, budgetContext) {
  const state = String(item?.state || 'received').toLowerCase();
  const exceptionCode = String(item?.exception_code || '').trim().toLowerCase();

  if (budgetContext?.requiresDecision) {
    return {
      title: 'Budget review required',
      detail: 'Choose override, budget adjustment, or rejection.',
      tone: 'warning'
    };
  }
  if (state === 'needs_info' || exceptionCode) {
    return {
      title: 'Needs review',
      detail: getIssueSummary(item),
      tone: 'warning'
    };
  }
  if (state === 'needs_approval') {
    return {
      title: 'Approval required',
      detail: 'Route to approver with full context.',
      tone: 'neutral'
    };
  }
  if (state === 'approved' || state === 'ready_to_post') {
    return {
      title: 'Ready for posting',
      detail: 'Required checks are complete.',
      tone: 'good'
    };
  }
  if (state === 'posted_to_erp' || state === 'closed') {
    return {
      title: 'Completed',
      detail: 'Invoice has already been posted.',
      tone: 'good'
    };
  }
  if (state === 'failed_post') {
    return {
      title: 'Posting failed',
      detail: 'Retry posting or escalate this invoice.',
      tone: 'warning'
    };
  }
  if (state === 'rejected') {
    return {
      title: 'Rejected',
      detail: 'No further action required unless reopened.',
      tone: 'warning'
    };
  }
  return {
    title: 'Under review',
    detail: getIssueSummary(item),
    tone: 'neutral'
  };
}

function buildOperatorDecisionBrief(item, {
  budgetContext = {},
  decisionSummary = null,
  issueSummary = '',
  apReasoning = '',
  browserFallbackStatus = null,
  metadata = null,
} = {}) {
  const state = String(item?.state || 'received').toLowerCase();
  const stateLabel = getStateLabel(state).toLowerCase();
  const nextAction = String(item?.next_action || '').trim();
  const recommendation = String(
    item?.ap_decision_recommendation
    || metadata?.ap_decision_recommendation
    || ''
  ).trim().toLowerCase();
  const followupAttemptCount = Number(
    item?.followup_attempt_count
    ?? metadata?.followup_attempt_count
    ?? 0
  ) || 0;
  const followupLastSentAt = String(
    item?.followup_last_sent_at
    || metadata?.followup_last_sent_at
    || ''
  ).trim();
  const followupNextAction = String(
    item?.followup_next_action
    || metadata?.followup_next_action
    || ''
  ).trim().toLowerCase();
  const followupSlaDueAt = String(
    item?.followup_sla_due_at
    || metadata?.followup_sla_due_at
    || ''
  ).trim();

  const whatParts = [`Invoice is currently ${stateLabel}.`];
  if (nextAction) {
    whatParts.push(`Queue next action: ${nextAction.replace(/_/g, ' ')}.`);
  }
  if (state === 'needs_info') {
    if (followupAttemptCount > 0) {
      whatParts.push(`Vendor follow-up attempts: ${followupAttemptCount}.`);
    }
    if (followupLastSentAt) {
      whatParts.push(`Last follow-up draft prepared ${formatDateTime(followupLastSentAt)}.`);
    }
  }
  if (state === 'failed_post' && browserFallbackStatus?.stage) {
    whatParts.push(`Fallback status: ${String(browserFallbackStatus.stage).toLowerCase()}.`);
  }
  if ((state === 'posted_to_erp' || state === 'closed') && item?.erp_reference) {
    whatParts.push(`ERP reference ${String(item.erp_reference)} is recorded.`);
  }

  const wantsDecisionFraming = Boolean(budgetContext?.requiresDecision) || state === 'needs_approval';
  const whyLabel = wantsDecisionFraming ? 'Why this needs your decision' : 'Why this needs attention';
  const whyParts = [];
  if (apReasoning) whyParts.push(apReasoning);
  if (decisionSummary?.detail) whyParts.push(String(decisionSummary.detail));
  if (!apReasoning && issueSummary) whyParts.push(issueSummary);
  if (recommendation && !['approve', 'approved'].includes(recommendation)) {
    whyParts.push(`Agent recommendation: ${recommendation.replace(/_/g, ' ')}.`);
  }
  const whyText = trimText(whyParts.filter(Boolean).join(' '), 220) || 'This item requires operator review before workflow progress.';

  let nextStep = 'Open Agent actions, preview the recommended operation, then run once policy checks look correct.';
  let expectedOutcome = 'The invoice workflow advances with full audit coverage.';
  let tone = decisionSummary?.tone || 'neutral';

  if (budgetContext?.requiresDecision) {
    nextStep = 'Decide budget path now: approve override only with justification, otherwise request budget adjustment.';
    expectedOutcome = 'Decision is recorded and posting remains blocked until budget path is resolved.';
    tone = 'warning';
  } else if (state === 'needs_info') {
    nextStep = 'Draft a vendor info request and collect the missing fields before attempting posting.';
    if (followupNextAction === 'nudge_vendor_followup') {
      nextStep = 'SLA window elapsed. Prepare the next vendor nudge draft and send after review.';
    } else if (followupNextAction === 'await_vendor_response') {
      nextStep = followupSlaDueAt
        ? `Wait for vendor response until ${formatDateTime(followupSlaDueAt)}, then nudge if still unanswered.`
        : 'Wait for vendor response, then nudge if still unanswered.';
    } else if (followupNextAction === 'manual_vendor_escalation') {
      nextStep = 'Follow-up attempt limit reached. Escalate to manual vendor outreach and policy review.';
    } else if (followupNextAction === 'prepare_vendor_followup_draft') {
      nextStep = 'Prepare and review the initial vendor follow-up draft before sending.';
    }
    expectedOutcome = 'Invoice returns to validated/approval flow after missing details are confirmed.';
    tone = 'warning';
  } else if (state === 'needs_approval' || state === 'pending_approval') {
    nextStep = 'Route or nudge approval in Slack/Teams, then monitor callback completion.';
    expectedOutcome = 'Once approved, the invoice moves to posting readiness automatically.';
  } else if (state === 'failed_post') {
    nextStep = 'Preview ERP retry/fallback, then run retry and confirm reconciled completion.';
    expectedOutcome = 'Invoice moves to posted_to_erp on success, or remains failed_post with explicit error evidence.';
    tone = 'warning';
  } else if (state === 'approved' || state === 'ready_to_post') {
    nextStep = 'Approve & Post now to execute API-first ERP posting with fallback controls.';
    expectedOutcome = 'Invoice should move to posted_to_erp with ERP reference and audit trail.';
  } else if (state === 'posted_to_erp' || state === 'closed') {
    nextStep = 'No action required unless you need to share context or review audit details.';
    expectedOutcome = 'Workflow remains complete and immutable in audit history.';
    tone = 'good';
  } else if (state === 'rejected') {
    nextStep = 'No action required on this item; use resubmission if a corrected invoice arrives.';
    expectedOutcome = 'Rejected state stays terminal and audit-linked.';
    tone = 'warning';
  }

  if (recommendation === 'needs_info' && state !== 'needs_info') {
    nextStep = 'Start with Request info and capture missing evidence before posting.';
  } else if (recommendation === 'reject') {
    nextStep = 'Reject only if policy/duplicate concerns are confirmed and non-recoverable.';
    expectedOutcome = 'Invoice is marked rejected and removed from posting path with reason logged.';
    tone = 'warning';
  } else if (recommendation === 'approve' && ['approved', 'ready_to_post'].includes(state)) {
    nextStep = 'Approve & Post now, then verify ERP reference in context.';
  }

  return {
    whatHappened: trimText(whatParts.filter(Boolean).join(' '), 220),
    whyLabel,
    whyText,
    nextStep,
    expectedOutcome,
    tone,
  };
}

function buildAgentBlockerSummary(item) {
  const state = String(item?.state || 'received').toLowerCase();
  const nextAction = String(item?.next_action || '').trim();
  const exceptionCode = String(item?.exception_code || '').trim();
  const confidenceBlockers = Array.isArray(item?.confidence_blockers) ? item.confidence_blockers : [];
  const lines = [];

  if (nextAction) {
    lines.push(`Next action: ${nextAction.replace(/_/g, ' ')}`);
  }

  const issue = getIssueSummary(item);
  if (issue) {
    lines.push(issue);
  }

  const exceptionReason = getExceptionReason(exceptionCode);
  if (exceptionReason) {
    lines.push(exceptionReason);
  }

  if (item?.requires_field_review && confidenceBlockers.length > 0) {
    const fields = confidenceBlockers
      .slice(0, 4)
      .map((entry) => {
        if (!entry) return '';
        if (typeof entry === 'string') return entry;
        return String(entry.field || entry.code || '').trim();
      })
      .filter(Boolean);
    if (fields.length > 0) {
      lines.push(`Field review required: ${fields.join(', ')}`);
    } else {
      lines.push('Field review required before posting');
    }
  }

  if (state === 'failed_post') {
    lines.push('ERP posting failed. Review fallback or retry actions.');
  } else if (state === 'needs_approval') {
    lines.push('Awaiting approver action in Slack or Teams.');
  } else if (state === 'needs_info') {
    lines.push('Missing or unverified invoice details need follow-up.');
  }

  const deduped = [];
  const seen = new Set();
  for (const line of lines) {
    const text = String(line || '').trim();
    if (!text) continue;
    const key = text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(text);
  }

  return {
    title: deduped.length ? 'Blocker summary' : 'No blockers detected',
    lines: deduped.length ? deduped : ['No active blocker details were found for this invoice.'],
  };
}

function buildFinanceLeadExceptionSummary(item, {
  contextPayload = null,
  auditEvents = []
} = {}) {
  const vendor = String(item?.vendor_name || item?.vendor || 'Unknown vendor').trim();
  const invoiceNumber = String(item?.invoice_number || 'N/A').trim();
  const state = String(item?.state || 'received').toLowerCase();
  const nextAction = String(item?.next_action || '').trim();
  const exceptionCode = String(item?.exception_code || '').trim();
  const issueSummary = getIssueSummary(item) || 'Review required before invoice can proceed.';
  const exceptionReason = getExceptionReason(exceptionCode);
  const dueRisk = getDueRiskLabel(item?.due_date);
  const budgetContext = normalizeBudgetContext(contextPayload || {}, item);
  const decisionSummary = getDecisionSummary(item, budgetContext);
  const contextSummary = String(contextPayload?.summary?.text || '').trim();
  const recentAudit = Array.isArray(auditEvents)
    ? auditEvents
      .slice(0, 3)
      .map((event) => prettifyEventType(event?.event_type || event?.eventType || ''))
      .filter(Boolean)
    : [];

  const lines = [];
  lines.push(`${vendor} · Invoice ${invoiceNumber} · ${formatAmount(item?.amount, item?.currency || 'USD')}`);
  lines.push(`Current state: ${state.replace(/_/g, ' ')}${nextAction ? ` · Next action: ${nextAction.replace(/_/g, ' ')}` : ''}`);
  lines.push(`Summary: ${issueSummary}`);
  if (exceptionReason) lines.push(`Exception detail: ${exceptionReason}`);
  if (decisionSummary?.detail) lines.push(`Recommended handling: ${decisionSummary.detail}`);
  if (dueRisk) lines.push(`Due risk: ${dueRisk}`);
  if (budgetContext?.requiresDecision) {
    const budgetStatus = String(budgetContext.status || 'review').replace(/_/g, ' ');
    lines.push(`Budget decision required (${budgetStatus}).`);
  }
  if (item?.requires_field_review) {
    const blockers = Array.isArray(item?.confidence_blockers) ? item.confidence_blockers : [];
    const fields = blockers
      .slice(0, 4)
      .map((entry) => (typeof entry === 'string' ? entry : String(entry?.field || entry?.code || '').trim()))
      .filter(Boolean);
    lines.push(
      fields.length
        ? `Field review blockers: ${fields.join(', ')}`
        : 'Field review blockers present.'
    );
  }
  if (contextSummary) {
    lines.push(`Context: ${trimText(contextSummary, 160)}`);
  }
  if (recentAudit.length > 0) {
    lines.push(`Recent activity: ${recentAudit.join(' → ')}`);
  }

  const deduped = [];
  const seen = new Set();
  for (const line of lines) {
    const text = String(line || '').trim();
    if (!text) continue;
    const key = text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(text);
  }

  return {
    title: 'Finance lead exception summary',
    lines: deduped.slice(0, 8)
  };
}

function buildFinanceSummarySharePreviewCard(result, fallbackTarget = 'email_draft') {
  const target = String(result?.target || fallbackTarget || 'email_draft').trim() || 'email_draft';
  const summary = result?.summary || {};
  const preview = result?.preview || {};
  const title = `Finance summary share preview · ${target.replace(/_/g, ' ')}`;
  const lines = Array.isArray(summary?.lines) ? summary.lines.slice(0, 3) : [];
  let previewText = '';

  if (target === 'email_draft') {
    const draft = preview?.draft || result?.draft || {};
    const to = String(preview?.recipient_email || draft?.to || '').trim() || 'Recipient not set';
    const subject = String(draft?.subject || '').trim() || 'No subject';
    const body = String(draft?.body || '').trim();
    lines.push(`Email draft recipient: ${to}`);
    lines.push(`Subject: ${subject}`);
    previewText = `To: ${to}\nSubject: ${subject}\n\n${body}`.trim();
  } else if (target === 'slack_thread') {
    const channelId = String(preview?.channel_id || '').trim() || 'unknown';
    const threadTs = String(preview?.thread_ts || '').trim() || 'unknown';
    lines.push(`Slack thread target: ${channelId}`);
    lines.push(`Thread: ${threadTs}`);
    previewText = String(preview?.text || '').trim();
  } else if (target === 'teams_reply') {
    const channelId = String(preview?.channel_id || '').trim() || 'unknown';
    const replyToId = String(preview?.reply_to_id || '').trim();
    lines.push(`Teams thread target: ${channelId}`);
    if (replyToId) lines.push(`Reply to: ${replyToId}`);
    try {
      previewText = JSON.stringify(preview?.activity || {}, null, 2);
    } catch (_) {
      previewText = '';
    }
  }

  if (result?.audit_event_id) {
    lines.push(`Preview audited (${result.audit_event_id}).`);
  }

  return {
    kind: 'finance_share_preview',
    title,
    lines: lines.filter(Boolean),
    previewText: previewText.length > 2500 ? `${previewText.slice(0, 2500)}\n…` : previewText
  };
}

function renderAgentSummaryCardHtml(data) {
  const lines = Array.isArray(data?.lines) ? data.lines : [];
  const visibleLineLimit = data?.kind === 'batch_run_result' ? 4 : 5;
  const visibleLines = lines.slice(0, visibleLineLimit);
  const hiddenLines = lines.slice(visibleLineLimit);
  const rows = visibleLines
    .map((line) => `
      <div class="cl-agent-related-row">
        <div class="cl-agent-detail">${escapeHtml(String(line))}</div>
      </div>
    `)
    .join('');
  const detailItems = Array.isArray(data?.detailItems) ? data.detailItems : [];
  const visibleDetailItems = detailItems.slice(0, 12);
  const renderDetailRow = (item) => {
      const tone = String(item?.tone || '').trim().toLowerCase();
      const toneClass = tone === 'success'
        ? ' cl-batch-result-status-success'
        : tone === 'warn'
          ? ' cl-batch-result-status-warn'
          : tone === 'error'
            ? ' cl-batch-result-status-error'
            : '';
      return `
        <div class="cl-batch-result-row">
          <div class="cl-batch-result-main">
            <span class="cl-batch-result-status${toneClass}">${escapeHtml(String(item?.status || 'result').replace(/_/g, ' '))}</span>
            <span class="cl-batch-result-label">${escapeHtml(String(item?.label || 'Item'))}</span>
          </div>
          ${item?.detail ? `<div class="cl-batch-result-detail">${escapeHtml(String(item.detail))}</div>` : ''}
        </div>
      `;
  };
  const groupedDetailRows = data?.kind === 'batch_run_result'
    ? [
        { key: 'success', title: 'Successful' },
        { key: 'warn', title: 'Needs follow-up' },
        { key: 'error', title: 'Failed' },
      ]
        .map((group) => {
          const members = visibleDetailItems.filter((item) => String(item?.tone || '').trim().toLowerCase() === group.key);
          if (!members.length) return '';
          return `
            <div class="cl-batch-result-group">
              <div class="cl-batch-result-group-title">${escapeHtml(group.title)} (${members.length})</div>
              <div class="cl-batch-result-group-body">
                ${members.map(renderDetailRow).join('')}
              </div>
            </div>
          `;
        })
        .filter(Boolean)
        .join('')
    : visibleDetailItems.map(renderDetailRow).join('');
  const detailRows = groupedDetailRows;
  const hiddenLineRows = hiddenLines
    .map((line) => `<div class="cl-agent-detail">${escapeHtml(String(line))}</div>`)
    .join('');
  const hiddenDetailCount = Math.max(0, detailItems.length - 12);
  const detailsHtml = (hiddenLines.length > 0 || detailRows || hiddenDetailCount > 0)
    ? `
      <details class="cl-details cl-agent-brief-details">
        <summary>${escapeHtml(
          detailRows
            ? `Show item results (${detailItems.length})`
            : `Show more details (${hiddenLines.length})`
        )}</summary>
        <div class="cl-detail-grid">
          ${hiddenLineRows}
          ${detailRows ? `<div class="cl-batch-result-list">${detailRows}${hiddenDetailCount > 0 ? `<div class="cl-agent-detail">${hiddenDetailCount} additional item result(s) not shown.</div>` : ''}</div>` : ''}
        </div>
      </details>
    `
    : '';
  const actions = Array.isArray(data?.actions) ? data.actions : [];
  const actionButtonsHtml = actions.length > 0
    ? `
      <div class="cl-agent-actions-bar cl-batch-summary-actions">
        ${actions.slice(0, 3).map((action) => `
          <button
            class="cl-btn cl-btn-secondary cl-batch-summary-action"
            data-action-id="${escapeHtml(String(action.id || ''))}"
            data-batch-op="${escapeHtml(String(action.opId || ''))}"
            data-target-item-ids="${escapeHtml((Array.isArray(action.itemIds) ? action.itemIds : []).join(','))}"
          >
            ${escapeHtml(String(action.label || 'Action'))}
          </button>
        `).join('')}
      </div>
    `
    : '';
  return `
    <div class="cl-agent-brief">
      <div class="cl-agent-brief-title">${escapeHtml(data?.title || 'Summary')}</div>
      ${rows || '<div class="cl-empty">No blocker details.</div>'}
      ${actionButtonsHtml}
      ${detailsHtml}
      ${data?.kind === 'finance_share_preview' && data?.previewText
        ? `<pre class="cl-agent-preview-payload">${escapeHtml(String(data.previewText))}</pre>`
        : ''
      }
    </div>
  `;
}

async function openNeedsInfoDraftCompose(item) {
  if (!item?.id || !queueManager) {
    return { ok: false, reason: 'unavailable' };
  }
  try {
    const followup = await queueManager.prepareVendorFollowup(item);
    const followupStatus = String(followup?.status || '').trim().toLowerCase();
    const followupDraftId = String(
      followup?.needs_info_draft_id
      || followup?.draft_id
      || item?.needs_info_draft_id
      || ''
    ).trim();
    if (followupDraftId) {
      window.open(`https://mail.google.com/#drafts/${encodeURIComponent(followupDraftId)}`, '_blank', 'noopener');
      return { ok: true, mode: 'draft_link', status: followupStatus || 'prepared' };
    }
    if (followupStatus === 'blocked') {
      return { ok: false, reason: 'followup_attempt_limit_reached' };
    }
    const settings = await queueManager.getSyncConfig();
    const backendUrl = String(settings?.backendUrl || '').trim();
    if (!backendUrl) {
      return { ok: false, reason: 'backend_unavailable' };
    }
    const url = `${backendUrl}/extension/needs-info-draft/${encodeURIComponent(item.id)}`;
    const resp = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
    if (!resp.ok) {
      return { ok: false, reason: `http_${resp.status}` };
    }
    const draft = await resp.json();
    return openComposePrefill(draft);
  } catch (_) {
    return { ok: false, reason: 'draft_fetch_failed' };
  }
}

function openComposePrefill(draft) {
  if (!sdk?.Compose?.openNewComposeView) {
    return { ok: false, reason: 'compose_unavailable' };
  }
  _pendingComposePrefill = {
    to: draft?.to || '',
    subject: draft?.subject || '',
    body: draft?.body || '',
  };
  sdk.Compose.openNewComposeView();
  return { ok: true };
}

function chunkList(items, size = 2) {
  const safeSize = Number(size) > 0 ? Number(size) : 2;
  const chunks = [];
  const list = Array.isArray(items) ? items : [];
  for (let i = 0; i < list.length; i += safeSize) {
    chunks.push(list.slice(i, i + safeSize));
  }
  return chunks;
}

function getItemActivityTimestampMs(item) {
  const raw = item?.updated_at || item?.updatedAt || item?.state_updated_at || item?.stateUpdatedAt || item?.created_at || item?.createdAt || '';
  if (!raw) return 0;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

function buildBatchAgentOpsSnapshot(items, agentSessionsByItem = new Map(), {
  nowMs = Date.now(),
  agingApprovalHours = 24,
  previewLimit = 5
} = {}) {
  const list = Array.isArray(items) ? items : [];
  const sessionsMap = agentSessionsByItem instanceof Map ? agentSessionsByItem : new Map();
  const thresholdMs = Number(agingApprovalHours) > 0 ? Number(agingApprovalHours) * 3600 * 1000 : 24 * 3600 * 1000;
  const classifyRecoverability = (item) => {
    const joined = `${String(item?.last_error || '').toLowerCase()} ${String(item?.exception_code || '').toLowerCase()}`.trim();
    if (!joined) return { recoverable: true, reason: 'recoverable_unknown_failure' };
    const nonRecoverableTokens = [
      'validation', 'invalid', 'schema', 'duplicate', 'already posted',
      'already_exists', 'permission', 'forbidden', 'unauthorized', 'auth_failed',
      'missing required', 'unmapped', 'policy_blocked',
    ];
    const recoverableTokens = [
      'timeout', 'timed out', 'temporar', 'transient', 'service unavailable',
      'network', 'connection', 'rate limit', 'throttle', 'gateway',
      'http_502', 'http_503', 'http_504', 'retryable', 'connector_timeout',
    ];
    const blocked = nonRecoverableTokens.find((token) => joined.includes(token));
    if (blocked) {
      return { recoverable: false, reason: `non_recoverable_${blocked.replace(/\s+/g, '_')}` };
    }
    const allowed = recoverableTokens.find((token) => joined.includes(token));
    if (allowed) {
      return { recoverable: true, reason: `recoverable_${allowed.replace(/\s+/g, '_')}` };
    }
    return { recoverable: true, reason: 'recoverable_unspecified' };
  };

  const summarizeItem = (item, extra = {}) => {
    const ts = getItemActivityTimestampMs(item);
    const ageMs = ts > 0 ? Math.max(0, nowMs - ts) : null;
    const ageHours = ageMs === null ? null : Number((ageMs / 3600000).toFixed(1));
    const hasSession = Boolean(item?.id && sessionsMap.get(item.id)?.session?.id);
    return {
      id: item?.id || '',
      threadId: item?.thread_id || item?.threadId || '',
      vendor: String(item?.vendor_name || item?.vendor || item?.sender || 'Unknown vendor').trim(),
      invoiceNumber: String(item?.invoice_number || 'N/A').trim(),
      amountRaw: Number(item?.amount),
      amountText: formatAmount(item?.amount, item?.currency || 'USD'),
      state: String(item?.state || 'received').toLowerCase(),
      nextAction: String(item?.next_action || '').trim(),
      followupNextAction: String(item?.followup_next_action || '').trim(),
      exceptionCode: String(item?.exception_code || '').trim(),
      documentType: String(item?.document_type || item?.email_type || 'invoice').trim().toLowerCase(),
      lastError: String(item?.last_error || '').trim(),
      hasSession,
      ageHours,
      ageUnknown: ageHours === null,
      ...extra
    };
  };

  const lowRiskReadyCandidates = list
    .filter((item) => String(item?.state || '').toLowerCase() === 'ready_to_post')
    .map((item) => {
      const blockedReasons = [];
      if (item?.requires_field_review) blockedReasons.push('field review required');
      if (Array.isArray(item?.confidence_blockers) && item.confidence_blockers.length > 0) blockedReasons.push('confidence blockers');
      if (item?.budget_requires_decision) blockedReasons.push('budget decision required');
      if (String(item?.next_action || '').trim().toLowerCase() === 'none') blockedReasons.push('merged/suppressed');
      if (item?.exception_code) blockedReasons.push(String(item.exception_code).replace(/_/g, ' '));
      return summarizeItem(item, {
        runnable: blockedReasons.length === 0,
        blockedReasons,
      });
    });

  const failedPostCandidates = list
    .filter((item) => String(item?.state || '').toLowerCase() === 'failed_post')
    .map((item) => summarizeItem(item, { runnable: Boolean(item?.id) }));

  const agingApprovalCandidates = list
    .filter((item) => ['needs_approval', 'pending_approval'].includes(String(item?.state || '').toLowerCase()))
    .map((item) => summarizeItem(item))
    .filter((item) => item.ageUnknown || (item.ageHours !== null && item.ageHours * 3600000 >= thresholdMs))
    .sort((a, b) => {
      const aAge = a.ageHours === null ? Number.POSITIVE_INFINITY : a.ageHours;
      const bAge = b.ageHours === null ? Number.POSITIVE_INFINITY : b.ageHours;
      return bAge - aAge;
    });

  const vendorFollowupCandidates = list
    .filter((item) => String(item?.state || '').toLowerCase() === 'needs_info')
    .map((item) => {
      const followupAction = String(item?.followup_next_action || item?.next_action || '').trim().toLowerCase();
      const blockedReasons = [];
      if (!item?.id) blockedReasons.push('missing AP item id');
      if (followupAction === 'await_vendor_response') blockedReasons.push('awaiting vendor response SLA');
      if (followupAction === 'manual_vendor_escalation') blockedReasons.push('manual escalation required');
      if (String(item?.next_action || '').trim().toLowerCase() === 'none') blockedReasons.push('merged/suppressed');
      return summarizeItem(item, {
        runnable: blockedReasons.length === 0,
        blockedReasons,
      });
    });

  const routeApprovalCandidates = list
    .filter((item) => String(item?.state || '').toLowerCase() === 'validated')
    .map((item) => {
      const blockedReasons = [];
      if (!item?.id) blockedReasons.push('missing AP item id');
      if (item?.requires_field_review) blockedReasons.push('field review required');
      if (Array.isArray(item?.confidence_blockers) && item.confidence_blockers.length > 0) blockedReasons.push('confidence blockers');
      if (item?.budget_requires_decision) blockedReasons.push('budget decision required');
      if (item?.exception_code) blockedReasons.push(String(item.exception_code).replace(/_/g, ' '));
      const docType = String(item?.document_type || item?.email_type || 'invoice').trim().toLowerCase();
      if (docType && docType !== 'invoice') blockedReasons.push('non-invoice document');
      if (String(item?.next_action || '').trim().toLowerCase() === 'none') blockedReasons.push('merged/suppressed');
      return summarizeItem(item, {
        runnable: blockedReasons.length === 0,
        blockedReasons,
      });
    });

  const recoverableFailureCandidates = list
    .filter((item) => String(item?.state || '').toLowerCase() === 'failed_post')
    .map((item) => {
      const recoverability = classifyRecoverability(item);
      const blockedReasons = [];
      if (!item?.id) blockedReasons.push('missing AP item id');
      if (!recoverability.recoverable) {
        blockedReasons.push(
          String(recoverability.reason || 'non_recoverable_failure').replace(/^non_recoverable_/, '').replace(/_/g, ' ')
        );
      }
      return summarizeItem(item, {
        runnable: blockedReasons.length === 0,
        blockedReasons,
        recoverability,
      });
    });

  const summarizeGroup = (itemsList, {
    runSupported = false,
    previewOnly = false
  } = {}) => {
    const itemsSafe = Array.isArray(itemsList) ? itemsList : [];
    const runnable = itemsSafe.filter((entry) => entry.runnable !== false);
    const withSession = runnable.filter((entry) => entry.hasSession);
    const missingSession = runnable.filter((entry) => !entry.hasSession);
    const blocked = itemsSafe.filter((entry) => entry.runnable === false);
    return {
      count: itemsSafe.length,
      runSupported,
      previewOnly,
      items: itemsSafe,
      previewItems: itemsSafe.slice(0, previewLimit),
      runnableCount: runnable.length,
      withSessionCount: withSession.length,
      missingSessionCount: missingSession.length,
      blockedCount: blocked.length,
    };
  };

  return {
    queueCount: list.length,
    agingApprovalHours,
    lowRiskReady: summarizeGroup(lowRiskReadyCandidates, { runSupported: true }),
    failedPostRetryPreview: summarizeGroup(failedPostCandidates, { runSupported: false, previewOnly: true }),
    nudgeAgingApprovals: summarizeGroup(agingApprovalCandidates, { runSupported: true }),
    prepareVendorFollowups: summarizeGroup(vendorFollowupCandidates, { runSupported: true }),
    routeLowRiskForApproval: summarizeGroup(routeApprovalCandidates, { runSupported: true }),
    retryRecoverableFailures: summarizeGroup(recoverableFailureCandidates, { runSupported: true }),
  };
}

function buildBatchOpsPreviewCard(operationId, snapshot) {
  const op = String(operationId || '').trim();
  const previewItems = Array.isArray(snapshot?.previewItems) ? snapshot.previewItems : [];
  const selectedReasonCounts = snapshot?.selectedReasonCounts && typeof snapshot.selectedReasonCounts === 'object'
    ? snapshot.selectedReasonCounts
    : {};
  const excludedReasonCounts = snapshot?.excludedReasonCounts && typeof snapshot.excludedReasonCounts === 'object'
    ? snapshot.excludedReasonCounts
    : {};
  const reasonLines = (counts, label) => Object.entries(counts)
    .slice(0, 4)
    .map(([reason, count]) => `${label}: ${String(reason).replace(/[:_]/g, ' ')} (${count})`);
  const itemLines = previewItems.map((entry) => {
    const ageText = entry.ageUnknown ? 'age unknown' : entry.ageHours !== null ? `${entry.ageHours}h old` : '';
    const sessionText = entry.hasSession ? 'agent session ready' : 'no agent session';
    const parts = [
      `${entry.vendor} · ${entry.invoiceNumber}`,
      entry.amountText,
      ageText,
      sessionText
    ].filter(Boolean);
    return trimText(parts.join(' · '), 180);
  });

  if (op === 'process_low_risk_ready') {
    return {
      kind: 'blocker_summary',
      title: 'Preview batch: process low-risk ready items',
      lines: [
        `${snapshot?.count || 0} ready-to-post item(s) matched the low-risk filter.`,
        snapshot?.selectedCount !== undefined ? `${snapshot?.selectedCount || 0} item(s) selected by current batch policy.` : '',
        `${snapshot?.runnableCount || 0} runnable now · ${snapshot?.withSessionCount || 0} with agent sessions · ${snapshot?.missingSessionCount || 0} missing sessions.`,
        `${snapshot?.blockedCount || 0} item(s) excluded due to field review, confidence blockers, budget decisions, or suppression.`,
        snapshot?.policySummary || '',
        ...reasonLines(selectedReasonCounts, 'Selected reason'),
        ...reasonLines(excludedReasonCounts, 'Excluded reason'),
        'Run dispatches the ERP posting macro to existing item agent sessions (preview-first; per-item confirmations may still be required).',
        ...itemLines,
      ].filter(Boolean)
    };
  }

  if (op === 'retry_failed_posts_preview') {
    return {
      kind: 'blocker_summary',
      title: 'Preview batch: failed post retries',
      lines: [
        `${snapshot?.count || 0} failed-post item(s) found.`,
        snapshot?.selectedCount !== undefined ? `${snapshot?.selectedCount || 0} item(s) selected by current batch policy.` : '',
        `${snapshot?.withSessionCount || 0} have active agent sessions and are eligible for retry previews.`,
        snapshot?.policySummary || '',
        ...reasonLines(selectedReasonCounts, 'Selected reason'),
        ...reasonLines(excludedReasonCounts, 'Excluded reason'),
        'Run retries uses the canonical AP retry-post path and preserves per-item audit/state transitions.',
        ...itemLines,
      ].filter(Boolean)
    };
  }

  if (op === 'nudge_aging_approvals') {
    return {
      kind: 'blocker_summary',
      title: 'Preview batch: approval nudges',
      lines: [
        `${snapshot?.count || 0} aging approval item(s) found (threshold: ${snapshot?.agingApprovalHours || 24}h).`,
        snapshot?.selectedCount !== undefined ? `${snapshot?.selectedCount || 0} item(s) selected by current batch policy.` : '',
        `${snapshot?.withSessionCount || 0} with agent sessions · ${snapshot?.missingSessionCount || 0} without sessions (nudge path still uses audited channel callbacks).`,
        snapshot?.policySummary || '',
        ...reasonLines(selectedReasonCounts, 'Selected reason'),
        ...reasonLines(excludedReasonCounts, 'Excluded reason'),
        'Run sends approver nudges via the audited `/extension/approval-nudge` path and records per-item nudge events.',
        ...itemLines,
      ].filter(Boolean)
    };
  }

  if (op === 'prepare_vendor_followups') {
    return {
      kind: 'blocker_summary',
      title: 'Preview batch: prepare vendor follow-ups',
      lines: [
        `${snapshot?.count || 0} needs-info item(s) found.`,
        snapshot?.selectedCount !== undefined ? `${snapshot?.selectedCount || 0} item(s) selected by current batch policy.` : '',
        `${snapshot?.runnableCount || 0} eligible now · ${snapshot?.blockedCount || 0} excluded by policy prechecks.`,
        snapshot?.policySummary || '',
        ...reasonLines(selectedReasonCounts, 'Selected reason'),
        ...reasonLines(excludedReasonCounts, 'Excluded reason'),
        'Run prepares Gmail follow-up drafts for selected items and records per-item audit events.',
        ...itemLines,
      ].filter(Boolean)
    };
  }

  if (op === 'route_low_risk_for_approval') {
    return {
      kind: 'blocker_summary',
      title: 'Preview batch: route low-risk for approval',
      lines: [
        `${snapshot?.count || 0} validated item(s) found.`,
        snapshot?.selectedCount !== undefined ? `${snapshot?.selectedCount || 0} item(s) selected by current batch policy.` : '',
        `${snapshot?.runnableCount || 0} eligible now · ${snapshot?.blockedCount || 0} excluded by policy prechecks.`,
        snapshot?.policySummary || '',
        ...reasonLines(selectedReasonCounts, 'Selected reason'),
        ...reasonLines(excludedReasonCounts, 'Excluded reason'),
        'Run routes selected invoices into approval surfaces via audited finance-agent runtime intents.',
        ...itemLines,
      ].filter(Boolean)
    };
  }

  if (op === 'retry_recoverable_failures') {
    return {
      kind: 'blocker_summary',
      title: 'Preview batch: retry recoverable failures',
      lines: [
        `${snapshot?.count || 0} failed-post item(s) found.`,
        snapshot?.selectedCount !== undefined ? `${snapshot?.selectedCount || 0} item(s) selected by current batch policy.` : '',
        `${snapshot?.runnableCount || 0} eligible now · ${snapshot?.blockedCount || 0} excluded by recoverability prechecks.`,
        snapshot?.policySummary || '',
        ...reasonLines(selectedReasonCounts, 'Selected reason'),
        ...reasonLines(excludedReasonCounts, 'Excluded reason'),
        'Run calls the recoverable retry intent and reconciles result states per item.',
        ...itemLines,
      ].filter(Boolean)
    };
  }

  return {
    kind: 'blocker_summary',
    title: 'Batch preview',
    lines: ['No preview details are available for this action.']
  };
}

function normalizeBatchOpsPolicyConfig(policyState = batchOpsPolicyState) {
  const rawMax = Number(policyState?.maxItems);
  const allowed = [3, 5, 10, 20];
  const maxItems = allowed.includes(rawMax) ? rawMax : 5;
  const rawAmount = String(policyState?.amountThreshold ?? '').trim();
  const parsedAmount = rawAmount === '' ? null : Number(rawAmount);
  const amountThreshold = Number.isFinite(parsedAmount) && parsedAmount > 0 ? parsedAmount : null;
  const allowedPresets = new Set(['queue_order', 'lowest_risk_first', 'oldest_first']);
  const selectionPreset = allowedPresets.has(String(policyState?.selectionPreset || '').trim())
    ? String(policyState.selectionPreset).trim()
    : 'queue_order';
  return {
    maxItems,
    amountThreshold,
    amountThresholdInput: amountThreshold === null ? '' : String(amountThreshold),
    selectionPreset,
  };
}

function applyBatchPolicyToGroup(group, policyConfig, { previewLimit = 4 } = {}) {
  const groupSafe = group || {};
  const items = Array.isArray(groupSafe.items) ? groupSafe.items : [];
  const policy = normalizeBatchOpsPolicyConfig(policyConfig);
  const runnableItems = items.filter((entry) => entry?.runnable !== false);
  const precheckExcluded = items.filter((entry) => entry?.runnable === false);
  const amountFiltered = [];
  const amountExcluded = [];

  for (const item of runnableItems) {
    const amountThreshold = policy.amountThreshold;
    if (amountThreshold === null) {
      amountFiltered.push(item);
      continue;
    }
    const numericAmount = Number(item?.amountRaw ?? item?.amount ?? NaN);
    if (!Number.isFinite(numericAmount) || numericAmount <= amountThreshold) {
      amountFiltered.push(item);
    } else {
      amountExcluded.push(item);
    }
  }

  const orderedItems = [...amountFiltered];
  if (policy.selectionPreset === 'oldest_first') {
    orderedItems.sort((a, b) => {
      const aUnknown = Boolean(a?.ageUnknown);
      const bUnknown = Boolean(b?.ageUnknown);
      if (aUnknown !== bUnknown) return aUnknown ? 1 : -1;
      const aAge = Number.isFinite(Number(a?.ageHours)) ? Number(a.ageHours) : -1;
      const bAge = Number.isFinite(Number(b?.ageHours)) ? Number(b.ageHours) : -1;
      return bAge - aAge;
    });
  } else if (policy.selectionPreset === 'lowest_risk_first') {
    orderedItems.sort((a, b) => {
      const aRunnable = a?.runnable !== false ? 1 : 0;
      const bRunnable = b?.runnable !== false ? 1 : 0;
      if (bRunnable !== aRunnable) return bRunnable - aRunnable;
      const aSession = a?.hasSession ? 1 : 0;
      const bSession = b?.hasSession ? 1 : 0;
      if (bSession !== aSession) return bSession - aSession;
      const aAmount = Number.isFinite(Number(a?.amountRaw)) ? Number(a.amountRaw) : Number.POSITIVE_INFINITY;
      const bAmount = Number.isFinite(Number(b?.amountRaw)) ? Number(b.amountRaw) : Number.POSITIVE_INFINITY;
      if (aAmount !== bAmount) return aAmount - bAmount;
      const aAgeUnknown = Boolean(a?.ageUnknown);
      const bAgeUnknown = Boolean(b?.ageUnknown);
      if (aAgeUnknown !== bAgeUnknown) return aAgeUnknown ? 1 : -1;
      const aAge = Number.isFinite(Number(a?.ageHours)) ? Number(a.ageHours) : -1;
      const bAge = Number.isFinite(Number(b?.ageHours)) ? Number(b.ageHours) : -1;
      return bAge - aAge;
    });
  }

  const selectedItems = orderedItems.slice(0, policy.maxItems);
  const limitExcluded = orderedItems.slice(selectedItems.length);
  const limitExcludedCount = limitExcluded.length;
  const withSession = selectedItems.filter((entry) => entry.hasSession);
  const missingSession = selectedItems.filter((entry) => !entry.hasSession);

  const summarizeReasons = (entries = []) => {
    const counts = {};
    for (const entry of entries) {
      const reasons = Array.isArray(entry?.reasons) && entry.reasons.length > 0
        ? entry.reasons
        : ['policy_reason_unspecified'];
      for (const reason of reasons) {
        const key = String(reason || 'policy_reason_unspecified').trim().toLowerCase() || 'policy_reason_unspecified';
        counts[key] = (counts[key] || 0) + 1;
      }
    }
    return counts;
  };

  const toDetail = (entry, reasons = []) => ({
    id: String(entry?.id || ''),
    label: `${String(entry?.vendor || 'Unknown vendor')} · ${String(entry?.invoiceNumber || 'N/A')}`,
    reasons: Array.isArray(reasons) && reasons.length > 0 ? reasons : ['policy_reason_unspecified'],
    hasSession: Boolean(entry?.hasSession),
    runnable: entry?.runnable !== false,
  });

  const selectedDetails = selectedItems.map((entry) => toDetail(entry, ['selected_by_policy']));
  const excludedDetails = [
    ...precheckExcluded.map((entry) => toDetail(
      entry,
      Array.isArray(entry?.blockedReasons) && entry.blockedReasons.length > 0
        ? entry.blockedReasons.map((reason) => `precheck:${String(reason).toLowerCase().replace(/\s+/g, '_')}`)
        : ['precheck:policy_blocked']
    )),
    ...amountExcluded.map((entry) => toDetail(entry, ['policy:amount_cap_exceeded'])),
    ...limitExcluded.map((entry) => toDetail(entry, ['policy:deferred_by_limit'])),
  ];

  const selectedReasonCounts = summarizeReasons(selectedDetails);
  const excludedReasonCounts = summarizeReasons(excludedDetails);

  const policySummaryParts = [];
  policySummaryParts.push(`Policy: top ${policy.maxItems} item(s)`);
  if (policy.selectionPreset === 'lowest_risk_first') {
    policySummaryParts.push('preset lowest risk first');
  } else if (policy.selectionPreset === 'oldest_first') {
    policySummaryParts.push('preset oldest first');
  } else {
    policySummaryParts.push('preset queue order');
  }
  if (policy.amountThreshold !== null) {
    policySummaryParts.push(`amount cap ${policy.amountThreshold.toFixed(2)}`);
  }
  if (amountExcluded.length > 0) {
    policySummaryParts.push(`${amountExcluded.length} excluded by amount cap`);
  }
  if (limitExcludedCount > 0) {
    policySummaryParts.push(`${limitExcludedCount} deferred by limit`);
  }
  const precheckExcludedCount = precheckExcluded.length;
  if (precheckExcludedCount > 0) {
    policySummaryParts.push(`${precheckExcludedCount} excluded by prechecks`);
  }

  return {
    ...groupSafe,
    policy,
    selectedItems,
    selectedCount: selectedItems.length,
    previewItems: selectedItems.slice(0, Math.max(1, Number(previewLimit) || 4)),
    runnableCount: selectedItems.length,
    withSessionCount: withSession.length,
    missingSessionCount: missingSession.length,
    blockedCount: precheckExcludedCount,
    policyAmountExcludedCount: amountExcluded.length,
    policyLimitExcludedCount: limitExcludedCount,
    policySummary: policySummaryParts.join(' · '),
    selectedDetails,
    excludedDetails,
    selectedReasonCounts,
    excludedReasonCounts,
  };
}

function buildBatchRefreshIndicator(operationId, targetedIds = [], queueItems = []) {
  const op = String(operationId || '').trim();
  const ids = Array.isArray(targetedIds) ? targetedIds.filter(Boolean) : [];
  if (ids.length === 0) return '';
  const queue = Array.isArray(queueItems) ? queueItems : [];
  const stateById = new Map(queue.filter((item) => item?.id).map((item) => [item.id, String(item.state || '').toLowerCase()]));

  let posted = 0;
  let ready = 0;
  let failed = 0;
  let awaitingApproval = 0;
  let needsInfo = 0;
  let other = 0;
  let missing = 0;

  for (const id of ids) {
    const state = stateById.get(id);
    if (!state) {
      missing += 1;
      continue;
    }
    if (state === 'posted_to_erp') posted += 1;
    else if (state === 'ready_to_post') ready += 1;
    else if (state === 'failed_post') failed += 1;
    else if (state === 'needs_approval' || state === 'pending_approval') awaitingApproval += 1;
    else if (state === 'needs_info') needsInfo += 1;
    else other += 1;
  }

  if (op === 'process_low_risk_ready') {
    return `Refresh check: ${posted} posted, ${ready} still ready, ${failed} failed_post, ${other} in-progress/other, ${missing} missing from current queue snapshot.`;
  }
  if (op === 'retry_failed_posts_preview') {
    return `Refresh check: ${posted} posted, ${ready} ready_to_post, ${failed} still failed_post, ${other} other, ${missing} missing from current queue snapshot.`;
  }
  if (op === 'nudge_aging_approvals') {
    return `Refresh check: ${awaitingApproval} still awaiting approval, ${other} moved to other states, ${missing} missing from current queue snapshot.`;
  }
  if (op === 'prepare_vendor_followups') {
    return `Refresh check: ${needsInfo} still needs info, ${awaitingApproval} awaiting approval, ${other} other states, ${missing} missing from current queue snapshot.`;
  }
  if (op === 'route_low_risk_for_approval') {
    return `Refresh check: ${awaitingApproval} now awaiting approval, ${other} moved to other states, ${missing} missing from current queue snapshot.`;
  }
  if (op === 'retry_recoverable_failures') {
    return `Refresh check: ${posted} posted, ${ready} ready_to_post, ${failed} still failed_post, ${other} other, ${missing} missing from current queue snapshot.`;
  }
  return `Refresh check: ${posted} posted, ${ready} ready_to_post, ${failed} failed_post, ${other} other, ${missing} missing from current queue snapshot.`;
}

function buildBatchOpsRunResultCard(operationId, {
  attempted = 0,
  successCount = 0,
  partialCount = 0,
  failureCount = 0,
  skippedCount = 0,
  items = [],
  policySummary = '',
  refreshSummary = '',
} = {}) {
  const op = String(operationId || '').trim();
  const normalizedDetailItems = (Array.isArray(items) ? items : []).map((entry) => ({
    itemId: entry?.itemId || '',
    status: entry?.status || '',
    label: entry?.label || 'Item',
    detail: entry?.detail || '',
    tone: entry?.ok ? 'success' : entry?.partial ? 'warn' : 'error',
    retryable: Boolean(entry?.retryable),
  }));
  const rerunFailedIds = normalizedDetailItems
    .filter((entry) => entry.retryable && entry.tone === 'error' && entry.itemId)
    .map((entry) => entry.itemId);
  const rerunActions = rerunFailedIds.length > 0
    ? [{
        id: 'rerun_failed_subset',
        opId: op,
        itemIds: rerunFailedIds,
        label: `Rerun failed subset (${rerunFailedIds.length})`,
      }]
    : [];

  if (op === 'process_low_risk_ready') {
    return {
      kind: 'batch_run_result',
      title: 'Batch run completed: low-risk ready items',
      lines: [
        `Attempted ${attempted} item(s): ${successCount} dispatched, ${failureCount} failed, ${skippedCount} skipped.`,
        policySummary,
        'Dispatched items now continue in their per-item agent timelines (preview/confirmation may still be required).',
        refreshSummary,
      ].filter(Boolean),
      detailItems: normalizedDetailItems,
      actions: rerunActions,
    };
  }

  if (op === 'retry_failed_posts_preview') {
    return {
      kind: 'batch_run_result',
      title: 'Batch run completed: failed post retries',
      lines: [
        `Attempted ${attempted} item(s): ${successCount} posted, ${partialCount} re-queued, ${failureCount} failed, ${skippedCount} skipped.`,
        policySummary,
        'Retries use the canonical AP retry-post path (failed_post -> ready_to_post -> posted_to_erp/failed_post) and preserve audit history.',
        refreshSummary,
      ].filter(Boolean),
      detailItems: normalizedDetailItems,
      actions: rerunActions,
    };
  }

  if (op === 'nudge_aging_approvals') {
    return {
      kind: 'batch_run_result',
      title: 'Batch run completed: approval nudges',
      lines: [
        `Attempted ${attempted} item(s): ${successCount} nudged, ${failureCount} failed, ${skippedCount} skipped.`,
        policySummary,
        'Per-item nudge outcomes are audited and appear in the item timeline/audit trail.',
        refreshSummary,
      ].filter(Boolean),
      detailItems: normalizedDetailItems,
      actions: rerunActions,
    };
  }

  if (op === 'prepare_vendor_followups') {
    return {
      kind: 'batch_run_result',
      title: 'Batch run completed: vendor follow-ups',
      lines: [
        `Attempted ${attempted} item(s): ${successCount} prepared, ${partialCount} waiting, ${failureCount} failed, ${skippedCount} skipped.`,
        policySummary,
        'Per-item follow-up outcomes are audited and keep Gmail draft state in sync.',
        refreshSummary,
      ].filter(Boolean),
      detailItems: normalizedDetailItems,
      actions: rerunActions,
    };
  }

  if (op === 'route_low_risk_for_approval') {
    return {
      kind: 'batch_run_result',
      title: 'Batch run completed: route low-risk for approval',
      lines: [
        `Attempted ${attempted} item(s): ${successCount} routed, ${failureCount} failed, ${skippedCount} skipped.`,
        policySummary,
        'Routing uses approval surfaces and emits per-item audit events.',
        refreshSummary,
      ].filter(Boolean),
      detailItems: normalizedDetailItems,
      actions: rerunActions,
    };
  }

  if (op === 'retry_recoverable_failures') {
    return {
      kind: 'batch_run_result',
      title: 'Batch run completed: retry recoverable failures',
      lines: [
        `Attempted ${attempted} item(s): ${successCount} posted, ${partialCount} re-queued, ${failureCount} failed, ${skippedCount} skipped.`,
        policySummary,
        'Retries use recoverability prechecks and workflow resume semantics.',
        refreshSummary,
      ].filter(Boolean),
      detailItems: normalizedDetailItems,
      actions: rerunActions,
    };
  }

  return {
    kind: 'batch_run_result',
    title: 'Batch run completed',
    lines: [
      `Attempted ${attempted} item(s).`,
      policySummary,
      refreshSummary,
    ].filter(Boolean),
    detailItems: normalizedDetailItems,
    actions: rerunActions,
  };
}

function buildAgentIntentRecommendations(item, {
  canRetryPostMacro = false,
  canRunCollectW9 = false,
  canRouteApproval = false,
  canNudgeApprovers = false,
  canSummarizeBlockers = false,
  canDraftVendorReply = false,
  canSummarizeFinanceLead = false,
  canShareFinanceSummary = false
} = {}) {
  const itemState = String(item?.state || 'received').toLowerCase();
  const nextAction = String(item?.next_action || '').trim().toLowerCase();
  const exceptionCode = String(item?.exception_code || '').trim().toLowerCase();
  const hasFieldReview = Boolean(item?.requires_field_review);
  const confidenceBlockers = Array.isArray(item?.confidence_blockers) ? item.confidence_blockers : [];
  const hasBlockers = Boolean(
    canSummarizeBlockers
    || hasFieldReview
    || confidenceBlockers.length > 0
    || exceptionCode
    || ['needs_info', 'failed_post'].includes(itemState)
  );
  const actions = [];
  let order = 0;

  const pushAction = (intent, label, why, {
    buttonTone = 'secondary',
    score = 0
  } = {}) => {
    actions.push({
      intent,
      label,
      why: trimText(why, 120),
      buttonTone,
      score,
      order: order++
    });
  };

  if (canRetryPostMacro) {
    pushAction(
      'preview_post_fallback',
      'Preview ERP retry plan',
      'See browser fallback steps and approval requirements before execution.',
      { buttonTone: 'secondary', score: 20 }
    );
    pushAction(
      'run_post_fallback',
      itemState === 'failed_post' ? 'Retry ERP posting now' : 'Run ERP fallback now',
      itemState === 'failed_post'
        ? 'Re-attempt ERP posting through the agent fallback flow.'
        : 'Execute the approved ERP fallback flow now.',
      { buttonTone: 'primary', score: 10 }
    );
  }

  pushAction(
    'preview_collect_w9',
    'Preview vendor docs check (W-9)',
    'Preview how the agent will check or collect vendor tax documentation.',
    { buttonTone: 'secondary', score: 8 }
  );
  if (canRunCollectW9) {
    pushAction(
      'run_collect_w9',
      'Run vendor docs check now',
      'Start the agent flow to check or collect vendor tax documentation.',
      { buttonTone: 'secondary', score: 6 }
    );
  }

  if (canRouteApproval) {
    const needsReroute = ['needs_approval', 'pending_approval'].includes(itemState);
    pushAction(
      'route_approval',
      needsReroute ? 'Re-send approval request' : 'Send for approval',
      needsReroute
        ? 'Push this invoice back to Slack/Teams with current context.'
        : 'Route this invoice to approvers in Slack/Teams with full context.',
      { buttonTone: 'secondary', score: 25 }
    );
  }

  if (canNudgeApprovers) {
    pushAction(
      'nudge_approvers',
      'Send reminder(s)',
      'Re-send approval context to prompt a decision in Slack/Teams.',
      { buttonTone: 'secondary', score: 26 }
    );
  }

  if (canSummarizeBlockers) {
    pushAction(
      'summarize_blockers',
      'Explain what is blocking this invoice',
      'Summarize policy, confidence, or posting blockers and the next recovery step.',
      { buttonTone: 'secondary', score: 30 }
    );
  }

  pushAction(
    'explain_decision',
    'Why did the agent decide this?',
    'Ask Claude to explain in plain English why it recommended this action for this invoice.',
    { buttonTone: 'secondary', score: 18 }
  );

  if (canDraftVendorReply) {
    pushAction(
      'draft_vendor_reply',
      'Draft vendor info request',
      'Open a pre-filled Gmail draft to request missing invoice details from the vendor.',
      { buttonTone: 'secondary', score: 28 }
    );
  }

  if (canSummarizeFinanceLead) {
    pushAction(
      'summarize_finance_lead',
      'Summarize exception for finance lead',
      'Prepare a concise, shareable exception summary using AP context and recent audit activity.',
      { buttonTone: 'secondary', score: 22 }
    );
  }
  if (canShareFinanceSummary) {
    pushAction(
      'preview_finance_summary_share',
      'Preview finance summary share',
      'Preview the exact finance summary message for the selected target before sending it.',
      { buttonTone: 'secondary', score: 27 }
    );
    pushAction(
      'share_finance_summary',
      'Share finance summary',
      'Open a finance-lead draft summary (and record an audit event for the share action).',
      { buttonTone: 'secondary', score: 24 }
    );
  }

  for (const action of actions) {
    if (action.intent === 'preview_post_fallback') {
      if (itemState === 'failed_post') action.score += 90;
      else if (['ready_to_post', 'approved'].includes(itemState)) action.score += 75;
      if (nextAction.includes('retry') || nextAction.includes('post')) action.score += 20;
    }
    if (action.intent === 'run_post_fallback') {
      if (itemState === 'failed_post') action.score += 70;
      else if (itemState === 'ready_to_post') action.score += 55;
      if (hasBlockers || hasFieldReview) action.score -= 20;
    }
    if (action.intent === 'route_approval') {
      if (['validated', 'needs_approval', 'pending_approval'].includes(itemState)) action.score += 75;
      if (nextAction === 'approve_or_reject') action.score += 25;
      if (hasFieldReview || confidenceBlockers.length > 0) action.score -= 10;
    }
    if (action.intent === 'nudge_approvers') {
      if (['needs_approval', 'pending_approval'].includes(itemState)) action.score += 95;
      if (nextAction === 'approve_or_reject') action.score += 25;
      if (itemState === 'validated') action.score -= 20;
    }
    if (action.intent === 'summarize_blockers') {
      if (hasBlockers) action.score += 75;
      if (exceptionCode === 'confidence_low' || hasFieldReview) action.score += 10;
      if (itemState === 'failed_post') action.score += 10;
    }
    if (action.intent === 'draft_vendor_reply') {
      if (itemState === 'needs_info') action.score += 95;
      if (nextAction === 'request_info') action.score += 35;
      if (hasFieldReview || confidenceBlockers.length > 0) action.score += 15;
      if (itemState === 'failed_post') action.score -= 15;
    }
    if (action.intent === 'summarize_finance_lead') {
      if (hasBlockers) action.score += 70;
      if (['needs_info', 'failed_post', 'needs_approval', 'pending_approval'].includes(itemState)) action.score += 20;
      if (exceptionCode) action.score += 15;
    }
    if (action.intent === 'preview_finance_summary_share') {
      if (hasBlockers) action.score += 82;
      if (['needs_info', 'failed_post', 'needs_approval', 'pending_approval'].includes(itemState)) action.score += 20;
      if (exceptionCode) action.score += 10;
    }
    if (action.intent === 'share_finance_summary') {
      if (hasBlockers) action.score += 78;
      if (['needs_info', 'failed_post', 'needs_approval', 'pending_approval'].includes(itemState)) action.score += 20;
      if (exceptionCode) action.score += 10;
    }
    if (action.intent === 'preview_collect_w9') {
      if (itemState === 'needs_info') action.score += 30;
      if (exceptionCode.includes('w9') || exceptionCode.includes('vendor')) action.score += 25;
      if (nextAction.includes('vendor') || nextAction.includes('document') || nextAction.includes('w9')) action.score += 15;
    }
    if (action.intent === 'run_collect_w9') {
      if (itemState === 'needs_info') action.score += 55;
      if (exceptionCode.includes('w9') || exceptionCode.includes('vendor')) action.score += 35;
      if (nextAction.includes('vendor') || nextAction.includes('document') || nextAction.includes('w9')) action.score += 20;
      if (itemState === 'posted_to_erp' || itemState === 'closed') action.score -= 50;
    }
  }

  actions.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.order - b.order;
  });

  const recommended = actions[0] || null;
  return {
    actions,
    recommended
  };
}

function parseAgentIntentCommand(rawValue, { availableIntents = new Set() } = {}) {
  const text = String(rawValue || '').trim().toLowerCase();
  if (!text) return null;
  const normalized = text.replace(/\s+/g, ' ');
  const includesAny = (...needles) => needles.some((needle) => normalized.includes(needle));
  const canUse = (intent) => availableIntents.has(intent);

  const candidates = [];
  const push = (intent, score = 0) => {
    if (!canUse(intent)) return;
    candidates.push({ intent, score });
  };

  if (includesAny('blocker', 'blocked', 'why blocked', 'explain')) {
    push('summarize_blockers', 100);
  }
  if (includesAny('finance lead', 'controller', 'finance summary', 'exception summary', 'summary for finance')) {
    push('summarize_finance_lead', 97);
  }
  if (includesAny('preview', 'show', 'what will send', 'what will post') && includesAny('finance summary', 'finance lead', 'exception summary')) {
    push('preview_finance_summary_share', 101);
  }
  if (includesAny('share', 'send') && includesAny('finance summary', 'finance lead', 'exception summary')) {
    push('share_finance_summary', 100);
  }
  if (includesAny('draft', 'email', 'reply', 'vendor') && includesAny('vendor', 'reply', 'info', 'missing', 'request')) {
    push('draft_vendor_reply', 98);
  }
  if (includesAny('nudge', 'remind', 'ping') && includesAny('approver', 'approval')) {
    push('nudge_approvers', 99);
  }
  if (includesAny('approval', 'approver') && includesAny('route', 'send', 're-send', 'reroute', 'escalate', 'route approval')) {
    push('route_approval', 95);
  }
  if (includesAny('w9', 'w-9', 'tax form', 'vendor docs', 'vendor document')) {
    if (includesAny('run', 'start', 'collect', 'send')) {
      push('run_collect_w9', 94);
    }
    push('preview_collect_w9', 88);
  }
  if (includesAny('erp', 'post', 'posting', 'fallback', 'retry')) {
    if (includesAny('run', 'retry', 'post now', 'execute')) {
      push('run_post_fallback', 96);
    }
    push('preview_post_fallback', 90);
  }

  if (candidates.length === 0) {
    return null;
  }
  candidates.sort((a, b) => b.score - a.score);
  return candidates[0];
}

function getLinkedSources(item) {
  if (!item?.id) {
    return [];
  }
  const sources = sourcesState.get(item.id);
  if (Array.isArray(sources) && sources.length > 0) {
    return sources;
  }

  const fallback = [];
  const threadId = getSourceThreadId(item);
  const messageId = getSourceMessageId(item);
  if (threadId) {
    fallback.push({
      source_type: 'gmail_thread',
      source_ref: threadId,
      subject: item.subject,
      sender: item.sender,
      detected_at: item.created_at || item.updated_at
    });
  }
  if (messageId) {
    fallback.push({
      source_type: 'gmail_message',
      source_ref: messageId,
      subject: item.subject,
      sender: item.sender,
      detected_at: item.created_at || item.updated_at
    });
  }
  return fallback;
}

async function ensureItemContext(item, { refresh = false } = {}) {
  if (!item?.id || !queueManager) return;
  if (!refresh && contextState.has(item.id) && sourcesState.has(item.id)) return;
  contextUiState = {
    itemId: item.id,
    loading: true,
    error: ''
  };
  renderThreadContext();
  try {
    await queueManager.hydrateItemContext(item.id, { refresh });
    contextUiState = {
      itemId: item.id,
      loading: false,
      error: ''
    };
  } catch (_) {
    contextUiState = {
      itemId: item.id,
      loading: false,
      error: 'Unable to load context'
    };
  }
  renderThreadContext();
}

function getPrimaryAgentSession() {
  const item = getPrimaryItem();
  if (!item?.id) return null;
  return agentSessionsState.get(item.id) || null;
}

function getPrimaryAgentInsight() {
  const item = getPrimaryItem();
  if (!item?.id) return null;
  return agentInsightsState.get(item.id) || null;
}

function getAgentScope(item, sessionPayload = null) {
  const metadata = queueManager?.parseMetadata?.(item?.metadata) || {};
  const sessionMetadata = queueManager?.parseMetadata?.(sessionPayload?.session?.metadata) || {};
  const actorRole = String(
    metadata.actor_role
    || metadata.agent_actor_role
    || item?.actor_role
    || item?.assignee_role
    || sessionMetadata.actor_role
    || ''
  ).trim() || null;
  const workflowId = String(
    item?.workflow_id
    || metadata.workflow_id
    || sessionMetadata.workflow_id
    || ''
  ).trim() || null;
  return { actorRole, workflowId };
}

function getMacroLabel(name) {
  const macro = String(name || '').trim().toLowerCase();
  if (macro === 'ingest_invoice_match_po') return 'Ingest + match PO';
  if (macro === 'collect_w9') return 'Collect W-9';
  return macro || 'Macro';
}

async function ensureAgentPreview(item, sessionPayload, command) {
  if (!item?.id || !sessionPayload?.session?.id || !command?.command_id || !queueManager) return;
  const previewKey = `${item.id}:${sessionPayload.session.id}:${command.command_id}`;
  if (agentPreviewState.loading && agentPreviewState.key === previewKey) return;
  if (agentPreviewState.data && agentPreviewState.key === previewKey) return;

  agentPreviewState = {
    key: previewKey,
    loading: true,
    error: '',
    data: null
  };
  renderAgentActions();

  const scope = getAgentScope(item, sessionPayload);
  const preview = await queueManager.previewAgentCommand(
    sessionPayload.session.id,
    command,
    'gmail_user',
    scope
  );
  if (agentPreviewState.key !== previewKey) return;

  if (!preview) {
    agentPreviewState = {
      key: previewKey,
      loading: false,
      error: 'Unable to generate preflight preview.',
      data: null
    };
  } else {
    agentPreviewState = {
      key: previewKey,
      loading: false,
      error: '',
      data: preview
    };
  }
  renderAgentActions();
}

function renderQueueList() {
  // Queue list has been replaced by compact prev/next navigator in the focused item view.
}

function setButtonState(button, enabled, reason) {
  if (!button) return;
  button.disabled = !enabled;
  if (!enabled && reason) {
    button.dataset.disabledReason = reason;
  } else {
    delete button.dataset.disabledReason;
  }
}

function openSourceReference(source, item) {
  const type = String(source?.source_type || '');
  const ref = String(source?.source_ref || '');
  if (type === 'gmail_thread' && ref) {
    return openSourceEmail({ thread_id: ref, subject: source?.subject || item?.subject });
  }
  if (type === 'gmail_message' && ref) {
    return openSourceEmail({ message_id: ref, subject: source?.subject || item?.subject });
  }
  if (type === 'portal' && ref) {
    try {
      window.open(ref, '_blank', 'noopener,noreferrer');
      return true;
    } catch (_) {
      return false;
    }
  }
  return false;
}

function renderContextTabBody(item, contextPayload, loading, error, agentInsight = null) {
  if (loading) {
    return '<div class="cl-empty">Loading invoice context...</div>';
  }
  if (error) {
    return `<div class="cl-empty">${escapeHtml(error)}</div>`;
  }
  if (!contextPayload) {
    return '<div class="cl-empty">Context will load automatically for this invoice.</div>';
  }

  const freshness = contextPayload.freshness || {};
  const ageText = formatAgeSeconds(freshness.age_seconds);
  const freshnessSummary = ageText ? `Context refreshed ${ageText} ago` : '';

  if (activeContextTab === 'email') {
    const email = contextPayload.email || {};
    const sourceCount = Number(email.source_count || 0);
    const rows = Array.isArray(email.sources)
      ? email.sources.slice(0, 5).map((source) => {
          const detected = formatDateTime(source.detected_at);
          return `
            <div class="cl-context-row">
              <div><strong>${escapeHtml(source.subject || item.subject || 'Email source')}</strong></div>
              <div>${escapeHtml(source.sender || item.sender || 'Unknown sender')}</div>
              ${detected ? `<div>${escapeHtml(detected)}</div>` : ''}
            </div>
          `;
        }).join('')
      : '';
    return `
      <div class="cl-context-meta">Linked email sources: ${sourceCount}</div>
      ${freshnessSummary ? `<div class="cl-context-row">${escapeHtml(freshnessSummary)}</div>` : ''}
      ${rows || '<div class="cl-empty">No linked email sources yet.</div>'}
    `;
  }

  if (activeContextTab === 'web') {
    const web = contextPayload.web || {};
    const portals = Array.isArray(web.related_portals) ? web.related_portals : [];
    const paymentPortals = Array.isArray(web.payment_portals) ? web.payment_portals : [];
    const procurement = Array.isArray(web.procurement) ? web.procurement : [];
    const dms = Array.isArray(web.dms_documents) ? web.dms_documents : [];
    const bankMatches = Array.isArray(web.bank_transactions) ? web.bank_transactions : [];
    const spreadsheets = Array.isArray(web.spreadsheets) ? web.spreadsheets : [];
    const coverage = web.connector_coverage || {};
    const events = Array.isArray(web.recent_browser_events) ? web.recent_browser_events : [];
    const relatedTabs = Array.isArray(agentInsight?.relatedTabs) ? agentInsight.relatedTabs : [];
    const portalRows = [...paymentPortals, ...portals].slice(0, 3).map((portal) => `
      <div class="cl-context-row">
        <div><strong>${escapeHtml(trimText(portal.url || 'Portal', 70))}</strong></div>
        ${portal.detected_at ? `<div>${escapeHtml(formatDateTime(portal.detected_at))}</div>` : ''}
      </div>
    `).join('');
    const procurementRows = procurement.slice(0, 2).map((entry) => `
      <div class="cl-context-row">
        <div><strong>${escapeHtml(trimText(entry.ref || entry.source_ref || entry.url || 'Procurement', 70))}</strong></div>
        ${entry.detected_at ? `<div>${escapeHtml(formatDateTime(entry.detected_at))}</div>` : ''}
      </div>
    `).join('');
    const dmsRows = dms.slice(0, 2).map((entry) => `
      <div class="cl-context-row">
        <div><strong>${escapeHtml(trimText(entry.ref || entry.source_ref || entry.url || 'DMS document', 70))}</strong></div>
        ${entry.detected_at ? `<div>${escapeHtml(formatDateTime(entry.detected_at))}</div>` : ''}
      </div>
    `).join('');
    const bankRows = bankMatches.slice(0, 3).map((match) => `
      <div class="cl-context-row">
        <div><strong>${escapeHtml(trimText(match.description || match.reference || match.transaction_id || 'Bank match', 70))}</strong></div>
        <div>${escapeHtml(String(match.currency || item.currency || 'USD'))} ${escapeHtml(String(match.amount ?? '0'))}</div>
      </div>
    `).join('');
    const spreadsheetRows = spreadsheets.slice(0, 2).map((sheet) => `
      <div class="cl-context-row">
        <div><strong>${escapeHtml(trimText(sheet.spreadsheet_id || sheet.reference || 'Spreadsheet', 70))}</strong></div>
        <div>${escapeHtml(trimText(sheet.reference || '', 80))}</div>
      </div>
    `).join('');
    const eventRows = events.slice(0, 3).map((event) => {
      const browserEvent = describeBrowserContextEvent(event);
      return `
        <div class="cl-context-row cl-context-row-browser">
          <div class="cl-context-row-browser-main">
            <strong>${escapeHtml(browserEvent.title || 'Browser action')}</strong>
            <span class="cl-context-row-browser-status" data-tone="${escapeHtml(String(browserEvent.statusTone || 'info'))}">${escapeHtml(browserEvent.status || 'Unknown')}</span>
          </div>
          ${browserEvent.fallbackRelated ? '<div class="cl-context-row-browser-tag">Fallback evidence</div>' : ''}
          ${browserEvent.detail ? `<div>${escapeHtml(browserEvent.detail)}</div>` : ''}
          ${browserEvent.timeLabel ? `<div>${escapeHtml(browserEvent.timeLabel)}</div>` : ''}
        </div>
      `;
    }).join('');
    const tabRows = relatedTabs.slice(0, 3).map((tab) => `
      <div class="cl-context-row">
        <div><strong>${escapeHtml(trimText(tab.title || tab.url || 'Browser tab', 80))}</strong></div>
        <div>${escapeHtml(tab.host || trimText(tab.url || '', 64))}</div>
      </div>
    `).join('');
    return `
      <div class="cl-context-meta">Browser events: ${escapeHtml(String(web.browser_event_count || 0))} · Related tabs: ${escapeHtml(String(agentInsight?.relatedCount || 0))}</div>
      <div class="cl-context-row">
        <div><strong>Coverage:</strong> portals ${coverage.payment_portal ? 'yes' : 'no'} · procurement ${coverage.procurement ? 'yes' : 'no'} · bank ${coverage.bank ? 'yes' : 'no'} · sheets ${coverage.spreadsheets ? 'yes' : 'no'} · dms ${coverage.dms ? 'yes' : 'no'}</div>
      </div>
      ${freshnessSummary ? `<div class="cl-context-row ${freshness.is_stale ? 'cl-context-warning' : ''}">${escapeHtml(freshnessSummary)}</div>` : ''}
      ${portalRows || '<div class="cl-empty">No vendor portal sources detected.</div>'}
      ${procurementRows || ''}
      ${bankRows || ''}
      ${spreadsheetRows || ''}
      ${dmsRows || ''}
      ${tabRows || ''}
      ${eventRows || ''}
    `;
  }

  if (activeContextTab === 'approvals') {
    const approvals = contextPayload.approvals || {};
    const latest = approvals.latest || null;
    const slack = approvals.slack || {};
    const teams = approvals.teams || {};
    const payroll = approvals.payroll || contextPayload.payroll || {};
    const budgetContext = normalizeBudgetContext(contextPayload, item);
    const budgetStatus = budgetContext.status ? String(budgetContext.status).replace(/_/g, ' ') : '';
    const payrollCount = Number(payroll.count || 0);
    const payrollAmount = Number(payroll.total_amount || 0);
    const threadPreview = Array.isArray(slack.thread_preview) ? slack.thread_preview : [];
    const previewRows = threadPreview.slice(0, 3).map((entry) => `
      <div class="cl-context-row">
        <div>${escapeHtml(trimText(entry.text || '', 120))}</div>
      </div>
    `).join('');
    const budgetRows = budgetContext.checks.slice(0, 3).map((check) => `
      <div class="cl-context-row">
        <div><strong>${escapeHtml(String(check.name || 'Budget'))}:</strong> ${escapeHtml(String(check.status || 'unknown'))}</div>
        <div>${escapeHtml(formatAmount(check.remaining, item.currency || 'USD'))} remaining · ${escapeHtml(formatAmount(check.invoice_amount, item.currency || 'USD'))} invoice</div>
      </div>
    `).join('');
    return `
      <div class="cl-context-meta">Approval records: ${escapeHtml(String(approvals.count || 0))}</div>
      ${latest ? `<div class="cl-context-row"><div><strong>Latest:</strong> ${escapeHtml(String(latest.status || 'pending'))}</div></div>` : '<div class="cl-empty">No approval record yet.</div>'}
      ${
        budgetStatus
          ? `<div class="cl-context-row ${budgetStatusTone(budgetContext.status)}"><div><strong>Budget widget:</strong> ${escapeHtml(budgetStatus)}</div></div>`
          : ''
      }
      ${budgetRows || ''}
      ${
        budgetContext.requiresDecision
          ? '<div class="cl-context-row cl-context-warning"><div>Decision required: approve override (with justification), request budget adjustment, or reject.</div></div>'
          : ''
      }
      ${
        teams && (teams.channel || teams.state || teams.thread || teams.message_id)
          ? `<div class="cl-context-row"><div><strong>Teams:</strong> ${escapeHtml(String(teams.state || teams.channel || teams.thread || teams.message_id))}</div></div>`
          : ''
      }
      ${payrollCount ? `<div class="cl-context-row"><div><strong>Payroll context:</strong> ${escapeHtml(String(payrollCount))} entries · ${escapeHtml(formatAmount(payrollAmount, item.currency || 'USD'))}</div></div>` : ''}
      ${freshnessSummary ? `<div class="cl-context-row ${freshness.is_stale ? 'cl-context-warning' : ''}">${escapeHtml(freshnessSummary)}</div>` : ''}
      ${previewRows || ''}
    `;
  }

  const erp = contextPayload.erp || {};
  const po = contextPayload.po_match || {};
  const budget = contextPayload.budget || {};
  const poStatus = po.status ? String(po.status).replace(/_/g, ' ') : '';
  const budgetStatus = budget.status ? String(budget.status).replace(/_/g, ' ') : '';
  return `
    <div class="cl-context-meta">Connector available: ${erp.connector_available ? 'Yes' : 'No'}</div>
    <div class="cl-context-row">
      <div><strong>Status:</strong> ${escapeHtml(String(erp.state || item.state || 'unknown'))}</div>
      <div><strong>Reference:</strong> ${escapeHtml(erp.erp_reference || 'N/A')}</div>
    </div>
    ${poStatus ? `<div class="cl-context-row"><div><strong>PO check:</strong> ${escapeHtml(poStatus)}</div></div>` : ''}
    ${budgetStatus ? `<div class="cl-context-row"><div><strong>Budget check:</strong> ${escapeHtml(budgetStatus)}</div></div>` : ''}
    ${freshnessSummary ? `<div class="cl-context-row ${freshness.is_stale ? 'cl-context-warning' : ''}">${escapeHtml(freshnessSummary)}</div>` : ''}
    ${erp.erp_posted_at ? `<div class="cl-context-row"><div>Posted: ${escapeHtml(formatDateTime(erp.erp_posted_at))}</div></div>` : ''}
  `;
}

function setSectionVisibility(sectionId, visible) {
  if (!globalSidebarEl) return;
  const section = globalSidebarEl.querySelector(`#${sectionId}`);
  if (!section) return;
  section.style.display = visible ? '' : 'none';
}

function decodeJwtPayload(token) {
  const raw = String(token || '').trim();
  if (!raw || !raw.includes('.')) return null;
  const parts = raw.split('.');
  if (parts.length < 2) return null;
  try {
    const payload = parts[1]
      .replace(/-/g, '+')
      .replace(/_/g, '/')
      .padEnd(Math.ceil(parts[1].length / 4) * 4, '=');
    const decoded = typeof atob === 'function'
      ? atob(payload)
      : Buffer.from(payload, 'base64').toString('utf8');
    return JSON.parse(decoded);
  } catch (_) {
    return null;
  }
}

function getSidebarUserRole() {
  const token = String(queueManager?.backendAuthToken || '').trim();
  const payload = decodeJwtPayload(token);
  return String(payload?.role || '').trim().toLowerCase();
}

function canViewOpsConsoleLink() {
  return ['admin', 'owner', 'operator'].includes(getSidebarUserRole());
}

function buildBlockedReasons(item, contextPayload, confidencePercent) {
  const reasons = [];
  const exceptionCode = String(item?.exception_code || '').trim();
  const exceptionReason = exceptionCode ? getExceptionReason(exceptionCode) : '';
  if (exceptionCode || exceptionReason) {
    reasons.push({
      label: `Policy: ${(exceptionCode || 'policy_rule').replace(/_/g, ' ')}`,
      detail: exceptionReason || 'Policy validation blocked autonomous progression.',
    });
  }

  const poStatus = String(contextPayload?.po_match?.status || '').trim().toLowerCase();
  if (
    poStatus.includes('missing')
    || poStatus.includes('no_gr')
    || (!item?.po_number && ['validated', 'needs_approval', 'pending_approval', 'ready_to_post'].includes(String(item?.state || '').toLowerCase()))
  ) {
    reasons.push({
      label: 'PO/GR missing',
      detail: 'PO/GR requirements are not satisfied for this invoice.',
    });
  }

  if (Boolean(item?.requires_field_review) || (Number.isFinite(confidencePercent) && confidencePercent < 95)) {
    reasons.push({
      label: 'Confidence below threshold',
      detail: `Current confidence is ${Number.isFinite(confidencePercent) ? `${confidencePercent}%` : 'unknown'} (threshold 95%).`,
    });
  }

  const budgetStatus = String(contextPayload?.budget?.status || item?.budget_status || '').trim().toLowerCase();
  if (['critical', 'exceeded', 'blocked'].includes(budgetStatus)) {
    reasons.push({
      label: `Budget: ${budgetStatus.replace(/_/g, ' ')}`,
      detail: 'Budget policy requires explicit decision before posting.',
    });
  }

  const state = String(item?.state || '').trim().toLowerCase();
  if (['needs_approval', 'pending_approval'].includes(state)) {
    reasons.push({
      label: 'Approval pending',
      detail: 'Waiting for human approval on Slack/Teams before posting.',
    });
  }

  const seen = new Set();
  return reasons.filter((reason) => {
    const key = `${reason.label}|${reason.detail}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 5);
}

function buildEvidenceChecklist(item, contextPayload, linkedSources) {
  const sources = Array.isArray(linkedSources) ? linkedSources : [];
  const emailLinked = Boolean(
    getSourceThreadId(item)
    || getSourceMessageId(item)
    || sources.some((entry) => String(entry?.source_type || '').includes('gmail'))
  );
  const attachmentLinked = Boolean(
    item?.has_attachment
    || sources.some((entry) => String(entry?.source_type || '').includes('attachment'))
    || Number(contextPayload?.email?.source_count || 0) > 0
  );
  const erpLinked = Boolean(item?.erp_reference || contextPayload?.erp?.erp_reference);
  const approvalLinked = Boolean(
    ['approved', 'ready_to_post', 'posted_to_erp', 'closed'].includes(String(item?.state || '').toLowerCase())
    || Number(contextPayload?.approvals?.count || 0) > 0
  );
  return [
    { label: 'Email', ok: emailLinked },
    { label: 'Attachment', ok: attachmentLinked },
    { label: 'ERP link', ok: erpLinked },
    { label: 'Approval', ok: approvalLinked },
  ];
}

function getWorkPrimaryAction(item, contextPayload) {
  const state = String(item?.state || 'received').toLowerCase();
  const approvalsCount = Number(contextPayload?.approvals?.count || 0);
  if (['received', 'validated'].includes(state)) {
    return { id: 'request_approval', label: 'Request approval' };
  }
  if (state === 'needs_info') {
    return { id: 'prepare_info_request', label: 'Prepare info request' };
  }
  if (['needs_approval', 'pending_approval'].includes(state)) {
    return approvalsCount > 0
      ? { id: 'nudge_approver', label: 'Send reminder' }
      : { id: 'send_approval_request', label: 'Send approval request' };
  }
  if (state === 'ready_to_post') {
    return { id: 'preview_erp_post', label: 'Preview ERP post' };
  }
  if (state === 'failed_post') {
    return { id: 'retry_erp_post', label: 'Retry ERP post' };
  }
  if (['posted_to_erp', 'closed'].includes(state)) {
    return { id: 'view_erp_record', label: 'View ERP record' };
  }
  return null;
}

function renderWorkModeThreadContext(context, item) {
  const items = Array.isArray(queueState) ? queueState : [];
  const itemIndex = getPrimaryItemIndex();
  const humanIndex = itemIndex >= 0 ? itemIndex + 1 : 1;
  const state = String(item?.state || 'received').toLowerCase();
  const stateLabel = getStateLabel(state);
  const stateColor = STATE_COLORS[state] || '#0f172a';
  const vendor = item.vendor_name || item.vendor || item.sender || 'Unknown vendor';
  const invoiceNumber = item.invoice_number || 'N/A';
  const dueDate = item.due_date || 'N/A';
  const amount = formatAmount(item.amount, item.currency || 'USD');
  const poNumber = item.po_number || null;
  const linkedSources = getLinkedSources(item);
  const contextPayload = item?.id ? contextState.get(item.id) || null : null;
  const auditEvents = auditState.itemId === item.id && Array.isArray(auditState.events) ? auditState.events : [];
  const confidenceNumber = Number(item.confidence);
  const confidencePercent = Number.isFinite(confidenceNumber)
    ? Math.round(Math.max(0, Math.min(1, confidenceNumber)) * 100)
    : null;
  const blockedReasons = buildBlockedReasons(item, contextPayload, confidencePercent);
  const evidenceChecklist = buildEvidenceChecklist(item, contextPayload, linkedSources);
  const primaryAction = getWorkPrimaryAction(item, contextPayload);
  const canOpenSource = Boolean(getSourceThreadId(item) || getSourceMessageId(item) || item.subject);
  const contextDetailRows = [
    item?.subject ? `<div class="cl-detail-row"><span>Subject</span><span>${escapeHtml(trimText(item.subject, 120))}</span></div>` : '',
    item?.sender ? `<div class="cl-detail-row"><span>Sender</span><span>${escapeHtml(trimText(item.sender, 96))}</span></div>` : '',
    item?.exception_code ? `<div class="cl-detail-row"><span>Exception</span><span>${escapeHtml(String(item.exception_code).replace(/_/g, ' '))}</span></div>` : '',
    Number.isFinite(confidencePercent) ? `<div class="cl-detail-row"><span>Confidence</span><span>${escapeHtml(`${confidencePercent}%`)}</span></div>` : '',
  ].filter(Boolean).join('');
  const auditRows = auditEvents.slice(0, 12).map((event) => {
    const presentation = getWorkAuditPresentation(event, item);
    const eventTime = formatDateTime(event.ts || event.created_at || event.createdAt);
    const detailText = String(presentation.detail || '').trim();
    const shortDetail = trimText(detailText, 140);
    const expandable = detailText.length > shortDetail.length;
    return `
      <div class="cl-audit-row">
        <div class="cl-audit-main">
          <span class="cl-audit-type">${escapeHtml(presentation.title)}</span>
          ${eventTime ? `<span class="cl-audit-time">${escapeHtml(eventTime)}</span>` : ''}
        </div>
        ${
          detailText
            ? expandable
              ? `
                <details class="cl-audit-detail-wrap">
                  <summary class="cl-audit-detail-summary">${escapeHtml(shortDetail)}</summary>
                  <div class="cl-audit-detail">${escapeHtml(detailText)}</div>
                </details>
              `
              : `<div class="cl-audit-detail">${escapeHtml(detailText)}</div>`
            : ''
        }
      </div>
    `;
  }).join('');

  const secondaryActions = [];
  secondaryActions.push(`<button class="cl-btn cl-btn-secondary cl-btn-small" id="cl-open-source-email" aria-label="Open source email"${canOpenSource ? '' : ' disabled'}>Open email</button>`);
  if (['received', 'validated', 'needs_approval', 'pending_approval', 'needs_info'].includes(state)) {
    secondaryActions.push('<button class="cl-btn cl-btn-secondary cl-btn-small" id="cl-secondary-reject" aria-label="Reject invoice">Reject</button>');
  }
  if (['needs_approval', 'pending_approval'].includes(state)) {
    if (primaryAction?.id !== 'send_approval_request') {
      secondaryActions.push('<button class="cl-btn cl-btn-secondary cl-btn-small" id="cl-secondary-send-approval" aria-label="Send approval request">Send approval request</button>');
    }
    if (primaryAction?.id !== 'nudge_approver') {
      secondaryActions.push('<button class="cl-btn cl-btn-secondary cl-btn-small" id="cl-secondary-nudge" aria-label="Send approval reminder">Send reminder</button>');
    }
  }
  if (state === 'ready_to_post') {
    secondaryActions.push('<button class="cl-btn cl-btn-secondary cl-btn-small" id="cl-secondary-post-now" aria-label="Post invoice to ERP">Post to ERP</button>');
  }
  if (canViewOpsConsoleLink()) {
    secondaryActions.push('<button class="cl-btn cl-btn-secondary cl-btn-small" id="cl-open-ops-console" aria-label="Open Admin Ops Console">Open Ops Console</button>');
  }

  context.innerHTML = `
    <div class="cl-thread-card cl-work-surface">
      <div class="cl-navigator">
        <div class="cl-thread-main">Invoice ${escapeHtml(String(humanIndex))} of ${escapeHtml(String(items.length || 1))}</div>
        <div class="cl-nav-buttons">
          <button class="cl-btn cl-btn-secondary cl-nav-btn" id="cl-prev-item" ${itemIndex <= 0 ? 'disabled' : ''}>Prev</button>
          <button class="cl-btn cl-btn-secondary cl-nav-btn" id="cl-next-item" ${itemIndex >= items.length - 1 ? 'disabled' : ''}>Next</button>
        </div>
      </div>
      <div class="cl-thread-header">
        <div class="cl-thread-title">${escapeHtml(vendor)}</div>
        <span class="cl-pill" style="color:${stateColor}; border-color:${stateColor};">${escapeHtml(stateLabel)}</span>
      </div>
      <div class="cl-thread-main">${escapeHtml(amount)} · Invoice ${escapeHtml(invoiceNumber)} · Due ${escapeHtml(dueDate)}${poNumber ? ` · PO ${escapeHtml(poNumber)}` : ' · No PO'}</div>
      ${
        blockedReasons.length
          ? `
            <div class="cl-blocked-reasons">
              ${blockedReasons.map((reason) => `
                <details class="cl-details cl-blocker-item">
                  <summary><span class="cl-risk-chip cl-risk-chip-warning">${escapeHtml(reason.label)}</span></summary>
                  <div class="cl-agent-detail">${escapeHtml(reason.detail)}</div>
                </details>
              `).join('')}
            </div>
          `
          : '<div class="cl-agent-detail">No blocking policy checks on this invoice.</div>'
      }
      ${
        ['needs_approval', 'pending_approval'].includes(state)
          ? '<div class="cl-agent-detail">Agent auto-reminders run at 4h and 24h when approval channels are configured. Use "Send reminder" for immediate follow-up.</div>'
          : ''
      }
      ${
        primaryAction
          ? `<button class="cl-btn cl-btn-primary cl-primary-cta" id="cl-primary-action" data-action="${escapeHtml(primaryAction.id)}" aria-label="${escapeHtml(primaryAction.label)}">${escapeHtml(primaryAction.label)}</button>`
          : '<div class="cl-agent-detail">No primary action required. This item is terminal.</div>'
      }
      <div class="cl-thread-actions">${secondaryActions.join('')}</div>
      <details class="cl-details" aria-label="Evidence checklist">
        <summary aria-label="Expand evidence checklist">Evidence checklist</summary>
        <div class="cl-detail-grid">
          ${evidenceChecklist.map((entry) => `
            <div class="cl-detail-row">
              <span>${entry.ok ? '✅' : '❌'} ${escapeHtml(entry.label)}</span>
              <span>${entry.ok ? 'Linked' : 'Missing'}</span>
            </div>
          `).join('')}
        </div>
      </details>
      <details class="cl-details" aria-label="Context">
        <summary aria-label="Expand context">Context</summary>
        <div class="cl-detail-grid">
          ${contextDetailRows || '<div class="cl-agent-detail">No extra context available.</div>'}
        </div>
      </details>
      <details class="cl-details" aria-label="Audit timeline">
        <summary aria-label="Expand audit timeline">View audit</summary>
        <div class="cl-audit-list">${auditRows || '<div class="cl-empty">No audit events yet.</div>'}</div>
      </details>
    </div>
  `;

  const prevBtn = context.querySelector('#cl-prev-item');
  const nextBtn = context.querySelector('#cl-next-item');
  const openSourceBtn = context.querySelector('#cl-open-source-email');
  const primaryBtn = context.querySelector('#cl-primary-action');
  const rejectBtn = context.querySelector('#cl-secondary-reject');
  const sendApprovalBtn = context.querySelector('#cl-secondary-send-approval');
  const nudgeBtn = context.querySelector('#cl-secondary-nudge');
  const postNowBtn = context.querySelector('#cl-secondary-post-now');
  const openOpsBtn = context.querySelector('#cl-open-ops-console');

  if (prevBtn) {
    prevBtn.addEventListener('click', () => selectItemByOffset(-1));
  }
  if (nextBtn) {
    nextBtn.addEventListener('click', () => selectItemByOffset(1));
  }

  if (openSourceBtn) {
    openSourceBtn.addEventListener('click', () => {
      if (openSourceBtn.disabled || !canOpenSource) {
        showToast('Source email reference unavailable', 'error');
        return;
      }
      if (!openSourceEmail(item)) {
        showToast('Unable to open source email', 'error');
      }
    });
  }

  const runPrimaryAction = async (actionId) => {
    const action = String(actionId || '').trim();
    if (!action) return;

    if (action === 'request_approval' || action === 'send_approval_request') {
      const result = await queueManager.requestApproval(item);
      if (result?.status === 'needs_approval' || result?.status === 'pending_approval') {
        showToast('Approval request sent');
      } else {
        showToast('Unable to route approval', 'error');
      }
      return;
    }

    if (action === 'nudge_approver') {
      const result = await queueManager.nudgeApproval(item);
      if (result?.status === 'nudged') {
        showToast('Approval reminder sent');
      } else {
        showToast('Unable to send reminder', 'error');
      }
      return;
    }

    if (action === 'prepare_info_request') {
      const result = await openNeedsInfoDraftCompose(item);
      if (result?.ok) {
        showToast('Vendor info request draft prepared');
      } else {
        const fallback = await queueManager.prepareVendorFollowup(item, { force: true });
        if (fallback?.status === 'prepared') {
          showToast('Vendor follow-up draft prepared');
        } else {
          showToast('Unable to prepare info request', 'error');
        }
      }
      return;
    }

    if (action === 'preview_erp_post') {
      const sessionPayload = getPrimaryAgentSession();
      const sessionId = String(sessionPayload?.session?.id || '').trim();
      if (!sessionId) {
        showToast('Agent session unavailable. Open Ops Console for retry tooling.', 'error');
        return;
      }
      const preview = await queueManager.dispatchAgentMacro(sessionId, 'post_invoice_to_erp', {
        actorId: 'gmail_user',
        params: {
          workflow_id: item.workflow_id || undefined,
          invoice_number: item.invoice_number || undefined,
          vendor_name: item.vendor_name || item.vendor || undefined,
          amount: item.amount,
          currency: item.currency || undefined,
        },
        dryRun: true,
      });
      if (preview) {
        showToast('ERP post preview is ready');
      } else {
        showToast('Unable to preview ERP posting', 'error');
      }
      return;
    }

    if (action === 'retry_erp_post') {
      const result = await queueManager.retryFailedPost(item);
      if (result?.status === 'ready_to_post' || result?.status === 'posted' || result?.status === 'completed') {
        showToast('ERP retry submitted');
      } else {
        showToast(result?.reason || 'Retry failed', 'error');
      }
      return;
    }

    if (action === 'view_erp_record') {
      const erpUrl = String(contextPayload?.erp?.record_url || item?.erp_record_url || '').trim();
      if (erpUrl) {
        window.open(erpUrl, '_blank', 'noopener,noreferrer');
      } else {
        showToast('ERP reference available, but no direct record URL is linked.', 'error');
      }
      return;
    }
  };

  if (primaryBtn) {
    primaryBtn.addEventListener('click', async () => {
      primaryBtn.disabled = true;
      await runPrimaryAction(primaryBtn.getAttribute('data-action') || '');
      primaryBtn.disabled = false;
      await queueManager.refreshQueue();
      await refreshAuditTrail(true);
    });
  }

  if (sendApprovalBtn) {
    sendApprovalBtn.addEventListener('click', async () => {
      await runPrimaryAction('send_approval_request');
      await queueManager.refreshQueue();
      await refreshAuditTrail(true);
    });
  }
  if (nudgeBtn) {
    nudgeBtn.addEventListener('click', async () => {
      await runPrimaryAction('nudge_approver');
      await refreshAuditTrail(true);
    });
  }
  if (postNowBtn) {
    postNowBtn.addEventListener('click', async () => {
      postNowBtn.disabled = true;
      const result = await queueManager.approveAndPost(item, { override: false });
      postNowBtn.disabled = false;
      if (result?.status === 'posted' || result?.status === 'approved' || result?.status === 'posted_to_erp') {
        showToast('Invoice posted to ERP');
      } else {
        showToast(result?.reason || 'ERP posting failed', 'error');
      }
      await queueManager.refreshQueue();
      await refreshAuditTrail(true);
    });
  }
  if (rejectBtn) {
    rejectBtn.addEventListener('click', async () => {
      const reason = await openReasonSheet('reject', {
        title: 'Reject invoice',
        label: 'Rejection reason',
        placeholder: 'Reason for rejection',
        confirmLabel: 'Reject',
        required: true,
      });
      if (!reason) return;
      window.dispatchEvent(new CustomEvent('clearledgr:reject-invoice', {
        detail: { emailId: item.id || item.thread_id, reason },
      }));
    });
  }
  if (openOpsBtn) {
    openOpsBtn.addEventListener('click', () => {
      const backendBase = String(queueManager?.runtimeConfig?.backendUrl || '').replace(/\/+$/, '');
      const org = encodeURIComponent(String(queueManager?.runtimeConfig?.organizationId || 'default'));
      const opsUrl = backendBase ? `${backendBase}/console?org=${org}&page=ops` : `/console?org=${org}&page=ops`;
      window.open(opsUrl, '_blank', 'noopener,noreferrer');
    });
  }

  return true;
}

function isOpsSidebarMode() {
  return false;
}

function renderThreadContext() {
  if (!globalSidebarEl) return;
  const context = globalSidebarEl.querySelector('#cl-thread-context');
  if (!context) return;
  const item = getPrimaryItem();
  if (!item) {
    context.innerHTML = '';
    setSectionVisibility('cl-section-current', false);
    return;
  }
  setSectionVisibility('cl-section-current', true);

  if (item?.id && !(contextUiState.loading && contextUiState.itemId === item.id)) {
    void ensureItemContext(item, { refresh: false });
  }

  renderWorkModeThreadContext(context, item);
}

function renderAgentActions() {
  // Removed legacy mixed-mode side sections from Gmail Work runtime.
}

async function refreshAuditTrail(force = false) {
  const item = getPrimaryItem();
  if (!item?.id || !queueManager?.fetchAuditTrail) {
    auditState = {
      itemId: item?.id || null,
      loading: false,
      events: []
    };
    renderThreadContext();
    return [];
  }

  if (!force && auditState.itemId === item.id && Array.isArray(auditState.events) && auditState.events.length > 0) {
    return auditState.events;
  }

  auditState = {
    itemId: item.id,
    loading: true,
    events: Array.isArray(auditState.events) && auditState.itemId === item.id ? auditState.events : []
  };
  renderThreadContext();

  try {
    const events = await queueManager.fetchAuditTrail(item);
    if (!getPrimaryItem() || getPrimaryItem().id !== item.id) {
      return [];
    }
    auditState = {
      itemId: item.id,
      loading: false,
      events: Array.isArray(events) ? events : []
    };
  } catch (_) {
    auditState = {
      itemId: item.id,
      loading: false,
      events: []
    };
  }

  renderThreadContext();
  return auditState.events;
}

function renderSidebar() {
  renderScanStatus();
  renderThreadContext();
  renderQueueList();
  renderAgentActions();
}

function renderSidebarFor(sidebarEl) {
  if (!sidebarEl) return;
  activateSidebarContext(sidebarEl);
  const activeItem = getPrimaryItem();
  if (activeItem?.id) {
    writeLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID, activeItem.id);
  }
  renderSidebar();
}

function renderAllSidebars() {
  renderSidebarFor(workSidebarEl);
  if (workSidebarEl) {
    activateSidebarContext(workSidebarEl);
  }
  void refreshAuditTrail();
}

function initializeSidebar() {
  if (workSidebarEl) return;
  const container = document.createElement('div');
  container.className = 'cl-sidebar';
  container.innerHTML = `
    <style>
      .cl-sidebar {
        --cl-bg: #ffffff;
        --cl-surface: #f8fafc;
        --cl-card: #ffffff;
        --cl-border: #e5e7eb;
        --cl-text: #111827;
        --cl-muted: #6b7280;
        --cl-accent: #0f766e;
        --cl-accent-soft: #ecfdf5;
        font-family: 'Google Sans', Roboto, sans-serif;
        color: var(--cl-text);
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 10px;
        height: 100%;
        background: var(--cl-bg);
        position: relative;
      }
      .cl-header {
        display: flex;
        flex-direction: column;
        gap: 2px;
        margin-bottom: 2px;
      }
      .cl-title {
        font-size: 14px;
        font-weight: 600;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .cl-logo {
        width: 16px;
        height: 16px;
        display: inline-block;
      }
      .cl-subtitle {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-toast {
        font-size: 11px;
        color: var(--cl-text);
        background: #f3f4f6;
        border: 1px solid var(--cl-border);
        border-radius: 6px;
        padding: 5px 8px;
        display: none;
      }
      .cl-toast[data-tone="error"] {
        color: #991b1b;
        border-color: #fecaca;
        background: #fef2f2;
      }
      .cl-action-dialog {
        position: absolute;
        inset: 0;
        display: none;
        align-items: center;
        justify-content: center;
        z-index: 24;
        background: rgba(15, 23, 42, 0.5);
        padding: 12px;
      }
      .cl-action-dialog-card {
        width: 100%;
        max-width: 320px;
        border-radius: 10px;
        border: 1px solid var(--cl-border);
        background: #ffffff;
        box-shadow: 0 12px 28px rgba(15, 23, 42, 0.2);
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .cl-action-dialog-title {
        font-size: 12px;
        font-weight: 700;
        color: var(--cl-text);
      }
      .cl-action-dialog-label {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-action-dialog-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .cl-action-chip {
        border: 1px solid var(--cl-border);
        background: #f8fafc;
        color: #334155;
        border-radius: 999px;
        font-size: 10px;
        font-weight: 600;
        padding: 4px 8px;
        cursor: pointer;
      }
      .cl-action-chip:hover {
        border-color: var(--cl-accent);
        color: var(--cl-accent);
      }
      .cl-action-chip:focus-visible {
        outline: 2px solid #0f766e;
        outline-offset: 2px;
      }
      .cl-action-dialog-input {
        width: 100%;
        min-height: 34px;
        border-radius: 8px;
        border: 1px solid var(--cl-border);
        padding: 8px 10px;
        font-size: 12px;
        color: var(--cl-text);
        background: #ffffff;
      }
      .cl-action-dialog-input:focus {
        border-color: var(--cl-accent);
        outline: none;
        box-shadow: 0 0 0 2px rgba(15, 118, 110, 0.16);
      }
      .cl-action-dialog-hint {
        font-size: 10px;
        color: var(--cl-muted);
        line-height: 1.3;
      }
      .cl-action-dialog-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
      }
      .cl-section {
        background: var(--cl-surface);
        border: 1px solid var(--cl-border);
        border-radius: 10px;
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .cl-section-title {
        font-size: 11px;
        font-weight: 600;
        color: var(--cl-muted);
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }
      .cl-thread-card {
        background: var(--cl-card);
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 5px;
      }
      .cl-navigator {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-bottom: 2px;
      }
      .cl-nav-buttons {
        display: flex;
        gap: 6px;
      }
      .cl-nav-btn {
        min-width: 56px;
      }
      .cl-thread-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .cl-thread-title {
        font-weight: 600;
        font-size: 13px;
      }
      .cl-thread-main {
        font-size: 12px;
        color: var(--cl-text);
      }
      .cl-thread-sub {
        font-size: 11px;
        color: #4b5563;
      }
      .cl-operator-brief {
        border: 1px solid #d1d5db;
        border-radius: 8px;
        background: #f8fafc;
        display: flex;
        flex-direction: column;
      }
      .cl-operator-brief[data-tone="warning"] {
        border-color: #fcd34d;
        background: #fffbeb;
      }
      .cl-operator-brief[data-tone="good"] {
        border-color: #86efac;
        background: #f0fdf4;
      }
      .cl-operator-brief-row {
        display: flex;
        flex-direction: column;
        gap: 2px;
        padding: 7px 8px;
      }
      .cl-operator-brief-row + .cl-operator-brief-row {
        border-top: 1px dashed #d1d5db;
      }
      .cl-operator-brief-label {
        font-size: 10px;
        font-weight: 700;
        color: #334155;
        text-transform: uppercase;
        letter-spacing: 0.03em;
      }
      .cl-operator-brief-text {
        font-size: 11px;
        color: #1f2937;
        line-height: 1.35;
      }
      .cl-operator-brief-outcome {
        margin-top: 1px;
        font-size: 10px;
        color: #475569;
      }
      .cl-decision-banner {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 8px;
        background: #ffffff;
      }
      .cl-decision-title {
        font-size: 11px;
        font-weight: 700;
        color: #111827;
      }
      .cl-decision-detail {
        margin-top: 2px;
        font-size: 10px;
        color: #4b5563;
      }
      .cl-decision-good {
        border-color: #86efac;
        background: #f0fdf4;
      }
      .cl-decision-warning {
        border-color: #fcd34d;
        background: #fffbeb;
      }
      .cl-decision-neutral {
        border-color: #d1d5db;
        background: #f9fafb;
      }
      .cl-risk-row {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .cl-risk-chip {
        font-size: 10px;
        border: 1px solid #d1d5db;
        border-radius: 999px;
        padding: 2px 8px;
        color: #374151;
        background: #f9fafb;
      }
      .cl-risk-chip-warning {
        border-color: #f59e0b;
        color: #92400e;
        background: #fffbeb;
      }
      .cl-agent-reasoning-banner {
        margin: 6px 0 2px;
        padding: 7px 10px;
        background: #f0f9ff;
        border-left: 3px solid #3b82f6;
        border-radius: 4px;
        font-size: 11px;
        color: #1e3a5f;
        line-height: 1.45;
      }
      .cl-agent-label {
        font-weight: 600;
        color: #1d4ed8;
        margin-right: 3px;
      }
      .cl-agent-risks {
        margin-top: 4px;
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
      }
      .cl-discount-banner {
        margin: 6px 0 2px;
        padding: 7px 10px;
        background: #f0fdf4;
        border-left: 3px solid #16a34a;
        border-radius: 4px;
        font-size: 11px;
        color: #14532d;
        line-height: 1.45;
      }
      .cl-discount-label {
        font-weight: 600;
        color: #15803d;
        margin-right: 3px;
      }
      .cl-needs-info-banner {
        margin: 6px 0 2px;
        padding: 7px 10px;
        background: #fffbeb;
        border-left: 3px solid #f59e0b;
        border-radius: 4px;
        font-size: 11px;
        color: #78350f;
        line-height: 1.45;
      }
      .cl-needs-info-label {
        font-weight: 600;
        color: #b45309;
        margin-right: 3px;
      }
      .cl-needs-info-meta {
        margin-top: 4px;
        color: #92400e;
      }
      .cl-draft-link {
        display: inline-block;
        margin-left: 8px;
        padding: 2px 7px;
        background: #fef3c7;
        border: 1px solid #f59e0b;
        border-radius: 3px;
        color: #92400e;
        font-size: 10px;
        font-weight: 600;
        text-decoration: none;
      }
      .cl-draft-link:hover {
        background: #fde68a;
      }
      .cl-thread-meta {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-source-subject {
        line-height: 1.35;
      }
      .cl-confidence-section {
        margin: 6px 0;
        padding: 8px;
        background: #f9fafb;
        border: 1px solid var(--cl-border);
        border-radius: 8px;
      }
      .cl-confidence-bar {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 11px;
      }
      .cl-confidence-label {
        color: var(--cl-muted);
        font-weight: 500;
      }
      .cl-confidence-value {
        font-weight: 600;
        font-size: 13px;
      }
      .cl-conf-high { color: #16a34a; }
      .cl-conf-med { color: #ca8a04; }
      .cl-conf-low { color: #dc2626; }
      .cl-confidence-threshold {
        margin-left: auto;
        color: var(--cl-muted);
        font-size: 10px;
      }
      .cl-mismatch {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 4px;
        padding: 4px 6px;
        border-radius: 4px;
        font-size: 10px;
      }
      .cl-mismatch-high {
        background: #fef2f2;
        border: 1px solid #fecaca;
        color: #991b1b;
      }
      .cl-mismatch-medium {
        background: #fffbeb;
        border: 1px solid #fed7aa;
        color: #92400e;
      }
      .cl-mismatch-low {
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        color: #166534;
      }
      .cl-mismatch-field {
        font-weight: 600;
        text-transform: capitalize;
      }
      /* Receipt notice banner */
      .cl-receipt-notice {
        font-size: 11px;
        color: #15803d;
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        border-radius: 6px;
        padding: 6px 8px;
        margin: 2px 0 4px;
        display: flex;
        align-items: flex-start;
        gap: 6px;
        line-height: 1.4;
      }
      .cl-receipt-icon {
        font-size: 13px;
        flex-shrink: 0;
      }
      /* Exception root-cause one-liner */
      .cl-exception-reason {
        font-size: 11px;
        color: #b45309;
        background: #fffbeb;
        border: 1px solid #fde68a;
        border-radius: 6px;
        padding: 4px 8px;
        margin: 2px 0 4px;
      }
      /* Per-field confidence collapsible */
      .cl-field-conf-details {
        margin-top: 6px;
      }
      .cl-field-conf-summary {
        font-size: 10px;
        color: var(--cl-muted);
        cursor: pointer;
        user-select: none;
      }
      .cl-field-conf-grid {
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 2px 8px;
        margin-top: 4px;
        font-size: 11px;
      }
      .cl-field-conf-row {
        display: contents;
      }
      .cl-field-conf-label {
        color: var(--cl-muted);
      }
      .cl-field-conf-value {
        font-weight: 600;
        text-align: right;
      }
      .cl-btn-approve {
        background: #16a34a !important;
        color: white !important;
        border-color: #16a34a !important;
      }
      .cl-btn-review {
        background: #ca8a04 !important;
        color: white !important;
        border-color: #ca8a04 !important;
      }
      .cl-btn-small {
        font-size: 10px !important;
        padding: 3px 6px !important;
      }
      .cl-thread-actions {
        display: flex;
        gap: 6px;
        margin-top: 4px;
        flex-wrap: wrap;
      }
      .cl-primary-cta {
        margin-top: 6px;
        width: 100%;
      }
      .cl-blocked-reasons {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-blocker-item {
        border-top: 0;
        margin-top: 0;
        padding-top: 0;
      }
      .cl-work-surface .cl-details {
        margin-top: 2px;
      }
      .cl-source-list {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-top: 6px;
      }
      .cl-source-row {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        background: #f9fafb;
        padding: 7px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-source-main {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 11px;
        color: var(--cl-text);
      }
      .cl-source-sub {
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-context-tabs {
        margin-top: 8px;
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
      }
      .cl-context-tab {
        border: 1px solid var(--cl-border);
        border-radius: 999px;
        background: #ffffff;
        color: #374151;
        padding: 4px 8px;
        font-size: 10px;
        cursor: pointer;
      }
      .cl-context-tab.active {
        border-color: #0f766e;
        color: #0f766e;
        background: #ecfdf5;
      }
      .cl-context-refresh {
        margin-left: auto;
        flex: 0;
        font-size: 10px;
        padding: 4px 8px;
      }
      .cl-context-body {
        margin-top: 8px;
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        background: #f9fafb;
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-context-row {
        font-size: 10px;
        color: #374151;
        line-height: 1.35;
      }
      .cl-context-row-browser {
        border: 1px solid #e5e7eb;
        border-radius: 6px;
        background: #ffffff;
        padding: 6px;
      }
      .cl-context-row-browser-main {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
      }
      .cl-context-row-browser-status {
        font-size: 9px;
        text-transform: uppercase;
        color: #475569;
      }
      .cl-context-row-browser-status[data-tone="success"] {
        color: #166534;
      }
      .cl-context-row-browser-status[data-tone="error"] {
        color: #b91c1c;
      }
      .cl-context-row-browser-tag {
        width: fit-content;
        font-size: 9px;
        color: #1d4ed8;
        background: #dbeafe;
        border: 1px solid #bfdbfe;
        border-radius: 999px;
        padding: 1px 5px;
        text-transform: uppercase;
        letter-spacing: 0.02em;
        font-weight: 600;
      }
      .cl-context-meta {
        font-size: 10px;
        color: #4b5563;
        font-weight: 600;
      }
      .cl-context-warning {
        color: #b45309;
        font-weight: 600;
      }
      .cl-fallback-banner {
        margin-top: 8px;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        background: #f8fafc;
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-fallback-banner[data-tone="info"] {
        border-color: #93c5fd;
        background: #eff6ff;
      }
      .cl-fallback-banner[data-tone="warning"] {
        border-color: #fbbf24;
        background: #fffbeb;
      }
      .cl-fallback-banner[data-tone="error"] {
        border-color: #fca5a5;
        background: #fef2f2;
      }
      .cl-fallback-banner[data-tone="success"] {
        border-color: #86efac;
        background: #f0fdf4;
      }
      .cl-fallback-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
        flex-wrap: wrap;
      }
      .cl-fallback-badge {
        font-size: 9px;
        text-transform: uppercase;
        letter-spacing: 0.02em;
        font-weight: 700;
        color: #334155;
        border: 1px solid #cbd5e1;
        border-radius: 999px;
        padding: 1px 6px;
        background: #ffffff;
      }
      .cl-fallback-stage {
        font-size: 9px;
        text-transform: uppercase;
        font-weight: 700;
        color: #475569;
      }
      .cl-fallback-title {
        font-size: 11px;
        font-weight: 600;
        color: #1f2937;
        line-height: 1.3;
      }
      .cl-fallback-progress {
        font-size: 10px;
        color: #334155;
        font-weight: 600;
      }
      .cl-fallback-detail {
        font-size: 10px;
        color: #374151;
        line-height: 1.35;
      }
      .cl-fallback-trust-note {
        font-size: 10px;
        color: #334155;
        border-left: 2px solid #cbd5e1;
        padding-left: 6px;
        line-height: 1.35;
      }
      .cl-fallback-stage-list {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
      }
      .cl-fallback-stage-chip {
        font-size: 9px;
        color: #334155;
        background: rgba(255, 255, 255, 0.8);
        border: 1px solid #cbd5e1;
        border-radius: 999px;
        padding: 1px 5px;
      }
      .cl-fallback-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        font-size: 9px;
        color: #475569;
      }
      .cl-fallback-meta span {
        background: rgba(255, 255, 255, 0.7);
        border: 1px solid #e2e8f0;
        border-radius: 999px;
        padding: 1px 5px;
      }
      .cl-conflict-panel {
        border: 1px solid #fbbf24;
        border-radius: 8px;
        background: #fffbeb;
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-select {
        width: 100%;
        border: 1px solid var(--cl-border);
        border-radius: 6px;
        padding: 6px;
        font-size: 11px;
        background: #ffffff;
        color: var(--cl-text);
      }
      .cl-btn {
        flex: 1;
        border-radius: 6px;
        border: 1px solid #059669;
        background: #059669;
        color: #ffffff;
        font-size: 11px;
        padding: 6px 8px;
        cursor: pointer;
      }
      .cl-btn:disabled {
        background: #e5e7eb;
        border-color: #e5e7eb;
        color: #9ca3af;
        cursor: not-allowed;
      }
      .cl-btn:focus-visible {
        outline: 2px solid #0f766e;
        outline-offset: 2px;
      }
      .cl-btn-secondary {
        background: #ffffff;
        color: var(--cl-text);
        border-color: #d1d5db;
      }
      .cl-pill {
        font-size: 10px;
        text-transform: uppercase;
        border: 1px solid currentColor;
        border-radius: 999px;
        padding: 2px 7px;
        font-weight: 600;
        white-space: nowrap;
      }
      .cl-queue {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-queue-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .cl-queue-count {
        font-size: 11px;
        color: #6b7280;
      }
      .cl-queue-list {
        display: flex;
        flex-direction: column;
        gap: 6px;
        max-height: 220px;
        overflow-y: auto;
        padding-right: 2px;
      }
      .cl-queue-row {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 8px;
        background: var(--cl-card);
        display: flex;
        flex-direction: column;
        gap: 3px;
        cursor: pointer;
      }
      .cl-queue-row-active {
        border-color: var(--cl-accent);
        background: var(--cl-accent-soft);
      }
      .cl-queue-row-main {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .cl-pill-queue {
        font-size: 9px;
        padding: 1px 6px;
      }
      .cl-queue-row-meta {
        display: flex;
        align-items: baseline;
        gap: 8px;
        flex-wrap: wrap;
      }
      .cl-queue-vendor {
        font-size: 12px;
        font-weight: 600;
        color: var(--cl-text);
      }
      .cl-queue-amount {
        font-size: 11px;
        color: #374151;
      }
      .cl-queue-subject {
        font-size: 11px;
        color: var(--cl-text);
        line-height: 1.35;
      }
      .cl-queue-meta {
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-empty {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-audit-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .cl-audit-row {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 10px;
        background: var(--cl-card);
        display: flex;
        flex-direction: column;
        gap: 6px;
        overflow: hidden;
      }
      .cl-audit-main {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 8px;
        flex-wrap: wrap;
      }
      .cl-audit-type {
        font-size: 12px;
        font-weight: 600;
        color: var(--cl-text);
        flex: 1;
        min-width: 0;
      }
      .cl-audit-time {
        font-size: 11px;
        color: var(--cl-muted);
        white-space: nowrap;
      }
      .cl-audit-detail {
        font-size: 12px;
        color: #4b5563;
        line-height: 1.4;
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-word;
      }
      .cl-audit-detail-wrap {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-audit-detail-summary {
        list-style: none;
        cursor: pointer;
        color: #4b5563;
        font-size: 12px;
        line-height: 1.4;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }
      .cl-audit-detail-summary::-webkit-details-marker {
        display: none;
      }
      .cl-audit-detail-summary:focus-visible {
        outline: 2px solid #0f766e;
        outline-offset: 2px;
      }
      .cl-scan-status {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-scan-status[data-tone="error"] {
        color: #b91c1c;
      }
      .cl-inline-actions {
        display: none;
        margin-top: 8px;
      }
      .cl-agent-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: center;
      }
      .cl-agent-chip {
        font-size: 9px;
        text-transform: uppercase;
        border: 1px solid #0f766e;
        border-radius: 999px;
        padding: 2px 7px;
        font-weight: 600;
      }
      .cl-agent-count {
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-agent-list {
        display: flex;
        flex-direction: column;
        gap: 5px;
        margin-top: 8px;
      }
      .cl-agent-timeline {
        margin-top: 8px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .cl-agent-group {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-agent-group-title {
        font-size: 10px;
        font-weight: 700;
        color: #334155;
        text-transform: uppercase;
        letter-spacing: 0.03em;
      }
      .cl-agent-timeline-empty {
        font-size: 10px;
        color: var(--cl-muted);
        border: 1px dashed var(--cl-border);
        border-radius: 8px;
        padding: 8px;
        background: #f8fafc;
      }
      .cl-agent-row {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 8px;
        background: var(--cl-card);
        display: flex;
        flex-direction: column;
        gap: 5px;
      }
      .cl-agent-row-timeline {
        padding: 7px;
        gap: 4px;
      }
      .cl-agent-row-timeline[data-source="audit"] {
        background: #f8fafc;
      }
      .cl-agent-row-timeline[data-kind="browser_fallback"] {
        border-color: #bfdbfe;
        background: #f8fbff;
      }
      .cl-agent-row-main {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .cl-agent-tool {
        font-size: 11px;
        font-weight: 600;
        color: var(--cl-text);
      }
      .cl-agent-status {
        font-size: 9px;
        color: var(--cl-muted);
        text-transform: uppercase;
      }
      .cl-agent-stage-chip {
        font-size: 9px;
        color: #1d4ed8;
        background: #dbeafe;
        border: 1px solid #bfdbfe;
        border-radius: 999px;
        padding: 1px 5px;
        font-weight: 700;
        text-transform: uppercase;
      }
      .cl-agent-detail {
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-agent-timeline-meta {
        display: flex;
        align-items: center;
        gap: 6px;
        flex-wrap: wrap;
      }
      .cl-agent-source {
        font-size: 9px;
        text-transform: uppercase;
        color: #334155;
        background: #e2e8f0;
        border-radius: 999px;
        padding: 1px 6px;
        font-weight: 600;
      }
      .cl-agent-time {
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-agent-preview {
        margin-top: 6px;
        border: 1px dashed #d1d5db;
        border-radius: 8px;
        padding: 7px;
        background: #f8fafc;
      }
      .cl-agent-preview-title {
        font-size: 10px;
        font-weight: 600;
        color: #1f2937;
        margin-bottom: 4px;
      }
      .cl-agent-preview-meta {
        margin-top: 4px;
        font-size: 10px;
        color: #4b5563;
      }
      .cl-agent-warning-list {
        margin: 6px 0 0;
        padding-left: 16px;
        font-size: 10px;
        color: #b45309;
      }
      .cl-agent-detail-error {
        color: #b91c1c;
      }
      .cl-agent-actions-bar {
        margin-top: 8px;
        display: flex;
        gap: 8px;
      }
      .cl-agent-command-bar {
        margin-top: 8px;
        display: flex;
        gap: 8px;
        align-items: center;
      }
      .cl-agent-command-input {
        flex: 1;
        min-width: 0;
        border: 1px solid var(--cl-border);
        border-radius: 6px;
        padding: 6px 8px;
        font-size: 11px;
        background: #ffffff;
        color: var(--cl-text);
      }
      .cl-agent-command-input:focus {
        outline: 2px solid rgba(15, 118, 110, 0.18);
        border-color: #0f766e;
      }
      .cl-agent-command-submit {
        flex: 0 0 auto;
        min-width: 56px;
      }
      .cl-agent-command-hint {
        margin-top: 6px;
        font-size: 10px;
        color: var(--cl-muted);
        line-height: 1.35;
      }
      .cl-agent-share-target-row {
        margin-top: 8px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-agent-share-target-label {
        font-size: 10px;
        color: var(--cl-muted);
        font-weight: 600;
      }
      .cl-agent-share-target {
        font-size: 11px;
      }
      .cl-agent-intent {
        display: inline-flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
        text-align: left;
      }
      .cl-agent-intent-recommended {
        border-color: #0f766e;
        box-shadow: inset 0 0 0 1px rgba(15, 118, 110, 0.15);
      }
      .cl-agent-intent-badge {
        font-size: 9px;
        text-transform: uppercase;
        color: #065f46;
        background: #d1fae5;
        border-radius: 999px;
        padding: 1px 6px;
        font-weight: 700;
        white-space: nowrap;
      }
      .cl-details {
        border-top: 1px dashed var(--cl-border);
        margin-top: 4px;
        padding-top: 4px;
      }
      .cl-details summary {
        list-style: none;
        cursor: pointer;
        font-size: 12px;
        font-weight: 500;
        color: var(--cl-muted);
      }
      .cl-details summary::-webkit-details-marker {
        display: none;
      }
      .cl-details summary:focus-visible {
        outline: 2px solid #0f766e;
        outline-offset: 2px;
      }
      .cl-detail-grid {
        display: flex;
        flex-direction: column;
        gap: 4px;
        margin-top: 6px;
      }
      .cl-detail-row {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-detail-row span:last-child {
        color: var(--cl-text);
      }
      @media (prefers-reduced-motion: reduce) {
        .cl-sidebar *,
        .cl-thread-card *,
        .cl-action-dialog * {
          animation: none !important;
          transition: none !important;
        }
        html {
          scroll-behavior: auto;
        }
      }
    </style>
    <div class="cl-header">
      <div class="cl-title">
        ${getAssetUrl(LOGO_PATH) ? `<img class="cl-logo" src="${getAssetUrl(LOGO_PATH)}" alt="Clearledgr" />` : ''}
        Clearledgr AP
      </div>
      <div class="cl-subtitle" id="cl-subtitle">Embedded accounts payable execution</div>
    </div>
    <div id="cl-toast" class="cl-toast"></div>
    <div id="cl-action-dialog" class="cl-action-dialog" aria-hidden="true">
      <div class="cl-action-dialog-card" role="dialog" aria-modal="true" aria-labelledby="cl-action-dialog-title" aria-describedby="cl-action-dialog-hint">
        <div class="cl-action-dialog-title" id="cl-action-dialog-title">Action required</div>
        <label class="cl-action-dialog-label" id="cl-action-dialog-label" for="cl-action-dialog-input">Reason</label>
        <div class="cl-action-dialog-chips"></div>
        <input id="cl-action-dialog-input" class="cl-action-dialog-input" type="text" aria-labelledby="cl-action-dialog-label" />
        <div class="cl-action-dialog-hint" id="cl-action-dialog-hint">A reason is required for this action.</div>
        <div class="cl-action-dialog-actions">
          <button class="cl-btn cl-btn-secondary cl-action-dialog-cancel">Cancel</button>
          <button class="cl-btn cl-action-dialog-confirm">Confirm</button>
        </div>
      </div>
    </div>
    <div class="cl-section">
      <div id="cl-scan-status" class="cl-scan-status"></div>
      <div id="cl-auth-actions" class="cl-inline-actions">
        <button class="cl-btn cl-btn-secondary" id="cl-authorize-gmail">Authorize Gmail</button>
        <button class="cl-btn cl-btn-secondary" id="cl-open-admin-auth">Open Integrations</button>
      </div>
    </div>
    <div class="cl-section" id="cl-section-current">
      <div class="cl-section-title">Decision</div>
      <div id="cl-thread-context"></div>
    </div>
  `;

  const logoUrl = getAssetUrl(LOGO_PATH);

  const configureSidebarPanel = (sidebarEl) => {
    if (!sidebarEl) return;
    bindSidebarContext(sidebarEl);

    const logoImg = sidebarEl.querySelector('.cl-logo');
    if (logoImg) {
      logoImg.addEventListener('error', () => {
        logoImg.remove();
      });
    }

    const authorizeButton = sidebarEl.querySelector('#cl-authorize-gmail');
    const openAdminAuthButton = sidebarEl.querySelector('#cl-open-admin-auth');

    if (authorizeButton) {
      authorizeButton.addEventListener('click', async () => {
        authorizeButton.disabled = true;
        const result = await queueManager.authorizeGmailNow();
        if (result?.success) {
          showToast('Gmail authorized. Autopilot is resuming.', 'success');
          await queueManager.refreshQueue();
        } else {
          const authMessage = typeof queueManager?.describeAuthResult === 'function'
            ? queueManager.describeAuthResult(result)
            : { toast: `Authorization failed: ${String(result?.error || 'authorization_failed')}`, severity: 'error' };
          showToast(authMessage.toast, authMessage.severity || 'error');
        }
        authorizeButton.disabled = false;
      });
    }
    if (openAdminAuthButton) {
      openAdminAuthButton.addEventListener('click', () => {
        const backendBase = String(queueManager?.runtimeConfig?.backendUrl || '').replace(/\/+$/, '');
        const org = encodeURIComponent(String(queueManager?.runtimeConfig?.organizationId || 'default'));
        const integrationsUrl = backendBase
          ? `${backendBase}/console?org=${org}&page=integrations`
          : `/console?org=${org}&page=integrations`;
        window.open(integrationsUrl, '_blank', 'noopener,noreferrer');
      });
    }

  };

  workSidebarEl = container;
  globalSidebarEl = workSidebarEl;

  configureSidebarPanel(workSidebarEl);

  sdk.Global.addSidebarContentPanel({
    title: 'Clearledgr AP',
    iconUrl: logoUrl || null,
    el: workSidebarEl,
    hideTitleBar: false
  });

  const restoredActiveItemId = readLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID);
  if (restoredActiveItemId) {
    selectedItemId = restoredActiveItemId;
  }

  renderAllSidebars();
}

function renderScanStatus() {
  if (!globalSidebarEl) return;
  const statusEl = globalSidebarEl.querySelector('#cl-scan-status');
  const authActionsEl = globalSidebarEl.querySelector('#cl-auth-actions');
  const authorizeButton = globalSidebarEl.querySelector('#cl-authorize-gmail');
  const openAdminAuthButton = globalSidebarEl.querySelector('#cl-open-admin-auth');
  if (!statusEl) return;
  if (authActionsEl) authActionsEl.style.display = 'none';
  if (authorizeButton) authorizeButton.style.display = 'none';
  if (openAdminAuthButton) openAdminAuthButton.style.display = 'none';

  const state = scanStatus?.state || 'idle';
  statusEl.dataset.tone = '';
  if (state === 'initializing') {
    statusEl.textContent = 'Preparing inbox monitor.';
    statusEl.style.display = 'block';
    return;
  }

  if (state === 'scanning') {
    statusEl.textContent = 'Scanning inbox for invoices.';
    statusEl.style.display = 'block';
    return;
  }

  if (state === 'auth_required') {
    const inlineAuthEnabled = String(queueManager?.runtimeConfig?.authEntryMode || '').toLowerCase() === 'inline';
    statusEl.textContent = inlineAuthEnabled
      ? 'Connect Gmail to resume invoice monitoring.'
      : 'Gmail connection required. Connect Gmail in Admin Console.';
    statusEl.style.display = 'block';
    if (authActionsEl) authActionsEl.style.display = 'block';
    if (inlineAuthEnabled) {
      if (authorizeButton) authorizeButton.style.display = 'inline-flex';
    } else if (openAdminAuthButton) {
      openAdminAuthButton.style.display = 'inline-flex';
    }
    return;
  }

  if (state === 'blocked') {
    if ((scanStatus?.error || '') === 'temporal_unavailable') {
      statusEl.textContent = 'Automation engine is unavailable.';
    } else {
      statusEl.textContent = 'Setup required before invoice monitoring can run.';
    }
    statusEl.dataset.tone = 'error';
    statusEl.style.display = 'block';
    return;
  }

  if (state === 'error') {
    const errorCode = String(scanStatus?.error || '');
    const backendDown = errorCode.includes('backend');
    if (backendDown) {
      statusEl.textContent = 'Cannot sync: backend is unreachable.';
    } else if (errorCode.includes('temporal')) {
      statusEl.textContent = 'Cannot process invoices: automation engine unavailable.';
    } else if (errorCode.includes('processing')) {
      const failedCount = Number(scanStatus?.failedCount || 0);
      statusEl.textContent = failedCount > 0
        ? `${failedCount} email(s) failed to process. Retrying automatically.`
        : 'Some emails failed to process. Retrying automatically.';
    } else {
      statusEl.textContent = 'Inbox sync issue. Retrying automatically.';
    }
    statusEl.dataset.tone = 'error';
    statusEl.style.display = 'block';
    return;
  }

  const lastScan = scanStatus?.lastScanAt ? new Date(scanStatus.lastScanAt) : null;
  if (lastScan) {
    statusEl.textContent = `Monitoring active. Last scan ${lastScan.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}.`;
  } else {
    statusEl.textContent = 'Monitoring active.';
  }
  statusEl.style.display = 'block';
}

function registerThreadHandler() {
  sdk.Conversations.registerThreadViewHandler((threadView) => {
    const getId = async () => {
      if (typeof threadView.getThreadIDAsync === 'function') {
        return await threadView.getThreadIDAsync();
      }
      return null;
    };

    getId()
      .then((threadId) => {
        currentThreadId = threadId;
        const threadItem = findItemByThreadId(threadId);
        if (threadItem?.id) {
          selectedItemId = threadItem.id;
          writeLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID, selectedItemId);
        }
        renderAllSidebars();

        threadView.on('destroy', () => {
          if (currentThreadId === threadId) {
            currentThreadId = null;
            renderAllSidebars();
          }
        });
      })
      .catch(() => {
        // ignore
      });
  });
}

function registerThreadRowLabels() {
  if (!sdk?.Lists || typeof sdk.Lists.registerThreadRowViewHandler !== 'function') {
    return;
  }
  sdk.Lists.registerThreadRowViewHandler((threadRowView) => {
    const getId = async () => {
      if (typeof threadRowView.getThreadIDAsync === 'function') {
        return await threadRowView.getThreadIDAsync();
      }
      return null;
    };

    getId()
      .then((threadId) => {
        if (!threadId || rowDecorated.has(threadId)) return;
        const item = findItemByThreadId(threadId);
        if (!item) return;
        rowDecorated.add(threadId);
        const label = getStateLabel(item.state || 'received');
        const color = STATE_COLORS[item.state] || '#2563eb';
        try {
          threadRowView.addLabel({
            title: label,
            foregroundColor: '#ffffff',
            backgroundColor: color
          });
        } catch (_) {
          // ignore
        }
      })
      .catch(() => {
        // ignore
      });
  });
}

async function bootstrap() {
  if (window[INIT_KEY]) return;
  window[INIT_KEY] = true;

  try {
    sdk = await InboxSDK.load(2, APP_ID, {
      // Disable InboxSDK telemetry pipeline in local/dev.
      // This avoids noisy pubsub token/logging errors in extension diagnostics.
      eventTracking: false,
      globalErrorLogging: false
    });
  } catch (error) {
    console.error('[Clearledgr] InboxSDK failed to load', error);
    return;
  }

  // Pre-fill compose views opened by the "Draft vendor reply" button.
  // openNewComposeView() is fire-and-forget; the handler fires when the view opens.
  sdk.Compose.registerComposeViewHandler((composeView) => {
    if (_pendingComposePrefill) {
      const prefill = _pendingComposePrefill;
      _pendingComposePrefill = null;
      try {
        if (prefill.to) composeView.setToRecipients([{ emailAddress: prefill.to }]);
        if (prefill.subject) composeView.setSubject(prefill.subject);
        if (prefill.body) composeView.setBodyHTML(prefill.body.replace(/\n/g, '<br>'));
      } catch (_) { /* ignore if SDK rejects */ }
    }
  });

  queueManager = new ClearledgrQueueManager();
  await queueManager.init();

  queueManager.onQueueUpdated((queue, status, agentSessions, tabs, agentInsights, sources, contexts) => {
    queueState = Array.isArray(queue) ? queue : [];
    scanStatus = status || {};
    agentSessionsState = agentSessions instanceof Map ? agentSessions : new Map();
    browserTabContext = Array.isArray(tabs) ? tabs : [];
    agentInsightsState = agentInsights instanceof Map ? agentInsights : new Map();
    sourcesState = sources instanceof Map ? sources : new Map();
    contextState = contexts instanceof Map ? contexts : new Map();
    if (selectedItemId && !findItemById(selectedItemId)) {
      selectedItemId = null;
      writeLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID, '');
    }
    if (!selectedItemId) {
      const restoredActiveItemId = readLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID);
      if (restoredActiveItemId && findItemById(restoredActiveItemId)) {
        selectedItemId = restoredActiveItemId;
      }
    }
    renderAllSidebars();
    registerThreadRowLabels();
  });

  initializeSidebar();
  registerThreadHandler();
  registerThreadRowLabels();
}

bootstrap();
