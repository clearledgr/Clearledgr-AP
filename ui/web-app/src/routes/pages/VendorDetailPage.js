/**
 * Vendor detail — Module 4 §154.
 *
 * The leader's per-vendor screen: ERP data (name, address, tax ID,
 * terms) plus the Clearledgr layer (verified IBANs, fraud flags,
 * custom routing, agent confidence) plus recent invoice history and
 * exception trend.
 *
 * Backed by GET /api/ap/items/vendors/{name} which returns a
 * comprehensive payload merged from vendor_profiles + ERP-side data
 * + recent invoice rollups + exception counts. The page is a thin
 * renderer over that payload.
 */
import { h } from 'preact';
import { useCallback, useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDate, fmtDateTime } from '../route-helpers.js';
import { formatAmount } from '../../utils/formatters.js';

const html = htm.bind(h);


export default function VendorDetailPage({ api, orgId, navigate, toast, vendorName }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!vendorName) {
      setError('No vendor name supplied');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await api(
        `/api/ap/items/vendors/${encodeURIComponent(vendorName)}`
        + `?organization_id=${encodeURIComponent(orgId)}&days=180&invoice_limit=20`,
      );
      setData(resp);
    } catch (exc) {
      setError(String(exc?.message || exc || 'Failed to load vendor'));
    } finally {
      setLoading(false);
    }
  }, [api, orgId, vendorName]);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) {
    return html`
      <div class="cl-vendor-detail" role="status" aria-live="polite">
        <div class="cl-vendor-skeleton cl-vendor-skeleton-head"></div>
        <div class="cl-vendor-skeleton cl-vendor-skeleton-grid"></div>
      </div>
    `;
  }

  if (error && !data) {
    return html`
      <div class="cl-vendor-detail cl-vendor-error" role="alert">
        <h2>Couldn't load this vendor</h2>
        <p>${error}</p>
        <div class="cl-vendor-error-actions">
          <button class="btn btn-primary" onClick=${load}>Try again</button>
          <button class="btn btn-secondary" onClick=${() => navigate('/vendors')}>
            Back to vendors
          </button>
        </div>
      </div>
    `;
  }

  if (!data) {
    return html`
      <div class="cl-vendor-detail cl-vendor-error">
        <h2>Vendor not found</h2>
        <button class="btn btn-secondary" onClick=${() => navigate('/vendors')}>
          Back to vendors
        </button>
      </div>
    `;
  }

  const profile = data.profile || data.vendor_profile || {};
  const erp = data.erp || {};
  const summary = data.summary || data.metrics || {};
  const invoices = Array.isArray(data.recent_invoices)
    ? data.recent_invoices
    : (data.recent || []);
  const fraudFlags = data.fraud_flags || profile.fraud_flags || [];
  const verifiedIbans = data.verified_ibans || profile.verified_ibans || [];
  const exceptionTrend = data.exception_trend || [];

  return html`
    <div class="cl-vendor-detail">
      <header class="cl-vendor-detail-topbar">
        <button class="cl-record-back" onClick=${() => navigate('/vendors')} aria-label="Back to vendors">
          ← Vendors
        </button>
      </header>

      <${VendorHeader}
        name=${vendorName}
        profile=${profile}
        summary=${summary} />

      <${RegistryVerifyBanner}
        api=${api}
        vendorName=${vendorName}
        profile=${profile}
        toast=${toast}
        onChanged=${load} />

      <div class="cl-vendor-grid">
        <${ErpDataPanel} erp=${erp} profile=${profile} />
        <${ClearledgrLayerPanel}
          profile=${profile}
          verifiedIbans=${verifiedIbans}
          fraudFlags=${fraudFlags}
          summary=${summary} />
      </div>

      <${ExceptionTrendPanel} trend=${exceptionTrend} summary=${summary} />

      <${RecentInvoicesPanel} invoices=${invoices} navigate=${navigate} />
    </div>
  `;
}


// ─── Header ─────────────────────────────────────────────────────────

