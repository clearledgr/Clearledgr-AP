/**
 * Review Page — burst-mode focus queue for AP operators.
 *
 * Operators land here with a backlog (often 30+ items piled up
 * after a weekend) and need to power through quickly. The page
 * commits to one item at a time with three big tone-coded action
 * buttons, auto-advance on resolve, and a session timer for
 * momentum. Single-item deep dive lives at /records/{id}; this
 * page is purpose-built for SPEED through MANY.
 *
 * Per the thesis: the workspace is the only surface that can
 * show 30 stuck records at once or batch-resolve a shared
 * blocker. Single-item resolution belongs in Gmail (the thread
 * sidebar) or Slack (approval cards) — escape hatches on each
 * focus card route there.
 */
import { h, Fragment } from 'preact';
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import ActionDialog, { useActionDialog } from '../../components/ActionDialog.js';
import { navigateToRecordDetail } from '../../utils/record-route.js';
import {
  formatAmount,
  getExceptionReason,
  getFieldReviewBlockers,
  getIssueSummary,
  getStateLabel,
  getWorkflowPauseReason,
  openSourceEmail,
} from '../../utils/formatters.js';
import {
  getDocumentTypeLabel,
  getNonInvoiceWorkflowGuidance,
  isInvoiceDocumentType,
  normalizeDocumentType,
} from '../../utils/document-types.js';
import {
  getWorkStateNotice,
} from '../../utils/work-actions.js';
import { fmtDateTime, useAction } from '../route-helpers.js';
import {
  activatePipelineSlice,
  clearPipelineNavigation,
  focusPipelineItem,
  getPipelineBlockerKinds,
} from '../pipeline-views.js';
import {
  clearReviewPreferences,
  readReviewPreferences,
  writeReviewPreferences,
} from '../review-preferences.js';

const html = htm.bind(h);


// ─── Section classification ───────────────────────────────────

const SECTION_LABELS = {
  field_review:     'Field check',
  non_invoice:      'Non-invoice',
  needs_info:       'Vendor follow-up',
  failed_post:      'Posting retry',
  policy_exception: 'Policy / exception',
};

const SECTION_TONES = {
  field_review:     'warning',
  non_invoice:      'info',
  needs_info:       'info',
  failed_post:      'danger',
  policy_exception: 'warning',
};


// ─── Helpers (kept from prior implementation) ─────────────────

function getPipelineScope(orgId, userEmail) {
  return { orgId, userEmail };
}

function sortReviewItems(items = []) {
  return [...items].sort((a, b) => {
    const aPri = Number(a?.priority_score || 0);
    const bPri = Number(b?.priority_score || 0);
    if (aPri !== bPri) return bPri - aPri;
    const aTs = Date.parse(String(a?.updated_at || a?.created_at || '')) || 0;
    const bTs = Date.parse(String(b?.updated_at || b?.created_at || '')) || 0;
    return bTs - aTs;
  });
}

function getNonInvoiceActions(item) {
  const dt = normalizeDocumentType(item?.document_type);
  const followup = { id: 'needs_followup', label: 'Needs follow-up', requiresReference: false };
  if (dt === 'credit_note') return [
    { id: 'apply_to_invoice', label: 'Apply to invoice', requiresReference: true, referenceLabel: 'Invoice reference' },
    { id: 'record_vendor_credit', label: 'Record vendor credit', requiresReference: false },
    followup,
  ];
  if (dt === 'refund') return [
    { id: 'link_to_payment', label: 'Link to payment', requiresReference: true, referenceLabel: 'Payment reference' },
    { id: 'record_vendor_refund', label: 'Record vendor refund', requiresReference: false },
    followup,
  ];
  if (dt === 'payment') return [
    { id: 'link_to_payment', label: 'Link to payment', requiresReference: true, referenceLabel: 'Payment reference' },
    { id: 'record_payment_confirmation', label: 'Record payment confirmation', requiresReference: false },
    followup,
  ];
  if (dt === 'receipt') return [
    { id: 'link_to_payment', label: 'Link to payment', requiresReference: true, referenceLabel: 'Payment reference' },
    { id: 'archive_receipt', label: 'Archive receipt', requiresReference: false },
    followup,
  ];
  if (dt === 'statement') return [
    { id: 'send_to_reconciliation', label: 'Send to reconciliation', requiresReference: false },
    followup,
  ];
  if (dt === 'payment_request') return [
    { id: 'route_outside_invoice_workflow', label: 'Route outside invoice workflow', requiresReference: false },
    followup,
  ];
  return [
    { id: 'mark_reviewed', label: 'Mark reviewed', requiresReference: false },
    followup,
  ];
}

