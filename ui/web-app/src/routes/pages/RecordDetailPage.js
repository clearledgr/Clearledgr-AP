/**
 * Record detail — Module 2 (Exception Detail and Resolution).
 *
 * Where the leader applies judgment. Renders the full payload from
 * `GET /api/workspace/ap-items/{id}/detail` as one cohesive surface:
 * header, action bar, agent reasoning panel (the harness made
 * legible), bill detail, three-way match, workflow timeline.
 *
 * Sub-components are colocated because they all consume the same
 * detail payload — splitting across files would force prop-drilling
 * the same object through five wrapper layers without buying anything.
 *
 * Action buttons route through `runtime.execute_intent` via
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

// Operator-facing intent copy. Keep these in lockstep with
// finance_skills/ap_skill.py — the backend is the source of truth for
// the intent vocabulary; this map is just the human label.
const INTENT_LABELS = {
  approve_invoice: 'Approve',
  reject_invoice: 'Reject',
  request_info: 'Send back for info',
  escalate_approval: 'Escalate to controller',
  request_approval: 'Send for approval',
  snooze_invoice: 'Snooze',
  unsnooze_invoice: 'Unsnooze',
  post_to_erp: 'Post to ERP',
  reverse_invoice_post: 'Reverse posting',
  manually_classify_invoice: 'Reclassify',
  resubmit_invoice: 'Resubmit',
};

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
    navigate('/pipeline');
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

  return html`
    <div class="cl-record-detail">
      <header class="cl-record-detail-topbar">
        <button class="cl-record-back" onClick=${onBack} aria-label="Back to pipeline">
          ← Pipeline
        </button>
        <span class="cl-record-detail-id">${item.id}</span>
      </header>

      <${RecordHeader} item=${item} />

      <${ActionBar}
        actions=${actions}
        onIntent=${onIntent}
        busy=${actionBusy}
      />

      <${AgentReasoningPanel}
        reasoning=${reasoning}
        item=${item}
      />

      <div class="cl-record-detail-grid">
        <${BillDetailPanel} item=${item} />
        <${ThreeWayMatchPanel} match=${match} />
      </div>

      <${WorkflowTimeline} events=${timeline} />
    </div>
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
        <button class="btn btn-secondary" onClick=${onBack}>Back to pipeline</button>
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
            ${item.vendor_name || item.vendor || 'Unknown vendor'}
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


// ─── Action bar ─────────────────────────────────────────────────────

function ActionBar({ actions, onIntent, busy }) {
  const available = (actions && actions.available) || [];
  const primary = (actions && actions.primary) || null;

  if (available.length === 0) {
    return html`
      <section class="cl-record-action-bar cl-record-action-bar-empty">
        <p class="cl-record-action-bar-empty-msg">
          This record is in a terminal state. No further actions available.
        </p>
      </section>
    `;
  }

  const orderedSecondary = available.filter((intent) => intent !== primary);

  return html`
    <section class="cl-record-action-bar" role="toolbar" aria-label="Record actions">
      ${primary ? html`
        <button
          class="btn btn-primary cl-record-action-primary"
          onClick=${() => onIntent(primary)}
          disabled=${busy}
        >${INTENT_LABELS[primary] || primary}</button>
      ` : null}
      ${orderedSecondary.map((intent) => html`
        <button
          key=${intent}
          class="btn btn-secondary"
          onClick=${() => onIntent(intent)}
          disabled=${busy}
        >${INTENT_LABELS[intent] || intent}</button>
      `)}
    </section>
  `;
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
        <div><dt>PO</dt><dd>${match.po_number ? html`<code>${match.po_number}</code>` : '—'}</dd></div>
        <div><dt>Invoice</dt><dd>${formatAmount(match.invoice_amount, match.currency)}</dd></div>
        <div><dt>PO amount</dt><dd>${match.po_amount !== null && match.po_amount !== undefined ? formatAmount(match.po_amount, match.currency) : '—'}</dd></div>
        <div><dt>GR amount</dt><dd>${match.gr_amount !== null && match.gr_amount !== undefined ? formatAmount(match.gr_amount, match.currency) : '—'}</dd></div>
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
              </div>
              <div class="cl-record-timeline-meta">
                <span>${fmtDateTime(event.ts)}</span>
                ${event.actor_id ? html`<span>· ${event.actor_id}</span>` : null}
                ${event.prev_state || event.new_state ? html`
                  <span>· ${event.prev_state || '—'} → ${event.new_state || '—'}</span>` : null}
              </div>
            </div>
          </li>`)}
      </ol>
    </section>
  `;
}


// ─── Helpers ────────────────────────────────────────────────────────

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
  if (s === 'matched' || s === 'match') return 'success';
  if (s === 'no_po' || s === 'pending') return 'info';
  if (s === 'exception' || s === 'mismatch') return 'error';
  if (s === 'partial' || s === 'partial_match' || s === 'partial_match_warn') return 'warning';
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
