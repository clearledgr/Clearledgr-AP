/**
 * Vendor Detail Page — organization-style record view for AP vendor context.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDate, fmtDateTime, fmtDollar, useAction } from '../route-helpers.js';
import {
  clearPipelineNavigation,
  focusPipelineItem,
  readPipelinePreferences,
  writePipelinePreferences,
} from '../pipeline-views.js';

const html = htm.bind(h);

const STATE_STYLES = {
  needs_approval: { bg: '#FEFCE8', text: '#A16207', label: 'Needs approval' },
  needs_info: { bg: '#FEFCE8', text: '#A16207', label: 'Needs info' },
  validated: { bg: '#EFF6FF', text: '#1D4ED8', label: 'Validated' },
  received: { bg: '#F1F5F9', text: '#64748B', label: 'Received' },
  approved: { bg: '#ECFDF5', text: '#059669', label: 'Approved' },
  ready_to_post: { bg: '#DCFCE7', text: '#166534', label: 'Ready to post' },
  posted_to_erp: { bg: '#ECFDF5', text: '#10B981', label: 'Posted' },
  closed: { bg: '#F1F5F9', text: '#64748B', label: 'Closed' },
  rejected: { bg: '#FEF2F2', text: '#DC2626', label: 'Rejected' },
  failed_post: { bg: '#FEF2F2', text: '#DC2626', label: 'Failed post' },
};

function StatePill({ state }) {
  const tone = STATE_STYLES[String(state || '').trim().toLowerCase()] || {
    bg: '#F8FAFC',
    text: '#475569',
    label: String(state || 'Unknown').replace(/_/g, ' '),
  };
  return html`<span style="
    display:inline-flex;align-items:center;padding:4px 10px;border-radius:999px;
    background:${tone.bg};color:${tone.text};font-size:11px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;
  ">${tone.label}</span>`;
}

function MetricCard({ label, value, detail }) {
  return html`<div style="padding:18px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
    <div style="font-size:26px;font-weight:700;letter-spacing:-0.02em">${value}</div>
    <div style="font-size:13px;font-weight:600;margin-top:2px">${label}</div>
    ${detail ? html`<div class="muted" style="margin-top:6px;font-size:12px">${detail}</div>` : null}
  </div>`;
}

function formatMoney(amount, currency = 'USD') {
  const value = Number(amount);
  if (!Number.isFinite(value)) return '—';
  return `${currency} ${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function VendorDetailPage({ api, orgId, userEmail, navigate, routeParams, toast }) {
  const vendorName = String(routeParams?.name || '').trim();
  const pipelineScope = useMemo(() => ({ orgId, userEmail }), [orgId, userEmail]);
  const [payload, setPayload] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadVendor = async ({ silent = false } = {}) => {
    if (!vendorName) {
      setPayload(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const data = await api(`/api/ap/items/vendors/${encodeURIComponent(vendorName)}?organization_id=${encodeURIComponent(orgId)}`, { silent });
      setPayload(data || null);
    } catch {
      setPayload(null);
      if (!silent) toast?.('Could not load the vendor record.', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadVendor({ silent: true });
  }, [api, orgId, vendorName]);

  const [refresh, refreshing] = useAction(async () => {
    await loadVendor();
    toast?.('Vendor record refreshed.', 'success');
  });

  const summary = payload?.summary || {};
  const profile = payload?.profile || {};
  const recentItems = Array.isArray(payload?.recent_items) ? payload.recent_items : [];
  const history = Array.isArray(payload?.history) ? payload.history : [];
  const topExceptionCodes = Array.isArray(payload?.top_exception_codes) ? payload.top_exception_codes : [];

  const openVendorInPipeline = () => {
    const current = readPipelinePreferences(pipelineScope);
    clearPipelineNavigation(pipelineScope);
    writePipelinePreferences(pipelineScope, {
      ...current,
      activeSliceId: 'all_open',
      sortCol: 'updated_at',
      sortDir: 'desc',
      filters: {
        ...current.filters,
        vendor: vendorName,
      },
    });
    navigate('clearledgr/pipeline');
  };

  const openItemDetail = (item) => {
    if (!item?.id) return;
    focusPipelineItem(pipelineScope, item, 'vendor_record');
    navigate(`clearledgr/invoice/${encodeURIComponent(item.id)}`);
  };

  if (loading) {
    return html`<div class="panel" style="text-align:center;padding:48px"><p class="muted">Loading vendor record…</p></div>`;
  }

  if (!payload) {
    return html`
      <div class="panel">
        <h3 style="margin-top:0">Vendor not found</h3>
        <p class="muted" style="margin:0 0 12px">This vendor does not have a shared AP record yet.</p>
        <button class="alt" onClick=${() => navigate('clearledgr/vendors')}>Back to vendors</button>
      </div>
    `;
  }

  return html`
    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap">
        <div>
          <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;margin-bottom:6px">Vendor record</div>
          <h3 style="margin:0 0 6px">${payload.vendor_name || vendorName}</h3>
          <p class="muted" style="margin:0;max-width:620px">
            Shared AP memory for this supplier: open invoices, recent outcomes, anomaly flags, and posting context.
          </p>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="alt" onClick=${() => navigate('clearledgr/vendors')}>Back to vendors</button>
          <button class="alt" onClick=${refresh} disabled=${refreshing}>${refreshing ? 'Refreshing…' : 'Refresh'}</button>
          <button onClick=${openVendorInPipeline}>Open vendor in pipeline</button>
        </div>
      </div>
    </div>

    <div class="kpi-row" style="grid-template-columns:repeat(4,1fr)">
      <${MetricCard} label="Tracked invoices" value=${Number(summary.invoice_count || 0).toLocaleString()} />
      <${MetricCard} label="Open now" value=${Number(summary.open_count || 0).toLocaleString()} detail=${`${Number(summary.approval_count || 0)} waiting approval`} />
      <${MetricCard} label="Posted" value=${Number(summary.posted_count || 0).toLocaleString()} detail=${`${Number(summary.failed_count || 0)} failed post`} />
      <${MetricCard} label="Tracked spend" value=${fmtDollar(summary.total_amount || 0)} detail=${summary.last_activity_at ? `Last activity ${fmtDateTime(summary.last_activity_at)}` : 'No recent activity'} />
    </div>

    <div style="display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,0.8fr);gap:20px">
      <div style="display:flex;flex-direction:column;gap:20px">
        <div class="panel">
          <h3 style="margin-top:0">Open and recent invoices</h3>
          ${recentItems.length === 0
            ? html`<p class="muted" style="margin:0">No recent invoices for this vendor yet.</p>`
            : html`<div style="display:flex;flex-direction:column;gap:10px">
                ${recentItems.map((item) => html`
                  <div key=${item.id} style="padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
                    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
                      <div>
                        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                          <strong style="font-size:14px">${item.invoice_number || 'No invoice #'}</strong>
                          <${StatePill} state=${item.state} />
                        </div>
                        <div class="muted" style="font-size:12px;margin-top:4px">
                          ${formatMoney(item.amount, item.currency || 'USD')} · Due ${item.due_date ? fmtDate(item.due_date) : '—'} · Updated ${fmtDateTime(item.updated_at)}
                        </div>
                        ${item.erp_reference
                          ? html`<div class="muted" style="font-size:12px;margin-top:4px">ERP ${item.erp_reference}</div>`
                          : null}
                        ${item.exception_code
                          ? html`<div class="muted" style="font-size:12px;margin-top:4px">Exception ${String(item.exception_code).replace(/_/g, ' ')}</div>`
                          : null}
                      </div>
                      <button class="alt" onClick=${() => openItemDetail(item)} style="padding:8px 12px;font-size:12px">Open record</button>
                    </div>
                  </div>
                `)}
              </div>`}
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Outcome history</h3>
          ${history.length === 0
            ? html`<p class="muted" style="margin:0">No vendor outcome history yet.</p>`
            : html`<div style="display:flex;flex-direction:column;gap:10px">
                ${history.map((entry) => html`
                  <div key=${entry.id || `${entry.ap_item_id}-${entry.created_at}`} style="padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
                    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
                      <div>
                        <strong style="font-size:13px">${entry.invoice_number || entry.ap_item_id || 'Invoice outcome'}</strong>
                        <div class="muted" style="font-size:12px;margin-top:4px">
                          ${formatMoney(entry.amount, entry.currency || 'USD')} · ${String(entry.final_state || 'unknown').replace(/_/g, ' ')}
                        </div>
                      </div>
                      <span class="muted" style="font-size:12px">${fmtDateTime(entry.created_at)}</span>
                    </div>
                  </div>
                `)}
              </div>`}
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:20px">
        <div class="panel">
          <h3 style="margin-top:0">Vendor profile</h3>
          <div style="display:flex;flex-direction:column;gap:10px">
            <div style="display:flex;justify-content:space-between;gap:16px;padding-bottom:8px;border-bottom:1px solid var(--border)">
              <span class="muted">Primary email</span>
              <span style="font-weight:600;text-align:right">${summary.primary_email || '—'}</span>
            </div>
            <div style="display:flex;justify-content:space-between;gap:16px;padding-bottom:8px;border-bottom:1px solid var(--border)">
              <span class="muted">Payment terms</span>
              <span style="font-weight:600;text-align:right">${profile.payment_terms || '—'}</span>
            </div>
            <div style="display:flex;justify-content:space-between;gap:16px;padding-bottom:8px;border-bottom:1px solid var(--border)">
              <span class="muted">Requires PO</span>
              <span style="font-weight:600">${profile.requires_po ? 'Yes' : 'No'}</span>
            </div>
            <div style="display:flex;justify-content:space-between;gap:16px;padding-bottom:8px;border-bottom:1px solid var(--border)">
              <span class="muted">Always approved</span>
              <span style="font-weight:600">${profile.always_approved ? 'Yes' : 'No'}</span>
            </div>
            <div style="display:flex;justify-content:space-between;gap:16px">
              <span class="muted">Approval override rate</span>
              <span style="font-weight:600">${Number(profile.approval_override_rate || 0).toFixed(2)}</span>
            </div>
          </div>

          ${(summary.sender_emails || []).length > 0 && html`
            <div style="margin-top:14px">
              <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;margin-bottom:8px">Known sender emails</div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                ${(summary.sender_emails || []).map((email) => html`<span key=${email} style="padding:5px 10px;border-radius:999px;border:1px solid var(--border);background:var(--bg);font-size:12px">${email}</span>`)}
              </div>
            </div>
          `}

          ${(profile.anomaly_flags || []).length > 0 && html`
            <div style="margin-top:14px">
              <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;margin-bottom:8px">Anomaly flags</div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                ${(profile.anomaly_flags || []).map((flag) => html`<span key=${flag} style="padding:5px 10px;border-radius:999px;background:#FEF2F2;color:#B91C1C;font-size:12px;font-weight:600">${String(flag).replace(/_/g, ' ')}</span>`)}
              </div>
            </div>
          `}
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Common states</h3>
          ${(summary.top_states || []).length === 0
            ? html`<p class="muted" style="margin:0">No state history yet.</p>`
            : html`<div style="display:flex;flex-direction:column;gap:8px">
                ${(summary.top_states || []).map((row) => html`
                  <div key=${row.state} style="display:flex;justify-content:space-between;gap:16px;padding-bottom:8px;border-bottom:1px solid var(--border)">
                    <span>${String(row.state || '').replace(/_/g, ' ')}</span>
                    <strong>${Number(row.count || 0).toLocaleString()}</strong>
                  </div>
                `)}
              </div>`}
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Recurring exception codes</h3>
          ${topExceptionCodes.length === 0
            ? html`<p class="muted" style="margin:0">No recurring exception patterns yet.</p>`
            : html`<div style="display:flex;flex-direction:column;gap:8px">
                ${topExceptionCodes.map((row) => html`
                  <div key=${row.exception_code} style="display:flex;justify-content:space-between;gap:16px;padding-bottom:8px;border-bottom:1px solid var(--border)">
                    <span>${String(row.exception_code || '').replace(/_/g, ' ')}</span>
                    <strong>${Number(row.count || 0).toLocaleString()}</strong>
                  </div>
                `)}
              </div>`}
        </div>
      </div>
    </div>
  `;
}
