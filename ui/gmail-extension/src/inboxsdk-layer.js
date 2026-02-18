/**
 * Clearledgr AP v1 InboxSDK Layer
 * Embedded only. No dashboard. No navigation.
 */
import * as InboxSDK from '@inboxsdk/core';
import { ClearledgrQueueManager } from '../queue-manager.js';

const APP_ID = 'sdk_Clearledgr2026_dc12c60472';
const INIT_KEY = '__clearledgr_ap_v1_inboxsdk_initialized';
const LOGO_PATH = 'icons/icon48.png';

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
let currentThreadId = null;
let selectedItemId = null;
let queueState = [];
let scanStatus = {};
let agentSessionsState = new Map();
let browserTabContext = [];
let agentInsightsState = new Map();
let sourcesState = new Map();
let contextState = new Map();
let kpiSnapshotState = null;
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
let toastTimer = null;
let rowDecorated = new Set();
let auditState = {
  itemId: null,
  loading: false,
  events: []
};

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
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  });
}

function formatAgeSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return '';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

function formatPercentMetric(metric) {
  const value = Number(metric?.value);
  if (!Number.isFinite(value)) return 'N/A';
  return `${value.toFixed(1)}%`;
}

function formatHoursMetric(metric) {
  const value = Number(metric?.avg_hours);
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

function getAgentToolLabel(toolName) {
  const tool = String(toolName || '').toLowerCase();
  if (tool === 'read_page') return 'Read source email';
  if (tool === 'extract_table') return 'Extract table';
  if (tool === 'find_element') return 'Find page element';
  if (tool === 'capture_evidence') return 'Capture evidence';
  return String(toolName || '').replace(/_/g, ' ');
}

function showToast(message, tone = 'info') {
  if (!globalSidebarEl) return;
  const toast = globalSidebarEl.querySelector('#cl-toast');
  if (!toast) return;
  toast.textContent = message;
  toast.dataset.tone = tone;
  toast.style.display = 'block';
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.style.display = 'none';
  }, 3000);
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
  activeContextTab = 'email';
  auditState = { itemId: null, loading: false, events: [] };
  contextUiState = { itemId: null, loading: false, error: '' };
  renderSidebar();
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
  const sourceQuality = contextPayload.source_quality || {};
  const ageText = formatAgeSeconds(freshness.age_seconds);
  const freshnessSummary = freshness.is_stale
    ? `Stale context${ageText ? ` (${ageText} old)` : ''}`
    : ageText
      ? `Refreshed ${ageText} ago`
      : '';

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
      ${
        sourceQuality?.distribution
          ? `<div class="cl-context-row"><div><strong>Source quality:</strong> ${escapeHtml(
              String(sourceQuality.distribution)
            )}</div></div>`
          : ''
      }
      ${freshnessSummary ? `<div class="cl-context-row ${freshness.is_stale ? 'cl-context-warning' : ''}">${escapeHtml(freshnessSummary)}</div>` : ''}
      ${rows || '<div class="cl-empty">No linked email sources yet.</div>'}
    `;
  }

  if (activeContextTab === 'web') {
    const web = contextPayload.web || {};
    const portals = Array.isArray(web.related_portals) ? web.related_portals : [];
    const paymentPortals = Array.isArray(web.payment_portals) ? web.payment_portals : [];
    const procurement = Array.isArray(web.procurement) ? web.procurement : [];
    const dms = Array.isArray(web.dms_documents) ? web.dms_documents : [];
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
    const eventRows = events.slice(0, 3).map((event) => `
      <div class="cl-context-row">
        <div><strong>${escapeHtml(getAgentToolLabel(event.tool_name || 'browser_action'))}</strong></div>
        <div>${escapeHtml(String(event.status || 'unknown').replace(/_/g, ' '))}</div>
      </div>
    `).join('');
    const tabRows = relatedTabs.slice(0, 3).map((tab) => `
      <div class="cl-context-row">
        <div><strong>${escapeHtml(trimText(tab.title || tab.url || 'Browser tab', 80))}</strong></div>
        <div>${escapeHtml(tab.host || trimText(tab.url || '', 64))}</div>
      </div>
    `).join('');
    return `
      <div class="cl-context-meta">Browser events: ${escapeHtml(String(web.browser_event_count || 0))} · Related tabs: ${escapeHtml(String(agentInsight?.relatedCount || 0))}</div>
      <div class="cl-context-row">
        <div><strong>Coverage:</strong> portals ${coverage.payment_portal ? 'yes' : 'no'} · procurement ${coverage.procurement ? 'yes' : 'no'} · dms ${coverage.dms ? 'yes' : 'no'}</div>
      </div>
      ${freshnessSummary ? `<div class="cl-context-row ${freshness.is_stale ? 'cl-context-warning' : ''}">${escapeHtml(freshnessSummary)}</div>` : ''}
      ${portalRows || '<div class="cl-empty">No vendor portal sources detected.</div>'}
      ${procurementRows || ''}
      ${dmsRows || ''}
      ${tabRows || ''}
      ${eventRows || ''}
    `;
  }

  if (activeContextTab === 'approvals') {
    const approvals = contextPayload.approvals || {};
    const latest = approvals.latest || null;
    const slack = approvals.slack || {};
    const budget = approvals.budget || contextPayload.budget || {};
    const budgetStatus = String(budget.status || '').replace(/_/g, ' ');
    const threadPreview = Array.isArray(slack.thread_preview) ? slack.thread_preview : [];
    const previewRows = threadPreview.slice(0, 3).map((entry) => `
      <div class="cl-context-row">
        <div>${escapeHtml(trimText(entry.text || '', 120))}</div>
      </div>
    `).join('');
    return `
      <div class="cl-context-meta">Approval records: ${escapeHtml(String(approvals.count || 0))}</div>
      ${latest ? `<div class="cl-context-row"><div><strong>Latest:</strong> ${escapeHtml(String(latest.status || 'pending'))}</div></div>` : '<div class="cl-empty">No approval record yet.</div>'}
      ${budgetStatus ? `<div class="cl-context-row"><div><strong>Budget:</strong> ${escapeHtml(budgetStatus)}</div></div>` : ''}
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

