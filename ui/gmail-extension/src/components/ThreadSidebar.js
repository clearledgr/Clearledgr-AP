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
import { useState, useEffect } from 'preact/hooks';

// ---------------------------------------------------------------------------
// CSS
// ---------------------------------------------------------------------------

const THREAD_SIDEBAR_CSS = `
.cl-thread-sidebar { padding: 0; }
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
.cl-ts-query-input {
  width: 100%; padding: 10px 12px; border: 1px solid #E2E8F0; border-radius: 8px;
  font-size: 13px; color: #0A1628; background: #FBFCFD; font-family: inherit;
}
.cl-ts-query-input:focus { outline: none; border-color: #00D67E; box-shadow: 0 0 0 3px rgba(0, 214, 126, 0.15); }
.cl-ts-query-input::placeholder { color: #94A3B8; }
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
        <div style="font-size: 12px; color: #92400E; margin-top: 4px; padding: 6px 8px; background: #FEFCE8; border-radius: 6px;">
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
      ${item.last_payment_date ? html`
        <div class="cl-ts-row" style="margin-top: 8px; padding-top: 8px; border-top: 1px solid #E2E8F0;">
          <span class="cl-ts-label">Last payment</span>
          <span class="cl-ts-value">${formatDate(item.last_payment_date)}</span>
        </div>
      ` : ''}
    </div>
  `;
}

function AgentActionsSection({ item, auditEvents }) {
  const events = (auditEvents || []).slice(0, 10);
  return html`
    <div class="cl-ts-section">
      <div class="cl-ts-section-title"><img src="${typeof chrome !== 'undefined' && chrome.runtime ? chrome.runtime.getURL('icons/icon16.png') : ''}" alt="" style="width:12px;height:12px;vertical-align:-1px;margin-right:4px;opacity:0.7;" />Agent Actions</div>
      ${events.length > 0
        ? html`
          <ul class="cl-ts-timeline">
            ${events.map(e => {
              // Thesis §6.6: "what the agent did, why it did it, and what happens next"
              // §10: Clearledgr icon marks agent-initiated actions (not human)
              const what = e.summary || e.decision_reason || e.event_type?.replace(/_/g, ' ') || 'Action';
              const why = e.reasoning_summary || e.reasoning || e.reason || '';
              const next = e.next_action || e.next_step || '';
              const isAgent = (e.actor || e.actor_type || '') !== 'user';
              return html`
                <li key=${e.id || e.ts}>
                  ${isAgent ? html`<img src="${typeof chrome !== 'undefined' && chrome.runtime ? chrome.runtime.getURL('icons/icon16.png') : ''}" alt="agent" style="width:10px;height:10px;vertical-align:-1px;margin-right:3px;opacity:0.6;" />` : ''}
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

// ---------------------------------------------------------------------------
// Linked Boxes — §5.1: show linked vendor onboarding sessions
// ---------------------------------------------------------------------------

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
      ${links.map(link => {
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

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ThreadSidebar({ item, auditEvents, onApprove, onSnooze, onQuery, fetchBoxLinks }) {
  const [boxLinks, setBoxLinks] = useState([]);

  useEffect(() => {
    if (!item?.id || !fetchBoxLinks) return;
    let cancelled = false;
    fetchBoxLinks(item.id, 'invoice').then(links => {
      if (!cancelled) setBoxLinks(links || []);
    }).catch(() => {
      if (!cancelled) setBoxLinks([]);
    });
    return () => { cancelled = true; };
  }, [item?.id, fetchBoxLinks]);

  if (!item) return null;

  const state = String(item.state || '').toLowerCase();
  const matchPassed = state === 'needs_approval' || state === 'pending_approval';
  const canSnooze = ['needs_approval', 'pending_approval', 'needs_info', 'validated', 'failed_post'].includes(state);
  const isSnoozed = state === 'snoozed';

  return html`
    <div class="cl-thread-sidebar">
      <style>${THREAD_SIDEBAR_CSS}</style>
      <${InvoiceSection} item=${item} />
      <${MatchSection} item=${item} />
      <${VendorSection} item=${item} />
      <${LinkedBoxesSection} links=${boxLinks} />
      <${AgentActionsSection} item=${item} auditEvents=${auditEvents} />

      <!-- §6.6: Below the four sections — approve button + snooze + query field -->
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
            style="padding:6px 14px;border:1px solid #CA8A04;border-radius:6px;background:#FEFCE8;color:#92400E;font:500 12px/1.2 'DM Sans',sans-serif;cursor:pointer;"
            onClick=${() => onSnooze(item)}
          >Snooze</button>
        ` : ''}
        ${isSnoozed ? html`
          <div style="font:500 11px/1.3 'DM Sans',sans-serif;color:#CA8A04;padding:4px 0;">
            Snoozed until ${item.metadata?.snoozed_until ? new Date(item.metadata.snoozed_until).toLocaleString() : 'later'}
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
