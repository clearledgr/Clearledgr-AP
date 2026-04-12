/**
 * ThreadSidebar — DESIGN_THESIS.md §6.6 + AGENT_DESIGN_SPECIFICATION.md §6 / §8.1 / §9.1 / §12.
 *
 * Fixed section order:
 *   0. (conditional) Resubmission banner  — lineage for superseded invoices
 *   0. (conditional) Override Window       — live countdown + Undo
 *   0. (conditional) Waiting               — why the agent is paused
 *   0. (conditional) Fraud Flags           — active IBAN/domain/velocity flags
 *   1. Invoice        — amount due, reference, PO, due date, terms
 *   2. 3-Way Match    — PO / GRN / Invoice rows + tolerance
 *   3. Vendor         — name, spend, risk, IBAN status
 *   4. Linked Records — linked onboarding / sibling invoices
 *   5. Agent Actions  — condensed timeline
 *
 * Design rules from the thesis:
 *   - "Clearledgr sidebar has four fixed sections in strict order"
 *   - "The sidebar loads in less than two seconds"
 *   - "The sidebar never shows more than one invoice"
 *
 * The conditional banners above the four fixed sections are not new
 * sections — they are state indicators that the thesis implies (see
 * spec §9.1 "Override window open until 09:56" and §6 waiting_condition
 * field) and that users need to see at a glance.
 */
import { html } from 'htm/preact';
import { useState, useEffect } from 'preact/hooks';

// ---------------------------------------------------------------------------
// CSS
// ---------------------------------------------------------------------------