function classifyReviewSection(item) {
  const blockers = getFieldReviewBlockers(item);
  if (blockers.length > 0 || item?.requires_field_review) return 'field_review';
  const dt = normalizeDocumentType(item?.document_type);
  const state = String(item?.state || '').trim().toLowerCase();
  if (!isInvoiceDocumentType(dt) && !['closed', 'rejected'].includes(state)) return 'non_invoice';
  if (state === 'failed_post') return 'failed_post';
  if (state === 'needs_info') return 'needs_info';
  const kinds = getPipelineBlockerKinds(item);
  if (kinds.some((k) => ['exception', 'budget', 'po'].includes(k))) return 'policy_exception';
  return null;
}

function buildReviewSummary(item) {
  const section = classifyReviewSection(item);
  const dt = normalizeDocumentType(item?.document_type);
  const workNotice = safeDisplayText(getWorkStateNotice(item?.state, dt, item), '');
  if (section === 'field_review') return getWorkflowPauseReason(item) || 'Check the blocked fields before continuing.';
  if (section === 'failed_post') return workNotice || getIssueSummary(item) || 'ERP posting still needs review.';
  if (section === 'needs_info') return workNotice || 'Missing details needed before this invoice can continue.';
  if (section === 'non_invoice') return getNonInvoiceWorkflowGuidance(dt);
  const reason = getExceptionReason(item?.exception_code);
  if (reason) return reason;
  return isInvoiceDocumentType(dt)
    ? 'This invoice still has an open exception.'
    : getNonInvoiceWorkflowGuidance(dt);
}

function safeDisplayText(value, fallback = '') {
  if (value === null || value === undefined) return fallback;
  if (typeof value === 'string') return value.trim() || fallback;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    const text = value.map((e) => safeDisplayText(e, '')).filter(Boolean).join(', ').trim();
    return text || fallback;
  }
  if (typeof value === 'object') {
    for (const key of ['display', 'display_value', 'value', 'label', 'name', 'title', 'text', 'id']) {
      const text = safeDisplayText(value?.[key], '');
      if (text) return text;
    }
    return fallback;
  }
  try { return String(value).trim() || fallback; } catch { return fallback; }
}

function getCommonFieldReviewTarget(items = []) {
  if (!Array.isArray(items) || items.length === 0) return null;
  const blockerRows = new Map();
  for (const item of items) {
    if (classifyReviewSection(item) !== 'field_review') return null;
    const b = getFieldReviewBlockers(item);
    if (!b.length) return null;
    blockerRows.set(String(item.id || ''), b);
  }
  const firstBlockers = blockerRows.get(String(items[0]?.id || '')) || [];
  const commonField = firstBlockers
    .map((b) => String(b?.field || '').trim())
    .find((f) => f && items.every((it) => (blockerRows.get(String(it.id || '')) || []).some((r) => String(r?.field || '').trim() === f)));
  if (!commonField) return null;
  const blockersByItemId = new Map();
  for (const item of items) {
    const b = (blockerRows.get(String(item.id || '')) || []).find((r) => String(r?.field || '').trim() === commonField);
    if (!b) return null;
    blockersByItemId.set(String(item.id || ''), b);
  }
  const firstBlocker = blockersByItemId.get(String(items[0]?.id || '')) || {};
  return {
    field: commonField,
    label: firstBlocker.field_label || 'Field',
    blockersByItemId,
    canUseEmail: items.every((it) => {
      const b = blockersByItemId.get(String(it.id || '')) || {};
      return b.email_value !== null && b.email_value !== undefined;
    }),
    canUseAttachment: items.every((it) => {
      const b = blockersByItemId.get(String(it.id || '')) || {};
      return b.attachment_value !== null && b.attachment_value !== undefined;
    }),
  };
}


// ─── Main page ────────────────────────────────────────────────

