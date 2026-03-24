import { h } from 'preact';
import { useRef } from 'preact/hooks';
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

export default function SettingsPage({ bootstrap, api, toast, orgId, onRefresh, routeId }) {
  const invites = bootstrap?.teamInvites || [];
  const org = bootstrap?.organization || {};
  const sub = bootstrap?.subscription || {};
  const usage = sub.usage || {};
  const usageKeys = Object.keys(usage);
  const planName = (sub.plan || 'free').charAt(0).toUpperCase() + (sub.plan || 'free').slice(1);

  const canManageTeam = hasCapability(bootstrap, 'manage_team');
  const canManageCompany = hasCapability(bootstrap, 'manage_company');
  const canManagePlan = hasCapability(bootstrap, 'manage_plan');
  const canManageAny = canManageTeam || canManageCompany || canManagePlan;

  const teamRef = useRef(null);
  const workspaceRef = useRef(null);
  const billingRef = useRef(null);

  const scrollToSection = (ref) => {
    try {
      ref?.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    } catch {
      ref?.current?.scrollIntoView?.();
    }
  };

  const [createInvite, creatingInvite] = useAction(async () => {
    if (!canManageTeam) return;
    const email = document.getElementById('cl-invite-email')?.value?.trim();
    const role = document.getElementById('cl-invite-role')?.value;
    if (!email) {
      toast?.('Enter an email before sending the invite.', 'error');
      return;
    }
    await api('/api/workspace/team/invites', {
      method: 'POST',
      body: JSON.stringify({ organization_id: orgId, email, role }),
    });
    toast?.(`Invite sent to ${email}.`, 'success');
    onRefresh?.();
  });

  const [revokeInvite, revokingInvite] = useAction(async (id) => {
    if (!canManageTeam) return;
    await api(`/api/workspace/team/invites/${id}/revoke?organization_id=${encodeURIComponent(orgId)}`, { method: 'POST' });
    toast?.('Invite revoked.', 'success');
    onRefresh?.();
  });

  const [saveOrg, savingOrg] = useAction(async () => {
    if (!canManageCompany) return;
    await api('/api/workspace/org/settings', {
      method: 'PATCH',
      body: JSON.stringify({
        organization_id: orgId,
        patch: {
          organization_name: document.getElementById('cl-org-name')?.value?.trim(),
          domain: document.getElementById('cl-org-domain')?.value?.trim(),
          integration_mode: document.getElementById('cl-org-mode')?.value,
        },
      }),
    });
    toast?.('Workspace details saved.', 'success');
    onRefresh?.();
  });

  const [changePlan, changingPlan] = useAction(async (plan) => {
    if (!canManagePlan) return;
    await api('/api/workspace/subscription/plan', {
      method: 'PATCH',
      body: JSON.stringify({ organization_id: orgId, plan }),
    });
    toast?.(`Plan updated to ${plan}.`, 'success');
    onRefresh?.();
  });

  const activeAlias = String(routeId || '').trim();

  return html`
    <div class=${`secondary-banner ${canManageAny ? '' : 'warning'}`}>
      <div class="secondary-banner-copy">
        <h3>${canManageAny ? 'Manage workspace settings' : 'Workspace settings'}</h3>
        <p class="muted">
          Team access, workspace details, and billing live here now.
          ${canManageAny
            ? ' Use the sections below to keep the workspace current.'
            : ' You can review these settings here, but only admins can make changes.'}
        </p>
      </div>
      <div class="secondary-banner-actions">
        <button
          class=${`segmented-button btn-sm ${activeAlias === 'clearledgr/team' ? 'is-active' : ''}`}
          onClick=${() => scrollToSection(teamRef)}
        >Team</button>
        <button
          class=${`segmented-button btn-sm ${activeAlias === 'clearledgr/company' ? 'is-active' : ''}`}
          onClick=${() => scrollToSection(workspaceRef)}
        >Workspace</button>
        <button
          class=${`segmented-button btn-sm ${activeAlias === 'clearledgr/plan' ? 'is-active' : ''}`}
          onClick=${() => scrollToSection(billingRef)}
        >Billing</button>
      </div>
    </div>

    <div class="settings-summary-grid" style="margin-bottom:20px">
      <div class="settings-summary-card">
        <strong>Pending invites</strong>
        <span>${Number(invites.filter((invite) => invite.status === 'pending').length).toLocaleString()} waiting for a response.</span>
      </div>
      <div class="settings-summary-card">
        <strong>Workspace</strong>
        <span>${org.domain || 'Domain not set'} · ${org.integration_mode === 'per_org' ? 'Per organization' : 'Shared workspace'}</span>
      </div>
      <div class="settings-summary-card">
        <strong>Plan</strong>
        <span>${planName} · ${sub.status || 'Active'}</span>
      </div>
      <div class="settings-summary-card">
        <strong>Access model</strong>
        <span>Admins manage setup. Operators work the queue. Read-only teammates can follow records without making changes.</span>
      </div>
    </div>

    <div class="secondary-main">
      <div class="panel" ref=${teamRef}>
        <div class="panel-head compact">
          <div>
            <h3 style="margin-top:0">Team</h3>
            <p class="muted" style="margin:0">Invite the people who need to work or monitor finance operations.</p>
          </div>
        </div>
        <div class="settings-section-grid">
          <div>
            <div class="secondary-form-grid">
              <input id="cl-invite-email" placeholder="teammate@company.com" disabled=${!canManageTeam} />
              <select id="cl-invite-role" disabled=${!canManageTeam}>
                <option value="member">Operator</option>
                <option value="admin">Admin</option>
                <option value="viewer">Read-only</option>
              </select>
            </div>
            <div class="row-actions" style="justify-content:flex-start;margin-top:14px">
              <button class="btn-primary" onClick=${createInvite} disabled=${!canManageTeam || creatingInvite}>
                ${creatingInvite ? 'Sending…' : 'Send invite'}
              </button>
            </div>
          </div>
          <div class="secondary-note">
            Operators review invoices and move work forward. Admins manage setup, rules, and workspace details. Read-only teammates can follow activity without changing records.
          </div>
        </div>
        <div style="margin-top:18px">
          ${invites.length
            ? html`<div class="secondary-list">
                ${invites.map((invite) => html`<${InviteRow} key=${invite.id} invite=${invite} onRevoke=${revokeInvite} canManage=${canManageTeam && !revokingInvite} />`)}
              </div>`
            : html`<div class="secondary-empty">No invites yet. Send one when someone needs access.</div>`}
        </div>
      </div>

      <div class="panel" ref=${workspaceRef}>
        <div class="panel-head compact">
          <div>
            <h3 style="margin-top:0">Workspace</h3>
            <p class="muted" style="margin:0">Keep the company record and inbox setup current.</p>
          </div>
          <div class="row-actions">
            <button class="btn-primary" onClick=${saveOrg} disabled=${savingOrg || !canManageCompany}>
              ${savingOrg ? 'Saving…' : 'Save workspace'}
            </button>
          </div>
        </div>
        <div class="settings-section-grid">
          <div style="display:flex;flex-direction:column;gap:16px">
            <div><label>Company name</label><input id="cl-org-name" value=${org.name || ''} placeholder="Your company name" disabled=${!canManageCompany} /></div>
            <div><label>Domain</label><input id="cl-org-domain" value=${org.domain || ''} placeholder="company.com" disabled=${!canManageCompany} /></div>
            <div><label>Integration mode</label>
              <select id="cl-org-mode" disabled=${!canManageCompany}>
                <option value="shared" selected=${org.integration_mode === 'shared'}>Shared workspace</option>
                <option value="per_org" selected=${org.integration_mode === 'per_org'}>Per organization</option>
              </select>
            </div>
          </div>
          <div class="settings-summary-grid">
            <div class="settings-summary-card">
              <strong>Organization ID</strong>
              <span>${org.id || orgId || '—'}</span>
            </div>
            <div class="settings-summary-card">
              <strong>Domain</strong>
              <span>${org.domain || 'Not set'}</span>
            </div>
            <div class="settings-summary-card">
              <strong>Mode</strong>
              <span>${org.integration_mode === 'per_org' ? 'Per organization' : 'Shared workspace'}</span>
            </div>
            <div class="settings-summary-card">
              <strong>Current plan</strong>
              <span>${planName}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="panel" ref=${billingRef}>
        <div class="panel-head compact">
          <div>
            <h3 style="margin-top:0">Billing</h3>
            <p class="muted" style="margin:0">Review the current plan and workspace usage for this billing period.</p>
          </div>
        </div>
        <div class="settings-section-grid">
          <div>
            <div style="display:flex;align-items:center;gap:12px;margin:4px 0 16px;flex-wrap:wrap">
              <span style="font-size:28px;font-weight:700;letter-spacing:-0.02em">${planName}</span>
              <span class="status-badge connected">${sub.status || 'Active'}</span>
            </div>
            <div class="segmented-actions" style="margin-bottom:18px">
              ${['free', 'trial', 'pro', 'enterprise'].map((p) => html`
                <button
                  class=${`segmented-button btn-sm ${sub.plan === p ? 'is-active' : ''}`}
                  onClick=${() => changePlan(p)}
                  disabled=${sub.plan === p || !canManagePlan || changingPlan}
                >
                  ${p.charAt(0).toUpperCase() + p.slice(1)}
                </button>
              `)}
            </div>
            <div class="secondary-note">
              Use the plan controls here if pricing, limits, or support needs have changed. Billing is kept together with team and workspace because it is part of the same admin setup surface.
            </div>
          </div>
          <div>
            ${usageKeys.length
              ? html`<div class="secondary-stat-grid">
                  ${usageKeys.map((key) => html`
                    <div class="secondary-stat-card" key=${key}>
                      <strong>${key.replace(/_/g, ' ')}</strong>
                      <span>${typeof usage[key] === 'number' ? usage[key].toLocaleString() : usage[key]}</span>
                    </div>
                  `)}
                </div>`
              : html`<p class="secondary-empty">Usage data will appear here once invoices are processed.</p>`}
          </div>
        </div>
      </div>
    </div>
  `;
}