function VendorHeader({ name, profile, summary }) {
  const totalInvoices = summary.total_invoices ?? profile.invoice_count ?? 0;
  const exceptionRate = summary.exception_rate;
  const avgAmount = summary.avg_invoice_amount ?? profile.avg_invoice_amount;
  const status = profile.status || 'active';

  return html`
    <section class="cl-vendor-header">
      <div class="cl-vendor-header-primary">
        <div>
          <span class="cl-record-header-eyebrow">Vendor</span>
          <h1 class="cl-record-header-vendor-name">${name}</h1>
        </div>
        <span class=${`cl-record-chip cl-record-chip-${vendorStatusTone(status)}`}>
          ${status}
        </span>
      </div>
      <dl class="cl-record-header-meta">
        <div class="cl-record-header-meta-cell">
          <dt>Invoices (180d)</dt>
          <dd>${totalInvoices}</dd>
        </div>
        ${avgAmount != null ? html`
          <div class="cl-record-header-meta-cell">
            <dt>Avg amount</dt>
            <dd>${formatAmount(avgAmount, profile.currency || 'USD')}</dd>
          </div>` : null}
        ${exceptionRate != null ? html`
          <div class="cl-record-header-meta-cell">
            <dt>Exception rate</dt>
            <dd>${(Number(exceptionRate) * 100).toFixed(1)}%</dd>
          </div>` : null}
        ${profile.always_approved !== undefined ? html`
          <div class="cl-record-header-meta-cell">
            <dt>Always approved</dt>
            <dd>${profile.always_approved ? 'Yes' : 'No'}</dd>
          </div>` : null}
      </dl>
    </section>
  `;
}

function vendorStatusTone(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'active' || s === 'verified') return 'success';
  if (s === 'pending' || s === 'unverified') return 'warning';
  if (s === 'blocked' || s === 'frozen') return 'error';
  return 'info';
}


// ─── ERP data panel ────────────────────────────────────────────────

function ErpDataPanel({ erp, profile }) {
  const fields = [
    ['Vendor ID', erp.vendor_id || profile.vendor_id || profile.erp_vendor_id],
    ['Tax ID', erp.tax_id || profile.tax_id],
    ['Payment terms', erp.payment_terms || profile.payment_terms],
    ['Currency', erp.currency || profile.currency],
    ['Address', erp.address || profile.address],
    ['Contact', erp.primary_contact_email || profile.primary_contact_email],
  ].filter(([, v]) => v !== null && v !== undefined && v !== '');

  return html`
    <section class="cl-record-panel">
      <header class="cl-record-panel-head">
        <h2>From your ERP</h2>
      </header>
      ${fields.length === 0 ? html`
        <p class="cl-record-empty">
          No ERP-side data yet. Vendor master sync will populate this on
          the next pull from your ERP.
        </p>
      ` : html`
        <dl class="cl-record-bill-grid">
          ${fields.map(([label, value]) => html`
            <div class="cl-record-bill-cell" key=${label}>
              <dt>${label}</dt>
              <dd>${value}</dd>
            </div>`)}
        </dl>
      `}
    </section>
  `;
}


// ─── Clearledgr-layer panel ────────────────────────────────────────

