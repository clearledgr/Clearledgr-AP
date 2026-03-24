import { h } from 'preact';
import htm from 'htm';
import { hasCapability, useAction } from '../route-helpers.js';

const html = htm.bind(h);

function InviteRow({ invite, onRevoke, canManage }) {
  return html`<div class="secondary-row">
    <div class="secondary-row-copy">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
        <strong style="font-size:14px">${invite.email}</strong>
        <span class="status-badge ${invite.status === 'pending' ? '' : 'connected'}">${invite.status || 'pending'}</span>
      </div>
      <div class="muted" style="font-size:12px">Role: ${(invite.role || 'member') === 'member' ? 'Operator' : invite.role === 'viewer' ? 'Read-only' : 'Admin'}</div>
    </div>
    ${invite.status === 'pending'
      ? html`<button class="btn-danger btn-sm" onClick=${() => onRevoke(invite.id)} disabled=${!canManage}>Revoke</button>`
      : null}
  </div>`;
}

export default function TeamPage({ bootstrap, api, toast, orgId, onRefresh }) {
  const invites = bootstrap?.teamInvites || [];
  const canManageTeam = hasCapability(bootstrap, 'manage_team');
  const [createInvite] = useAction(async () => {
    if (!canManageTeam) return;
    const email = document.getElementById('cl-invite-email')?.value?.trim();
    const role = document.getElementById('cl-invite-role')?.value;
    await api('/api/workspace/team/invites', { method: 'POST', body: JSON.stringify({ organization_id: orgId, email, role }) });
    toast(`Invite sent to ${email}.`); onRefresh();
  });
  const [revokeInvite] = useAction(async (id) => {
    if (!canManageTeam) return;
    await api(`/api/workspace/team/invites/${id}/revoke?organization_id=${encodeURIComponent(orgId)}`, { method: 'POST' });
    toast('Invite revoked.'); onRefresh();
  });

  return html`
    <div class=${`secondary-banner ${canManageTeam ? '' : 'warning'}`}>
      <div class="secondary-banner-copy">
        <h3>${canManageTeam ? 'Manage workspace access' : 'Workspace access is visible here'}</h3>
        <p class="muted">${canManageTeam ? 'Invite operators, admins, and read-only teammates. Approvers can still work from Slack or Teams without using Gmail every day.' : 'You can see who has access, but only admins can invite or revoke workspace users.'}</p>
      </div>
    </div>

    <div class="secondary-shell">
      <div class="secondary-main">
        <div class="panel">
          <h3 style="margin-top:0">Invite someone</h3>
          <p class="muted" style="margin:0 0 14px">Add a teammate to this workspace and pick the level of access they need.</p>
          <div class="secondary-form-grid">
            <input id="cl-invite-email" placeholder="teammate@company.com" disabled=${!canManageTeam} />
            <select id="cl-invite-role" disabled=${!canManageTeam}>
              <option value="member">Operator</option>
              <option value="admin">Admin</option>
              <option value="viewer">Read-only</option>
            </select>
          </div>
          <div class="row" style="margin-top:14px">
            <button class="btn-primary" onClick=${createInvite} disabled=${!canManageTeam}>Send invite</button>
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Pending invites</h3>
          <p class="muted" style="margin-top:0">Keep this list current so it is clear who still needs access.</p>
          ${invites.length
            ? html`<div class="secondary-list">
                ${invites.map((invite) => html`<${InviteRow} key=${invite.id} invite=${invite} onRevoke=${revokeInvite} canManage=${canManageTeam} />`)}
              </div>`
            : html`<div class="secondary-empty">No invites yet. Send one when someone needs access.</div>`}
        </div>
      </div>

      <div class="secondary-side">
        <div class="panel">
          <h3 style="margin-top:0">Role guide</h3>
          <div class="secondary-list" style="margin-top:12px">
            <div class="secondary-row">
              <div class="secondary-row-copy">
                <strong>Operator</strong>
                <p>Reviews invoices and takes action in the queue.</p>
              </div>
            </div>
            <div class="secondary-row">
              <div class="secondary-row-copy">
                <strong>Admin</strong>
                <p>Manages setup, rules, and workspace settings.</p>
              </div>
            </div>
            <div class="secondary-row">
              <div class="secondary-row-copy">
                <strong>Read-only</strong>
                <p>Can view records without making changes.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}