function renderThreadContext() {
  if (!globalSidebarEl) return;
  const context = globalSidebarEl.querySelector('#cl-thread-context');
  if (!context) return;

  const item = getPrimaryItem();
  if (!item) {
    context.innerHTML = '<div class="cl-empty">Autopilot is scanning your inbox. AP items will appear automatically.</div>';
    return;
  }

  if (item?.id && !(contextUiState.loading && contextUiState.itemId === item.id)) {
    void ensureItemContext(item, { refresh: false });
  }

  const items = Array.isArray(queueState) ? queueState : [];
  const itemIndex = getPrimaryItemIndex();
  const humanIndex = itemIndex >= 0 ? itemIndex + 1 : 1;
  const vendor = item.vendor_name || item.vendor || item.sender || 'Unknown vendor';
  const invoiceNumber = item.invoice_number || 'N/A';
  const dueDate = item.due_date || 'N/A';
  const amount = formatAmount(item.amount, item.currency || 'USD');
  const state = item.state || 'received';
  const stateLabel = getStateLabel(state);
  const sourceSubject = trimText(item.subject || 'Subject unavailable', 96);
  const sourceSender = trimText(item.sender || 'Sender unavailable', 84);
  const issueSummary = getIssueSummary(item);
  const linkedSources = getLinkedSources(item);
  const agentInsight = getPrimaryAgentInsight();
  const hasConflict = Boolean(item.has_context_conflict);
  const conflictActions = Array.isArray(item.conflict_actions) ? item.conflict_actions : [];
  const mergeCandidates = hasConflict && queueManager?.findMergeCandidates
    ? queueManager.findMergeCandidates(item)
    : [];
  const mergeReason = item.merge_reason ? String(item.merge_reason).replace(/_/g, ' ') : '';
  const exceptionSeverity = item.exception_severity ? String(item.exception_severity).toLowerCase() : '';
  const exceptionCode = item.exception_code ? String(item.exception_code).replace(/_/g, ' ') : '';
  const riskSignals = item.risk_signals || {};
  const latePaymentRisk = String(riskSignals?.late_payment_risk?.level || '').trim();
  const discountSignal = Boolean(riskSignals?.discount_opportunity?.available);
  const confidenceNumber = Number(item.confidence);
  const hasConfidence = Number.isFinite(confidenceNumber) && confidenceNumber > 0;
  const confidencePercent = hasConfidence ? Math.round(Math.max(0, Math.min(1, confidenceNumber)) * 100) : null;
  const contextPayload = item?.id ? contextState.get(item.id) || null : null;
  const loadingContext = item?.id && contextUiState.loading && contextUiState.itemId === item.id;
  const contextError = item?.id && contextUiState.itemId === item.id ? contextUiState.error : '';
  const metadata = queueManager?.parseMetadata ? queueManager.parseMetadata(item.metadata) : {};
  const stateColor = STATE_COLORS[state] || '#0f172a';
  const sourceRows = linkedSources
    .slice(0, 12)
    .map((source, index) => {
      const sourceType = String(source.source_type || 'source').replace(/_/g, ' ');
      const detected = formatDateTime(source.detected_at);
      const canOpen = source.source_type === 'gmail_thread' || source.source_type === 'gmail_message' || source.source_type === 'portal';
      return `
        <div class="cl-source-row">
          <div class="cl-source-main">
            <span class="cl-pill cl-pill-queue">${escapeHtml(sourceType)}</span>
            <span>${escapeHtml(trimText(source.subject || source.source_ref || 'Source', 86))}</span>
          </div>
          <div class="cl-source-sub">
            ${escapeHtml(trimText(source.sender || source.source_ref || '', 72))}
            ${detected ? ` · ${escapeHtml(detected)}` : ''}
          </div>
          ${
            canOpen
              ? `<button class="cl-btn cl-btn-secondary cl-source-open" data-source-index="${index}">Open</button>`
              : ''
          }
        </div>
      `;
    })
    .join('');

  context.innerHTML = `
    <div class="cl-thread-card">
      <div class="cl-navigator">
        <div class="cl-thread-main">Invoice ${escapeHtml(humanIndex)} of ${escapeHtml(items.length || 1)}</div>
        <div class="cl-nav-buttons">
          <button class="cl-btn cl-btn-secondary cl-nav-btn" id="cl-prev-item" ${itemIndex <= 0 ? 'disabled' : ''}>Prev</button>
          <button class="cl-btn cl-btn-secondary cl-nav-btn" id="cl-next-item" ${itemIndex >= items.length - 1 ? 'disabled' : ''}>Next</button>
        </div>
      </div>
      <div class="cl-thread-header">
        <div class="cl-thread-title">${escapeHtml(vendor)}</div>
        <span class="cl-pill" style="color:${stateColor}; border-color:${stateColor};">${escapeHtml(stateLabel)}</span>
      </div>
      <div class="cl-thread-main">${escapeHtml(amount)} · Invoice ${escapeHtml(invoiceNumber)} · Due ${escapeHtml(dueDate)}</div>
      <div class="cl-thread-sub">${escapeHtml(issueSummary)}</div>
      <div class="cl-thread-meta">${escapeHtml(sourceSender)}</div>
      <div class="cl-thread-meta cl-source-subject">${escapeHtml(sourceSubject)}</div>
      ${
        mergeReason
          ? `<div class="cl-thread-meta"><span class="cl-pill cl-pill-queue">Merged: ${escapeHtml(mergeReason)}</span></div>`
          : ''
      }
      ${
        hasConfidence
          ? `<div class="cl-thread-meta"><span class="cl-pill cl-pill-queue">Confidence: ${escapeHtml(String(confidencePercent))}%</span></div>`
          : ''
      }
      ${
        exceptionCode
          ? `<div class="cl-thread-meta"><span class="cl-pill cl-pill-queue">${escapeHtml(exceptionSeverity || 'issue')}: ${escapeHtml(exceptionCode)}</span></div>`
          : ''
      }
      ${
        latePaymentRisk
          ? `<div class="cl-thread-meta"><span class="cl-pill cl-pill-queue">Late risk: ${escapeHtml(latePaymentRisk)}</span>${discountSignal ? ' <span class="cl-pill cl-pill-queue">Discount candidate</span>' : ''}</div>`
          : ''
      }
      ${
        hasConflict
          ? '<div class="cl-thread-meta cl-context-warning">Potential merge conflict detected for this invoice.</div>'
          : ''
      }
      ${
        hasConflict
          ? `
            <div class="cl-conflict-panel">
              <div class="cl-context-meta">Resolve conflict</div>
              <div class="cl-thread-sub">Choose merge/split action for this invoice cluster.</div>
              ${
                conflictActions.includes('merge')
                  ? `
                    <select id="cl-merge-target" class="cl-select">
                      <option value="">Select item to merge into this invoice</option>
                      ${mergeCandidates
                        .map(
                          (candidate) =>
                            `<option value="${escapeHtml(candidate.id)}">${escapeHtml(
                              `${candidate.vendor_name || candidate.vendor || 'Vendor'} · ${candidate.invoice_number || 'N/A'} · sources ${candidate.source_count || 0}`
                            )}</option>`
                        )
                        .join('')}
                    </select>
                    <button class="cl-btn cl-btn-secondary" id="cl-merge-item">Merge selected item</button>
                  `
                  : ''
              }
              ${
                conflictActions.includes('split')
                  ? `
                    <select id="cl-split-source" class="cl-select">
                      <option value="">Select source to split into new item</option>
                      ${linkedSources
                        .map((source, index) => {
                          const label = `${source.source_type || 'source'} · ${trimText(source.subject || source.source_ref || 'source', 54)}`;
                          return `<option value="${index}">${escapeHtml(label)}</option>`;
                        })
                        .join('')}
                    </select>
                    <button class="cl-btn cl-btn-secondary" id="cl-split-item">Split selected source</button>
                  `
                  : ''
              }
            </div>
          `
          : ''
      }
      <div class="cl-thread-actions">
        <button class="cl-btn cl-btn-secondary" id="cl-open-source-email">Open source email</button>
        <button class="cl-btn" id="cl-request-approval">Request approval</button>
      </div>
      <details class="cl-details">
        <summary>Sources (${escapeHtml(String(linkedSources.length))})</summary>
        <div class="cl-source-list">
          ${sourceRows || '<div class="cl-empty">No linked sources.</div>'}
        </div>
      </details>
      <div class="cl-context-tabs">
        <button class="cl-context-tab ${activeContextTab === 'email' ? 'active' : ''}" data-tab="email">Email</button>
        <button class="cl-context-tab ${activeContextTab === 'web' ? 'active' : ''}" data-tab="web">Web</button>
        <button class="cl-context-tab ${activeContextTab === 'approvals' ? 'active' : ''}" data-tab="approvals">Approvals</button>
        <button class="cl-context-tab ${activeContextTab === 'erp' ? 'active' : ''}" data-tab="erp">ERP</button>
        <button class="cl-btn cl-btn-secondary cl-context-refresh" id="cl-refresh-context">Refresh</button>
      </div>
      <div class="cl-context-body">
        ${renderContextTabBody(item, contextPayload, loadingContext, contextError, agentInsight)}
      </div>
      <details class="cl-details">
        <summary>Technical details</summary>
        <div class="cl-detail-grid">
          <div class="cl-detail-row"><span>Thread</span><span>${escapeHtml(getSourceThreadId(item) || 'N/A')}</span></div>
          <div class="cl-detail-row"><span>Message</span><span>${escapeHtml(getSourceMessageId(item) || 'N/A')}</span></div>
          <div class="cl-detail-row"><span>Workflow</span><span>${escapeHtml(metadata.workflow_id || item.workflow_id || 'N/A')}</span></div>
          <div class="cl-detail-row"><span>Run</span><span>${escapeHtml(metadata.run_id || item.run_id || 'N/A')}</span></div>
        </div>
      </details>
    </div>
  `;

  const prevBtn = context.querySelector('#cl-prev-item');
  const nextBtn = context.querySelector('#cl-next-item');
  const openSourceBtn = context.querySelector('#cl-open-source-email');
  const requestBtn = context.querySelector('#cl-request-approval');
  const mergeBtn = context.querySelector('#cl-merge-item');
  const splitBtn = context.querySelector('#cl-split-item');
  const canOpenSource = Boolean(getSourceThreadId(item) || getSourceMessageId(item) || item.subject);

  setButtonState(openSourceBtn, canOpenSource, 'Source email reference unavailable');

  const requestReason = queueManager.getUiActionDisabledReason('request_approval', state);
  setButtonState(requestBtn, !requestReason, requestReason);

  if (openSourceBtn) {
    openSourceBtn.addEventListener('click', () => {
      if (openSourceBtn.disabled) {
        showToast(openSourceBtn.dataset.disabledReason || 'Source email reference unavailable');
        return;
      }
      if (!openSourceEmail(item)) {
        showToast('Unable to open source email', 'error');
      }
    });
  }

  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      selectItemByOffset(-1);
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      selectItemByOffset(1);
    });
  }

  context.querySelectorAll('.cl-source-open').forEach((button) => {
    button.addEventListener('click', () => {
      const sourceIndex = Number(button.getAttribute('data-source-index') || -1);
      const source = linkedSources[sourceIndex];
      if (!source || !openSourceReference(source, item)) {
        showToast('Unable to open source', 'error');
      }
    });
  });

  context.querySelectorAll('.cl-context-tab').forEach((button) => {
    button.addEventListener('click', () => {
      const tab = button.getAttribute('data-tab') || 'email';
      activeContextTab = tab;
      renderThreadContext();
    });
  });

  const refreshContextBtn = context.querySelector('#cl-refresh-context');
  if (refreshContextBtn) {
    refreshContextBtn.addEventListener('click', async () => {
      await ensureItemContext(item, { refresh: true });
    });
  }

  if (requestBtn) {
    requestBtn.addEventListener('click', async () => {
      if (requestBtn.disabled) {
        showToast(requestBtn.dataset.disabledReason || 'Action unavailable');
        return;
      }
      const result = await queueManager.requestApproval(item);
      if (result?.status === 'needs_approval') {
        showToast('Approval requested');
      } else {
        showToast('Approval request failed', 'error');
      }
    });
  }

  if (mergeBtn) {
    mergeBtn.addEventListener('click', async () => {
      const select = context.querySelector('#cl-merge-target');
      const sourceId = String(select?.value || '').trim();
      if (!sourceId) {
        showToast('Select an invoice item to merge', 'error');
        return;
      }
      mergeBtn.disabled = true;
      const result = await queueManager.mergeItems(item.id, sourceId, 'gmail_user', 'manual_merge_from_sidebar');
      mergeBtn.disabled = false;
      if (result?.status === 'merged') {
        showToast('Items merged');
      } else {
        showToast('Merge failed', 'error');
      }
    });
  }

  if (splitBtn) {
    splitBtn.addEventListener('click', async () => {
      const select = context.querySelector('#cl-split-source');
      const sourceIndex = Number(select?.value || -1);
      const source = linkedSources[sourceIndex];
      if (!source) {
        showToast('Select a source to split', 'error');
        return;
      }
      splitBtn.disabled = true;
      const result = await queueManager.splitItem(
        item.id,
        [{ source_type: source.source_type, source_ref: source.source_ref }],
        'gmail_user',
        'manual_split_from_sidebar'
      );
      splitBtn.disabled = false;
      if (result?.status === 'split') {
        showToast('Source split into a new item');
      } else {
        showToast('Split failed', 'error');
      }
    });
  }
}

