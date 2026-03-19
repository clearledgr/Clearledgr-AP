/** ThreadContext — tabbed context panel (email, web, approvals, ERP) */
import { h } from 'preact';
import { useState, useCallback } from 'preact/hooks';
import htm from 'htm';
import store from '../utils/store.js';
import {
  formatDateTime, formatAgeSeconds, formatAmount, trimText,
  normalizeBudgetContext, budgetStatusTone,
} from '../utils/formatters.js';

const html = htm.bind(h);

const TABS = [
  { id: 'email', label: 'Email' },
  { id: 'web', label: 'Web' },
  { id: 'approvals', label: 'Approvals' },
  { id: 'erp', label: 'ERP' },
];

function ContextRow({ children }) {
  return html`<div class="cl-context-row">${children}</div>`;
}

function EmailTab({ item, ctx, freshness }) {
  const email = ctx.email || {};
  const sources = Array.isArray(email.sources) ? email.sources : [];
  return html`
    <div class="cl-context-meta">${Number(email.source_count || 0)} linked source${Number(email.source_count || 0) !== 1 ? 's' : ''}</div>
    ${freshness && html`<${ContextRow}>${freshness}</>`}
    ${sources.length === 0 && html`<div class="cl-empty">No linked email sources yet.</div>`}
    ${sources.slice(0, 5).map((src, i) => html`
      <${ContextRow} key=${i}>
        <div><strong>${src.subject || item.subject || 'Email source'}</strong></div>
        <div>${src.sender || item.sender || 'Unknown sender'}</div>
        ${src.detected_at && html`<div>${formatDateTime(src.detected_at)}</div>`}
      <//>
    `)}
  `;
}

function WebTab({ item, ctx, freshness, agentInsight }) {
  const web = ctx.web || {};
  const portals = [...(Array.isArray(web.payment_portals) ? web.payment_portals : []), ...(Array.isArray(web.related_portals) ? web.related_portals : [])];
  const procurement = Array.isArray(web.procurement) ? web.procurement : [];
  const bank = Array.isArray(web.bank_transactions) ? web.bank_transactions : [];
  const sheets = Array.isArray(web.spreadsheets) ? web.spreadsheets : [];
  const dms = Array.isArray(web.dms_documents) ? web.dms_documents : [];
  const events = Array.isArray(web.recent_browser_events) ? web.recent_browser_events : [];
  const coverage = web.connector_coverage || {};
  const tabs = Array.isArray(agentInsight?.relatedTabs) ? agentInsight.relatedTabs : [];

  return html`
    <div class="cl-context-meta">${web.browser_event_count || 0} events \u00b7 ${agentInsight?.relatedCount || 0} tabs</div>
    <${ContextRow}>
      <div><strong>Coverage:</strong> portals ${coverage.payment_portal ? 'yes' : 'no'} \u00b7 procurement ${coverage.procurement ? 'yes' : 'no'} \u00b7 bank ${coverage.bank ? 'yes' : 'no'} \u00b7 sheets ${coverage.spreadsheets ? 'yes' : 'no'} \u00b7 dms ${coverage.dms ? 'yes' : 'no'}</div>
    <//>
    ${freshness && html`<${ContextRow}>${freshness}</>`}
    ${portals.length === 0 && html`<div class="cl-empty">No vendor portal sources detected.</div>`}
    ${portals.slice(0, 3).map((p, i) => html`<${ContextRow} key="p${i}"><div><strong>${trimText(p.url || 'Portal', 70)}</strong></div>${p.detected_at && html`<div>${formatDateTime(p.detected_at)}</div>`}</>`)}
    ${procurement.slice(0, 2).map((e, i) => html`<${ContextRow} key="pr${i}"><div><strong>${trimText(e.ref || e.source_ref || e.url || 'Procurement', 70)}</strong></div></>`)}
    ${bank.slice(0, 3).map((m, i) => html`<${ContextRow} key="b${i}"><div><strong>${trimText(m.description || m.reference || 'Bank match', 70)}</strong></div><div>${item.currency || 'USD'} ${m.amount ?? 0}</div></>`)}
    ${sheets.slice(0, 2).map((s, i) => html`<${ContextRow} key="s${i}"><div><strong>${trimText(s.spreadsheet_id || s.reference || 'Spreadsheet', 70)}</strong></div></>`)}
    ${dms.slice(0, 2).map((d, i) => html`<${ContextRow} key="d${i}"><div><strong>${trimText(d.ref || d.source_ref || 'DMS document', 70)}</strong></div></>`)}
    ${tabs.slice(0, 3).map((t, i) => html`<${ContextRow} key="t${i}"><div><strong>${trimText(t.title || t.url || 'Browser tab', 80)}</strong></div><div>${t.host || trimText(t.url || '', 64)}</div></>`)}
    ${events.slice(0, 3).map((ev, i) => {
      const status = String(ev?.status || 'unknown').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      const tone = String(ev?.status || '').toLowerCase() === 'failed' ? 'error' : String(ev?.status || '').toLowerCase() === 'completed' ? 'success' : 'info';
      return html`<div key="ev${i}" class="cl-context-row cl-context-row-browser">
        <div class="cl-context-row-browser-main">
          <strong>${String(ev?.tool_name || 'Browser action').replace(/_/g, ' ')}</strong>
          <span class="cl-context-row-browser-status" data-tone=${tone}>${status}</span>
        </div>
        ${ev?.detail && html`<div>${trimText(ev.detail, 110)}</div>`}
      </div>`;
    })}
  `;
}

