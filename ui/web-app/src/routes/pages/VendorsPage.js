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
  const [bulkImportOpen, setBulkImportOpen] = useState(false);

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
        <button class="btn-secondary btn-sm" onClick=${() => setBulkImportOpen(true)}>Bulk import</button>
        <button class="btn-primary btn-sm" onClick=${() => navigate('clearledgr/invoices')}>Open invoices</button>
      </div>
    </div>

    ${bulkImportOpen ? html`<${VendorBulkImportModal}
      api=${api}
      orgId=${orgId}
      toast=${toast}
      onClose=${() => setBulkImportOpen(false)}
      onCommitted=${() => { setBulkImportOpen(false); loadVendors({ silent: true }); }} />` : null}

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
                      ${typeof vendor.risk_score === 'number' && vendor.risk_score > 0
                        ? html`<${RiskScoreChip} score=${vendor.risk_score} />`
                        : null}
                      ${vendor.profile?.status && vendor.profile.status !== 'active'
                        ? html`<span class="secondary-chip" style=${vendor.profile.status === 'blocked'
                            ? 'background:#FEE2E2;color:#991B1B;border-color:#FCA5A5;font-weight:600'
                            : 'background:#F4F4F5;color:#52525B;border-color:#D4D4D8'}>
                            ${vendor.profile.status === 'blocked' ? 'Blocked' : (vendor.profile.status === 'archived' ? 'Archived' : vendor.profile.status)}
                          </span>`
                        : null}
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
                  <${VendorStatusButton}
                    api=${api}
                    orgId=${orgId}
                    vendor=${vendor}
                    toast=${toast}
                    onChanged=${() => loadVendors({ silent: true })} />
                  <${VendorPushButton}
                    api=${api}
                    orgId=${orgId}
                    vendor=${vendor}
                    toast=${toast} />
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


// ─── Module 4 Pass A — Vendor risk score chip ─────────────────────────
// Surfaces VendorRiskScoreService output on the vendor list row. The
// score is computed server-side from the already-loaded profile, so
// rendering here costs nothing extra. Color thresholds match the
// service's component weights:
//   * 0      → no chip (handled by caller; we never render a "0" chip)
//   * 1-29   → low (green) — usually one missing-field component
//   * 30-49  → medium (amber) — new vendor, or two missing components
//   * 50+    → high (red) — IBAN freeze + new vendor or worse
function RiskScoreChip({ score }) {
  const tone =
    score >= 50 ? { bg: '#FEE2E2', fg: '#991B1B', bd: '#FCA5A5' }
    : score >= 30 ? { bg: '#FEF3C7', fg: '#92400E', bd: '#FCD34D' }
    : { bg: '#ECFDF5', fg: '#0A663E', bd: '#86EFAC' };
  return html`<span
    class="secondary-chip"
    title="Vendor risk score (0-100). Higher = more risk."
    style=${`background:${tone.bg};color:${tone.fg};border-color:${tone.bd};font-variant-numeric:tabular-nums`}>
    Risk ${score}
  </span>`;
}


// ─── Module 4 Pass B — Vendor allowlist/blocklist action ──────────────
// Block / Unblock toggle wired to PATCH /api/vendors/{name}/status.
// Admin-gated server-side (403 if non-admin); the button is always
// visible so the role gate's the source of truth.
function VendorStatusButton({ api, orgId, vendor, toast, onChanged }) {
  const status = vendor?.profile?.status || 'active';
  const isBlocked = status === 'blocked';
  const verb = isBlocked ? 'Unblock' : 'Block';
  const [pending, run] = useAction(async () => {
    let reason = null;
    if (!isBlocked) {
      // window.prompt is intentionally synchronous here so the
      // operator stops and writes a real reason — the audit row
      // carries this verbatim.
      const input = window.prompt(
        `Block invoices from "${vendor.vendor_name}"? Add a reason for the audit log:`,
        '',
      );
      if (input === null) return; // user cancelled
      reason = String(input || '').trim() || null;
    } else {
      const ok = window.confirm(
        `Unblock "${vendor.vendor_name}"? New invoices will be accepted again.`,
      );
      if (!ok) return;
    }
    try {
      await api(
        `/api/vendors/${encodeURIComponent(vendor.vendor_name)}/status?organization_id=${encodeURIComponent(orgId)}`,
        {
          method: 'PATCH',
          body: JSON.stringify({
            status: isBlocked ? 'active' : 'blocked',
            reason,
          }),
        },
      );
      toast?.(
        isBlocked
          ? `${vendor.vendor_name} unblocked — invoices will post again.`
          : `${vendor.vendor_name} blocked — new invoices will be rejected.`,
        'success',
      );
      onChanged?.();
    } catch (exc) {
      const detail = exc?.detail || exc?.body?.detail;
      const reasonStr = typeof detail === 'object' ? detail.reason : null;
      if (reasonStr === 'admin_role_required') {
        toast?.('Only admins can change vendor status.', 'error');
      } else {
        toast?.(exc?.message || 'Could not change vendor status.', 'error');
      }
    }
  });
  return html`
    <button
      class=${isBlocked ? 'btn-secondary btn-sm' : 'btn-ghost btn-sm'}
      onClick=${run}
      disabled=${pending}>
      ${pending ? '…' : verb}
    </button>
  `;
}