function renderAgentActions() {
  if (!globalSidebarEl) return;
  const container = globalSidebarEl.querySelector('#cl-agent-actions');
  if (!container) return;
  const item = getPrimaryItem();
  if (!item) {
    container.innerHTML = '<div class="cl-empty">Agent actions will appear when AP items are detected.</div>';
    return;
  }

  const sessionPayload = getPrimaryAgentSession();
  if (!sessionPayload || !sessionPayload.session) {
    container.innerHTML = '<div class="cl-empty">Preparing browser agent session...</div>';
    return;
  }

  const session = sessionPayload.session;
  const pending = Array.isArray(sessionPayload.pending_approvals) ? sessionPayload.pending_approvals : [];
  const queued = Array.isArray(sessionPayload.queued_commands) ? sessionPayload.queued_commands : [];
  const allEvents = Array.isArray(sessionPayload.events) ? sessionPayload.events : [];
  const scope = getAgentScope(item, sessionPayload);
  const summary = summarizeAgentEvents(allEvents, 8);
  const historyEvents = summary.events;
  const state = String(session.state || 'running');
  const stateTone = state === 'blocked_for_approval' ? '#b45309' : state === 'failed' ? '#b91c1c' : '#0f766e';
  const stateLabel = state.replace(/_/g, ' ');

  const nextActionEvent = pending[0] || queued[0] || historyEvents.find((entry) => entry.status === 'failed') || null;
  const requestPayload = nextActionEvent?.request_payload || nextActionEvent?.requestPayload || {};
  const nextActionLabel = nextActionEvent
    ? String(requestPayload.step || getAgentToolLabel(nextActionEvent.tool_name || nextActionEvent?.request_payload?.tool_name || 'action'))
    : 'No immediate action';
  const nextActionStatus = nextActionEvent
    ? String(nextActionEvent.status || 'queued').replace(/_/g, ' ')
    : 'idle';
  const nextActionDetail = nextActionEvent ? describeAgentEvent(nextActionEvent) : 'Agent is monitoring this invoice context.';
  const requiresApproval = nextActionEvent?.status === 'blocked_for_approval';
  const previewKey = requiresApproval && nextActionEvent?.command_id
    ? `${item.id}:${session.id}:${nextActionEvent.command_id}`
    : null;
  const activePreview = previewKey && agentPreviewState.key === previewKey ? agentPreviewState : null;
  if (!previewKey && agentPreviewState.key?.startsWith(`${item.id}:`)) {
    agentPreviewState = { key: null, loading: false, error: '', data: null };
  }
  if (previewKey && (!activePreview || (!activePreview.loading && !activePreview.data && !activePreview.error))) {
    void ensureAgentPreview(item, sessionPayload, nextActionEvent);
  }

  let previewHtml = '';
  if (requiresApproval) {
    if (activePreview?.loading) {
      previewHtml = '<div class="cl-agent-preview cl-empty">Generating preflight preview...</div>';
    } else if (activePreview?.error) {
      previewHtml = `<div class="cl-agent-preview cl-agent-detail-error">${escapeHtml(activePreview.error)}</div>`;
    } else if (activePreview?.data) {
      const preview = activePreview.data;
      const warnings = Array.isArray(preview?.warnings) ? preview.warnings : [];
      const warningRows = warnings
        .slice(0, 4)
        .map((warning) => `<li>${escapeHtml(String(warning))}</li>`)
        .join('');
      const decision = preview?.decision || {};
      previewHtml = `
        <div class="cl-agent-preview">
          <div class="cl-agent-preview-title">Preflight preview</div>
          <div class="cl-agent-detail">${escapeHtml(preview?.summary || 'Summary unavailable')}</div>
          <div class="cl-agent-preview-meta">
            Scope: ${escapeHtml(String(decision.scope || 'default'))}
            · Risk: ${escapeHtml(String(decision.tool_risk || 'unknown').replace(/_/g, ' '))}
          </div>
          ${warningRows ? `<ul class="cl-agent-warning-list">${warningRows}</ul>` : ''}
        </div>
      `;
    }
  }

  const itemSummaryState = agentSummaryState.itemId === item.id ? agentSummaryState : null;
  let macroSummaryHtml = '';
  if (itemSummaryState) {
    if (itemSummaryState.loading) {
      macroSummaryHtml = '<div class="cl-agent-brief"><div class="cl-empty">Running macro...</div></div>';
    } else if (itemSummaryState.error) {
      macroSummaryHtml = `<div class="cl-agent-brief"><div class="cl-agent-detail-error">${escapeHtml(itemSummaryState.error)}</div></div>`;
    } else if (itemSummaryState.data) {
      const data = itemSummaryState.data;
      const mode = String(itemSummaryState.mode || '').toLowerCase();
      const title = mode.includes('preview') ? 'Macro preview' : 'Macro dispatched';
      let rows = '';
      if (Array.isArray(data.commands)) {
        rows = data.commands
          .slice(0, 4)
          .map((entry) => {
            const command = entry?.command || {};
            const tool = getAgentToolLabel(command.tool_name || '');
            const detail = entry?.summary || command.step || '';
            return `
              <div class="cl-agent-related-row">
                <div class="cl-agent-related-title">${escapeHtml(tool || 'Step')}</div>
                <div class="cl-agent-detail">${escapeHtml(detail)}</div>
              </div>
            `;
          })
          .join('');
      } else {
        rows = `
          <div class="cl-agent-related-row">
            <div class="cl-agent-detail">
              Queued: ${escapeHtml(String(data.queued || 0))}
              · Awaiting approval: ${escapeHtml(String(data.blocked || 0))}
              · Denied: ${escapeHtml(String(data.denied || 0))}
            </div>
          </div>
        `;
      }
      macroSummaryHtml = `
        <div class="cl-agent-brief">
          <div class="cl-agent-brief-title">${escapeHtml(title)} · ${escapeHtml(getMacroLabel(data.macro_name || ''))}</div>
          ${rows}
        </div>
      `;
    }
  }

  const historyRows = historyEvents
    .slice(0, 5)
    .map((event) => {
      const statusText = String(event.status || 'queued').replace(/_/g, ' ');
      const tool = getAgentToolLabel(event.tool_name || '');
      return `
        <div class="cl-agent-row">
          <div class="cl-agent-row-main">
            <span class="cl-agent-tool">${escapeHtml(tool)}</span>
            <span class="cl-agent-status">${escapeHtml(statusText)}</span>
          </div>
        </div>
      `;
    })
    .join('');

  container.innerHTML = `
    <div class="cl-agent-meta">
      <span class="cl-agent-chip" style="color:${stateTone}; border-color:${stateTone};">${escapeHtml(stateLabel)}</span>
      <span class="cl-agent-count">${escapeHtml(String(queued.length))} queued</span>
      <span class="cl-agent-count">${escapeHtml(String(pending.length))} awaiting approval</span>
    </div>
    <div class="cl-agent-row">
      <div class="cl-agent-row-main">
        <span class="cl-agent-tool">${escapeHtml(nextActionLabel)}</span>
        <span class="cl-agent-status">${escapeHtml(nextActionStatus)}</span>
      </div>
      <div class="cl-agent-detail">${escapeHtml(nextActionDetail || '')}</div>
      ${
        requiresApproval
          ? `<button class="cl-btn cl-btn-secondary cl-agent-approve" data-session-id="${session.id}" data-command-id="${nextActionEvent.command_id}">Approve action</button>`
          : ''
      }
      ${previewHtml}
    </div>
    <div class="cl-agent-actions-bar">
      <button class="cl-btn cl-btn-secondary cl-agent-action" data-macro="ingest_invoice_match_po" data-dry-run="1">Preview intake macro</button>
      <button class="cl-btn cl-btn-primary cl-agent-action" data-macro="ingest_invoice_match_po" data-dry-run="0">Run intake macro</button>
    </div>
    <div class="cl-agent-actions-bar">
      <button class="cl-btn cl-btn-secondary cl-agent-action" data-macro="collect_w9" data-dry-run="1">Preview W-9 macro</button>
    </div>
    ${macroSummaryHtml}
    <details class="cl-details">
      <summary>View history</summary>
      <div class="cl-agent-list">
        ${historyRows || '<div class="cl-empty">No recent actions.</div>'}
      </div>
    </details>
  `;

  container.querySelectorAll('.cl-agent-approve').forEach((button) => {
    button.addEventListener('click', async () => {
      const sessionId = button.getAttribute('data-session-id');
      const commandId = button.getAttribute('data-command-id');
      if (!sessionId || !commandId) return;
      const sessionData = getPrimaryAgentSession();
      const command = (sessionData?.events || []).find((event) => event.command_id === commandId);
      if (!command) return;
      button.disabled = true;
      const result = await queueManager.confirmAgentCommand(sessionId, command, 'gmail_user', scope);
      if (result?.event) {
        showToast('Agent action approved');
        await queueManager.syncAgentSessions();
      } else {
        showToast('Unable to approve action', 'error');
      }
      button.disabled = false;
    });
  });

  container.querySelectorAll('.cl-agent-action').forEach((button) => {
    button.addEventListener('click', async () => {
      const macro = button.getAttribute('data-macro');
      const dryRun = button.getAttribute('data-dry-run') === '1';
      if (!macro) return;
      agentSummaryState = {
        itemId: item.id,
        mode: dryRun ? 'macro_preview' : 'macro_run',
        loading: true,
        error: '',
        data: null
      };
      renderAgentActions();
      const payload = await queueManager.dispatchAgentMacro(session.id, macro, {
        actorId: 'gmail_user',
        actorRole: scope.actorRole,
        workflowId: scope.workflowId,
        params: {
          workflow_id: scope.workflowId || undefined,
          actor_role: scope.actorRole || undefined
        },
        dryRun
      });
      if (!payload) {
        agentSummaryState = {
          itemId: item.id,
          mode: dryRun ? 'macro_preview' : 'macro_run',
          loading: false,
          error: 'Unable to run macro.',
          data: null
        };
        renderAgentActions();
        showToast('Macro request failed', 'error');
        return;
      }

      agentSummaryState = {
        itemId: item.id,
        mode: dryRun ? 'macro_preview' : 'macro_run',
        loading: false,
        error: '',
        data: payload
      };
      renderAgentActions();
      if (dryRun) {
        const stepCount = Array.isArray(payload.commands) ? payload.commands.length : 0;
        showToast(`Preview ready (${stepCount} steps)`);
      } else {
        showToast('Macro dispatched');
        await queueManager.syncAgentSessions();
      }
    });
  });
}

