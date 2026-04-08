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
  getAgentMemoryView,
  getFinanceEffectBlockers,
  getFinanceEffectNotice,
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
  canEscalateApproval,
  canReassignApproval,
  canNudgeApprover,
  canRejectWorkItem,
  getDefaultNextMoveLabel,
  hasErpPostingConnection,
  getOperatorOverrideCopy,
  getPrimaryActionConfig,
  getWorkStateNotice,
  needsEntityRouting,
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

function humanizePrepareInfoFailure(reason) {
  const token = String(reason || '').trim();
  if (!token) return '';
  const map = {
    waiting_for_sla_window: 'Follow-up already sent. Wait for the vendor response before nudging again.',
    followup_attempt_limit_reached: 'Clearledgr reached the vendor follow-up limit. This now needs manual escalation.',
    state_not_needs_info: 'This invoice is no longer waiting on vendor information.',
  };
  return map[token] || token.replace(/_/g, ' ');
}

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
  const financeEffectBlockers = getFinanceEffectBlockers(item);
  const financeEffectNotice = getFinanceEffectNotice(item);
  const approvalFollowup = item?.approval_followup && typeof item.approval_followup === 'object'
    ? item.approval_followup
    : {};
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
      fieldReviewBlockers.length ? 'Needs a quick field check' : 'Check extracted fields',
      pauseReason || `Current confidence is ${Math.round(confidence * 100)}%, so a field check is still required.`,
    );
  }
  if (item?.finance_effect_review_required) {
    push(
      'finance_effect',
      financeEffectBlockers[0]?.label || 'Credits or payments need review',
      financeEffectBlockers[0]?.detail || financeEffectNotice || 'Linked finance documents changed the payable or settlement balance.',
    );
  }
  if (needsEntityRouting(item, state, documentType)) {
    push(
      'entity',
      'Entity route needs review',
      item?.entity_route_reason || 'Choose the correct legal entity before approval routing can continue.',
    );
  }

  if (state === 'needs_approval') {
    const pendingAssignees = Array.isArray(approvalFollowup?.pending_assignees) ? approvalFollowup.pending_assignees : [];
    push(
      'approval',
      approvalFollowup?.escalation_due
        ? 'Approval needs escalation'
        : (approvalFollowup?.sla_breached ? 'Approval reminder is due' : 'Waiting on approver'),
      approvalFollowup?.escalation_due || approvalFollowup?.sla_breached || pendingAssignees.length
        ? getWorkStateNotice(state, documentType, item)
        : 'The approval request is still pending.',
    );
  }
  if (state === 'needs_info') {
    const workflowPause = getWorkflowPauseReason(item);
    const disputeStatus = item?.dispute_status;
    const infoLabel = disputeStatus === 'vendor_contacted'
      ? 'Waiting on vendor response'
      : disputeStatus === 'escalated'
      ? 'Dispute escalated — vendor unresponsive'
      : isInvoiceDocument ? 'Missing invoice details' : 'Missing document details';
    push(
      'info',
      infoLabel,
      getWorkStateNotice(state, documentType, item)
        || workflowPause
        || `Clearledgr still needs more information before this ${isInvoiceDocument ? 'invoice' : 'record'} can continue.`,
    );
  }
  if ((state === 'approved' || state === 'ready_to_post') && !exceptionReason && !hasErpPostingConnection(item)) {
    push('erp_setup', 'ERP is not connected', 'Connect QuickBooks, Xero, NetSuite, or SAP before Clearledgr can post this invoice.');
  }
  if (state === 'failed_post' && !exceptionReason) {
    push(
      'erp',
      hasErpPostingConnection(item) ? 'ERP posting failed' : 'ERP is not connected',
      hasErpPostingConnection(item)
        ? 'Retry the ERP post or review the connector result.'
        : 'Connect QuickBooks, Xero, NetSuite, or SAP before Clearledgr can post this invoice.',
    );
  }

  // Validation gate warnings
  const gateReasons = Array.isArray(item?.validation_reasons) ? item.validation_reasons : [];
  for (const reason of gateReasons) {
    const code = String(reason?.code || '').trim();
    if (code === 'period_locked') {
      push('period_locked', 'Period is locked', reason.message || 'Cannot post — accounting period is closed.');
    } else if (code === 'payment_terms_mismatch') {
      push('terms_mismatch', 'Payment terms changed', reason.message || 'Invoice terms differ from vendor profile.');
    } else if (code === 'bank_details_mismatch_from_invoice') {
      push('bank_change', 'Bank details changed', reason.message || 'Vendor bank details differ from previous invoices.');
    } else if (code === 'invalid_vendor_tax_id') {
      push('tax_id', 'Invalid vendor tax ID', reason.message || 'Vendor tax ID format is invalid.');
    } else if (code === 'invalid_gl_code') {
      push('gl_code', 'Invalid GL code', reason.message || 'GL code not found in chart of accounts.');
    }
  }
  if (blockers.length === 0 && state === 'received') {
    push(
      'received',
      isInvoiceDocument ? 'Ready for approval' : 'Needs finance review',
      isInvoiceDocument
        ? 'This invoice is ready to send for approval.'
        : getNonInvoiceWorkflowGuidance(documentType),
    );
  }
  if (blockers.length === 0 && state === 'validated') {
    push(
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
  return blockers.slice(0, 5);
}

function FieldReviewRows({ blockers, pauseReason, onResolve = null, resolvingField = '' }) {
  if ((!Array.isArray(blockers) || blockers.length === 0) && !pauseReason) {
    return html`<p class="muted">No field checks are waiting.</p>`;
  }

  return html`
    <div style="display:flex;flex-direction:column;gap:10px">
      ${pauseReason && html`
        <div style="padding:10px 12px;border:1px solid #fcd34d;border-radius:var(--radius-sm);background:#FEFCE8;color:#78350f;font-size:13px;line-height:1.45">
          ${pauseReason}
        </div>
      `}
      ${(blockers || []).map((blocker) => html`
        <div key=${`${blocker.field || 'field'}-${blocker.kind || 'review'}`} style="padding:12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg)">
          <div class="review-block-layout">
            <div class="review-block-main">
              <div style="font-weight:700;font-size:13px;margin-bottom:10px">
                ${blocker.kind === 'confidence'
                  ? `Confirm ${(blocker.field_label || 'field').toLowerCase()}`
                  : `Choose the correct ${(blocker.field_label || 'field').toLowerCase()}`}
              </div>
              <div class="review-block-facts">
                ${blocker.kind === 'confidence' && html`
                  <>
                    <span class="review-block-fact-label">Clearledgr read</span>
                    <span class="review-block-fact-value">${blocker.current_value_display || 'Not found'}</span>
                  </>
                `}
                ${blocker.kind === 'confidence' && blocker.current_source_label && html`
                  <>
                    <span class="review-block-fact-label">Read from</span>
                    <span class="review-block-fact-value">${blocker.current_source_label}</span>
                  </>
                `}
                ${blocker.email_value !== null && blocker.email_value !== undefined && html`
                  <>
                    <span class="review-block-fact-label">Email says</span>
                    <span class="review-block-fact-value">${blocker.email_value_display}</span>
                  </>
                `}
                ${blocker.attachment_value !== null && blocker.attachment_value !== undefined && html`
                  <>
                    <span class="review-block-fact-label">Attachment says</span>
                    <span class="review-block-fact-value">${blocker.attachment_value_display}</span>
                  </>
                `}
                ${blocker.kind === 'source_conflict' && html`
                  <>
                    <span class="review-block-fact-label">Current choice</span>
                    <span class="review-block-fact-value">
                      ${blocker.winning_source_label || 'Needs review'}
                      ${blocker.winning_value_display ? ` (${blocker.winning_value_display})` : ''}
                    </span>
                  </>
                `}
              </div>
            </div>
            <div class="review-block-side">
              <div class="review-block-heading">Why it stopped</div>
              <div class="review-block-copy">${blocker.winner_reason || blocker.reason_label || blocker.paused_reason}</div>
              ${blocker.auto_check_note && html`<div class="review-block-note">${blocker.auto_check_note}</div>`}
              ${typeof onResolve === 'function' && html`
                <div class="review-block-actions">
                  ${blocker.email_value !== null && blocker.email_value !== undefined && html`
                    <button
                      class="btn-secondary btn-sm"
                      onClick=${() => onResolve(blocker, 'email')}
                      disabled=${Boolean(resolvingField === `${blocker.field}:email`)}
                    >
                      ${resolvingField === `${blocker.field}:email` ? 'Saving…' : 'Use email'}
                    </button>
                  `}
                  ${blocker.attachment_value !== null && blocker.attachment_value !== undefined && html`
                    <button
                      class="btn-secondary btn-sm"
                      onClick=${() => onResolve(blocker, 'attachment')}
                      disabled=${Boolean(resolvingField === `${blocker.field}:attachment`)}
                    >
                      ${resolvingField === `${blocker.field}:attachment` ? 'Saving…' : 'Use attachment'}
                    </button>
                  `}
                  <button
                    class="btn-secondary btn-sm"
                    onClick=${() => onResolve(blocker, 'manual')}
                    disabled=${Boolean(resolvingField === `${blocker.field}:manual`)}
                  >
                    ${resolvingField === `${blocker.field}:manual` ? 'Saving…' : 'Enter manually'}
                  </button>
                </div>
              `}
            </div>
          </div>
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
          <span>${row.evidenceDetail || 'Saved on the record.'}</span>
        </div>
      `}
      ${row.actionHint && !row.isBackground && html`<div class="cl-audit-hint">Next: ${row.actionHint}</div>`}
    </div>
  `;
}

function RelatedRecordRow({ label, item, onOpen }) {
  if (!item?.id) return null;
  return html`
    <div class="secondary-card">
      <div class="secondary-card-head">
        <div class="secondary-card-copy">
          <span class="secondary-card-title">${item.vendor_name || 'Unknown vendor'} · ${item.invoice_number || 'No invoice #'}</span>
          <div class="secondary-card-meta">${label} · ${formatAmount(item.amount, item.currency)} · ${String(item.state || 'received').replace(/_/g, ' ')}</div>
        </div>
        <div class="secondary-inline-actions">
          <button class="btn-secondary btn-sm" onClick=${onOpen}>Open</button>
        </div>
      </div>
    </div>
  `;
}

function SourceGroupRow({ group }) {
  if (!group) return null;
  const sourceType = String(group.source_type || '').trim().toLowerCase();
  const sourceLabels = {
    gmail_message: {
      title: 'Gmail message',
      meta: 'The specific email linked to this record.',
    },
    gmail_thread: {
      title: 'Gmail thread',
      meta: 'The wider email thread linked to this record.',
    },
    compose_draft: {
      title: 'Draft email',
      meta: 'Draft reply linked to this record.',
    },
    attachment: {
      title: 'Attachment',
      meta: 'File evidence linked to this record.',
    },
    portal: {
      title: 'Vendor portal',
      meta: 'Portal evidence linked to this record.',
    },
    procurement: {
      title: 'Procurement system',
      meta: 'Procurement evidence linked to this record.',
    },
    dms: {
      title: 'Document system',
      meta: 'Document-system evidence linked to this record.',
    },
    spreadsheet: {
      title: 'Spreadsheet',
      meta: 'Spreadsheet evidence linked to this record.',
    },
    sheets: {
      title: 'Spreadsheet',
      meta: 'Spreadsheet evidence linked to this record.',
    },
    bank: {
      title: 'Bank record',
      meta: 'Bank evidence linked to this record.',
    },
  };
  const sourceLabel = sourceLabels[sourceType] || {
    title: String(group.source_type || 'linked evidence').replace(/_/g, ' '),
    meta: 'Evidence linked to this record.',
  };
  const itemCount = Number(group.count || 0);
  return html`
    <div class="secondary-card">
      <div class="secondary-card-head">
        <div class="secondary-card-copy">
          <span class="secondary-card-title">${sourceLabel.title}</span>
          <div class="secondary-card-meta">${sourceLabel.meta}</div>
        </div>
        <div class="secondary-card-stat">
          <strong>${itemCount.toLocaleString()}</strong>
          <span>${itemCount === 1 ? 'item' : 'items'}</span>
        </div>
      </div>
      <div class="secondary-card-list" style="margin-top:10px">
      ${(group.items || []).slice(0, 2).map((entry, index) => html`
        <div key=${`${group.source_type}-${entry?.source_ref || index}`} class="secondary-row">
          <div class="secondary-row-copy">
            <strong>${entry?.subject || entry?.source_ref || 'Linked evidence'}</strong>
            <p>${entry?.sender || 'Linked source'}${entry?.detected_at ? ` · ${fmtDateTime(entry.detected_at)}` : ''}</p>
          </div>
        </div>
      `)}
      </div>
    </div>
  `;
}

function TemplateActionRow({ template, onDraft }) {
  return html`
    <div class="secondary-card">
      <div class="secondary-card-head">
        <div class="secondary-card-copy">
          <span class="secondary-card-title">${template.name}</span>
          <div class="secondary-card-meta">${template.description || 'Reusable reply template.'}</div>
        </div>
        <div class="secondary-inline-actions">
          <button class="btn-secondary btn-sm" onClick=${onDraft}>Draft</button>
        </div>
      </div>
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
        api(`/api/ap/items/${encodeURIComponent(itemId)}?organization_id=${encodeURIComponent(orgId)}`, { silent: true }).catch(() => null),
        api(`/api/ap/items/${encodeURIComponent(itemId)}/audit?organization_id=${encodeURIComponent(orgId)}`, { silent: true }).catch(() => ({ events: [] })),
        api(`/api/ap/items/${encodeURIComponent(itemId)}/context?organization_id=${encodeURIComponent(orgId)}`, { silent: true }).catch(() => null),
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
  const approvalFollowup = item?.approval_followup && typeof item.approval_followup === 'object'
    ? item.approval_followup
    : {};
  const entityRouting = item?.entity_routing && typeof item.entity_routing === 'object'
    ? item.entity_routing
    : {};
  const entityCandidates = Array.isArray(item?.entity_candidates)
    ? item.entity_candidates
    : (Array.isArray(entityRouting?.candidates) ? entityRouting.candidates : []);
  const entityNeedsReview = needsEntityRouting(item, state, documentType);
  const stateNotice = resumeWorkflowEligible
    ? 'Field review is cleared. Resume workflow to continue the posting step.'
    : getWorkStateNotice(state, documentType, item);
  const basePrimaryAction = (pauseReason || item?.finance_effect_review_required)
    ? null
    : getPrimaryActionConfig(state, actorRole, documentType, item);
  const primaryAction = resumeWorkflowEligible && ['preview_erp_post', 'retry_erp_post'].includes(basePrimaryAction?.id)
    ? { id: 'resume_workflow', label: 'Resume workflow' }
    : basePrimaryAction;
  const canOpenEmail = Boolean(item && (getSourceThreadId(item) || getSourceMessageId(item) || item.subject));
  const smartRejectDefault = item?.exception_code ? getExceptionReason(item.exception_code) : '';
  const relatedRecords = context?.related_records || {};
  const linkedRecord = item?.linked_record && typeof item.linked_record === 'object' ? item.linked_record : null;
  const linkedFinanceDocuments = Array.isArray(item?.linked_finance_documents) ? item.linked_finance_documents.slice(0, 4) : [];
  const financeEffectSummary = item?.finance_effect_summary && typeof item.finance_effect_summary === 'object'
    ? item.finance_effect_summary
    : {};
  const financeEffectBlockers = getFinanceEffectBlockers(item);
  const financeEffectNotice = getFinanceEffectNotice(item);
  const reconciliationReference = item?.reconciliation_reference && typeof item.reconciliation_reference === 'object'
    ? item.reconciliation_reference
    : {};
  const agentView = useMemo(() => getAgentMemoryView(item), [item]);
  const hasAccountingLinkage = Boolean(
    linkedRecord
    || linkedFinanceDocuments.length
    || Object.keys(financeEffectSummary).length
    || reconciliationReference?.session_id
    || item?.non_invoice_accounting_treatment
    || item?.non_invoice_downstream_queue
  );
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
  const pendingApproverSummary = Array.isArray(approvalFollowup?.pending_assignees) && approvalFollowup.pending_assignees.length
    ? approvalFollowup.pending_assignees.slice(0, 3).join(', ')
    : 'Not recorded';
  const contextSummary = String(context?.reasoning_summary || '').trim();
  const contextNextStep = String(context?.next_action || '').trim();
  const contextRisks = String(context?.reasoning_risks || '').trim();
  const showContextPanel = Boolean(
    (contextSummary && contextSummary !== agentView.beliefReason)
    || (contextNextStep && contextNextStep !== agentView.nextActionLabel)
    || contextRisks
  );
  const heroMeta = [
    item?.invoice_number ? getDocumentReferenceText(documentType, item.invoice_number) : documentLabel,
    ...(isInvoiceDocument ? [item?.due_date ? `Due ${item.due_date}` : 'Due date pending', item?.po_number ? `PO ${item.po_number}` : 'No PO linked'] : []),
    item?.sender || null,
  ].filter(Boolean);
  const heroNote = pauseReason || stateNotice;

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
    const status = String(result?.status || '').toLowerCase();
    const ok = ['prepared', 'queued'].includes(status);
    const informational = status === 'waiting_sla';
    toast(
      ok
        ? 'Info request draft prepared.'
        : informational
        ? (humanizePrepareInfoFailure(result?.reason) || 'Follow-up already sent.')
        : (humanizePrepareInfoFailure(result?.reason) || 'Could not prepare info request.'),
      ok ? 'success' : informational ? 'info' : 'error',
    );
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

  const [doEscalateApproval, escalatingApproval] = useAction(async () => {
    const result = await executeIntent(api, orgId, 'escalate_approval', {
      ap_item_id: item.id,
      email_id: item.thread_id || item.message_id || item.id,
      source_channel: 'gmail_route',
      source_channel_id: 'gmail_route',
      source_message_ref: item.thread_id || item.message_id || item.id,
    });
    const ok = String(result?.status || '').toLowerCase() === 'escalated';
    toast(ok ? 'Approval escalated.' : (result?.reason || 'Could not escalate approval.'), ok ? 'success' : 'error');
    await refresh();
  });

  const [doReassignApproval, reassigningApproval] = useAction(async () => {
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
    const result = await executeIntent(api, orgId, 'reassign_approval', {
      ap_item_id: item.id,
      email_id: item.thread_id || item.message_id || item.id,
      assignee,
      source_channel: 'gmail_route',
      source_channel_id: 'gmail_route',
      source_message_ref: item.thread_id || item.message_id || item.id,
    });
    const ok = String(result?.status || '').toLowerCase() === 'reassigned';
    toast(ok ? `Approval reassigned to ${assignee}.` : (result?.reason || 'Could not reassign approval.'), ok ? 'success' : 'error');
    await refresh();
  });

  const [doResolveEntityRoute, resolvingEntityRoute] = useAction(async () => {
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
    const result = await api(`/api/ap/items/${encodeURIComponent(item.id)}/entity-route/resolve?organization_id=${encodeURIComponent(orgId)}`, {
      method: 'POST',
      body: JSON.stringify({
        selection: selection || candidate?.entity_code || candidate?.entity_name,
        entity_id: candidate?.entity_id,
        entity_code: candidate?.entity_code,
        entity_name: candidate?.entity_name,
      }),
    });
    const ok = String(result?.status || '').toLowerCase() === 'resolved';
    toast(ok ? 'Entity route resolved.' : (result?.reason || 'Could not resolve entity route.'), ok ? 'success' : 'error');
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
        formatAmount(item?.amount, item?.currency),
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
    if (!hasErpPostingConnection(item)) {
      toast('Connect an ERP before posting this invoice.', 'error');
      return;
    }
    const confirmed = await openDialog({
      dialogMode: 'confirm',
      actionType: 'preview_erp_post',
      title: 'Preview ERP post',
      message: 'Review this invoice before posting it to the ERP.',
      previewLines: [
        item?.vendor_name || item?.vendor || 'Unknown vendor',
        formatAmount(item?.amount, item?.currency),
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
    navigate('clearledgr/invoices');
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
    const issueSummary = getIssueSummary(item) || agentView.beliefReason || context?.summary?.text || 'additional information is required';
    const prefill = buildReplyTemplatePrefill(template, item, {
      issue_summary: issueSummary,
      next_action: agentView.nextActionLabel || item?.next_action || context?.summary?.text || 'Review in Clearledgr',
    });
    prefill.recordContext = {
      apItemId: item.id,
      vendorName: item.vendor_name || item.vendor || item.sender || '',
      invoiceNumber: item.invoice_number || '',
      amountLabel: formatAmount(item.amount, item.currency),
    };
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
        <button class="btn-secondary" onClick=${() => navigate('clearledgr/invoices')}>Back to invoices</button>
      </div>
    `;
  }

  let primaryHandler = null;
  let primaryPending = false;
  if (primaryAction?.id === 'request_approval') {
    primaryHandler = doRequestApproval;
    primaryPending = requestingApproval;
  } else if (primaryAction?.id === 'resolve_entity_route') {
    primaryHandler = doResolveEntityRoute;
    primaryPending = resolvingEntityRoute;
  } else if (primaryAction?.id === 'prepare_info_request') {
    primaryHandler = doPrepareInfo;
    primaryPending = preparingInfo;
  } else if (primaryAction?.id === 'escalate_approval') {
    primaryHandler = doEscalateApproval;
    primaryPending = escalatingApproval;
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
  const bestNextStepLabel = agentView.nextActionLabel
    || primaryAction?.label
    || getDefaultNextMoveLabel(state, item, actorRole, documentType);
  const operatorOverrideCopy = getOperatorOverrideCopy(state, item, documentType);
  const secondaryActionCount = [
    canRejectWorkItem(state, actorRole, documentType),
    canReassignApproval(item, state, actorRole, documentType),
    canEscalateApproval(item, state, actorRole, documentType) && primaryAction?.id !== 'escalate_approval',
    entityNeedsReview && primaryAction?.id !== 'resolve_entity_route',
    canNudgeApprover(state, actorRole, documentType) && primaryAction?.id !== 'nudge_approver',
  ].filter(Boolean).length;

  return html`
    <div class="record-detail-toolbar">
      <div class="toolbar-actions">
        <button class="btn-secondary btn-sm" onClick=${openInPipeline}>Back to invoices</button>
        ${canOpenEmail && html`<button class="btn-ghost btn-sm" onClick=${openEmail}>Open email</button>`}
        ${(item?.vendor_name || item?.vendor) && html`<button class="btn-ghost btn-sm" onClick=${openVendorRecord}>Open vendor record</button>`}
      </div>
    </div>

    <div class="panel record-detail-hero">
      <div class="panel-head">
        <div>
          <div class="record-detail-eyebrow">
            <${StatePill} state=${state} />
            <span class="secondary-chip">${documentLabel}</span>
            ${entityNeedsReview && html`<span class="secondary-chip">Entity review</span>`}
            ${item?.finance_effect_review_required && html`<span class="secondary-chip">Finance review</span>`}
          </div>
          <h3 style="margin:0 0 6px">${item.vendor_name || item.vendor || 'Unknown vendor'}</h3>
          <div class="record-detail-amount">${formatAmount(item.amount, item.currency)}</div>
          <div class="record-detail-meta">
            ${heroMeta.join(' · ')}
          </div>
        </div>
      </div>

      ${heroNote && html`<div class="secondary-note record-detail-hero-note">${heroNote}</div>`}

      <div class="secondary-stat-grid record-detail-summary">
        <div class="secondary-stat-card">
          <strong>Best next step</strong>
          <span>${bestNextStepLabel}</span>
        </div>
        <div class="secondary-stat-card">
          <strong>Confidence</strong>
          <span>${item.confidence ? `${Math.round(Number(item.confidence) * 100)}%` : 'Awaiting confidence score'}</span>
        </div>
        <div class="secondary-stat-card">
          <strong>${state === 'needs_approval' ? 'Approval owner' : 'Step owner'}</strong>
          <span>${state === 'needs_approval' ? pendingApproverSummary : (agentView.nextActionOwnerLabel || 'Operator')}</span>
        </div>
        <div class="secondary-stat-card">
          <strong>Last update</strong>
          <span>${fmtDateTime(item.updated_at || item.created_at)}</span>
        </div>
      </div>

      <div class="toolbar-actions record-detail-hero-actions">
        ${primaryAction?.label && primaryHandler && html`
          <button class="btn-primary" onClick=${primaryHandler} disabled=${primaryPending}>
            ${primaryPending ? 'Processing…' : primaryAction.label}
          </button>
        `}
        ${!readOnlyMode && !isInvoiceDocument && nonInvoiceActions.map((action) => html`
          <button
            key=${action.id}
            class="btn-secondary btn-sm"
            onClick=${() => doResolveNonInvoice(action)}
            disabled=${Boolean(resolvingNonInvoice && resolvingNonInvoiceKey === `${item.id}:${action.id}`)}
          >
            ${resolvingNonInvoice && resolvingNonInvoiceKey === `${item.id}:${action.id}` ? 'Processing…' : action.label}
          </button>
        `)}
      </div>
      ${secondaryActionCount > 0 && html`
        <details class="route-operator-overrides">
          <summary class="route-operator-overrides-summary">
            <span>${operatorOverrideCopy.title}</span>
            <span class="route-operator-overrides-count">${secondaryActionCount}</span>
          </summary>
          <div class="route-operator-overrides-copy">${operatorOverrideCopy.detail}</div>
          <div class="toolbar-actions route-operator-overrides-actions">
            ${canRejectWorkItem(state, actorRole, documentType) && html`
              <button class="btn-danger btn-sm" onClick=${doReject} disabled=${rejecting}>Reject</button>
            `}
            ${canReassignApproval(item, state, actorRole, documentType) && html`
              <button class="btn-secondary btn-sm" onClick=${doReassignApproval} disabled=${reassigningApproval}>
                ${reassigningApproval ? 'Reassigning…' : 'Reassign approver'}
              </button>
            `}
            ${canEscalateApproval(item, state, actorRole, documentType) && primaryAction?.id !== 'escalate_approval' && html`
              <button class="btn-secondary btn-sm" onClick=${doEscalateApproval} disabled=${escalatingApproval}>
                ${escalatingApproval ? 'Escalating…' : 'Escalate approval'}
              </button>
            `}
            ${entityNeedsReview && primaryAction?.id !== 'resolve_entity_route' && html`
              <button class="btn-secondary btn-sm" onClick=${doResolveEntityRoute} disabled=${resolvingEntityRoute}>
                ${resolvingEntityRoute ? 'Resolving…' : 'Resolve entity'}
              </button>
            `}
            ${canNudgeApprover(state, actorRole, documentType) && primaryAction?.id !== 'nudge_approver' && html`
              <button class="btn-secondary btn-sm" onClick=${doNudge} disabled=${nudging}>Nudge approver</button>
            `}
          </div>
        </details>
      `}
      ${readOnlyMode && html`
        <div class="secondary-note record-detail-hero-note">
          Read-only view. You can review this record here, but only operators can take action.
        </div>
      `}
    </div>

    <div class="record-detail-shell">
      <div class="record-detail-main">
        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Blocked because</h3>
              <p class="muted" style="margin:4px 0 0">What is stopping the record from moving cleanly to the next step.</p>
            </div>
          </div>
          ${blockers.length
            ? html`<div class="secondary-card-list">
                ${blockers.map((blocker) => html`
                  <div key=${blocker.key} class="secondary-card">
                    <div class="secondary-card-copy">
                      <span class="secondary-card-title">${blocker.label}</span>
                      ${blocker.detail && html`<div class="secondary-card-meta">${blocker.detail}</div>`}
                    </div>
                  </div>
                `)}
              </div>`
            : html`<p class="secondary-empty">No active blockers.</p>`}
        </div>

        ${Array.isArray(item?.line_items) && item.line_items.length > 0 && html`
          <div class="panel">
            <div class="panel-head compact">
              <div>
                <h3 style="margin:0">Line items</h3>
                <p class="muted" style="margin:4px 0 0">${item.line_items.length} extracted lines with invoice roll-up totals.</p>
              </div>
            </div>
            <div class="secondary-card-list">
              ${item.line_items.slice(0, 15).map((li, i) => html`
                <div key=${i} class="secondary-row">
                  <div class="secondary-row-copy">
                    <strong>${li.description || `Line ${i + 1}`}</strong>
                    <p>
                      ${[
                        li.gl_code ? `GL ${li.gl_code}` : null,
                        li.quantity ? `Qty ${li.quantity}` : null,
                      ].filter(Boolean).join(' · ') || 'No additional line details'}
                    </p>
                  </div>
                  <div class="secondary-inline-actions">
                    <span class="secondary-chip">${formatAmount(li.amount || 0, item.currency)}</span>
                  </div>
                </div>
              `)}
            </div>
            ${(item.tax_amount || item.discount_amount) && html`
              <div class="detail-row-list" style="margin-top:12px">
                ${item.tax_amount ? detailRow('Tax', formatAmount(item.tax_amount, item.currency)) : null}
                ${item.discount_amount ? detailRow(`Discount${item.discount_terms ? ` (${item.discount_terms})` : ''}`, `-${formatAmount(item.discount_amount, item.currency)}`) : null}
              </div>
            `}
          </div>
        `}

        ${item?.payment_status && item.payment_status !== 'none' && html`
          <div class="panel">
            <div class="panel-head compact">
              <div>
                <h3 style="margin:0">Payment</h3>
                <p class="muted" style="margin:4px 0 0">Settlement details currently tracked on the record.</p>
              </div>
            </div>
            <div class="detail-row-list">
              ${detailRow('Status', (item.payment_status || '').replace(/_/g, ' '))}
              ${item.payment_reference && detailRow('Reference', item.payment_reference)}
              ${item.payment_method && detailRow('Method', item.payment_method)}
            </div>
          </div>
        `}

        ${(state === 'needs_approval' || entityNeedsReview) && html`
          <div class="panel">
            <div class="panel-head compact">
              <div>
                <h3 style="margin:0">Follow-up and routing</h3>
                <p class="muted" style="margin:4px 0 0">Who owns the approval path and whether entity routing still needs a decision.</p>
              </div>
            </div>
            <div class="detail-row-list">
              ${state === 'needs_approval' && html`
                ${detailRow('Approval wait', approvalFollowup?.wait_minutes ? `${approvalFollowup.wait_minutes} minutes` : '—')}
                ${detailRow('Pending approvers', pendingApproverSummary)}
                ${detailRow(
                  'Approval SLA',
                  approvalFollowup?.escalation_due
                    ? 'Escalation due'
                    : (approvalFollowup?.sla_breached ? 'Reminder due' : 'Within SLA'),
                )}
                ${detailRow('Escalations', String(approvalFollowup?.escalation_count || 0))}
                ${detailRow('Reassignments', String(approvalFollowup?.reassignment_count || 0))}
              `}
              ${isInvoiceDocument && html`
                ${detailRow('Entity route', entityNeedsReview ? 'Needs review' : (item?.entity_code || item?.entity_name || 'Not set'))}
                ${entityCandidates.length
                  ? detailRow('Entity candidates', entityCandidates.slice(0, 4).map((candidate) => candidate?.label || candidate?.entity_name || candidate?.entity_code).filter(Boolean).join(', '))
                  : null}
              `}
            </div>
          </div>
        `}

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Check these fields</h3>
              <p class="muted" style="margin:4px 0 0">Resolve extraction conflicts before the workflow keeps moving.</p>
            </div>
          </div>
          <${FieldReviewRows}
            blockers=${fieldReviewBlockers}
            pauseReason=${pauseReason}
            onResolve=${readOnlyMode ? null : doResolveFieldReview}
            resolvingField=${resolvingFieldReview ? resolvingFieldKey : ''}
          />
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Evidence checklist</h3>
              <p class="muted" style="margin:4px 0 0">Everything Clearledgr needs to approve, explain, and post this record with confidence.</p>
            </div>
          </div>
          <div class="secondary-card-list">
            ${evidence.map((entry) => html`
              <div key=${entry.key} class="secondary-row">
                <div class="secondary-row-copy">
                  <strong>${entry.label}</strong>
                  ${entry.detail && html`<p>${entry.detail}</p>`}
                </div>
                <div class="secondary-inline-actions">
                  <span
                    class="secondary-chip"
                    style=${entry.status === 'ok'
                      ? 'border-color:#A7F3D0;background:#ECFDF5;color:#059669'
                      : 'border-color:#E2E8F0;background:#F8FAFC;color:#64748B'}
                  >
                    ${entry.text}
                  </span>
                </div>
              </div>
            `)}
          </div>
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">${documentLabel} details</h3>
              <p class="muted" style="margin:4px 0 0">Core source fields and identifiers captured on this AP record.</p>
            </div>
          </div>
          <div class="detail-row-list">
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

        ${hasAccountingLinkage && html`
          <div class="panel">
            <div class="panel-head compact">
              <div>
                <h3 style="margin:0">Credits and payments</h3>
                <p class="muted" style="margin:4px 0 0">How credits, refunds, reconciliation, and linked finance documents change the payable balance.</p>
              </div>
            </div>
            <div class="detail-detail-stack">
              ${financeEffectNotice
                ? html`<div class="secondary-note">${financeEffectNotice}</div>`
                : null}
              ${Object.keys(financeEffectSummary).length
                ? html`
                    <div class="detail-row-list">
                    ${detailRow('Original amount', formatAmount(financeEffectSummary.original_amount, financeEffectSummary.currency || item.currency))}
                    ${detailRow('Credits applied', formatAmount(financeEffectSummary.applied_credit_total, financeEffectSummary.currency || item.currency))}
                    ${detailRow('Cash out evidence', formatAmount(financeEffectSummary.gross_cash_out_total, financeEffectSummary.currency || item.currency))}
                    ${detailRow('Refunds linked', formatAmount(financeEffectSummary.refund_total, financeEffectSummary.currency || item.currency))}
                    ${detailRow('Net cash applied', formatAmount(financeEffectSummary.net_cash_applied_total, financeEffectSummary.currency || item.currency))}
                    ${detailRow('Remaining balance', formatAmount(financeEffectSummary.remaining_balance_amount, financeEffectSummary.currency || item.currency))}
                    ${detailRow('Credit state', String(financeEffectSummary.credit_application_state || 'none').replace(/_/g, ' '))}
                    ${detailRow('Settlement state', String(financeEffectSummary.settlement_state || 'open').replace(/_/g, ' '))}
                    </div>
                  `
                : null}
              ${financeEffectBlockers.length > 0
                ? html`
                    <div class="secondary-card-list">
                      ${financeEffectBlockers.map((blocker) => html`
                        <div key=${blocker.code} class="secondary-card">
                          <div class="secondary-card-copy">
                            <span class="secondary-card-title">${blocker.label}</span>
                            ${blocker.detail && html`<div class="secondary-card-meta">${blocker.detail}</div>`}
                          </div>
                        </div>
                      `)}
                    </div>
                  `
                : null}
              ${linkedRecord
                ? html`<${RelatedRecordRow}
                    label="Linked record"
                    item=${linkedRecord}
                    onOpen=${() => openRelatedRecord(linkedRecord)}
                  />`
                : null}
              ${item?.non_invoice_accounting_treatment
                ? detailRow('Treatment', String(item.non_invoice_accounting_treatment).replace(/_/g, ' '))
                : null}
              ${item?.non_invoice_downstream_queue
                ? detailRow('Downstream queue', String(item.non_invoice_downstream_queue).replace(/_/g, ' '))
                : null}
              ${reconciliationReference?.session_id
                ? detailRow(
                    'Reconciliation queue',
                    `Session ${reconciliationReference.session_id}${reconciliationReference.item_id ? ` · Item ${reconciliationReference.item_id}` : ''}`
                  )
                : null}
              ${linkedFinanceDocuments.length > 0 && html`
                <div class="secondary-card-list">
              ${linkedFinanceDocuments.map((linkedDocument) => html`
                <${RelatedRecordRow}
                  key=${linkedDocument.source_ap_item_id}
                  label=${`${getDocumentTypeLabel(linkedDocument.document_type || 'other')} linked`}
                  item=${{
                    id: linkedDocument.source_ap_item_id,
                    vendor_name: linkedDocument.vendor_name,
                    invoice_number: linkedDocument.invoice_number,
                    amount: linkedDocument.amount,
                    currency: linkedDocument.currency,
                    state: linkedDocument.outcome,
                    updated_at: linkedDocument.linked_at,
                  }}
                  onOpen=${() => openRelatedRecord({ id: linkedDocument.source_ap_item_id })}
                />`
              )}
                </div>
              `}
            </div>
          </div>
        `}

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Linked records</h3>
              <p class="muted" style="margin:0">Related invoices and superseded records linked to this AP item.</p>
            </div>
            ${(item?.vendor_name || item?.vendor) && html`<button class="btn-secondary btn-sm" onClick=${openVendorRecord}>Open vendor record</button>`}
          </div>
          <div class="secondary-card-list">
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
              : html`<p class="secondary-empty" style="margin:0">No linked records yet.</p>`}
          </div>
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Record history</h3>
              <p class="muted" style="margin:4px 0 0">The key decisions, retries, and background events on this record.</p>
            </div>
          </div>
          ${auditSections.rows.length === 0
            ? html`<p class="secondary-empty">No audit events yet.</p>`
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
      </div>

      <div class="record-detail-side">
        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">What Clearledgr sees</h3>
              <p class="muted" style="margin:4px 0 0;line-height:1.6">Current status, what happens next, and what still needs attention.</p>
            </div>
          </div>
          <div class="detail-row-list">
            ${detailRow('Current status', agentView.stateSummaryLabel || agentView.currentStateLabel || agentView.statusLabel || 'Received')}
            ${detailRow('Next step', agentView.nextActionLabel || 'Review this record')}
            ${detailRow('Waiting on', agentView.nextActionActorLabel || agentView.nextActionOwnerLabel || 'Clearledgr')}
          </div>
          ${agentView.beliefReason && html`
            <div class="secondary-callout" style="margin-top:12px">
              <strong style="display:block;margin-bottom:6px;color:var(--ink)">Why this record is waiting</strong>
              ${agentView.beliefReason}
            </div>
          `}
          ${agentView.highlights.length > 0 && html`
            <div class="secondary-card-list" style="margin-top:12px">
              ${agentView.highlights.map((entry) => html`
                <div key=${entry} class="secondary-card">
                  <div class="secondary-card-copy">
                    <span class="secondary-card-title">Still needs attention</span>
                    <div class="secondary-card-meta">${entry}</div>
                  </div>
                </div>
              `)}
            </div>
          `}
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Ready-to-send replies</h3>
              <p class="muted" style="margin:4px 0 0">Draft vendor or approver messages from this record without leaving Gmail.</p>
            </div>
          </div>
          ${quickReplyTemplates.length === 0
            ? html`<p class="secondary-empty" style="margin:0">No reply templates are available yet.</p>`
            : html`<div class="secondary-card-list">
                ${quickReplyTemplates.map((template) => html`
                  <${TemplateActionRow}
                    key=${template.id}
                    template=${template}
                    onDraft=${() => draftReply(template.id)}
                  />
                `)}
              </div>`}
          <div class="toolbar-actions" style="margin-top:12px">
            <button class="btn-secondary btn-sm" onClick=${() => navigate('clearledgr/templates')}>Manage templates</button>
            ${draftingReply && html`<span class="muted" style="font-size:12px;align-self:center">Opening compose…</span>`}
          </div>
        </div>

        ${showContextPanel && html`
          <div class="panel">
            <div class="panel-head compact">
              <div>
                <h3 style="margin:0">Decision context</h3>
                <p class="muted" style="margin:4px 0 0">Why Clearledgr paused here and what it will do once this is resolved.</p>
              </div>
            </div>
            ${contextSummary && contextSummary !== agentView.beliefReason && html`<div class="secondary-callout">${contextSummary}</div>`}
            ${contextRisks && html`<div class="secondary-callout warning" style="margin-top:10px">${contextRisks}</div>`}
            ${contextNextStep && contextNextStep !== agentView.nextActionLabel && html`
              <div class="detail-row-list" style="margin-top:10px">
                ${detailRow('After that', contextNextStep)}
              </div>
            `}
          </div>
        `}

        ${context && html`
          <div class="panel">
            <div class="panel-head compact">
              <div>
                <h3 style="margin:0">Evidence attached</h3>
                <p class="muted" style="margin:4px 0 0">Messages, files, and linked records Clearledgr is using for this record.</p>
              </div>
            </div>
            ${sourceGroups.length === 0
              ? html`<p class="secondary-empty" style="margin:0">No linked evidence sources yet.</p>`
              : html`<div class="secondary-card-list">
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
    <div class="detail-row">
      <span class="detail-row-label">${label}</span>
      <span class="detail-row-value">${value}</span>
    </div>
  `;
}
