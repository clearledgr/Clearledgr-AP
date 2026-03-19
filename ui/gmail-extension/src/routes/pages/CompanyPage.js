import { h } from 'preact';
import htm from 'htm';
import { useAction } from '../route-helpers.js';

const html = htm.bind(h);

export default function CompanyPage({ bootstrap, api, toast, orgId, onRefresh }) {
  const org = bootstrap?.organization || {};
  const [saveOrg, saving] = useAction(async () => {
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
    <div class="panel">
      <h3>Workspace identity only</h3>
      <p class="muted" style="margin-top:0">Keep the company record current here, but leave deeper organization administration outside Gmail.</p>
      <div style="display:flex;flex-direction:column;gap:16px;margin-top:8px">
        <div><label>Company name</label><input id="cl-org-name" value=${org.name || ''} placeholder="Your company name" /></div>
        <div><label>Domain</label><input id="cl-org-domain" value=${org.domain || ''} placeholder="company.com" /></div>
        <div><label>Integration mode</label>
          <select id="cl-org-mode">
            <option value="shared" selected=${org.integration_mode === 'shared'}>Shared workspace</option>
            <option value="per_org" selected=${org.integration_mode === 'per_org'}>Per organization</option>
          </select>
        </div>
      </div>
      <div class="row" style="margin-top:20px"><button onClick=${saveOrg} disabled=${saving}>${saving ? 'Saving…' : 'Save'}</button></div>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">Current workspace record</h3>
      <p class="muted" style="margin-top:0">The fields below shape how Clearledgr identifies this AP workspace across Gmail, approvals, and ERP.</p>
      <div class="readiness-list" style="margin-top:12px">
        <div class="readiness-item"><strong>Organization ID:</strong> ${org.id || orgId || '—'}</div>
        <div class="readiness-item"><strong>Domain:</strong> ${org.domain || 'Not set'}</div>
        <div class="readiness-item"><strong>Mode:</strong> ${org.integration_mode === 'per_org' ? 'Per organization' : 'Shared workspace'}</div>
      </div>
    </div>
  `;
}
