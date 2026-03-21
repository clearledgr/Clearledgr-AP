/** Root sidebar Preact component — compact AP-first Gmail work surface */
import { h, Component } from 'preact';
import { useState, useEffect, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import store from '../utils/store.js';
import { SIDEBAR_CSS, STATE_PILL_CSS } from '../styles.js';
import ActionDialog, { useActionDialog } from './ActionDialog.js';
import { hasOpsAccessRole } from '../utils/roles.js';
import {
  getStateLabel,
  formatAmount,
  getAssetUrl,
  getFieldReviewBlockers,
  normalizeBudgetContext,
  getIssueSummary,
  getExceptionReason,
  getSourceThreadId,
  getSourceMessageId,
  getWorkflowPauseReason,
  openSourceEmail,
  partitionAuditEvents,
} from '../utils/formatters.js';
import {
  normalizeWorkState,
  getPrimaryActionConfig,
  getWorkStateNotice,
  shouldOfferResumeWorkflow,
  canRejectWorkItem,
  canNudgeApprover,
} from '../utils/work-actions.js';
import {
  getDocumentTypeLabel,
  getNonInvoiceWorkflowGuidance,
  isInvoiceDocumentType,
  normalizeDocumentType,
} from '../utils/document-types.js';
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
  _toastTimer = setTimeout(() => {
    if (_toastEl) _toastEl.style.display = 'none';
  }, 3000);
}

function Toast() {
  const ref = useRef(null);
  useEffect(() => {
    _toastEl = ref.current;
    return () => {
      _toastEl = null;
    };
  }, []);
  return html`<div ref=${ref} class="cl-toast" style="display:none"></div>`;
}

