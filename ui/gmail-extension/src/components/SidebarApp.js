/** Root sidebar Preact component — compact AP-first Gmail work surface */
import { h, Component } from 'preact';
import { useState, useEffect, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import store from '../utils/store.js';
import { SIDEBAR_CSS, STATE_PILL_CSS } from '../styles.js';
import ActionDialog, { useActionDialog } from './ActionDialog.js';
import { hasAdminAccessRole, hasOpsAccessRole } from '../utils/roles.js';
import {
  getStateLabel,
  getAgentMemoryView,
  formatAmount,
  getAssetUrl,
  getFinanceEffectBlockers,
  getFinanceEffectNotice,
  getFieldReviewBlockers,
  normalizeBudgetContext,
  getIssueSummary,
  getExceptionReason,
  getEvidenceChecklistEntries,
  getSourceThreadId,
  getSourceMessageId,
  getWorkflowPauseReason,
  openSourceEmail,
  partitionAuditEvents,
} from '../utils/formatters.js';
import {
  canEscalateApproval,
  canReassignApproval,
  getDefaultNextMoveLabel,
  getOperatorOverrideCopy,
  normalizeWorkState,
  getPrimaryActionConfig,
  getWorkStateNotice,
  shouldOfferResumeWorkflow,
  canRejectWorkItem,
  canNudgeApprover,
  hasErpPostingConnection,
  needsEntityRouting,
} from '../utils/work-actions.js';
import {
  getDocumentTypeLabel,
  getNonInvoiceWorkflowGuidance,
  isInvoiceDocumentType,
  normalizeDocumentType,
} from '../utils/document-types.js';
import { navigateInboxRoute } from '../utils/inbox-route.js';
import { navigateToRecordDetail } from '../utils/record-route.js';
import { navigateToVendorRecord } from '../utils/vendor-route.js';
import { focusPipelineItem } from '../routes/pipeline-views.js';

const html = htm.bind(h);
const LOGO_PATH = 'icons/icon48.png';

class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('[Clearledgr]', error, info?.componentStack || '');
  }

  render() {
    if (this.state.error) {
      return html`<div class="cl-empty" role="alert">
        <p>${this.props.fallback || 'Something went wrong.'}</p>
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${() => this.setState({ error: null })}>Retry</button>
      </div>`;
    }
    return this.props.children;
  }
}

function useStore() {
  const [, update] = useState(0);
  useEffect(() => store.subscribe(() => update((n) => n + 1)), []);
  return store;
}

