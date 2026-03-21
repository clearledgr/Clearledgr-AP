/**
 * Review Page — exception and blocker workbench for AP operators.
 * Keeps blocked finance work in one place without turning Gmail into a generic dashboard.
 */
import { h } from 'preact';
import { useCallback, useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import ActionDialog, { useActionDialog } from '../../components/ActionDialog.js';
import { navigateToRecordDetail } from '../../utils/record-route.js';
import {
  formatAmount,
  getExceptionReason,
  getFieldReviewBlockers,
  getIssueSummary,
  getWorkflowPauseReason,
  openSourceEmail,
} from '../../utils/formatters.js';
import {
  getDocumentReferenceLabel,
  getDocumentTypeLabel,
  getNonInvoiceWorkflowGuidance,
  isInvoiceDocumentType,
  normalizeDocumentType,
} from '../../utils/document-types.js';
import { fmtDate, fmtDateTime, useAction } from '../route-helpers.js';
import {
  activatePipelineSlice,
  clearPipelineNavigation,
  focusPipelineItem,
  getPipelineBlockerKinds,
} from '../pipeline-views.js';

const html = htm.bind(h);

const SECTION_CONFIG = {
  field_review: {
    title: 'Paused field review',
    detail: 'Resolve conflicting or low-confidence extracted fields directly from Gmail.',
    sliceId: 'blocked_exception',
  },
  non_invoice: {
    title: 'Refunds and credit notes',
    detail: 'Handle non-invoice finance documents with explicit link-and-close workflows.',
    sliceId: 'all_open',
  },
  needs_info: {
    title: 'Needs info',
    detail: 'Items waiting on vendor follow-up or missing finance data.',
    sliceId: 'needs_info',
  },
  failed_post: {
    title: 'Posting retries',
    detail: 'Records that failed ERP posting and need operator attention.',
    sliceId: 'failed_post',
  },
  policy_exception: {
    title: 'Policy and exception review',
    detail: 'Budget, PO, policy, and non-field blockers that still need review.',
    sliceId: 'blocked_exception',
  },
};

function getPipelineScope(orgId, userEmail) {
  return { orgId, userEmail };
}

function isTypingTarget(target) {
  if (!target || typeof target !== 'object') return false;
  const tag = String(target.tagName || '').toUpperCase();
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || Boolean(target.isContentEditable);
}

function sortReviewItems(items = []) {
  return [...items].sort((left, right) => {
    const leftPriority = Number(left?.priority_score || 0);
    const rightPriority = Number(right?.priority_score || 0);
    if (leftPriority !== rightPriority) return rightPriority - leftPriority;
    const leftTs = Date.parse(String(left?.updated_at || left?.created_at || '')) || 0;
    const rightTs = Date.parse(String(right?.updated_at || right?.created_at || '')) || 0;
    return rightTs - leftTs;
  });
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
  if (documentType === 'receipt') {
    return [
      { id: 'link_to_payment', label: 'Link to payment', requiresReference: true, referenceLabel: 'Payment reference' },
      { id: 'archive_receipt', label: 'Archive receipt', requiresReference: false },
      { id: 'needs_followup', label: 'Needs follow-up', requiresReference: false },
    ];
  }
  return [
    { id: 'mark_reviewed', label: 'Mark reviewed', requiresReference: false },
    { id: 'needs_followup', label: 'Needs follow-up', requiresReference: false },
  ];
}

function classifyReviewSection(item) {
  const blockers = getFieldReviewBlockers(item);
  if (blockers.length > 0 || item?.requires_field_review) return 'field_review';

  const documentType = normalizeDocumentType(item?.document_type);
  const state = String(item?.state || '').trim().toLowerCase();
  if (!isInvoiceDocumentType(documentType) && !['closed', 'rejected'].includes(state)) return 'non_invoice';
  if (state === 'failed_post') return 'failed_post';
  if (state === 'needs_info') return 'needs_info';

  const blockerKinds = getPipelineBlockerKinds(item);
  if (blockerKinds.some((kind) => ['exception', 'budget', 'po'].includes(kind))) return 'policy_exception';
  return null;
}

function buildReviewSummary(item) {
  const section = classifyReviewSection(item);
  const documentType = normalizeDocumentType(item?.document_type);
  if (section === 'field_review') {
    return getWorkflowPauseReason(item) || 'Resolve the blocked extracted fields before workflow can continue.';
  }
  if (section === 'failed_post') {
    return getIssueSummary(item) || 'ERP posting failed and needs operator follow-up.';
  }
  if (section === 'needs_info') {
    return getIssueSummary(item) || 'Additional finance details are still required.';
  }
  if (section === 'non_invoice') {
    return getNonInvoiceWorkflowGuidance(documentType);
  }
  const exceptionReason = getExceptionReason(item?.exception_code);
  if (exceptionReason) return exceptionReason;
  return isInvoiceDocumentType(documentType)
    ? 'This invoice still has an open exception.'
    : getNonInvoiceWorkflowGuidance(documentType);
}

function getCommonFieldReviewTarget(items = []) {
  if (!Array.isArray(items) || items.length === 0) return null;
  const blockerRows = new Map();
  let commonField = null;

  for (const item of items) {
    if (classifyReviewSection(item) !== 'field_review') return null;
    const blockers = getFieldReviewBlockers(item);
    if (!blockers.length) return null;
    blockerRows.set(String(item.id || ''), blockers);
  }

  const firstBlockers = blockerRows.get(String(items[0]?.id || '')) || [];
  commonField = firstBlockers
    .map((blocker) => String(blocker?.field || '').trim())
    .find((field) => (
      field
      && items.every((item) => (blockerRows.get(String(item.id || '')) || []).some((row) => String(row?.field || '').trim() === field))
    ));

  if (!commonField) return null;

  const blockersByItemId = new Map();
  for (const item of items) {
    const blocker = (blockerRows.get(String(item.id || '')) || []).find((row) => String(row?.field || '').trim() === commonField);
    if (!blocker) return null;
    blockersByItemId.set(String(item.id || ''), blocker);
  }

  const firstBlocker = blockersByItemId.get(String(items[0]?.id || '')) || {};
  return {
    field: commonField,
    label: firstBlocker.field_label || 'Field',
    blockersByItemId,
    canUseEmail: items.every((item) => {
      const blocker = blockersByItemId.get(String(item.id || '')) || {};
      return blocker.email_value !== null && blocker.email_value !== undefined;
    }),
    canUseAttachment: items.every((item) => {
      const blocker = blockersByItemId.get(String(item.id || '')) || {};
      return blocker.attachment_value !== null && blocker.attachment_value !== undefined;
    }),
  };
}

function SummaryCard({ label, value, tone = 'default' }) {
  const accent = tone === 'danger'
    ? '#B91C1C'
    : tone === 'warning'
      ? '#92400E'
      : tone === 'success'
        ? '#047857'
        : 'var(--ink)';
  return html`<div style="padding:18px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
    <div style="font-size:28px;font-weight:700;letter-spacing:-0.02em;color:${accent}">${Number(value || 0).toLocaleString()}</div>
    <div class="muted" style="font-size:12px;margin-top:4px">${label}</div>
  </div>`;
}

function SectionHeader({ title, detail, count, onOpenSlice }) {
  return html`
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:12px">
      <div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
          <h3 style="margin:0">${title}</h3>
          <span style="font-size:11px;font-weight:700;padding:4px 8px;border-radius:999px;background:var(--bg);border:1px solid var(--border);color:var(--ink-secondary)">
            ${Number(count || 0).toLocaleString()}
          </span>
        </div>
        <p class="muted" style="margin:0">${detail}</p>
      </div>
      <button class="alt" onClick=${onOpenSlice} style="padding:8px 12px;font-size:12px">Open slice</button>
    </div>
  `;
}

function FieldReviewCard({ item, blockers, onResolve, resolvingField }) {
  const pauseReason = getWorkflowPauseReason(item);
  return html`
    <div style="display:flex;flex-direction:column;gap:10px">
      <div style="padding:10px 12px;border:1px solid #fcd34d;border-radius:var(--radius-sm);background:#FEFCE8;color:#78350f;font-size:13px;line-height:1.45">
        ${pauseReason || 'Workflow is paused until the blocked fields are resolved.'}
      </div>
      ${blockers.map((blocker) => html`
        <div key=${`${item.id}-${blocker.field || 'field'}`} style="padding:12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);display:flex;flex-direction:column;gap:6px">
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
            <span class="muted" style="font-size:12px">Current winner</span>
            <span style="font-size:13px;font-weight:600;text-align:right">
              ${blocker.winning_source_label || 'Review required'}
              ${blocker.winning_value_display ? ` (${blocker.winning_value_display})` : ''}
            </span>
          </div>
          <div class="muted" style="font-size:12px;line-height:1.45">${blocker.winner_reason || blocker.reason_label || blocker.paused_reason}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
            ${blocker.email_value !== null && blocker.email_value !== undefined && html`
              <button
                class="alt"
                onClick=${() => onResolve(item, blocker, 'email')}
                disabled=${Boolean(resolvingField === `${item.id}:${blocker.field}:email`)}
                style="padding:8px 12px;font-size:12px"
              >
                ${resolvingField === `${item.id}:${blocker.field}:email` ? 'Saving...' : 'Use email'}
              </button>
            `}
            ${blocker.attachment_value !== null && blocker.attachment_value !== undefined && html`
              <button
                class="alt"
                onClick=${() => onResolve(item, blocker, 'attachment')}
                disabled=${Boolean(resolvingField === `${item.id}:${blocker.field}:attachment`)}
                style="padding:8px 12px;font-size:12px"
              >
                ${resolvingField === `${item.id}:${blocker.field}:attachment` ? 'Saving...' : 'Use attachment'}
              </button>
            `}
            <button
              class="alt"
              onClick=${() => onResolve(item, blocker, 'manual')}
              disabled=${Boolean(resolvingField === `${item.id}:${blocker.field}:manual`)}
              style="padding:8px 12px;font-size:12px"
            >
              ${resolvingField === `${item.id}:${blocker.field}:manual` ? 'Saving...' : 'Enter manually'}
            </button>
          </div>
        </div>
      `)}
    </div>
  `;
}

function ReviewCard({
  item,
  sectionId,
  active,
  selected,
  onOpenRecord,
  onOpenEmail,
  onOpenSlice,
  onResolve,
  onResolveNonInvoice,
  onToggleSelected,
  onSetActive,
  resolvingField,
  resolvingNonInvoiceKey,
}) {
  const blockers = getFieldReviewBlockers(item);
  const documentType = normalizeDocumentType(item?.document_type);
  const referenceLabel = getDocumentReferenceLabel(documentType);
  const referenceValue = String(item?.invoice_number || '').trim() || 'Not set';
  const amountLabel = formatAmount(item?.amount, item?.currency);
  const summary = buildReviewSummary(item);
  const dueLabel = item?.due_date ? fmtDate(item.due_date) : 'N/A';
  const referenceSummary = item?.invoice_number
    ? `${referenceLabel} ${referenceValue}`
    : getDocumentTypeLabel(documentType);
  const lastUpdated = fmtDateTime(item?.updated_at || item?.created_at);
  const nonInvoiceActions = sectionId === 'non_invoice' ? getNonInvoiceActions(item) : [];

  return html`
    <div
      style="
        padding:14px 16px;border:1px solid ${active ? 'var(--accent)' : 'var(--border)'};
        border-radius:var(--radius-md);background:var(--surface);
        box-shadow:${active ? '0 0 0 1px var(--accent-soft)' : 'none'};
      "
      onClick=${() => onSetActive(item.id)}
    >
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <div style="min-width:0;flex:1">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
            <label style="display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:var(--ink-secondary)">
              <input
                type="checkbox"
                checked=${selected}
                onClick=${(event) => event.stopPropagation()}
                onChange=${() => onToggleSelected(item.id)}
              />
              Select
            </label>
            <strong style="font-size:14px">${item.vendor_name || 'Unknown vendor'}</strong>
            <span style="font-size:11px;font-weight:700;padding:4px 8px;border-radius:999px;background:var(--bg);border:1px solid var(--border);color:var(--ink-secondary)">
              ${String(item.state || 'received').replace(/_/g, ' ')}
            </span>
            <span style="font-size:11px;font-weight:700;padding:4px 8px;border-radius:999px;background:#EFF6FF;color:#1D4ED8">
              ${getDocumentTypeLabel(documentType)}
            </span>
          </div>
          <div class="muted" style="font-size:12px;line-height:1.55">
            ${amountLabel} · ${referenceSummary}
            ${isInvoiceDocumentType(documentType) ? ` · Due ${dueLabel}` : ''}
            ${lastUpdated ? ` · Updated ${lastUpdated}` : ''}
          </div>
          ${sectionId === 'field_review'
            ? html`<div style="margin-top:12px"><${FieldReviewCard} item=${item} blockers=${blockers} onResolve=${onResolve} resolvingField=${resolvingField} /></div>`
            : html`<div style="margin-top:10px;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);font-size:12px;line-height:1.5;color:var(--ink-secondary)">
                ${summary}
              </div>`}
          ${nonInvoiceActions.length > 0 && html`
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
              ${nonInvoiceActions.map((action) => html`
                <button
                  key=${action.id}
                  class="alt"
                  onClick=${(event) => {
                    event.stopPropagation();
                    onResolveNonInvoice(item, action);
                  }}
                  disabled=${Boolean(resolvingNonInvoiceKey === `${item.id}:${action.id}`)}
                  style="padding:8px 12px;font-size:12px"
                >
                  ${resolvingNonInvoiceKey === `${item.id}:${action.id}` ? 'Saving...' : action.label}
                </button>
              `)}
            </div>
          `}
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
          <button class="alt" onClick=${(event) => { event.stopPropagation(); onOpenSlice(item); }} style="padding:8px 12px;font-size:12px">Open slice</button>
          <button class="alt" onClick=${(event) => { event.stopPropagation(); onOpenRecord(item); }} style="padding:8px 12px;font-size:12px">Open record</button>
          <button class="alt" onClick=${(event) => { event.stopPropagation(); onOpenEmail(item); }} disabled=${!item.thread_id && !item.message_id} style="padding:8px 12px;font-size:12px">Open email</button>
        </div>
      </div>
    </div>
  `;
}

export default function ReviewPage({ api, orgId, userEmail, navigate, toast }) {
  const pipelineScope = useMemo(() => getPipelineScope(orgId, userEmail), [orgId, userEmail]);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selectedIds, setSelectedIds] = useState([]);
  const [activeItemId, setActiveItemId] = useState('');
  const [resolvingFieldKey, setResolvingFieldKey] = useState('');
  const [resolvingNonInvoiceKey, setResolvingNonInvoiceKey] = useState('');
  const [dialog, openDialog] = useActionDialog();

  const loadItems = useCallback(async ({ silent = false } = {}) => {
    setLoading(true);
    try {
      const data = await api(`/extension/worklist?organization_id=${encodeURIComponent(orgId)}&limit=500`, { silent });
      const nextItems = Array.isArray(data?.items) ? data.items : [];
      setItems(nextItems.filter((item) => Boolean(classifyReviewSection(item))));
    } catch {
      setItems([]);
      if (!silent) toast?.('Could not load the review workbench.', 'error');
    } finally {
      setLoading(false);
    }
  }, [api, orgId, toast]);

  useEffect(() => {
    void loadItems({ silent: true });
  }, [loadItems]);

  const [refresh, refreshing] = useAction(async () => {
    await loadItems();
    toast?.('Review workbench refreshed.', 'success');
  });

  const filtered = useMemo(() => {
    const query = String(search || '').trim().toLowerCase();
    const base = sortReviewItems(items);
    if (!query) return base;
    return base.filter((item) => [
      item.vendor_name,
      item.vendor,
      item.invoice_number,
      item.subject,
      item.sender,
      item.exception_code,
      buildReviewSummary(item),
    ].some((value) => String(value || '').toLowerCase().includes(query)));
  }, [items, search]);

  const buildSections = useCallback((sourceItems = []) => {
    const grouped = {
      field_review: [],
      non_invoice: [],
      needs_info: [],
      failed_post: [],
      policy_exception: [],
    };
    for (const item of sourceItems) {
      const section = classifyReviewSection(item);
      if (section && grouped[section]) grouped[section].push(item);
    }
    return grouped;
  }, []);

  const sections = useMemo(() => buildSections(filtered), [buildSections, filtered]);
  const overallSummary = useMemo(() => {
    const grouped = buildSections(items);
    return {
      total: items.length,
      fieldReview: grouped.field_review.length,
      nonInvoice: grouped.non_invoice.length,
      needsInfo: grouped.needs_info.length,
      failedPost: grouped.failed_post.length,
      policyException: grouped.policy_exception.length,
    };
  }, [buildSections, items]);
  const filteredCount = filtered.length;
  const hasSearch = Boolean(String(search || '').trim());

  useEffect(() => {
    const validIds = new Set(items.map((item) => String(item.id || '')));
    setSelectedIds((prev) => prev.filter((itemId) => validIds.has(String(itemId || ''))));
  }, [items]);

  useEffect(() => {
    if (!filtered.length) {
      if (activeItemId) setActiveItemId('');
      return;
    }
    if (!filtered.some((item) => String(item.id || '') === String(activeItemId || ''))) {
      setActiveItemId(String(filtered[0]?.id || ''));
    }
  }, [filtered, activeItemId]);

  const selectedSet = useMemo(() => new Set(selectedIds.map((itemId) => String(itemId || ''))), [selectedIds]);
  const selectedItems = useMemo(
    () => filtered.filter((item) => selectedSet.has(String(item.id || ''))),
    [filtered, selectedSet],
  );
  const bulkFieldTarget = useMemo(() => getCommonFieldReviewTarget(selectedItems), [selectedItems]);
  const activeItem = useMemo(
    () => filtered.find((item) => String(item.id || '') === String(activeItemId || '')) || null,
    [activeItemId, filtered],
  );

  const openSlice = useCallback((item, fallbackSliceId = 'blocked_exception') => {
    clearPipelineNavigation(pipelineScope);
    activatePipelineSlice(pipelineScope, fallbackSliceId || 'blocked_exception');
    if (item?.id) {
      focusPipelineItem(pipelineScope, item, 'review');
    }
    navigate('clearledgr/pipeline');
  }, [navigate, pipelineScope]);

  const openRecord = useCallback((item) => {
    if (!item?.id) return;
    focusPipelineItem(pipelineScope, item, 'review');
    navigateToRecordDetail(navigate, item.id);
  }, [navigate, pipelineScope]);

  const openEmail = useCallback((item) => {
    const ok = openSourceEmail(item);
    if (!ok) toast?.('Unable to open the source email thread.', 'error');
  }, [toast]);

  const toggleSelected = useCallback((itemId) => {
    const normalizedId = String(itemId || '').trim();
    if (!normalizedId) return;
    setSelectedIds((prev) => (
      prev.includes(normalizedId)
        ? prev.filter((value) => value !== normalizedId)
        : [...prev, normalizedId]
    ));
  }, []);

  const selectVisible = useCallback(() => {
    setSelectedIds(filtered.map((item) => String(item.id || '')));
  }, [filtered]);

  const clearSelection = useCallback(() => setSelectedIds([]), []);

  const [resolveField, resolvingField] = useAction(async (item, blocker, source) => {
    if (!item?.id || !blocker?.field) return;

    let manualValue;
    if (source === 'manual') {
      manualValue = await openDialog({
        actionType: 'field_review_manual',
        title: `Resolve ${blocker.field_label || blocker.field}`,
        label: 'Resolved value',
        message: `Set the canonical value for ${blocker.field_label || blocker.field}. Clearledgr will keep the losing evidence in audit history and resume workflow if this clears the last blocker.`,
        placeholder: `Enter ${String(blocker.field_label || blocker.field || 'value').toLowerCase()}`,
        defaultValue: blocker.winning_value != null
          ? String(blocker.winning_value)
          : (blocker.winning_value_display || ''),
        confirmLabel: 'Apply value',
        cancelLabel: 'Cancel',
        chips: [],
      });
      if (manualValue == null) return;
    }

    const resolvingKey = `${item.id}:${blocker.field}:${source}`;
    setResolvingFieldKey(resolvingKey);
    try {
      const result = await api(`/api/ap/items/${encodeURIComponent(item.id)}/field-review/resolve?organization_id=${encodeURIComponent(orgId)}`, {
        method: 'POST',
        body: JSON.stringify({
          field: blocker.field,
          source,
          manual_value: source === 'manual' ? manualValue : undefined,
          auto_resume: true,
        }),
      });
      const status = String(result?.status || '').toLowerCase();
      const ok = status === 'resolved' || status === 'resolved_and_resumed';
      await loadItems({ silent: true });
      toast?.(
        ok
          ? (
              status === 'resolved_and_resumed'
                ? `${blocker.field_label || 'Field'} updated and workflow resumed.`
                : `${blocker.field_label || 'Field'} updated.`
            )
          : (result?.reason || 'Could not resolve blocked field.'),
        ok ? 'success' : 'error',
      );
    } catch (error) {
      toast?.(error?.message || 'Could not resolve blocked field.', 'error');
    } finally {
      setResolvingFieldKey('');
    }
  });

  const [bulkResolveField, bulkResolvingField] = useAction(async (source) => {
    if (!bulkFieldTarget || selectedItems.length === 0) return;
    let manualValue;
    if (source === 'manual') {
      manualValue = await openDialog({
        actionType: 'field_review_manual',
        title: `Bulk resolve ${bulkFieldTarget.label}`,
        label: `${bulkFieldTarget.label} value`,
        message: `Apply one canonical ${bulkFieldTarget.label.toLowerCase()} value across ${selectedItems.length} selected items.`,
        confirmLabel: 'Apply to selected',
        cancelLabel: 'Cancel',
        required: true,
        chips: [],
      });
      if (manualValue == null) return;
    }

    const result = await api(`/api/ap/items/field-review/bulk-resolve?organization_id=${encodeURIComponent(orgId)}`, {
      method: 'POST',
      body: JSON.stringify({
        ap_item_ids: selectedItems.map((item) => item.id),
        field: bulkFieldTarget.field,
        source,
        manual_value: source === 'manual' ? manualValue : undefined,
        auto_resume: true,
      }),
    });

    await loadItems({ silent: true });
    clearSelection();
    const successCount = Number(result?.success_count || 0);
    const failedCount = Number(result?.failed_count || 0);
    const autoResumedCount = Number(result?.auto_resumed_count || 0);
    toast?.(
      failedCount > 0
        ? `${successCount} updated, ${failedCount} failed${autoResumedCount > 0 ? `, ${autoResumedCount} resumed` : ''}.`
        : `${successCount} updated${autoResumedCount > 0 ? `, ${autoResumedCount} resumed` : ''}.`,
      failedCount > 0 ? 'warning' : 'success',
    );
  });

  const [resolveNonInvoice, resolvingNonInvoice] = useAction(async (item, action) => {
    if (!item?.id || !action?.id) return;
    const resolvingKey = `${item.id}:${action.id}`;
    setResolvingNonInvoiceKey(resolvingKey);
    try {
      let relatedReference = null;
      let note = null;
      if (action.requiresReference) {
        relatedReference = await openDialog({
          actionType: 'generic',
          title: action.label,
          label: action.referenceLabel || 'Related reference',
          message: 'Capture the linked invoice or payment reference so the non-invoice finance record is auditable.',
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
      await loadItems({ silent: true });
      toast?.(
        ok
          ? `${getDocumentTypeLabel(item?.document_type)} updated.`
          : (result?.reason || 'Could not resolve non-invoice review.'),
        ok ? 'success' : 'error',
      );
    } catch (error) {
      toast?.(error?.message || 'Could not resolve non-invoice review.', 'error');
    } finally {
      setResolvingNonInvoiceKey('');
    }
  });

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (dialog.visible || !filtered.length || isTypingTarget(event.target)) return;
      const currentIndex = Math.max(0, filtered.findIndex((item) => String(item.id || '') === String(activeItemId || '')));
      const currentItem = filtered[currentIndex] || filtered[0];
      const lower = String(event.key || '').toLowerCase();
      let handled = false;

      if (lower === 'j' || event.key === 'ArrowDown') {
        const nextIndex = Math.min(filtered.length - 1, currentIndex + 1);
        setActiveItemId(String(filtered[nextIndex]?.id || ''));
        handled = true;
      } else if (lower === 'k' || event.key === 'ArrowUp') {
        const nextIndex = Math.max(0, currentIndex - 1);
        setActiveItemId(String(filtered[nextIndex]?.id || ''));
        handled = true;
      } else if (lower === 'x' && currentItem?.id) {
        toggleSelected(currentItem.id);
        handled = true;
      } else if (lower === 'o' && currentItem) {
        openRecord(currentItem);
        handled = true;
      } else if (lower === 'e' && currentItem) {
        openEmail(currentItem);
        handled = true;
      } else if (lower === 'p' && currentItem) {
        const sectionId = classifyReviewSection(currentItem);
        openSlice(currentItem, SECTION_CONFIG[sectionId || 'field_review']?.sliceId || 'blocked_exception');
        handled = true;
      } else if (['1', '2', '3'].includes(lower) && currentItem && classifyReviewSection(currentItem) === 'field_review') {
        const blocker = getFieldReviewBlockers(currentItem)[0];
        if (blocker) {
          if (lower === '1' && blocker.email_value !== null && blocker.email_value !== undefined) {
            void resolveField(currentItem, blocker, 'email');
            handled = true;
          } else if (lower === '2' && blocker.attachment_value !== null && blocker.attachment_value !== undefined) {
            void resolveField(currentItem, blocker, 'attachment');
            handled = true;
          } else if (lower === '3') {
            void resolveField(currentItem, blocker, 'manual');
            handled = true;
          }
        }
      } else if (lower === 'l' && currentItem && classifyReviewSection(currentItem) === 'non_invoice') {
        const action = getNonInvoiceActions(currentItem)[0];
        if (action) {
          void resolveNonInvoice(currentItem, action);
          handled = true;
        }
      } else if (lower === 'b' && selectedItems.length > 0 && bulkFieldTarget && !bulkResolvingField) {
        if (bulkFieldTarget.canUseAttachment) {
          void bulkResolveField('attachment');
          handled = true;
        } else if (bulkFieldTarget.canUseEmail) {
          void bulkResolveField('email');
          handled = true;
        }
      }

      if (handled) {
        event.preventDefault();
        event.stopPropagation();
      }
    };

    window.addEventListener('keydown', handleKeyDown, true);
    return () => window.removeEventListener('keydown', handleKeyDown, true);
  }, [
    activeItemId,
    bulkFieldTarget,
    bulkResolveField,
    bulkResolvingField,
    dialog.visible,
    filtered,
    openEmail,
    openRecord,
    openSlice,
    resolveField,
    resolveNonInvoice,
    selectedItems,
    toggleSelected,
  ]);

  if (loading) {
    return html`<div class="panel" style="text-align:center;padding:48px"><p class="muted">Loading review workbench...</p></div>`;
  }

  return html`
    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap">
        <div>
          <h3 style="margin:0 0 6px">Review workbench</h3>
          <p class="muted" style="margin:0;max-width:680px">
            Resolve blocked fields, work open exceptions, handle posting retries, and close refunds or credit notes from one finance-focused surface.
          </p>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="alt" onClick=${refresh} disabled=${refreshing}>${refreshing ? 'Refreshing...' : 'Refresh'}</button>
          <button onClick=${() => navigate('clearledgr/pipeline')}>Open pipeline</button>
        </div>
      </div>
    </div>

    <div class="kpi-row" style="grid-template-columns:repeat(5,1fr)">
      <${SummaryCard} label="Open review items" value=${overallSummary.total} />
      <${SummaryCard} label="Paused field review" value=${overallSummary.fieldReview} tone="warning" />
      <${SummaryCard} label="Refunds / credits" value=${overallSummary.nonInvoice} tone="success" />
      <${SummaryCard} label="Needs info" value=${overallSummary.needsInfo} />
      <${SummaryCard} label="Posting retries" value=${overallSummary.failedPost} tone="danger" />
    </div>

    <div class="panel">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px">
        <div>
          <h3 style="margin:0 0 4px">Search review work</h3>
          <p class="muted" style="margin:0">Find a blocked record by vendor, reference, sender, or exception.</p>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${hasSearch && html`
            <span style="font-size:12px;font-weight:700;padding:5px 10px;border-radius:999px;background:var(--bg);border:1px solid var(--border);color:var(--ink-secondary)">
              Showing ${filteredCount} of ${overallSummary.total}
            </span>
          `}
          ${overallSummary.policyException > 0 && html`
            <span style="font-size:12px;font-weight:700;padding:5px 10px;border-radius:999px;background:#FFF7ED;border:1px solid #FED7AA;color:#9A3412">
              ${overallSummary.policyException} policy / exception blockers
            </span>
          `}
        </div>
      </div>
      <div style="position:relative">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--ink-muted)" stroke-width="2" style="position:absolute;left:10px;top:50%;transform:translateY(-50%)"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        <input
          placeholder="Search review items..."
          value=${search}
          onInput=${(event) => setSearch(event.target.value)}
          style="width:100%;padding:8px 8px 8px 34px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;font-family:inherit;background:var(--bg)"
        />
      </div>
      <div class="muted" style="font-size:12px;margin-top:10px">
        Keyboard: J/K move · X select · O open record · E open email · P open slice · 1/2/3 resolve current blocker · L apply primary non-invoice action · B bulk resolve selected blockers
      </div>
    </div>

    ${selectedIds.length > 0 && html`
      <div class="panel">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
          <div>
            <h3 style="margin:0 0 4px">${selectedIds.length} selected</h3>
            <p class="muted" style="margin:0">
              ${bulkFieldTarget
                ? `Bulk resolve ${bulkFieldTarget.label.toLowerCase()} across similar blocked items.`
                : 'Current selection does not share a single blocked field.'}
            </p>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="alt" onClick=${selectVisible}>Select visible</button>
            <button class="alt" onClick=${clearSelection}>Clear selection</button>
            ${bulkFieldTarget?.canUseEmail && html`
              <button class="alt" onClick=${() => bulkResolveField('email')} disabled=${bulkResolvingField}>
                ${bulkResolvingField ? 'Saving...' : 'Bulk use email'}
              </button>
            `}
            ${bulkFieldTarget?.canUseAttachment && html`
              <button class="alt" onClick=${() => bulkResolveField('attachment')} disabled=${bulkResolvingField}>
                ${bulkResolvingField ? 'Saving...' : 'Bulk use attachment'}
              </button>
            `}
            ${bulkFieldTarget && html`
              <button class="alt" onClick=${() => bulkResolveField('manual')} disabled=${bulkResolvingField}>
                ${bulkResolvingField ? 'Saving...' : 'Bulk enter manually'}
              </button>
            `}
          </div>
        </div>
      </div>
    `}

    ${Object.entries(SECTION_CONFIG).map(([sectionId, config]) => {
      const sectionItems = sections[sectionId] || [];
      if (sectionItems.length === 0) return null;
      return html`
        <div class="panel" key=${sectionId}>
          <${SectionHeader}
            title=${config.title}
            detail=${config.detail}
            count=${sectionItems.length}
            onOpenSlice=${() => openSlice(null, config.sliceId)}
          />
          <div style="display:flex;flex-direction:column;gap:12px">
            ${sectionItems.map((item) => html`
              <${ReviewCard}
                key=${item.id}
                item=${item}
                sectionId=${sectionId}
                active=${String(activeItemId || '') === String(item.id || '')}
                selected=${selectedSet.has(String(item.id || ''))}
                onOpenRecord=${openRecord}
                onOpenEmail=${openEmail}
                onOpenSlice=${(target) => openSlice(target, config.sliceId)}
                onResolve=${resolveField}
                onResolveNonInvoice=${resolveNonInvoice}
                onToggleSelected=${toggleSelected}
                onSetActive=${setActiveItemId}
                resolvingField=${resolvingFieldKey}
                resolvingNonInvoiceKey=${resolvingNonInvoiceKey}
              />
            `)}
          </div>
        </div>
      `;
    })}

    ${overallSummary.total === 0 && html`
      <div class="panel">
        <h3 style="margin:0 0 6px">Nothing blocked right now</h3>
        <p class="muted" style="margin:0">Clearledgr will surface field review, non-invoice review, needs-info, posting retry, and policy exception work here as it appears.</p>
      </div>
    `}

    ${overallSummary.total > 0 && filteredCount === 0 && html`
      <div class="panel">
        <h3 style="margin:0 0 6px">No review items match this search</h3>
        <p class="muted" style="margin:0">Try a vendor name, reference number, sender, or exception keyword.</p>
      </div>
    `}

    <${ActionDialog} ...${dialog} />
  `;
}