export default function ReviewPage({ api, orgId, userEmail, navigate, toast }) {
  const pipelineScope = useMemo(() => getPipelineScope(orgId, userEmail), [orgId, userEmail]);

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState(() => readReviewPreferences(pipelineScope).searchQuery || '');
  const [resolvingFieldKey, setResolvingFieldKey] = useState('');
  const [resolvingNonInvoiceKey, setResolvingNonInvoiceKey] = useState('');
  const [dialog, openDialog] = useActionDialog();

  // Burst-mode state
  const [focusedId, setFocusedId] = useState('');
  const [skippedIds, setSkippedIds] = useState(() => new Set());
  const [clearedCount, setClearedCount] = useState(0);
  const sessionStartRef = useRef(Date.now());

  // ── Data layer ─────────────────────────────────────────────

  const loadItems = useCallback(async ({ silent = false } = {}) => {
    setLoading(true);
    try {
      const data = await api(`/extension/worklist?organization_id=${encodeURIComponent(orgId)}&limit=500`, { silent });
      const next = Array.isArray(data?.items) ? data.items : [];
      setItems(next.filter((it) => Boolean(classifyReviewSection(it))));
    } catch {
      setItems([]);
      if (!silent) toast?.('Could not load the review queue.', 'error');
    } finally {
      setLoading(false);
    }
  }, [api, orgId, toast]);

  useEffect(() => { void loadItems({ silent: true }); }, [loadItems]);

  useEffect(() => {
    if (String(search || '').trim()) {
      writeReviewPreferences(pipelineScope, { searchQuery: search });
    } else {
      clearReviewPreferences(pipelineScope);
    }
  }, [pipelineScope, search]);

  const [refresh, refreshing] = useAction(async () => {
    await loadItems();
    toast?.('Review queue refreshed.', 'success');
  });

  // ── Derived queue (sorted, search-filtered, skip-filtered) ──

  const queue = useMemo(() => {
    const q = String(search || '').trim().toLowerCase();
    const sorted = sortReviewItems(items);
    return sorted.filter((it) => {
      if (skippedIds.has(String(it.id || ''))) return false;
      if (!q) return true;
      return [it.vendor_name, it.vendor, it.invoice_number, it.subject, it.sender, it.exception_code, buildReviewSummary(it)]
        .some((v) => String(v || '').toLowerCase().includes(q));
    });
  }, [items, search, skippedIds]);

  const overall = useMemo(() => {
    const stats = { total: items.length, field_review: 0, non_invoice: 0, needs_info: 0, failed_post: 0, policy_exception: 0 };
    for (const it of items) {
      const s = classifyReviewSection(it);
      if (s) stats[s] = (stats[s] || 0) + 1;
    }
    return stats;
  }, [items]);

  const focusedIndex = queue.findIndex((it) => String(it.id || '') === focusedId);
  const focusedItem = focusedIndex >= 0 ? queue[focusedIndex] : queue[0];

  // Auto-advance focus when the focused item leaves the queue (resolved / skipped / dropped by API).
  useEffect(() => {
    if (queue.length === 0) {
      if (focusedId) setFocusedId('');
      return;
    }
    if (!focusedId || !queue.some((it) => String(it.id || '') === focusedId)) {
      setFocusedId(String(queue[0]?.id || ''));
    }
  }, [queue, focusedId]);

  // ── Navigation callbacks ───────────────────────────────────

  const advanceFocus = useCallback(() => {
    if (queue.length <= 1) return;
    const idx = Math.max(0, queue.findIndex((it) => String(it.id || '') === focusedId));
    setFocusedId(String(queue[(idx + 1) % queue.length]?.id || ''));
  }, [queue, focusedId]);

  const prevFocus = useCallback(() => {
    if (queue.length <= 1) return;
    const idx = Math.max(0, queue.findIndex((it) => String(it.id || '') === focusedId));
    setFocusedId(String(queue[(idx - 1 + queue.length) % queue.length]?.id || ''));
  }, [queue, focusedId]);

  const skipFocused = useCallback(() => {
    const id = String(focusedItem?.id || '').trim();
    if (!id) return;
    setSkippedIds((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  }, [focusedItem]);

  const openRecord = useCallback((item) => {
    if (!item?.id) return;
    focusPipelineItem(pipelineScope, item, 'review');
    navigateToRecordDetail(navigate, item.id);
  }, [navigate, pipelineScope]);

  const openEmail = useCallback((item) => {
    const ok = openSourceEmail(item);
    if (!ok) toast?.('No source email thread linked to this record.', 'error');
  }, [toast]);

  const openSlice = useCallback((item, sliceId = 'blocked_exception') => {
    clearPipelineNavigation(pipelineScope);
    activatePipelineSlice(pipelineScope, sliceId);
    if (item?.id) focusPipelineItem(pipelineScope, item, 'review');
    navigate('clearledgr/invoices');
  }, [navigate, pipelineScope]);

  // ── Resolve actions ────────────────────────────────────────

  const [resolveField, resolvingField] = useAction(async (item, blocker, source) => {
    if (!item?.id || !blocker?.field) return;
    let manualValue;
    if (source === 'manual') {
      manualValue = await openDialog({
        actionType: 'field_review_manual',
        title: `Resolve ${blocker.field_label || blocker.field}`,
        label: 'Resolved value',
        message: `Set the canonical value for ${blocker.field_label || blocker.field}. Solden keeps the losing evidence in audit history.`,
        placeholder: `Enter ${String(blocker.field_label || blocker.field || 'value').toLowerCase()}`,
        defaultValue: blocker.winning_value != null ? String(blocker.winning_value) : (blocker.winning_value_display || ''),
        confirmLabel: 'Apply value',
        cancelLabel: 'Cancel',
      });
      if (manualValue == null) return;
    }
    const key = `${item.id}:${blocker.field}:${source}`;
    setResolvingFieldKey(key);
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
      if (ok) setClearedCount((c) => c + 1);
      await loadItems({ silent: true });
      toast?.(
        ok
          ? (status === 'resolved_and_resumed' ? `${blocker.field_label || 'Field'} updated, workflow resumed.` : `${blocker.field_label || 'Field'} updated.`)
          : (result?.reason || 'Could not resolve blocked field.'),
        ok ? 'success' : 'error',
      );
    } catch (err) {
      toast?.(err?.message || 'Could not resolve blocked field.', 'error');
    } finally {
      setResolvingFieldKey('');
    }
  });

  const [resolveNonInvoice, resolvingNonInvoice] = useAction(async (item, action) => {
    if (!item?.id || !action?.id) return;
    const key = `${item.id}:${action.id}`;
    setResolvingNonInvoiceKey(key);
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
      if (ok) setClearedCount((c) => c + 1);
      await loadItems({ silent: true });
      toast?.(
        ok
          ? `${getDocumentTypeLabel(item?.document_type)} updated.`
          : (result?.reason || 'Could not resolve non-invoice review.'),
        ok ? 'success' : 'error',
      );
    } catch (err) {
      toast?.(err?.message || 'Could not resolve non-invoice review.', 'error');
    } finally {
      setResolvingNonInvoiceKey('');
    }
  });

  // ── Bulk-resolve detection (across full items list) ───────

  const bulkTarget = useMemo(() => {
    // If 3+ field-review items share the same blocker + source preference,
    // surface a "resolve all" banner. Detected on the FULL queue, not the
    // search-filtered one — power-users want to act on the cluster.
    const fieldItems = items.filter((it) => classifyReviewSection(it) === 'field_review');
    if (fieldItems.length < 3) return null;
    const target = getCommonFieldReviewTarget(fieldItems);
    if (!target) return null;
    return { ...target, items: fieldItems, count: fieldItems.length };
  }, [items]);

  const [bulkApply, bulkApplying] = useAction(async (source) => {
    if (!bulkTarget) return;
    let manualValue;
    if (source === 'manual') {
      manualValue = await openDialog({
        actionType: 'field_review_manual',
        title: `Resolve ${bulkTarget.label} on ${bulkTarget.count} items`,
        label: `${bulkTarget.label} value`,
        message: `Apply one canonical ${bulkTarget.label.toLowerCase()} across all ${bulkTarget.count} items that share this blocker.`,
        confirmLabel: `Apply to ${bulkTarget.count}`,
        cancelLabel: 'Cancel',
        required: true,
      });
      if (manualValue == null) return;
    }
    const result = await api(`/api/ap/items/field-review/bulk-resolve?organization_id=${encodeURIComponent(orgId)}`, {
      method: 'POST',
      body: JSON.stringify({
        ap_item_ids: bulkTarget.items.map((it) => it.id),
        field: bulkTarget.field,
        source,
        manual_value: source === 'manual' ? manualValue : undefined,
        auto_resume: true,
      }),
    });
    const success = Number(result?.success_count || 0);
    if (success > 0) setClearedCount((c) => c + success);
    await loadItems({ silent: true });
    toast?.(`${success} resolved${result?.failed_count ? `, ${result.failed_count} failed` : ''}.`, success > 0 ? 'success' : 'error');
  });

  // ── Render ────────────────────────────────────────────────

  if (loading && items.length === 0) {
    return html`<div class="review-shell"><div class="panel" style="text-align:center;padding:48px"><p class="muted">Loading review queue…</p></div></div>`;
  }

  return html`
    <div class="review-shell review-burst">
      <${BurstHeader}
        overall=${overall}
        remaining=${queue.length}
        cleared=${clearedCount}
        sessionStartTs=${sessionStartRef.current}
        search=${search}
        onSearch=${setSearch}
        onRefresh=${refresh}
        refreshing=${refreshing}
      />

      ${bulkTarget ? html`
        <${BulkBanner}
          target=${bulkTarget}
          applying=${bulkApplying}
          onApply=${bulkApply}
        />
      ` : null}

      ${queue.length === 0 ? html`
        <${EmptyState}
          totalReviewed=${clearedCount}
          sessionStartTs=${sessionStartRef.current}
          onRefresh=${refresh}
          onPipeline=${() => navigate('clearledgr/invoices')}
          hadItems=${items.length > 0 && skippedIds.size > 0}
          skippedCount=${skippedIds.size}
          onUnskip=${() => setSkippedIds(new Set())}
        />
      ` : html`
        <${FocusCard}
          item=${focusedItem}
          position=${focusedIndex + 1}
          total=${queue.length}
          resolvingField=${resolvingFieldKey}
          resolvingNonInvoice=${resolvingNonInvoiceKey}
          onResolveField=${resolveField}
          onResolveNonInvoice=${resolveNonInvoice}
          onSkip=${skipFocused}
          onPrev=${prevFocus}
          onNext=${advanceFocus}
          onOpenRecord=${() => openRecord(focusedItem)}
          onOpenEmail=${() => openEmail(focusedItem)}
          onOpenSlice=${(sliceId) => openSlice(focusedItem, sliceId)}
        />
        <${QueuePeek} queue=${queue} focusedIndex=${focusedIndex} />
      `}

      <${DisputesPanel} api=${api} orgId=${orgId} navigate=${navigate} />

      <${ActionDialog} ...${dialog} />
    </div>
  `;
}


