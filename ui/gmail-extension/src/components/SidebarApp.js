/** Root sidebar Preact component — compact AP-first Gmail work surface */
import { h, Component } from 'preact';
import { useState, useEffect, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import store from '../utils/store.js';
import { SIDEBAR_CSS, STATE_PILL_CSS } from '../styles.js';
import ActionDialog, { useActionDialog } from './ActionDialog.js';
import {
  getStateLabel,
  formatAmount,
  trimText,
  getAssetUrl,
  normalizeBudgetContext,
  getIssueSummary,
  getExceptionReason,
  getSourceThreadId,
  getSourceMessageId,
  openSourceEmail,
  formatDateTime,
  getAuditEventPayload,
  normalizeAuditEventType,
} from '../utils/formatters.js';
import {
  normalizeWorkState,
  getPrimaryActionConfig,
  getWorkStateNotice,
  canRejectWorkItem,
  canNudgeApprover,
} from '../utils/work-actions.js';

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

  return html`<div class="cl-scan-status" data-tone=${tone}>${text}</div>`;
}

function StatePill({ state }) {
  const cls = `cl-pill cl-pill-${String(state || 'received').replace(/_/g, '-')}`;
  return html`<span class=${cls}>${getStateLabel(state)}</span>`;
}

function getBlockers(item, state, budgetContext) {
  const blockers = [];
  const add = (id, label, detail) => {
    if (!label) return;
    if (blockers.some((entry) => entry.id === id || entry.label === label)) return;
    blockers.push({ id, label, detail });
  };

  if (budgetContext?.requiresDecision) {
    add(
      'budget',
      'Budget review required',
      'A budget decision is still needed before this invoice can move forward.',
    );
  }

  const exceptionCode = String(item?.exception_code || '').trim().toLowerCase();
  const exceptionReason = getExceptionReason(exceptionCode);
  if (exceptionReason) {
    add('exception', exceptionReason, getIssueSummary(item));
  }

  if (!item?.po_number && exceptionCode.includes('po')) {
    add('po', 'PO reference missing', 'Link the correct PO before continuing this invoice.');
  }

  const confidence = Number(item?.confidence);
  if (Number.isFinite(confidence) && confidence < 0.95 && !['posted_to_erp', 'closed', 'rejected'].includes(state)) {
    add(
      'confidence',
      'Review extracted fields',
      `Current confidence is ${Math.round(confidence * 100)}%, so a quick field check is still required.`,
    );
  }

  if (state === 'needs_approval') {
    add('approval', 'Waiting on approver', 'The approval request is still outstanding.');
  }

  if (state === 'needs_info') {
    add('needs_info', 'Missing invoice details', 'Clearledgr still needs more information before routing or posting.');
  }

  if (state === 'failed_post') {
    add('failed_post', 'ERP posting failed', 'Retry the ERP post or review the connector response.');
  }

  if (blockers.length === 0 && state === 'received') {
    add('received', 'Ready for review', 'This invoice is ready for AP validation and approval routing.');
  }

  if (blockers.length === 0 && state === 'validated') {
    add('validated', 'Ready for approval', 'Checks are complete and the invoice can be routed to approval.');
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

function getAuditRow(event) {
  const payload = getAuditEventPayload(event);
  const eventType = normalizeAuditEventType(
    event?.event_type || event?.eventType || payload?.event_type || event?.action || 'action_recorded',
  );
  const safeTitle = eventType === 'state_transition' ? 'Status updated' : 'Action recorded';
  let safeDetail = 'Action recorded for this invoice.';
  if (eventType === 'state_transition') safeDetail = 'Invoice status changed.';
  else if (eventType === 'erp_post_completed') safeDetail = 'Invoice posting completed successfully.';
  else if (eventType === 'erp_post_failed') safeDetail = 'Clearledgr could not complete ERP posting.';
  const title = trimText(
    String(event?.operator_title || safeTitle),
    72,
  );
  return {
    title,
    detail: trimText(String(event?.operator_message || safeDetail).trim(), 160),
    timestamp: formatDateTime(event?.ts || event?.created_at || event?.createdAt || event?.updated_at || event?.updatedAt),
  };
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

function AuditDisclosure({ events, loading }) {
  const visibleEvents = Array.isArray(events) ? events.slice(0, 6) : [];
  return html`
    <details class="cl-details">
      <summary>View audit${visibleEvents.length ? ` (${visibleEvents.length})` : ''}</summary>
      <div class="cl-audit-list">
        ${loading && html`<div class="cl-empty">Loading audit…</div>`}
        ${!loading && visibleEvents.length === 0 && html`<div class="cl-empty">No audit events yet.</div>`}
        ${!loading && visibleEvents.map((event, index) => {
          const row = getAuditRow(event);
          return html`
            <div key=${event?.id || index} class="cl-audit-row">
              <div class="cl-audit-main">
                <div class="cl-audit-type">${row.title}</div>
                ${row.timestamp && html`<div class="cl-audit-time">${row.timestamp}</div>`}
              </div>
              <div class="cl-audit-detail">${row.detail}</div>
            </div>
          `;
        })}
      </div>
    </details>
  `;
}

function AuthPrompt({ queueManager }) {
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
      <div class="cl-auth-copy">Connect Gmail once so Clearledgr can keep monitoring invoices in this mailbox.</div>
      <div class="cl-thread-actions">
        <button class="cl-btn cl-primary-cta" onClick=${authorize} disabled=${pending}>
          ${pending ? 'Connecting…' : 'Connect Gmail'}
        </button>
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${goConnections}>Connections</button>
      </div>
    </div>
  `;
}

function WorkPanel({ item, queueManager, itemIndex, totalItems }) {
  const s = useStore();
  const humanIndex = itemIndex >= 0 ? itemIndex + 1 : 1;
  const state = normalizeWorkState(item?.state || 'received');
  const vendor = item.vendor_name || item.vendor || item.sender || 'Unknown vendor';
  const amountLabel = formatAmount(item.amount, item.currency || 'USD');
  const invoiceNumber = item.invoice_number || 'N/A';
  const dueDate = item.due_date || 'N/A';
  const poLabel = item.po_number ? `PO ${item.po_number}` : 'No PO';
  const contextPayload = item?.id ? s.contextState.get(item.id) || null : null;
  const budgetContext = normalizeBudgetContext(contextPayload || {}, item);
  const blockers = getBlockers(item, state, budgetContext);
  const evidence = getEvidenceChecklist(item, state, contextPayload);
  const auditEvents = s.auditState.itemId === item.id && Array.isArray(s.auditState.events) ? s.auditState.events : [];
  const stateNotice = getWorkStateNotice(state);
  const smartDefault = item?.exception_code ? getExceptionReason(item.exception_code) : '';
  const canOpenSource = Boolean(getSourceThreadId(item) || getSourceMessageId(item) || item.subject);

  const [optimisticState, setOptimisticState] = useState(null);
  const displayState = normalizeWorkState(optimisticState || state);
  const [dialog, openDialog] = useActionDialog();

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
        `Invoice ${invoiceNumber}`,
        dueDate && dueDate !== 'N/A' ? `Due ${dueDate}` : null,
      ].filter(Boolean),
      confirmLabel: 'Post to ERP',
      cancelLabel: 'Cancel',
    });
    if (!confirmed) return;
    await doPost();
  });

  const goPrev = useCallback(() => store.selectItemByOffset(-1), []);
  const goNext = useCallback(() => store.selectItemByOffset(1), []);
  const openSource = useCallback(() => {
    if (!openSourceEmail(item)) showToast('Unable to open source email', 'error');
  }, [item]);

  const primaryAction = getPrimaryActionConfig(displayState);
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
  }

  return html`
    <div class="cl-thread-card cl-work-surface">
      ${totalItems > 1 && html`
        <div class="cl-navigator">
          <div class="cl-nav-label">Invoice ${humanIndex} of ${totalItems}</div>
          <div class="cl-nav-buttons">
            <button class="cl-nav-btn" onClick=${goPrev} disabled=${itemIndex <= 0} aria-label="Previous">‹</button>
            <button class="cl-nav-btn" onClick=${goNext} disabled=${itemIndex >= totalItems - 1} aria-label="Next">›</button>
          </div>
        </div>
      `}

      <div class="cl-thread-header">
        <div class="cl-thread-header-copy">
          <div class="cl-thread-title">${vendor}</div>
          <div class="cl-thread-meta-inline">${amountLabel} · Invoice ${invoiceNumber} · Due ${dueDate} · ${poLabel}</div>
        </div>
        <${StatePill} state=${displayState} />
      </div>

      ${blockers.length > 0 && html`
        <div class="cl-blocker-list" aria-label="What is blocking this invoice">
          ${blockers.map((blocker) => html`
            <div key=${blocker.id} class="cl-blocker-row">
              <div class="cl-blocker-label">${blocker.label}</div>
              ${blocker.detail && html`<div class="cl-blocker-detail">${blocker.detail}</div>`}
            </div>
          `)}
        </div>
      `}

      ${stateNotice && html`<div class="cl-state-note">${stateNotice}</div>`}

      ${primaryAction?.label && primaryHandler && html`
        <button class="cl-btn cl-primary-cta ${primaryClass}" onClick=${primaryHandler} disabled=${primaryPending}>
          ${primaryPending ? 'Processing…' : primaryAction.label}
        </button>
      `}

      <div class="cl-thread-actions">
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openSource} disabled=${!canOpenSource}>Open email</button>
        ${canRejectWorkItem(displayState) && html`
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doReject} disabled=${rejectPending}>Reject</button>
        `}
        ${canNudgeApprover(displayState) && primaryAction?.id !== 'nudge_approver' && html`
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doNudge} disabled=${nudgePending}>Nudge approver</button>
        `}
      </div>

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
      <p>No invoice is linked to this thread.</p>
      <p class="cl-muted">Open the AP pipeline to work invoices that Clearledgr has already detected.</p>
      <div class="cl-thread-actions">
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openPipeline}>Open pipeline</button>
      </div>
    </div></div>`;
  }

  if (queueCount > 0) {
    return html`<div class="cl-section"><div class="cl-empty">
      <p>${queueCount} invoice${queueCount !== 1 ? 's are' : ' is'} ready in the AP pipeline.</p>
      <p class="cl-muted">Open a thread to work a specific invoice, or review the queue in Pipeline.</p>
      <div class="cl-thread-actions">
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openPipeline}>Open pipeline</button>
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openHome}>Home</button>
      </div>
    </div></div>`;
  }

  return html`<div class="cl-section"><div class="cl-empty">
    <p>No invoices in queue.</p>
    <p class="cl-muted">Clearledgr is monitoring this mailbox and will surface AP work here as invoices arrive.</p>
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
          ${queueCount > 0 && html`<span class="cl-header-badge">${queueCount} invoice${queueCount !== 1 ? 's' : ''}</span>`}
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

      <${ErrorBoundary} fallback="Could not load invoice details">
        ${item
          ? html`<${WorkPanel} item=${item} queueManager=${queueManager} itemIndex=${itemIndex} totalItems=${queueCount} />`
          : html`<${EmptyState} queueCount=${queueCount} />`}
      <//>
    </div>
  `;
}
