/** Root sidebar Preact component — replaces renderSidebarFor() + renderAllSidebars() */
import { h, Component } from 'preact';
import { useState, useEffect, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import store from '../utils/store.js';
import { SIDEBAR_CSS, STATE_PILL_CSS } from '../styles.js';
import ThreadContext from './ThreadContext.js';
import AgentTimeline from './AgentTimeline.js';
import ActionDialog, { useActionDialog } from './ActionDialog.js';
import {
  getStateLabel, formatAmount, trimText, getAssetUrl,
  getDecisionSummary, normalizeBudgetContext,
  getSourceThreadId, getSourceMessageId, openSourceEmail,
} from '../utils/formatters.js';

const html = htm.bind(h);
const LOGO_PATH = 'icons/icon48.png';

// ==================== ERROR BOUNDARY ====================

class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(e, info) { console.error('[Clearledgr]', e, info?.componentStack || ''); }
  render() {
    if (this.state.error) {
      return html`<div class="cl-empty" role="alert">
        <p>${this.props.fallback || 'Something went wrong.'}</p>
        <button class="cl-btn cl-btn-secondary" onClick=${() => this.setState({ error: null })}>Retry</button>
      </div>`;
    }
    return this.props.children;
  }
}

// ==================== HOOKS ====================

function useStore() {
  const [, update] = useState(0);
  useEffect(() => store.subscribe(() => update(n => n + 1)), []);
  return store;
}

function useAction(fn) {
  const [pending, setPending] = useState(false);
  const ref = useRef(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const exec = useCallback(async (...args) => {
    if (ref.current) return ref.current;
    setPending(true);
    const p = fn(...args);
    ref.current = p;
    try { const r = await p; return r; }
    finally { ref.current = null; if (mounted.current) setPending(false); }
  }, [fn]);
  return [exec, pending];
}

// ==================== TOAST ====================

let _toastEl = null;
let _toastTimer = null;

export function showToast(message, tone = 'info') {
  if (!_toastEl) return;
  _toastEl.textContent = message;
  _toastEl.dataset.tone = tone;
  _toastEl.style.display = 'block';
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { if (_toastEl) _toastEl.style.display = 'none'; }, 3000);
}

function Toast() {
  const ref = useRef(null);
  useEffect(() => { _toastEl = ref.current; return () => { _toastEl = null; }; }, []);
  return html`<div ref=${ref} class="cl-toast" style="display:none"></div>`;
}

// ==================== SCAN STATUS ====================

function ScanStatus({ queueManager }) {
  const s = useStore();
  const status = s.scanStatus;
  const state = status?.state || 'idle';

  const [authorize, authPending] = useAction(async () => {
    const result = await queueManager.authorizeGmailNow();
    if (result?.success) {
      showToast('Gmail authorized. Autopilot is resuming.', 'success');
      await queueManager.refreshQueue();
    } else {
      const msg = queueManager.describeAuthResult?.(result) || { toast: 'Authorization failed', severity: 'error' };
      showToast(msg.toast, msg.severity || 'error');
    }
  });

  const openAdmin = useCallback(() => {
    const base = String(queueManager?.runtimeConfig?.backendUrl || '').replace(/\/+$/, '');
    const org = encodeURIComponent(String(queueManager?.runtimeConfig?.organizationId || 'default'));
    window.open(base ? `${base}/console?org=${org}&page=integrations` : `/console?org=${org}&page=integrations`, '_blank', 'noopener,noreferrer');
  }, [queueManager]);

  let text = '';
  let tone = '';
  let showAuth = false;
  let inlineAuth = false;

  if (state === 'initializing') text = 'Preparing inbox monitor.';
  else if (state === 'scanning') text = 'Scanning inbox for invoices.';
  else if (state === 'auth_required') {
    inlineAuth = String(queueManager?.runtimeConfig?.authEntryMode || '').toLowerCase() === 'inline';
    text = inlineAuth ? 'Connect Gmail to resume invoice monitoring.' : 'Gmail connection required. Connect Gmail in Admin Console.';
    showAuth = true;
  } else if (state === 'blocked') {
    text = (status?.error || '') === 'temporal_unavailable' ? 'Automation engine is unavailable.' : 'Setup required before invoice monitoring can run.';
    tone = 'error';
  } else if (state === 'error') {
    const err = String(status?.error || '');
    if (err.includes('backend')) text = 'Cannot sync: backend is unreachable.';
    else if (err.includes('temporal')) text = 'Cannot process invoices: automation engine unavailable.';
    else if (err.includes('processing')) { const fc = Number(status?.failedCount || 0); text = fc > 0 ? `${fc} email(s) failed to process. Retrying automatically.` : 'Some emails failed to process. Retrying automatically.'; }
    else text = 'Inbox sync issue. Retrying automatically.';
    tone = 'error';
  } else {
    const lastScan = status?.lastScanAt ? new Date(status.lastScanAt) : null;
    text = lastScan ? `Monitoring active. Last scan ${lastScan.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}.` : 'Monitoring active.';
  }

  return html`
    <div class="cl-section">
      <div class="cl-scan-status" data-tone=${tone}>${text}</div>
      ${showAuth && html`<div class="cl-inline-actions">
        ${inlineAuth && html`<button class="cl-btn cl-btn-secondary" onClick=${authorize} disabled=${authPending}>${authPending ? 'Connecting...' : 'Authorize Gmail'}</button>`}
        ${!inlineAuth && html`<button class="cl-btn cl-btn-secondary" onClick=${openAdmin}>Open Integrations</button>`}
      </div>`}
    </div>
  `;
}