// ─── Burst header ─────────────────────────────────────────────

function BurstHeader({ overall, remaining, cleared, sessionStartTs, search, onSearch, onRefresh, refreshing }) {
  const [elapsed, setElapsed] = useState(() => Date.now() - sessionStartTs);
  useEffect(() => {
    const timer = setInterval(() => setElapsed(Date.now() - sessionStartTs), 5000);
    return () => clearInterval(timer);
  }, [sessionStartTs]);

  const mins = Math.floor(elapsed / 60000);
  const secs = Math.floor((elapsed % 60000) / 1000);
  const elapsedLabel = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;

  const pct = overall.total > 0 ? Math.round(((overall.total - remaining) / overall.total) * 100) : 0;

  return html`
    <div class="review-burst-header">
      <div class="review-burst-headline">
        <div class="review-burst-eyebrow">Review queue</div>
        <div class="review-burst-counter">
          <span class="review-burst-remaining">${remaining}</span>
          <span class="review-burst-counter-label">${remaining === 1 ? 'item to clear' : 'items to clear'}</span>
        </div>
        ${cleared > 0 ? html`
          <div class="review-burst-progress">
            <div class="review-burst-progress-bar"><div class="review-burst-progress-fill" style=${`width: ${pct}%`}></div></div>
            <div class="review-burst-progress-label">${cleared} cleared this session · ${elapsedLabel}</div>
          </div>
        ` : html`
          <div class="review-burst-progress-label muted">Session timer · ${elapsedLabel}</div>
        `}
      </div>

      <div class="review-burst-controls">
        <input
          class="review-burst-search"
          placeholder="Filter by vendor, invoice #, sender…"
          value=${search}
          onInput=${(e) => onSearch(e.target.value)}
        />
        <button class="btn-ghost btn-sm" onClick=${onRefresh} disabled=${refreshing}>${refreshing ? '…' : 'Refresh'}</button>
      </div>

      <div class="review-burst-mix">
        ${[
          ['field_review',     overall.field_review],
          ['needs_info',       overall.needs_info],
          ['failed_post',      overall.failed_post],
          ['policy_exception', overall.policy_exception],
          ['non_invoice',      overall.non_invoice],
        ].filter(([, n]) => n > 0).map(([section, n]) => html`
          <span class=${`review-burst-mix-pill review-burst-mix-${SECTION_TONES[section]}`} key=${section}>
            <strong>${n}</strong> ${SECTION_LABELS[section]}${n === 1 ? '' : 's'}
          </span>
        `)}
      </div>
    </div>
  `;
}