function ClearledgrLayerPanel({
  profile, verifiedIbans, fraudFlags, summary,
}) {
  const agentConfidence = summary.agent_confidence ?? profile.agent_confidence;
  const requiresPo = profile.requires_po;
  const customRouting = profile.custom_routing;

  return html`
    <section class="cl-record-panel">
      <header class="cl-record-panel-head">
        <h2>Clearledgr layer</h2>
        <span class="cl-record-panel-eyebrow">What the agent knows</span>
      </header>

      <div class="cl-vendor-section">
        <h3 class="cl-vendor-subhead">Verified IBANs</h3>
        ${verifiedIbans.length === 0 ? html`
          <p class="cl-record-empty cl-vendor-section-empty">
            No verified IBANs yet. The agent runs IBAN verification on
            first payment to a new account.
          </p>
        ` : html`
          <ul class="cl-vendor-list">
            ${verifiedIbans.map((entry, idx) => html`
              <li key=${idx} class="cl-vendor-list-row">
                <code>${entry.iban_masked || entry.iban || '—'}</code>
                <span class="cl-record-muted">
                  ${entry.verified_at ? `verified ${fmtDate(entry.verified_at)}` : 'pending'}
                  ${entry.source ? ` · ${entry.source}` : ''}
                </span>
              </li>`)}
          </ul>
        `}
      </div>

      <div class="cl-vendor-section">
        <h3 class="cl-vendor-subhead">Fraud signals</h3>
        ${fraudFlags.length === 0 ? html`
          <p class="cl-record-empty cl-vendor-section-empty">
            No active fraud signals.
          </p>
        ` : html`
          <ul class="cl-vendor-list">
            ${fraudFlags.map((flag, idx) => html`
              <li key=${idx} class="cl-vendor-list-row">
                <span class=${`cl-record-chip cl-record-chip-${fraudTone(flag.severity)}`}>
                  ${flag.flag_type || flag.type || 'flag'}
                </span>
                <span>${flag.note || flag.message || ''}</span>
                ${flag.raised_at ? html`
                  <span class="cl-record-muted">${fmtDate(flag.raised_at)}</span>` : null}
              </li>`)}
          </ul>
        `}
      </div>

      <div class="cl-vendor-section">
        <h3 class="cl-vendor-subhead">Agent context</h3>
        <dl class="cl-record-bill-grid">
          ${typeof agentConfidence === 'number' ? html`
            <div class="cl-record-bill-cell">
              <dt>Agent confidence</dt>
              <dd>${(agentConfidence * 100).toFixed(0)}%</dd>
            </div>` : null}
          ${requiresPo !== undefined ? html`
            <div class="cl-record-bill-cell">
              <dt>PO required</dt>
              <dd>${requiresPo ? 'Yes' : 'No'}</dd>
            </div>` : null}
          ${customRouting ? html`
            <div class="cl-record-bill-cell">
              <dt>Custom routing</dt>
              <dd>${typeof customRouting === 'object'
                ? JSON.stringify(customRouting)
                : String(customRouting)}</dd>
            </div>` : null}
          ${profile.bank_details_changed_at ? html`
            <div class="cl-record-bill-cell">
              <dt>Bank details changed</dt>
              <dd>${fmtDate(profile.bank_details_changed_at)}</dd>
            </div>` : null}
        </dl>
      </div>
    </section>
  `;
}

function fraudTone(severity) {
  const s = String(severity || '').toLowerCase();
  if (s === 'high' || s === 'critical') return 'error';
  if (s === 'medium') return 'warning';
  return 'info';
}


// ─── Exception trend ───────────────────────────────────────────────

function ExceptionTrendPanel({ trend, summary }) {
  const safeTrend = Array.isArray(trend) ? trend : [];
  if (safeTrend.length === 0) {
    return html`
      <section class="cl-record-panel">
        <header class="cl-record-panel-head">
          <h2>Exception trend</h2>
        </header>
        <p class="cl-record-empty">
          Not enough invoices yet to plot a trend. Comes back to life once
          this vendor has a few months of activity.
        </p>
      </section>
    `;
  }

  const max = Math.max(...safeTrend.map((p) => Number(p.exception_count || 0)), 1);

  return html`
    <section class="cl-record-panel">
      <header class="cl-record-panel-head">
        <h2>Exception trend</h2>
        <span class="cl-record-panel-eyebrow">${safeTrend.length} buckets</span>
      </header>
      <svg class="cl-vendor-chart" viewBox="0 0 100 30" preserveAspectRatio="none"
        role="img" aria-label="Exception count over time">
        <line x1="0" y1="30" x2="100" y2="30" stroke="#E2E8F0" stroke-width="0.2" />
        ${safeTrend.map((p, idx) => {
          const v = Number(p.exception_count || 0);
          const heightPct = (v / max) * 26;
          const w = (100 / safeTrend.length) * 0.8;
          const x = (idx / safeTrend.length) * 100 + (100 / safeTrend.length) * 0.1;
          const y = 30 - heightPct;
          return html`
            <rect key=${idx} x=${x} y=${y} width=${w} height=${heightPct}
              fill="#CA8A04" opacity="0.85">
              <title>${p.bucket || p.period}: ${v} exceptions</title>
            </rect>`;
        })}
      </svg>
      <div class="cl-vendor-chart-axis">
        <span>${safeTrend[0]?.bucket || safeTrend[0]?.period || ''}</span>
        <span>${safeTrend[safeTrend.length - 1]?.bucket || safeTrend[safeTrend.length - 1]?.period || ''}</span>
      </div>
    </section>
  `;
}


// ─── Recent invoices ───────────────────────────────────────────────

