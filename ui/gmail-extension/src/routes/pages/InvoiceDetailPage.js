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
import { hasOpsAccessRole } from '../../utils/roles.js';
import {
  formatAmount,
  getExceptionReason,
  getIssueSummary,
  getSourceMessageId,
  getSourceThreadId,
  normalizeBudgetContext,
  openSourceEmail,
  partitionAuditEvents,
} from '../../utils/formatters.js';
import {
  canNudgeApprover,
  canRejectWorkItem,
  getPrimaryActionConfig,
  getWorkStateNotice,
  normalizeWorkState,
} from '../../utils/work-actions.js';
import { focusPipelineItem } from '../pipeline-views.js';
import {
  buildReplyTemplatePrefill,
  getAllReplyTemplates,
  getBootstrappedReplyTemplatePreferences,
  normalizeReplyTemplatePreferences,
  readReplyTemplatePreferences,
  resolveReplyTemplate,
  writeReplyTemplatePreferences,
} from '../reply-templates.js';

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

function AuditCard({ row }) {
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

function RelatedRecordRow({ label, item, onOpen }) {
  if (!item?.id) return null;
  return html`
    <div style="padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <div>
          <div class="muted" style="font-size:11px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase">${label}</div>
          <div style="font-size:13px;font-weight:700;margin-top:4px">${item.vendor_name || 'Unknown vendor'} · ${item.invoice_number || 'No invoice #'}</div>
          <div class="muted" style="font-size:12px;margin-top:4px">
            ${formatAmount(item.amount, item.currency || 'USD')} · ${String(item.state || 'received').replace(/_/g, ' ')}
          </div>
        </div>
        <button class="alt" onClick=${onOpen} style="padding:8px 12px;font-size:12px">Open</button>
      </div>
    </div>
  `;
}

function SourceGroupRow({ group }) {
  if (!group) return null;
  return html`
    <div style="padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:6px">
        <strong style="font-size:13px">${String(group.source_type || 'unknown').replace(/_/g, ' ')}</strong>
        <span class="muted" style="font-size:12px;font-weight:700">${Number(group.count || 0).toLocaleString()}</span>
      </div>
      ${(group.items || []).slice(0, 2).map((entry, index) => html`
        <div key=${`${group.source_type}-${entry?.source_ref || index}`} class="muted" style="font-size:12px;line-height:1.5;padding-top:${index > 0 ? '8px' : '0'}">
          <div>${entry?.subject || entry?.source_ref || 'Linked evidence'}</div>
          <div>${entry?.sender || 'Unknown sender'}${entry?.detected_at ? ` · ${fmtDateTime(entry.detected_at)}` : ''}</div>
        </div>
      `)}
    </div>
  `;
}

function TemplateActionRow({ template, onDraft }) {
  return html`
    <div style="display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
      <div>
        <strong style="display:block;font-size:13px">${template.name}</strong>
        <span class="muted" style="font-size:12px">${template.description || 'Reusable reply template.'}</span>
      </div>
      <button class="alt" onClick=${onDraft} style="padding:8px 12px;font-size:12px">Draft</button>
    </div>
  `;
}

export default function InvoiceDetailPage({ api, bootstrap, toast, orgId, userEmail, navigate, routeParams }) {
  const [item, setItem] = useState(null);
  const [auditEvents, setAuditEvents] = useState([]);
  const [context, setContext] = useState(null);
  const [loading, setLoading] = useState(true);
  const [dialog, openDialog] = useActionDialog();
  const itemId = routeParams?.id || '';
  const templateScope = { orgId, userEmail };
  const [templatePrefs, setTemplatePrefs] = useState(() => readReplyTemplatePreferences(templateScope));
  const bootstrapTemplatePrefs = getBootstrappedReplyTemplatePreferences(bootstrap);

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

  useEffect(() => {
    const local = readReplyTemplatePreferences(templateScope);
    const remote = bootstrapTemplatePrefs ? normalizeReplyTemplatePreferences(bootstrapTemplatePrefs) : null;
    if (remote && JSON.stringify(remote) !== JSON.stringify(local)) {
      setTemplatePrefs(writeReplyTemplatePreferences(templateScope, remote));
      return;
    }
    setTemplatePrefs(local);
  }, [bootstrapTemplatePrefs, orgId, userEmail]);

  const state = normalizeWorkState(item?.state || 'received');
  const actorRole = bootstrap?.current_user?.role || 'operator';
  const readOnlyMode = !hasOpsAccessRole(actorRole);
  const pipelineScope = { orgId, userEmail };
  const budgetContext = normalizeBudgetContext(context || {}, item);
  const blockers = useMemo(() => getBlockers(item, state, budgetContext), [item, state, budgetContext]);
  const evidence = useMemo(() => getEvidenceChecklist(item, state, context), [item, state, context]);
  const auditSections = useMemo(() => partitionAuditEvents(auditEvents), [auditEvents]);
  const stateNotice = getWorkStateNotice(state);
  const primaryAction = getPrimaryActionConfig(state, actorRole);
  const canOpenEmail = Boolean(item && (getSourceThreadId(item) || getSourceMessageId(item) || item.subject));
  const smartRejectDefault = item?.exception_code ? getExceptionReason(item.exception_code) : '';
  const relatedRecords = context?.related_records || {};
  const sourceGroups = Array.isArray(context?.email?.source_groups?.groups) ? context.email.source_groups.groups : [];
  const replyTemplates = useMemo(() => getAllReplyTemplates(templatePrefs), [templatePrefs]);
  const quickReplyTemplates = useMemo(() => {
    const ordered = ['vendor_missing_info', 'payment_status', 'rejection_note', 'approval_nudge']
      .map((templateId) => resolveReplyTemplate(templatePrefs, templateId))
      .filter(Boolean);
    if (ordered.length > 0) return ordered;
    return replyTemplates.slice(0, 4);
  }, [replyTemplates, templatePrefs]);

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

  const openInPipeline = useCallback(() => {
    if (!item?.id) return;
    selectActiveItem(item.id);
    focusPipelineItem(pipelineScope, item, 'detail');
    navigate('clearledgr/pipeline');
  }, [item, navigate, pipelineScope]);

  const openVendorRecord = useCallback(() => {
    const vendorName = String(item?.vendor_name || item?.vendor || '').trim();
    if (!vendorName) return;
    navigate(`clearledgr/vendor/${encodeURIComponent(vendorName)}`);
  }, [item, navigate]);

  const openRelatedRecord = useCallback((relatedItem) => {
    if (!relatedItem?.id) return;
    focusPipelineItem(pipelineScope, relatedItem, 'related_record');
    navigate(`clearledgr/invoice/${encodeURIComponent(relatedItem.id)}`);
  }, [navigate, pipelineScope]);

  const [draftReply, draftingReply] = useAction(async (templateId) => {
    const template = resolveReplyTemplate(templatePrefs, templateId);
    if (!template || !item) {
      toast('Template unavailable for this record.', 'warning');
      return;
    }
    const issueSummary = getIssueSummary(item) || context?.summary?.text || 'additional information is required';
    const prefill = buildReplyTemplatePrefill(template, item, {
      issue_summary: issueSummary,
      next_action: item?.next_action || context?.summary?.text || 'Review in Clearledgr',
    });
    try {
      await store.composeWithPrefill(prefill);
      toast('Draft opened in Gmail compose.', 'success');
    } catch {
      toast('Could not open Gmail compose for this template.', 'error');
    }
  });

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
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="alt" style="padding:6px 14px;font-size:13px" onClick=${openInPipeline}>← Back to pipeline</button>
        ${canOpenEmail && html`<button class="alt" onClick=${openEmail}>Open email</button>`}
        ${(item?.vendor_name || item?.vendor) && html`<button class="alt" onClick=${openVendorRecord}>Open vendor record</button>`}
      </div>
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
        ${readOnlyMode && html`
          <div class="muted" style="width:100%">Read-only view. Queue actions are reserved for AP operators.</div>
        `}
        ${canRejectWorkItem(state, actorRole) && html`
          <button class="alt" onClick=${doReject} disabled=${rejecting}>Reject</button>
        `}
        ${canNudgeApprover(state, actorRole) && primaryAction?.id !== 'nudge_approver' && html`
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

        <div class="panel">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px">
            <div>
              <h3 style="margin:0 0 4px">Linked records</h3>
              <p class="muted" style="margin:0">Related invoices and superseded records linked to this AP item.</p>
            </div>
            ${(item?.vendor_name || item?.vendor) && html`<button class="alt" onClick=${openVendorRecord} style="padding:8px 12px;font-size:12px">Vendor record</button>`}
          </div>
          <div style="display:flex;flex-direction:column;gap:10px">
            ${(relatedRecords?.supersession?.previous_item || relatedRecords?.supersession?.next_item || (relatedRecords?.same_invoice_number_items || []).length || (relatedRecords?.vendor_recent_items || []).length)
              ? html`
                  ${relatedRecords?.supersession?.previous_item
                    ? html`<${RelatedRecordRow}
                        label="Supersedes"
                        item=${relatedRecords.supersession.previous_item}
                        onOpen=${() => openRelatedRecord(relatedRecords.supersession.previous_item)}
                      />`
                    : null}
                  ${relatedRecords?.supersession?.next_item
                    ? html`<${RelatedRecordRow}
                        label="Superseded by"
                        item=${relatedRecords.supersession.next_item}
                        onOpen=${() => openRelatedRecord(relatedRecords.supersession.next_item)}
                      />`
                    : null}
                  ${(relatedRecords?.same_invoice_number_items || []).slice(0, 2).map((relatedItem) => html`
                    <${RelatedRecordRow}
                      key=${relatedItem.id}
                      label="Same invoice number"
                      item=${relatedItem}
                      onOpen=${() => openRelatedRecord(relatedItem)}
                    />
                  `)}
                  ${(relatedRecords?.vendor_recent_items || []).slice(0, 2).map((relatedItem) => html`
                    <${RelatedRecordRow}
                      key=${relatedItem.id}
                      label="Recent vendor item"
                      item=${relatedItem}
                      onOpen=${() => openRelatedRecord(relatedItem)}
                    />
                  `)}
                `
              : html`<p class="muted" style="margin:0">No linked AP records yet.</p>`}
          </div>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="panel">
          <h3 style="margin-top:0">Reply templates</h3>
          <p class="muted" style="margin:0 0 12px">Draft consistent vendor or approver messages from this record without leaving Gmail.</p>
          ${quickReplyTemplates.length === 0
            ? html`<p class="muted" style="margin:0">No reply templates are available yet.</p>`
            : html`<div style="display:flex;flex-direction:column;gap:10px">
                ${quickReplyTemplates.map((template) => html`
                  <${TemplateActionRow}
                    key=${template.id}
                    template=${template}
                    onDraft=${() => draftReply(template.id)}
                  />
                `)}
              </div>`}
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
            <button class="alt" onClick=${() => navigate('clearledgr/templates')} style="padding:8px 12px;font-size:12px">Manage templates</button>
            ${draftingReply && html`<span class="muted" style="font-size:12px;align-self:center">Opening compose…</span>`}
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Record history</h3>
          ${auditSections.rows.length === 0
            ? html`<p class="muted">No audit events yet.</p>`
            : html`
              <div style="display:flex;flex-direction:column;gap:14px">
                ${auditSections.primaryRows.length > 0 && html`
                  <div style="display:flex;flex-direction:column;gap:10px">
                    <div style="font-size:12px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;color:var(--ink-muted)">Key history</div>
                    <div class="cl-audit-list">
                      ${auditSections.primaryRows.map((row, index) => html`<${AuditCard} key=${row.event?.id || index} row=${row} />`)}
                    </div>
                  </div>
                `}
                ${auditSections.secondaryRows.length > 0 && html`
                  <div style="display:flex;flex-direction:column;gap:10px">
                    <div style="font-size:12px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;color:var(--ink-muted)">Background activity</div>
                    <div class="cl-audit-list">
                      ${auditSections.secondaryRows.map((row, index) => html`<${AuditCard} key=${row.event?.id || `secondary-${index}`} row=${row} />`)}
                    </div>
                  </div>
                `}
              </div>
            `}
        </div>

        ${context && html`
          <div class="panel">
            <h3 style="margin-top:0">Context</h3>
            ${context.reasoning_summary && html`<p style="font-size:13px;color:var(--ink-secondary);line-height:1.6">${context.reasoning_summary}</p>`}
            ${context.reasoning_risks && html`<p style="font-size:13px;color:var(--amber);line-height:1.6">${context.reasoning_risks}</p>`}
            ${context.next_action && html`<p class="muted" style="margin:0"><strong>Best next step:</strong> ${context.next_action}</p>`}
          </div>
        `}

        ${context && html`
          <div class="panel">
            <h3 style="margin-top:0">Evidence sources</h3>
            ${sourceGroups.length === 0
              ? html`<p class="muted" style="margin:0">No linked evidence sources yet.</p>`
              : html`<div style="display:flex;flex-direction:column;gap:10px">
                  ${sourceGroups.slice(0, 5).map((group) => html`<${SourceGroupRow} key=${group.source_type} group=${group} />`)}
                </div>`}
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