// ─── Bulk-resolve banner ─────────────────────────────────────

function BulkBanner({ target, applying, onApply }) {
  return html`
    <div class="review-bulk-banner">
      <div class="review-bulk-banner-copy">
        <strong>${target.count} items share the same blocker:</strong>
        <span> ${target.label}</span>
        <div class="muted" style="font-size:12px;margin-top:2px">
          Apply one canonical value to all of them in one shot — faster than working through them card by card.
        </div>
      </div>
      <div class="review-bulk-banner-actions">
        ${target.canUseEmail && html`
          <button class="btn-secondary btn-sm" onClick=${() => onApply('email')} disabled=${applying}>
            ${applying ? '…' : 'Use all email values'}
          </button>
        `}
        ${target.canUseAttachment && html`
          <button class="btn-secondary btn-sm" onClick=${() => onApply('attachment')} disabled=${applying}>
            ${applying ? '…' : 'Use all attachment values'}
          </button>
        `}
        <button class="btn-primary btn-sm" onClick=${() => onApply('manual')} disabled=${applying}>
          ${applying ? 'Applying…' : `Set ${target.label.toLowerCase()} for all`}
        </button>
      </div>
    </div>
  `;
}


// ─── Focus card (the hero) ───────────────────────────────────

function FocusCard({
  item, position, total,
  resolvingField, resolvingNonInvoice,
  onResolveField, onResolveNonInvoice,
  onSkip, onPrev, onNext, onOpenRecord, onOpenEmail, onOpenSlice,
}) {
  const section = classifyReviewSection(item);
  const documentType = normalizeDocumentType(item?.document_type);
  const vendorLabel = safeDisplayText(item?.vendor_name, 'Unknown vendor');
  const invoiceNumber = safeDisplayText(item?.invoice_number, '—');
  const amountLabel = safeDisplayText(formatAmount(item?.amount, item?.currency), '—');
  const stateLabel = safeDisplayText(getStateLabel(item?.state), 'Received');
  const lastUpdated = safeDisplayText(fmtDateTime(item?.updated_at || item?.created_at), '');
  const sectionLabel = SECTION_LABELS[section] || 'Review';
  const sectionTone = SECTION_TONES[section] || 'info';
  const summary = safeDisplayText(buildReviewSummary(item), 'Review needed.');

  return html`
    <div class="review-focus-card" data-section=${section}>
      <header class="review-focus-head">
        <div class="review-focus-head-left">
          <span class=${`review-focus-section review-focus-section-${sectionTone}`}>${sectionLabel}</span>
          <span class="review-focus-position">${position} of ${total}</span>
        </div>
        <div class="review-focus-head-right">
          <button class="btn-ghost btn-sm" onClick=${onPrev}>← Previous</button>
          <button class="btn-ghost btn-sm" onClick=${onNext}>Next →</button>
        </div>
      </header>

      <div class="review-focus-meta">
        <h2 class="review-focus-vendor">${vendorLabel}</h2>
        <div class="review-focus-fact-row">
          <span><strong>${amountLabel}</strong></span>
          <span class="review-focus-sep">·</span>
          <span>Invoice <strong>#${invoiceNumber}</strong></span>
          <span class="review-focus-sep">·</span>
          <span class="muted">${getDocumentTypeLabel(documentType)}</span>
          <span class="review-focus-sep">·</span>
          <span class="muted">${stateLabel}</span>
          ${lastUpdated ? html`<${Fragment}><span class="review-focus-sep">·</span><span class="muted">Updated ${lastUpdated}</span></${Fragment}>` : null}
        </div>
        <p class="review-focus-summary">${summary}</p>
      </div>

      ${section === 'field_review' ? html`
        <${FieldReviewActions}
          item=${item}
          resolving=${resolvingField}
          onResolve=${onResolveField}
        />
      ` : section === 'non_invoice' ? html`
        <${NonInvoiceActions}
          item=${item}
          resolving=${resolvingNonInvoice}
          onResolve=${onResolveNonInvoice}
        />
      ` : html`
        <${OpenRecordActions}
          section=${section}
          onOpenRecord=${onOpenRecord}
        />
      `}

      <footer class="review-focus-footer">
        <div class="review-focus-escapes">
          <button class="btn-ghost btn-sm" onClick=${onOpenEmail}>Open in Gmail</button>
          <button class="btn-ghost btn-sm" onClick=${() => onOpenSlice('blocked_exception')}>Open slice</button>
          <button class="btn-secondary btn-sm" onClick=${onOpenRecord}>Open record</button>
          <button class="btn-ghost btn-sm" onClick=${onSkip}>Skip</button>
        </div>
      </footer>
    </div>
  `;
}


