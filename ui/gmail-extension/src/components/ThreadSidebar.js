/**
 * ThreadSidebar — Phase 3.3.b (DESIGN_THESIS.md §6.6).
 *
 * Four fixed sections in strict order:
 *   1. Invoice  — amount due, invoice reference, PO, due date, terms
 *   2. 3-Way Match — PO / GRN / Invoice rows with match-status icons
 *   3. Vendor — vendor name, YTD spend, risk score, IBAN status
 *   4. Agent Actions — condensed timeline of what the agent did
 *
 * Renders inside the existing SidebarApp when a thread-linked AP item
 * is selected. The full WorkPanel still exists for the pipeline view
 * (detail review, approvals, tasks, files) — ThreadSidebar is the
 * simplified "at a glance" view the thesis specifies for the thread
 * context.
 *
 * Design rules from the thesis:
 *   - "Clearledgr sidebar has four fixed sections in strict order"
 *   - "The sidebar loads in less than two seconds"
 *   - "The sidebar never shows more than one invoice"
 */
import { html } from 'htm/preact';

// ---------------------------------------------------------------------------
// CSS
// ---------------------------------------------------------------------------

const THREAD_SIDEBAR_CSS = `
.cl-thread-sidebar { padding: 0; }
.cl-ts-section { padding: 12px 16px; border-bottom: 1px solid #E5EBF0; }
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
.cl-ts-match-icon.warn { color: #D97706; }
.cl-ts-match-icon.fail { color: #DC2626; }
.cl-ts-match-icon.na { color: #9CA3AF; }
.cl-ts-match-label { font-size: 12px; color: #0A1628; flex: 1; }
.cl-ts-match-detail { font-size: 11px; color: #5C6B7A; }
.cl-ts-risk-badge {
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.cl-ts-risk-low { background: #ECFDF5; color: #059669; }
.cl-ts-risk-medium { background: #FEF9EE; color: #92400E; }
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
.cl-ts-timeline-time { font-size: 10px; color: #9CA3AF; display: block; }
.cl-ts-iban-pill {
  display: inline-block; padding: 1px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.cl-ts-iban-verified { background: #ECFDF5; color: #059669; }
.cl-ts-iban-unverified { background: #FEF2F2; color: #991B1B; }
.cl-ts-iban-pending { background: #FEF9EE; color: #92400E; }
.cl-ts-expand-btn {
  background: none; border: none; color: #00D67E; font-size: 12px;
  font-weight: 600; cursor: pointer; padding: 4px 0; font-family: inherit;
}
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
      ${item.payment_terms ? html`
        <div class="cl-ts-row">
          <span class="cl-ts-label">Terms</span>
          <span class="cl-ts-value">${item.payment_terms}</span>
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
      ${item.match_exception_reason ? html`
        <div style="font-size: 12px; color: #92400E; margin-top: 4px; padding: 6px 8px; background: #FEF9EE; border-radius: 6px;">
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
  const events = (auditEvents || []).slice(0, 5);
  return html`
    <div class="cl-ts-section">
      <div class="cl-ts-section-title">Agent Actions</div>
      ${events.length > 0
        ? html`
          <ul class="cl-ts-timeline">
            ${events.map(e => html`
              <li key=${e.id || e.ts}>
                ${e.summary || e.decision_reason || e.event_type?.replace(/_/g, ' ') || 'Action'}
                <span class="cl-ts-timeline-time">${formatTimeAgo(e.ts || e.created_at)}</span>
              </li>
            `)}
          </ul>
          ${(auditEvents || []).length > 5 ? html`
            <button class="cl-ts-expand-btn">Show all ${auditEvents.length} actions</button>
          ` : ''}
        `
        : html`<div style="font-size: 12px; color: #9CA3AF;">No agent actions yet</div>`
      }
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ThreadSidebar({ item, auditEvents }) {
  if (!item) return null;

  return html`
    <div class="cl-thread-sidebar">
      <style>${THREAD_SIDEBAR_CSS}</style>
      <${InvoiceSection} item=${item} />
      <${MatchSection} item=${item} />
      <${VendorSection} item=${item} />
      <${AgentActionsSection} item=${item} auditEvents=${auditEvents} />
    </div>
  `;
}
