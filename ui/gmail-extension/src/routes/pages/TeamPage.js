import { h } from 'preact';
import htm from 'htm';
import { useAction } from '../route-helpers.js';

const html = htm.bind(h);

function InviteRow({ invite, onRevoke }) {
  return html`<div style="
    display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;
    padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);
  ">
    <div>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
        <strong style="font-size:14px">${invite.email}</strong>
        <span class="status-badge ${invite.status === 'pending' ? '' : 'connected'}">${invite.status || 'pending'}</span>
      </div>
      <div class="muted" style="font-size:12px">Role: ${(invite.role || 'member') === 'member' ? 'Operator' : invite.role === 'viewer' ? 'Read-only' : 'Admin'}</div>
    </div>
    ${invite.status === 'pending'
      ? html`<button class="alt" onClick=${() => onRevoke(invite.id)} style="padding:8px 12px;font-size:12px">Revoke</button>`
      : null}
  </div>`;
}

export default function TeamPage({ bootstrap, api, toast, orgId, onRefresh }) {
  const invites = bootstrap?.teamInvites || [];
  const [createInvite] = useAction(async () => {
    const email = document.getElementById('cl-invite-email')?.value?.trim();
    const role = document.getElementById('cl-invite-role')?.value;
    await api('/api/workspace/team/invites', { method: 'POST', body: JSON.stringify({ organization_id: orgId, email, role }) });
    toast(`Invite sent to ${email}.`); onRefresh();
  });
  const [revokeInvite] = useAction(async (id) => {
    await api(`/api/workspace/team/invites/${id}/revoke?organization_id=${encodeURIComponent(orgId)}`, { method: 'POST' });
    toast('Invite revoked.'); onRefresh();
  });

  return html`
    <div class="panel">
      <h3 style="margin:0 0 6px">Team access belongs here, not in the daily queue</h3>
      <p class="muted" style="margin:0 0 14px">Use this page for workspace access only. Approvers can still act from Slack or Teams without living in Gmail every day.</p>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <input id="cl-invite-email" placeholder="teammate@company.com" style="flex:1;min-width:200px" />
        <select id="cl-invite-role" style="padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;font-family:inherit;cursor:pointer">
          <option value="member">Operator</option>
          <option value="admin">Admin</option>
          <option value="viewer">Read-only</option>
        </select>
        <button onClick=${createInvite} style="padding:8px 18px;font-size:13px">Send invite</button>
      </div>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">Role guide</h3>
      <div style="display:grid;gap:10px">
        <div class="readiness-item"><strong>Operator:</strong> works the Pipeline and Gmail thread surface.</div>
        <div class="readiness-item"><strong>Admin:</strong> manages setup, rules, and workspace settings.</div>
        <div class="readiness-item"><strong>Read-only:</strong> can review records without mutating AP workflow state.</div>
      </div>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">Pending invites</h3>
      <p class="muted" style="margin-top:0">Keep this list short and current so ownership stays clear.</p>
      ${invites.length
        ? html`<div style="display:grid;gap:10px">
            ${invites.map((invite) => html`<${InviteRow} key=${invite.id} invite=${invite} onRevoke=${revokeInvite} />`)}
          </div>`
        : html`<div class="muted">No invites yet. Send an invite when someone needs Gmail access to the shared AP record.</div>`}
    </div>
  `;
}
