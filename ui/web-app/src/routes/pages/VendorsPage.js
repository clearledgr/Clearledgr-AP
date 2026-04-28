/**
 * Vendors Page — shared vendor directory for AP follow-up.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDateTime, fmtDollar, useAction } from '../route-helpers.js';
import { clearPipelineNavigation, readPipelinePreferences, writePipelinePreferences } from '../pipeline-views.js';
import { writeReviewPreferences } from '../review-preferences.js';
import { navigateToVendorRecord } from '../../utils/vendor-route.js';
import { getExceptionLabel } from '../../utils/formatters.js';
import { EmptyState, LoadingSkeleton, ErrorRetry } from '../../components/StatePrimitives.js';

const html = htm.bind(h);

export default function VendorsPage({ api, orgId, userEmail, navigate, toast }) {
  const pipelineScope = useMemo(() => ({ orgId, userEmail }), [orgId, userEmail]);
  const [vendors, setVendors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [search, setSearch] = useState('');

  const loadVendors = async ({ silent = false } = {}) => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api(`/api/ap/items/vendors?organization_id=${encodeURIComponent(orgId)}&limit=200`, { silent });
      setVendors(Array.isArray(data?.vendors) ? data.vendors : []);
    } catch (exc) {
      setVendors([]);
      setLoadError(exc?.message || 'Could not load vendors.');
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
    navigate('clearledgr/invoices');
  };

  const openVendorIssues = (vendor) => {
    const vendorName = String(vendor?.vendor_name || '').trim();
    if (!vendorName) return;
    writeReviewPreferences(pipelineScope, { searchQuery: vendorName });
    navigate('clearledgr/review');
  };

  if (loading) {
    return html`<div class="panel"><${LoadingSkeleton} rows=${5} label="Loading vendor directory" /></div>`;
  }

  if (loadError) {
    return html`<div class="panel"><${ErrorRetry}
      message="Couldn't load the vendor directory."
      detail=${loadError}
      onRetry=${() => loadVendors()}
    /></div>`;
  }

  return html`
    <div class="secondary-banner">
      <div class="secondary-banner-copy">
        <h3>Vendor directory</h3>
        <p class="muted">See past invoices, open issues, and recent activity for each vendor, then jump back into the queue when you need to act.</p>
      </div>
      <div class="secondary-banner-actions">
        <button class="btn-secondary btn-sm" onClick=${refresh} disabled=${refreshing}>${refreshing ? 'Refreshing…' : 'Refresh'}</button>
        <button class="btn-primary btn-sm" onClick=${() => navigate('clearledgr/invoices')}>Open invoices</button>
      </div>
    </div>

    <div class="secondary-chip-row" style="margin:0 0 18px">
      <span class="secondary-chip">Vendors tracked ${vendors.length}</span>
      <span class="secondary-chip">Open invoices ${vendors.reduce((sum, vendor) => sum + Number(vendor.open_count || 0), 0).toLocaleString()}</span>
      <span class="secondary-chip">Open issues ${vendors.reduce((sum, vendor) => sum + Number(vendor.issue_count || 0), 0).toLocaleString()}</span>
      <span class="secondary-chip">Total spend ${fmtDollar(vendors.reduce((sum, vendor) => sum + Number(vendor.total_amount || 0), 0))}</span>
    </div>

    <${DedupBanner} api=${api} orgId=${orgId} toast=${toast} />

    <div class="panel">
      <div class="secondary-search-row">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--ink-muted)" stroke-width="2" style="position:absolute;left:10px;top:50%;transform:translateY(-50%)"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        <input
          placeholder="Search vendors…"
          value=${search}
          onInput=${(event) => setSearch(event.target.value)}
        />
      </div>

      <div class="secondary-card-list" style="margin-top:14px">
        ${filtered.length === 0
          ? html`<div class="muted">${search ? 'No vendors match your search.' : 'No vendors yet. Vendor records appear once invoices are processed.'}</div>`
          : filtered.map((vendor) => html`
              <div key=${vendor.vendor_name} class="secondary-card">
                <div class="secondary-card-head">
                  <div class="secondary-card-copy">
                    <strong class="secondary-card-title">${vendor.vendor_name}</strong>
                    <div class="secondary-card-meta">
                      ${vendor.primary_email || 'No primary sender'} · Last activity ${vendor.last_activity_at ? fmtDateTime(vendor.last_activity_at) : '—'}
                    </div>
                    <div class="secondary-card-tags">
                      ${(vendor.top_states || []).map((row) => html`
                        <span key=${row.state} class="secondary-chip">
                          ${String(row.state || '').replace(/_/g, ' ')} ${row.count}
                        </span>
                      `)}
                      ${(vendor.top_exception_codes || []).slice(0, 2).map((row) => html`
                        <span key=${row.exception_code} class="secondary-chip" style="background:#FFF7ED;color:#9A3412;border-color:#FED7AA">
                          ${getExceptionLabel(row.exception_code)} ${row.count}
                        </span>
                      `)}
                      ${vendor.profile?.requires_po
                        ? html`<span class="secondary-chip" style="background:#FEF3C7;color:#92400E;border-color:#FDE68A">Requires PO</span>`
                        : null}
                      ${(vendor.profile?.anomaly_flags || []).slice(0, 2).map((flag) => html`
                        <span key=${flag} class="secondary-chip" style="background:#FEF2F2;color:#B91C1C;border-color:#FECACA">${String(flag).replace(/_/g, ' ')}</span>
                      `)}
                    </div>
                  </div>
                  <div class="secondary-card-stat">
                    <strong>${fmtDollar(vendor.total_amount || 0)}</strong>
                    <span>${Number(vendor.invoice_count || 0).toLocaleString()} invoices</span>
                    <span>${Number(vendor.open_count || 0).toLocaleString()} open · ${Number(vendor.issue_count || 0).toLocaleString()} issues · ${Number(vendor.approval_count || 0).toLocaleString()} awaiting approval</span>
                  </div>
                </div>
                <div class="secondary-card-actions">
                  <button class="btn-secondary btn-sm" onClick=${() => openVendorRecord(vendor)}>Open vendor record</button>
                  <button class="btn-secondary btn-sm" onClick=${() => openVendorIssues(vendor)}>Review issues</button>
                  <button class="btn-ghost btn-sm" onClick=${() => openVendorPipeline(vendor)}>Open in invoices</button>
                </div>
              </div>
            `)}
      </div>
    </div>
  `;
}

function DedupBanner({ api, orgId, toast }) {
  const [clusters, setClusters] = useState([]);
  const [merging, setMerging] = useState('');
  useEffect(() => {
    api(`/api/workspace/vendor-intelligence/duplicates?organization_id=${encodeURIComponent(orgId)}`)
      .then((d) => setClusters(d?.clusters || []))
      .catch(() => {});
  }, [api, orgId]);
  if (!clusters.length) return null;
  const doMerge = async (cluster) => {
    const canonical = cluster.canonical.vendor_name;
    const dupes = cluster.duplicates.map((d) => d.vendor_name);
    setMerging(canonical);
    try {
      await api(`/api/workspace/vendor-intelligence/merge`, {
        method: 'POST',
        body: JSON.stringify({ canonical, duplicates: dupes }),
      });
      setClusters((prev) => prev.filter((c) => c.canonical.vendor_name !== canonical));
      toast?.(`Merged ${dupes.join(', ')} into ${canonical}`, 'success');
    } catch (e) {
      toast?.('Merge failed', 'error');
    }
    setMerging('');
  };
  return html`
    <div class="panel" style="margin-bottom:14px">
      <h3 style="margin-top:0">Possible duplicate vendors (${clusters.length})</h3>
      <p class="muted" style="margin:0 0 8px;font-size:12px">These vendors have similar names and may be the same entity.</p>
      ${clusters.slice(0, 5).map((c) => html`
        <div key=${c.canonical.vendor_name} class="secondary-row">
          <div class="secondary-row-copy">
            <strong>${c.canonical.vendor_name}</strong> (${c.canonical.invoice_count} invoices)
            <div class="muted">${c.duplicates.map((d) => `${d.vendor_name} (${d.similarity * 100 | 0}%)`).join(', ')}</div>
          </div>
          <button class="btn-secondary btn-sm" onClick=${() => doMerge(c)} disabled=${merging === c.canonical.vendor_name}>
            ${merging === c.canonical.vendor_name ? 'Merging...' : 'Merge'}
          </button>
        </div>
      `)}
    </div>
  `;
}