function renderAuditTrail() {
  if (!globalSidebarEl) return;
  const container = globalSidebarEl.querySelector('#cl-audit-trail');
  if (!container) return;

  const item = getPrimaryItem();
  if (!item) {
    container.innerHTML = '<div class="cl-empty">Audit events will appear once AP items are detected.</div>';
    return;
  }

  if (auditState.loading && auditState.itemId === item.id) {
    container.innerHTML = '<div class="cl-empty">Loading audit trail...</div>';
    return;
  }

  const events = Array.isArray(auditState.events) ? auditState.events : [];
  if (!events.length) {
    container.innerHTML = '<div class="cl-empty">No audit events yet.</div>';
    return;
  }

  container.innerHTML = events
    .slice(0, 5)
    .map((event) => {
      const eventType = prettifyEventType(event.event_type || event.eventType);
      const detail = event.decision_reason || event.reason || event.payload_json?.reason || '';
      const time = formatTimestamp(event.ts || event.created_at || event.createdAt);
      return `
        <div class="cl-audit-row">
          <div class="cl-audit-main">
            <span class="cl-audit-type">${eventType}</span>
            ${time ? `<span class="cl-audit-time">${time}</span>` : ''}
          </div>
          ${detail ? `<div class="cl-audit-detail">${detail}</div>` : ''}
        </div>
      `;
    })
    .join('');
}

