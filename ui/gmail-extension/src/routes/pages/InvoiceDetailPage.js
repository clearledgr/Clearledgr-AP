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
  getFieldReviewBlockers,
  getEvidenceChecklistEntries,
  getIssueSummary,
  getSourceMessageId,
  getSourceThreadId,
  getWorkflowPauseReason,
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
  shouldOfferResumeWorkflow,
} from '../../utils/work-actions.js';
import {
  getDocumentReferenceLabel,
  getDocumentReferenceText,
  getDocumentTypeLabel,
  getNonInvoiceWorkflowGuidance,
  isInvoiceDocumentType,
  normalizeDocumentType,
} from '../../utils/document-types.js';
import { navigateToRecordDetail } from '../../utils/record-route.js';
import { navigateToVendorRecord } from '../../utils/vendor-route.js';
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

function getNonInvoiceActions(item) {
  const documentType = normalizeDocumentType(item?.document_type);
  if (documentType === 'credit_note') {
    return [
      { id: 'apply_to_invoice', label: 'Apply to invoice', requiresReference: true, referenceLabel: 'Invoice reference' },
      { id: 'record_vendor_credit', label: 'Record vendor credit', requiresReference: false },
      { id: 'needs_followup', label: 'Needs follow-up', requiresReference: false },
    ];
  }
  if (documentType === 'refund') {
    return [
      { id: 'link_to_payment', label: 'Link to payment', requiresReference: true, referenceLabel: 'Payment reference' },
      { id: 'record_vendor_refund', label: 'Record vendor refund', requiresReference: false },
      { id: 'needs_followup', label: 'Needs follow-up', requiresReference: false },
    ];
  }
  if (documentType === 'payment') {
    return [
      { id: 'link_to_payment', label: 'Link to payment', requiresReference: true, referenceLabel: 'Payment reference' },
      { id: 'record_payment_confirmation', label: 'Record payment confirmation', requiresReference: false },
      { id: 'needs_followup', label: 'Needs follow-up', requiresReference: false },
    ];
  }
  if (documentType === 'receipt') {
    return [
      { id: 'link_to_payment', label: 'Link to payment', requiresReference: true, referenceLabel: 'Payment reference' },
      { id: 'archive_receipt', label: 'Archive receipt', requiresReference: false },
      { id: 'needs_followup', label: 'Needs follow-up', requiresReference: false },
    ];
  }
  if (documentType === 'statement') {
    return [
      { id: 'send_to_reconciliation', label: 'Send to reconciliation', requiresReference: false },
      { id: 'needs_followup', label: 'Needs follow-up', requiresReference: false },
    ];
  }
  if (documentType === 'payment_request') {
    return [
      { id: 'route_outside_invoice_workflow', label: 'Route outside invoice workflow', requiresReference: false },
      { id: 'needs_followup', label: 'Needs follow-up', requiresReference: false },
    ];
  }
  return [
    { id: 'mark_reviewed', label: 'Mark reviewed', requiresReference: false },
    { id: 'needs_followup', label: 'Needs follow-up', requiresReference: false },
  ];
}