// ─── Module 4 Pass D — Reverse vendor sync (Clearledgr → ERP) ─────────
// "Push to ERP" button: calls POST /api/vendors/{name}/sync-erp,
// surfaces the structured PushResult as a toast. Admin-gated server-
// side; the button is always visible so the role gate's the source
// of truth.
function VendorPushButton({ api, orgId, vendor, toast }) {
  const [pending, run] = useAction(async () => {
    try {
      const res = await api(
        `/api/vendors/${encodeURIComponent(vendor.vendor_name)}/sync-erp?organization_id=${encodeURIComponent(orgId)}`,
        { method: 'POST' },
      );
      // 200-shaped responses come through here as the parsed body.
      if (res?.status === 'ok') {
        const fields = (res.fields_pushed || []).join(', ');
        toast?.(`${vendor.vendor_name} pushed to ${res.erp_type} (${fields}).`, 'success');
      } else if (res?.status === 'no_change') {
        toast?.(`${vendor.vendor_name}: no fields changed since last push.`, 'info');
      } else if (res?.status === 'not_supported') {
        toast?.(`Reverse sync not supported for ${res.erp_type} yet.`, 'error');
      } else {
        toast?.(`Push returned ${res?.status || 'unknown'}.`, 'error');
      }
    } catch (exc) {
      const detail = exc?.detail || exc?.body?.detail || {};
      const status = typeof detail === 'object' ? detail.status : null;
      const reason = typeof detail === 'object' ? detail.error : null;
      if (status === 'no_erp_id') {
        toast?.(
          `${vendor.vendor_name} has no recorded ERP id. Run a vendor sync first.`,
          'error',
        );
      } else if (reason === 'admin_role_required') {
        toast?.('Only admins can push vendors to the ERP.', 'error');
      } else {
        toast?.(reason || exc?.message || 'Push failed.', 'error');
      }
    }
  });
  return html`
    <button
      class="btn-ghost btn-sm"
      onClick=${run}
      disabled=${pending}
      title="Push the in-Clearledgr vendor profile to the connected ERP.">
      ${pending ? 'Pushing…' : 'Push to ERP'}
    </button>
  `;
}


