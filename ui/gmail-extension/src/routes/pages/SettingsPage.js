import { h } from 'preact';
import { useRef, useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { hasCapability, useAction } from '../route-helpers.js';

const html = htm.bind(h);

function formatDisplayDate(value) {
  if (!value) return 'Not set';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Not set';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

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

export default function SettingsPage({ bootstrap, api, toast, orgId, onRefresh, routeId, navigate }) {
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
  const approvalRef = useRef(null);

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

  // --- Approval Rules state ---
  const [approvalRules, setApprovalRules] = useState([]);
  const [showAddRule, setShowAddRule] = useState(false);

  useEffect(() => {
    if (!orgId) return;
    let cancelled = false;
    api(`/settings/${encodeURIComponent(orgId)}`)
      .then((res) => {
        if (!cancelled && Array.isArray(res?.approval_thresholds)) {
          setApprovalRules(res.approval_thresholds);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [orgId]);

  const [saveApprovalRules, savingApprovalRules] = useAction(async (rules) => {
    if (!canManageCompany) return;
    await api(`/settings/${encodeURIComponent(orgId)}/approval-thresholds`, {
      method: 'PUT',
      body: JSON.stringify({ approval_thresholds: rules }),
    });
    toast?.('Approval rules saved.', 'success');
  });

  const resetFieldBorder = (e) => { e.target.style.borderColor = ''; };

  const addApprovalRule = async () => {
    const channel = document.getElementById('cl-rule-channel')?.value?.trim();
    if (!channel) {
      document.getElementById('cl-rule-channel')?.style?.setProperty('border-color', '#DC2626');
      toast?.('Approver channel is required.', 'error');
      return;
    }
    const minAmt = parseFloat(document.getElementById('cl-rule-min')?.value || '0');
    const maxRaw = document.getElementById('cl-rule-max')?.value?.trim();
    if (maxRaw && parseFloat(maxRaw) <= minAmt) {
      document.getElementById('cl-rule-max')?.style?.setProperty('border-color', '#DC2626');
      toast?.('Max amount must be greater than min amount.', 'error');
      return;
    }
    const min = minAmt;
    const max = parseFloat(maxRaw) || 0;
    const approvers = (document.getElementById('cl-rule-approvers')?.value || '').split(',').map((s) => s.trim()).filter(Boolean);
    const glCodes = (document.getElementById('cl-rule-gl')?.value || '').split(',').map((s) => s.trim()).filter(Boolean);
    const departments = (document.getElementById('cl-rule-depts')?.value || '').split(',').map((s) => s.trim()).filter(Boolean);
    const vendors = (document.getElementById('cl-rule-vendors')?.value || '').split(',').map((s) => s.trim()).filter(Boolean);
    const approvalType = document.getElementById('cl-rule-type')?.value || 'any';

    if (!approvers.length) {
      toast?.('Add at least one approver email.', 'error');
      return;
    }

    const newRule = { min_amount: min, max_amount: max, approver_channel: channel, approvers, gl_codes: glCodes, departments, vendors, approval_type: approvalType };
    const updated = [...approvalRules, newRule];
    setApprovalRules(updated);
    setShowAddRule(false);
    await saveApprovalRules(updated);
  };

  const deleteApprovalRule = async (index) => {
    const updated = approvalRules.filter((_, i) => i !== index);
    setApprovalRules(updated);
    await saveApprovalRules(updated);
  };

  const activeAlias = String(routeId || '').trim();
  const billingPreview = [
    { label: 'Plan', value: planName },
    { label: 'Status', value: sub.status || 'Active' },
    {
      label: 'Billing cycle',
      value: String(sub.billing_cycle || 'monthly').toLowerCase() === 'yearly' ? 'Annual' : 'Monthly',
    },
    {
      label: sub.status === 'trialing' ? 'Trial ends' : 'Current period',
      value: formatDisplayDate(sub.status === 'trialing' ? sub.trial_ends_at : sub.current_period_end),
    },
  ];
  const billingUsagePreview = [
    { label: 'Invoices', value: Number(usage.invoices_this_month || 0).toLocaleString() },
    { label: 'AI credits', value: Number(usage.ai_credits_this_month || 0).toLocaleString() },
    { label: 'Users', value: Number(usage.users_count || 0).toLocaleString() },
  ];

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
        <button
          class=${`segmented-button btn-sm ${activeAlias === 'clearledgr/approvals' ? 'is-active' : ''}`}
          onClick=${() => scrollToSection(approvalRef)}
        >Approvals</button>
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
            <h3 style="margin-top:0">Team${!canManageTeam ? html`<span class="status-badge" style="font-size:10px;margin-left:8px">Read-only</span>` : null}</h3>
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
            <h3 style="margin-top:0">Workspace${!canManageCompany ? html`<span class="status-badge" style="font-size:10px;margin-left:8px">Read-only</span>` : null}</h3>
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
            <h3 style="margin-top:0">Billing${!canManagePlan ? html`<span class="status-badge" style="font-size:10px;margin-left:8px">Read-only</span>` : null}</h3>
            <p class="muted" style="margin:0">See the current billing state here, then open the dedicated billing page for plan comparison and usage detail.</p>
          </div>
          ${navigate
            ? html`<div class="row-actions">
                <button class="btn-primary btn-sm" onClick=${() => navigate('clearledgr/plan')}>Open billing page</button>
              </div>`
            : null}
        </div>
        <div class="settings-section-grid">
          <div>
            <div class="settings-summary-grid">
              ${billingPreview.map((entry) => html`
                <div class="settings-summary-card" key=${entry.label}>
                  <strong>${entry.label}</strong>
                  <span>${entry.value}</span>
                </div>
              `)}
            </div>
            <div class="secondary-note">
              Billing changes now live on the dedicated page so subscription details, limits, and plan choices are not squeezed into the general settings view.
            </div>
          </div>
          <div>
            ${usageKeys.length
              ? html`<div class="secondary-stat-grid">
                  ${billingUsagePreview.map((entry) => html`
                    <div class="secondary-stat-card" key=${entry.label}>
                      <strong>${entry.label}</strong>
                      <span>${entry.value}</span>
                    </div>
                  `)}
                </div>`
              : html`<p class="secondary-empty">Usage data will appear here once invoices are processed.</p>`}
          </div>
        </div>
      </div>

      <div class="panel" ref=${approvalRef}>
        <div class="panel-head compact">
          <div>
            <h3 style="margin-top:0">Approval rules${!canManageCompany ? html`<span class="status-badge" style="font-size:10px;margin-left:8px">Read-only</span>` : null}</h3>
            <p class="muted" style="margin:0">Define who approves invoices based on amount, GL code, department, or vendor.</p>
          </div>
          <div class="row-actions">
            ${canManageCompany ? html`
              <button class="btn-primary" onClick=${() => setShowAddRule(!showAddRule)} disabled=${savingApprovalRules}>
                ${showAddRule ? 'Cancel' : 'Add rule'}
              </button>
            ` : null}
          </div>
        </div>

        ${showAddRule && canManageCompany ? html`
          <div style="padding:16px 0;border-bottom:1px solid var(--cl-border, #e2e8f0)">
            <div class="secondary-form-stack">
              <div class="secondary-form-grid" style="gap:12px">
                <div><label>Min amount</label><input id="cl-rule-min" type="number" placeholder="0" step="0.01" onFocus=${resetFieldBorder} /></div>
                <div><label>Max amount</label><input id="cl-rule-max" type="number" placeholder="10000" step="0.01" onFocus=${resetFieldBorder} /></div>
              </div>
              <div class="secondary-form-grid" style="gap:12px">
                <div>
                  <label>Channel</label>
                  <select id="cl-rule-channel" onFocus=${resetFieldBorder}>
                    <option value="slack">Slack</option>
                    <option value="teams">Teams</option>
                    <option value="email">Email</option>
                  </select>
                </div>
                <div>
                  <label>Approval type</label>
                  <select id="cl-rule-type">
                    <option value="any">Any approver</option>
                    <option value="all">All approvers</option>
                  </select>
                </div>
              </div>
              <div><label>Approvers</label><input id="cl-rule-approvers" placeholder="alice@co.com, bob@co.com" /></div>
              <div><label>GL codes</label><input id="cl-rule-gl" placeholder="6000, 6100 (optional)" /></div>
              <div><label>Departments</label><input id="cl-rule-depts" placeholder="engineering, marketing (optional)" /></div>
              <div><label>Vendors</label><input id="cl-rule-vendors" placeholder="Acme Corp, Widgets Inc (optional)" /></div>
            </div>
            <div class="row-actions" style="justify-content:flex-start;margin-top:14px">
              <button class="btn-primary" onClick=${addApprovalRule} disabled=${savingApprovalRules}>
                ${savingApprovalRules ? 'Saving...' : 'Save rule'}
              </button>
            </div>
          </div>
        ` : null}

        <div style="margin-top:18px">
          ${approvalRules.length
            ? html`<div class="secondary-list">
                ${approvalRules.map((rule, idx) => html`
                  <div class="secondary-row" key=${idx}>
                    <div class="secondary-row-copy">
                      <div class="secondary-inline-actions" style="margin-bottom:4px">
                        <strong style="font-size:14px;margin-right:2px">
                          $${Number(rule.min_amount || 0).toLocaleString()} – $${rule.max_amount ? Number(rule.max_amount).toLocaleString() : 'No limit'}
                        </strong>
                        <span class="status-badge">${rule.approver_channel || 'slack'}</span>
                        <span class="status-badge connected">${rule.approval_type === 'all' ? 'All must approve' : 'Any can approve'}</span>
                      </div>
                      <div class="muted" style="font-size:12px">
                        Approvers: ${(rule.approvers || []).join(', ') || 'None'}
                      </div>
                      ${(rule.gl_codes || []).length ? html`<div class="muted" style="font-size:12px">GL codes: ${rule.gl_codes.join(', ')}</div>` : null}
                      ${(rule.departments || []).length ? html`<div class="muted" style="font-size:12px">Departments: ${rule.departments.join(', ')}</div>` : null}
                      ${(rule.vendors || []).length ? html`<div class="muted" style="font-size:12px">Vendors: ${rule.vendors.join(', ')}</div>` : null}
                    </div>
                    ${canManageCompany ? html`
                      <button class="btn-danger btn-sm" onClick=${() => deleteApprovalRule(idx)} disabled=${savingApprovalRules}>Delete</button>
                    ` : null}
                  </div>
                `)}
              </div>`
            : html`<div class="secondary-empty">No approval rules yet. Add one to route invoices for review based on amount or category.</div>`}
        </div>

        <div class="secondary-note" style="margin-top:14px">
          Rules are evaluated in order. The first rule whose amount range, GL codes, departments, and vendors match the invoice will be used to route the approval request.
        </div>
      </div>
    </div>
  `;
}
