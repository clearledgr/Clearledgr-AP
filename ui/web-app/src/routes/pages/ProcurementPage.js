/**
 * Procurement Page — purchase-order approval workflow (BoxType #3).
 *
 * Lists POs, creates a draft, and drives the approval lifecycle
 * (submit -> approve / reject -> close / cancel) against the
 * /api/workspace/purchase-orders endpoints. Mirrors VendorsPage.
 *
 * NOTE: written to the established page patterns but pending browser QA.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { useAction } from '../route-helpers.js';
import { formatAmount } from '../../utils/formatters.js';
import { EmptyState, LoadingSkeleton, ErrorRetry } from '../../components/StatePrimitives.js';

const html = htm.bind(h);

// Which lifecycle actions are offered for a PO in a given state.
const ACTIONS_BY_STATE = {
  draft: [['submit', 'Submit for approval'], ['cancel', 'Cancel']],
  pending_approval: [['approve', 'Approve'], ['reject', 'Reject'], ['cancel', 'Cancel']],
  approved: [['close', 'Close']],
  partially_received: [['close', 'Close']],
  fully_received: [['close', 'Close']],
};

export default function ProcurementPage({ api, orgId, toast }) {
  const [pos, setPos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [search, setSearch] = useState('');
  const [vendorName, setVendorName] = useState('');
  const [amount, setAmount] = useState('');

  const load = async ({ silent = false } = {}) => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api('/api/workspace/purchase-orders', { silent });
      setPos(Array.isArray(data?.purchase_orders) ? data.purchase_orders : []);
    } catch (exc) {
      setPos([]);
      setLoadError(exc?.message || 'Could not load purchase orders.');
      if (!silent) toast?.('Could not load purchase orders.', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load({ silent: true });
  }, [api, orgId]);

  const [createPo, creating] = useAction(async () => {
    const name = String(vendorName || '').trim();
    if (!name) {
      toast?.('Vendor name is required.', 'error');
      return;
    }
    await api('/api/workspace/purchase-orders', {
      method: 'POST',
      body: { vendor_name: name, total_amount: Number(amount) || 0 },
    });
    setVendorName('');
    setAmount('');
    toast?.('Purchase order created.', 'success');
    await load();
  });

  const act = async (poId, action) => {
    try {
      await api(`/api/workspace/purchase-orders/${encodeURIComponent(poId)}/${action}`, {
        method: 'POST',
        body: { reason: '' },
      });
      toast?.(`PO ${action} done.`, 'success');
      await load();
    } catch (exc) {
      toast?.(exc?.message || `Could not ${action} the PO.`, 'error');
    }
  };

  const filtered = useMemo(() => {
    const q = String(search || '').trim().toLowerCase();
    if (!q) return pos;
    return pos.filter((p) => String(p.vendor_name || '').toLowerCase().includes(q)
      || String(p.po_number || '').toLowerCase().includes(q));
  }, [pos, search]);

  if (loading) {
    return html`<div class="panel"><${LoadingSkeleton} rows=${5} label="Loading purchase orders" /></div>`;
  }
  if (loadError) {
    return html`<div class="panel"><${ErrorRetry}
      message="Couldn't load purchase orders."
      detail=${loadError}
      onRetry=${() => load()}
    /></div>`;
  }

  return html`
    <div class="panel">
      <div class="panel__header">
        <h1>Procurement</h1>
        <input
          class="input"
          placeholder="Search vendor or PO #"
          value=${search}
          onInput=${(e) => setSearch(e.target.value)}
        />
      </div>

      <div class="po-create" style="display:flex; gap:8px; align-items:flex-end; margin:12px 0;">
        <label>Vendor
          <input class="input" value=${vendorName} onInput=${(e) => setVendorName(e.target.value)} />
        </label>
        <label>Amount
          <input class="input" type="number" value=${amount} onInput=${(e) => setAmount(e.target.value)} />
        </label>
        <button class="btn btn--primary" disabled=${creating} onClick=${createPo}>
          ${creating ? 'Creating…' : 'New PO'}
        </button>
      </div>

      ${filtered.length === 0
        ? html`<${EmptyState} title="No purchase orders" description="Create one to start the approval workflow." />`
        : html`
          <table class="data-table">
            <thead><tr>
              <th>PO #</th><th>Vendor</th><th>Amount</th><th>Status</th><th>Actions</th>
            </tr></thead>
            <tbody>
              ${filtered.map((p) => html`
                <tr key=${p.po_id}>
                  <td>${p.po_number || p.po_id}</td>
                  <td>${p.vendor_name || '—'}</td>
                  <td>${formatAmount(p.total_amount, p.currency)}</td>
                  <td><span class="badge">${p.status}</span></td>
                  <td>
                    ${(ACTIONS_BY_STATE[p.status] || []).map(([action, label]) => html`
                      <button class="btn btn--sm" onClick=${() => act(p.po_id, action)}>${label}</button>
                    `)}
                  </td>
                </tr>
              `)}
            </tbody>
          </table>
        `}
    </div>
  `;
}