// ─── Field-review actions (the diff + 3 action buttons) ──

function FieldReviewActions({ item, resolving, onResolve }) {
  const blockers = getFieldReviewBlockers(item);
  const active = blockers[0];
  if (!active) {
    return html`
      <div class="review-focus-actions">
        <p class="muted">No blocked fields detected. Refresh the queue.</p>
      </div>
    `;
  }
  const fieldLabel = safeDisplayText(active.field_label, 'Field');
  const emailValue = safeDisplayText(active.email_value_display, '');
  const attachmentValue = safeDisplayText(active.attachment_value_display, '');
  const currentSource = safeDisplayText(active.winning_source_label, '');
  const currentValue = safeDisplayText(active.winning_value_display, '');
  const reason = safeDisplayText(active.winner_reason || active.reason_label || active.paused_reason, '');
  const remaining = blockers.length - 1;

  const emailKey = `${item.id}:${active.field}:email`;
  const attachKey = `${item.id}:${active.field}:attachment`;
  const manualKey = `${item.id}:${active.field}:manual`;
  const isResolving = (key) => resolving === key;
  const anyResolving = !!resolving;
  const hasEmail = active.email_value !== null && active.email_value !== undefined;
  const hasAttachment = active.attachment_value !== null && active.attachment_value !== undefined;

  return html`
    <div class="review-focus-actions">
      <div class="review-focus-question">
        <div class="review-focus-question-label">Resolve</div>
        <div class="review-focus-question-text">
          ${active.kind === 'confidence'
            ? `Confirm ${fieldLabel.toLowerCase()}`
            : `Choose the correct ${fieldLabel.toLowerCase()}`}
        </div>
        ${remaining > 0 ? html`
          <div class="review-focus-question-meta muted">${remaining} more field${remaining === 1 ? '' : 's'} on this invoice after this one</div>
        ` : null}
      </div>

      <div class="review-focus-diff">
        <${DiffOption}
          label="Email"
          value=${emailValue || '—'}
          available=${hasEmail}
          onClick=${() => onResolve(item, active, 'email')}
          loading=${isResolving(emailKey)}
          disabled=${anyResolving || !hasEmail}
        />
        <${DiffOption}
          label="Attachment"
          value=${attachmentValue || '—'}
          available=${hasAttachment}
          onClick=${() => onResolve(item, active, 'attachment')}
          loading=${isResolving(attachKey)}
          disabled=${anyResolving || !hasAttachment}
        />
        <${DiffOption}
          label="Type the value"
          value=${currentValue ? `Current: ${currentValue}` : 'Enter a new value'}
          available=${true}
          variant="manual"
          onClick=${() => onResolve(item, active, 'manual')}
          loading=${isResolving(manualKey)}
          disabled=${anyResolving}
        />
      </div>

      ${reason || currentSource ? html`
        <div class="review-focus-why">
          ${reason ? html`<span>${reason}</span>` : null}
          ${currentSource ? html`<span class="muted"> · Current choice: ${currentSource}${currentValue ? ` (${currentValue})` : ''}</span>` : null}
        </div>
      ` : null}
    </div>
  `;
}