const THREAD_SIDEBAR_CSS = `
.cl-thread-sidebar { padding: 0; max-width: 100%; overflow-x: hidden; }
.cl-thread-sidebar, .cl-thread-sidebar * { word-break: break-word; overflow-wrap: anywhere; }
.cl-ts-section { padding: 12px 16px; border-bottom: 1px solid #E2E8F0; }
.cl-ts-section:last-child { border-bottom: none; }
.cl-ts-section-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.04em; color: #5C6B7A; margin-bottom: 8px;
}
.cl-ts-row { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px; }
.cl-ts-label { font-size: 12px; color: #5C6B7A; }
.cl-ts-value { font-size: 13px; color: #0A1628; font-weight: 500; text-align: right; max-width: 60%; }
.cl-ts-value.mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; }
.cl-ts-match-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.cl-ts-match-icon { width: 16px; text-align: center; font-size: 14px; }
.cl-ts-match-icon.pass { color: #10B981; }
.cl-ts-match-icon.warn { color: #CA8A04; }
.cl-ts-match-icon.fail { color: #DC2626; }
.cl-ts-match-icon.na { color: #94A3B8; }
.cl-ts-match-label { font-size: 12px; color: #0A1628; flex: 1; }
.cl-ts-match-detail { font-size: 11px; color: #5C6B7A; }
.cl-ts-match-tolerance {
  font-size: 11px; color: #16A34A; background: #ECFDF5;
  padding: 2px 8px; border-radius: 10px; margin-top: 6px; display: inline-block;
}
.cl-ts-match-tolerance.warn { color: #92400E; background: #FEFCE8; }
.cl-ts-match-tolerance.fail { color: #991B1B; background: #FEF2F2; }
.cl-ts-match-exception-box {
  font-size: 12px; color: #92400E; margin-top: 4px;
  padding: 6px 8px; background: #FEFCE8; border-radius: 6px;
}
.cl-ts-risk-badge {
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.cl-ts-risk-low { background: #ECFDF5; color: #16A34A; }
.cl-ts-risk-medium { background: #FEFCE8; color: #92400E; }
.cl-ts-risk-high { background: #FEF2F2; color: #991B1B; }
.cl-ts-timeline { list-style: none; margin: 0; padding: 0; }
.cl-ts-timeline li {
  font-size: 12px; color: #374151; margin-bottom: 8px;
  padding-left: 16px; position: relative; line-height: 1.4;
}
.cl-ts-timeline li::before {
  content: ''; width: 6px; height: 6px; border-radius: 50%;
  background: #00D67E; position: absolute; left: 0; top: 5px;
}
.cl-ts-timeline-time { font-size: 10px; color: #94A3B8; display: block; }
.cl-ts-agent-icon { width: 10px; height: 10px; vertical-align: -1px; margin-right: 3px; opacity: 0.6; }
.cl-ts-section-icon { width: 12px; height: 12px; vertical-align: -1px; margin-right: 4px; opacity: 0.7; }
.cl-ts-iban-pill {
  display: inline-block; padding: 1px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.cl-ts-iban-verified { background: #ECFDF5; color: #16A34A; }
.cl-ts-iban-unverified { background: #FEF2F2; color: #991B1B; }
.cl-ts-iban-pending { background: #FEFCE8; color: #92400E; }
.cl-ts-expand-btn {
  background: none; border: none; color: #00D67E; font-size: 12px;
  font-weight: 600; cursor: pointer; padding: 4px 0; font-family: inherit;
}
.cl-ts-timeline-why { font-weight: 400; color: #5C6B7A; }
.cl-ts-timeline-next { display: block; font-size: 11px; color: #00A85F; font-weight: 500; margin-top: 2px; }
.cl-ts-linked-box {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px;
  margin-bottom: 6px;
}
.cl-ts-linked-box-icon { font-size: 14px; width: 20px; text-align: center; }
.cl-ts-linked-box-info { flex: 1; min-width: 0; }
.cl-ts-linked-box-title { font-size: 12px; color: #0A1628; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cl-ts-linked-box-meta { font-size: 11px; color: #5C6B7A; }
.cl-ts-linked-box-status {
  display: inline-block; padding: 1px 6px; border-radius: 8px;
  font-size: 10px; font-weight: 600; text-transform: uppercase;
}
.cl-ts-linked-box-status.active { background: #ECFDF5; color: #16A34A; }
.cl-ts-linked-box-status.pending { background: #FEFCE8; color: #92400E; }
.cl-ts-linked-box-status.completed { background: #EFF6FF; color: #1D4ED8; }
.cl-ts-actions-bar { padding: 12px 16px; border-top: 1px solid #E2E8F0; }
.cl-ts-approve-btn {
  width: 100%; padding: 10px 16px; border: none; border-radius: 8px;
  background: #00D67E; color: #0A1628; font-size: 14px; font-weight: 600;
  cursor: pointer; font-family: inherit; margin-bottom: 8px;
}
.cl-ts-approve-btn:hover { background: #00C271; }
.cl-ts-approve-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.cl-ts-snooze-btn {
  padding: 6px 14px; border: 1px solid #CA8A04; border-radius: 6px;
  background: #FEFCE8; color: #92400E;
  font: 500 12px/1.2 'DM Sans', sans-serif; cursor: pointer;
}
.cl-ts-snoozed-notice {
  font: 500 11px/1.3 'DM Sans', sans-serif; color: #CA8A04; padding: 4px 0;
}
.cl-ts-query-input {
  width: 100%; padding: 10px 12px; border: 1px solid #E2E8F0; border-radius: 8px;
  font-size: 13px; color: #0A1628; background: #FBFCFD; font-family: inherit;
}
.cl-ts-query-input:focus { outline: none; border-color: #00D67E; box-shadow: 0 0 0 3px rgba(0, 214, 126, 0.15); }
.cl-ts-query-input::placeholder { color: #94A3B8; }

/* -- Banners (conditional, above the fixed sections) -- */
.cl-ts-banner {
  padding: 10px 16px; display: flex; align-items: center; gap: 10px;
  border-bottom: 1px solid #E2E8F0;
}
.cl-ts-banner-icon {
  width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px;
}
.cl-ts-banner-body { flex: 1; min-width: 0; }
.cl-ts-banner-title { font-size: 12px; font-weight: 700; color: #0A1628; line-height: 1.2; }
.cl-ts-banner-detail { font-size: 11px; color: #5C6B7A; margin-top: 2px; line-height: 1.3; }
.cl-ts-banner.override { background: #ECFDF5; }
.cl-ts-banner.override .cl-ts-banner-icon { background: #00D67E; color: #0A1628; }
.cl-ts-banner.waiting { background: #FEFCE8; }
.cl-ts-banner.waiting .cl-ts-banner-icon { background: #CA8A04; color: #FEFCE8; }
.cl-ts-banner.fraud { background: #FEF2F2; }
.cl-ts-banner.fraud .cl-ts-banner-icon { background: #DC2626; color: #FEF2F2; }
.cl-ts-banner.resubmission { background: #EFF6FF; }
.cl-ts-banner.resubmission .cl-ts-banner-icon { background: #1D4ED8; color: #EFF6FF; }
.cl-ts-banner-action {
  padding: 6px 12px; border: 1px solid #0A1628; border-radius: 6px;
  background: #fff; color: #0A1628; font: 600 12px/1 'DM Sans', sans-serif;
  cursor: pointer; flex-shrink: 0;
}
.cl-ts-banner-action:hover { background: #0A1628; color: #fff; }
.cl-ts-banner-action:disabled { opacity: 0.5; cursor: not-allowed; }
.cl-ts-fraud-flag {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: #991B1B; margin-top: 4px; padding-left: 38px;
}
.cl-ts-fraud-flag::before {
  content: '⚠'; color: #DC2626;
}

/* Loading skeleton */
.cl-ts-skeleton {
  padding: 12px 16px;
  border-bottom: 1px solid #E2E8F0;
}
.cl-ts-skeleton-row {
  height: 12px; background: linear-gradient(90deg, #F1F5F9 0%, #E2E8F0 50%, #F1F5F9 100%);
  background-size: 200% 100%; animation: cl-ts-shimmer 1.4s infinite linear;
  border-radius: 4px; margin-bottom: 8px;
}
@keyframes cl-ts-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
`;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatAmount(amount, currency) {
  if (amount == null || amount === '') return '—';
  try {
    const num = parseFloat(amount);
    if (isNaN(num)) return '—';
    const cur = String(currency || 'USD').toUpperCase();
    return `${cur} ${num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  } catch { return '—'; }
}

function formatDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  } catch { return iso; }
}

function formatTimeAgo(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const now = new Date();
    const hours = Math.floor((now - d) / 3600000);
    if (hours < 1) return 'just now';
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  } catch { return ''; }
}

// For countdowns: "3m 42s" / "1h 4m"
function formatCountdown(targetIso, nowMs) {
  if (!targetIso) return '';
  try {
    const target = new Date(targetIso).getTime();
    const diff = target - nowMs;
    if (diff <= 0) return 'closed';
    const totalSec = Math.floor(diff / 1000);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  } catch { return ''; }
}

function matchIcon(status) {
  if (!status) return html`<span class="cl-ts-match-icon na">—</span>`;
  const s = String(status).toLowerCase();
  if (s === 'passed' || s === 'match' || s === 'matched' || s === 'verified')
    return html`<span class="cl-ts-match-icon pass">✓</span>`;
  if (s === 'exception' || s === 'warning' || s === 'partial')
    return html`<span class="cl-ts-match-icon warn">⚠</span>`;
  if (s === 'failed' || s === 'mismatch' || s === 'missing')
    return html`<span class="cl-ts-match-icon fail">✗</span>`;
  return html`<span class="cl-ts-match-icon na">—</span>`;
}

function riskBadge(score) {
  if (score == null) return '';
  const n = parseInt(score, 10);
  if (isNaN(n)) return '';
  if (n <= 30) return html`<span class="cl-ts-risk-badge cl-ts-risk-low">Low (${n})</span>`;
  if (n <= 60) return html`<span class="cl-ts-risk-badge cl-ts-risk-medium">Medium (${n})</span>`;
  return html`<span class="cl-ts-risk-badge cl-ts-risk-high">High (${n})</span>`;
}

function ibanPill(item) {
  if (item?.iban_change_pending) return html`<span class="cl-ts-iban-pill cl-ts-iban-pending">Freeze active</span>`;
  if (item?.iban_verified) return html`<span class="cl-ts-iban-pill cl-ts-iban-verified">Verified</span>`;
  return html`<span class="cl-ts-iban-pill cl-ts-iban-unverified">Unverified</span>`;
}

function agentIconUrl() {
  return typeof chrome !== 'undefined' && chrome.runtime ? chrome.runtime.getURL('icons/icon16.png') : '';
}

// Event type strings from the audit trail (e.g.
// "ap_invoice_processing_field_review_required") are raw snake_case
// identifiers. Render them as human text and cap length so they never
// break the sidebar layout.
function humanizeEventType(raw) {
  if (!raw) return 'Action';
  const s = String(raw).trim();
  if (!s) return 'Action';
  // Common AP agent event prefix → shorter label
  const prefixMap = [
    ['ap_invoice_processing_', 'Invoice processing'],
    ['vendor_onboarding_', 'Vendor onboarding'],
    ['agent_action:', ''],
  ];
  let label = s;
  for (const [prefix, replacement] of prefixMap) {
    if (label.toLowerCase().startsWith(prefix)) {
      label = replacement + (replacement ? ' — ' : '') + label.slice(prefix.length);
      break;
    }
  }
  // snake_case + colons → spaces, lowercase everything, capitalize first
  label = label.replace(/[:_]/g, ' ').replace(/\s+/g, ' ').trim();
  if (label.length > 0) label = label[0].toUpperCase() + label.slice(1);
  // Guard: truncate at 80 chars so even a pathological event name
  // can't nuke the layout.
  if (label.length > 80) label = label.slice(0, 77) + '…';
  return label;
}

function humanizeWaitingType(type) {
  if (!type) return 'the next step';
  const t = String(type).toLowerCase();
  const map = {
    grn_check: 'GRN confirmation',
    grn_confirmation: 'GRN confirmation',
    approval_response: 'approval',
    vendor_onboarding_completion: 'vendor onboarding',
    iban_verification: 'IBAN verification',
    external_dependency_unavailable: 'ERP to come back online',
    erp_unavailable: 'ERP to come back online',
    erp_recheck: 'ERP reconnection',
    payment_confirmation: 'payment confirmation',
    vendor_response: 'vendor response',
  };
  return map[t] || t.replace(/_/g, ' ');
}

// ---------------------------------------------------------------------------
// Banner components (conditional, above the four fixed sections)
// ---------------------------------------------------------------------------

function OverrideWindowBanner({ window_, onUndo, nowMs }) {
  if (!window_ || !window_.expires_at) return null;
  const [undoing, setUndoing] = useState(false);
  const remaining = formatCountdown(window_.expires_at, nowMs);
  const isOpen = remaining && remaining !== 'closed';
  if (!isOpen) return null;
  const action = String(window_.action_type || 'posted_to_erp').replace(/_/g, ' ');
  return html`
    <div class="cl-ts-banner override">
      <div class="cl-ts-banner-icon">✓</div>
      <div class="cl-ts-banner-body">
        <div class="cl-ts-banner-title">Auto-${action} — ${remaining} to undo</div>
        <div class="cl-ts-banner-detail">
          ${window_.erp_reference ? `ERP ref ${window_.erp_reference} · ` : ''}
          Closes ${formatTimeAgo(window_.expires_at).replace(/ ago/, '') || 'shortly'}
        </div>
      </div>
      ${onUndo ? html`
        <button class="cl-ts-banner-action" disabled=${undoing}
          onClick=${async () => {
            if (undoing) return;
            setUndoing(true);
            try { await onUndo(window_); } finally { setUndoing(false); }
          }}
        >${undoing ? 'Undoing…' : 'Undo'}</button>
      ` : ''}
    </div>
  `;
}

function WaitingBanner({ waiting }) {
  if (!waiting || typeof waiting !== 'object') return null;
  const type = waiting.type || waiting.condition;
  if (!type) return null;
  const label = humanizeWaitingType(type);
  const setAt = waiting.set_at || waiting.context?.set_at || waiting.created_at;
  const expectedBy = waiting.expected_by || waiting.context?.expected_by;
  const since = setAt ? formatTimeAgo(setAt) : '';
  const nextCheck = expectedBy ? `Next check ${formatTimeAgo(expectedBy).replace(/ ago/, '') || 'soon'}` : '';
  return html`
    <div class="cl-ts-banner waiting">
      <div class="cl-ts-banner-icon">⏳</div>
      <div class="cl-ts-banner-body">
        <div class="cl-ts-banner-title">Waiting for ${label}</div>
        <div class="cl-ts-banner-detail">
          ${since ? `Paused ${since}` : 'Paused'}${nextCheck ? ` · ${nextCheck}` : ''}
        </div>
      </div>
    </div>
  `;
}

function FraudFlagsBanner({ flags }) {
  if (!Array.isArray(flags) || flags.length === 0) return null;
  // Only show unresolved flags
  const active = flags.filter((f) => f && typeof f === 'object' && !f.resolved_at);
  if (active.length === 0) return null;
  const primary = active[0];
  const type = (primary.flag_type || primary.type || 'flag').replace(/_/g, ' ');
  return html`
    <div class="cl-ts-banner fraud">
      <div class="cl-ts-banner-icon">!</div>
      <div class="cl-ts-banner-body">
        <div class="cl-ts-banner-title">${active.length} fraud ${active.length === 1 ? 'flag' : 'flags'} active</div>
        <div class="cl-ts-banner-detail">Primary: ${type}</div>
        ${active.slice(1).map((f) => html`
          <div class="cl-ts-fraud-flag" key=${f.detected_at || f.flag_type}>
            ${(f.flag_type || f.type || 'flag').replace(/_/g, ' ')}
          </div>
        `)}
      </div>
    </div>
  `;
}

function ResubmissionBanner({ item }) {
  if (!item?.is_resubmission && !item?.has_resubmission) return null;
  if (item.has_resubmission) {
    return html`
      <div class="cl-ts-banner resubmission">
        <div class="cl-ts-banner-icon">↻</div>
        <div class="cl-ts-banner-body">
          <div class="cl-ts-banner-title">Superseded by newer invoice</div>
          <div class="cl-ts-banner-detail">ID ${item.superseded_by_ap_item_id}</div>
        </div>
      </div>
    `;
  }
  return html`
    <div class="cl-ts-banner resubmission">
      <div class="cl-ts-banner-icon">↻</div>
      <div class="cl-ts-banner-body">
        <div class="cl-ts-banner-title">Resubmission</div>
        <div class="cl-ts-banner-detail">
          ${item.resubmission_reason ? item.resubmission_reason : 'Supersedes earlier invoice'}
          ${item.supersedes_ap_item_id ? ` · replaces ${item.supersedes_ap_item_id}` : ''}
        </div>
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Section components
// ---------------------------------------------------------------------------

function InvoiceSection({ item }) {
  return html`
    <div class="cl-ts-section">
      <div class="cl-ts-section-title">Invoice</div>
      <div class="cl-ts-row">
        <span class="cl-ts-label">Amount</span>
        <span class="cl-ts-value mono">${formatAmount(item.amount, item.currency)}</span>
      </div>
      <div class="cl-ts-row">
        <span class="cl-ts-label">Invoice #</span>
        <span class="cl-ts-value">${item.invoice_number || item.reference || '—'}</span>
      </div>
      ${item.po_number ? html`
        <div class="cl-ts-row">
          <span class="cl-ts-label">PO #</span>
          <span class="cl-ts-value">${item.po_number}</span>
        </div>
      ` : ''}
      <div class="cl-ts-row">
        <span class="cl-ts-label">Due date</span>
        <span class="cl-ts-value">${formatDate(item.due_date || item.payment_due_date)}</span>
      </div>
      ${(item.due_date || item.payment_due_date) ? html`
        <div class="cl-ts-row">
          <span class="cl-ts-label">Days to due</span>
          <span class="cl-ts-value mono">${(() => {
            try {
              const due = new Date(item.due_date || item.payment_due_date);
              const now = new Date();
              const days = Math.ceil((due - now) / 86400000);
              return days > 0 ? days + 'd' : days === 0 ? 'Today' : Math.abs(days) + 'd overdue';
            } catch { return '—'; }
          })()}</span>
        </div>
      ` : ''}
      ${item.payment_terms ? html`
        <div class="cl-ts-row">
          <span class="cl-ts-label">Terms</span>
          <span class="cl-ts-value">${item.payment_terms}</span>
        </div>
      ` : ''}
      ${item.erp_posted_at ? html`
        <div class="cl-ts-row">
          <span class="cl-ts-label">ERP posted</span>
          <span class="cl-ts-value">${formatDate(item.erp_posted_at)}</span>
        </div>
      ` : ''}
    </div>
  `;
}

function MatchSection({ item }) {
  const matchStatus = item.match_status || item.three_way_match_status;
  const poStatus = item.po_match_status || (item.po_number ? 'matched' : 'missing');
  const grnStatus = item.grn_match_status || 'na';
  const invoiceStatus = matchStatus || 'na';

  // §8.1: summarize the match with a tolerance indicator when we have it
  const score = item.match_score;
  const deltaPct = item.match_amount_delta_pct;
  const tolPct = item.match_tolerance_pct;
  let toleranceLabel = null;
  let toleranceTone = 'pass';
  if (deltaPct != null && !isNaN(parseFloat(deltaPct))) {
    const dp = Math.abs(parseFloat(deltaPct));
    const tp = tolPct != null ? parseFloat(tolPct) : null;
    toleranceLabel = `Δ ${dp.toFixed(2)}%${tp != null ? ` / ${tp.toFixed(2)}% tol.` : ''}`;
    if (tp != null) {
      if (dp <= tp) toleranceTone = 'pass';
      else if (dp <= tp * 2) toleranceTone = 'warn';
      else toleranceTone = 'fail';
    }
  } else if (score != null && !isNaN(parseFloat(score))) {
    const s = parseFloat(score);
    toleranceLabel = s <= 1 ? `Score ${(s * 100).toFixed(1)}%` : `Score ${s.toFixed(2)}`;
  }

  return html`
    <div class="cl-ts-section">
      <div class="cl-ts-section-title">3-Way Match</div>
      <div class="cl-ts-match-row">
        ${matchIcon(poStatus)}
        <span class="cl-ts-match-label">Purchase Order</span>
        <span class="cl-ts-match-detail">${item.po_number || 'Not linked'}</span>
      </div>
      <div class="cl-ts-match-row">
        ${matchIcon(grnStatus)}
        <span class="cl-ts-match-label">Goods Received Note</span>
        <span class="cl-ts-match-detail">${item.grn_reference || '—'}</span>
      </div>
      <div class="cl-ts-match-row">
        ${matchIcon(invoiceStatus)}
        <span class="cl-ts-match-label">Invoice</span>
        <span class="cl-ts-match-detail">${String(matchStatus || '—').replace(/_/g, ' ')}</span>
      </div>
      ${toleranceLabel ? html`
        <span class="cl-ts-match-tolerance ${toleranceTone}">${toleranceLabel}</span>
      ` : ''}
      ${item.match_exception_reason ? html`
        <div class="cl-ts-match-exception-box">
          ${item.match_exception_reason}
        </div>
      ` : ''}
    </div>
  `;
}

function VendorSection({ item }) {
  const vendorName = item.vendor_name || item.vendor || 'Unknown';
  return html`
    <div class="cl-ts-section">
      <div class="cl-ts-section-title">Vendor</div>
      <div class="cl-ts-row">
        <span class="cl-ts-label">Name</span>
        <span class="cl-ts-value">${vendorName}</span>
      </div>
      ${item.vendor_category ? html`
        <div class="cl-ts-row">
          <span class="cl-ts-label">Category</span>
          <span class="cl-ts-value">${item.vendor_category}</span>
        </div>
      ` : ''}
      ${item.ytd_spend != null ? html`
        <div class="cl-ts-row">
          <span class="cl-ts-label">YTD spend</span>
          <span class="cl-ts-value mono">${formatAmount(item.ytd_spend, item.currency)}</span>
        </div>
      ` : ''}
      ${item.invoice_count != null ? html`
        <div class="cl-ts-row">
          <span class="cl-ts-label">Invoices</span>
          <span class="cl-ts-value">${item.invoice_count}</span>
        </div>
      ` : ''}
      ${item.exception_count != null ? html`
        <div class="cl-ts-row">
          <span class="cl-ts-label">Exceptions</span>
          <span class="cl-ts-value">${item.exception_count}</span>
        </div>
      ` : ''}
      ${item.vendor_payment_terms || item.payment_terms ? html`
        <div class="cl-ts-row">
          <span class="cl-ts-label">Payment terms</span>
          <span class="cl-ts-value">${item.vendor_payment_terms || item.payment_terms}</span>
        </div>
      ` : ''}
      <div class="cl-ts-row">
        <span class="cl-ts-label">IBAN</span>
        <span class="cl-ts-value">${ibanPill(item)}</span>
      </div>
      ${item.risk_score != null ? html`
        <div class="cl-ts-row">
          <span class="cl-ts-label">Risk</span>
          <span class="cl-ts-value">${riskBadge(item.risk_score)}</span>
        </div>
      ` : ''}
    </div>
  `;
}

function AgentActionsSection({ item, auditEvents }) {
  const events = (auditEvents || []).slice(0, 10);
  return html`
    <div class="cl-ts-section">
      <div class="cl-ts-section-title">
        <img src="${agentIconUrl()}" alt="" class="cl-ts-section-icon" />Agent Actions
      </div>
      ${events.length > 0
        ? html`
          <ul class="cl-ts-timeline">
            ${events.map((e) => {
              // Thesis §6.6: "what the agent did, why it did it, and what happens next"
              const what = e.summary || e.decision_reason || humanizeEventType(e.event_type);
              const why = e.reasoning_summary || e.reasoning || e.reason || '';
              const next = e.next_action || e.next_step || '';
              const isAgent = (e.actor || e.actor_type || '') !== 'user';
              return html`
                <li key=${e.id || e.ts}>
                  ${isAgent ? html`<img src="${agentIconUrl()}" alt="agent" class="cl-ts-agent-icon" />` : ''}
                  <strong>${what}</strong>
                  ${why ? html`<span class="cl-ts-timeline-why"> — ${why}</span>` : ''}
                  ${next ? html`<span class="cl-ts-timeline-next">Next: ${next}</span>` : ''}
                  <span class="cl-ts-timeline-time">${formatTimeAgo(e.ts || e.created_at)}</span>
                </li>
              `;
            })}
          </ul>
          ${(auditEvents || []).length > 10 ? html`
            <button class="cl-ts-expand-btn">Show all ${auditEvents.length} actions</button>
          ` : ''}
        `
        : html`<div style="font-size: 12px; color: #94A3B8;">No agent actions yet</div>`
      }
    </div>
  `;
}

function LinkedBoxesSection({ links }) {
  if (!links || links.length === 0) return null;

  function statusClass(link) {
    const type = String(link.target_box_type || link.source_box_type || '').toLowerCase();
    if (type === 'vendor_onboarding') return 'pending';
    return 'active';
  }

  return html`
    <div class="cl-ts-section">
      <div class="cl-ts-section-title">Linked Records</div>
      ${links.map((link) => {
        const isSource = link.source_box_type === 'invoice';
        const linkedId = isSource ? link.target_box_id : link.source_box_id;
        const linkedType = isSource ? link.target_box_type : link.source_box_type;
        const icon = linkedType === 'vendor_onboarding' ? '🏢' : '🔗';
        const label = (linkedType || 'record').replace(/_/g, ' ');
        return html`
          <div class="cl-ts-linked-box" key=${link.id}>
            <span class="cl-ts-linked-box-icon">${icon}</span>
            <div class="cl-ts-linked-box-info">
              <div class="cl-ts-linked-box-title">${label}</div>
              <div class="cl-ts-linked-box-meta">${linkedId}</div>
            </div>
            <span class="cl-ts-linked-box-status ${statusClass(link)}">${link.link_type || 'related'}</span>
          </div>
        `;
      })}
    </div>
  `;
}

function LoadingSkeleton() {
  return html`
    <div class="cl-thread-sidebar">
      <style>${THREAD_SIDEBAR_CSS}</style>
      ${[0, 1, 2, 3].map((i) => html`
        <div class="cl-ts-skeleton" key=${i}>
          <div class="cl-ts-skeleton-row" style="width:40%"></div>
          <div class="cl-ts-skeleton-row" style="width:80%"></div>
          <div class="cl-ts-skeleton-row" style="width:60%"></div>
        </div>
      `)}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ThreadSidebar({
  item,
  auditEvents,
  onApprove,
  onSnooze,
  onQuery,
  onUndoOverride,
  fetchBoxLinks,
  loading,
}) {
  const [boxLinks, setBoxLinks] = useState([]);
  const [nowMs, setNowMs] = useState(Date.now());

  useEffect(() => {
    if (!item?.id || !fetchBoxLinks) return;
    let cancelled = false;
    fetchBoxLinks(item.id, 'invoice').then((links) => {
      if (!cancelled) setBoxLinks(links || []);
    }).catch(() => {
      if (!cancelled) setBoxLinks([]);
    });
    return () => { cancelled = true; };
  }, [item?.id, fetchBoxLinks]);

  // Tick for live countdown when an override window is open
  useEffect(() => {
    if (!item?.override_window?.expires_at) return;
    const handle = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(handle);
  }, [item?.override_window?.expires_at]);

  if (loading) return html`<${LoadingSkeleton} />`;
  if (!item) return null;

  const state = String(item.state || '').toLowerCase();
  const matchPassed = state === 'needs_approval' || state === 'pending_approval';
  const canSnooze = ['needs_approval', 'pending_approval', 'needs_info', 'validated', 'failed_post'].includes(state);
  const isSnoozed = state === 'snoozed';
  const snoozedUntil = item.metadata?.snoozed_until || item.snoozed_until;

  return html`
    <div class="cl-thread-sidebar">
      <style>${THREAD_SIDEBAR_CSS}</style>

      <${ResubmissionBanner} item=${item} />
      <${OverrideWindowBanner} window_=${item.override_window} onUndo=${onUndoOverride} nowMs=${nowMs} />
      <${WaitingBanner} waiting=${item.waiting_condition} />
      <${FraudFlagsBanner} flags=${item.fraud_flags} />

      <${InvoiceSection} item=${item} />
      <${MatchSection} item=${item} />
      <${VendorSection} item=${item} />
      <${LinkedBoxesSection} links=${boxLinks} />
      <${AgentActionsSection} item=${item} auditEvents=${auditEvents} />

      <div class="cl-ts-actions-bar">
        ${matchPassed ? html`
          <button
            class="cl-ts-approve-btn"
            onClick=${() => onApprove && onApprove(item)}
          >Approve</button>
        ` : ''}
        ${canSnooze && onSnooze ? html`
          <button
            class="cl-ts-snooze-btn"
            onClick=${() => onSnooze(item)}
          >Snooze</button>
        ` : ''}
        ${isSnoozed ? html`
          <div class="cl-ts-snoozed-notice">
            Snoozed until ${snoozedUntil ? new Date(snoozedUntil).toLocaleString() : 'later'}
          </div>
        ` : ''}
        <input
          class="cl-ts-query-input"
          type="text"
          placeholder="Ask about this vendor or invoice..."
          onKeyDown=${(e) => {
            if (e.key === 'Enter' && e.target.value.trim() && onQuery) {
              onQuery(e.target.value.trim(), item);
              e.target.value = '';
            }
          }}
        />
      </div>
    </div>
  `;
}