function useAction(fn) {
  const [pending, setPending] = useState(false);
  const ref = useRef(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const exec = useCallback(async (...args) => {
    if (ref.current) return ref.current;
    setPending(true);
    const promise = fn(...args);
    ref.current = promise;
    try {
      return await promise;
    } finally {
      ref.current = null;
      if (mounted.current) setPending(false);
    }
  }, [fn]);

  return [exec, pending];
}

let _toastEl = null;
let _toastTimer = null;

export function showToast(message, tone = 'info') {
  if (!_toastEl) return;
  _toastEl.textContent = message;
  _toastEl.dataset.tone = tone;
  _toastEl.style.display = 'block';
  clearTimeout(_toastTimer);
  const duration = tone === 'error' ? 6000 : 3000;
  _toastTimer = setTimeout(() => {
    if (_toastEl) _toastEl.style.display = 'none';
  }, duration);
  _toastTimer?.unref?.();
}

function humanizeActionFailure(reason) {
  const token = String(reason || '').trim();
  if (!token) return '';
  const map = {
    missing_gmail_reference: 'Clearledgr could not find the Gmail thread for this invoice.',
    missing_item_reference: 'Clearledgr could not identify this invoice record.',
    ap_item_not_found: 'Clearledgr could not find this invoice record.',
    state_not_ready_for_approval: 'This invoice is not ready to send for approval yet.',
    entity_route_review_required: 'Choose the legal entity before sending this invoice for approval.',
    entity_selection_required: 'Select the correct legal entity first.',
    field_review_required: 'Finish the required field checks before sending this invoice for approval.',
    organization_mismatch: 'This invoice belongs to a different workspace.',
    assignee_required: 'Choose the approver who should own this approval request.',
    state_not_waiting_for_approval: 'This invoice is no longer waiting on approval.',
    waiting_for_sla_window: 'Follow-up already sent. Wait for the vendor response before nudging again.',
    followup_attempt_limit_reached: 'Clearledgr reached the vendor follow-up limit. This now needs manual escalation.',
    state_not_needs_info: 'This invoice is no longer waiting on vendor information.',
    segregation_of_duties_violation: 'You cannot approve this invoice because you submitted or processed it. Another team member must approve.',
    not_authorized_approver: 'You are not a designated approver for this invoice. Only named approvers in the routing rule can approve.',
  };
  return map[token] || token.replace(/_/g, ' ');
}

function didSendApprovalReminder(result) {
  const payload = result && typeof result === 'object' ? result : {};
  if (String(payload.status || '').toLowerCase() === 'nudged') return true;
  return ['slack', 'teams', 'fallback'].some((key) => (
    String(payload?.[key]?.status || '').toLowerCase() === 'sent'
  ));
}

function Toast() {
  const ref = useRef(null);
  useEffect(() => {
    _toastEl = ref.current;
    return () => {
      clearTimeout(_toastTimer);
      _toastTimer = null;
      _toastEl = null;
    };
  }, []);
  return html`<div ref=${ref} class="cl-toast" style="display:none" onClick=${() => { if (_toastEl) _toastEl.style.display = 'none'; clearTimeout(_toastTimer); }}></div>`;
}

function ScanStatus() {
  const s = useStore();
  const status = s.scanStatus;
  const gmail = s.gmailIntegration || {};
  const state = status?.state || 'idle';

  let text = '';
  let tone = '';

  if (state === 'initializing') text = 'Getting ready.';
  else if (state === 'scanning') text = 'Scanning this inbox.';
  else if (state === 'auth_required') {
    text = 'Connect Gmail to keep Clearledgr working here.';
    tone = 'warning';
  } else if (state === 'blocked') {
    text = 'Finish setup to keep Clearledgr working here.';
    tone = 'warning';
  } else if (state === 'error') {
    const err = String(status?.error || '');
    if (err.includes('backend')) text = "Can't reach Clearledgr.";
    else if (err.includes('temporal')) text = 'Clearledgr is temporarily unavailable.';
    else if (err.includes('processing')) {
      const failedCount = Number(status?.failedCount || 0);
      text = failedCount > 0 ? `${failedCount} email(s) need another try.` : 'Something needs another try.';
    } else text = 'Inbox sync issue. Retrying.';
    tone = 'error';
  } else {
    const lastScan = status?.lastScanAt ? new Date(status.lastScanAt) : null;
      text = lastScan
      ? `Monitoring active · ${lastScan.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
      : 'Monitoring active';
  }

  if (state !== 'auth_required' && gmail?.requires_reconnect) {
    text = 'Reconnect Gmail to keep this inbox connected.';
    tone = 'warning';
  }

  return html`<div id="cl-scan-status" class="cl-scan-status" data-tone=${tone}>${text}</div>`;
}

function StatePill({ state }) {
  const cls = `cl-pill cl-pill-${String(state || 'received').replace(/_/g, '-')}`;
  return html`<span class=${cls}>${getStateLabel(state)}</span>`;
}

function getBlockers(item, state, budgetContext, documentType = 'invoice') {
  const blockers = [];
  const fieldReviewBlockers = getFieldReviewBlockers(item);
  const financeEffectBlockers = getFinanceEffectBlockers(item);
  const financeEffectNotice = getFinanceEffectNotice(item);
  const approvalFollowup = item?.approval_followup && typeof item.approval_followup === 'object'
    ? item.approval_followup
    : {};
  const pauseReason = getWorkflowPauseReason(item);
  const documentLabel = getDocumentTypeLabel(documentType, { lowercase: true });
  const isInvoiceDocument = isInvoiceDocumentType(documentType);
  const add = (id, label, detail) => {
    if (!label) return;
    if (blockers.some((entry) => entry.id === id || entry.label === label)) return;
    blockers.push({ id, label, detail });
  };

  if (budgetContext?.requiresDecision) {
    add(
      'budget',
      'Budget review required',
      `A budget decision is still needed before this ${isInvoiceDocument ? 'invoice' : 'record'} can move forward.`,
    );
  }

  const exceptionCode = String(item?.exception_code || '').trim().toLowerCase();
  const exceptionReason = getExceptionReason(exceptionCode);
  if (exceptionReason) {
    add('exception', exceptionReason, getIssueSummary(item));
  }

  if (!item?.po_number && exceptionCode.includes('po')) {
    add('po', 'PO reference missing', `Link the correct PO before continuing this ${isInvoiceDocument ? 'invoice' : 'record'}.`);
  }

  const confidence = Number(item?.confidence);
  if ((item?.requires_field_review || (Number.isFinite(confidence) && confidence < 0.95)) && !['posted_to_erp', 'closed', 'rejected'].includes(state)) {
    add(
      'confidence',
      fieldReviewBlockers.length ? 'Needs a quick field check' : 'Check extracted fields',
      fieldReviewBlockers.length
        ? null
        : (pauseReason || `Current confidence is ${Math.round(confidence * 100)}%, so a quick field check is still required.`),
    );
  }
  if (item?.finance_effect_review_required) {
    add(
      'finance_effect',
      financeEffectBlockers[0]?.label || 'Credits or payments need review',
      financeEffectBlockers[0]?.detail || financeEffectNotice || 'Linked finance documents changed the payable or settlement balance.',
    );
  }
  if (needsEntityRouting(item, state, documentType)) {
    add(
      'entity',
      'Entity route needs review',
      item?.entity_route_reason || 'Choose the correct legal entity before approval routing can continue.',
    );
  }

  if (state === 'needs_approval') {
    const pendingAssignees = Array.isArray(approvalFollowup?.pending_assignees) ? approvalFollowup.pending_assignees : [];
    add(
      'approval',
      approvalFollowup?.escalation_due
        ? 'Approval needs escalation'
        : (approvalFollowup?.sla_breached ? 'Approval reminder is due' : 'Waiting on approver'),
      approvalFollowup?.escalation_due || approvalFollowup?.sla_breached || pendingAssignees.length
        ? getWorkStateNotice(state, documentType, item)
        : 'The approval request is still outstanding.',
    );
  }

  if (state === 'needs_info') {
    const disputeStatus = item?.dispute_status;
    const disputeLabel = disputeStatus === 'vendor_contacted'
      ? 'Waiting on vendor response'
      : disputeStatus === 'escalated'
      ? 'Dispute escalated — vendor unresponsive'
      : isInvoiceDocument ? 'Missing invoice details' : 'Missing document details';
    add(
      'needs_info',
      disputeLabel,
      getWorkStateNotice(state, documentType, item)
        || `Clearledgr still needs more information before this ${isInvoiceDocument ? 'invoice' : 'record'} can continue.`,
    );
  }

  if ((state === 'approved' || state === 'ready_to_post') && !exceptionReason && !hasErpPostingConnection(item)) {
    add('erp_setup', 'ERP is not connected', 'Connect QuickBooks, Xero, NetSuite, or SAP before Clearledgr can post this invoice.');
  }

  if (state === 'failed_post' && !exceptionReason) {
    add(
      'failed_post',
      hasErpPostingConnection(item) ? 'ERP posting failed' : 'ERP is not connected',
      hasErpPostingConnection(item)
        ? 'Retry the ERP post or review the connector response.'
        : 'Connect QuickBooks, Xero, NetSuite, or SAP before Clearledgr can post this invoice.',
    );
  }

  // Validation gate warnings surfaced as blockers
  const gateReasons = Array.isArray(item?.validation_reasons) ? item.validation_reasons : [];
  for (const reason of gateReasons) {
    const code = String(reason?.code || '').trim();
    if (code === 'period_locked') {
      add('period_locked', 'Period is locked', reason.message || 'Cannot post — accounting period is closed.');
    } else if (code === 'payment_terms_mismatch') {
      add('terms_mismatch', 'Payment terms changed', reason.message || 'Invoice terms differ from vendor profile.');
    } else if (code === 'bank_details_mismatch_from_invoice') {
      add('bank_change', 'Bank details changed', reason.message || 'Vendor bank details differ from previous invoices.');
    } else if (code === 'invalid_vendor_tax_id') {
      add('tax_id', 'Invalid vendor tax ID', reason.message || 'Vendor tax ID format is invalid.');
    } else if (code === 'invalid_gl_code') {
      add('gl_code', 'Invalid GL code', reason.message || 'GL code not found in chart of accounts.');
    }
  }

  if (blockers.length === 0 && state === 'received') {
    add(
      'received',
      isInvoiceDocument ? 'Ready for approval' : 'Needs finance review',
      isInvoiceDocument
        ? 'This invoice is ready to send for approval.'
        : getNonInvoiceWorkflowGuidance(documentType),
    );
  }

  if (blockers.length === 0 && state === 'validated') {
    add(
      'validated',
      isInvoiceDocument && needsEntityRouting(item, state, documentType)
        ? 'Resolve entity route'
        : (isInvoiceDocument ? 'Ready for approval' : `Ready to review ${documentLabel}`),
      isInvoiceDocument
        ? (
          needsEntityRouting(item, state, documentType)
            ? 'Choose the correct legal entity before sending this invoice for approval.'
            : 'Checks are complete and the invoice is ready to send for approval.'
        )
        : getNonInvoiceWorkflowGuidance(documentType),
    );
  }

  return blockers.slice(0, 4);
}

function EvidenceChecklist({ entries }) {
  return html`
    <div class="cl-evidence-section" aria-label="Evidence checklist">
      <div class="cl-section-title">Evidence checklist</div>
      <div class="cl-evidence-list">
        ${entries.map((entry) => html`
          <div key=${entry.key} class="cl-evidence-row">
            <div class="cl-evidence-main">
              <span class="cl-evidence-label">${entry.label}</span>
              ${entry.detail && html`<span class="cl-evidence-detail">${entry.detail}</span>`}
            </div>
            <span class="cl-evidence-status" data-status=${entry.status}>
              <span class="cl-evidence-status-pill">${entry.text}</span>
            </span>
          </div>
        `)}
      </div>
    </div>
  `;
}

function FieldReviewPanel({ blockers, pauseReason, onResolve = null, resolvingField = '' }) {
  if ((!Array.isArray(blockers) || blockers.length === 0) && !pauseReason) return null;
  return html`
    <div class="cl-review-panel" aria-label="Field review">
      <div class="cl-section-title">Check these fields</div>
      ${pauseReason && html`<div class="cl-review-copy">${pauseReason}</div>`}
      ${(blockers || []).map((blocker) => html`
        <div key=${`${blocker.field || 'field'}-${blocker.kind || 'review'}`} class="cl-review-card">
          <div class="cl-review-card-title">
            ${blocker.kind === 'confidence'
              ? `Confirm ${(blocker.field_label || 'field').toLowerCase()}`
              : `Choose the correct ${(blocker.field_label || 'field').toLowerCase()}`}
          </div>
          ${blocker.kind === 'confidence' && html`
            <div class="cl-review-row">
              <span class="cl-review-label">Clearledgr read</span>
              <span class="cl-review-value">${blocker.current_value_display || 'Not found'}</span>
            </div>
          `}
          ${blocker.kind === 'confidence' && blocker.current_source_label && html`
            <div class="cl-review-row">
              <span class="cl-review-label">Read from</span>
              <span class="cl-review-value">${blocker.current_source_label}</span>
            </div>
          `}
          ${blocker.email_value !== null && blocker.email_value !== undefined && html`
            <div class="cl-review-row">
              <span class="cl-review-label">Email says</span>
              <span class="cl-review-value">${blocker.email_value_display}</span>
            </div>
          `}
          ${blocker.attachment_value !== null && blocker.attachment_value !== undefined && html`
            <div class="cl-review-row">
              <span class="cl-review-label">Attachment says</span>
              <span class="cl-review-value">${blocker.attachment_value_display}</span>
            </div>
          `}
          ${blocker.kind === 'source_conflict' && html`
            <div class="cl-review-row">
              <span class="cl-review-label">Current choice</span>
              <span class="cl-review-value">
                ${blocker.winning_source_label || 'Needs review'}
                ${blocker.winning_value_display ? ` (${blocker.winning_value_display})` : ''}
              </span>
            </div>
          `}
          <div class="cl-review-why">${blocker.winner_reason || blocker.reason_label || blocker.paused_reason}</div>
          ${blocker.auto_check_note && html`<div class="cl-review-why">${blocker.auto_check_note}</div>`}
          ${typeof onResolve === 'function' && html`
            <div class="cl-thread-actions" style="margin-top:8px">
              ${blocker.email_value !== null && blocker.email_value !== undefined && html`
                <button
                  class="cl-btn cl-btn-secondary cl-btn-small"
                  onClick=${() => onResolve(blocker, 'email')}
                  disabled=${Boolean(resolvingField === `${blocker.field}:email`)}
                >
                  ${resolvingField === `${blocker.field}:email` ? 'Saving…' : 'Use email'}
                </button>
              `}
              ${blocker.attachment_value !== null && blocker.attachment_value !== undefined && html`
                <button
                  class="cl-btn cl-btn-secondary cl-btn-small"
                  onClick=${() => onResolve(blocker, 'attachment')}
                  disabled=${Boolean(resolvingField === `${blocker.field}:attachment`)}
                >
                  ${resolvingField === `${blocker.field}:attachment` ? 'Saving…' : 'Use attachment'}
                </button>
              `}
              <button
                class="cl-btn cl-btn-secondary cl-btn-small"
                onClick=${() => onResolve(blocker, 'manual')}
                disabled=${Boolean(resolvingField === `${blocker.field}:manual`)}
              >
                ${resolvingField === `${blocker.field}:manual` ? 'Saving…' : 'Enter manually'}
              </button>
            </div>
          `}
        </div>
      `)}
    </div>
  `;
}

function AuditRowCard({ row }) {
  if (!row) return null;
  return html`
    <div class="cl-audit-row" data-importance=${row.importance} data-severity=${row.severity}>
      <div class="cl-audit-main">
        <div class="cl-audit-main-copy">
          <div class="cl-audit-type">${row.title}</div>
          <div class="cl-audit-badges">
            <span class="cl-audit-badge" data-importance=${row.importance}>${row.importanceLabel}</span>
            ${row.category && html`<span class="cl-audit-badge" data-kind="category">${row.category.replace(/_/g, ' ')}</span>`}
          </div>
        </div>
        ${row.timestamp && html`<div class="cl-audit-time">${row.timestamp}</div>`}
      </div>
      <div class="cl-audit-detail">${row.detail}</div>
      ${(row.evidenceLabel || row.evidenceDetail) && html`
        <div class="cl-audit-evidence">
          ${row.evidenceLabel && html`<span class="cl-audit-evidence-label">${row.evidenceLabel}</span>`}
          <span>${row.evidenceDetail || 'Saved on the record.'}</span>
        </div>
      `}
      ${row.actionHint && !row.isBackground && html`<div class="cl-audit-hint">Next: ${row.actionHint}</div>`}
    </div>
  `;
}

function AuditDisclosure({ events, loading }) {
  const totalEvents = Array.isArray(events) ? events.length : 0;
  const { primaryRows, secondaryRows, primaryHiddenCount, secondaryHiddenCount } = partitionAuditEvents(events, {
    primaryLimit: 4,
    secondaryLimit: 2,
  });
  return html`
    <details class="cl-details cl-audit-disclosure">
      <summary class="cl-audit-disclosure-summary">View audit${totalEvents ? ` (${totalEvents})` : ''}</summary>
      <div class="cl-audit-list">
        ${loading && html`<div class="cl-empty">Loading audit…</div>`}
        ${!loading && totalEvents === 0 && html`<div class="cl-empty">No audit events yet.</div>`}
        ${!loading && primaryRows.length > 0 && html`
          <div class="cl-audit-group">
            <div class="cl-audit-section-title">Key history</div>
            ${primaryRows.map((row, index) => html`<${AuditRowCard} key=${row.event?.id || index} row=${row} />`)}
            ${primaryHiddenCount > 0 && html`<div class="cl-audit-more">+${primaryHiddenCount} more key events in the full record.</div>`}
          </div>
        `}
        ${!loading && secondaryRows.length > 0 && html`
          <details class="cl-audit-secondary">
            <summary class="cl-audit-secondary-summary">
              Background activity (${secondaryRows.length + secondaryHiddenCount})
            </summary>
            <div class="cl-audit-group">
              ${secondaryRows.map((row, index) => html`<${AuditRowCard} key=${row.event?.id || `secondary-${index}`} row=${row} />`)}
              ${secondaryHiddenCount > 0 && html`<div class="cl-audit-more">+${secondaryHiddenCount} more background events in the full record.</div>`}
            </div>
          </details>
        `}
      </div>
    </details>
  `;
}

function AuthPrompt({ queueManager }) {
  const s = useStore();
  const gmail = s.gmailIntegration || {};
  const canOpenConnections = hasAdminAccessRole(s.currentUserRole);
  const goConnections = useCallback(() => {
    if (!navigateInboxRoute('clearledgr/connections', store.sdk)) {
      showToast('Unable to open Connections', 'error');
    }
  }, []);
  const [authorize, pending] = useAction(async () => {
    const result = await queueManager?.authorizeGmailNow?.();
    const ok = Boolean(result?.success || result?.authorized || result?.status === 'ok');
    const started = String(result?.status || '').toLowerCase() === 'started';
    if (started) {
      showToast('Gmail authorization started.', 'info');
      return;
    }
    if (ok) {
      showToast('Gmail connected', 'success');
    } else {
      const authMessage = queueManager?.describeAuthResult?.(result) || {};
      showToast(authMessage.toast || result?.error || 'Authorization failed', authMessage.severity || 'error');
    }
    if (ok && queueManager?.refreshQueue) {
      await queueManager.refreshQueue();
    }
  });

  return html`
    <div class="cl-section cl-auth-panel">
      <div class="cl-section-title">Connect Gmail</div>
      <div class="cl-auth-copy">
        ${gmail?.requires_reconnect
          ? 'Reconnect Gmail to keep this inbox connected.'
          : 'Connect Gmail once so Clearledgr can keep working in this inbox.'}
      </div>
      <div class="cl-thread-actions">
        <button class="cl-btn cl-primary-cta" onClick=${authorize} disabled=${pending}>
          ${pending ? 'Connecting…' : (gmail?.requires_reconnect ? 'Reconnect Gmail' : 'Connect Gmail')}
        </button>
        ${canOpenConnections && html`
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${goConnections}>Connections</button>
        `}
      </div>
    </div>
  `;
}

function AgentViewSection({ view, item, fallbackNextMove = 'Review this record' }) {
  if (!view?.hasContext) return null;
  const needsReview = Boolean(item?.requires_field_review || view.nextActionType === 'human_field_review');
  const uncertaintyLabel = view.highlights.slice(0, 2).join(' · ');
  const nextMove = view.nextActionLabel || fallbackNextMove || view.currentStateLabel || view.statusLabel || 'Review this record';
  const whyNow = view.beliefReason || (needsReview
    ? 'Clearledgr paused because this invoice still needs a quick check before it can continue.'
    : 'Clearledgr is holding the current workflow context on this record.');
  const ownerLine = view.nextActionResponsibility || '';
  const tone = uncertaintyLabel ? 'warning' : 'good';
  const sectionTitle = needsReview ? 'Before Clearledgr continues' : 'What happens next';
  const reasonLabel = needsReview ? 'Why it paused' : 'Why it is waiting';

  return html`
    <div class="cl-section" aria-label=${sectionTitle}>
      <div class="cl-section-title">${sectionTitle}</div>
      <div class="cl-operator-brief" data-tone=${tone}>
        <div class="cl-operator-brief-row">
          <div class="cl-operator-brief-label">Next step</div>
          <div class="cl-operator-brief-text">${nextMove}</div>
          ${ownerLine && html`<div class="cl-operator-brief-outcome">${ownerLine}</div>`}
        </div>
        <div class="cl-operator-brief-row">
          <div class="cl-operator-brief-label">${reasonLabel}</div>
          <div class="cl-operator-brief-text">${whyNow}</div>
        </div>
        ${uncertaintyLabel && html`
          <div class="cl-operator-brief-row">
            <div class="cl-operator-brief-label">Needs attention</div>
            <div class="cl-operator-brief-text">${uncertaintyLabel}</div>
            <div class="cl-operator-brief-outcome">${view.highlights.length} open item${view.highlights.length === 1 ? '' : 's'}</div>
          </div>
        `}
      </div>
    </div>
  `;
}

function RelatedRecordMiniRow({ label, item, onOpen }) {
  if (!item?.id) return null;
  return html`
    <div class="cl-mini-card">
      <div class="cl-mini-card-main">
        <div class="cl-mini-card-copy">
          <div class="cl-mini-card-label">${label}</div>
          <div class="cl-mini-card-title">${item.vendor_name || 'Unknown vendor'} · ${item.invoice_number || 'No invoice #'}</div>
          <div class="cl-mini-card-meta">
            ${formatAmount(item.amount, item.currency)} · ${String(item.state || 'received').replace(/_/g, ' ')}
          </div>
        </div>
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${onOpen}>Open</button>
      </div>
    </div>
  `;
}

function RelatedRecordsSection({ item, contextPayload, onOpenRecord, onOpenVendor }) {
  const relatedRecords = contextPayload?.related_records || {};
  const linkedFinanceDocuments = Array.isArray(item?.linked_finance_documents) ? item.linked_finance_documents.slice(0, 4) : [];
  const rows = [];
  if (relatedRecords?.supersession?.previous_item) {
    rows.push({ key: `prev-${relatedRecords.supersession.previous_item.id}`, label: 'Supersedes', item: relatedRecords.supersession.previous_item });
  }
  if (relatedRecords?.supersession?.next_item) {
    rows.push({ key: `next-${relatedRecords.supersession.next_item.id}`, label: 'Superseded by', item: relatedRecords.supersession.next_item });
  }
  (relatedRecords?.same_invoice_number_items || []).slice(0, 2).forEach((relatedItem) => {
    rows.push({ key: `same-${relatedItem.id}`, label: 'Same invoice number', item: relatedItem });
  });
  (relatedRecords?.vendor_recent_items || []).slice(0, 2).forEach((relatedItem) => {
    rows.push({ key: `vendor-${relatedItem.id}`, label: 'Recent vendor item', item: relatedItem });
  });
  linkedFinanceDocuments.forEach((relatedItem, index) => {
    rows.push({
      key: `linked-${relatedItem.source_ap_item_id || index}`,
      label: `${getDocumentTypeLabel(relatedItem.document_type || 'other')} linked`,
      item: {
        id: relatedItem.source_ap_item_id,
        vendor_name: relatedItem.vendor_name,
        invoice_number: relatedItem.invoice_number,
        amount: relatedItem.amount,
        currency: relatedItem.currency,
        state: relatedItem.outcome,
      },
    });
  });
  if (rows.length === 0) return null;

  return html`
    <div class="cl-section" aria-label="Related records">
      <div class="cl-section-head">
        <div class="cl-section-title">Related records</div>
        ${(item?.vendor_name || item?.vendor) && html`
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${onOpenVendor}>Vendor</button>
        `}
      </div>
      <div class="cl-card-stack">
        ${rows.slice(0, 6).map((entry) => html`
          <${RelatedRecordMiniRow}
            key=${entry.key}
            label=${entry.label}
            item=${entry.item}
            onOpen=${() => onOpenRecord(entry.item)}
          />
        `)}
      </div>
    </div>
  `;
}

function FilesSection({ item, contextPayload, files, queueManager, readOnlyMode }) {
  const attachmentNames = Array.isArray(item?.attachment_names) ? item.attachment_names.filter(Boolean) : [];
  const dmsDocuments = Array.isArray(contextPayload?.web?.dms_documents)
    ? contextPayload.web.dms_documents
    : (Array.isArray(contextPayload?.dms_documents?.documents) ? contextPayload.dms_documents.documents : []);
  const procurementDocs = Array.isArray(contextPayload?.web?.procurement)
    ? contextPayload.web.procurement
    : [];
  const linkedFiles = Array.isArray(files) ? files : [];
  const [labelDraft, setLabelDraft] = useState('');
  const [urlDraft, setUrlDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const fileCount = attachmentNames.length + dmsDocuments.length + procurementDocs.length + linkedFiles.length;
  if (!fileCount && readOnlyMode) return null;

  const addFileLink = async () => {
    const label = String(labelDraft || '').trim();
    const url = String(urlDraft || '').trim();
    if (!label || !url) return;
    setSaving(true);
    try {
      const result = await queueManager.addItemFileLink(item, { label, url, file_type: 'link', source: 'gmail_sidebar' });
      const ok = String(result?.status || '').toLowerCase() === 'created';
      showToast(ok ? 'File link added' : (result?.reason || 'Could not add file link'), ok ? 'success' : 'error');
      if (ok) {
        setLabelDraft('');
        setUrlDraft('');
      }
    } finally {
      setSaving(false);
    }
  };

  return html`
    <div class="cl-section" aria-label="Files">
      <div class="cl-section-title">Files and evidence</div>
      ${!readOnlyMode && html`
        <div class="cl-inline-form cl-inline-form-wide">
          <input class="cl-input" value=${labelDraft} placeholder="Link label" onInput=${(event) => setLabelDraft(event.target.value)} />
          <input class="cl-input" value=${urlDraft} placeholder="Paste Drive, DMS, or procurement URL" onInput=${(event) => setUrlDraft(event.target.value)} />
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${addFileLink} disabled=${saving}>${saving ? 'Saving…' : 'Add link'}</button>
        </div>
      `}
      <div class="cl-card-stack">
        ${linkedFiles.length === 0 && fileCount === 0 && html`<div class="cl-muted">No linked files on this record yet.</div>`}
        ${linkedFiles.map((file) => html`
          <div key=${file.id} class="cl-evidence-row">
            <div class="cl-evidence-main">
              <span class="cl-evidence-label">${file.label || file.file_name || 'Linked file'}</span>
              <span class="cl-evidence-detail">
                ${file.note || file.file_name || file.url || 'Linked on this finance record'}
                ${file.url && html`
                  <span>
                    · 
                    <a href=${file.url} target="_blank" rel="noreferrer noopener" style="color:var(--cl-accent);text-decoration:none">Open link</a>
                  </span>
                `}
              </span>
            </div>
            <span class="cl-evidence-status" data-status="ok">${file.file_type || 'Linked'}</span>
          </div>
        `)}
        ${attachmentNames.map((name, index) => html`
          <div key=${`attachment-${index}`} class="cl-evidence-row">
            <div class="cl-evidence-main">
              <span class="cl-evidence-label">Email attachment</span>
              <span class="cl-evidence-detail">${name}</span>
            </div>
            <span class="cl-evidence-status" data-status="ok">Attached</span>
          </div>
        `)}
        ${dmsDocuments.slice(0, 3).map((doc, index) => html`
          <div key=${`dms-${index}`} class="cl-evidence-row">
            <div class="cl-evidence-main">
              <span class="cl-evidence-label">DMS document</span>
              <span class="cl-evidence-detail">${doc?.subject || doc?.source_ref || 'Linked document'}</span>
            </div>
            <span class="cl-evidence-status" data-status="ok">Linked</span>
          </div>
        `)}
        ${procurementDocs.slice(0, 2).map((doc, index) => html`
          <div key=${`proc-${index}`} class="cl-evidence-row">
            <div class="cl-evidence-main">
              <span class="cl-evidence-label">Procurement</span>
              <span class="cl-evidence-detail">${doc?.subject || doc?.source_ref || 'Procurement evidence'}</span>
            </div>
            <span class="cl-evidence-status" data-status="ok">Linked</span>
          </div>
        `)}
      </div>
    </div>
  `;
}

function EditableFieldRow({ label, value, fieldKey, type = 'text', placeholder = '', onSave, savingField }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value ?? '');

  useEffect(() => {
    setDraft(value ?? '');
  }, [value]);

  const save = async () => {
    await onSave(fieldKey, draft);
    setEditing(false);
  };

  return html`
    <div class="cl-field-row">
      <div class="cl-field-row-body">
        <div class="cl-field-main">
          <div class="cl-field-label">${label}</div>
          ${editing
            ? html`
                <input
                  class="cl-input cl-field-input"
                  type=${type}
                  value=${draft ?? ''}
                  placeholder=${placeholder}
                  onInput=${(event) => setDraft(event.target.value)}
                />
              `
            : html`<div class="cl-field-value">${value || '—'}</div>`}
        </div>
        ${editing
          ? html`
              <div class="cl-mini-card-actions">
                <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${() => { setDraft(value ?? ''); setEditing(false); }}>Cancel</button>
                <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${save} disabled=${savingField === fieldKey}>
                  ${savingField === fieldKey ? 'Saving…' : 'Save'}
                </button>
              </div>
            `
          : html`<button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${() => setEditing(true)}>Edit</button>`}
      </div>
    </div>
  `;
}

function RecordEditorSection({ item, queueManager, readOnlyMode }) {
  const [savingField, setSavingField] = useState('');

  if (readOnlyMode) return null;

  const saveField = async (fieldKey, fieldValue) => {
    setSavingField(fieldKey);
    try {
      const payload = { [fieldKey]: fieldKey === 'amount' && fieldValue !== '' ? Number(fieldValue) : fieldValue };
      const result = await queueManager.updateRecordFields(item, payload);
      const ok = String(result?.status || '').toLowerCase() === 'updated';
      showToast(ok ? `${fieldKey.replace(/_/g, ' ')} updated` : (result?.reason || 'Could not update record'), ok ? 'success' : 'error');
    } finally {
      setSavingField('');
    }
  };

  return html`
    <div class="cl-section" aria-label="Edit record">
      <div class="cl-section-title">Edit record</div>
      <div class="cl-field-list">
        <${EditableFieldRow} label="Vendor" fieldKey="vendor_name" value=${item?.vendor_name || item?.vendor || ''} onSave=${saveField} savingField=${savingField} />
        <${EditableFieldRow} label="Invoice #" fieldKey="invoice_number" value=${item?.invoice_number || ''} onSave=${saveField} savingField=${savingField} />
        <${EditableFieldRow} label="Amount" fieldKey="amount" type="number" value=${item?.amount ?? ''} onSave=${saveField} savingField=${savingField} />
        <${EditableFieldRow} label="Due date" fieldKey="due_date" type="date" value=${item?.due_date || ''} onSave=${saveField} savingField=${savingField} />
        <${EditableFieldRow} label="PO number" fieldKey="po_number" value=${item?.po_number || ''} onSave=${saveField} savingField=${savingField} />
      </div>
    </div>
  `;
}

function TaskSection({ item, tasks, queueManager, readOnlyMode = false }) {
  const [title, setTitle] = useState('');
  const [dueDate, setDueDate] = useState('');
  const [creating, setCreating] = useState(false);
  const [commentDrafts, setCommentDrafts] = useState({});
  const myEmail = String(queueManager?.runtimeConfig?.userEmail || '').trim();

  const createTask = async () => {
    if (!title.trim()) return;
    setCreating(true);
    try {
      const result = await queueManager.createTask(item, {
        title: title.trim(),
        due_date: dueDate || undefined,
        task_type: 'follow_up',
        priority: 'medium',
      });
      const ok = String(result?.status || '').toLowerCase() === 'created';
      showToast(ok ? 'Task added' : (result?.reason || 'Could not add task'), ok ? 'success' : 'error');
      if (ok) {
        setTitle('');
        setDueDate('');
      }
    } finally {
      setCreating(false);
    }
  };

  const updateStatus = async (task, status) => {
    const result = await queueManager.updateTaskStatus(task.task_id, { status }, item.id);
    const ok = String(result?.status || '').toLowerCase() === 'updated';
    showToast(ok ? 'Task updated' : (result?.reason || 'Could not update task'), ok ? 'success' : 'error');
  };

  const assignToMe = async (task) => {
    if (!myEmail) return;
    const result = await queueManager.assignTask(task.task_id, { assignee_email: myEmail }, item.id);
    const ok = String(result?.status || '').toLowerCase() === 'updated';
    showToast(ok ? 'Task assigned' : (result?.reason || 'Could not assign task'), ok ? 'success' : 'error');
  };

  const addComment = async (task) => {
    const comment = String(commentDrafts[task.task_id] || '').trim();
    if (!comment) return;
    const result = await queueManager.addTaskComment(task.task_id, { comment }, item.id);
    const ok = String(result?.status || '').toLowerCase() === 'created';
    showToast(ok ? 'Comment added' : (result?.reason || 'Could not add comment'), ok ? 'success' : 'error');
    if (ok) {
      setCommentDrafts((current) => ({ ...current, [task.task_id]: '' }));
    }
  };

  return html`
    <div class="cl-section" aria-label="Tasks">
      <div class="cl-section-title">Tasks</div>
      ${!readOnlyMode && html`
        <div class="cl-inline-form cl-inline-form-task">
          <input class="cl-input" value=${title} placeholder="Add follow-up task" onInput=${(event) => setTitle(event.target.value)} />
          <input class="cl-input" type="date" value=${dueDate} onInput=${(event) => setDueDate(event.target.value)} />
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${createTask} disabled=${creating}>${creating ? 'Adding…' : 'Add'}</button>
        </div>
      `}
      <div class="cl-card-stack">
        ${(tasks || []).length === 0 && html`<div class="cl-muted">No tasks on this record yet.</div>`}
        ${(tasks || []).slice(0, 6).map((task) => html`
          <div key=${task.task_id} class="cl-mini-card">
            <div class="cl-mini-card-main">
              <div class="cl-mini-card-copy">
                <div class="cl-mini-card-title">${task.title}</div>
                <div class="cl-mini-card-meta">
                  ${String(task.status || 'open').replace(/_/g, ' ')}
                  ${task.due_date ? ` · Due ${task.due_date}` : ''}
                  ${task.assignee_email ? ` · ${task.assignee_email}` : ''}
                </div>
                ${task.description && html`<div class="cl-mini-card-body">${task.description}</div>`}
              </div>
              ${!readOnlyMode && html`
                <div class="cl-mini-card-actions">
                  ${task.status !== 'in_progress' && task.status !== 'completed' && html`
                    <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${() => updateStatus(task, 'in_progress')}>Start</button>
                  `}
                  ${task.status !== 'completed' && html`
                    <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${() => updateStatus(task, 'completed')}>Done</button>
                  `}
                  ${myEmail && task.assignee_email !== myEmail && html`
                    <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${() => assignToMe(task)}>Assign me</button>
                  `}
                </div>
              `}
            </div>
            ${!readOnlyMode && html`
              <div class="cl-inline-form cl-inline-form-comment">
                <input
                  class="cl-input"
                  value=${commentDrafts[task.task_id] || ''}
                  placeholder="Add task comment"
                  onInput=${(event) => setCommentDrafts((current) => ({ ...current, [task.task_id]: event.target.value }))}
                />
                <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${() => addComment(task)}>Comment</button>
              </div>
            `}
            ${Array.isArray(task.comments) && task.comments.length > 0 && html`
              <div class="cl-mini-card-comments">
                ${task.comments.slice(0, 2).map((comment) => html`
                  <div key=${comment.comment_id} class="cl-mini-card-comment">
                    <strong>${comment.user_email}</strong>: ${comment.comment}
                  </div>
                `)}
              </div>
            `}
          </div>
        `)}
      </div>
    </div>
  `;
}

function NotesSection({ item, notes, queueManager, readOnlyMode }) {
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);

  const addNote = async () => {
    const body = String(draft || '').trim();
    if (!body) return;
    setSaving(true);
    try {
      const result = await queueManager.addItemNote(item, { body });
      const ok = String(result?.status || '').toLowerCase() === 'created';
      showToast(ok ? 'Note added' : (result?.reason || 'Could not add note'), ok ? 'success' : 'error');
      if (ok) setDraft('');
    } finally {
      setSaving(false);
    }
  };

  return html`
    <div class="cl-section" aria-label="Notes">
      <div class="cl-section-title">Notes</div>
      ${!readOnlyMode && html`
        <div class="cl-inline-form cl-inline-form-comment">
          <input class="cl-input" value=${draft} placeholder="Add note on this record" onInput=${(event) => setDraft(event.target.value)} />
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${addNote} disabled=${saving}>${saving ? 'Saving…' : 'Add note'}</button>
        </div>
      `}
      <div class="cl-card-stack">
        ${(notes || []).length === 0 && html`<div class="cl-muted">No notes on this record yet.</div>`}
        ${(notes || []).slice(0, 5).map((note) => html`
          <div key=${note.id} class="cl-mini-card">
            <div class="cl-mini-card-meta">${note.author || 'Operator'}${note.created_at ? ` · ${new Date(note.created_at).toLocaleString([], { hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric' })}` : ''}</div>
            <div class="cl-mini-card-body">${note.body}</div>
          </div>
        `)}
      </div>
    </div>
  `;
}

function CommentsSection({ item, comments, queueManager, readOnlyMode }) {
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);

  const addComment = async () => {
    const body = String(draft || '').trim();
    if (!body) return;
    setSaving(true);
    try {
      const result = await queueManager.addItemComment(item, { body });
      const ok = String(result?.status || '').toLowerCase() === 'created';
      showToast(ok ? 'Comment added' : (result?.reason || 'Could not add comment'), ok ? 'success' : 'error');
      if (ok) setDraft('');
    } finally {
      setSaving(false);
    }
  };

  return html`
    <div class="cl-section" aria-label="Comments">
      <div class="cl-section-title">Comments</div>
      ${!readOnlyMode && html`
        <div class="cl-inline-form cl-inline-form-comment">
          <input class="cl-input" value=${draft} placeholder="Add discussion on this record" onInput=${(event) => setDraft(event.target.value)} />
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${addComment} disabled=${saving}>${saving ? 'Saving…' : 'Add comment'}</button>
        </div>
      `}
      <div class="cl-card-stack">
        ${(comments || []).length === 0 && html`<div class="cl-muted">No comments on this record yet.</div>`}
        ${(comments || []).slice(0, 5).map((comment) => html`
          <div key=${comment.id} class="cl-mini-card">
            <div class="cl-mini-card-meta">${comment.author || 'Operator'}${comment.created_at ? ` · ${new Date(comment.created_at).toLocaleString([], { hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric' })}` : ''}</div>
            <div class="cl-mini-card-body">${comment.body}</div>
          </div>
        `)}
      </div>
    </div>
  `;
}

function WorkPanel({ item, queueManager }) {
  const s = useStore();
  const actorRole = s.currentUserRole || queueManager?.currentUserRole || 'operator';
  const state = normalizeWorkState(item?.state || 'received');
  const documentType = normalizeDocumentType(item?.document_type);
  const documentLabel = getDocumentTypeLabel(documentType);
  const isInvoiceDocument = isInvoiceDocumentType(documentType);
  const vendor = item.vendor_name || item.vendor || item.sender || 'Unknown vendor';
  const amountLabel = formatAmount(item.amount, item.currency);
  const invoiceNumber = item.invoice_number || 'N/A';
  const dueDate = item.due_date || 'N/A';
  const referenceText = invoiceNumber !== 'N/A' ? `${documentLabel} #: ${invoiceNumber}` : documentLabel;
  const taxAmount = item.tax_amount ? formatAmount(item.tax_amount, item.currency) : '';
  const discountAmount = item.discount_amount ? formatAmount(item.discount_amount, item.currency) : '';
  const metaParts = [amountLabel];
  if (taxAmount) metaParts.push(`Tax: ${taxAmount}`);
  if (discountAmount) metaParts.push(`Disc: -${discountAmount}`);
  metaParts.push(referenceText);
  if (isInvoiceDocument) {
    metaParts.push(`Due: ${dueDate}`);
    metaParts.push(item.po_number ? `PO: ${item.po_number}` : 'No PO');
    if (item.payment_terms) metaParts.push(item.payment_terms);
  }
  const metaLine = metaParts.join(' · ');
  const contextPayload = item?.id ? s.contextState.get(item.id) || null : null;
  const budgetContext = normalizeBudgetContext(contextPayload || {}, item);
  const blockers = getBlockers(item, state, budgetContext, documentType);
  const fieldReviewBlockers = getFieldReviewBlockers(item);
  const evidence = getEvidenceChecklistEntries(item, state, contextPayload);
  const auditEvents = s.auditState.itemId === item.id && Array.isArray(s.auditState.events) ? s.auditState.events : [];
  const pauseReason = getWorkflowPauseReason(item);
  const financeEffectSummary = item?.finance_effect_summary && typeof item.finance_effect_summary === 'object'
    ? item.finance_effect_summary
    : {};
  const financeEffectBlockers = getFinanceEffectBlockers(item);
  const financeEffectNotice = getFinanceEffectNotice(item);
  const approvalFollowup = item?.approval_followup && typeof item.approval_followup === 'object'
    ? item.approval_followup
    : {};
  const entityRouting = item?.entity_routing && typeof item.entity_routing === 'object'
    ? item.entity_routing
    : {};
  const entityCandidates = Array.isArray(item?.entity_candidates)
    ? item.entity_candidates
    : (Array.isArray(entityRouting?.candidates) ? entityRouting.candidates : []);
  const resumeWorkflowEligible = !pauseReason && shouldOfferResumeWorkflow(item, auditEvents, documentType);
  const stateNotice = resumeWorkflowEligible
    ? 'Field review is cleared. Resume workflow to continue the posting step.'
    : getWorkStateNotice(state, documentType, item);
  const smartDefault = item?.exception_code ? getExceptionReason(item.exception_code) : '';
  const canOpenSource = Boolean(getSourceThreadId(item) || getSourceMessageId(item) || item.subject);
  const entityNeedsReview = needsEntityRouting(item, state, documentType);
  const agentView = getAgentMemoryView(item);
  const tasks = item?.id ? (s.tasksState.get(item.id) || []) : [];
  const notes = item?.id ? (s.notesState.get(item.id) || []) : [];
  const comments = item?.id ? (s.commentsState.get(item.id) || []) : [];
  const files = item?.id ? (s.filesState.get(item.id) || []) : [];

  const [optimisticState, setOptimisticState] = useState(null);
  const displayState = normalizeWorkState(optimisticState || state);
  const readOnlyMode = !hasOpsAccessRole(actorRole);
  const [dialog, openDialog] = useActionDialog();
  const [resolvingFieldKey, setResolvingFieldKey] = useState('');
  const pipelineScope = {
    orgId: queueManager?.runtimeConfig?.organizationId || 'default',
    userEmail: queueManager?.runtimeConfig?.userEmail || '',
  };
  const gotoRoute = useCallback((routeId, params) => navigateInboxRoute(routeId, store.sdk, params), []);

  const [doApproval, approvalPending] = useAction(async () => {
    setOptimisticState('needs_approval');
    const result = await queueManager.requestApproval(item);
    const ok = ['needs_approval', 'pending_approval'].includes(String(result?.status || '').toLowerCase());
    showToast(
      ok ? 'Approval request sent' : (humanizeActionFailure(result?.reason) || 'Unable to route approval'),
      ok ? 'success' : 'error'
    );
    if (!ok) setOptimisticState(null);
    setOptimisticState(null);
  });

  const [doNudge, nudgePending] = useAction(async () => {
    const result = await queueManager.nudgeApproval(item);
    const ok = didSendApprovalReminder(result);
    showToast(
      ok ? 'Approval reminder sent' : (humanizeActionFailure(result?.reason || result?.fallback?.reason) || 'Unable to send reminder'),
      ok ? 'success' : 'error'
    );
    if (ok) await queueManager.refreshQueue();
  });

  const [doEscalateApproval, escalatePending] = useAction(async () => {
    const result = await queueManager.escalateApproval(item);
    const ok = String(result?.status || '').toLowerCase() === 'escalated';
    showToast(ok ? 'Approval escalated' : (humanizeActionFailure(result?.reason) || 'Unable to escalate approval'), ok ? 'success' : 'error');
    if (ok) await queueManager.refreshQueue();
  });

  const [doReassignApproval, reassignPending] = useAction(async () => {
    const assignee = await openDialog({
      actionType: 'generic',
      title: 'Reassign approval',
      label: 'New approver',
      message: 'Enter the approver who should own this approval request now.',
      placeholder: 'Approver email or Slack user',
      confirmLabel: 'Reassign',
      cancelLabel: 'Cancel',
      required: true,
      chips: Array.isArray(approvalFollowup?.pending_assignees) ? approvalFollowup.pending_assignees.slice(0, 4) : [],
    });
    if (!assignee) return;
    const result = await queueManager.reassignApproval(item, { assignee });
    const ok = String(result?.status || '').toLowerCase() === 'reassigned';
    showToast(ok ? `Approval reassigned to ${assignee}` : (humanizeActionFailure(result?.reason) || 'Unable to reassign approval'), ok ? 'success' : 'error');
    if (ok) await queueManager.refreshQueue();
  });

  const [doPrepareInfo, prepareInfoPending] = useAction(async () => {
    const result = await queueManager.prepareVendorFollowup(item, {
      reason: 'Request missing invoice details from vendor',
    });
    const status = String(result?.status || '').toLowerCase();
    const ok = ['prepared', 'queued'].includes(status);
    const informational = status === 'waiting_sla';
    showToast(
      ok
        ? 'Info request draft prepared'
        : informational
        ? (humanizeActionFailure(result?.reason) || 'Follow-up already sent')
        : (humanizeActionFailure(result?.reason) || 'Unable to prepare info request'),
      ok ? 'success' : informational ? 'info' : 'error',
    );
    if (ok || informational) await queueManager.refreshQueue();
  });

  const [doRetry, retryPending] = useAction(async () => {
    setOptimisticState('ready_to_post');
    const result = await queueManager.retryFailedPost(item);
    const ok = ['ready_to_post', 'posted', 'completed'].includes(String(result?.status || '').toLowerCase());
    showToast(ok ? 'ERP retry submitted' : (result?.reason || 'Retry failed'), ok ? 'success' : 'error');
    if (!ok) setOptimisticState(null);
    await queueManager.refreshQueue();
    setOptimisticState(null);
  });

  const [doResumeWorkflow, resumePending] = useAction(async () => {
    const confirmed = await openDialog({
      dialogMode: 'confirm',
      actionType: 'resume_workflow',
      title: 'Resume workflow',
      message: 'Review blockers are cleared. Clearledgr will continue the guarded posting step.',
      previewLines: [
        vendor,
        amountLabel,
        referenceText,
        isInvoiceDocument && dueDate && dueDate !== 'N/A' ? `Due: ${dueDate}` : null,
      ].filter(Boolean),
      confirmLabel: 'Resume workflow',
      cancelLabel: 'Cancel',
    });
    if (!confirmed) return;
    const result = await queueManager.retryRecoverableFailure(item, {
      reason: 'Resume workflow after review cleared',
    });
    const status = String(result?.status || '').toLowerCase();
    const ok = ['posted', 'posted_to_erp', 'recovered', 'ready_to_post'].includes(status);
    showToast(
      ok
        ? (status === 'posted' || status === 'posted_to_erp' ? 'Workflow resumed and invoice posted' : 'Workflow resumed')
        : (result?.reason || 'Could not resume workflow'),
      ok ? 'success' : 'error',
    );
    await queueManager.refreshQueue();
  });

  const [doPost, postPending] = useAction(async () => {
    if (!hasErpPostingConnection(item)) {
      showToast('Connect an ERP before posting this invoice.', 'error');
      return;
    }
    setOptimisticState('posted_to_erp');
    const result = await queueManager.approveAndPost(item, { override: false });
    const ok = ['posted', 'approved', 'posted_to_erp'].includes(String(result?.status || '').toLowerCase());
    showToast(ok ? 'Invoice posted to ERP' : (result?.reason || 'ERP posting failed'), ok ? 'success' : 'error');
    if (!ok) setOptimisticState(null);
    await queueManager.refreshQueue();
    setOptimisticState(null);
  });

  const [doReject, rejectPending] = useAction(async () => {
    const reason = await openDialog({
      actionType: 'reject',
      title: 'Reject invoice',
      label: 'Rejection reason',
      confirmLabel: 'Reject',
      defaultValue: smartDefault,
    });
    if (!reason) return;
    const result = await queueManager.rejectInvoice(item, { reason });
    const ok = String(result?.status || '').toLowerCase() === 'rejected';
    showToast(ok ? 'Invoice rejected' : 'Unable to reject invoice', ok ? 'success' : 'error');
    if (ok) {
      await queueManager.refreshQueue();
    }
  });

  const [doPreviewPost, previewPending] = useAction(async () => {
    const confirmed = await openDialog({
      dialogMode: 'confirm',
      actionType: 'preview_erp_post',
      title: 'Preview ERP post',
      message: 'Review this invoice before posting it to the ERP.',
      previewLines: [
        vendor,
        amountLabel,
        referenceText,
        isInvoiceDocument && dueDate && dueDate !== 'N/A' ? `Due: ${dueDate}` : null,
      ].filter(Boolean),
      confirmLabel: 'Post to ERP',
      cancelLabel: 'Cancel',
    });
    if (!confirmed) return;
    await doPost();
  });

  const [doResolveFieldReview, resolvePending] = useAction(async (blocker, source) => {
    if (!item?.id || !queueManager?.resolveFieldReview || !blocker?.field) return;
    const pendingKey = `${blocker.field}:${source}`;
    setResolvingFieldKey(pendingKey);
    let manualValue;
    try {
      if (source === 'manual') {
        manualValue = await openDialog({
          actionType: 'field_review_manual',
          title: `Set ${blocker.field_label || 'field'}`,
          label: `${blocker.field_label || 'Field'} value`,
          message: 'Enter the canonical value that Clearledgr should keep on the AP record.',
          defaultValue: blocker.winning_value ?? '',
          confirmLabel: 'Apply value',
          cancelLabel: 'Cancel',
          required: true,
          chips: [],
        });
        if (manualValue === null) {
          return;
        }
      }

      const result = await queueManager.resolveFieldReview(item, {
        field: blocker.field,
        source,
        manualValue,
        autoResume: true,
      });
      const ok = ['resolved', 'resolved_and_resumed'].includes(String(result?.status || '').toLowerCase());
      if (!ok) {
        showToast(result?.reason || 'Could not resolve blocked field', 'error');
        setResolvingFieldKey('');
        return;
      }

      showToast(
        result?.auto_resumed
          ? `${blocker.field_label || 'Field'} updated and workflow resumed`
          : `${blocker.field_label || 'Field'} updated`,
        'success',
      );
      await queueManager.refreshQueue();
    } finally {
      setResolvingFieldKey('');
    }
  });

  const [doResolveEntityRoute, resolveEntityPending] = useAction(async () => {
    let selection = '';
    if (entityCandidates.length > 1) {
      selection = await openDialog({
        actionType: 'generic',
        title: 'Resolve entity route',
        label: 'Entity code or name',
        message: 'Choose the legal entity Clearledgr should use for this invoice.',
        previewLines: entityCandidates.slice(0, 6).map((candidate) => (
          candidate?.label || candidate?.entity_name || candidate?.entity_code || ''
        )).filter(Boolean),
        placeholder: 'e.g. US-01 or Cowrywise Inc US',
        confirmLabel: 'Resolve entity',
        cancelLabel: 'Cancel',
        required: true,
        chips: entityCandidates.slice(0, 4).map((candidate) => (
          candidate?.entity_code || candidate?.entity_name || candidate?.label || ''
        )).filter(Boolean),
      });
      if (!selection) return;
    }
    const candidate = entityCandidates.length === 1 ? entityCandidates[0] : null;
    const result = await queueManager.resolveEntityRoute(item, {
      selection: selection || candidate?.entity_code || candidate?.entity_name,
      entityId: candidate?.entity_id,
      entityCode: candidate?.entity_code,
      entityName: candidate?.entity_name,
    });
    const ok = String(result?.status || '').toLowerCase() === 'resolved';
    showToast(ok ? 'Entity route resolved' : (humanizeActionFailure(result?.reason) || 'Unable to resolve entity route'), ok ? 'success' : 'error');
    if (ok) await queueManager.refreshQueue();
  });

  const openPipeline = useCallback(() => {
    if (!item?.id) return;
    store.setSelectedItem(String(item.id));
    focusPipelineItem(pipelineScope, item, 'thread');
    if (!gotoRoute('clearledgr/invoices')) {
      showToast('Unable to open invoices', 'error');
    }
  }, [gotoRoute, item, pipelineScope]);
  const openSource = useCallback(() => {
    if (!openSourceEmail(item)) showToast('Unable to open source email', 'error');
  }, [item]);
  const openVendorRecord = useCallback(() => {
    const vendorName = String(item?.vendor_name || item?.vendor || '').trim();
    if (!vendorName) return;
    if (!navigateToVendorRecord(gotoRoute, vendorName)) {
      showToast('Unable to open vendor record', 'error');
    }
  }, [gotoRoute, item]);
  const openRelatedRecord = useCallback((relatedItem) => {
    if (!relatedItem?.id) return;
    focusPipelineItem(pipelineScope, relatedItem, 'related_record');
    if (!navigateToRecordDetail(gotoRoute, relatedItem.id)) {
      showToast('Unable to open related record', 'error');
    }
  }, [gotoRoute, pipelineScope]);

  const basePrimaryAction = (pauseReason || item?.finance_effect_review_required)
    ? null
    : getPrimaryActionConfig(displayState, actorRole, documentType, item);
  const primaryAction = resumeWorkflowEligible && ['preview_erp_post', 'retry_erp_post'].includes(basePrimaryAction?.id)
    ? { id: 'resume_workflow', label: 'Resume workflow' }
    : basePrimaryAction;
  const fallbackNextMove = primaryAction?.label || getDefaultNextMoveLabel(displayState, item, actorRole, documentType);
  const operatorOverrideCopy = getOperatorOverrideCopy(displayState, item, documentType);
  let primaryHandler = null;
  let primaryPending = false;
  let primaryClass = '';
  if (primaryAction?.id === 'request_approval') {
    primaryHandler = doApproval;
    primaryPending = approvalPending;
  } else if (primaryAction?.id === 'resolve_entity_route') {
    primaryHandler = doResolveEntityRoute;
    primaryPending = resolveEntityPending;
  } else if (primaryAction?.id === 'prepare_info_request') {
    primaryHandler = doPrepareInfo;
    primaryPending = prepareInfoPending;
  } else if (primaryAction?.id === 'escalate_approval') {
    primaryHandler = doEscalateApproval;
    primaryPending = escalatePending;
  } else if (primaryAction?.id === 'nudge_approver') {
    primaryHandler = doNudge;
    primaryPending = nudgePending;
  } else if (primaryAction?.id === 'preview_erp_post') {
    primaryHandler = doPreviewPost;
    primaryPending = previewPending || postPending;
    primaryClass = 'cl-btn-approve';
  } else if (primaryAction?.id === 'retry_erp_post') {
    primaryHandler = doRetry;
    primaryPending = retryPending;
  } else if (primaryAction?.id === 'resume_workflow') {
    primaryHandler = doResumeWorkflow;
    primaryPending = resumePending;
    primaryClass = 'cl-btn-approve';
  }

  const showReject = canRejectWorkItem(displayState, actorRole, documentType);
  const showReassign = canReassignApproval(item, displayState, actorRole, documentType);
  const showEscalate = canEscalateApproval(item, displayState, actorRole, documentType) && primaryAction?.id !== 'escalate_approval';
  const showResolveEntity = entityNeedsReview && primaryAction?.id !== 'resolve_entity_route';
  const showNudge = canNudgeApprover(displayState, actorRole, documentType) && primaryAction?.id !== 'nudge_approver';
  const hasSecondaryActions = showReject || showReassign || showEscalate || showResolveEntity || showNudge;

  return html`
    <div id="cl-thread-context" class="cl-thread-card cl-work-surface">
      <div class="cl-thread-header">
        <div class="cl-thread-header-copy">
          <div class="cl-thread-title">${vendor}</div>
          <div class="cl-thread-meta-inline">${metaLine}</div>
        </div>
        <${StatePill} state=${displayState} />
      </div>

      ${blockers.length > 0 && html`
        <div class="cl-blocker-list" aria-label="What is blocking this record">
          ${blockers.map((blocker) => html`
            <div key=${blocker.id} class="cl-blocker-row">
              <div class="cl-blocker-label">${blocker.label}</div>
              ${blocker.detail && html`<div class="cl-blocker-detail">${blocker.detail}</div>`}
            </div>
          `)}
        </div>
      `}

      <${AgentViewSection} view=${agentView} item=${item} fallbackNextMove=${fallbackNextMove} />

      ${Array.isArray(item?.line_items) && item.line_items.length > 0 && html`
        <details class="cl-section cl-disclosure">
          <summary class="cl-disclosure-summary">
            <span class="cl-section-title">Line items</span>
            <span class="cl-disclosure-count">${item.line_items.length}</span>
          </summary>
          <div class="cl-card-stack">
            ${item.line_items.slice(0, 10).map((li, i) => html`
              <div key=${i} class="cl-evidence-row">
                <div class="cl-evidence-copy">
                  <div>${li.description || `Line ${i + 1}`}</div>
                  ${li.gl_code && html`<div class="cl-evidence-detail">GL: ${li.gl_code}</div>`}
                </div>
                <div class="cl-evidence-status">${formatAmount(li.amount || 0, item.currency)}</div>
              </div>
            `)}
          </div>
        </details>
      `}

      ${item?.payment_status && item.payment_status !== 'none' && html`
        <div class="cl-section" aria-label="Payment status">
          <div class="cl-section-title">Payment status</div>
          <div class="cl-evidence-row">
            <div class="cl-evidence-copy">
              <div>Payment</div>
              <div class="cl-evidence-detail">${item.payment_reference || ''}</div>
            </div>
            <div class="cl-evidence-status">
              <span class="cl-state-pill ${item.payment_status}">${(item.payment_status || '').replace(/_/g, ' ')}</span>
            </div>
          </div>
        </div>
      `}

      ${item?.erp_sync_status && item.erp_sync_status !== 'verified' && item.state === 'posted_to_erp' && html`
        <div class="cl-state-note">ERP sync: ${item.erp_sync_status === 'mismatch' ? 'Mismatch detected — verify posting' : item.erp_sync_status}</div>
      `}

      ${pauseReason && fieldReviewBlockers.length === 0 && html`<div class="cl-state-note">${pauseReason}</div>`}
      ${!pauseReason && stateNotice && html`<div class="cl-state-note">${stateNotice}</div>`}
      ${readOnlyMode && html`
        <div class="cl-state-note">Read-only view. You can review this record here, but only operators can take action.</div>
      `}

      ${primaryAction?.label && primaryHandler && html`
        <button class="cl-btn cl-primary-cta ${primaryClass}" onClick=${primaryHandler} disabled=${primaryPending}>
          ${primaryPending ? 'Processing…' : primaryAction.label}
        </button>
      `}

      <div class="cl-thread-links" aria-label="Record links">
        <button class="cl-thread-link-btn" onClick=${openPipeline}>Open in invoices</button>
        ${canOpenSource && html`<button class="cl-thread-link-btn" onClick=${openSource}>Open email</button>`}
        ${(item?.vendor_name || item?.vendor) && html`<button class="cl-thread-link-btn" onClick=${openVendorRecord}>Open vendor record</button>`}
      </div>

      ${hasSecondaryActions && html`
        <details id="cl-agent-actions" class="cl-details cl-operator-overrides">
          <summary class="cl-operator-overrides-summary">
            <span class="cl-operator-overrides-title">${operatorOverrideCopy.title}</span>
            <span class="cl-operator-overrides-count">
              ${[showReject, showReassign, showEscalate, showResolveEntity, showNudge].filter(Boolean).length}
            </span>
          </summary>
          <div class="cl-operator-overrides-copy">${operatorOverrideCopy.detail}</div>
          <div class="cl-thread-actions cl-thread-actions-secondary">
            ${showReject && html`
              <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doReject} disabled=${rejectPending}>Reject</button>
            `}
            ${showReassign && html`
              <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doReassignApproval} disabled=${reassignPending}>
                ${reassignPending ? 'Reassigning…' : 'Reassign approver'}
              </button>
            `}
            ${showEscalate && html`
              <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doEscalateApproval} disabled=${escalatePending}>
                ${escalatePending ? 'Escalating…' : 'Escalate approval'}
              </button>
            `}
            ${showResolveEntity && html`
              <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doResolveEntityRoute} disabled=${resolveEntityPending}>
                ${resolveEntityPending ? 'Resolving…' : 'Resolve entity'}
              </button>
            `}
            ${showNudge && html`
              <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doNudge} disabled=${nudgePending}>Nudge approver</button>
            `}
          </div>
        </details>
      `}

      ${(displayState === 'needs_approval' || entityNeedsReview) && html`
        <div class="cl-section" aria-label="Follow-up and routing">
          <div class="cl-section-title">Follow-up and routing</div>
          <div class="cl-summary-grid">
            ${displayState === 'needs_approval' && html`
              <div class="cl-summary-card">
                <div class="cl-summary-label">Approval wait</div>
                <div class="cl-summary-value">${approvalFollowup?.wait_minutes ? `${approvalFollowup.wait_minutes}m` : '—'}</div>
                <div class="cl-summary-detail">
                  ${approvalFollowup?.escalation_due
                    ? 'Past escalation policy'
                    : (approvalFollowup?.sla_breached ? 'Past reminder SLA' : 'Still within SLA')}
                </div>
              </div>
              <div class="cl-summary-card">
                <div class="cl-summary-label">Pending approvers</div>
                <div class="cl-summary-value cl-summary-value-compact">
                  ${Array.isArray(approvalFollowup?.pending_assignees) && approvalFollowup.pending_assignees.length
                    ? approvalFollowup.pending_assignees.join(', ')
                    : 'Not recorded'}
                </div>
              </div>
            `}
            ${isInvoiceDocument && html`
              <div class="cl-summary-card">
                <div class="cl-summary-label">Entity route</div>
                <div class="cl-summary-value cl-summary-value-compact">
                  ${entityNeedsReview ? 'Needs review' : (item?.entity_code || item?.entity_name || 'Not set')}
                </div>
                ${item?.entity_route_reason && html`<div class="cl-summary-detail">${item.entity_route_reason}</div>`}
              </div>
              ${entityCandidates.length > 0 && html`
                <div class="cl-summary-card">
                  <div class="cl-summary-label">Entity candidates</div>
                  <div class="cl-summary-value cl-summary-value-compact">
                    ${entityCandidates.slice(0, 3).map((candidate) => candidate?.label || candidate?.entity_name || candidate?.entity_code).filter(Boolean).join(', ')}
                  </div>
                </div>
              `}
            `}
          </div>
        </div>
      `}

      ${!['closed', 'rejected', 'posted_to_erp'].includes(state) && html`
        <${FieldReviewPanel}
          blockers=${fieldReviewBlockers}
          pauseReason=${pauseReason}
          onResolve=${readOnlyMode ? null : doResolveFieldReview}
          resolvingField=${resolvePending ? resolvingFieldKey : ''}
        />
      `}
      ${Boolean(financeEffectNotice || Object.keys(financeEffectSummary).length > 0) && html`
        <div class="cl-section" aria-label="Credits and payments">
          <div class="cl-section-title">Credits and payments</div>
          ${financeEffectNotice && html`<div class="cl-review-copy">${financeEffectNotice}</div>`}
          <div class="cl-card-stack">
            ${Object.keys(financeEffectSummary).length > 0 && html`
              <div class="cl-summary-grid">
                <div class="cl-summary-card">
                  <div class="cl-summary-label">Original amount</div>
                  <div class="cl-summary-value">${formatAmount(financeEffectSummary.original_amount, financeEffectSummary.currency || item.currency)}</div>
                </div>
                <div class="cl-summary-card">
                  <div class="cl-summary-label">Credits applied</div>
                  <div class="cl-summary-value">${formatAmount(financeEffectSummary.applied_credit_total, financeEffectSummary.currency || item.currency)}</div>
                </div>
                <div class="cl-summary-card">
                  <div class="cl-summary-label">Net cash applied</div>
                  <div class="cl-summary-value">${formatAmount(financeEffectSummary.net_cash_applied_total, financeEffectSummary.currency || item.currency)}</div>
                </div>
                <div class="cl-summary-card">
                  <div class="cl-summary-label">Remaining balance</div>
                  <div class="cl-summary-value">${formatAmount(financeEffectSummary.remaining_balance_amount, financeEffectSummary.currency || item.currency)}</div>
                </div>
              </div>
            `}
            ${financeEffectBlockers.map((blocker) => html`
              <div key=${blocker.code} class="cl-blocker-row">
                <div class="cl-blocker-label">${blocker.label}</div>
                ${blocker.detail && html`<div class="cl-blocker-detail">${blocker.detail}</div>`}
              </div>
            `)}
          </div>
        </div>
      `}
      <${RelatedRecordsSection}
        item=${item}
        contextPayload=${contextPayload}
        onOpenRecord=${openRelatedRecord}
        onOpenVendor=${openVendorRecord}
      />
      <${TaskSection}
        item=${item}
        tasks=${tasks}
        queueManager=${queueManager}
        readOnlyMode=${readOnlyMode}
      />
      <${CommentsSection}
        item=${item}
        comments=${comments}
        queueManager=${queueManager}
        readOnlyMode=${readOnlyMode}
      />
      <${NotesSection}
        item=${item}
        notes=${notes}
        queueManager=${queueManager}
        readOnlyMode=${readOnlyMode}
      />
      <${FilesSection}
        item=${item}
        contextPayload=${contextPayload}
        files=${files}
        queueManager=${queueManager}
        readOnlyMode=${readOnlyMode}
      />
      <${RecordEditorSection}
        item=${item}
        queueManager=${queueManager}
        readOnlyMode=${readOnlyMode}
      />
      <${EvidenceChecklist} entries=${evidence} />
      <${AuditDisclosure} events=${auditEvents} loading=${Boolean(s.auditState.loading && s.auditState.itemId === item.id)} />
      <${ActionDialog} ...${dialog} />
    </div>
  `;
}

function EmptyState({ queueCount, queueManager }) {
  const actorRole = store.currentUserRole || queueManager?.currentUserRole || 'operator';
  const canMutate = hasOpsAccessRole(actorRole);
  const [search, setSearch] = useState('');
  const [searching, setSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [results, setResults] = useState([]);
  const [recovering, setRecovering] = useState(false);
  const [linkingId, setLinkingId] = useState('');
  const openPipeline = useCallback(() => {
    if (!navigateInboxRoute('clearledgr/invoices', store.sdk)) {
      showToast('Unable to open invoices', 'error');
    }
  }, []);
  const openHome = useCallback(() => {
    if (!navigateInboxRoute('clearledgr/home', store.sdk)) {
      showToast('Unable to open Home', 'error');
    }
  }, []);
  const threadSelected = Boolean(store.currentThreadId);
  const threadId = String(store.currentThreadId || '').trim();

  const searchCandidates = useCallback(async () => {
    const query = String(search || '').trim();
    if (!query || !queueManager?.searchRecordCandidates) {
      setHasSearched(false);
      setResults([]);
      return;
    }
    setHasSearched(true);
    setSearching(true);
    try {
      const items = await queueManager.searchRecordCandidates(query, { limit: 8 });
      setResults(Array.isArray(items) ? items : []);
    } finally {
      setSearching(false);
    }
  }, [queueManager, search]);

  const createFromThread = useCallback(async () => {
    if (!threadId || !queueManager?.recoverCurrentThread) return;
    setRecovering(true);
    try {
      const result = await queueManager.recoverCurrentThread(threadId);
      if (result?.item?.id) {
        store.setSelectedItem(result.item.id);
        showToast('Finance record created from this email.', 'success');
        return;
      }
      showToast('Clearledgr could not create a finance record from this email yet.', 'warning');
    } finally {
      setRecovering(false);
    }
  }, [queueManager, threadId]);

  const linkThreadToItem = useCallback(async (candidate) => {
    if (!threadId || !candidate?.id || !queueManager?.linkCurrentThreadToItem) return;
    setLinkingId(String(candidate.id));
    try {
      const result = await queueManager.linkCurrentThreadToItem(candidate, { thread_id: threadId });
      const linkedItem = result?.ap_item || candidate;
      if (linkedItem?.id) {
        store.setSelectedItem(linkedItem.id);
        showToast(`Linked this thread to ${linkedItem.vendor_name || linkedItem.invoice_number || 'the selected record'}.`, 'success');
        return;
      }
      showToast(result?.reason || 'Could not link this thread to the selected record.', 'error');
    } finally {
      setLinkingId('');
    }
  }, [queueManager, threadId]);

  if (threadSelected) {
    if (!canMutate) {
      return html`<div class="cl-section"><div class="cl-empty">
        <p>No finance record is linked to this email yet.</p>
        <p class="cl-muted">Open the queue to review records Clearledgr already found. Only operators can create or link records from Gmail.</p>
        <div class="cl-thread-actions cl-empty-actions">
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openPipeline}>Open invoices</button>
        </div>
      </div></div>`;
    }

    return html`
      <div class="cl-section">
        <div class="cl-empty cl-empty-stretch">
        <p>No finance record is linked to this email yet.</p>
        <p class="cl-muted">Create one from this email or link this email to an existing record.</p>
        <div class="cl-thread-actions cl-empty-actions">
            <button class="cl-btn cl-primary-cta cl-empty-primary" onClick=${createFromThread} disabled=${recovering}>
              ${recovering ? 'Creating…' : 'Create record from email'}
            </button>
            <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openPipeline}>Open invoices</button>
          </div>
          <div class="cl-inline-form cl-inline-form-comment cl-empty-search">
            <input
              class="cl-input"
              value=${search}
              placeholder="Search existing records by vendor, invoice, or email"
              onInput=${(event) => {
                setSearch(event.target.value);
                setHasSearched(false);
              }}
              onKeyDown=${(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  searchCandidates();
                }
              }}
            />
            <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${searchCandidates} disabled=${searching}>
              ${searching ? 'Searching…' : 'Find record'}
            </button>
          </div>
          ${searching && html`<div class="cl-muted">Looking for matching finance records…</div>`}
          ${!searching && hasSearched && search.trim() && results.length === 0 && html`
            <div class="cl-muted">No matching finance records found yet.</div>
          `}
          ${results.length > 0 && html`
            <div class="cl-card-stack cl-empty-results">
              ${results.map((candidate) => html`
                <div key=${candidate.id} class="cl-mini-card">
                  <div class="cl-mini-card-main">
                    <div class="cl-mini-card-copy">
                      <div class="cl-mini-card-title">${candidate.vendor_name || 'Unknown vendor'}</div>
                      <div class="cl-mini-card-meta">
                        ${candidate.invoice_number || 'No invoice #'} · ${formatAmount(candidate.amount, candidate.currency)}
                      </div>
                      <div class="cl-mini-card-meta">
                        ${String(candidate.state || 'received').replace(/_/g, ' ')}
                      </div>
                    </div>
                    <button
                      class="cl-btn cl-btn-secondary cl-btn-small"
                      onClick=${() => linkThreadToItem(candidate)}
                      disabled=${linkingId === String(candidate.id)}
                    >
                      ${linkingId === String(candidate.id) ? 'Linking…' : 'Link email'}
                    </button>
                  </div>
                </div>
              `)}
            </div>
          `}
        </div>
      </div>
    `;
  }

  if (queueCount > 0) {
    return html`<div class="cl-section"><div class="cl-empty">
      <p>${queueCount} record${queueCount !== 1 ? 's are' : ' is'} ready in the queue.</p>
      <p class="cl-muted">Open an email to work one record, or open Invoices to see the full queue.</p>
      <div class="cl-thread-actions cl-empty-actions">
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openPipeline}>Open invoices</button>
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openHome}>Open Home</button>
      </div>
    </div></div>`;
  }

  return html`<div class="cl-section"><div class="cl-empty">
    <p>Nothing is waiting right now.</p>
    <p class="cl-muted">Invoices is your control plane. Home is available if you want the lighter overview.</p>
    <div class="cl-thread-actions cl-empty-actions">
      <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openPipeline}>Open invoices</button>
      <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openHome}>Open Home</button>
    </div>
  </div></div>`;
}

export default function SidebarApp({ queueManager }) {
  const s = useStore();
  const item = s.getPrimaryItem();
  const logoUrl = getAssetUrl(LOGO_PATH);
  const queueCount = s.queueState.length;
  const currentIndex = s.getPrimaryItemIndex();
  const authRequired = s.scanStatus?.state === 'auth_required';
  const hasQueueNavigation = Boolean(item && queueCount > 1 && currentIndex >= 0);
  const pipelineScope = {
    orgId: queueManager?.runtimeConfig?.organizationId || 'default',
    userEmail: queueManager?.runtimeConfig?.userEmail || '',
  };
  const openPipeline = useCallback(() => navigateInboxRoute('clearledgr/invoices', store.sdk, pipelineScope), [pipelineScope.orgId, pipelineScope.userEmail]);

  useEffect(() => {
    if (item?.id && queueManager?.fetchItemContext) {
      queueManager.fetchItemContext(item.id).catch(() => {});
    }
  }, [item?.id, queueManager]);

  useEffect(() => {
    if (item?.id && queueManager?.fetchItemTasks) {
      queueManager.fetchItemTasks(item.id).catch(() => {});
    }
  }, [item?.id, queueManager]);

  useEffect(() => {
    if (item?.id && queueManager?.fetchItemNotes) {
      queueManager.fetchItemNotes(item.id).catch(() => {});
    }
  }, [item?.id, queueManager]);

  useEffect(() => {
    if (item?.id && queueManager?.fetchItemComments) {
      queueManager.fetchItemComments(item.id).catch(() => {});
    }
  }, [item?.id, queueManager]);

  useEffect(() => {
    if (item?.id && queueManager?.fetchItemFiles) {
      queueManager.fetchItemFiles(item.id).catch(() => {});
    }
  }, [item?.id, queueManager]);

  useEffect(() => {
    if (!item?.id || !queueManager?.fetchAuditTrail) return;
    store.update({ auditState: { itemId: item.id, loading: true, events: [] } });
    queueManager.fetchAuditTrail(item.id).then((events) => {
      if (store.getPrimaryItem()?.id === item.id) {
        store.update({
          auditState: {
            itemId: item.id,
            loading: false,
            events: Array.isArray(events) ? events : [],
          },
        });
      }
    }).catch(() => {
      store.update({ auditState: { itemId: item.id, loading: false, events: [] } });
    });
  }, [item?.id, queueManager]);

  return html`
    <div class="cl-sidebar">
      <style>${SIDEBAR_CSS}${STATE_PILL_CSS}</style>

      <div class="cl-header">
        <div class="cl-title">
          ${logoUrl && html`<img class="cl-logo" src=${logoUrl} alt="Clearledgr" onError=${(e) => e.target.remove()} />`}
          Clearledgr AP
        </div>
        <div class="cl-header-right">
          ${hasQueueNavigation
            ? html`
                <div class="cl-header-queue" aria-label="Queue navigation">
                  <button
                    class="cl-header-nav-btn"
                    aria-label="Previous record"
                    onClick=${() => store.selectItemByOffset(-1)}
                    disabled=${currentIndex <= 0}
                  >‹</button>
                  <button
                    class="cl-header-count"
                    onClick=${openPipeline}
                    title="Open invoices"
                  >${currentIndex + 1} of ${queueCount}</button>
                  <button
                    class="cl-header-nav-btn"
                    aria-label="Next record"
                    onClick=${() => store.selectItemByOffset(1)}
                    disabled=${currentIndex >= queueCount - 1}
                  >›</button>
                </div>
              `
            : queueCount > 0 && html`
                <button class="cl-header-count" onClick=${openPipeline} title="Open invoices">
                  ${queueCount} record${queueCount !== 1 ? 's' : ''}
                </button>
              `}
        </div>
      </div>

      <${Toast} />

      <${ErrorBoundary} fallback="Scan status unavailable">
        <${ScanStatus} />
      <//>

      ${authRequired && html`
        <${ErrorBoundary} fallback="Authorization prompt unavailable">
          <${AuthPrompt} queueManager=${queueManager} />
        <//>
      `}

      <${ErrorBoundary} fallback="Could not load record details">
        ${item
          ? html`<${WorkPanel} item=${item} queueManager=${queueManager} />`
          : html`<${EmptyState} queueCount=${queueCount} queueManager=${queueManager} />`}
      <//>
    </div>
  `;
}