function ApprovalsTab({ item, ctx, freshness }) {
  const approvals = ctx.approvals || {};
  const latest = approvals.latest || null;
  const teams = approvals.teams || {};
  const payroll = approvals.payroll || ctx.payroll || {};
  const budgetContext = normalizeBudgetContext(ctx, item);
  const budgetStatus = budgetContext.status ? String(budgetContext.status).replace(/_/g, ' ') : '';
  const threadPreview = Array.isArray(approvals.slack?.thread_preview) ? approvals.slack.thread_preview : [];

  return html`
    <div class="cl-context-meta">${approvals.count || 0} approval record${(approvals.count || 0) !== 1 ? 's' : ''}</div>
    ${latest
      ? html`<${ContextRow}><div><strong>Latest:</strong> ${latest.status || 'pending'}</div></>`
      : html`<div class="cl-empty">No approval record yet.</div>`}
    ${budgetStatus && html`<div class="cl-context-row ${budgetStatusTone(budgetContext.status)}"><div><strong>Budget:</strong> ${budgetStatus}</div></div>`}
    ${budgetContext.checks.slice(0, 3).map((check, i) => html`
      <${ContextRow} key=${i}>
        <div><strong>${check.name || 'Budget'}:</strong> ${check.status || 'unknown'}</div>
        <div>${formatAmount(check.remaining, item.currency || 'USD')} remaining \u00b7 ${formatAmount(check.invoice_amount, item.currency || 'USD')} invoice</div>
      <//>
    `)}
    ${budgetContext.requiresDecision && html`<div class="cl-context-row cl-context-warning"><div>Decision required: approve override, request adjustment, or reject.</div></div>`}
    ${(teams.channel || teams.state || teams.thread || teams.message_id) && html`<${ContextRow}><div><strong>Teams:</strong> ${teams.state || teams.channel || teams.thread || teams.message_id}</div></>`}
    ${Number(payroll.count || 0) > 0 && html`<${ContextRow}><div><strong>Payroll:</strong> ${payroll.count} entries \u00b7 ${formatAmount(payroll.total_amount, item.currency || 'USD')}</div></>`}
    ${freshness && html`<${ContextRow}>${freshness}</>`}
    ${threadPreview.slice(0, 3).map((entry, i) => html`<${ContextRow} key="tp${i}"><div>${trimText(entry.text || '', 120)}</div></>`)}
  `;
}

function ErpTab({ item, ctx, freshness }) {
  const erp = ctx.erp || {};
  const po = ctx.po_match || {};
  const budget = ctx.budget || {};
  return html`
    <div class="cl-context-meta">Connector: ${erp.connector_available ? 'Connected' : 'Not connected'}</div>
    <${ContextRow}>
      <div><strong>Status:</strong> ${erp.state || item.state || 'unknown'}</div>
      <div><strong>Reference:</strong> ${erp.erp_reference || 'N/A'}</div>
    <//>
    ${po.status && html`<${ContextRow}><div><strong>PO check:</strong> ${String(po.status).replace(/_/g, ' ')}</div></>`}
    ${budget.status && html`<${ContextRow}><div><strong>Budget check:</strong> ${String(budget.status).replace(/_/g, ' ')}</div></>`}
    ${freshness && html`<${ContextRow}>${freshness}</>`}
    ${erp.erp_posted_at && html`<${ContextRow}><div>Posted: ${formatDateTime(erp.erp_posted_at)}</div></>`}
  `;
}

const TAB_MAP = { email: EmailTab, web: WebTab, approvals: ApprovalsTab, erp: ErpTab };

export default function ThreadContext({ item, queueManager }) {
  const s = store;
  const [activeTab, setActiveTab] = useState('email');
  const ctx = item?.id ? s.contextState.get(item.id) || null : null;
  const loading = s.contextUiState.loading && s.contextUiState.itemId === item?.id;
  const error = s.contextUiState.error;
  const agentInsight = item?.id ? s.agentInsightsState.get(item.id) || null : null;

  const refreshContext = useCallback(async () => {
    if (!item?.id || !queueManager?.fetchItemContext) return;
    store.update({ contextUiState: { itemId: item.id, loading: true, error: '' } });
    try {
      await queueManager.fetchItemContext(item.id, { refresh: true });
      store.update({ contextUiState: { itemId: item.id, loading: false, error: '' } });
    } catch (err) {
      store.update({ contextUiState: { itemId: item.id, loading: false, error: err?.message || 'Failed to load context' } });
    }
  }, [item?.id, queueManager]);

  if (loading) return html`<div class="cl-empty">Loading invoice context\u2026</div>`;
  if (error) return html`<div class="cl-empty">${error}</div>`;
  if (!ctx) return html`<div class="cl-empty">Context will load automatically.</div>`;

  const freshness = ctx.freshness || {};
  const ageText = formatAgeSeconds(freshness.age_seconds);
  const freshnessEl = ageText ? `Updated ${ageText} ago` : null;

  const TabComponent = TAB_MAP[activeTab] || EmailTab;

  return html`
    <div class="cl-context-tabs" role="tablist">
      ${TABS.map(tab => html`
        <button key=${tab.id} class="cl-context-tab ${activeTab === tab.id ? 'active' : ''}"
          role="tab" aria-selected=${activeTab === tab.id}
          onClick=${() => setActiveTab(tab.id)}>${tab.label}</button>
      `)}
      <button class="cl-btn cl-btn-secondary cl-context-refresh" onClick=${refreshContext} aria-label="Refresh context">\u21BB</button>
    </div>
    <div class="cl-context-body" role="tabpanel">
      <${TabComponent} item=${item} ctx=${ctx} freshness=${freshnessEl} agentInsight=${agentInsight} />
    </div>
  `;
}
