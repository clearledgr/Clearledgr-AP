/**
 * Vendor Database Page — like Streak's Contacts/Organizations view.
 * Rich vendor list with spend history and status breakdown.
 */
import { h } from 'preact';
import { useState, useEffect, useMemo } from 'preact/hooks';
import htm from 'htm';
import { fmtDollar, useAction } from '../route-helpers.js';

const html = htm.bind(h);

export default function VendorsPage({ api, toast, orgId, navigate }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  useEffect(() => {
    setLoading(true);
    api(`/extension/worklist?organization_id=${encodeURIComponent(orgId)}&limit=1000`)
      .then(data => setItems(data?.items || []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [api, orgId]);

  const vendors = useMemo(() => {
    const map = {};
    for (const item of items) {
      const name = (item.vendor_name || item.vendor || 'Unknown').trim();
      if (!map[name]) map[name] = { name, invoices: 0, totalAmount: 0, lastDate: '', states: {}, emails: new Set() };
      const v = map[name];
      v.invoices += 1;
      v.totalAmount += Number(item.amount) || 0;
      const date = item.due_date || item.created_at || '';
      if (date > v.lastDate) v.lastDate = date;
      v.states[item.state] = (v.states[item.state] || 0) + 1;
      if (item.sender) v.emails.add(item.sender);
    }
    return Object.values(map).sort((a, b) => b.totalAmount - a.totalAmount);
  }, [items]);

  const filtered = useMemo(() => {
    if (!search.trim()) return vendors;
    const q = search.trim().toLowerCase();
    return vendors.filter(v => v.name.toLowerCase().includes(q));
  }, [vendors, search]);

  if (loading) return html`<div class="panel" style="text-align:center;padding:48px"><p class="muted">Loading vendors\u2026</p></div>`;

  return html`
    <div class="kpi-row" style="grid-template-columns:1fr 1fr 1fr">
      <div class="kpi-card">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums">${vendors.length}</strong>
        <span>Vendors</span>
      </div>
      <div class="kpi-card">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums">${items.length}</strong>
        <span>Total invoices</span>
      </div>
      <div class="kpi-card">
        <strong style="font-family:var(--font-mono);font-variant-numeric:tabular-nums">${fmtDollar(vendors.reduce((s, v) => s + v.totalAmount, 0))}</strong>
        <span>Total spend</span>
      </div>
    </div>

    <div style="
      padding:12px 16px;background:var(--surface);border:1px solid var(--border);
      border-radius:var(--radius-md) var(--radius-md) 0 0;border-bottom:none;
    ">
      <div style="position:relative">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--ink-muted)" stroke-width="2" style="position:absolute;left:10px;top:50%;transform:translateY(-50%)"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        <input placeholder="Search vendors\u2026" value=${search} onInput=${e => setSearch(e.target.value)}
          style="width:100%;padding:8px 8px 8px 34px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;font-family:inherit;background:var(--bg)" />
      </div>
    </div>

    <div style="background:var(--surface);border:1px solid var(--border);border-radius:0 0 var(--radius-md) var(--radius-md);overflow-x:auto">
      <table class="table">
        <thead><tr>
          <th>Vendor</th>
          <th style="text-align:right">Total Spend</th>
          <th style="text-align:right">Invoices</th>
          <th>Status</th>
          <th>Last Activity</th>
        </tr></thead>
        <tbody>
          ${filtered.length === 0
            ? html`<tr><td colspan="5" class="muted" style="text-align:center;padding:32px">
                ${search ? 'No vendors match your search.' : html`
                  <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--ink-muted)" stroke-width="1" style="margin-bottom:8px;opacity:0.4"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>
                  <div>No vendors yet. Vendors appear when invoices are processed.</div>
                `}
              </td></tr>`
            : filtered.map(v => html`<tr key=${v.name}>
              <td>
                <div style="font-weight:500">${v.name}</div>
                ${v.emails.size > 0 ? html`<div style="font-size:11px;color:var(--ink-muted);margin-top:1px">${[...v.emails][0]}</div>` : null}
              </td>
              <td style="text-align:right;font-family:var(--font-mono);font-weight:600;font-variant-numeric:tabular-nums">${fmtDollar(v.totalAmount)}</td>
              <td style="text-align:right;font-family:var(--font-mono)">${v.invoices}</td>
              <td>
                <div style="display:flex;gap:4px;flex-wrap:wrap">
                  ${Object.entries(v.states).map(([st, count]) => html`
                    <span style="font-size:10px;font-weight:500;padding:2px 7px;border-radius:999px;background:var(--bg);border:1px solid var(--border);color:var(--ink-secondary)">${st.replace(/_/g, ' ')} ${count}</span>
                  `)}
                </div>
              </td>
              <td style="color:var(--ink-muted);font-size:13px">${v.lastDate ? v.lastDate.slice(0, 10) : '\u2014'}</td>
            </tr>`)
          }
        </tbody>
      </table>
    </div>
  `;
}