async function refreshAuditTrail(force = false) {
  if (!globalSidebarEl || !queueManager) return;
  const item = getPrimaryItem();
  if (!item || !item.id) {
    auditState = { itemId: null, loading: false, events: [] };
    renderAuditTrail();
    return;
  }

  const shouldLoad =
    force ||
    auditState.itemId !== item.id ||
    !Array.isArray(auditState.events) ||
    auditState.events.length === 0;

  if (!shouldLoad) {
    renderAuditTrail();
    return;
  }

  auditState = { itemId: item.id, loading: true, events: [] };
  renderAuditTrail();

  const events = await queueManager.fetchAuditTrail(item.id, { force });
  const activeItem = getPrimaryItem();
  if (!activeItem || activeItem.id !== item.id) {
    return;
  }
  auditState = { itemId: item.id, loading: false, events: Array.isArray(events) ? events : [] };
  renderAuditTrail();
}

function renderSidebar() {
  if (!globalSidebarEl) return;
  renderThreadContext();
  renderKpiSummary();
  renderAgentActions();
  renderScanStatus();
  void refreshAuditTrail();
}

function renderKpiSummary() {
  if (!globalSidebarEl) return;
  const container = globalSidebarEl.querySelector('#cl-kpi-summary');
  if (!container) return;
  const kpis = kpiSnapshotState || queueManager?.getKpiSnapshot?.() || null;
  if (!kpis) {
    container.innerHTML = '<div class="cl-empty">KPI snapshot will appear once telemetry sync completes.</div>';
    return;
  }

  const touchless = formatPercentMetric(kpis.touchless_rate);
  const exceptions = formatPercentMetric(kpis.exception_rate);
  const approvals = formatPercentMetric(kpis.on_time_approvals);
  const cycle = formatHoursMetric(kpis.cycle_time_hours);
  const missed = Number(kpis?.missed_discounts_baseline?.candidate_count || 0);
  const friction = Number(kpis?.approval_friction?.sla_breach_rate || 0);
  const frictionText = Number.isFinite(friction) ? `${friction.toFixed(1)}%` : 'N/A';

  container.innerHTML = `
    <div class="cl-kpi-grid">
      <div class="cl-kpi-tile"><span>Touchless</span><strong>${escapeHtml(touchless)}</strong></div>
      <div class="cl-kpi-tile"><span>Exceptions</span><strong>${escapeHtml(exceptions)}</strong></div>
      <div class="cl-kpi-tile"><span>On-time approvals</span><strong>${escapeHtml(approvals)}</strong></div>
      <div class="cl-kpi-tile"><span>Avg cycle</span><strong>${escapeHtml(cycle)}</strong></div>
    </div>
    <div class="cl-kpi-footnote">
      Missed discount candidates: ${escapeHtml(String(missed))}
      · SLA breach rate: ${escapeHtml(frictionText)}
    </div>
  `;
}

