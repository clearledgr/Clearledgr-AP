/**
 * Vendors Page — shared vendor directory for AP follow-up.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDateTime, fmtDollar, useAction } from '../route-helpers.js';
import { clearPipelineNavigation, readPipelinePreferences, writePipelinePreferences } from '../pipeline-views.js';
import { navigateToVendorRecord } from '../../utils/vendor-route.js';

const html = htm.bind(h);

export default function VendorsPage({ api, orgId, userEmail, navigate, toast }) {
  const pipelineScope = useMemo(() => ({ orgId, userEmail }), [orgId, userEmail]);
  const [vendors, setVendors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  const loadVendors = async ({ silent = false } = {}) => {
    setLoading(true);
    try {
      const data = await api(`/api/ap/items/vendors?organization_id=${encodeURIComponent(orgId)}&limit=200`, { silent });
      setVendors(Array.isArray(data?.vendors) ? data.vendors : []);
    } catch {
      setVendors([]);
      if (!silent) toast?.('Could not load vendors.', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadVendors({ silent: true });
  }, [api, orgId]);

  const [refresh, refreshing] = useAction(async () => {
    await loadVendors();
    toast?.('Vendor directory refreshed.', 'success');
  });

  const filtered = useMemo(() => {
    if (!String(search || '').trim()) return vendors;
    const query = String(search || '').trim().toLowerCase();
    return vendors.filter((vendor) => String(vendor.vendor_name || '').toLowerCase().includes(query));
  }, [vendors, search]);

  const openVendorRecord = (vendor) => {
    const vendorName = String(vendor?.vendor_name || '').trim();
    if (!vendorName) return;
    navigateToVendorRecord(navigate, vendorName);
  };

  const openVendorPipeline = (vendor) => {
    const vendorName = String(vendor?.vendor_name || '').trim();
    if (!vendorName) return;
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

  if (loading) {
    return html`<div class="panel" style="text-align:center;padding:48px"><p class="muted">Loading vendor directory…</p></div>`;
  }

  return html`
    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap">
        <div>
          <h3 style="margin:0 0 6px">Vendor directory</h3>
          <p class="muted" style="margin:0;max-width:620px">
            Use vendor records for shared supplier memory, then jump back into Pipeline for the actual queue work.
          </p>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="alt" onClick=${refresh} disabled=${refreshing}>${refreshing ? 'Refreshing…' : 'Refresh'}</button>
          <button onClick=${() => navigate('clearledgr/pipeline')}>Open pipeline</button>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="readiness-list" style="margin:0 0 14px">
        <div class="readiness-item"><strong>Vendors tracked:</strong> ${vendors.length}</div>
        <div class="readiness-item"><strong>Open invoices:</strong> ${vendors.reduce((sum, vendor) => sum + Number(vendor.open_count || 0), 0).toLocaleString()}</div>
        <div class="readiness-item"><strong>Total spend:</strong> ${fmtDollar(vendors.reduce((sum, vendor) => sum + Number(vendor.total_amount || 0), 0))}</div>
        <div class="readiness-item"><strong>Needs info:</strong> ${vendors.reduce((sum, vendor) => sum + Number(vendor.needs_info_count || 0), 0).toLocaleString()}</div>
      </div>

      <div style="position:relative">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--ink-muted)" stroke-width="2" style="position:absolute;left:10px;top:50%;transform:translateY(-50%)"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        <input
          placeholder="Search vendors…"
          value=${search}
          onInput=${(event) => setSearch(event.target.value)}
          style="width:100%;padding:8px 8px 8px 34px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;font-family:inherit;background:var(--bg)"
        />
      </div>

      <div style="display:grid;gap:10px;margin-top:14px">
        ${filtered.length === 0
          ? html`<div class="muted">${search ? 'No vendors match your search.' : 'No vendors yet. Vendor records appear once invoices are processed.'}</div>`
          : filtered.map((vendor) => html`
              <div key=${vendor.vendor_name} style="padding:14px 16px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
                <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
                  <div style="min-width:0;flex:1">
                    <strong style="display:block;font-size:14px">${vendor.vendor_name}</strong>
                    <div class="muted" style="font-size:12px;margin-top:4px">
                      ${vendor.primary_email || 'No primary sender'} · Last activity ${vendor.last_activity_at ? fmtDateTime(vendor.last_activity_at) : '—'}
                    </div>
                    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px">
                      ${(vendor.top_states || []).map((row) => html`
                        <span key=${row.state} style="font-size:10px;font-weight:600;padding:3px 8px;border-radius:999px;background:var(--bg);border:1px solid var(--border);color:var(--ink-secondary)">
                          ${String(row.state || '').replace(/_/g, ' ')} ${row.count}
                        </span>
                      `)}
                      ${vendor.profile?.requires_po
                        ? html`<span style="font-size:10px;font-weight:700;padding:3px 8px;border-radius:999px;background:#FEF3C7;color:#92400E">Requires PO</span>`
                        : null}
                      ${(vendor.profile?.anomaly_flags || []).slice(0, 2).map((flag) => html`
                        <span key=${flag} style="font-size:10px;font-weight:700;padding:3px 8px;border-radius:999px;background:#FEF2F2;color:#B91C1C">${String(flag).replace(/_/g, ' ')}</span>
                      `)}
                    </div>
                  </div>
                  <div style="text-align:right;min-width:140px">
                    <div style="font-weight:700">${fmtDollar(vendor.total_amount || 0)}</div>
                    <div class="muted" style="font-size:12px;margin-top:2px">${Number(vendor.invoice_count || 0).toLocaleString()} invoices</div>
                    <div class="muted" style="font-size:12px;margin-top:4px">${Number(vendor.open_count || 0).toLocaleString()} open · ${Number(vendor.approval_count || 0).toLocaleString()} awaiting approval</div>
                  </div>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
                  <button class="alt" onClick=${() => openVendorRecord(vendor)} style="padding:8px 12px;font-size:12px">Open vendor record</button>
                  <button class="alt" onClick=${() => openVendorPipeline(vendor)} style="padding:8px 12px;font-size:12px">Open in pipeline</button>
                </div>
              </div>
            `)}
      </div>
    </div>
  `;
}
