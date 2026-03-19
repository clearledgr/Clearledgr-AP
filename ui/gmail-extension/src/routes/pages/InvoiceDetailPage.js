/**
 * Invoice Detail Page — secondary record view for a single AP item.
 * Streak-style doctrine: keep the page contextual and readable, while the
 * thread sidebar remains the primary execution surface.
 */
import { h } from 'preact';
import { useCallback, useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDate, fmtDateTime, useAction } from '../route-helpers.js';
import ActionDialog, { useActionDialog } from '../../components/ActionDialog.js';
import store from '../../utils/store.js';
import {
  formatAmount,
  formatDateTime,
  getAuditEventPayload,
  getExceptionReason,
  getIssueSummary,
  getSourceMessageId,
  getSourceThreadId,
  normalizeAuditEventType,
  normalizeBudgetContext,
  openSourceEmail,
  trimText,
} from '../../utils/formatters.js';
import {
  canNudgeApprover,
  canRejectWorkItem,
  getPrimaryActionConfig,
  getWorkStateNotice,
  normalizeWorkState,
} from '../../utils/work-actions.js';

const html = htm.bind(h);
const ACTIVE_AP_ITEM_STORAGE_KEY = 'clearledgr_active_ap_item_id';

const STATE_STYLES = {
  needs_approval: { bg: '#FEFCE8', text: '#A16207', label: 'Needs approval' },
  needs_info: { bg: '#FEFCE8', text: '#A16207', label: 'Needs info' },
  validated: { bg: '#EFF6FF', text: '#1D4ED8', label: 'Validated' },
  received: { bg: '#F1F5F9', text: '#64748B', label: 'Received' },
  approved: { bg: '#ECFDF5', text: '#059669', label: 'Approved' },
  ready_to_post: { bg: '#DCFCE7', text: '#166534', label: 'Ready to post' },
  posted_to_erp: { bg: '#ECFDF5', text: '#10B981', label: 'Posted to ERP' },
  closed: { bg: '#F1F5F9', text: '#64748B', label: 'Closed' },
  rejected: { bg: '#FEF2F2', text: '#DC2626', label: 'Rejected' },
  failed_post: { bg: '#FEF2F2', text: '#DC2626', label: 'Failed post' },
};

function StatePill({ state }) {
  const tone = STATE_STYLES[state] || {
    bg: '#F1F5F9',
    text: '#64748B',
    label: String(state || 'received').replace(/_/g, ' '),
  };
  return html`<span style="
    font-size:11px;font-weight:700;padding:4px 10px;border-radius:999px;
    background:${tone.bg};color:${tone.text};text-transform:uppercase;letter-spacing:0.02em;
  ">${tone.label}</span>`;
}

function getBlockers(item, state, budgetContext) {
  const blockers = [];
  const push = (key, label, detail) => {
    if (!label || blockers.some((entry) => entry.key === key)) return;
    blockers.push({ key, label, detail });
  };

  if (budgetContext?.requiresDecision) {
    push('budget', 'Budget review required', 'A budget decision is still required before this invoice can move forward.');
  }

  const exceptionCode = String(item?.exception_code || '').trim().toLowerCase();
  const exceptionReason = getExceptionReason(exceptionCode);
  if (exceptionReason) {
    push('exception', exceptionReason, getIssueSummary(item));
  }

  if (!item?.po_number && exceptionCode.includes('po')) {
    push('po', 'PO reference missing', 'Link the correct PO before continuing this invoice.');
  }

  const confidence = Number(item?.confidence);
  if (Number.isFinite(confidence) && confidence < 0.95 && !['posted_to_erp', 'closed', 'rejected'].includes(state)) {
    push('confidence', 'Review extracted fields', `Current confidence is ${Math.round(confidence * 100)}%, so a field check is still required.`);
  }

  if (state === 'needs_approval') {
    push('approval', 'Waiting on approver', 'The approval request is still pending.');
  }
  if (state === 'needs_info') {
    push('info', 'Missing invoice details', 'Clearledgr still needs more information before this invoice can continue.');
  }
  if (state === 'failed_post') {
    push('erp', 'ERP posting failed', 'Retry the ERP post or review the connector result.');
  }
  if (blockers.length === 0 && state === 'received') {
    push('received', 'Ready for review', 'This invoice is ready for AP validation and approval routing.');
  }
  if (blockers.length === 0 && state === 'validated') {
    push('validated', 'Ready for approval', 'Checks are complete and the invoice can be routed to approval.');
  }
  return blockers.slice(0, 5);
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
    { key: 'email', label: 'Email', text: hasEmail ? 'Linked' : 'Not linked', ok: hasEmail },
    { key: 'attachment', label: 'Attachment', text: hasAttachment ? 'Attached' : 'No file', ok: hasAttachment },
    { key: 'approval', label: 'Approval', text: hasApproval ? (state === 'needs_approval' ? 'Routed' : 'Available') : 'Not routed', ok: hasApproval },
    { key: 'erp', label: 'ERP', text: hasErpLink ? (item?.erp_reference || erp.erp_reference ? 'Linked' : 'Connected') : 'Not connected', ok: hasErpLink },
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
  const detail = trimText(
    String(event?.operator_message || safeDetail),
    160,
  );
  return {
    title,
    detail,
    timestamp: formatDateTime(event?.ts || event?.created_at || event?.timestamp || event?.updated_at),
  };
}