function RecentInvoicesPanel({ invoices, navigate }) {
  if (!Array.isArray(invoices) || invoices.length === 0) {
    return html`
      <section class="cl-record-panel">
        <header class="cl-record-panel-head">
          <h2>Recent invoices</h2>
        </header>
        <p class="cl-record-empty">No invoices in the last 180 days.</p>
      </section>
    `;
  }

  return html`
    <section class="cl-record-panel">
      <header class="cl-record-panel-head">
        <h2>Recent invoices</h2>
        <span class="cl-record-panel-eyebrow">${invoices.length} shown</span>
      </header>
      <table class="cl-record-bill-line-table">
        <thead>
          <tr>
            <th>Invoice no.</th>
            <th>Date</th>
            <th class="cl-record-num">Amount</th>
            <th>State</th>
            <th>Exception</th>
          </tr>
        </thead>
        <tbody>
          ${invoices.slice(0, 25).map((inv) => html`
            <tr key=${inv.id || inv.invoice_number} class="cl-vendor-invoice-row"
              onClick=${() => navigate(`/items/${encodeURIComponent(inv.id)}`)}>
              <td><code>${inv.invoice_number || inv.id}</code></td>
              <td class="cl-record-muted">
                ${inv.invoice_date ? fmtDate(inv.invoice_date)
                  : (inv.created_at ? fmtDateTime(inv.created_at) : '—')}
              </td>
              <td class="cl-record-num">
                ${formatAmount(inv.amount, inv.currency)}
              </td>
              <td>${(inv.state || '').replace(/_/g, ' ')}</td>
              <td class="cl-record-muted">${inv.exception_code || '—'}</td>
            </tr>
          `)}
        </tbody>
      </table>
    </section>
  `;
}


// ─── Module 4 — Business registry verification banner ────────────
//
// Spec line 158: "Verification: agent attempts auto-verification on
// creation (IBAN check, business registry lookup, prior payment
// match)." This banner exposes the registry-lookup verb with a
// click-to-verify button. Persists the result on the vendor profile
// so subsequent views show the verified state without re-querying.

function RegistryVerifyBanner({ api, vendorName, profile, toast, onChanged }) {
  const [busy, setBusy] = useState(false);
  const verified = !!profile?.registry_verified;
  const provider = profile?.registry_verification_provider;
  const verifiedAt = profile?.registry_verification_at;
  const payload = profile?.registry_verification_payload || {};

  const onVerify = async () => {
    setBusy(true);
    try {
      const params = new URLSearchParams();
      if (profile?.registration_number) params.set('registration_number', profile.registration_number);
      if (profile?.jurisdiction) params.set('jurisdiction', profile.jurisdiction);
      const resp = await api(
        `/api/vendors/${encodeURIComponent(vendorName)}/verify-registration${params.toString() ? `?${params.toString()}` : ''}`,
        { method: 'POST' },
      );
      if (resp?.status === 'verified') {
        toast?.(`Verified at ${resp.registry || 'registry'}: ${resp.company_name || vendorName}`, 'success');
      } else if (resp?.status === 'not_found') {
        toast?.('Not found in the registry. Add a registration number on the vendor profile and try again.', 'info');
      } else if (resp?.status === 'ambiguous') {
        toast?.(`Ambiguous match. ${(resp.candidates || []).length} candidates returned.`, 'info');
      } else {
        toast?.(resp?.error || 'Verification failed.', 'error');
      }
      onChanged?.();
    } catch (exc) {
      toast?.(`Verification failed: ${exc?.message || exc}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  return html`
    <section class="cl-vendor-registry-banner">
      <div>
        <strong>Business registry</strong>
        ${verified ? html`
          <p class="cl-record-muted">
            Verified via ${provider || 'registry'}${verifiedAt ? ` · ${new Date(verifiedAt).toLocaleDateString()}` : ''}
            ${payload.company_number ? html` · <code>${payload.company_number}</code>` : null}
            ${payload.jurisdiction ? html` · ${String(payload.jurisdiction).toUpperCase()}` : null}
            ${typeof payload.match_score === 'number' ? html` · match ${Math.round(payload.match_score * 100)}%` : null}
          </p>
        ` : html`
          <p class="cl-record-muted">
            Confirm this vendor exists in the official business registry. Defaults to OpenCorporates.
            ${profile?.jurisdiction === 'gb' ? ' UK customers can switch to Companies House via REGISTRY_PROVIDER.' : ''}
          </p>
        `}
      </div>
      <button class="btn btn-secondary btn-sm" onClick=${onVerify} disabled=${busy}>
        ${busy ? 'Verifying…' : (verified ? 'Re-verify' : 'Verify in registry')}
      </button>
    </section>
  `;
}