// ==================== WORK PANEL (Thread Context) ====================

function StatePill({ state }) {
  const cls = `cl-pill cl-pill-${String(state || 'received').replace(/_/g, '-')}`;
  return html`<span class=${cls}>${getStateLabel(state)}</span>`;
}

function WorkPanel({ item, queueManager, itemIndex, totalItems }) {
  const s = useStore();
  const humanIndex = itemIndex >= 0 ? itemIndex + 1 : 1;
  const state = String(item?.state || 'received').toLowerCase();
  const vendor = item.vendor_name || item.vendor || item.sender || 'Unknown vendor';
  const amount = formatAmount(item.amount, item.currency || 'USD');
  const invoiceNumber = item.invoice_number || 'N/A';
  const dueDate = item.due_date || 'N/A';
  const poNumber = item.po_number || null;
  const contextPayload = item?.id ? s.contextState.get(item.id) || null : null;
  const budgetContext = normalizeBudgetContext(contextPayload || {}, item);
  const decision = getDecisionSummary(item, budgetContext);
  const canOpenSource = Boolean(getSourceThreadId(item) || getSourceMessageId(item) || item.subject);
  const confidenceNumber = Number(item.confidence);
  const confidencePercent = Number.isFinite(confidenceNumber) ? Math.round(Math.max(0, Math.min(1, confidenceNumber)) * 100) : null;
  const auditEvents = s.auditState.itemId === item.id && Array.isArray(s.auditState.events) ? s.auditState.events : [];

  // Actions
  const [doApproval, approvalPending] = useAction(async () => {
    const result = await queueManager.requestApproval(item);
    showToast(result?.status === 'needs_approval' || result?.status === 'pending_approval' ? 'Approval request sent' : 'Unable to route approval', result?.status ? 'success' : 'error');
    await queueManager.refreshQueue();
  });
  const [doNudge, nudgePending] = useAction(async () => {
    const result = await queueManager.nudgeApproval(item);
    showToast(result?.status === 'nudged' ? 'Approval reminder sent' : 'Unable to send reminder', result?.status === 'nudged' ? 'success' : 'error');
  });
  const [doRetry, retryPending] = useAction(async () => {
    const result = await queueManager.retryFailedPost(item);
    showToast(result?.status === 'ready_to_post' || result?.status === 'posted' || result?.status === 'completed' ? 'ERP retry submitted' : (result?.reason || 'Retry failed'), result?.status ? 'success' : 'error');
    await queueManager.refreshQueue();
  });
  const [doPost, postPending] = useAction(async () => {
    const result = await queueManager.approveAndPost(item, { override: false });
    showToast(result?.status === 'posted' || result?.status === 'approved' || result?.status === 'posted_to_erp' ? 'Invoice posted to ERP' : (result?.reason || 'ERP posting failed'), result?.status ? 'success' : 'error');
    await queueManager.refreshQueue();
  });
  const [dialog, openDialog] = useActionDialog();
  const [doReject, rejectPending] = useAction(async () => {
    const reason = await openDialog({ actionType: 'reject', title: 'Reject invoice', label: 'Rejection reason', confirmLabel: 'Reject' });
    if (!reason) return;
    window.dispatchEvent(new CustomEvent('clearledgr:reject-invoice', { detail: { emailId: item.id || item.thread_id, reason } }));
  });

  const openSource = useCallback(() => {
    if (!openSourceEmail(item)) showToast('Unable to open source email', 'error');
  }, [item]);

  const goPrev = useCallback(() => store.selectItemByOffset(-1), []);
  const goNext = useCallback(() => store.selectItemByOffset(1), []);

  // Determine primary action
  let primaryLabel = null;
  let primaryHandler = null;
  let primaryPending = false;
  if (state === 'needs_approval' || state === 'pending_approval') { primaryLabel = 'Send approval request'; primaryHandler = doApproval; primaryPending = approvalPending; }
  else if (state === 'needs_info') { primaryLabel = 'Request info'; primaryHandler = doApproval; primaryPending = approvalPending; }
  else if (state === 'approved' || state === 'ready_to_post') { primaryLabel = 'Approve & Post'; primaryHandler = doPost; primaryPending = postPending; }
  else if (state === 'failed_post') { primaryLabel = 'Retry ERP post'; primaryHandler = doRetry; primaryPending = retryPending; }

  return html`
    <div class="cl-thread-card cl-work-surface">
      <div class="cl-navigator">
        <div class="cl-thread-main">Invoice ${humanIndex} of ${totalItems || 1}</div>
        <div class="cl-nav-buttons">
          <button class="cl-btn cl-btn-secondary cl-nav-btn" onClick=${goPrev} disabled=${itemIndex <= 0}>Prev</button>
          <button class="cl-btn cl-btn-secondary cl-nav-btn" onClick=${goNext} disabled=${itemIndex >= totalItems - 1}>Next</button>
        </div>
      </div>
      <div class="cl-thread-header">
        <div class="cl-thread-title">${vendor}</div>
        <${StatePill} state=${state} />
      </div>
      <div class="cl-thread-main">${amount} · Invoice ${invoiceNumber} · Due ${dueDate}${poNumber ? ` · PO ${poNumber}` : ' · No PO'}</div>

      ${decision.tone === 'warning' && html`<div class="cl-agent-detail" style="color:#b45309">${decision.detail}</div>`}

      ${primaryLabel && html`
        <button class="cl-btn cl-btn-primary cl-primary-cta" onClick=${primaryHandler} disabled=${primaryPending}>
          ${primaryPending ? 'Processing...' : primaryLabel}
        </button>
      `}
      ${!primaryLabel && html`<div class="cl-agent-detail">No primary action required.</div>`}

      <div class="cl-thread-actions">
        <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${openSource} disabled=${!canOpenSource}>Open email</button>
        ${['received', 'validated', 'needs_approval', 'pending_approval', 'needs_info'].includes(state) && html`
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doReject} disabled=${rejectPending}>Reject</button>
        `}
        ${['needs_approval', 'pending_approval'].includes(state) && html`
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doNudge} disabled=${nudgePending}>Send reminder</button>
        `}
        ${state === 'ready_to_post' && html`
          <button class="cl-btn cl-btn-secondary cl-btn-small" onClick=${doPost} disabled=${postPending}>Post to ERP</button>
        `}
      </div>

      <${ErrorBoundary} fallback="Context tabs unavailable">
        <${ThreadContext} item=${item} queueManager=${queueManager} />
      <//>

      <details class="cl-details">
        <summary>Context fields</summary>
        <div class="cl-detail-grid">
          ${item?.subject && html`<div class="cl-detail-row"><span>Subject</span><span>${trimText(item.subject, 120)}</span></div>`}
          ${item?.sender && html`<div class="cl-detail-row"><span>Sender</span><span>${trimText(item.sender, 96)}</span></div>`}
          ${item?.exception_code && html`<div class="cl-detail-row"><span>Exception</span><span>${String(item.exception_code).replace(/_/g, ' ')}</span></div>`}
          ${Number.isFinite(confidencePercent) && html`<div class="cl-detail-row"><span>Confidence</span><span>${confidencePercent}%</span></div>`}
        </div>
      </details>

      <details class="cl-details">
        <summary>Agent timeline (${auditEvents.length})</summary>
        <${ErrorBoundary} fallback="Timeline unavailable">
          <${AgentTimeline} agentEvents=${[]} auditEvents=${auditEvents} loading=${s.auditState.loading} />
        <//>
      </details>

      <${ActionDialog} ...${dialog} />
    </div>
  `;
}