function initializeSidebar() {
  if (globalSidebarEl) return;
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
      .cl-thread-meta {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-source-subject {
        line-height: 1.35;
      }
      .cl-thread-actions {
        display: flex;
        gap: 6px;
        margin-top: 4px;
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
      .cl-context-meta {
        font-size: 10px;
        color: #4b5563;
        font-weight: 600;
      }
      .cl-context-warning {
        color: #b45309;
        font-weight: 600;
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
        gap: 6px;
        max-height: 160px;
        overflow-y: auto;
        padding-right: 2px;
      }
      .cl-audit-row {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 8px;
        background: var(--cl-card);
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-audit-main {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .cl-audit-type {
        font-size: 11px;
        font-weight: 600;
        color: var(--cl-text);
      }
      .cl-audit-time {
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-audit-detail {
        font-size: 11px;
        color: #4b5563;
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
      .cl-agent-row {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 8px;
        background: var(--cl-card);
        display: flex;
        flex-direction: column;
        gap: 5px;
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
      .cl-agent-detail {
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
      .cl-agent-action {
        flex: 1;
      }
      .cl-agent-brief {
        margin-top: 8px;
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 8px;
        background: #f9fafb;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-agent-brief-title {
        font-size: 11px;
        font-weight: 600;
        color: var(--cl-text);
      }
      .cl-agent-related-row {
        border-top: 1px solid var(--cl-border);
        padding-top: 6px;
      }
      .cl-agent-related-row:first-child {
        border-top: 0;
        padding-top: 0;
      }
      .cl-agent-related-title {
        font-size: 11px;
        color: #1f2937;
        font-weight: 600;
      }
      .cl-details {
        border-top: 1px dashed var(--cl-border);
        margin-top: 4px;
        padding-top: 4px;
      }
      .cl-details summary {
        list-style: none;
        cursor: pointer;
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-details summary::-webkit-details-marker {
        display: none;
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
      .cl-debug-controls {
        display: none;
        gap: 8px;
      }
      .cl-kpi-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px;
      }
      .cl-kpi-tile {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 6px;
        background: #ffffff;
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .cl-kpi-tile span {
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-kpi-tile strong {
        font-size: 12px;
        color: var(--cl-text);
      }
      .cl-kpi-footnote {
        font-size: 10px;
        color: var(--cl-muted);
      }
    </style>
    <div class="cl-header">
      <div class="cl-title">
        ${getAssetUrl(LOGO_PATH) ? `<img class="cl-logo" src="${getAssetUrl(LOGO_PATH)}" alt="Clearledgr" />` : ''}
        Clearledgr AP
      </div>
      <div class="cl-subtitle">Embedded accounts payable execution</div>
    </div>
    <div id="cl-toast" class="cl-toast"></div>
    <div class="cl-section">
      <div id="cl-scan-status" class="cl-scan-status"></div>
      <div id="cl-auth-actions" class="cl-inline-actions">
        <button class="cl-btn cl-btn-secondary" id="cl-authorize-gmail">Authorize Gmail</button>
      </div>
      <div id="cl-debug-controls" class="cl-debug-controls">
        <button class="cl-btn cl-btn-secondary" id="cl-debug-refresh">Refresh</button>
        <button class="cl-btn cl-btn-secondary" id="cl-debug-scan">Scan</button>
      </div>
    </div>
    <div class="cl-section">
      <div class="cl-section-title">Current item</div>
      <div id="cl-thread-context"></div>
    </div>
    <div class="cl-section">
      <div class="cl-section-title">KPI snapshot</div>
      <div id="cl-kpi-summary"></div>
    </div>
    <div class="cl-section">
      <div class="cl-section-title">Agent actions</div>
      <div id="cl-agent-actions"></div>
    </div>
    <div class="cl-section">
      <div class="cl-section-title">Audit</div>
      <div id="cl-audit-trail" class="cl-audit-list"></div>
    </div>
  `;

  globalSidebarEl = container;
  const logoUrl = getAssetUrl(LOGO_PATH);
  sdk.Global.addSidebarContentPanel({
    title: 'Clearledgr',
    iconUrl: logoUrl || null,
    el: container,
    hideTitleBar: false
  });
  const logoImg = container.querySelector('.cl-logo');
  if (logoImg) {
    logoImg.addEventListener('error', () => {
      logoImg.remove();
    });
  }

  const debugControls = container.querySelector('#cl-debug-controls');
  const debugRefresh = container.querySelector('#cl-debug-refresh');
  const debugScan = container.querySelector('#cl-debug-scan');
  const authorizeButton = container.querySelector('#cl-authorize-gmail');
  if (authorizeButton) {
    authorizeButton.addEventListener('click', async () => {
      authorizeButton.disabled = true;
      const result = await queueManager.authorizeGmailNow();
      if (result?.success) {
        showToast('Gmail authorized. Autopilot is resuming.', 'success');
        await queueManager.refreshQueue();
      } else {
        const message = String(result?.error || 'authorization_failed');
        showToast(`Authorization failed: ${message}`, 'error');
      }
      authorizeButton.disabled = false;
    });
  }

  if (queueManager?.isDebugUiEnabled()) {
    if (debugControls) debugControls.style.display = 'flex';
    if (debugRefresh) {
      debugRefresh.addEventListener('click', async () => {
        await queueManager.refreshQueue();
        await refreshAuditTrail(true);
      });
    }
    if (debugScan) {
      debugScan.addEventListener('click', async () => {
        await queueManager.scanNow('debug');
        await refreshAuditTrail(true);
      });
    }
  }

  renderSidebar();
}

function renderScanStatus() {
  if (!globalSidebarEl) return;
  const statusEl = globalSidebarEl.querySelector('#cl-scan-status');
  const authActionsEl = globalSidebarEl.querySelector('#cl-auth-actions');
  if (!statusEl) return;
  if (authActionsEl) authActionsEl.style.display = 'none';

  const state = scanStatus?.state || 'idle';
  statusEl.dataset.tone = '';
  if (state === 'initializing') {
    statusEl.textContent = 'Autopilot initializing.';
    statusEl.style.display = 'block';
    return;
  }

  if (state === 'scanning') {
    statusEl.textContent = 'Autopilot scanning inbox.';
    statusEl.style.display = 'block';
    return;
  }

  if (state === 'auth_required') {
    statusEl.textContent = 'Gmail authorization required to start autopilot.';
    statusEl.style.display = 'block';
    if (authActionsEl) authActionsEl.style.display = 'block';
    return;
  }

  if (state === 'blocked') {
    if ((scanStatus?.error || '') === 'temporal_unavailable') {
      statusEl.textContent = 'Autopilot is blocked because Temporal is not connected.';
    } else {
      statusEl.textContent = 'Setup required. Configure backend and organization settings.';
    }
    statusEl.dataset.tone = 'error';
    statusEl.style.display = 'block';
    return;
  }

  if (state === 'error') {
    const errorCode = String(scanStatus?.error || '');
    const backendDown = errorCode.includes('backend');
    if (backendDown) {
      statusEl.textContent = 'Autopilot cannot sync because backend is unreachable.';
    } else if (errorCode.includes('temporal')) {
      statusEl.textContent = 'Autopilot cannot process AP runs because Temporal is unavailable.';
    } else if (errorCode.includes('processing')) {
      const failedCount = Number(scanStatus?.failedCount || 0);
      statusEl.textContent = failedCount > 0
        ? `Autopilot is running but ${failedCount} email(s) failed to process. We are retrying automatically.`
        : 'Autopilot is running but some emails failed to process. We are retrying automatically.';
    } else {
      statusEl.textContent = 'Inbox scan error. We will retry automatically.';
    }
    statusEl.dataset.tone = 'error';
    statusEl.style.display = 'block';
    return;
  }

  const lastScan = scanStatus?.lastScanAt ? new Date(scanStatus.lastScanAt) : null;
  if (lastScan) {
    statusEl.textContent = `Autopilot active. Last scan ${lastScan.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}.`;
  } else {
    statusEl.textContent = 'Autopilot running.';
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
        renderThreadContext();

        threadView.on('destroy', () => {
          if (currentThreadId === threadId) {
            currentThreadId = null;
            renderThreadContext();
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

  queueManager = new ClearledgrQueueManager();
  await queueManager.init();

  queueManager.onQueueUpdated((queue, status, agentSessions, tabs, agentInsights, sources, contexts, kpis) => {
    queueState = Array.isArray(queue) ? queue : [];
    scanStatus = status || {};
    agentSessionsState = agentSessions instanceof Map ? agentSessions : new Map();
    browserTabContext = Array.isArray(tabs) ? tabs : [];
    agentInsightsState = agentInsights instanceof Map ? agentInsights : new Map();
    sourcesState = sources instanceof Map ? sources : new Map();
    contextState = contexts instanceof Map ? contexts : new Map();
    kpiSnapshotState = kpis || null;
    if (selectedItemId && !findItemById(selectedItemId)) {
      selectedItemId = null;
    }
    renderSidebar();
    registerThreadRowLabels();
  });

  initializeSidebar();
  registerThreadHandler();
  registerThreadRowLabels();
}

bootstrap();