async function executeIntent(api, orgId, intent, input) {
  return api('/api/agent/intents/execute', {
    method: 'POST',
    body: JSON.stringify({
      intent,
      input: input && typeof input === 'object' ? input : {},
      organization_id: orgId,
    }),
  });
}

function selectActiveItem(itemId) {
  if (!itemId) return;
  store.setSelectedItem(String(itemId));
  if (typeof window !== 'undefined' && window?.localStorage) {
    try {
      window.localStorage.setItem(ACTIVE_AP_ITEM_STORAGE_KEY, String(itemId));
    } catch {
      /* best effort */
    }
  }
}

export default function InvoiceDetailPage({ api, toast, orgId, navigate, routeParams }) {
  const [item, setItem] = useState(null);
  const [auditEvents, setAuditEvents] = useState([]);
  const [context, setContext] = useState(null);
  const [loading, setLoading] = useState(true);
  const [dialog, openDialog] = useActionDialog();
  const itemId = routeParams?.id || '';

  const refresh = useCallback(async () => {
    if (!itemId) return;
    setLoading(true);
    try {
      const [itemData, auditData, ctxData] = await Promise.all([
        api(`/api/ap/items/${encodeURIComponent(itemId)}?organization_id=${encodeURIComponent(orgId)}`).catch(() => null),
        api(`/api/ap/items/${encodeURIComponent(itemId)}/audit?organization_id=${encodeURIComponent(orgId)}`).catch(() => ({ events: [] })),
        api(`/api/ap/items/${encodeURIComponent(itemId)}/context?organization_id=${encodeURIComponent(orgId)}`).catch(() => null),
      ]);
      setItem(itemData);
      if (itemData?.id) selectActiveItem(itemData.id);
      setAuditEvents(Array.isArray(auditData?.events) ? auditData.events : []);
      setContext(ctxData);
    } finally {
      setLoading(false);
    }
  }, [api, itemId, orgId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (itemId) selectActiveItem(itemId);
  }, [itemId]);

  const state = normalizeWorkState(item?.state || 'received');
  const budgetContext = normalizeBudgetContext(context || {}, item);
  const blockers = useMemo(() => getBlockers(item, state, budgetContext), [item, state, budgetContext]);
  const evidence = useMemo(() => getEvidenceChecklist(item, state, context), [item, state, context]);
  const stateNotice = getWorkStateNotice(state);
  const primaryAction = getPrimaryActionConfig(state);
  const canOpenEmail = Boolean(item && (getSourceThreadId(item) || getSourceMessageId(item) || item.subject));
  const smartRejectDefault = item?.exception_code ? getExceptionReason(item.exception_code) : '';

  const [doRequestApproval, requestingApproval] = useAction(async () => {
    const result = await executeIntent(api, orgId, 'request_approval', {
      ap_item_id: item.id,
      email_id: item.thread_id || item.message_id || item.id,
      source_channel: 'gmail_route',
      source_channel_id: 'gmail_route',
      source_message_ref: item.thread_id || item.message_id || item.id,
    });
    const ok = ['pending_approval', 'needs_approval'].includes(String(result?.status || '').toLowerCase());
    toast(ok ? 'Approval request sent.' : (result?.reason || 'Could not send approval request.'), ok ? 'success' : 'error');
    await refresh();
  });

  const [doPrepareInfo, preparingInfo] = useAction(async () => {
    const result = await executeIntent(api, orgId, 'prepare_vendor_followups', {
      ap_item_id: item.id,
      email_id: item.thread_id || item.message_id || item.id,
      reason: 'Request missing invoice details from vendor',
    });
    const ok = ['prepared', 'queued'].includes(String(result?.status || '').toLowerCase());
    toast(ok ? 'Info request draft prepared.' : (result?.reason || 'Could not prepare info request.'), ok ? 'success' : 'error');
    await refresh();
  });

  const [doNudge, nudging] = useAction(async () => {
    const result = await executeIntent(api, orgId, 'nudge_approval', {
      ap_item_id: item.id,
      email_id: item.thread_id || item.message_id || item.id,
      source_channel: 'gmail_route',
      source_channel_id: 'gmail_route',
      source_message_ref: item.thread_id || item.message_id || item.id,
    });
    const ok = String(result?.status || '').toLowerCase() === 'nudged';
    toast(ok ? 'Approval reminder sent.' : (result?.reason || 'Could not send reminder.'), ok ? 'success' : 'error');
    await refresh();
  });

  const [doRetry, retrying] = useAction(async () => {
    const result = await api(`/api/ap/items/${encodeURIComponent(item.id)}/retry-post?organization_id=${encodeURIComponent(orgId)}`, {
      method: 'POST',
    });
    const ok = ['ready_to_post', 'posted', 'completed'].includes(String(result?.status || '').toLowerCase());
    toast(ok ? 'ERP retry submitted.' : (result?.reason || 'Retry failed.'), ok ? 'success' : 'error');
    await refresh();
  });

  const [doPost, posting] = useAction(async () => {
    const confirmed = await openDialog({
      dialogMode: 'confirm',
      actionType: 'preview_erp_post',
      title: 'Preview ERP post',
      message: 'Review this invoice before posting it to the ERP.',
      previewLines: [
        item?.vendor_name || item?.vendor || 'Unknown vendor',
        formatAmount(item?.amount, item?.currency || 'USD'),
        `Invoice ${item?.invoice_number || 'N/A'}`,
        item?.due_date ? `Due ${item.due_date}` : null,
      ].filter(Boolean),
      confirmLabel: 'Post to ERP',
      cancelLabel: 'Cancel',
    });
    if (!confirmed) return;
    const result = await executeIntent(api, orgId, 'post_to_erp', {
      ap_item_id: item.id,
      email_id: item.thread_id || item.message_id || item.id,
      source_channel: 'gmail_route',
      source_channel_id: 'gmail_route',
      source_message_ref: item.thread_id || item.message_id || item.id,
    });
    const ok = ['posted', 'approved', 'posted_to_erp'].includes(String(result?.status || '').toLowerCase());
    toast(ok ? 'Invoice posted to ERP.' : (result?.reason || 'ERP posting failed.'), ok ? 'success' : 'error');
    await refresh();
  });

  const [doReject, rejecting] = useAction(async () => {
    const reason = await openDialog({
      actionType: 'reject',
      title: 'Reject invoice',
      label: 'Rejection reason',
      confirmLabel: 'Reject',
      defaultValue: smartRejectDefault,
    });
    if (!reason) return;
    const result = await executeIntent(api, orgId, 'reject_invoice', {
      ap_item_id: item.id,
      email_id: item.thread_id || item.message_id || item.id,
      reason,
      source_channel: 'gmail_route',
      source_channel_id: 'gmail_route',
      source_message_ref: item.thread_id || item.message_id || item.id,
    });
    const ok = String(result?.status || '').toLowerCase() === 'rejected';
    toast(ok ? 'Invoice rejected.' : (result?.reason || 'Could not reject invoice.'), ok ? 'success' : 'error');
    await refresh();
  });

  const openEmail = useCallback(() => {
    if (item && !openSourceEmail(item)) {
      toast('Unable to open source email.', 'error');
    }
  }, [item, toast]);

  if (loading) {
    return html`<div class="panel"><p class="muted">Loading invoice…</p></div>`;
  }

  if (!item) {
    return html`
      <div class="panel">
        <p class="muted">Invoice not found.</p>
        <button class="alt" onClick=${() => navigate('clearledgr/pipeline')}>Back to pipeline</button>
      </div>
    `;
  }

  let primaryHandler = null;
  let primaryPending = false;
  if (primaryAction?.id === 'request_approval') {
    primaryHandler = doRequestApproval;
    primaryPending = requestingApproval;
  } else if (primaryAction?.id === 'prepare_info_request') {
    primaryHandler = doPrepareInfo;
    primaryPending = preparingInfo;
  } else if (primaryAction?.id === 'nudge_approver') {
    primaryHandler = doNudge;
    primaryPending = nudging;
  } else if (primaryAction?.id === 'preview_erp_post') {
    primaryHandler = doPost;
    primaryPending = posting;
  } else if (primaryAction?.id === 'retry_erp_post') {
    primaryHandler = doRetry;
    primaryPending = retrying;
  }

  return html`
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;flex-wrap:wrap">
      <button class="alt" style="padding:6px 14px;font-size:13px" onClick=${() => navigate('clearledgr/pipeline')}>← Back to pipeline</button>
      ${canOpenEmail && html`<button class="alt" onClick=${openEmail}>Open email</button>`}
    </div>

    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap">
        <div>
          <h3 style="margin:0 0 4px">${item.vendor_name || item.vendor || 'Unknown vendor'}</h3>
          <div style="font-size:28px;font-weight:700;letter-spacing:-0.02em">${formatAmount(item.amount, item.currency || 'USD')}</div>
          <div class="muted" style="margin-top:6px">
            Invoice ${item.invoice_number || 'N/A'} · Due ${item.due_date || 'N/A'} · ${item.po_number ? `PO ${item.po_number}` : 'No PO'}
          </div>
        </div>
        <${StatePill} state=${state} />
      </div>

      ${stateNotice && html`<div class="muted" style="margin-top:12px">${stateNotice}</div>`}

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px">
        ${primaryAction?.label && primaryHandler && html`
          <button onClick=${primaryHandler} disabled=${primaryPending}>
            ${primaryPending ? 'Processing…' : primaryAction.label}
          </button>
        `}
        ${canRejectWorkItem(state) && html`
          <button class="alt" onClick=${doReject} disabled=${rejecting}>Reject</button>
        `}
        ${canNudgeApprover(state) && primaryAction?.id !== 'nudge_approver' && html`
          <button class="alt" onClick=${doNudge} disabled=${nudging}>Nudge approver</button>
        `}
      </div>
    </div>

    <div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px">
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="panel">
          <h3 style="margin-top:0">Blocked because</h3>
          ${blockers.length
            ? html`<div style="display:flex;flex-direction:column;gap:10px">
                ${blockers.map((blocker) => html`
                  <div key=${blocker.key} style="padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg)">
                    <div style="font-weight:700;font-size:13px">${blocker.label}</div>
                    ${blocker.detail && html`<div class="muted" style="margin-top:4px;font-size:13px">${blocker.detail}</div>`}
                  </div>
                `)}
              </div>`
            : html`<p class="muted">No active blockers.</p>`}
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Evidence checklist</h3>
          <div style="display:flex;flex-direction:column;gap:10px">
            ${evidence.map((entry) => html`
              <div key=${entry.key} style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding-bottom:8px;border-bottom:1px solid var(--border)">
                <span>${entry.label}</span>
                <span style="font-size:12px;font-weight:700;color:${entry.ok ? 'var(--brand-muted)' : 'var(--ink-muted)'}">${entry.text}</span>
              </div>
            `)}
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Invoice details</h3>
          <div style="display:flex;flex-direction:column;gap:10px">
            ${detailRow('Invoice #', item.invoice_number || '—')}
            ${detailRow('Due date', item.due_date ? fmtDate(item.due_date) : '—')}
            ${detailRow('PO number', item.po_number || 'None')}
            ${detailRow('Confidence', item.confidence ? `${Math.round(Number(item.confidence) * 100)}%` : '—')}
            ${detailRow('Sender', item.sender || '—')}
            ${detailRow('Subject', item.subject || '—')}
            ${detailRow('Last update', fmtDateTime(item.updated_at || item.created_at))}
          </div>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="panel">
          <h3 style="margin-top:0">What happened</h3>
          ${auditEvents.length === 0
            ? html`<p class="muted">No audit events yet.</p>`
            : html`<div style="display:flex;flex-direction:column;gap:12px">
                ${auditEvents.map((event, index) => {
                  const row = getAuditRow(event);
                  return html`
                    <div key=${event?.id || index} style="padding:12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg)">
                      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
                        <div style="font-weight:700;font-size:13px">${row.title}</div>
                        ${row.timestamp && html`<div class="muted" style="font-size:12px;white-space:nowrap">${row.timestamp}</div>`}
                      </div>
                      <div class="muted" style="margin-top:6px;font-size:13px;line-height:1.5">${row.detail}</div>
                    </div>
                  `;
                })}
              </div>`}
        </div>

        ${context && html`
          <div class="panel">
            <h3 style="margin-top:0">Context</h3>
            ${context.reasoning_summary && html`<p style="font-size:13px;color:var(--ink-secondary);line-height:1.6">${context.reasoning_summary}</p>`}
            ${context.reasoning_risks && html`<p style="font-size:13px;color:var(--amber);line-height:1.6">${context.reasoning_risks}</p>`}
            ${context.next_action && html`<p class="muted" style="margin:0"><strong>Best next step:</strong> ${context.next_action}</p>`}
          </div>
        `}
      </div>
    </div>

    <${ActionDialog} ...${dialog} />
  `;
}

function detailRow(label, value) {
  return html`
    <div style="display:flex;justify-content:space-between;gap:16px;padding-bottom:8px;border-bottom:1px solid var(--border)">
      <span class="muted">${label}</span>
      <span style="font-weight:500;text-align:right;max-width:65%">${value}</span>
    </div>
  `;
}