// ─── Module 4 Pass E — Bulk vendor import via CSV ─────────────────────
// Two-step modal: paste CSV → preview (server validates per-row) →
// commit. Operators iterate on the source sheet until everything's
// green, then commit. Backed by:
//   POST /api/vendors/import/preview
//   POST /api/vendors/import/commit
function VendorBulkImportModal({ api, orgId, toast, onClose, onCommitted }) {
  const [csvText, setCsvText] = useState('');
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [committing, setCommitting] = useState(false);

  const onPreview = async () => {
    if (!csvText.trim()) {
      toast?.('Paste CSV content first.', 'error');
      return;
    }
    setPreviewing(true);
    try {
      const res = await api(
        `/api/vendors/import/preview?organization_id=${encodeURIComponent(orgId)}`,
        { method: 'POST', body: JSON.stringify({ csv_text: csvText }) },
      );
      setPreview(res);
      if (res.fatal_error) {
        toast?.(`CSV invalid: ${res.fatal_error}`, 'error');
      }
    } catch (exc) {
      const detail = exc?.detail || exc?.body?.detail;
      toast?.(detail?.message || exc?.message || 'Preview failed.', 'error');
    } finally {
      setPreviewing(false);
    }
  };

  const onCommit = async () => {
    if (!preview || preview.valid_rows === 0) return;
    if (!window.confirm(
      `Apply ${preview.valid_rows} valid row${preview.valid_rows === 1 ? '' : 's'} to the workspace? ` +
      `${preview.error_rows ? preview.error_rows + ' invalid row(s) will be skipped.' : ''}`
    )) return;
    setCommitting(true);
    try {
      const res = await api(
        `/api/vendors/import/commit?organization_id=${encodeURIComponent(orgId)}`,
        { method: 'POST', body: JSON.stringify({ csv_text: csvText }) },
      );
      toast?.(
        `Imported ${res.applied_count} vendor${res.applied_count === 1 ? '' : 's'}.` +
        (res.skipped_count ? ` Skipped ${res.skipped_count}.` : ''),
        'success',
      );
      onCommitted?.();
    } catch (exc) {
      const detail = exc?.detail || exc?.body?.detail;
      toast?.(detail?.fatal_error || detail?.message || exc?.message || 'Commit failed.', 'error');
    } finally {
      setCommitting(false);
    }
  };

  return html`
    <div class="cl-modal-overlay" onClick=${onClose}>
      <div
        class="cl-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cl-bulk-import-title"
        style="max-width:880px;width:90vw"
        onClick=${(e) => e.stopPropagation()}>
        <h3 id="cl-bulk-import-title" class="cl-modal-title">Bulk vendor import</h3>
        <div class="cl-modal-body">
          <p class="muted" style="font-size:12px;margin:0 0 8px">
            Paste CSV content. The first row must be a header. Required column:
            <code>vendor_name</code>. Optional columns:
            <code>email</code>, <code>address</code>, <code>terms</code>,
            <code>vat_number</code>, <code>registration_number</code>,
            <code>status</code> (active|blocked|archived). 5 000 rows max.
          </p>
          <textarea
            value=${csvText}
            onInput=${(e) => setCsvText(e.target.value)}
            placeholder="vendor_name,email,terms\nAcme Inc,ap@acme.test,Net 30"
            style="width:100%;min-height:160px;font-family:var(--font-mono,monospace);font-size:12px"
            disabled=${previewing || committing}></textarea>
          <div class="row-actions" style="justify-content:flex-start;margin-top:8px">
            <button class="btn-secondary btn-sm" onClick=${onPreview} disabled=${previewing || !csvText.trim()}>
              ${previewing ? 'Validating…' : 'Validate preview'}
            </button>
          </div>

          ${preview ? html`
            <div style="margin-top:14px">
              ${preview.fatal_error
                ? html`<div class="form-error">CSV invalid: <code>${preview.fatal_error}</code></div>`
                : html`
                  <div class="muted" style="font-size:12px;margin-bottom:6px">
                    ${preview.total_rows} row${preview.total_rows === 1 ? '' : 's'} parsed:
                    <strong style="color:var(--cl-mint,#0a663e)">${preview.valid_rows} valid</strong>
                    · <strong style="color:#991b1b">${preview.error_rows} with errors</strong>
                  </div>
                  ${preview.rows && preview.rows.length > 0 ? html`
                    <div style="max-height:240px;overflow:auto;border:1px solid var(--cl-border,#e5e7eb);border-radius:6px">
                      <table style="width:100%;border-collapse:collapse;font-size:11px">
                        <thead style="background:var(--cl-bg-subtle,#fafafa)">
                          <tr>
                            <th style="padding:4px 8px;text-align:left">Row</th>
                            <th style="padding:4px 8px;text-align:left">Vendor</th>
                            <th style="padding:4px 8px;text-align:left">Status</th>
                            <th style="padding:4px 8px;text-align:left">Issue</th>
                          </tr>
                        </thead>
                        <tbody>
                          ${preview.rows.slice(0, 100).map((r) => html`
                            <tr key=${r.row_number} style="border-top:1px solid var(--cl-border-subtle,#efefef)">
                              <td style="padding:4px 8px">${r.row_number}</td>
                              <td style="padding:4px 8px;font-family:var(--font-mono,monospace)">
                                ${r.parsed?.vendor_name || r.raw?.vendor_name || '—'}
                              </td>
                              <td style="padding:4px 8px">
                                ${r.valid
                                  ? html`<span style="color:#0a663e">OK</span>`
                                  : html`<span style="color:#991b1b">Error</span>`}
                              </td>
                              <td style="padding:4px 8px;color:#991b1b">
                                ${(r.errors || []).join(', ')}
                              </td>
                            </tr>
                          `)}
                        </tbody>
                      </table>
                    </div>
                  ` : null}
                `}
            </div>
          ` : null}
        </div>
        <div class="cl-modal-actions">
          <button class="btn-secondary btn-sm" onClick=${onClose} disabled=${committing}>Cancel</button>
          <button
            class="btn-primary btn-sm"
            disabled=${committing || !preview || preview.fatal_error || preview.valid_rows === 0}
            onClick=${onCommit}>
            ${committing ? 'Importing…' : `Import ${preview?.valid_rows || 0} vendor${(preview?.valid_rows ?? 0) === 1 ? '' : 's'}`}
          </button>
        </div>
      </div>
    </div>
  `;
}