function getBlockers(item, state, budgetContext, documentType = 'invoice') {
  const blockers = [];
  const fieldReviewBlockers = getFieldReviewBlockers(item);
  const pauseReason = getWorkflowPauseReason(item);
  const documentLabel = getDocumentTypeLabel(documentType, { lowercase: true });
  const isInvoiceDocument = isInvoiceDocumentType(documentType);
  const push = (key, label, detail) => {
    if (!label || blockers.some((entry) => entry.key === key)) return;
    blockers.push({ key, label, detail });
  };

  if (budgetContext?.requiresDecision) {
    push('budget', 'Budget review required', `A budget decision is still required before this ${isInvoiceDocument ? 'invoice' : 'record'} can move forward.`);
  }

  const exceptionCode = String(item?.exception_code || '').trim().toLowerCase();
  const exceptionReason = getExceptionReason(exceptionCode);
  if (exceptionReason) {
    push('exception', exceptionReason, getIssueSummary(item));
  }

  if (!item?.po_number && exceptionCode.includes('po')) {
    push('po', 'PO reference missing', `Link the correct PO before continuing this ${isInvoiceDocument ? 'invoice' : 'record'}.`);
  }

  const confidence = Number(item?.confidence);
  if ((item?.requires_field_review || (Number.isFinite(confidence) && confidence < 0.95)) && !['posted_to_erp', 'closed', 'rejected'].includes(state)) {
    push(
      'confidence',
      fieldReviewBlockers.length ? 'Workflow paused for field review' : 'Review extracted fields',
      pauseReason || `Current confidence is ${Math.round(confidence * 100)}%, so a field check is still required.`,
    );
  }

  if (state === 'needs_approval') {
    push('approval', 'Waiting on approver', 'The approval request is still pending.');
  }
  if (state === 'needs_info') {
    push(
      'info',
      isInvoiceDocument ? 'Missing invoice details' : 'Missing document details',
      `Clearledgr still needs more information before this ${isInvoiceDocument ? 'invoice' : 'record'} can continue.`,
    );
  }
  if (state === 'failed_post') {
    push('erp', 'ERP posting failed', 'Retry the ERP post or review the connector result.');
  }
  if (blockers.length === 0 && state === 'received') {
    push(
      'received',
      isInvoiceDocument ? 'Ready for review' : 'Needs finance review',
      isInvoiceDocument
        ? 'This invoice is ready for AP validation and approval routing.'
        : getNonInvoiceWorkflowGuidance(documentType),
    );
  }
  if (blockers.length === 0 && state === 'validated') {
    push(
      'validated',
      isInvoiceDocument ? 'Ready for approval' : `Ready to review ${documentLabel}`,
      isInvoiceDocument
        ? 'Checks are complete and the invoice can be routed to approval.'
        : getNonInvoiceWorkflowGuidance(documentType),
    );
  }
  return blockers.slice(0, 5);
}