function ScanStatus() {
  const s = useStore();
  const status = s.scanStatus;
  const gmail = s.gmailIntegration || {};
  const state = status?.state || 'idle';

  let text = '';
  let tone = '';

  if (state === 'initializing') text = 'Preparing invoice monitoring.';
  else if (state === 'scanning') text = 'Scanning inbox for invoices.';
  else if (state === 'auth_required') {
    text = 'Connect Gmail to continue monitoring.';
    tone = 'warning';
  } else if (state === 'blocked') {
    text = 'Finish setup before monitoring can continue.';
    tone = 'warning';
  } else if (state === 'error') {
    const err = String(status?.error || '');
    if (err.includes('backend')) text = 'Backend unreachable.';
    else if (err.includes('temporal')) text = 'Processing is temporarily unavailable.';
    else if (err.includes('processing')) {
      const failedCount = Number(status?.failedCount || 0);
      text = failedCount > 0 ? `${failedCount} email(s) need another processing attempt.` : 'Processing issue. Retrying.';
    } else text = 'Inbox sync issue. Retrying.';
    tone = 'error';
  } else {
    const lastScan = status?.lastScanAt ? new Date(status.lastScanAt) : null;
      text = lastScan
      ? `Monitoring active · ${lastScan.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
      : 'Monitoring active';
  }

  if (state !== 'auth_required' && gmail?.requires_reconnect) {
    text = 'Reconnect Gmail to keep background monitoring durable.';
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
      fieldReviewBlockers.length ? 'Workflow paused for field review' : 'Review extracted fields',
      fieldReviewBlockers.length
        ? null
        : (pauseReason || `Current confidence is ${Math.round(confidence * 100)}%, so a quick field check is still required.`),
    );
  }

  if (state === 'needs_approval') {
    add('approval', 'Waiting on approver', 'The approval request is still outstanding.');
  }

  if (state === 'needs_info') {
    add(
      'needs_info',
      isInvoiceDocument ? 'Missing invoice details' : 'Missing document details',
      `Clearledgr still needs more information before this ${isInvoiceDocument ? 'invoice' : 'record'} can continue.`,
    );
  }

  if (state === 'failed_post') {
    add('failed_post', 'ERP posting failed', 'Retry the ERP post or review the connector response.');
  }

  if (blockers.length === 0 && state === 'received') {
    add(
      'received',
      isInvoiceDocument ? 'Ready for review' : 'Needs finance review',
      isInvoiceDocument
        ? 'This invoice is ready for AP validation and approval routing.'
        : getNonInvoiceWorkflowGuidance(documentType),
    );
  }

  if (blockers.length === 0 && state === 'validated') {
    add(
      'validated',
      isInvoiceDocument ? 'Ready for approval' : `Ready to review ${documentLabel}`,
      isInvoiceDocument
        ? 'Checks are complete and the invoice can be routed to approval.'
        : getNonInvoiceWorkflowGuidance(documentType),
    );
  }

  return blockers.slice(0, 4);
}

function getEvidenceChecklist(item, state, contextPayload) {
  const approvals = contextPayload?.approvals || {};
  const erp = contextPayload?.erp || {};
  const hasEmail = Boolean(getSourceThreadId(item) || getSourceMessageId(item) || item?.subject);
  const hasAttachment = Boolean(item?.has_attachment || Number(item?.attachment_count || 0) > 0);
  const hasApproval = Boolean(
    Number(approvals.count || 0) > 0
    || ['needs_approval', 'approved', 'ready_to_post', 'posted_to_erp', 'closed'].includes(state)
  );
  const hasErpLink = Boolean(item?.erp_reference || item?.erp_bill_id || erp.erp_reference || erp.connector_available || erp.state);

  return [
    {
      key: 'email',
      label: 'Email',
      status: hasEmail ? 'ok' : 'missing',
      text: hasEmail ? 'Linked' : 'Not linked',
    },
    {
      key: 'attachment',
      label: 'Attachment',
      status: hasAttachment ? 'ok' : 'missing',
      text: hasAttachment ? 'Attached' : 'No file',
    },
    {
      key: 'approval',
      label: 'Approval',
      status: hasApproval ? 'ok' : 'missing',
      text: hasApproval ? (state === 'needs_approval' ? 'Routed' : 'Available') : 'Not routed',
    },
    {
      key: 'erp',
      label: 'ERP',
      status: hasErpLink ? 'ok' : 'missing',
      text: hasErpLink
        ? (item?.erp_reference || erp.erp_reference ? 'Linked' : 'Connected')
        : 'Not connected',
    },
  ];
}

function EvidenceChecklist({ entries }) {
  return html`
    <div class="cl-evidence-section" aria-label="Evidence checklist">
      <div class="cl-section-title">Evidence checklist</div>
      <div class="cl-evidence-list">
        ${entries.map((entry) => html`
          <div key=${entry.key} class="cl-evidence-row">
            <span class="cl-evidence-label">${entry.label}</span>
            <span class="cl-evidence-status" data-status=${entry.status}>${entry.text}</span>
          </div>
        `)}
      </div>
    </div>
  `;
}

function FieldReviewPanel({ blockers, pauseReason, onResolve = null, resolvingField = '' }) {
  if ((!Array.isArray(blockers) || blockers.length === 0) && !pauseReason) return null;
  return html`
    <div class="cl-review-panel" aria-label="Paused field review">
      <div class="cl-section-title">Paused field review</div>
      ${pauseReason && html`<div class="cl-review-copy">${pauseReason}</div>`}
      ${(blockers || []).map((blocker) => html`
        <div key=${`${blocker.field || 'field'}-${blocker.kind || 'review'}`} class="cl-review-card">
          <div class="cl-review-card-title">${blocker.field_label || 'Field'} blocked</div>
          ${blocker.email_value_display && html`
            <div class="cl-review-row">
              <span class="cl-review-label">Email said</span>
              <span class="cl-review-value">${blocker.email_value_display}</span>
            </div>
          `}
          ${blocker.attachment_value_display && html`
            <div class="cl-review-row">
              <span class="cl-review-label">Attachment said</span>
              <span class="cl-review-value">${blocker.attachment_value_display}</span>
            </div>
          `}
          <div class="cl-review-row">
            <span class="cl-review-label">Source selected</span>
            <span class="cl-review-value">
              ${blocker.winning_source_label || 'Review required'}
              ${blocker.winning_value_display ? ` (${blocker.winning_value_display})` : ''}
            </span>
          </div>
          <div class="cl-review-why">${blocker.winner_reason || blocker.reason_label || blocker.paused_reason}</div>
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
          <span>${row.evidenceDetail || 'Recorded on the shared AP record.'}</span>
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
    <details class="cl-details">
      <summary>View audit${totalEvents ? ` (${totalEvents})` : ''}</summary>
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
  const goConnections = useCallback(() => store.sdk?.Router?.goto?.('clearledgr/connections'), []);
  const [authorize, pending] = useAction(async () => {
    const result = await queueManager?.authorizeGmailNow?.();
    const ok = Boolean(result?.success || result?.authorized || result?.status === 'ok');
    showToast(ok ? 'Gmail connected' : 'Authorization failed', ok ? 'success' : 'error');
    if (ok && queueManager?.refreshQueue) {
      await queueManager.refreshQueue();
    }
  });

  return html`
    <div class="cl-section cl-auth-panel">
      <div class="cl-section-title">Action required</div>
      <div class="cl-auth-copy">
        ${gmail?.requires_reconnect
          ? 'Reconnect Gmail so Clearledgr can keep monitoring this mailbox after the current session expires.'
          : 'Connect Gmail once so Clearledgr can keep monitoring invoices in this mailbox.'}
      </div>
      <div class="cl-thread-actions">
        <button class="cl-btn cl-primary-cta" onClick=${authorize} disabled=${pending}>
          ${pending ? 'Connecting…' : (gmail?.requires_reconnect ? 'Reconnect Gmail' : 'Connect Gmail')}
        </button>
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${goConnections}>Connections</button>
      </div>
    </div>
  `;
}

function WorkPanel({ item, queueManager, itemIndex, totalItems }) {
  const s = useStore();
  const actorRole = s.currentUserRole || queueManager?.currentUserRole || 'operator';
  const humanIndex = itemIndex >= 0 ? itemIndex + 1 : 1;
  const state = normalizeWorkState(item?.state || 'received');
  const documentType = normalizeDocumentType(item?.document_type);
  const documentLabel = getDocumentTypeLabel(documentType);
  const isInvoiceDocument = isInvoiceDocumentType(documentType);
  const vendor = item.vendor_name || item.vendor || item.sender || 'Unknown vendor';
  const amountLabel = formatAmount(item.amount, item.currency || 'USD');
  const invoiceNumber = item.invoice_number || 'N/A';
  const dueDate = item.due_date || 'N/A';
  const referenceText = invoiceNumber !== 'N/A' ? `${documentLabel} #: ${invoiceNumber}` : documentLabel;
  const metaLine = [
    amountLabel,
    referenceText,
    ...(isInvoiceDocument ? [`Due: ${dueDate}`, item.po_number ? `PO: ${item.po_number}` : 'No PO'] : []),
  ].join(' · ');
  const contextPayload = item?.id ? s.contextState.get(item.id) || null : null;
  const budgetContext = normalizeBudgetContext(contextPayload || {}, item);
  const blockers = getBlockers(item, state, budgetContext, documentType);
  const fieldReviewBlockers = getFieldReviewBlockers(item);
  const evidence = getEvidenceChecklist(item, state, contextPayload);
  const auditEvents = s.auditState.itemId === item.id && Array.isArray(s.auditState.events) ? s.auditState.events : [];
  const pauseReason = getWorkflowPauseReason(item);
  const resumeWorkflowEligible = !pauseReason && shouldOfferResumeWorkflow(item, auditEvents, documentType);
  const stateNotice = resumeWorkflowEligible
    ? 'Field review is cleared. Resume workflow to continue the posting step.'
    : getWorkStateNotice(state, documentType);
  const smartDefault = item?.exception_code ? getExceptionReason(item.exception_code) : '';
  const canOpenSource = Boolean(getSourceThreadId(item) || getSourceMessageId(item) || item.subject);

  const [optimisticState, setOptimisticState] = useState(null);
  const displayState = normalizeWorkState(optimisticState || state);
  const readOnlyMode = !hasOpsAccessRole(actorRole);
  const [dialog, openDialog] = useActionDialog();
  const [resolvingFieldKey, setResolvingFieldKey] = useState('');
  const pipelineScope = {
    orgId: queueManager?.runtimeConfig?.organizationId || 'default',
    userEmail: queueManager?.runtimeConfig?.userEmail || '',
  };

  const [doApproval, approvalPending] = useAction(async () => {
    setOptimisticState('needs_approval');
    const result = await queueManager.requestApproval(item);
    const ok = ['needs_approval', 'pending_approval'].includes(String(result?.status || '').toLowerCase());
    showToast(ok ? 'Approval request sent' : 'Unable to route approval', ok ? 'success' : 'error');
    if (!ok) setOptimisticState(null);
    await queueManager.refreshQueue();
    setOptimisticState(null);
  });

  const [doNudge, nudgePending] = useAction(async () => {
    const result = await queueManager.nudgeApproval(item);
    const ok = String(result?.status || '').toLowerCase() === 'nudged';
    showToast(ok ? 'Approval reminder sent' : 'Unable to send reminder', ok ? 'success' : 'error');
    if (ok) await queueManager.refreshQueue();
  });

  const [doPrepareInfo, prepareInfoPending] = useAction(async () => {
    const result = await queueManager.prepareVendorFollowup(item, {
      reason: 'Request missing invoice details from vendor',
    });
    const ok = ['prepared', 'queued'].includes(String(result?.status || '').toLowerCase());
    showToast(ok ? 'Info request draft prepared' : 'Unable to prepare info request', ok ? 'success' : 'error');
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

  const goPrev = useCallback(() => store.selectItemByOffset(-1), []);
  const goNext = useCallback(() => store.selectItemByOffset(1), []);
  const openPipeline = useCallback(() => {
    if (!item?.id) return;
    store.setSelectedItem(String(item.id));
    focusPipelineItem(pipelineScope, item, 'thread');
    store.sdk?.Router?.goto?.('clearledgr/pipeline');
  }, [item, pipelineScope]);
  const openSource = useCallback(() => {
    if (!openSourceEmail(item)) showToast('Unable to open source email', 'error');
  }, [item]);
  const openVendorRecord = useCallback(() => {
    const vendorName = String(item?.vendor_name || item?.vendor || '').trim();
    if (!vendorName) return;
    navigateToVendorRecord((routeId) => store.sdk?.Router?.goto?.(routeId), vendorName);
  }, [item]);

  const basePrimaryAction = pauseReason ? null : getPrimaryActionConfig(displayState, actorRole, documentType);
  const primaryAction = resumeWorkflowEligible && ['preview_erp_post', 'retry_erp_post'].includes(basePrimaryAction?.id)
    ? { id: 'resume_workflow', label: 'Resume workflow' }
    : basePrimaryAction;
  let primaryHandler = null;
  let primaryPending = false;
  let primaryClass = '';
  if (primaryAction?.id === 'request_approval') {
    primaryHandler = doApproval;
    primaryPending = approvalPending;
  } else if (primaryAction?.id === 'prepare_info_request') {
    primaryHandler = doPrepareInfo;
    primaryPending = prepareInfoPending;
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

  return html`
    <div id="cl-thread-context" class="cl-thread-card cl-work-surface">
      ${totalItems > 1 && html`
        <div class="cl-navigator">
          <div class="cl-nav-label">Record ${humanIndex} of ${totalItems}</div>
          <div class="cl-nav-buttons">
            <button class="cl-nav-btn" onClick=${goPrev} disabled=${itemIndex <= 0} aria-label="Previous">‹</button>
            <button class="cl-nav-btn" onClick=${goNext} disabled=${itemIndex >= totalItems - 1} aria-label="Next">›</button>
          </div>
        </div>
      `}

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

      ${pauseReason && fieldReviewBlockers.length === 0 && html`<div class="cl-state-note">${pauseReason}</div>`}
      ${!pauseReason && stateNotice && html`<div class="cl-state-note">${stateNotice}</div>`}
      ${readOnlyMode && html`
        <div class="cl-state-note">Read-only view. Queue actions are reserved for AP operators.</div>
      `}

      ${primaryAction?.label && primaryHandler && html`
        <button class="cl-btn cl-primary-cta ${primaryClass}" onClick=${primaryHandler} disabled=${primaryPending}>
          ${primaryPending ? 'Processing…' : primaryAction.label}
        </button>
      `}

      <div id="cl-agent-actions" class="cl-thread-actions">
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openPipeline}>Open in pipeline</button>
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openSource} disabled=${!canOpenSource}>Open email</button>
        ${(item?.vendor_name || item?.vendor) && html`
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openVendorRecord}>Vendor record</button>
        `}
        ${canRejectWorkItem(displayState, actorRole, documentType) && html`
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doReject} disabled=${rejectPending}>Reject</button>
        `}
        ${canNudgeApprover(displayState, actorRole, documentType) && primaryAction?.id !== 'nudge_approver' && html`
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doNudge} disabled=${nudgePending}>Nudge approver</button>
        `}
      </div>

      <${FieldReviewPanel}
        blockers=${fieldReviewBlockers}
        pauseReason=${pauseReason}
        onResolve=${readOnlyMode ? null : doResolveFieldReview}
        resolvingField=${resolvePending ? resolvingFieldKey : ''}
      />
      <${EvidenceChecklist} entries=${evidence} />
      <${AuditDisclosure} events=${auditEvents} loading=${Boolean(s.auditState.loading && s.auditState.itemId === item.id)} />
      <${ActionDialog} ...${dialog} />
    </div>
  `;
}

function EmptyState({ queueCount }) {
  const openPipeline = useCallback(() => store.sdk?.Router?.goto?.('clearledgr/pipeline'), []);
  const openHome = useCallback(() => store.sdk?.Router?.goto?.('clearledgr/home'), []);
  const threadSelected = Boolean(store.currentThreadId);

  if (threadSelected) {
    return html`<div class="cl-section"><div class="cl-empty">
      <p>No finance document is linked to this thread.</p>
      <p class="cl-muted">Open the pipeline to work records that Clearledgr has already detected.</p>
      <div class="cl-thread-actions">
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openPipeline}>Open pipeline</button>
      </div>
    </div></div>`;
  }

  if (queueCount > 0) {
    return html`<div class="cl-section"><div class="cl-empty">
      <p>${queueCount} record${queueCount !== 1 ? 's are' : ' is'} ready in the pipeline.</p>
      <p class="cl-muted">Open a thread to work a specific finance document, or review the queue in Pipeline.</p>
      <div class="cl-thread-actions">
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openPipeline}>Open pipeline</button>
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openHome}>Home</button>
      </div>
    </div></div>`;
  }

  return html`<div class="cl-section"><div class="cl-empty">
    <p>No finance documents in queue.</p>
    <p class="cl-muted">Clearledgr is monitoring this mailbox and will surface finance work here as records arrive.</p>
    <div class="cl-thread-actions">
      <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openHome}>Home</button>
    </div>
  </div></div>`;
}

export default function SidebarApp({ queueManager }) {
  const s = useStore();
  const item = s.getPrimaryItem();
  const itemIndex = s.getPrimaryItemIndex();
  const logoUrl = getAssetUrl(LOGO_PATH);
  const queueCount = s.queueState.length;
  const authRequired = s.scanStatus?.state === 'auth_required';

  useEffect(() => {
    if (item?.id && queueManager?.fetchItemContext) {
      queueManager.fetchItemContext(item.id).catch(() => {});
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
          ${queueCount > 0 && html`<span class="cl-header-badge">${queueCount} record${queueCount !== 1 ? 's' : ''}</span>`}
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
          ? html`<${WorkPanel} item=${item} queueManager=${queueManager} itemIndex=${itemIndex} totalItems=${queueCount} />`
          : html`<${EmptyState} queueCount=${queueCount} />`}
      <//>
    </div>
  `;
}
