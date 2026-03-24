import { h } from 'preact';
import htm from 'htm';
import { hasCapability, useAction } from '../route-helpers.js';

const html = htm.bind(h);

export default function CompanyPage({ bootstrap, api, toast, orgId, onRefresh }) {
  const org = bootstrap?.organization || {};
  const canManageCompany = hasCapability(bootstrap, 'manage_company');
  const [saveOrg, saving] = useAction(async () => {
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
    toast('Company details saved.');
    onRefresh();
  });

  return html`
    <div class=${`secondary-banner ${canManageCompany ? '' : 'warning'}`}>
      <div class="secondary-banner-copy">
        <h3>${canManageCompany ? 'Keep the workspace record current' : 'Workspace record is visible here'}</h3>
        <p class="muted">${canManageCompany ? 'These settings tell Clearledgr which company and workspace this inbox belongs to.' : 'You can review company details here, but only admins can change workspace settings.'}</p>
      </div>
      <div class="secondary-banner-actions">
        <button class="btn-primary" onClick=${saveOrg} disabled=${saving || !canManageCompany}>${saving ? 'Saving…' : 'Save company details'}</button>
      </div>
    </div>

    <div class="secondary-shell">
      <div class="secondary-main">
        <div class="panel">
          <h3 style="margin-top:0">Company details</h3>
          <div style="display:flex;flex-direction:column;gap:16px;margin-top:8px">
            <div><label>Company name</label><input id="cl-org-name" value=${org.name || ''} placeholder="Your company name" disabled=${!canManageCompany} /></div>
            <div><label>Domain</label><input id="cl-org-domain" value=${org.domain || ''} placeholder="company.com" disabled=${!canManageCompany} /></div>
            <div><label>Integration mode</label>
              <select id="cl-org-mode" disabled=${!canManageCompany}>
                <option value="shared" selected=${org.integration_mode === 'shared'}>Shared workspace</option>
                <option value="per_org" selected=${org.integration_mode === 'per_org'}>Per organization</option>
              </select>
            </div>
          </div>
        </div>
      </div>

      <div class="secondary-side">
        <div class="panel">
          <h3 style="margin-top:0">Current workspace record</h3>
          <div class="secondary-stat-grid" style="margin-top:12px">
            <div class="secondary-stat-card">
              <strong>Organization ID</strong>
              <span>${org.id || orgId || '—'}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Domain</strong>
              <span>${org.domain || 'Not set'}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Mode</strong>
              <span>${org.integration_mode === 'per_org' ? 'Per organization' : 'Shared workspace'}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}
