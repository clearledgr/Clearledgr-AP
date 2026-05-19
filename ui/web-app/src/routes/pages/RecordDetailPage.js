/**
 * Record detail — intervention surface for an escalated AP record.
 *
 * The workspace is the coordination-layer control center, not a
 * workflow desktop. Routine approve / reject / post / snooze decisions
 * belong to Slack and Teams approval cards (where the approver lives)
 * and to Gmail (where vendor follow-up lives). This page is for:
 *
 *   1. Reading the agent's reasoning + audit trail (always available)
 *   2. Intervening when the agent has escalated the record to a human
 *      (gated by escalation state OR an open exception)
 *   3. Reversing an autonomous ERP post within the 15-minute override
 *      window (gated by posted_to_erp + clock)
 *
 * The visible action set is intentionally narrow: Escalate to
 * controller, Reassign approver, Reclassify, Resubmit, Reverse
 * posting, Mark as duplicate, Override and post. Approve / Reject /
 * Send-back / Post-to-ERP / Snooze are stripped — those happen in
 * the render targets, not here.
 *
 * Sub-components are colocated because they all consume the same
 * detail payload — splitting across files would force prop-drilling
 * the same object through five wrapper layers without buying anything.
 *
 * Intervention actions route through `runtime.execute_intent` via
 * `/api/agent/intents/execute`, the same path Slack/Teams/Gmail use,
 * so every workspace decision lands on the canonical audit chain.
 */
import { h } from 'preact';
import { useCallback, useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDate, fmtDateTime } from '../route-helpers.js';
import {
  formatAmount,
  getStateLabel,
} from '../../utils/formatters.js';

const html = htm.bind(h);


// ─── Constants ──────────────────────────────────────────────────────

// Operator-facing label for every intervention intent the workspace
// surfaces. These are the judgment calls that legitimately happen in
// the control center — handoffs, reclassification, override-post,
// and the override-window reversal.
//
// Approve / Reject / Send-back / Post-to-ERP / Snooze / request_info
// are intentionally absent: those are routine workflow decisions and
// belong in Slack / Teams / Gmail, not the workspace.
const INTENT_LABELS = {
  escalate_approval: 'Escalate to controller',
  reassign_approval: 'Reassign approver',
  manually_classify_invoice: 'Reclassify',
  resubmit_invoice: 'Resubmit',
  reverse_invoice_post: 'Reverse posting',
};

// Whitelist enforced on the frontend. `actions.available` from the
// backend is the upper bound (it knows state-validity); this set is
// the policy bound (what the workspace surfaces vs. delegates to the
// render targets).
const INTERVENTION_INTENTS = new Set(Object.keys(INTENT_LABELS));

// Workflow states where the agent has handed the record to a human.
// In any of these states, the intervention bar may show.
const ESCALATED_STATES = new Set([
  'needs_info',
  'needs_approval',
  'pending_approval',
  'needs_second_approval',
  'failed_post',
]);

// 15-minute override window for autonomous ERP posts. After this
// elapsed time, the post is finalized and only an out-of-band
// intervention (manual credit note, etc.) can undo it.
const OVERRIDE_WINDOW_MS = 15 * 60 * 1000;

// Two named intervention affordances that route existing intents with
// extra payload. Each requires a small dialog (duplicate target /
// override reason) before submission.
//   - mark_duplicate  → reject_invoice + duplicate metadata
//   - override_post   → post_to_erp + override_validation flag
const SPECIAL_ACTIONS = {
  mark_duplicate: {
    label: 'Mark as duplicate',
    intent: 'reject_invoice',
    requiresState: new Set(['received', 'validated', 'needs_info', 'needs_approval', 'pending_approval']),
  },
  override_post: {
    label: 'Override and post',
    intent: 'post_to_erp',
    requiresState: new Set(['received', 'validated', 'needs_info', 'needs_approval', 'pending_approval', 'approved', 'ready_to_post', 'failed_post']),
  },
};

function isEscalated(item) {
  if (!item) return false;
  const state = String(item.state || '').toLowerCase();
  if (ESCALATED_STATES.has(state)) return true;
  if (item.exception_code) return true;
  if (item.has_unresolved_exception) return true;
  return false;
}

function overrideWindowStatus(item) {
  if (!item) return null;
  const state = String(item.state || '').toLowerCase();
  if (state !== 'posted_to_erp') return null;
  const postedAt = item.posted_to_erp_at || item.posted_at || item.erp_posted_at;
  if (!postedAt) return null;
  const ms = new Date(postedAt).getTime();
  if (!Number.isFinite(ms)) return null;
  const elapsed = Date.now() - ms;
  if (elapsed < 0 || elapsed >= OVERRIDE_WINDOW_MS) return null;
  return {
    minutesRemaining: Math.max(0, Math.ceil((OVERRIDE_WINDOW_MS - elapsed) / 60000)),
    postedAt,
  };
}

// Render-target handoff. Returns whichever links are present on the
// detail payload — never fabricates URLs.
function getHandoffLinks(item) {
  if (!item) return [];
  const links = [];
  if (item.slack_thread_url) {
    links.push({ kind: 'slack', label: 'Open in Slack', url: item.slack_thread_url });
  }
  if (item.teams_thread_url) {
    links.push({ kind: 'teams', label: 'Open in Teams', url: item.teams_thread_url });
  }
  if (item.gmail_thread_url) {
    links.push({ kind: 'gmail', label: 'Open in Gmail', url: item.gmail_thread_url });
  } else if (item.thread_id) {
    // The agent stores a Gmail thread id; build the canonical URL.
    links.push({
      kind: 'gmail',
      label: 'Open in Gmail',
      url: `https://mail.google.com/mail/u/0/#all/${encodeURIComponent(item.thread_id)}`,
    });
  }
  if (item.erp_invoice_url) {
    links.push({ kind: 'erp', label: 'Open in ERP', url: item.erp_invoice_url });
  }
  return links;
}

const VERDICT_TONE = {
  allow: 'success',
  permitted: 'success',
  block: 'error',
  blocked: 'error',
  escalate: 'warning',
  veto: 'warning',
};

const RECOMMENDATION_TONE = {
  approve: 'success',
  needs_info: 'warning',
  escalate: 'warning',
  reject: 'error',
};

const ERP_NAMES = {
  quickbooks: 'QuickBooks',
  xero: 'Xero',
  netsuite: 'NetSuite',
  sap: 'SAP',
};

function formatErpName(erpType) {
  if (!erpType) return 'ERP';
  return ERP_NAMES[String(erpType).toLowerCase()] || 'ERP';
}


// ─── Top-level page ─────────────────────────────────────────────────

