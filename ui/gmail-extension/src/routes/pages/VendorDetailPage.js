/**
 * Vendor Detail Page — organization-style record view for AP vendor context.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDate, fmtDateTime, fmtDollar, useAction } from '../route-helpers.js';
import { navigateToRecordDetail } from '../../utils/record-route.js';
import { formatAmount, getExceptionLabel, getExceptionReason, getStateLabel, openSourceEmail } from '../../utils/formatters.js';
import {
  clearPipelineNavigation,
  focusPipelineItem,
  readPipelinePreferences,
  writePipelinePreferences,
} from '../pipeline-views.js';
import { writeReviewPreferences } from '../review-preferences.js';

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
  return html`<div class="secondary-stat-card">
    <strong>${label}</strong>
    <span style="font-family:var(--font-display);font-size:24px;font-weight:700;letter-spacing:-0.03em;color:var(--ink);display:block;margin-bottom:4px">${value}</span>
    ${detail ? html`<span>${detail}</span>` : null}
  </div>`;
}

function getRecordId(item) {
  return String(item?.ap_item_id || item?.id || '').trim();
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
  const openIssues = Array.isArray(payload?.open_issues) ? payload.open_issues : [];
  const issueSummary = payload?.issue_summary || {};
  const senderEmails = Array.isArray(summary?.sender_emails) ? summary.sender_emails : [];
  const topStates = Array.isArray(summary?.top_states) ? summary.top_states : [];
  const anomalyFlags = Array.isArray(profile?.anomaly_flags) ? profile.anomaly_flags : [];

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
    navigate('clearledgr/invoices');
  };

  const openItemDetail = (item) => {
    const recordId = getRecordId(item);
    if (!recordId) return;
    focusPipelineItem(pipelineScope, { ...item, id: recordId }, 'vendor_record');
    navigateToRecordDetail(navigate, recordId);
  };

  const openVendorIssues = () => {
    if (!vendorName) return;
    writeReviewPreferences(pipelineScope, { searchQuery: vendorName });
    navigate('clearledgr/review');
  };

  const openIssueEmail = (item) => {
    const ok = openSourceEmail(item);
    if (!ok) toast?.('Could not open the source email for this issue.', 'error');
  };

  if (loading) {
    return html`<div class="panel" style="text-align:center;padding:48px"><p class="muted">Loading vendor record…</p></div>`;
  }

  if (!payload) {
    return html`
      <div class="panel">
        <h3 style="margin-top:0">Vendor not found</h3>
        <p class="muted" style="margin:0 0 12px">This vendor does not have a shared AP record yet.</p>
        <button class="btn-secondary" onClick=${() => navigate('clearledgr/vendors')}>Back to vendors</button>
      </div>
    `;
  }

  return html`
    <div class="panel">
      <div class="panel-head">
        <div>
          <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;margin-bottom:6px">Vendor record</div>
          <h3 style="margin:0 0 6px">${payload.vendor_name || vendorName}</h3>
          <p class="muted" style="margin:0;max-width:620px">
            Shared AP memory for this supplier: open invoices, recent outcomes, anomaly flags, and posting context.
          </p>
        </div>
        <div class="toolbar-actions">
          <button class="btn-secondary btn-sm" onClick=${() => navigate('clearledgr/vendors')}>Back to vendors</button>
          <button class="btn-secondary btn-sm" onClick=${refresh} disabled=${refreshing}>${refreshing ? 'Refreshing…' : 'Refresh'}</button>
          <button class="btn-secondary btn-sm" onClick=${openVendorIssues}>Review issues</button>
          <button class="btn-primary btn-sm" onClick=${openVendorInPipeline}>Open vendor in invoices</button>
        </div>
      </div>
    </div>

    <div class="secondary-stat-grid" style="margin-bottom:14px">
      <${MetricCard} label="Tracked invoices" value=${Number(summary.invoice_count || 0).toLocaleString()} />
      <${MetricCard} label="Open now" value=${Number(summary.open_count || 0).toLocaleString()} detail=${`${Number(summary.issue_count || 0)} with issues`} />
      <${MetricCard} label="Posted" value=${Number(summary.posted_count || 0).toLocaleString()} detail=${`${Number(summary.failed_count || 0)} failed post`} />
      <${MetricCard} label="Tracked spend" value=${fmtDollar(summary.total_amount || 0)} detail=${summary.last_activity_at ? `Last activity ${fmtDateTime(summary.last_activity_at)}` : 'No recent activity'} />
    </div>

    ${(profile.suggested_gl || profile.override_rate != null || anomalyFlags.length > 0 || profile.last_correction_at) && html`
      <div class="panel" style="margin-top:0">
        <div class="panel-head compact">
          <div>
            <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;margin-bottom:6px">Vendor intelligence</div>
            <p class="muted" style="margin:0">AI-derived insights from this vendor's invoice history and GL correction patterns.</p>
          </div>
        </div>
        <div class="secondary-stat-grid">
          ${profile.suggested_gl ? html`
            <div class="secondary-stat-card">
              <strong>Suggested GL</strong>
              <span style="font-family:var(--font-display);font-size:20px;font-weight:700;color:var(--ink)">${profile.suggested_gl}</span>
              <span>Most likely GL code based on posting history</span>
            </div>
          ` : null}
          ${profile.override_rate != null ? html`
            <div class="secondary-stat-card">
              <strong>Override rate</strong>
              <span style="font-family:var(--font-display);font-size:20px;font-weight:700;color:var(--ink)">${(Number(profile.override_rate) * 100).toFixed(1)}%</span>
              <span>How often operators override the AI recommendation</span>
            </div>
          ` : null}
          ${profile.last_correction_at ? html`
            <div class="secondary-stat-card">
              <strong>Last GL correction</strong>
              <span style="font-family:var(--font-display);font-size:20px;font-weight:700;color:var(--ink)">${fmtDate(profile.last_correction_at)}</span>
              <span>Most recent time a GL code was corrected for this vendor</span>
            </div>
          ` : null}
        </div>
        ${anomalyFlags.length > 0 ? html`
          <div style="margin-top:14px">
            <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;margin-bottom:8px">Risk indicators</div>
            <div class="secondary-chip-row">
              ${anomalyFlags.map((flag) => html`<span key=${flag} class="secondary-chip" style="background:#FEF2F2;border-color:#FECACA;color:#B91C1C">${String(flag).replace(/_/g, ' ')}</span>`)}
            </div>
          </div>
        ` : null}
      </div>
    `}

    <div class="secondary-shell">
      <div class="secondary-stack">
        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0 0 6px">Open issues and follow-up</h3>
              <p class="muted" style="margin:0">Work the vendor-specific blockers that still need action before this supplier’s invoices can move cleanly.</p>
            </div>
            <button class="btn-secondary btn-sm" onClick=${openVendorIssues}>Open in review</button>
          </div>
          ${openIssues.length === 0
            ? html`<p class="muted" style="margin:0">No open vendor issues right now.</p>`
            : html`<div class="secondary-card-list">
                ${openIssues.map((item) => html`
                  <div key=${item.id} class="secondary-card">
                    <div class="secondary-card-head">
                      <div class="secondary-card-copy">
                        <div class="secondary-chip-row" style="margin-bottom:6px">
                          <strong style="font-size:14px">${item.invoice_number || 'No invoice #'}</strong>
                          <span class="secondary-chip" style="background:#FFF7ED;border-color:#FED7AA;color:#9A3412">
                            ${item.issue_label || 'Open issue'}
                          </span>
                          <${StatePill} state=${item.state} />
                        </div>
                        <div class="secondary-card-meta">
                          ${formatAmount(item.amount, item.currency)} · Updated ${fmtDateTime(item.updated_at)}
                        </div>
                        <div class="secondary-card-meta" style="margin-top:6px">
                          ${item.issue_summary || getIssueSummary(item)}
                        </div>
                        ${item.exception_code
                          ? html`<div class="secondary-card-meta" style="margin-top:4px">${getExceptionLabel(item.exception_code)}</div>`
                          : null}
                      </div>
                      <div class="secondary-inline-actions">
                        <button class="btn-secondary btn-sm" onClick=${() => openItemDetail(item)}>Open record</button>
                        ${(item.thread_id || item.message_id) && html`
                          <button class="btn-ghost btn-sm" onClick=${() => openIssueEmail(item)}>Open email</button>
                        `}
                      </div>
                    </div>
                  </div>
                `)}
              </div>`}
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Open and recent invoices</h3>
              <p class="muted" style="margin:4px 0 0">The current invoice context for this vendor, including active blockers and recent outcomes.</p>
            </div>
          </div>
          ${recentItems.length === 0
            ? html`<p class="muted" style="margin:0">No recent invoices for this vendor yet.</p>`
            : html`<div class="secondary-card-list">
                ${recentItems.map((item) => html`
                  <div key=${item.id} class="secondary-card">
                    <div class="secondary-card-head">
                      <div class="secondary-card-copy">
                        <div class="secondary-chip-row" style="margin-bottom:6px">
                          <strong style="font-size:14px">${item.invoice_number || 'No invoice #'}</strong>
                          <${StatePill} state=${item.state} />
                        </div>
                        <div class="secondary-card-meta">
                          ${formatAmount(item.amount, item.currency)} · Due ${item.due_date ? fmtDate(item.due_date) : '—'} · Updated ${fmtDateTime(item.updated_at)}
                        </div>
                        ${item.erp_reference
                          ? html`<div class="secondary-card-meta" style="margin-top:4px">ERP ${item.erp_reference}</div>`
                          : null}
                        ${item.exception_code
                          ? html`<div class="secondary-card-meta" style="margin-top:4px">${getExceptionLabel(item.exception_code)}${getExceptionReason(item.exception_code) ? ` · ${getExceptionReason(item.exception_code)}` : ''}</div>`
                          : null}
                      </div>
                      <div class="secondary-inline-actions">
                        <button class="btn-secondary btn-sm" onClick=${() => openItemDetail(item)}>Open record</button>
                      </div>
                    </div>
                  </div>
                `)}
              </div>`}
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Outcome history</h3>
              <p class="muted" style="margin:4px 0 0">What recently happened to this vendor’s invoices after review and posting.</p>
            </div>
          </div>
          ${history.length === 0
            ? html`<p class="muted" style="margin:0">No vendor outcome history yet.</p>`
            : html`<div class="secondary-card-list">
                ${history.map((entry) => html`
                  <div key=${entry.id || `${entry.ap_item_id}-${entry.created_at}`} class="secondary-card">
                    <div class="secondary-card-head">
                      <div class="secondary-card-copy">
                        <span class="secondary-card-title">${entry.invoice_number || entry.ap_item_id || 'Invoice outcome'}</span>
                        <div class="secondary-card-meta">
                          ${formatAmount(entry.amount, entry.currency)} · ${getStateLabel(String(entry.final_state || 'received').trim().toLowerCase())}
                        </div>
                      </div>
                      <div class="secondary-card-stat">
                        <strong>${fmtDate(entry.created_at)}</strong>
                        <span>${fmtDateTime(entry.created_at)}</span>
                      </div>
                    </div>
                  </div>
                `)}
              </div>`}
        </div>
      </div>

      <div class="secondary-stack">
        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Vendor profile</h3>
              <p class="muted" style="margin:4px 0 0">Persistent AP settings and contact assumptions Clearledgr is carrying for this supplier.</p>
            </div>
          </div>
          <div class="detail-row-list">
            <div class="detail-row">
              <span class="detail-row-label">Primary email</span>
              <span class="detail-row-value">${summary.primary_email || '—'}</span>
            </div>
            <div class="detail-row">
              <span class="detail-row-label">Payment terms</span>
              <span class="detail-row-value">${profile.payment_terms || '—'}</span>
            </div>
            <div class="detail-row">
              <span class="detail-row-label">Requires PO</span>
              <span class="detail-row-value">${profile.requires_po ? 'Yes' : 'No'}</span>
            </div>
            <div class="detail-row">
              <span class="detail-row-label">Always approved</span>
              <span class="detail-row-value">${profile.always_approved ? 'Yes' : 'No'}</span>
            </div>
            <div class="detail-row">
              <span class="detail-row-label">Approval override rate</span>
              <span class="detail-row-value">${Number(profile.approval_override_rate || 0).toFixed(2)}</span>
            </div>
          </div>

          ${senderEmails.length > 0 && html`
            <div style="margin-top:14px">
              <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;margin-bottom:8px">Known sender emails</div>
              <div class="secondary-chip-row">
                ${senderEmails.map((email) => html`<span key=${email} class="secondary-chip">${email}</span>`)}
              </div>
            </div>
          `}

          ${anomalyFlags.length > 0 && html`
            <div style="margin-top:14px">
              <div class="muted" style="font-size:12px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;margin-bottom:8px">Anomaly flags</div>
              <div class="secondary-chip-row">
                ${anomalyFlags.map((flag) => html`<span key=${flag} class="secondary-chip" style="background:#FEF2F2;border-color:#FECACA;color:#B91C1C">${String(flag).replace(/_/g, ' ')}</span>`)}
              </div>
            </div>
          `}
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Issue summary</h3>
              <p class="muted" style="margin:4px 0 0">The kinds of friction Clearledgr keeps seeing from this vendor.</p>
            </div>
          </div>
          <div class="detail-row-list">
            <div class="detail-row">
              <span class="detail-row-label">Open issues</span>
              <span class="detail-row-value">${Number(issueSummary.total || 0).toLocaleString()}</span>
            </div>
            <div class="detail-row">
              <span class="detail-row-label">Field review</span>
              <span class="detail-row-value">${Number(issueSummary.field_review || 0).toLocaleString()}</span>
            </div>
            <div class="detail-row">
              <span class="detail-row-label">Needs info</span>
              <span class="detail-row-value">${Number(issueSummary.needs_info || 0).toLocaleString()}</span>
            </div>
            <div class="detail-row">
              <span class="detail-row-label">Failed post</span>
              <span class="detail-row-value">${Number(issueSummary.failed_post || 0).toLocaleString()}</span>
            </div>
            <div class="detail-row">
              <span class="detail-row-label">Policy / entity</span>
              <span class="detail-row-value">${Number((issueSummary.policy_exception || 0) + (issueSummary.entity_route || 0)).toLocaleString()}</span>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Common workflow states</h3>
              <p class="muted" style="margin:4px 0 0">Where this vendor’s records most often end up in the AP flow.</p>
            </div>
          </div>
          ${topStates.length === 0
            ? html`<p class="muted" style="margin:0">No state history yet.</p>`
            : html`<div class="secondary-card-list">
                ${topStates.map((row) => html`
                  <div key=${row.state} class="secondary-row">
                    <div class="secondary-row-copy">
                      <strong>${getStateLabel(String(row.state || 'received').trim().toLowerCase())}</strong>
                    </div>
                    <div class="secondary-inline-actions">
                      <span class="secondary-chip">${Number(row.count || 0).toLocaleString()}</span>
                    </div>
                  </div>
                `)}
              </div>`}
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin:0">Recurring issues</h3>
              <p class="muted" style="margin:4px 0 0">The exception patterns that repeat most often for this vendor.</p>
            </div>
          </div>
          ${topExceptionCodes.length === 0
            ? html`<p class="muted" style="margin:0">No recurring issue patterns yet.</p>`
            : html`<div class="secondary-card-list">
                ${topExceptionCodes.map((row) => html`
                  <div key=${row.exception_code} class="secondary-row">
                    <div class="secondary-row-copy">
                      <strong>${getExceptionLabel(row.exception_code)}</strong>
                    </div>
                    <div class="secondary-inline-actions">
                      <span class="secondary-chip">${Number(row.count || 0).toLocaleString()}</span>
                    </div>
                  </div>
                `)}
              </div>`}
        </div>
      </div>
    </div>
  `;
}
