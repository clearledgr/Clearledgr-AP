/**
 * Reports Page — lightweight AP reporting kept secondary to Pipeline.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDollar, useAction } from '../route-helpers.js';
import {
  clearPipelineNavigation,
  getStarterPipelineViews,
  writePipelinePreferences,
} from '../pipeline-views.js';

const html = htm.bind(h);

function MetricCard({ label, value, detail }) {
  return html`<div style="padding:18px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
    <div style="font-size:26px;font-weight:700;letter-spacing:-0.02em">${value}</div>
    <div style="font-size:13px;font-weight:600;margin-top:2px">${label}</div>
    ${detail ? html`<div class="muted" style="margin-top:6px;font-size:12px">${detail}</div>` : null}
  </div>`;
}

function ReportRow({ label, value, detail }) {
  return html`<div style="display:flex;justify-content:space-between;gap:16px;padding:10px 0;border-bottom:1px solid var(--border)">
    <div>
      <div style="font-weight:600">${label}</div>
      ${detail ? html`<div class="muted" style="font-size:12px;margin-top:3px">${detail}</div>` : null}
    </div>
    <div style="font-weight:700;text-align:right">${value}</div>
  </div>`;
}

export default function ReportsPage({ api, bootstrap, orgId, userEmail, navigate, toast }) {
  const pipelineScope = useMemo(() => ({ orgId, userEmail }), [orgId, userEmail]);
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);
  const starterViews = useMemo(() => getStarterPipelineViews({}), []);

  const loadMetrics = async ({ silent = false } = {}) => {
    setLoading(true);
    try {
      const data = await api(`/api/ap/items/metrics/aggregation?organization_id=${encodeURIComponent(orgId)}`, { silent });
      setMetrics(data?.metrics || null);
    } catch {
      setMetrics(null);
      if (!silent) toast?.('Could not load AP reporting.', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadMetrics({ silent: true });
  }, [api, orgId]);

  const [refresh, refreshing] = useAction(async () => {
    await loadMetrics();
    toast?.('AP reporting refreshed.', 'success');
  });

  const openStarterView = (view) => {
    if (!view?.snapshot) return;
    clearPipelineNavigation(pipelineScope);
    writePipelinePreferences(pipelineScope, view.snapshot);
    navigate('clearledgr/pipeline');
  };

  const dashboard = bootstrap?.dashboard || {};
  const totals = metrics?.totals || {};
  const sources = metrics?.sources || {};
  const duplicates = metrics?.duplicates || {};
  const topVendors = Array.isArray(metrics?.spend_by_vendor) ? metrics.spend_by_vendor.slice(0, 6) : [];
  const sourceTypes = Object.entries(sources.link_count_by_type || {}).sort((left, right) => right[1] - left[1]).slice(0, 6);

  if (loading) {
    return html`<div class="panel" style="text-align:center;padding:48px"><p class="muted">Loading AP reporting…</p></div>`;
  }

  return html`
    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap">
        <div>
          <h3 style="margin:0 0 6px">AP reporting</h3>
          <p class="muted" style="margin:0;max-width:620px">
            Keep reporting narrow: queue health, spend concentration, source coverage, and duplicate risk. Pipeline remains the operational surface.
          </p>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="alt" onClick=${refresh} disabled=${refreshing}>${refreshing ? 'Refreshing…' : 'Refresh'}</button>
          <button onClick=${() => navigate('clearledgr/pipeline')}>Open pipeline</button>
        </div>
      </div>
    </div>

    <div class="kpi-row">
      <${MetricCard} label="Tracked invoices" value=${Number(totals.items || dashboard.total_invoices || 0).toLocaleString()} />
      <${MetricCard} label="Open items" value=${Number(totals.open_items || 0).toLocaleString()} detail=${`${Number(dashboard.pending_approval || 0).toLocaleString()} waiting approval`} />
      <${MetricCard} label="Tracked spend" value=${fmtDollar(totals.total_amount || 0)} detail=${`${Number(totals.amount_unavailable_count || 0).toLocaleString()} without amount`} />
      <${MetricCard} label="Duplicate clusters" value=${Number(duplicates.cluster_count || 0).toLocaleString()} detail=${`${Number(duplicates.duplicate_invoice_count || 0).toLocaleString()} duplicate invoices`} />
    </div>

    <div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px">
      <div style="display:flex;flex-direction:column;gap:20px">
        <div class="panel">
          <h3 style="margin-top:0">Top vendors by tracked spend</h3>
          ${topVendors.length === 0
            ? html`<p class="muted" style="margin:0">No vendor spend data yet.</p>`
            : html`${topVendors.map((row) => html`
                <${ReportRow}
                  key=${row.vendor_name}
                  label=${row.vendor_name || 'Unknown vendor'}
                  value=${fmtDollar(row.total_amount || 0)}
                  detail=${`${Number(row.open_count || 0).toLocaleString()} open · ${Number(row.invoice_count || 0).toLocaleString()} tracked invoices`}
                />
              `)}`}
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Source coverage</h3>
          <div style="display:flex;flex-direction:column;gap:8px">
            <${ReportRow}
              label="Total linked sources"
              value=${Number(sources.total_links || 0).toLocaleString()}
              detail=${`${Number(sources.items_with_sources || 0).toLocaleString()} invoices have linked evidence`}
            />
            <${ReportRow}
              label="Average links per invoice"
              value=${Number(sources.avg_links_per_item || 0).toFixed(2)}
              detail="Across all tracked AP items"
            />
            <${ReportRow}
              label="Average links per linked invoice"
              value=${Number(sources.avg_links_per_linked_item || 0).toFixed(2)}
              detail="Only invoices with at least one linked source"
            />
          </div>

          ${sourceTypes.length > 0 && html`
            <div style="margin-top:14px">
              <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;margin-bottom:8px">Connected source types</div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                ${sourceTypes.map(([sourceType, count]) => html`
                  <span key=${sourceType} style="padding:5px 10px;border-radius:999px;border:1px solid var(--border);background:var(--bg);font-size:12px;font-weight:600">
                    ${sourceType} ${count}
                  </span>
                `)}
              </div>
            </div>
          `}
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:20px">
        <div class="panel">
          <h3 style="margin-top:0">Start from the right queue view</h3>
          <p class="muted" style="margin:0 0 12px">Reports should send you back into queue work, not trap you in a dashboard.</p>
          <div style="display:flex;flex-direction:column;gap:10px">
            ${starterViews.slice(0, 4).map((view) => html`
              <div key=${view.id} style="display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
                <div>
                  <strong style="display:block;font-size:13px">${view.name}</strong>
                  <span class="muted" style="font-size:12px">${view.description}</span>
                </div>
                <button class="alt" onClick=${() => openStarterView(view)} style="padding:8px 12px;font-size:12px">Open view</button>
              </div>
            `)}
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Keep this page secondary</h3>
          <p class="muted" style="margin:0">
            Use this page to orient spend, evidence coverage, and duplicate risk. Operators should still work approvals, vendor replies, and posting from Pipeline and the shared AP record.
          </p>
        </div>
      </div>
    </div>
  `;
}