export default function RecordDetailPage({
  api, orgId, navigate, toast, recordId,
}) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionBusy, setActionBusy] = useState(false);

  const loadDetail = useCallback(async () => {
    if (!recordId) {
      setError('No record id supplied');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await api(
        `/api/workspace/ap-items/${encodeURIComponent(recordId)}/detail`,
      );
      setDetail(resp);
    } catch (exc) {
      const message = String(exc?.message || exc || 'Failed to load record');
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [api, recordId]);

  useEffect(() => { loadDetail(); }, [loadDetail]);

  const onIntent = useCallback(async (intent, extraInput = {}) => {
    if (!detail || actionBusy) return;
    setActionBusy(true);
    try {
      await api('/api/agent/intents/execute', {
        method: 'POST',
        body: JSON.stringify({
          intent,
          organization_id: orgId,
          input: {
            ap_item_id: detail.item.id,
            actor: 'workspace_detail',
            ...extraInput,
          },
        }),
      });
      toast(`${INTENT_LABELS[intent] || intent} recorded.`, 'success');
      await loadDetail();
    } catch (exc) {
      toast(`Action failed: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setActionBusy(false);
    }
  }, [api, orgId, detail, loadDetail, toast, actionBusy]);

  const onBack = useCallback(() => {
    navigate('/records');
  }, [navigate]);

  if (loading && !detail) {
    return html`<${LoadingState} />`;
  }
  if (error && !detail) {
    return html`<${ErrorState} message=${error} onRetry=${loadDetail} onBack=${onBack} />`;
  }
  if (!detail) {
    return html`<${ErrorState} message="Record not found" onRetry=${loadDetail} onBack=${onBack} />`;
  }

  const { item, reasoning, match, timeline, actions } = detail;
  const handoffLinks = getHandoffLinks(item);

  return html`
    <div class="cl-record-detail">
      <header class="cl-record-detail-topbar">
        <button class="cl-record-back" onClick=${onBack} aria-label="Back to records">
          ← Records
        </button>
        <span class="cl-record-detail-id">${item.id}</span>
      </header>

      <${RecordHeader} item=${item} />

      <${HandoffStrip} links=${handoffLinks} />

      <${ActionBar}
        actions=${actions}
        onIntent=${onIntent}
        item=${item}
        busy=${actionBusy}
        handoffLinks=${handoffLinks}
      />

      <${RecordStatePanel} item=${item} timeline=${timeline} />

      <${AgentReasoningPanel}
        reasoning=${reasoning}
        item=${item}
      />

      <${AskTheAgentPanel}
        api=${api}
        recordId=${recordId}
        toast=${toast}
      />

      <div class="cl-record-detail-grid">
        <${BillDetailPanel} item=${item} />
        <${ThreeWayMatchPanel} match=${match} />
      </div>

      <${BankMatchPanel} api=${api} recordId=${recordId} item=${item} />

      <${WorkflowTimeline} events=${timeline} />
    </div>
  `;
}


// ─── Handoff strip ──────────────────────────────────────────────────
//
// Always visible (when at least one render target link is on the
// payload). Routine approve / reject / send-back / post / snooze
// decisions don't happen in the workspace — they happen in Slack /
// Teams / Gmail / the ERP. This strip is the durable reminder of
// where those decisions live, plus the deep links to get there.

function HandoffStrip({ links }) {
  if (!Array.isArray(links) || links.length === 0) return null;
  return html`
    <section class="cl-record-handoff" aria-label="Render target handoffs">
      <div class="cl-record-handoff-copy">
        <strong>Day-to-day decisions live in the render targets.</strong>
        <p class="cl-record-handoff-sub">
          Approvals in Slack and Teams. Vendor follow-up in Gmail. Posting in your ERP.
          Use this page when the agent escalates or you need to intervene.
        </p>
      </div>
      <div class="cl-record-handoff-links">
        ${links.map((link) => html`
          <a
            key=${link.kind}
            class=${`cl-record-handoff-link cl-record-handoff-link-${link.kind}`}
            href=${link.url}
            target="_blank"
            rel="noreferrer noopener"
          >
            ${link.label} <span aria-hidden="true">↗</span>
          </a>
        `)}
      </div>
    </section>
  `;
}


// ─── States ─────────────────────────────────────────────────────────

function LoadingState() {
  return html`
    <div class="cl-record-detail cl-record-detail-loading" role="status" aria-live="polite">
      <div class="cl-record-skeleton cl-record-skeleton-header"></div>
      <div class="cl-record-skeleton cl-record-skeleton-action-bar"></div>
      <div class="cl-record-skeleton cl-record-skeleton-panel"></div>
      <div class="cl-record-skeleton-grid">
        <div class="cl-record-skeleton cl-record-skeleton-panel"></div>
        <div class="cl-record-skeleton cl-record-skeleton-panel"></div>
      </div>
    </div>
  `;
}

function ErrorState({ message, onRetry, onBack }) {
  return html`
    <div class="cl-record-detail cl-record-detail-error" role="alert">
      <h2>Couldn't load this record</h2>
      <p class="cl-record-detail-error-message">${message}</p>
      <div class="cl-record-detail-error-actions">
        <button class="btn btn-primary" onClick=${onRetry}>Try again</button>
        <button class="btn btn-secondary" onClick=${onBack}>Back to records</button>
      </div>
    </div>
  `;
}


// ─── Header ─────────────────────────────────────────────────────────

function RecordHeader({ item }) {
  const stateLabel = getStateLabel(item.state || 'received') || item.state || 'received';
  const ageDays = computeAgeDays(item.created_at);

  return html`
    <section class="cl-record-header">
      <div class="cl-record-header-primary">
        <div class="cl-record-header-vendor">
          <span class="cl-record-header-eyebrow">Vendor</span>
          <h1 class="cl-record-header-vendor-name">
            ${item.vendor_name || item.vendor || 'Vendor not extracted'}
          </h1>
        </div>
        <div class="cl-record-header-amount">
          <span class="cl-record-header-eyebrow">Amount</span>
          <span class="cl-record-header-money">
            ${formatAmount(item.amount, item.currency)}
          </span>
        </div>
      </div>
      <dl class="cl-record-header-meta">
        <div class="cl-record-header-meta-cell">
          <dt>Invoice number</dt>
          <dd><code>${item.invoice_number || '—'}</code></dd>
        </div>
        <div class="cl-record-header-meta-cell">
          <dt>Status</dt>
          <dd>
            <span class=${`cl-record-state cl-record-state-${item.state || 'received'}`}>
              ${stateLabel}
            </span>
          </dd>
        </div>
        <div class="cl-record-header-meta-cell">
          <dt>Due</dt>
          <dd>${item.due_date ? fmtDate(item.due_date) : '—'}</dd>
        </div>
        <div class="cl-record-header-meta-cell">
          <dt>Age</dt>
          <dd>${ageDays !== null ? `${ageDays}d` : '—'}</dd>
        </div>
        ${item.entity_name ? html`
          <div class="cl-record-header-meta-cell">
            <dt>Entity</dt>
            <dd>${item.entity_name}</dd>
          </div>` : null}
      </dl>
    </section>
  `;
}


// ─── Action bar — intervention-only ─────────────────────────────────
//
// Renders only when the workspace has a legitimate reason to surface
// mutating affordances:
//   - The agent has escalated this record (state in ESCALATED_STATES
//     or there is an open exception). The intervention set is the
//     judgment work that doesn't fit Slack/Teams approval cards:
//     reassign, escalate further, reclassify, resubmit after the
//     external resolution, mark-duplicate, override-and-post.
//   - The record was just autonomously posted to the ERP and the
//     15-minute override window is still open. The only affordance
//     here is "Reverse posting"; everything else is finalized.
//
// In the default state (received, validated, approved, ready_to_post,
// posted_to_erp-after-window, snoozed, closed, reversed, etc.) no
// action bar renders. The handoff strip above is the durable answer
// for where to act.

function ActionBar({ actions, onIntent, item, busy, handoffLinks }) {
  const available = Array.isArray(actions?.available) ? actions.available : [];
  const primary = actions?.primary || null;
  const [dialog, setDialog] = useState(null);
  const state = String(item?.state || '').toLowerCase();

  const escalated = isEscalated(item);
  const overrideWindow = overrideWindowStatus(item);

  // Filter against the workspace policy whitelist.
  const interventionAvailable = available.filter((intent) => INTERVENTION_INTENTS.has(intent));
  const interventionPrimary = primary && INTERVENTION_INTENTS.has(primary) ? primary : null;

  const reverseAvailable = interventionAvailable.includes('reverse_invoice_post');
  const showMarkDuplicate = escalated && SPECIAL_ACTIONS.mark_duplicate.requiresState.has(state);
  const showOverridePost = escalated && SPECIAL_ACTIONS.override_post.requiresState.has(state);

  // If nothing applies, render nothing. The HandoffStrip already
  // covers the "decisions live elsewhere" message.
  const hasIntervention = escalated && (
    interventionAvailable.length > 0 || showMarkDuplicate || showOverridePost
  );
  const hasReverse = !!overrideWindow && reverseAvailable;
  if (!hasIntervention && !hasReverse) return null;

  const orderedSecondary = interventionAvailable
    .filter((intent) => intent !== interventionPrimary)
    // Reverse posting renders in its own (window-gated) row, not the
    // intervention grid.
    .filter((intent) => intent !== 'reverse_invoice_post');

  const openDialog = (kind, intent) => setDialog({ kind, intent });
  const closeDialog = () => setDialog(null);
  const submitDialog = async (values) => {
    if (!dialog) return;
    await onIntent(dialog.intent, values);
    setDialog(null);
  };

  const intentClick = (intent) => {
    if (intent === 'reassign_approval') return openDialog('reassign_approval', intent);
    return onIntent(intent);
  };

  const escalationReason = describeEscalation(item);

  return html`
    <section class="cl-record-action-bar" role="toolbar" aria-label="Intervention actions">
      ${hasReverse ? html`
        <div class="cl-record-action-window">
          <div class="cl-record-action-window-copy">
            <strong>Override window open · ${overrideWindow.minutesRemaining} min left.</strong>
            <p>
              This record was autonomously posted to the ERP. You can reverse the post
              before the window expires; after that the post is finalized.
            </p>
          </div>
          <button
            class="btn btn-warning cl-record-action-reverse"
            onClick=${() => onIntent('reverse_invoice_post')}
            disabled=${busy}>
            ${busy ? 'Working…' : 'Reverse posting'}
          </button>
        </div>
      ` : null}

      ${hasIntervention ? html`
        <div class="cl-record-action-escalation">
          <div class="cl-record-action-escalation-copy">
            <strong>Agent escalated this to you.</strong>
            <p>${escalationReason}</p>
            ${handoffLinks?.length ? html`
              <p class="cl-record-action-escalation-aside">
                If this should be handled by the approver or vendor instead,
                hand off via Slack, Teams, or Gmail above.
              </p>
            ` : null}
          </div>
          <div class="cl-record-action-grid">
            ${interventionPrimary ? html`
              <button
                class="btn btn-primary cl-record-action-primary"
                onClick=${() => intentClick(interventionPrimary)}
                disabled=${busy}>
                ${INTENT_LABELS[interventionPrimary] || interventionPrimary}
              </button>
            ` : null}
            ${orderedSecondary.map((intent) => html`
              <button
                key=${intent}
                class="btn btn-secondary"
                onClick=${() => intentClick(intent)}
                disabled=${busy}>
                ${INTENT_LABELS[intent] || intent}
              </button>
            `)}
            ${showMarkDuplicate ? html`
              <button
                class="btn btn-secondary"
                onClick=${() => openDialog('mark_duplicate', 'reject_invoice')}
                disabled=${busy}>
                Mark as duplicate
              </button>
            ` : null}
            ${showOverridePost ? html`
              <button
                class="btn btn-secondary cl-record-action-override"
                onClick=${() => openDialog('override_post', 'post_to_erp')}
                disabled=${busy}>
                Override and post
              </button>
            ` : null}
          </div>
        </div>
      ` : null}

      ${dialog ? html`<${ActionDialog} kind=${dialog.kind} onCancel=${closeDialog} onSubmit=${submitDialog} busy=${busy} />` : null}
    </section>
  `;
}

// Human-readable reason the intervention bar is visible.
function describeEscalation(item) {
  if (!item) return 'The agent flagged this for human judgment.';
  const state = String(item.state || '').toLowerCase();
  if (item.exception_code) {
    const code = String(item.exception_code).replace(/_/g, ' ');
    return `Exception raised: ${code}. The agent can't progress this record without a human call.`;
  }
  if (state === 'needs_info') {
    return 'The agent needs information from the vendor or operator before this record can move forward. Respond in Gmail; the agent will resubmit when the answer arrives.';
  }
  if (state === 'needs_approval' || state === 'pending_approval') {
    return 'The record is waiting on an approver. The approver should respond in Slack or Teams; intervene here only to reassign, escalate, or override.';
  }
  if (state === 'needs_second_approval') {
    return 'High-value record awaiting a second, distinct approver. The next approver should respond in Slack or Teams; intervene here only to reassign or escalate.';
  }
  if (state === 'failed_post') {
    return 'Posting to the ERP failed. Investigate, reclassify if needed, or override and post once you have a real reason.';
  }
  return 'The agent escalated this record. Apply your judgment, or hand off to a render target.';
}


function ActionDialog({ kind, onCancel, onSubmit, busy }) {
  const [email, setEmail] = useState('');
  const [note, setNote] = useState('');
  const [duplicateOf, setDuplicateOf] = useState('');
  const [overrideReason, setOverrideReason] = useState('');

  const onConfirm = (e) => {
    e?.preventDefault?.();
    if (kind === 'reassign_approval') {
      const trimmed = email.trim();
      if (!trimmed) return;
      onSubmit({ assignee: trimmed, note: note.trim() || undefined });
    } else if (kind === 'mark_duplicate') {
      const dup = duplicateOf.trim();
      const reason = dup
        ? `Marked as duplicate of ${dup}${note.trim() ? `: ${note.trim()}` : ''}`
        : `Marked as duplicate${note.trim() ? `: ${note.trim()}` : ''}`;
      if (!dup && !note.trim()) return; // require at least one piece of info
      onSubmit({
        reason,
        metadata: dup ? { duplicate_of_ap_item_id: dup, duplicate_note: note.trim() || undefined } : { duplicate_note: note.trim() },
      });
    } else if (kind === 'override_post') {
      const reason = overrideReason.trim();
      if (!reason) return;
      onSubmit({ override_validation: true, override_reason: reason });
    }
  };

  const titles = {
    reassign_approval: 'Reassign the current approval step',
    mark_duplicate: 'Mark as duplicate',
    override_post: 'Override validation and post anyway',
  };
  const subs = {
    reassign_approval: 'Hands off the current approval step to the named approver. Recorded in the audit trail.',
    mark_duplicate: 'Closes this invoice with a duplicate marker. Optionally link the original.',
    override_post: 'Skips the eligibility gate and posts to the ERP. The override + reason are recorded; CFO and audit trail will see this.',
  };

  return html`
    <div class="cl-record-dialog-backdrop" onClick=${onCancel}>
      <form class="cl-record-dialog" onClick=${(e) => e.stopPropagation()} onSubmit=${onConfirm}>
        <header class="cl-record-dialog-head">
          <h3>${titles[kind]}</h3>
          <p class="cl-record-dialog-sub">${subs[kind]}</p>
        </header>
        <div class="cl-record-dialog-body">
          ${kind === 'reassign_approval' ? html`
            <label class="cl-record-dialog-field">
              <span>Approver email</span>
              <input
                type="email"
                placeholder="approver@company.com"
                value=${email}
                onInput=${(e) => setEmail(e.target.value)}
                required
                autoFocus />
            </label>
            <label class="cl-record-dialog-field">
              <span>Note (optional)</span>
              <textarea
                placeholder="Why this person? Any context they need."
                rows="3"
                value=${note}
                onInput=${(e) => setNote(e.target.value)}></textarea>
            </label>
          ` : null}
          ${kind === 'mark_duplicate' ? html`
            <label class="cl-record-dialog-field">
              <span>Original invoice ID (if known)</span>
              <input
                type="text"
                placeholder="AP-..."
                value=${duplicateOf}
                onInput=${(e) => setDuplicateOf(e.target.value)}
                autoFocus />
            </label>
            <label class="cl-record-dialog-field">
              <span>Note</span>
              <textarea
                placeholder="Why is this a duplicate? (Required if no original ID.)"
                rows="3"
                value=${note}
                onInput=${(e) => setNote(e.target.value)}></textarea>
            </label>
          ` : null}
          ${kind === 'override_post' ? html`
            <div class="cl-record-dialog-warning">
              <strong>This skips validation.</strong> The CFO and external auditor will see the override + your stated reason. Use only when you have a real reason.
            </div>
            <label class="cl-record-dialog-field">
              <span>Reason for override</span>
              <textarea
                placeholder="e.g. Contract signed off-system; finance approved verbally on 2026-04-30."
                rows="4"
                value=${overrideReason}
                onInput=${(e) => setOverrideReason(e.target.value)}
                required
                autoFocus></textarea>
            </label>
          ` : null}
        </div>
        <footer class="cl-record-dialog-foot">
          <button type="button" class="btn btn-tertiary" onClick=${onCancel} disabled=${busy}>Cancel</button>
          <button type="submit" class="btn btn-primary" disabled=${busy}>
            ${busy ? 'Working…' : 'Confirm'}
          </button>
        </footer>
      </form>
    </div>
  `;
}


// ─── Record state panel — what this record IS, beyond the operator view ──
// Owner / waiting on / exception / history / policy. The fields exist on
// ap_items (owner_email v84, waiting_condition v59, exception_code legacy)
// and on audit_events (policy_version v83), but no other panel surfaces
// them as a unified spec view. Matches the marketing card's bottom half.

function RecordStatePanel({ item, timeline }) {
  const events = Array.isArray(timeline) ? timeline : [];
  const ownerEmail = item?.owner_email || '';
  const exceptionCode = String(item?.exception_code || '').trim().toLowerCase();
  const stateLower = String(item?.state || '').toLowerCase();
  const isSealed = ['closed', 'sealed', 'rejected', 'paid'].includes(stateLower);

  // waiting_condition is JSON: {type, expected_by, context}. Sometimes
  // already-parsed, sometimes a JSON string — handle both.
  let waiting = item?.waiting_condition;
  if (typeof waiting === 'string') {
    try { waiting = JSON.parse(waiting); } catch (_) { waiting = null; }
  }
  const waitingDescription = formatWaitingCondition(waiting);

  // Policy version: take the most recent event with one. v83 stamps
  // policy_version on every state-changing audit_event.
  const policyEvent = events.find((e) => e && (e.policy_version != null));
  const policyVersion = policyEvent?.policy_version != null
    ? `v${policyEvent.policy_version}`
    : null;

  // Nothing to surface? Skip the panel entirely. Empty rows make the
  // detail page look broken; missing-by-design is better than empty.
  const rows = [
    ownerEmail ? { label: 'Owner', value: ownerEmail } : null,
    waitingDescription ? { label: 'Waiting on', value: waitingDescription } : null,
    exceptionCode ? { label: 'Exception', value: html`<span class="cl-record-spec-exception">${exceptionCode}</span>` } : null,
    events.length > 0
      ? { label: 'History', value: `${events.length} event${events.length === 1 ? '' : 's'}${isSealed ? ' · sealed' : ''}` }
      : null,
    policyVersion
      ? { label: 'Policy', value: html`<code class="cl-record-spec-code">${policyVersion}</code> · stamped on transition` }
      : null,
  ].filter(Boolean);

  if (rows.length === 0) return null;

  return html`
    <section class="cl-record-panel cl-record-spec" aria-label="Record state">
      <header class="cl-record-panel-head">
        <h2>Record state</h2>
      </header>
      <dl class="cl-record-spec-grid">
        ${rows.map((row) => html`
          <div class="cl-record-spec-row" key=${row.label}>
            <dt>${row.label}</dt>
            <dd>${row.value}</dd>
          </div>
        `)}
      </dl>
    </section>
  `;
}

function formatWaitingCondition(waiting) {
  if (!waiting || typeof waiting !== 'object') return '';
  const ctx = waiting.context || {};
  // Common shapes from the agent: reasons + expected_by + a po_ref.
  const reasons = Array.isArray(ctx.reasons) ? ctx.reasons : (Array.isArray(waiting.reasons) ? waiting.reasons : []);
  const ref = ctx.po_ref || ctx.po_number || ctx.invoice_ref || waiting.po_ref || '';
  const parts = [];
  if (reasons.length) parts.push(reasons.join(' · '));
  if (ref) parts.push(typeof ref === 'string' && ref.startsWith('PO') ? ref : `PO #${ref}`);
  if (!parts.length && waiting.type) parts.push(String(waiting.type).replace(/_/g, ' '));
  return parts.join(' · ');
}


// ─── Agent reasoning panel — the centerpiece ────────────────────────

function AgentReasoningPanel({ reasoning, item }) {
  const decision = reasoning?.agent_decision || {};
  const governance = reasoning?.governance;
  const sources = reasoning?.sources || {};
  const narrative = reasoning?.narrative;

  const recTone = RECOMMENDATION_TONE[(decision.recommendation || '').toLowerCase()] || 'info';
  const verdictTone = governance
    ? VERDICT_TONE[(governance.verdict || '').toLowerCase()] || 'info'
    : null;

  return html`
    <section class="cl-record-panel cl-record-reasoning">
      <header class="cl-record-panel-head">
        <h2>Agent reasoning</h2>
        <span class="cl-record-panel-eyebrow">${decision.model || 'rules'}</span>
      </header>

      ${narrative ? html`
        <p class="cl-record-reasoning-narrative">${narrative}</p>
      ` : null}

      <dl class="cl-record-reasoning-summary">
        <div class="cl-record-reasoning-cell">
          <dt>Recommendation</dt>
          <dd>
            ${decision.recommendation
              ? html`<span class=${`cl-record-chip cl-record-chip-${recTone}`}>
                  ${formatRecommendation(decision.recommendation)}
                </span>`
              : html`<span class="cl-record-muted">No decision recorded yet</span>`}
          </dd>
        </div>
        ${governance ? html`
          <div class="cl-record-reasoning-cell">
            <dt>Governance verdict</dt>
            <dd>
              <span class=${`cl-record-chip cl-record-chip-${verdictTone}`}>
                ${governance.verdict || '—'}
              </span>
              ${governance.recorded_at ? html`
                <span class="cl-record-muted cl-record-tiny">
                  ${fmtDateTime(governance.recorded_at)}
                </span>` : null}
            </dd>
          </div>
          ${typeof governance.agent_confidence === 'number' ? html`
            <div class="cl-record-reasoning-cell">
              <dt>Agent confidence</dt>
              <dd>
                <${ConfidenceBar} value=${governance.agent_confidence} />
              </dd>
            </div>` : null}
        ` : null}
        ${decision.risk_flags && decision.risk_flags.length > 0 ? html`
          <div class="cl-record-reasoning-cell cl-record-reasoning-cell-wide">
            <dt>Risk flags</dt>
            <dd class="cl-record-reasoning-flags">
              ${decision.risk_flags.map((flag) => html`
                <span class="cl-record-chip cl-record-chip-warning" key=${flag}>
                  ${formatRiskFlag(flag)}
                </span>
              `)}
            </dd>
          </div>` : null}
      </dl>

      <${SourcesSection} sources=${sources} item=${item} />

      ${sources.recovery_plan ? html`
        <${RecoveryPlanSection} plan=${sources.recovery_plan} />
      ` : null}
    </section>
  `;
}

function SourcesSection({ sources, item }) {
  const vendorContext = sources.vendor_context || {};
  const feedback = sources.decision_feedback || {};
  const hints = sources.single_pass_hints;
  const confidenceGate = sources.confidence_gate || {};
  const blockers = confidenceGate.confidence_blockers || [];

  const hasAnything = (
    Object.keys(vendorContext).length > 0
    || Object.keys(feedback).length > 0
    || hints
    || blockers.length > 0
  );

  if (!hasAnything) return null;

  return html`
    <details class="cl-record-reasoning-sources" open>
      <summary>Sources the agent consulted</summary>
      <div class="cl-record-reasoning-sources-grid">
        ${Object.keys(vendorContext).length > 0 ? html`
          <div class="cl-record-source-card">
            <h4>Vendor history</h4>
            <ul>
              ${vendorContext.invoice_count !== undefined ? html`
                <li>Prior invoices: <strong>${vendorContext.invoice_count}</strong></li>` : null}
              ${vendorContext.avg_invoice_amount !== undefined && vendorContext.avg_invoice_amount !== null ? html`
                <li>Average amount: <strong>${formatAmount(vendorContext.avg_invoice_amount, item?.currency)}</strong></li>` : null}
              ${vendorContext.always_approved !== undefined ? html`
                <li>100% approval history: <strong>${vendorContext.always_approved ? 'yes' : 'no'}</strong></li>` : null}
              ${vendorContext.bank_details_changed_at ? html`
                <li>Bank details last changed: <strong>${fmtDate(vendorContext.bank_details_changed_at)}</strong></li>` : null}
            </ul>
          </div>` : null}

        ${Object.keys(feedback).length > 0 ? html`
          <div class="cl-record-source-card">
            <h4>Operator feedback (180d)</h4>
            <ul>
              ${feedback.count !== undefined ? html`<li>Decisions reviewed: <strong>${feedback.count}</strong></li>` : null}
              ${feedback.override_rate !== undefined ? html`
                <li>Override rate: <strong>${(feedback.override_rate * 100).toFixed(0)}%</strong></li>` : null}
              ${feedback.strictness_bias ? html`
                <li>Bias: <strong>${feedback.strictness_bias}</strong></li>` : null}
            </ul>
          </div>` : null}

        ${hints ? html`
          <div class="cl-record-source-card">
            <h4>LLM advisory hints</h4>
            ${hints.gl_coding?.suggested_gl_code ? html`
              <p>Suggested GL: <code>${hints.gl_coding.suggested_gl_code}</code></p>` : null}
            ${hints.duplicate_analysis ? html`
              <p>Duplicate signal: <strong>${hints.duplicate_analysis.is_duplicate ? 'likely duplicate' : 'no'}</strong></p>` : null}
            ${hints.risk_assessment?.fraud_risk ? html`
              <p>Fraud risk: <strong>${hints.risk_assessment.fraud_risk}</strong>
                ${(hints.risk_assessment.fraud_signals || []).length > 0 ? html`
                  <span class="cl-record-tiny"> (${hints.risk_assessment.fraud_signals.join(', ')})</span>` : null}
              </p>` : null}
          </div>` : null}

        ${blockers.length > 0 ? html`
          <div class="cl-record-source-card cl-record-source-card-warn">
            <h4>Field review blockers</h4>
            <ul>
              ${blockers.map((blocker) => html`
                <li key=${blocker.field || JSON.stringify(blocker)}>
                  ${blocker.field ? html`<strong>${blocker.field}</strong>: ${blocker.reason || 'low confidence'}` : JSON.stringify(blocker)}
                </li>`)}
            </ul>
          </div>` : null}
      </div>
    </details>
  `;
}

function RecoveryPlanSection({ plan }) {
  const steps = (plan && plan.steps) || [];
  if (steps.length === 0) return null;

  return html`
    <details class="cl-record-reasoning-recovery">
      <summary>Suggested recovery plan</summary>
      <p class="cl-record-recovery-summary">${plan.summary}</p>
      <ol class="cl-record-recovery-steps">
        ${steps.map((step, idx) => html`
          <li key=${idx} class="cl-record-recovery-step">
            <div class="cl-record-recovery-action">
              <span class="cl-record-chip cl-record-chip-info">${step.action.replace(/_/g, ' ')}</span>
              ${step.trigger_after_hours > 0 ? html`
                <span class="cl-record-tiny">after ${step.trigger_after_hours}h</span>` : null}
            </div>
            <p class="cl-record-recovery-rationale">${step.rationale}</p>
          </li>`)}
      </ol>
    </details>
  `;
}

function ConfidenceBar({ value }) {
  const pct = Math.max(0, Math.min(100, Math.round((value || 0) * 100)));
  const tone = pct >= 85 ? 'success' : pct >= 60 ? 'warning' : 'error';
  return html`
    <div class="cl-record-confidence" aria-label=${`Agent confidence ${pct}%`}>
      <div class=${`cl-record-confidence-bar cl-record-confidence-${tone}`} style=${`width: ${pct}%`}></div>
      <span class="cl-record-confidence-label">${pct}%</span>
    </div>
  `;
}


// ─── Bill detail ────────────────────────────────────────────────────

function BillDetailPanel({ item }) {
  const lineItems = Array.isArray(item.line_items) ? item.line_items : [];
  const fields = [
    ['Invoice date', item.invoice_date ? fmtDate(item.invoice_date) : null],
    ['Due date', item.due_date ? fmtDate(item.due_date) : null],
    ['Payment terms', item.payment_terms],
    ['Subtotal', item.subtotal !== undefined && item.subtotal !== null
      ? formatAmount(item.subtotal, item.currency) : null],
    ['Tax', item.tax_amount !== undefined && item.tax_amount !== null
      ? formatAmount(item.tax_amount, item.currency) : null],
    ['Total', formatAmount(item.amount, item.currency)],
    ['PO number', item.po_number],
    ['GL code', item.gl_code],
    ['Department', item.department],
    ['Memo', item.memo],
  ].filter(([, value]) => value !== null && value !== undefined && value !== '');

  const bankDetails = item.bank_details;

  return html`
    <section class="cl-record-panel cl-record-bill-detail">
      <header class="cl-record-panel-head">
        <h2>Bill detail</h2>
      </header>
      <dl class="cl-record-bill-grid">
        ${fields.map(([label, value]) => html`
          <div class="cl-record-bill-cell" key=${label}>
            <dt>${label}</dt>
            <dd>${value}</dd>
          </div>`)}
      </dl>

      ${lineItems.length > 0 ? html`
        <div class="cl-record-bill-lines">
          <h3>Line items</h3>
          <table class="cl-record-bill-line-table">
            <thead>
              <tr>
                <th>Description</th>
                <th class="cl-record-num">Qty</th>
                <th class="cl-record-num">Unit price</th>
                <th class="cl-record-num">Amount</th>
                <th>GL</th>
              </tr>
            </thead>
            <tbody>
              ${lineItems.map((line, idx) => html`
                <tr key=${idx}>
                  <td>${line.description || '—'}</td>
                  <td class="cl-record-num">${line.quantity ?? '—'}</td>
                  <td class="cl-record-num">${line.unit_price !== undefined ? formatAmount(line.unit_price, item.currency) : '—'}</td>
                  <td class="cl-record-num">${line.amount !== undefined ? formatAmount(line.amount, item.currency) : '—'}</td>
                  <td>${line.gl_code || '—'}</td>
                </tr>`)}
            </tbody>
          </table>
        </div>
      ` : null}

      ${bankDetails && hasAnyBankDetail(bankDetails) ? html`
        <div class="cl-record-bill-bank">
          <h3>Bank details</h3>
          <dl>
            ${bankDetails.bank_name ? html`<div><dt>Bank</dt><dd>${bankDetails.bank_name}</dd></div>` : null}
            ${bankDetails.iban_masked ? html`<div><dt>IBAN</dt><dd><code>${bankDetails.iban_masked}</code></dd></div>` : null}
            ${bankDetails.account_number_masked ? html`<div><dt>Account</dt><dd><code>${bankDetails.account_number_masked}</code></dd></div>` : null}
            ${bankDetails.swift ? html`<div><dt>SWIFT</dt><dd><code>${bankDetails.swift}</code></dd></div>` : null}
          </dl>
        </div>
      ` : null}
    </section>
  `;
}


// ─── 3-way match ────────────────────────────────────────────────────

function ThreeWayMatchPanel({ match }) {
  if (!match) {
    return html`
      <section class="cl-record-panel cl-record-three-way">
        <header class="cl-record-panel-head">
          <h2>3-way match</h2>
        </header>
        <p class="cl-record-empty">No PO data available for this invoice.</p>
      </section>
    `;
  }

  const statusTone = matchStatusTone(match.match_status);
  const lineBreakdown = match.line_breakdown || [];

  return html`
    <section class="cl-record-panel cl-record-three-way">
      <header class="cl-record-panel-head">
        <h2>3-way match</h2>
        <span class=${`cl-record-chip cl-record-chip-${statusTone}`}>
          ${formatMatchStatus(match.match_status)}
        </span>
      </header>
      <dl class="cl-record-three-way-grid">
        <div><dt>PO</dt><dd>
          ${match.po_number
            ? (match.po_url
                ? html`<a class="cl-record-erp-link" href=${match.po_url} target="_blank" rel="noreferrer noopener" title="Open in ${formatErpName(match.erp_type)}"><code>${match.po_number}</code> ↗</a>`
                : html`<code>${match.po_number}</code>`)
            : '—'}
        </dd></div>
        <div><dt>Invoice</dt><dd>${formatAmount(match.invoice_amount, match.currency)}</dd></div>
        <div><dt>PO amount</dt><dd>${match.po_amount !== null && match.po_amount !== undefined ? formatAmount(match.po_amount, match.currency) : '—'}</dd></div>
        <div><dt>GR ${match.gr_number && match.gr_url
          ? html`<a class="cl-record-erp-link" href=${match.gr_url} target="_blank" rel="noreferrer noopener" title="Open in ${formatErpName(match.erp_type)}"><code>${match.gr_number}</code> ↗</a>`
          : (match.gr_number ? html` <code>${match.gr_number}</code>` : '')} amount</dt><dd>${match.gr_amount !== null && match.gr_amount !== undefined ? formatAmount(match.gr_amount, match.currency) : '—'}</dd></div>
        ${match.price_variance_pct !== null && match.price_variance_pct !== undefined ? html`
          <div><dt>Price variance</dt><dd>${(match.price_variance_pct * 100).toFixed(1)}%</dd></div>` : null}
        ${match.quantity_variance !== null && match.quantity_variance !== undefined ? html`
          <div><dt>Quantity variance</dt><dd>${match.quantity_variance}</dd></div>` : null}
      </dl>

      ${(match.exceptions || []).length > 0 ? html`
        <div class="cl-record-three-way-exceptions">
          <h3>Exceptions</h3>
          <ul>
            ${match.exceptions.map((exc, idx) => html`
              <li key=${idx}>
                <strong>${exc.code || exc.type || 'exception'}</strong>
                ${exc.message ? html`: ${exc.message}` : null}
              </li>`)}
          </ul>
        </div>
      ` : null}

      ${lineBreakdown.length > 0 ? html`
        <details class="cl-record-three-way-lines">
          <summary>Line-by-line breakdown</summary>
          <table class="cl-record-three-way-line-table">
            <thead>
              <tr>
                <th>Description</th>
                <th class="cl-record-num">Inv qty</th>
                <th class="cl-record-num">PO qty</th>
                <th class="cl-record-num">GR qty</th>
                <th class="cl-record-num">Price var</th>
                <th>Match</th>
              </tr>
            </thead>
            <tbody>
              ${lineBreakdown.map((line, idx) => html`
                <tr key=${idx}>
                  <td>${line.description || '—'}</td>
                  <td class="cl-record-num">${line.invoice_quantity ?? '—'}</td>
                  <td class="cl-record-num">${line.po_quantity ?? '—'}</td>
                  <td class="cl-record-num">${line.gr_quantity_received ?? '—'}</td>
                  <td class="cl-record-num">${line.price_variance_pct !== null && line.price_variance_pct !== undefined ? `${(line.price_variance_pct * 100).toFixed(1)}%` : '—'}</td>
                  <td><span class=${`cl-record-chip cl-record-chip-${matchStatusTone(line.match_flag)}`}>${line.match_flag || 'unknown'}</span></td>
                </tr>`)}
            </tbody>
          </table>
        </details>
      ` : null}
    </section>
  `;
}


// ─── Bank match (closing leg of AP) ─────────────────────────────────
//
// AP item posts to ERP → vendor paid → bank statement line clears →
// bank-rec matches the line back to the posted AP item → record
// moves Posted → Paid → Closed. This panel surfaces that final leg
// on the AP record itself, since reconciliation is AP-subordinate
// (not a peer surface). Backed by GET /ap-items/{id}/bank-match
// which composes payment_confirmations + bank_statement_lines.

const BANK_MATCH_LABEL = {
  no_payment: 'Awaiting payment',
  awaiting_match: 'Paid · awaiting bank match',
  matched: 'Reconciled to bank',
  ambiguous: 'Match needs review',
};

const BANK_MATCH_TONE = {
  no_payment: 'info',
  awaiting_match: 'info',
  matched: 'success',
  ambiguous: 'warning',
};

function BankMatchPanel({ api, recordId, item }) {
  const [state, setState] = useState({ status: 'loading', data: null, error: null });

  useEffect(() => {
    if (!recordId) return undefined;
    let cancelled = false;
    setState({ status: 'loading', data: null, error: null });
    api(`/api/workspace/ap-items/${encodeURIComponent(recordId)}/bank-match`, { silent: true })
      .then((data) => { if (!cancelled) setState({ status: 'ready', data, error: null }); })
      .catch((err) => { if (!cancelled) setState({ status: 'error', data: null, error: err?.message || 'load_failed' }); });
    return () => { cancelled = true; };
  }, [recordId, api]);

  // Hide the panel for AP states where bank match doesn't apply yet —
  // the bill needs to at least be approved/posted before payment is
  // even possible. Pre-posting states get Three-way match instead.
  const stateLc = String(item?.state || item?.status || '').toLowerCase();
  const PRE_POSTING = new Set([
    'received', 'validated', 'needs_info', 'needs_approval',
    'pending_approval', 'needs_second_approval', 'rejected', 'snoozed',
  ]);
  if (PRE_POSTING.has(stateLc)) return null;

  if (state.status === 'loading') {
    return html`
      <section class="cl-record-panel">
        <header class="cl-record-panel-head">
          <h2>Bank match</h2>
        </header>
        <div class="cl-record-bank-skeleton">Loading bank match…</div>
      </section>
    `;
  }

  if (state.status === 'error') {
    return html`
      <section class="cl-record-panel">
        <header class="cl-record-panel-head">
          <h2>Bank match</h2>
        </header>
        <p class="cl-record-empty cl-record-empty-error">
          Couldn't load bank-match status. ${state.error || ''}
        </p>
      </section>
    `;
  }

  const { status, confirmations = [], lines = [] } = state.data || {};
  const tone = BANK_MATCH_TONE[status] || 'neutral';
  const label = BANK_MATCH_LABEL[status] || 'Unknown';

  return html`
    <section class="cl-record-panel">
      <header class="cl-record-panel-head">
        <h2>Bank match</h2>
        <span class=${`cl-record-chip cl-record-chip-${tone}`}>${label}</span>
      </header>

      ${status === 'no_payment' ? html`
        <p class="cl-record-empty">
          The bill hasn't been paid yet. Bank reconciliation closes the loop
          once the ERP confirms a payment and the bank statement clears.
        </p>
      ` : null}

      ${status === 'awaiting_match' ? html`
        <div class="cl-record-bank-summary">
          <p class="cl-record-empty">
            Payment confirmed. Waiting for the bank statement line to import
            and match. Either no statement covering this date has been
            ingested, or the matcher couldn't find the line yet.
          </p>
        </div>
      ` : null}

      ${confirmations.length > 0 ? html`
        <div class="cl-record-bank-block">
          <h3 class="cl-record-bank-subhead">Payment confirmations</h3>
          <ul class="cl-record-bank-list">
            ${confirmations.map((c) => html`
              <li class="cl-record-bank-row" key=${c.id}>
                <div class="cl-record-bank-row-main">
                  <div class="cl-record-bank-row-amount">
                    ${c.amount != null ? formatAmount(c.amount, c.currency) : '—'}
                  </div>
                  <div class="cl-record-bank-row-meta">
                    ${c.source || 'erp'}
                    ${c.rail ? ` · ${c.rail}` : ''}
                    ${c.payment_reference ? ` · ${c.payment_reference}` : ''}
                  </div>
                </div>
                <div class="cl-record-bank-row-side">
                  <span class=${`cl-record-chip cl-record-chip-${c.status === 'confirmed' ? 'success' : c.status === 'failed' ? 'error' : 'info'}`}>
                    ${c.status || 'pending'}
                  </span>
                  ${c.settlement_at ? html`
                    <div class="cl-record-bank-row-meta">${fmtDate(c.settlement_at)}</div>
                  ` : null}
                </div>
              </li>
            `)}
          </ul>
        </div>
      ` : null}

      ${lines.length > 0 ? html`
        <div class="cl-record-bank-block">
          <h3 class="cl-record-bank-subhead">Bank statement lines</h3>
          <ul class="cl-record-bank-list">
            ${lines.map((line) => html`
              <li class="cl-record-bank-row" key=${line.id}>
                <div class="cl-record-bank-row-main">
                  <div class="cl-record-bank-row-amount">
                    ${line.amount != null ? formatAmount(line.amount, line.currency) : '—'}
                  </div>
                  <div class="cl-record-bank-row-meta">
                    ${line.counterparty || line.description || 'bank line'}
                    ${line.bank_reference ? ` · ${line.bank_reference}` : ''}
                  </div>
                </div>
                <div class="cl-record-bank-row-side">
                  <span class=${`cl-record-chip cl-record-chip-${matchStatusTone(line.match_status)}`}>
                    ${line.match_status || 'unknown'}
                  </span>
                  ${line.value_date ? html`
                    <div class="cl-record-bank-row-meta">${fmtDate(line.value_date)}</div>
                  ` : null}
                </div>
              </li>
            `)}
          </ul>
        </div>
      ` : null}
    </section>
  `;
}

// Bank-side amounts use the canonical formatAmount (imported above) —
// previously this had a separate Intl.NumberFormat path that defaulted
// to USD when currency was missing. Consolidated in the EU/UK launch
// money-formatter sweep so every render goes through one helper.


// ─── Workflow timeline ──────────────────────────────────────────────

function WorkflowTimeline({ events }) {
  const safeEvents = Array.isArray(events) ? events : [];

  if (safeEvents.length === 0) {
    return html`
      <section class="cl-record-panel cl-record-timeline">
        <header class="cl-record-panel-head">
          <h2>Timeline</h2>
        </header>
        <p class="cl-record-empty">No workflow events recorded yet.</p>
      </section>
    `;
  }

  return html`
    <section class="cl-record-panel cl-record-timeline">
      <header class="cl-record-panel-head">
        <h2>Timeline</h2>
        <span class="cl-record-panel-eyebrow">${safeEvents.length} events</span>
      </header>
      <ol class="cl-record-timeline-list">
        ${safeEvents.map((event, idx) => html`
          <li key=${event.id || idx} class="cl-record-timeline-event">
            <span class="cl-record-timeline-dot" aria-hidden="true"></span>
            <div class="cl-record-timeline-body">
              <div class="cl-record-timeline-line">
                <span class="cl-record-timeline-summary">
                  ${event.summary || event.event_type || 'event'}
                </span>
                ${event.governance_verdict ? html`
                  <span class=${`cl-record-chip cl-record-chip-${VERDICT_TONE[String(event.governance_verdict).toLowerCase()] || 'info'}`}>
                    ${event.governance_verdict}
                  </span>` : null}
                ${event.policy_version != null ? html`
                  <span class="cl-record-timeline-policy" title="Policy version stamped on this transition">
                    v${event.policy_version}
                  </span>` : null}
              </div>
              <div class="cl-record-timeline-meta">
                <span>${fmtDateTime(event.ts)}</span>
                ${event.actor_id ? html`<span>· ${event.actor_id}</span>` : null}
                ${event.prev_state || event.new_state ? html`
                  <span>· ${event.prev_state || '—'} → ${event.new_state || '—'}</span>` : null}
              </div>
              ${event.hash ? html`
                <div class="cl-record-timeline-chain" title="Append-only hash chain. The audit trail proves its own integrity.">
                  <span class="cl-record-timeline-chain-key">hash</span>
                  <code class="cl-record-timeline-chain-val">${truncateHash(event.hash)}</code>
                  ${event.prev_hash ? html`
                    <span class="cl-record-timeline-chain-key">prev</span>
                    <code class="cl-record-timeline-chain-val">${truncateHash(event.prev_hash)}</code>
                  ` : null}
                </div>
              ` : null}
            </div>
          </li>`)}
      </ol>
    </section>
  `;
}


// ─── Helpers ────────────────────────────────────────────────────────

// Hashes are 64-char sha256 hex. Show the leading 8 chars + ellipsis so
// the chain is visible without dominating the timeline.
function truncateHash(value) {
  if (!value || typeof value !== 'string') return '';
  const trimmed = value.trim();
  if (trimmed.length <= 8) return trimmed;
  return `${trimmed.slice(0, 8)}…`;
}

function computeAgeDays(createdAt) {
  if (!createdAt) return null;
  try {
    const ms = Date.now() - new Date(createdAt).getTime();
    if (Number.isNaN(ms) || ms < 0) return null;
    return Math.floor(ms / (1000 * 60 * 60 * 24));
  } catch {
    return null;
  }
}

function formatRecommendation(rec) {
  if (!rec) return '';
  return rec.replace(/_/g, ' ');
}

function formatRiskFlag(flag) {
  return String(flag).replace(/_/g, ' ');
}

function formatMatchStatus(status) {
  if (!status) return 'unknown';
  return status.replace(/_/g, ' ');
}

function matchStatusTone(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'matched' || s === 'match' || s === 'reconciled') return 'success';
  if (s === 'no_po' || s === 'pending') return 'info';
  if (s === 'exception' || s === 'mismatch') return 'error';
  if (s === 'partial' || s === 'partial_match' || s === 'partial_match_warn'
      || s === 'ambiguous' || s === 'unmatched') return 'warning';
  return 'info';
}

function hasAnyBankDetail(bankDetails) {
  if (!bankDetails || typeof bankDetails !== 'object') return false;
  return Boolean(
    bankDetails.bank_name
    || bankDetails.iban_masked
    || bankDetails.account_number_masked
    || bankDetails.swift,
  );
}


// ─── Module 2 — Ask the agent ─────────────────────────────────────
//
// Spec line 100: "Ask the agent: free-form questions about this
// invoice ('show prior bills from this vendor', 'what does PO 4471-A
// reference'). Returns within 10 seconds for typical questions."
//
// The model is bounded to a structured context bundle for THIS
// invoice — it can't run other DB queries or web fetches. Citations
// surface as inline [s1][s2] markers; the source list maps each
// marker to a context-bundle row.

const ASK_SUGGESTIONS = [
  'Why is this invoice flagged?',
  'Show me prior bills from this vendor.',
  'What was the agent\'s confidence and why?',
  'Summarise this invoice in two sentences.',
];

function AskTheAgentPanel({ api, recordId, toast }) {
  const [question, setQuestion] = useState('');
  const [history, setHistory] = useState([]); // [{question, answer, sources, fallback, latency_ms}]
  const [busy, setBusy] = useState(false);

  const ask = async (q) => {
    const trimmed = String(q || '').trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setQuestion('');
    try {
      const resp = await api(
        `/api/workspace/ap-items/${encodeURIComponent(recordId)}/ask`,
        { method: 'POST', body: { question: trimmed } },
      );
      if (resp?.error) {
        toast(`Couldn't answer: ${resp.error}`, 'error');
        return;
      }
      setHistory((prev) => [
        ...prev,
        {
          question: trimmed,
          answer: resp.answer || '',
          sources: resp.sources || [],
          fallback: !!resp.fallback,
          latencyMs: resp.latency_ms || 0,
          model: resp.model,
        },
      ]);
    } catch (exc) {
      toast(`Ask failed: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  return html`
    <section class="cl-record-panel cl-record-ask">
      <header class="cl-record-panel-head">
        <h2>Ask the agent</h2>
        <span class="cl-record-panel-eyebrow">about this invoice</span>
      </header>

      ${history.length === 0 ? html`
        <div class="cl-record-ask-suggestions">
          <p class="cl-record-muted">Try one of these, or type your own question:</p>
          <div class="cl-record-ask-chip-row">
            ${ASK_SUGGESTIONS.map((s) => html`
              <button class="cl-record-ask-chip" key=${s} onClick=${() => ask(s)} disabled=${busy}>
                ${s}
              </button>
            `)}
          </div>
        </div>
      ` : html`
        <ul class="cl-record-ask-history">
          ${history.map((turn, i) => html`
            <li key=${i} class="cl-record-ask-turn">
              <div class="cl-record-ask-question"><strong>You:</strong> ${turn.question}</div>
              <div class="cl-record-ask-answer">
                <strong>Agent:</strong> ${turn.answer}
                ${turn.fallback ? html` <span class="cl-record-ask-fallback">(deterministic — LLM unavailable)</span>` : null}
              </div>
              ${turn.sources && turn.sources.length > 0 ? html`
                <div class="cl-record-ask-sources">
                  ${turn.sources.map((s) => html`
                    <span key=${s.id} class="cl-record-ask-source">[${s.id}] ${s.summary}</span>
                  `)}
                </div>
              ` : null}
              <div class="cl-record-ask-meta">
                ${turn.model ? html`${turn.model} · ` : null}${turn.latencyMs} ms
              </div>
            </li>
          `)}
        </ul>
      `}

      <form
        class="cl-record-ask-form"
        onSubmit=${(e) => { e.preventDefault(); ask(question); }}>
        <input
          type="text"
          placeholder="Ask anything about this invoice…"
          value=${question}
          onInput=${(e) => setQuestion(e.target.value)}
          disabled=${busy}
          autoFocus />
        <button type="submit" class="btn btn-primary btn-sm" disabled=${busy || !question.trim()}>
          ${busy ? 'Thinking…' : 'Ask'}
        </button>
      </form>
    </section>
  `;
}