function DiffOption({ label, value, available, variant = 'source', onClick, loading, disabled }) {
  return html`
    <button
      class=${`review-diff-option review-diff-option-${variant} ${available ? '' : 'is-unavailable'}`}
      onClick=${onClick}
      disabled=${disabled}>
      <span class="review-diff-option-label">${label}</span>
      <div class="review-diff-option-value">
        ${loading ? 'Saving…' : value}
      </div>
    </button>
  `;
}


// ─── Non-invoice actions ────────────────────────────────────

function NonInvoiceActions({ item, resolving, onResolve }) {
  const actions = getNonInvoiceActions(item);
  const documentType = normalizeDocumentType(item?.document_type);
  return html`
    <div class="review-focus-actions">
      <div class="review-focus-question">
        <div class="review-focus-question-label">Resolve</div>
        <div class="review-focus-question-text">
          How should ${getDocumentTypeLabel(documentType).toLowerCase()} be handled?
        </div>
      </div>
      <div class="review-focus-noninvoice">
        ${actions.map((action) => {
          const key = `${item.id}:${action.id}`;
          const isResolving = resolving === key;
          return html`
            <button
              key=${action.id}
              class="review-noninvoice-option"
              onClick=${() => onResolve(item, action)}
              disabled=${!!resolving}>
              <span class="review-noninvoice-label">${action.label}</span>
              <span class="review-noninvoice-state">${isResolving ? 'Saving…' : '→'}</span>
            </button>
          `;
        })}
      </div>
    </div>
  `;
}


// ─── Open-record fallback for sections we don't focus on ────

function OpenRecordActions({ section, onOpenRecord }) {
  const copy = section === 'needs_info'
    ? 'Vendor follow-up. Review the question and reply to the vendor from the record page.'
    : section === 'failed_post'
      ? 'ERP posting failed. Open the record to inspect the connector error and retry.'
      : 'Policy or exception review. Open the record for full context and resolution.';
  return html`
    <div class="review-focus-actions">
      <p class="review-focus-summary" style="margin-top:0">${copy}</p>
      <div style="display:flex;justify-content:center;margin-top:12px">
        <button class="btn-primary" onClick=${onOpenRecord}>Open record</button>
      </div>
    </div>
  `;
}