function FieldReviewRows({ blockers, pauseReason, onResolve = null, resolvingField = '' }) {
  if ((!Array.isArray(blockers) || blockers.length === 0) && !pauseReason) {
    return html`<p class="muted">No paused field review.</p>`;
  }

  return html`
    <div style="display:flex;flex-direction:column;gap:10px">
      ${pauseReason && html`
        <div style="padding:10px 12px;border:1px solid #fcd34d;border-radius:var(--radius-sm);background:#FEFCE8;color:#78350f;font-size:13px;line-height:1.45">
          ${pauseReason}
        </div>
      `}
      ${(blockers || []).map((blocker) => html`
        <div key=${`${blocker.field || 'field'}-${blocker.kind || 'review'}`} style="padding:12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);display:flex;flex-direction:column;gap:6px">
          <div style="font-weight:700;font-size:13px">${blocker.field_label || 'Field'} blocked</div>
          ${blocker.email_value_display && html`
            <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
              <span class="muted" style="font-size:12px">Email said</span>
              <span style="font-size:13px;font-weight:600;text-align:right">${blocker.email_value_display}</span>
            </div>
          `}
          ${blocker.attachment_value_display && html`
            <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
              <span class="muted" style="font-size:12px">Attachment said</span>
              <span style="font-size:13px;font-weight:600;text-align:right">${blocker.attachment_value_display}</span>
            </div>
          `}
          <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
            <span class="muted" style="font-size:12px">Source selected</span>
            <span style="font-size:13px;font-weight:600;text-align:right">
              ${blocker.winning_source_label || 'Review required'}
              ${blocker.winning_value_display ? ` (${blocker.winning_value_display})` : ''}
            </span>
          </div>
          <div class="muted" style="font-size:12px;line-height:1.45">${blocker.winner_reason || blocker.reason_label || blocker.paused_reason}</div>
          ${typeof onResolve === 'function' && html`
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
              ${blocker.email_value !== null && blocker.email_value !== undefined && html`
                <button
                  class="alt"
                  onClick=${() => onResolve(blocker, 'email')}
                  disabled=${Boolean(resolvingField === `${blocker.field}:email`)}
                  style="padding:8px 12px;font-size:12px"
                >
                  ${resolvingField === `${blocker.field}:email` ? 'Saving…' : 'Use email'}
                </button>
              `}
              ${blocker.attachment_value !== null && blocker.attachment_value !== undefined && html`
                <button
                  class="alt"
                  onClick=${() => onResolve(blocker, 'attachment')}
                  disabled=${Boolean(resolvingField === `${blocker.field}:attachment`)}
                  style="padding:8px 12px;font-size:12px"
                >
                  ${resolvingField === `${blocker.field}:attachment` ? 'Saving…' : 'Use attachment'}
                </button>
              `}
              <button
                class="alt"
                onClick=${() => onResolve(blocker, 'manual')}
                disabled=${Boolean(resolvingField === `${blocker.field}:manual`)}
                style="padding:8px 12px;font-size:12px"
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
  const [resolvingFieldKey, setResolvingFieldKey] = useState('');
  const [resolvingNonInvoiceKey, setResolvingNonInvoiceKey] = useState('');
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
  const documentType = normalizeDocumentType(item?.document_type);
  const documentLabel = getDocumentTypeLabel(documentType);
  const isInvoiceDocument = isInvoiceDocumentType(documentType);
  const actorRole = bootstrap?.current_user?.role || 'operator';
  const readOnlyMode = !hasOpsAccessRole(actorRole);
  const pipelineScope = { orgId, userEmail };
  const budgetContext = normalizeBudgetContext(context || {}, item);
  const blockers = useMemo(() => getBlockers(item, state, budgetContext, documentType), [item, state, budgetContext, documentType]);
  const fieldReviewBlockers = useMemo(() => getFieldReviewBlockers(item), [item]);
  const evidence = useMemo(() => getEvidenceChecklistEntries(item, state, context), [item, state, context]);
  const auditSections = useMemo(() => partitionAuditEvents(auditEvents), [auditEvents]);
  const pauseReason = useMemo(() => getWorkflowPauseReason(item), [item]);
  const resumeWorkflowEligible = useMemo(
    () => !pauseReason && shouldOfferResumeWorkflow(item, auditEvents, documentType),
    [auditEvents, documentType, item, pauseReason],
  );
  const stateNotice = resumeWorkflowEligible
    ? 'Field review is cleared. Resume workflow to continue the posting step.'
    : getWorkStateNotice(state, documentType, item);
  const basePrimaryAction = pauseReason ? null : getPrimaryActionConfig(state, actorRole, documentType);
  const primaryAction = resumeWorkflowEligible && ['preview_erp_post', 'retry_erp_post'].includes(basePrimaryAction?.id)
    ? { id: 'resume_workflow', label: 'Resume workflow' }
    : basePrimaryAction;
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
  const nonInvoiceActions = useMemo(() => (!isInvoiceDocument ? getNonInvoiceActions(item) : []), [isInvoiceDocument, item]);

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

  const [doResumeWorkflow, resumingWorkflow] = useAction(async () => {
    const confirmed = await openDialog({
      dialogMode: 'confirm',
      actionType: 'resume_workflow',
      title: 'Resume workflow',
      message: 'Review blockers are cleared. Clearledgr will continue the guarded posting step.',
      previewLines: [
        item?.vendor_name || item?.vendor || 'Unknown vendor',
        formatAmount(item?.amount, item?.currency || 'USD'),
        getDocumentReferenceText(documentType, item?.invoice_number || ''),
        isInvoiceDocument && item?.due_date ? `Due: ${item.due_date}` : null,
      ].filter(Boolean),
      confirmLabel: 'Resume workflow',
      cancelLabel: 'Cancel',
    });
    if (!confirmed) return;
    const result = await executeIntent(api, orgId, 'retry_recoverable_failures', {
      ap_item_id: item.id,
      email_id: item.thread_id || item.message_id || item.id,
      reason: 'Resume workflow after review cleared',
      source_channel: 'gmail_route',
      source_channel_id: 'gmail_route',
      source_message_ref: item.thread_id || item.message_id || item.id,
    });
    const status = String(result?.status || '').toLowerCase();
    const ok = ['posted', 'posted_to_erp', 'recovered', 'ready_to_post'].includes(status);
    toast(
      ok
        ? (status === 'posted' || status === 'posted_to_erp' ? 'Workflow resumed and invoice posted.' : 'Workflow resumed.')
        : (result?.reason || 'Could not resume workflow.'),
      ok ? 'success' : 'error',
    );
    await refresh();
  });

  const [doResolveFieldReview, resolvingFieldReview] = useAction(async (blocker, source) => {
    if (!item?.id || !blocker?.field) return;
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
        if (manualValue === null) return;
      }

      const result = await api(`/api/ap/items/${encodeURIComponent(item.id)}/field-review/resolve?organization_id=${encodeURIComponent(orgId)}`, {
        method: 'POST',
        body: JSON.stringify({
          field: blocker.field,
          source,
          manual_value: manualValue,
          auto_resume: true,
        }),
      });
      const ok = ['resolved', 'resolved_and_resumed'].includes(String(result?.status || '').toLowerCase());
      toast(
        ok
          ? (result?.auto_resumed
              ? `${blocker.field_label || 'Field'} updated and workflow resumed.`
              : `${blocker.field_label || 'Field'} updated.`)
          : (result?.reason || 'Could not resolve blocked field.'),
        ok ? 'success' : 'error',
      );
      await refresh();
    } catch (error) {
      toast(error?.message || 'Could not resolve blocked field.', 'error');
    } finally {
      setResolvingFieldKey('');
    }
  });

  const [doResolveNonInvoice, resolvingNonInvoice] = useAction(async (action) => {
    if (!item?.id || !action?.id) return;
    const pendingKey = `${item.id}:${action.id}`;
    setResolvingNonInvoiceKey(pendingKey);
    try {
      let relatedReference = null;
      let note = null;
      if (action.requiresReference) {
        relatedReference = await openDialog({
          actionType: 'generic',
          title: action.label,
          label: action.referenceLabel || 'Related reference',
          message: 'Capture the linked invoice or payment reference so this non-invoice finance document is auditable.',
          placeholder: action.referenceLabel || 'Reference',
          defaultValue: String(item?.invoice_number || '').trim(),
          confirmLabel: action.label,
          cancelLabel: 'Cancel',
          required: true,
          chips: [],
        });
        if (relatedReference == null) return;
      } else if (action.id === 'needs_followup') {
        note = await openDialog({
          actionType: 'generic',
          title: 'Needs follow-up',
          label: 'Why does this still need follow-up?',
          message: 'Record the next operator action before keeping this document open.',
          confirmLabel: 'Save follow-up',
          cancelLabel: 'Cancel',
          required: true,
          chips: [],
        });
        if (note == null) return;
      }

      const result = await api(`/api/ap/items/${encodeURIComponent(item.id)}/non-invoice/resolve?organization_id=${encodeURIComponent(orgId)}`, {
        method: 'POST',
        body: JSON.stringify({
          outcome: action.id,
          related_reference: relatedReference || undefined,
          note: note || undefined,
          close_record: action.id !== 'needs_followup',
        }),
      });
      const ok = String(result?.status || '').toLowerCase() === 'resolved';
      toast(
        ok
          ? `${documentLabel} updated.`
          : (result?.reason || 'Could not resolve non-invoice review.'),
        ok ? 'success' : 'error',
      );
      await refresh();
    } catch (error) {
      toast(error?.message || 'Could not resolve non-invoice review.', 'error');
    } finally {
      setResolvingNonInvoiceKey('');
    }
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
        getDocumentReferenceText(documentType, item?.invoice_number || ''),
        isInvoiceDocument && item?.due_date ? `Due ${item.due_date}` : null,
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
    navigateToVendorRecord(navigate, vendorName);
  }, [item, navigate]);

  const openRelatedRecord = useCallback((relatedItem) => {
    if (!relatedItem?.id) return;
    focusPipelineItem(pipelineScope, relatedItem, 'related_record');
    navigateToRecordDetail(navigate, relatedItem.id);
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
    return html`<div class="panel"><p class="muted">Loading record…</p></div>`;
  }

  if (!item) {
    return html`
      <div class="panel">
        <p class="muted">Record not found.</p>
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
  } else if (primaryAction?.id === 'resume_workflow') {
    primaryHandler = doResumeWorkflow;
    primaryPending = resumingWorkflow;
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
            ${[
              item?.invoice_number ? getDocumentReferenceText(documentType, item.invoice_number) : documentLabel,
              ...(isInvoiceDocument ? [`Due ${item.due_date || 'N/A'}`, item.po_number ? `PO ${item.po_number}` : 'No PO'] : []),
            ].join(' · ')}
          </div>
        </div>
        <${StatePill} state=${state} />
      </div>

      ${pauseReason && html`<div class="muted" style="margin-top:12px">${pauseReason}</div>`}
      ${!pauseReason && stateNotice && html`<div class="muted" style="margin-top:12px">${stateNotice}</div>`}

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px">
        ${primaryAction?.label && primaryHandler && html`
          <button onClick=${primaryHandler} disabled=${primaryPending}>
            ${primaryPending ? 'Processing…' : primaryAction.label}
          </button>
        `}
        ${!readOnlyMode && !isInvoiceDocument && nonInvoiceActions.map((action) => html`
          <button
            key=${action.id}
            class="alt"
            onClick=${() => doResolveNonInvoice(action)}
            disabled=${Boolean(resolvingNonInvoice && resolvingNonInvoiceKey === `${item.id}:${action.id}`)}
          >
            ${resolvingNonInvoice && resolvingNonInvoiceKey === `${item.id}:${action.id}` ? 'Processing…' : action.label}
          </button>
        `)}
        ${readOnlyMode && html`
          <div class="muted" style="width:100%">Read-only view. Queue actions are reserved for AP operators.</div>
        `}
        ${canRejectWorkItem(state, actorRole, documentType) && html`
          <button class="alt" onClick=${doReject} disabled=${rejecting}>Reject</button>
        `}
        ${canNudgeApprover(state, actorRole, documentType) && primaryAction?.id !== 'nudge_approver' && html`
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
          <h3 style="margin-top:0">Paused field review</h3>
          <${FieldReviewRows}
            blockers=${fieldReviewBlockers}
            pauseReason=${pauseReason}
            onResolve=${readOnlyMode ? null : doResolveFieldReview}
            resolvingField=${resolvingFieldReview ? resolvingFieldKey : ''}
          />
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Evidence checklist</h3>
          <div style="display:flex;flex-direction:column;gap:10px">
            ${evidence.map((entry) => html`
              <div key=${entry.key} style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding-bottom:8px;border-bottom:1px solid var(--border)">
                <div style="display:flex;flex-direction:column;gap:2px;min-width:0">
                  <span>${entry.label}</span>
                  ${entry.detail && html`<span class="muted" style="font-size:12px;line-height:1.4">${entry.detail}</span>`}
                </div>
                <span style="font-size:12px;font-weight:700;color:${entry.status === 'ok' ? 'var(--brand-muted)' : 'var(--ink-muted)'}">${entry.text}</span>
              </div>
            `)}
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">${documentLabel} details</h3>
          <div style="display:flex;flex-direction:column;gap:10px">
            ${detailRow(getDocumentReferenceLabel(documentType), item.invoice_number || '—')}
            ${detailRow('Document type', documentLabel)}
            ${isInvoiceDocument ? detailRow('Due date', item.due_date ? fmtDate(item.due_date) : '—') : null}
            ${isInvoiceDocument ? detailRow('PO number', item.po_number || 'None') : null}
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
            ${(item?.vendor_name || item?.vendor) && html`<button class="alt" onClick=${openVendorRecord} style="padding:8px 12px;font-size:12px">Open vendor record</button>`}
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
