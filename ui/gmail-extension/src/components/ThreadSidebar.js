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
import { useState, useEffect, useRef } from 'preact/hooks';
import InviteVendorModal from './InviteVendorModal.js';
import BudgetPausedBanner from './BudgetPausedBanner.js';

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
.cl-ts-awaiting-approval {
  padding: 10px 12px; border-radius: 8px;
  background: #EFF6FF; border: 1px solid #DBEAFE;
  margin-bottom: 8px;
}
.cl-ts-awaiting-approval-title {
  font: 600 13px/1.3 'DM Sans', sans-serif; color: #1D4ED8;
}
.cl-ts-awaiting-approval-sub {
  font: 400 12px/1.4 'DM Sans', sans-serif; color: #475569; margin-top: 2px;
}
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
.cl-ts-query-input:disabled { background: #F1F5F9; color: #94A3B8; cursor: not-allowed; }

/* Conversational Q&A log — thesis §6.8 ("plain English questions
   answered with live ERP data"). Shown above the input; scroll caps
   at ~220px so the sidebar never grows unbounded. */
.cl-ts-qa-log {
  display: flex; flex-direction: column; gap: 8px;
  max-height: 240px; overflow-y: auto; padding: 4px 2px;
}
.cl-ts-qa-row { display: flex; flex-direction: column; gap: 4px; }
.cl-ts-qa-q {
  align-self: flex-end; max-width: 92%;
  background: #0A1628; color: #fff; padding: 7px 11px;
  border-radius: 12px 12px 2px 12px;
  font: 500 12px/1.4 'DM Sans', sans-serif;
  word-wrap: break-word;
}
.cl-ts-qa-a {
  align-self: flex-start; max-width: 96%;
  background: #F1F5F9; color: #0A1628; padding: 7px 11px;
  border-radius: 12px 12px 12px 2px;
  font: 400 12px/1.45 'DM Sans', sans-serif;
  white-space: pre-wrap; word-wrap: break-word;
}
.cl-ts-qa-a.pending {
  color: #64748B; font-style: italic;
}
.cl-ts-qa-a.error {
  background: #FEF2F2; color: #B91C1C;
}
.cl-ts-qa-a strong { font-weight: 700; color: #0A1628; }
.cl-ts-qa-a em { font-style: italic; color: #0A1628; }
.cl-ts-qa-a code {
  font: 600 11px/1.3 'Geist Mono', ui-monospace, monospace;
  background: #E2E8F0; color: #0A1628;
  padding: 1px 5px; border-radius: 4px;
}
.cl-ts-qa-a ul {
  margin: 4px 0 2px; padding-left: 18px;
}
.cl-ts-qa-a li { margin: 2px 0; }
.cl-ts-qa-a .cl-ts-ref {
  display: inline-block; padding: 0 5px;
  background: rgba(0, 214, 126, 0.12); color: #059669;
  border-radius: 3px; font-weight: 600;
  cursor: pointer; text-decoration: none;
  font-variant-numeric: tabular-nums;
}
.cl-ts-qa-a .cl-ts-ref:hover { background: rgba(0, 214, 126, 0.22); }

/* Streaming caret — blinks while Claude is still writing */
.cl-ts-qa-a .cl-ts-caret {
  display: inline-block; width: 7px; height: 13px; vertical-align: text-bottom;
  background: #00D67E; margin-left: 2px; animation: cl-ts-blink 0.9s steps(2) infinite;
}
@keyframes cl-ts-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

/* Flash highlight when a reference chip scrolls an audit row into view */
.cl-ts-audit-flash {
  animation: cl-ts-flash 1.5s ease-out;
  border-radius: 4px;
}
@keyframes cl-ts-flash {
  0%, 20% { background: rgba(0, 214, 126, 0.25); }
  100% { background: transparent; }
}

/* Suggested starter questions — shown when the Q&A log is empty */
.cl-ts-suggestions {
  display: flex; flex-direction: column; gap: 6px; margin-bottom: 6px;
}
.cl-ts-suggestions-label {
  font: 600 10px/1.2 'DM Sans', sans-serif; color: #94A3B8;
  text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 2px;
}
.cl-ts-suggestion-chip {
  text-align: left; padding: 7px 10px; border-radius: 8px;
  border: 1px solid #E2E8F0; background: #FBFCFD; color: #0A1628;
  font: 500 12px/1.35 'DM Sans', sans-serif; cursor: pointer;
  transition: background 0.12s, border-color 0.12s;
}
.cl-ts-suggestion-chip:hover {
  background: #fff; border-color: #00D67E; color: #059669;
}

/* Feedback link + dialog at the bottom of the sidebar */
.cl-ts-footer {
  padding: 10px 16px 14px; border-top: 1px solid #E2E8F0; margin-top: 8px;
  display: flex; justify-content: center; gap: 12px;
  font: 500 11px/1.3 'DM Sans', sans-serif;
}
.cl-ts-footer-link {
  color: #94A3B8; cursor: pointer; background: transparent; border: 0;
  padding: 0; font: inherit; text-decoration: none;
}
.cl-ts-footer-link:hover { color: #00D67E; }

.cl-ts-feedback-overlay {
  position: fixed; inset: 0; background: rgba(10, 22, 40, 0.4); z-index: 99999;
  display: flex; align-items: center; justify-content: center;
}
.cl-ts-feedback-modal {
  background: #fff; border-radius: 12px; padding: 20px; width: 92%; max-width: 420px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.2); font-family: 'DM Sans', sans-serif;
}
.cl-ts-feedback-modal h3 {
  margin: 0 0 4px; font: 700 16px/1.3 'Instrument Sans','DM Sans',sans-serif;
  color: #0A1628;
}
.cl-ts-feedback-modal .muted {
  margin: 0 0 14px; font-size: 12px; color: #64748B;
}
.cl-ts-feedback-kinds {
  display: flex; gap: 6px; margin-bottom: 10px;
}
.cl-ts-feedback-kind {
  padding: 6px 10px; border-radius: 999px; border: 1px solid #E2E8F0;
  background: #fff; color: #475569; font: 500 12px/1 'DM Sans', sans-serif;
  cursor: pointer;
}
.cl-ts-feedback-kind.active {
  background: #0A1628; color: #fff; border-color: #0A1628;
}
.cl-ts-feedback-textarea {
  width: 100%; box-sizing: border-box; min-height: 96px; padding: 10px 12px;
  border: 1px solid #E2E8F0; border-radius: 8px; font: 400 13px/1.45 'DM Sans', sans-serif;
  color: #0A1628; resize: vertical;
}
.cl-ts-feedback-textarea:focus {
  outline: none; border-color: #00D67E; box-shadow: 0 0 0 3px rgba(0, 214, 126, 0.15);
}
.cl-ts-feedback-actions {
  display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px;
}
.cl-ts-feedback-btn {
  padding: 8px 14px; border-radius: 8px; border: 0; cursor: pointer;
  font: 600 13px/1 'DM Sans', sans-serif;
}
.cl-ts-feedback-btn.secondary {
  background: transparent; color: #64748B;
}
.cl-ts-feedback-btn.primary {
  background: #00D67E; color: #0A1628;
}
.cl-ts-feedback-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.cl-ts-feedback-thanks {
  padding: 14px; text-align: center; color: #059669;
  font: 600 13px/1.4 'DM Sans', sans-serif;
}

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
.cl-ts-banner.onboarding-progress { background: #EFF6FF; border-color: #BFDBFE; color: #1E3A8A; }
.cl-ts-banner.onboarding-progress .cl-ts-banner-icon { background: #1E3A8A; color: #EFF6FF; }
.cl-ts-banner.onboarding-progress .cl-ts-banner-link {
  color: #1E3A8A; text-decoration: underline; cursor: pointer;
}
.cl-ts-banner.onboarding-invite { background: #FEFCE8; border-color: #FDE68A; color: #92400E; }
.cl-ts-banner.onboarding-invite .cl-ts-banner-icon { background: #CA8A04; color: #FEFCE8; }
.cl-ts-banner.onboarding-invite .cl-ts-banner-detail-wide { margin-bottom: 6px; }
.cl-ts-banner-btn-onboard {
  background: #CA8A04; color: #fff; border: none; padding: 6px 10px;
  border-radius: 4px; font-size: 12px; font-weight: 600; cursor: pointer;
}
.cl-ts-banner-btn-onboard:hover { background: #A16207; }
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
//
// Returns '' on empty input so callers using humanizeEventType on
// optional fields (reason, reasoning_summary) don't render a
// placeholder for absent values.
function humanizeEventType(raw, { fallback = '' } = {}) {
  if (!raw) return fallback;
  const s = String(raw).trim();
  if (!s) return fallback;
  // If the string is already humanized (has a space and no snake_case),
  // pass it through unchanged so human-written summaries aren't mangled.
  if (s.includes(' ') && !s.includes('_')) return s.length > 120 ? s.slice(0, 117) + '…' : s;
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

// Mini markdown renderer for agent Q&A answer bubbles.
// Handles only what the system prompt is told to use:
//   **bold**, *italic*, `code`, and "- " bullets (one per line).
// Also substitutes reference timestamps (HH:MM) with clickable chips.
// Inline content is XSS-safe: we never set innerHTML from Claude's
// output — every piece is returned as a Preact vnode or plain string.
function renderInlineMarkdown(text, { references = [], onReferenceClick }) {
  if (!text) return '';
  const refByLabel = new Map();
  (references || []).forEach((r) => {
    if (r && r.label) refByLabel.set(String(r.label), r);
  });
  // Tokenise inline: **bold**, *italic*, `code`, HH:MM timestamps, plain text.
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\b(?:[01]?\d|2[0-3]):[0-5]\d\b)/g;
  const out = [];
  let lastIndex = 0;
  let key = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      out.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith('**')) {
      out.push(html`<strong key=${++key}>${token.slice(2, -2)}</strong>`);
    } else if (token.startsWith('*')) {
      out.push(html`<em key=${++key}>${token.slice(1, -1)}</em>`);
    } else if (token.startsWith('`')) {
      out.push(html`<code key=${++key}>${token.slice(1, -1)}</code>`);
    } else if (refByLabel.has(token)) {
      const ref = refByLabel.get(token);
      out.push(html`<a
        key=${++key}
        class="cl-ts-ref"
        role="button"
        tabindex="0"
        onClick=${(e) => { e.preventDefault(); onReferenceClick?.(ref); }}
        onKeyDown=${(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onReferenceClick?.(ref);
          }
        }}
      >${token}</a>`);
    } else {
      out.push(token);
    }
    lastIndex = pattern.lastIndex;
  }
  if (lastIndex < text.length) out.push(text.slice(lastIndex));
  return out;
}

function renderAnswerMarkdown(answer, { references = [], onReferenceClick, streaming = false }) {
  if (!answer) {
    return streaming
      ? html`<span class="cl-ts-caret"></span>`
      : '';
  }
  const lines = String(answer).split('\n');
  const blocks = [];
  let bulletBuffer = [];
  const flushBullets = (keyPrefix) => {
    if (bulletBuffer.length === 0) return;
    blocks.push(html`<ul key=${keyPrefix}>
      ${bulletBuffer.map((line, i) => html`
        <li key=${i}>${renderInlineMarkdown(line, { references, onReferenceClick })}</li>
      `)}
    </ul>`);
    bulletBuffer = [];
  };
  lines.forEach((line, i) => {
    const trimmed = line.replace(/^\s+/, '');
    if (/^([-•*])\s+/.test(trimmed)) {
      bulletBuffer.push(trimmed.replace(/^([-•*])\s+/, ''));
    } else {
      flushBullets(`ul-${i}`);
      if (trimmed.length > 0) {
        blocks.push(html`<div key=${`p-${i}`}>${renderInlineMarkdown(line, { references, onReferenceClick })}</div>`);
      } else if (blocks.length > 0) {
        // Blank line → paragraph break
        blocks.push(html`<div key=${`br-${i}`} style="height:4px"></div>`);
      }
    }
  });
  flushBullets('ul-end');
  if (streaming) {
    blocks.push(html`<span key="caret" class="cl-ts-caret"></span>`);
  }
  return blocks;
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
  // Hooks MUST be called in the same order every render. If window_
  // flips null ↔ object, bailing out via `return null` BEFORE useState
  // changes the hook count and Preact throws
  // "Rendered fewer hooks than expected." Call useState first,
  // unconditionally, then apply the guards.
  const [undoing, setUndoing] = useState(false);
  if (!window_ || !window_.expires_at) return null;
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

function VendorOnboardingPromptBanner({
  status,
  onInvite,
  onNavigatePipeline,
}) {
  if (!status) return null;
  const session = status.active_session;
  if (session) {
    const stateLabel = String(session.state || '').replace(/_/g, ' ');
    return html`
      <div class="cl-ts-banner onboarding-progress">
        <div class="cl-ts-banner-icon">🏢</div>
        <div class="cl-ts-banner-body">
          <div class="cl-ts-banner-title">Onboarding in progress</div>
          <div class="cl-ts-banner-detail">
            Stage: ${stateLabel}
            ${onNavigatePipeline ? html` · <span
              class="cl-ts-banner-link"
              role="button"
              tabIndex=${0}
              onClick=${() => onNavigatePipeline()}
            >view pipeline</span>` : ''}
          </div>
        </div>
      </div>
    `;
  }
  if (!status.suggest_invite) return null;
  return html`
    <div class="cl-ts-banner onboarding-invite">
      <div class="cl-ts-banner-icon">⚠</div>
      <div class="cl-ts-banner-body">
        <div class="cl-ts-banner-title">Vendor not onboarded</div>
        <div class="cl-ts-banner-detail cl-ts-banner-detail-wide">
          KYC + bank verification is not complete. Invite them before paying.
        </div>
        <button
          class="cl-ts-banner-btn-onboard"
          onClick=${onInvite}
        >Invite to onboarding</button>
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
              // Humanize whichever string wins — summary/decision_reason/event_type
              // are all free-form and frequently raw snake_case from the audit pipeline.
              const what = humanizeEventType(
                e.summary || e.decision_reason || e.event_type,
                { fallback: 'Action' },
              );
              // Skip "why" if it is identical to "what" (the backend sometimes
              // fills reason with the same snake_case token as event_type).
              const rawWhy = e.reasoning_summary || e.reasoning || e.reason || '';
              const humanizedWhy = humanizeEventType(rawWhy);
              const why = humanizedWhy && humanizedWhy !== what ? humanizedWhy : '';
              const next = e.next_action || e.next_step || '';
              const isAgent = (e.actor || e.actor_type || '') !== 'user';
              // data-audit-ts carries the full timestamp (up to minutes) so the
              // Q&A log's reference chips can scroll the matching row into view.
              const auditTs = String(e.ts || e.created_at || '').slice(0, 16).replace('T', ' ');
              return html`
                <li key=${e.id || e.ts} data-audit-ts=${auditTs}>
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
  onSnooze,
  onQuery,
  onUndoOverride,
  onSubmitFeedback,
  fetchBoxLinks,
  fetchOnboardingStatus,
  inviteVendorApi,
  orgId,
  toast,
  loading,
  budgetStatus,
  onBudgetOverride,
  budgetOverridePending,
}) {
  const [boxLinks, setBoxLinks] = useState([]);
  const [onboardingStatus, setOnboardingStatus] = useState(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [nowMs, setNowMs] = useState(Date.now());
  // Conversational Q&A log. Each row:
  //   { q, a, references: [...], status: 'pending'|'streaming'|'done'|'error' }
  // Reset when the user switches to a different invoice — conversations
  // are per-Box, not per-session.
  const [qaLog, setQaLog] = useState([]);
  const [queryPending, setQueryPending] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  // Feedback dialog state
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackKind, setFeedbackKind] = useState('bug');
  const [feedbackText, setFeedbackText] = useState('');
  const [feedbackSending, setFeedbackSending] = useState(false);
  const [feedbackSent, setFeedbackSent] = useState(false);

  const openFeedback = () => {
    setFeedbackKind('bug');
    setFeedbackText('');
    setFeedbackSent(false);
    setFeedbackOpen(true);
  };

  const submitFeedback = async () => {
    const text = feedbackText.trim();
    if (!text || feedbackSending) return;
    setFeedbackSending(true);
    try {
      if (typeof onSubmitFeedback === 'function') {
        await onSubmitFeedback({
          message: text,
          kind: feedbackKind,
          ap_item_id: item?.id || null,
          page: 'sidebar',
        });
      }
      setFeedbackSent(true);
      // Auto-close after 1.2s so the user sees the confirmation
      setTimeout(() => {
        setFeedbackOpen(false);
        setFeedbackText('');
      }, 1200);
    } catch (err) {
      // Keep the modal open; user can retry or copy their message.
      alert('Could not send feedback. Please try again.');  // eslint-disable-line no-alert
    } finally {
      setFeedbackSending(false);
    }
  };
  // Abort controller for the in-flight stream. We cancel on invoice
  // switch (real Anthropic $ saver: user moves on, we stop generating
  // tokens they'll never see) and before starting a new stream.
  const activeAbortRef = useRef(null);

  const cancelActiveStream = (reason) => {
    const controller = activeAbortRef.current;
    activeAbortRef.current = null;
    if (controller) {
      try { controller.abort(reason || 'cancelled'); } catch (_) { /* ignore */ }
    }
  };

  useEffect(() => {
    cancelActiveStream('invoice_changed');
    setQaLog([]);
    setQueryPending(false);
    setSuggestions([]);
  }, [item?.id]);

  // Also cancel on unmount (sidebar closed, tab closed).
  useEffect(() => () => cancelActiveStream('sidebar_unmounted'), []);

  // Pull suggested starter questions when we have a focus invoice and
  // no conversation yet. The backend tailors them to invoice state.
  useEffect(() => {
    if (!item?.id || qaLog.length > 0) return;
    if (typeof onQuery !== 'function' || !onQuery.fetchSuggestions) return;
    let cancelled = false;
    onQuery.fetchSuggestions(item)
      .then((list) => { if (!cancelled) setSuggestions(Array.isArray(list) ? list.slice(0, 4) : []); })
      .catch(() => { if (!cancelled) setSuggestions([]); });
    return () => { cancelled = true; };
  }, [item?.id, qaLog.length, onQuery]);

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

  // Pull the vendor's onboarding status so we can show the "not
  // onboarded yet — invite" banner or the "onboarding in progress"
  // badge. Fails silently — if the endpoint isn't available the
  // banner just stays hidden.
  //
  // Fetcher is held in a ref so identity churn (inline arrows from the
  // parent in some call sites) doesn't re-fire the effect. We only
  // refetch when the vendor actually changes.
  const fetchOnboardingStatusRef = useRef(fetchOnboardingStatus);
  useEffect(() => { fetchOnboardingStatusRef.current = fetchOnboardingStatus; }, [fetchOnboardingStatus]);
  useEffect(() => {
    const vendor = item?.vendor_name || item?.vendor;
    const fetcher = fetchOnboardingStatusRef.current;
    if (!vendor || !fetcher) { setOnboardingStatus(null); return; }
    let cancelled = false;
    fetcher(vendor)
      .then((status) => { if (!cancelled) setOnboardingStatus(status || null); })
      .catch(() => { if (!cancelled) setOnboardingStatus(null); });
    return () => { cancelled = true; };
  }, [item?.vendor_name, item?.vendor]);

  // Tick for live countdown when an override window is open
  useEffect(() => {
    if (!item?.override_window?.expires_at) return;
    const handle = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(handle);
  }, [item?.override_window?.expires_at]);

  // Mutate the trailing pending/streaming row. Factored out so stream
  // deltas, references, completion, and error all use the same updater.
  const updateTrailingRow = (patch) => {
    setQaLog((prev) => {
      const next = prev.slice();
      for (let i = next.length - 1; i >= 0; i -= 1) {
        if (next[i].status === 'pending' || next[i].status === 'streaming') {
          next[i] = { ...next[i], ...patch };
          break;
        }
      }
      return next;
    });
  };

  // Submit a query to the agent and stream the response into the log.
  // Streaming pathway (onQuery.stream):
  //   - emits text deltas that we concatenate
  //   - emits a final references array
  //   - resolves on completion or rejects on error
  // Non-streaming fallback (onQuery as a plain function) is still
  // supported for clients that can't handle SSE.
  const submitQuery = async (question) => {
    if (!question || queryPending) return;
    setSuggestions([]);
    // Cancel any in-flight stream before starting a new one. (Rare path
    // — the input is disabled while queryPending — but cheap insurance.)
    cancelActiveStream('superseded');

    // Build history to send — last 3 completed exchanges.
    const history = qaLog
      .filter((r) => r.status === 'done' && r.q && r.a)
      .slice(-3)
      .map((r) => ({ q: r.q, a: r.a }));
    setQaLog((prev) => [...prev, {
      q: question,
      a: '',
      references: [],
      status: 'pending',
    }]);
    setQueryPending(true);

    // Set up abort controller + 30s silence watchdog. Any received
    // chunk (or server ping — which arrives as an SSE comment and
    // resets the reader, not as a delta) resets the watchdog.
    const controller = new AbortController();
    activeAbortRef.current = controller;
    let silenceTimer = null;
    const SILENCE_MS = 30000;
    const resetSilenceTimer = () => {
      if (silenceTimer) clearTimeout(silenceTimer);
      silenceTimer = setTimeout(() => {
        try { controller.abort('silence_timeout'); } catch (_) { /* ignore */ }
      }, SILENCE_MS);
    };
    resetSilenceTimer();

    try {
      if (onQuery && typeof onQuery.stream === 'function') {
        let buffered = '';
        let seenFirstDelta = false;
        await onQuery.stream({
          question,
          item,
          history,
          signal: controller.signal,
          onActivity: resetSilenceTimer,
          onDelta: (chunk) => {
            if (!seenFirstDelta) {
              seenFirstDelta = true;
              updateTrailingRow({ status: 'streaming' });
            }
            buffered += chunk;
            resetSilenceTimer();
            updateTrailingRow({ a: buffered });
          },
          onReferences: (refs) => {
            updateTrailingRow({ references: Array.isArray(refs) ? refs : [] });
          },
        });
        updateTrailingRow({
          status: buffered ? 'done' : 'error',
          a: buffered || 'No answer returned. Try rephrasing.',
        });
      } else if (typeof onQuery === 'function') {
        // Legacy single-shot path.
        const result = await onQuery(question, item);
        const answer = typeof result === 'string'
          ? result
          : (result?.answer || result?.content || '');
        const refs = (typeof result === 'object' && Array.isArray(result?.references))
          ? result.references : [];
        updateTrailingRow({
          a: answer || 'No answer returned. Try rephrasing.',
          references: refs,
          status: answer ? 'done' : 'error',
        });
      } else {
        updateTrailingRow({
          a: 'No query handler wired.',
          status: 'error',
        });
      }
    } catch (err) {
      const reason = controller.signal.reason;
      const isCancellation = err?.name === 'AbortError'
        || /abort/i.test(String(err?.message || ''));
      if (isCancellation && reason === 'invoice_changed') {
        // User moved on — drop the partial row silently. Resetting qaLog
        // in the item-change effect already happened; nothing to do here.
      } else if (isCancellation && reason === 'silence_timeout') {
        updateTrailingRow({
          a: 'The agent stopped responding. The connection may be unstable — try again.',
          status: 'error',
        });
      } else if (isCancellation) {
        updateTrailingRow({ status: 'error', a: 'Cancelled.' });
      } else {
        updateTrailingRow({
          a: String(err?.message || err || 'Query failed'),
          status: 'error',
        });
      }
    } finally {
      if (silenceTimer) clearTimeout(silenceTimer);
      if (activeAbortRef.current === controller) activeAbortRef.current = null;
      setQueryPending(false);
    }
  };

  // Click handler for reference chips inside answers. Looks up the
  // timestamp in the Agent Actions section and scrolls to it with a
  // brief highlight so the user can see which action was cited.
  const handleReferenceClick = (ref) => {
    if (!ref) return;
    const label = String(ref.label || '').trim();
    if (!label) return;
    try {
      const nodes = document.querySelectorAll('[data-audit-ts]');
      for (const node of nodes) {
        const ts = String(node.getAttribute('data-audit-ts') || '');
        if (ts.includes(label)) {
          node.scrollIntoView({ behavior: 'smooth', block: 'center' });
          node.classList.add('cl-ts-audit-flash');
          setTimeout(() => node.classList.remove('cl-ts-audit-flash'), 1600);
          return;
        }
      }
    } catch (_) { /* best effort */ }
  };

  if (loading) return html`<${LoadingSkeleton} />`;
  if (!item) return null;

  const state = String(item.state || '').toLowerCase();
  const needsApproval = state === 'needs_approval' || state === 'pending_approval';
  const canSnooze = ['needs_approval', 'pending_approval', 'needs_info', 'validated', 'failed_post'].includes(state);
  const isSnoozed = state === 'snoozed';
  const snoozedUntil = item.metadata?.snoozed_until || item.snoozed_until;

  return html`
    <div class="cl-thread-sidebar">
      <style>${THREAD_SIDEBAR_CSS}</style>

      <${BudgetPausedBanner}
        status=${budgetStatus}
        onRequestOverride=${onBudgetOverride}
        pending=${budgetOverridePending}
      />
      <${ResubmissionBanner} item=${item} />
      <${OverrideWindowBanner} window_=${item.override_window} onUndo=${onUndoOverride} nowMs=${nowMs} />
      <${WaitingBanner} waiting=${item.waiting_condition} />
      <${FraudFlagsBanner} flags=${item.fraud_flags} />
      <${VendorOnboardingPromptBanner}
        status=${onboardingStatus}
        onInvite=${() => setInviteOpen(true)}
      />
      ${inviteOpen && inviteVendorApi ? html`<${InviteVendorModal}
        api=${inviteVendorApi}
        orgId=${orgId || 'default'}
        defaultVendor=${item?.vendor_name || item?.vendor || ''}
        defaultEmail=${item?.vendor_email || item?.sender_email || ''}
        toast=${toast}
        onClose=${() => setInviteOpen(false)}
        onSuccess=${(res) => {
          setInviteOpen(false);
          setOnboardingStatus({
            vendor_name: item?.vendor_name || item?.vendor,
            has_profile: true,
            bank_verified: false,
            active_session: res?.session || null,
            suggest_invite: false,
          });
        }}
      />` : ''}

      <${InvoiceSection} item=${item} />
      <${MatchSection} item=${item} />
      <${VendorSection} item=${item} />
      <${LinkedBoxesSection} links=${boxLinks} />
      <${AgentActionsSection} item=${item} auditEvents=${auditEvents} />

      <div class="cl-ts-actions-bar">
        ${needsApproval ? html`
          <div class="cl-ts-awaiting-approval" role="status">
            <div class="cl-ts-awaiting-approval-title">Awaiting approval in Slack</div>
            <div class="cl-ts-awaiting-approval-sub">Approver notified. Decision returns here.</div>
          </div>
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
        ${qaLog.length > 0 ? html`
          <div class="cl-ts-qa-log">
            ${qaLog.map((row, idx) => html`
              <div class="cl-ts-qa-row" key=${idx}>
                <div class="cl-ts-qa-q">${row.q}</div>
                <div class="cl-ts-qa-a ${
                  row.status === 'pending' ? 'pending'
                    : row.status === 'error' ? 'error' : ''
                }">
                  ${row.status === 'pending'
                    ? 'Thinking…'
                    : renderAnswerMarkdown(row.a, {
                        references: row.references || [],
                        onReferenceClick: handleReferenceClick,
                        streaming: row.status === 'streaming',
                      })}
                </div>
              </div>
            `)}
          </div>
        ` : (suggestions.length > 0 ? html`
          <div class="cl-ts-suggestions">
            <div class="cl-ts-suggestions-label">Ask the agent</div>
            ${suggestions.map((s, idx) => html`
              <button
                class="cl-ts-suggestion-chip"
                key=${idx}
                disabled=${queryPending}
                onClick=${() => submitQuery(s)}
              >${s}</button>
            `)}
          </div>
        ` : '')}
        <input
          class="cl-ts-query-input"
          type="text"
          placeholder=${queryPending ? 'Waiting for answer…' : 'Ask about this vendor or invoice...'}
          disabled=${queryPending}
          onKeyDown=${(e) => {
            if (e.key === 'Enter' && e.target.value.trim() && !queryPending) {
              const q = e.target.value.trim();
              e.target.value = '';
              void submitQuery(q);
            }
          }}
        />
      </div>

      <div class="cl-ts-footer">
        <button type="button" class="cl-ts-footer-link" onClick=${openFeedback}>
          Report issue
        </button>
      </div>

      ${feedbackOpen ? html`
        <div
          class="cl-ts-feedback-overlay"
          onClick=${(e) => { if (e.target === e.currentTarget) setFeedbackOpen(false); }}
        >
          <div class="cl-ts-feedback-modal">
            ${feedbackSent ? html`
              <div class="cl-ts-feedback-thanks">
                ✓ Thanks — we saw it.
              </div>
            ` : html`
              <h3>Send feedback</h3>
              <p class="muted">
                ${item?.vendor_name
                  ? `This includes the current invoice (${item.vendor_name}) as context.`
                  : 'Tell us what you hit or what you want.'}
              </p>
              <div class="cl-ts-feedback-kinds">
                ${[
                  { id: 'bug', label: '🐞 Bug' },
                  { id: 'suggestion', label: '💡 Idea' },
                  { id: 'praise', label: '🎉 Love it' },
                  { id: 'other', label: '💬 Other' },
                ].map((k) => html`
                  <button
                    type="button"
                    key=${k.id}
                    class="cl-ts-feedback-kind ${feedbackKind === k.id ? 'active' : ''}"
                    onClick=${() => setFeedbackKind(k.id)}
                  >${k.label}</button>
                `)}
              </div>
              <textarea
                class="cl-ts-feedback-textarea"
                value=${feedbackText}
                onInput=${(e) => setFeedbackText(e.target.value)}
                placeholder=${
                  feedbackKind === 'bug'
                    ? 'What happened? What did you expect?'
                    : feedbackKind === 'suggestion'
                      ? 'What should we build?'
                      : feedbackKind === 'praise'
                        ? 'Tell us what worked well.'
                        : 'What did you want to tell us?'
                }
                disabled=${feedbackSending}
                autofocus
              ></textarea>
              <div class="cl-ts-feedback-actions">
                <button
                  type="button"
                  class="cl-ts-feedback-btn secondary"
                  onClick=${() => setFeedbackOpen(false)}
                  disabled=${feedbackSending}
                >Cancel</button>
                <button
                  type="button"
                  class="cl-ts-feedback-btn primary"
                  onClick=${submitFeedback}
                  disabled=${feedbackSending || !feedbackText.trim()}
                >${feedbackSending ? 'Sending…' : 'Send'}</button>
              </div>
            `}
          </div>
        </div>
      ` : ''}
    </div>
  `;
}