// ==================== SIDEBAR APP ====================

export default function SidebarApp({ queueManager }) {
  const s = useStore();
  const item = s.getPrimaryItem();
  const itemIndex = s.getPrimaryItemIndex();
  const logoUrl = getAssetUrl(LOGO_PATH);

  // Auto-fetch context when item changes
  useEffect(() => {
    if (item?.id && queueManager?.fetchItemContext) {
      queueManager.fetchItemContext(item).catch(() => {});
    }
  }, [item?.id]);

  // Auto-fetch audit trail when item changes
  useEffect(() => {
    if (!item?.id || !queueManager?.fetchAuditTrail) return;
    if (s.auditState.itemId === item.id && s.auditState.events.length > 0) return;
    store.update({ auditState: { itemId: item.id, loading: true, events: [] } });
    queueManager.fetchAuditTrail(item).then(events => {
      if (store.getPrimaryItem()?.id === item.id) {
        store.update({ auditState: { itemId: item.id, loading: false, events: Array.isArray(events) ? events : [] } });
      }
    }).catch(() => {
      store.update({ auditState: { itemId: item.id, loading: false, events: [] } });
    });
  }, [item?.id]);

  return html`
    <div class="cl-sidebar">
      <style>${SIDEBAR_CSS}${STATE_PILL_CSS}</style>
      <div class="cl-header">
        <div class="cl-title">
          ${logoUrl && html`<img class="cl-logo" src=${logoUrl} alt="Clearledgr" onError=${e => e.target.remove()} />`}
          Clearledgr AP
        </div>
        <div class="cl-subtitle">Embedded accounts payable execution</div>
      </div>
      <${Toast} />
      <${ErrorBoundary} fallback="Scan status unavailable">
        <${ScanStatus} queueManager=${queueManager} />
      <//>
      <div class="cl-section">
        <div class="cl-section-title">Decision</div>
        <${ErrorBoundary} fallback="Could not load invoice details">
          ${item ? html`<${WorkPanel} item=${item} queueManager=${queueManager} itemIndex=${itemIndex} totalItems=${s.queueState.length} />` : html`<div class="cl-empty">No invoices in queue.</div>`}
        <//>
      </div>
    </div>
  `;
}
