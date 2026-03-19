import { h } from 'preact';
import htm from 'htm';
import { useAction } from '../route-helpers.js';

const html = htm.bind(h);

export default function TeamPage({ bootstrap, api, toast, orgId, onRefresh }) {
  const invites = bootstrap?.teamInvites || [];
  const [createInvite] = useAction(async () => {
    const email = document.getElementById('cl-invite-email')?.value?.trim();
    const role = document.getElementById('cl-invite-role')?.value;
    await api('/api/admin/team/invites', { method: 'POST', body: JSON.stringify({ organization_id: orgId, email, role }) });
    toast(`Invite sent to ${email}.`); onRefresh();
  });
  const [revokeInvite] = useAction(async (id) => {
    await api(`/api/admin/team/invites/${id}/revoke?organization_id=${encodeURIComponent(orgId)}`, { method: 'POST' });
    toast('Invite revoked.'); onRefresh();
  });

  return html`
    <div class="panel">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" stroke-width="1.5"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
        <h3 style="margin:0">Invite teammate</h3>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <input id="cl-invite-email" placeholder="teammate@company.com" style="flex:1;min-width:200px" />
        <select id="cl-invite-role" style="padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;font-family:inherit;cursor:pointer">
          <option value="member">Member</option><option value="admin">Admin</option><option value="viewer">Viewer</option>
        </select>
        <button onClick=${createInvite} style="padding:8px 18px;font-size:13px">Create Invite</button>
      </div>
    </div>

    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden">
      <div style="padding:16px 20px;border-bottom:1px solid var(--border)">
        <h3 style="margin:0;font-size:15px">Active invites</h3>
      </div>
      <table class="table">
        <thead><tr><th>Email</th><th>Role</th><th>Status</th><th style="width:80px"></th></tr></thead>
        <tbody>${invites.length ? invites.map(inv => html`<tr key=${inv.id}>
          <td style="font-weight:500">${inv.email}</td>
          <td style="text-transform:capitalize">${inv.role}</td>
          <td><span style="
            font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;
            ${inv.status === 'pending' ? 'background:#FEFCE8;color:#A16207' : 'background:#F1F5F9;color:#64748B'}
          ">${inv.status}</span></td>
          <td>${inv.status === 'pending' ? html`<button class="alt" onClick=${() => revokeInvite(inv.id)} style="padding:4px 10px;font-size:12px">Revoke</button>` : null}</td>
        </tr>`) : html`<tr><td colspan="4" style="text-align:center;padding:32px">
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="var(--ink-muted)" stroke-width="1" style="margin-bottom:8px;opacity:0.4"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>
          <div class="muted">No invites yet. Send an invite to get started.</div>
        </td></tr>`}</tbody>
      </table>
    </div>
  `;
}