// ─── Queue peek (small "up next" footer) ────────────────────

function QueuePeek({ queue, focusedIndex }) {
  if (queue.length <= 1) return null;
  const upcoming = [];
  for (let i = 1; i <= 3; i += 1) {
    const next = queue[(focusedIndex + i) % queue.length];
    if (next && !upcoming.find((u) => u.id === next.id)) upcoming.push(next);
  }
  if (upcoming.length === 0) return null;
  return html`
    <div class="review-queue-peek">
      <div class="review-queue-peek-label">Up next</div>
      <ul class="review-queue-peek-list">
        ${upcoming.map((item) => html`
          <li class="review-queue-peek-item" key=${item.id}>
            <span class="review-queue-peek-vendor">${safeDisplayText(item.vendor_name, 'Unknown vendor')}</span>
            <span class="muted">·</span>
            <span class="muted">${SECTION_LABELS[classifyReviewSection(item)] || 'Review'}</span>
            <span class="muted">·</span>
            <span class="muted">${safeDisplayText(formatAmount(item.amount, item.currency), '—')}</span>
          </li>
        `)}
      </ul>
    </div>
  `;
}


// ─── Empty state (queue cleared) ────────────────────────────

function EmptyState({ totalReviewed, sessionStartTs, onRefresh, onPipeline, hadItems, skippedCount, onUnskip }) {
  const elapsed = Date.now() - sessionStartTs;
  const mins = Math.floor(elapsed / 60000);
  const secs = Math.floor((elapsed % 60000) / 1000);
  const elapsedLabel = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  const cleared = totalReviewed > 0;

  if (hadItems) {
    return html`
      <div class="review-empty-card">
        <h3>You skipped ${skippedCount} item${skippedCount === 1 ? '' : 's'}.</h3>
        <p class="muted">Bring them back if you're ready to clear them now, or refresh to pull anything new.</p>
        <div class="review-empty-actions">
          <button class="btn-primary" onClick=${onUnskip}>Show skipped (${skippedCount})</button>
          <button class="btn-secondary" onClick=${onRefresh}>Refresh</button>
        </div>
      </div>
    `;
  }

  return html`
    <div class="review-empty-card review-empty-card-celebrate">
      <div class="review-empty-checkmark">✓</div>
      <h3>${cleared ? 'Queue cleared.' : 'Nothing needs review right now.'}</h3>
      <p class="muted">
        ${cleared
          ? `You worked through ${totalReviewed} item${totalReviewed === 1 ? '' : 's'} in ${elapsedLabel}.`
          : 'Anything that needs your judgment will land here automatically.'}
      </p>
      <div class="review-empty-actions">
        <button class="btn-secondary" onClick=${onRefresh}>Check for new</button>
        <button class="btn-ghost" onClick=${onPipeline}>Open pipeline</button>
      </div>
    </div>
  `;
}


// ─── Disputes panel (kept from prior implementation) ───────

function DisputesPanel({ api, orgId, navigate }) {
  const [summary, setSummary] = useState(null);
  const [disputes, setDisputes] = useState([]);
  useEffect(() => {
    if (!api) return;
    api(`/api/workspace/disputes/summary?organization_id=${encodeURIComponent(orgId)}`).then(setSummary).catch(() => {});
    api(`/api/workspace/disputes?organization_id=${encodeURIComponent(orgId)}&limit=10`).then((d) => setDisputes(d?.disputes || [])).catch(() => {});
  }, [api, orgId]);
  const open = disputes.filter((d) => !['resolved', 'closed'].includes(d.status));
  if (!summary || summary.total === 0) return null;
  return html`
    <div class="review-disputes-panel">
      <h3 style="margin:0 0 8px;font-size:13px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;color:var(--cl-ink-muted)">
        Active disputes (${summary.open_count || 0})
      </h3>
      ${open.slice(0, 5).map((d) => html`
        <div key=${d.id} class="review-dispute-row">
          <strong>${d.vendor_name || 'Unknown'}</strong>
          <span class="muted">${(d.dispute_type || '').replace(/_/g, ' ')}</span>
          <span class=${`review-dispute-status review-dispute-status-${d.status === 'escalated' ? 'warn' : 'info'}`}>${(d.status || '').replace(/_/g, ' ')}</span>
        </div>
      `)}
    </div>
  `;
}
