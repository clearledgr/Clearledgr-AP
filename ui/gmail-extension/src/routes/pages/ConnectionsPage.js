/**
 * Connections Page — integration status and setup.
 * Polished with proper status indicators and card layout.
 */
import { h } from 'preact';
import htm from 'htm';
import { integrationByName, humanizeStatus, humanizeMode, useAction } from '../route-helpers.js';

const html = htm.bind(h);

function StatusDot({ connected }) {
  return html`<div style="
    width:8px;height:8px;border-radius:50%;
    background:${connected ? '#10B981' : '#94A3B8'};
  "></div>`;
}

export default function ConnectionsPage({ bootstrap, api, toast, orgId, onRefresh, oauthBridge }) {
  const integrations = bootstrap?.integrations || [];
  const slack = integrationByName(bootstrap, 'slack');
  const teams = integrationByName(bootstrap, 'teams');

  const [connectSlack] = useAction(async () => {
    const p = await api('/api/admin/integrations/slack/install/start', { method: 'POST', body: JSON.stringify({ organization_id: orgId, mode: 'per_org', redirect_path: '/console' }) });
    oauthBridge.startOAuth(p.auth_url, 'slack');
  });
  const [saveChannel] = useAction(async () => {
    await api('/api/admin/integrations/slack/channel', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: document.getElementById('cl-slack-channel')?.value?.trim() }) });
    toast('Channel saved.'); onRefresh();
  });
  const [testSlackMsg] = useAction(async () => {
    await api('/api/admin/integrations/slack/test', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: document.getElementById('cl-slack-channel')?.value?.trim() }) });
    toast('Test sent to Slack.');
  });
  const [saveWebhook] = useAction(async () => {
    const wh = document.getElementById('cl-teams-webhook')?.value?.trim();
    if (!wh) { toast('Webhook URL required.', 'error'); return; }
    await api('/api/admin/integrations/teams/webhook', { method: 'POST', body: JSON.stringify({ organization_id: orgId, webhook_url: wh }) });
    toast('Teams webhook saved.'); onRefresh();
  });
  const [testTeamsMsg] = useAction(async () => {
    await api('/api/admin/integrations/teams/test', { method: 'POST', body: JSON.stringify({ organization_id: orgId }) });
    toast('Test sent to Teams.');
  });

  return html`
    ${/* Status Overview */''}
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden;margin-bottom:20px">
      <table class="table">
        <thead><tr>
          <th style="width:30px"></th>
          <th>Integration</th>
          <th>Status</th>
          <th>Mode</th>
          <th>Last sync</th>
        </tr></thead>
        <tbody>
          ${integrations.length === 0
            ? html`<tr><td colspan="5" class="muted" style="text-align:center;padding:24px">No integrations configured yet.</td></tr>`
            : integrations.map(i => html`<tr key=${i.name}>
              <td><${StatusDot} connected=${i.status === 'connected'} /></td>
              <td style="font-weight:500;text-transform:capitalize">${i.name}</td>
              <td>
                <span style="
                  font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;
                  ${i.status === 'connected'
                    ? 'background:#ECFDF5;color:#059669'
                    : 'background:#F1F5F9;color:#64748B'
                  }
                ">${humanizeStatus(i.status || 'unknown')}</span>
              </td>
              <td style="color:var(--ink-secondary)">${humanizeMode(i.mode || '-')}</td>
              <td style="color:var(--ink-muted);font-size:13px">${i.last_sync_at || '\u2014'}</td>
            </tr>`)
          }
        </tbody>
      </table>
    </div>

    ${/* Slack Setup */''}
    <div class="panel">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" stroke-width="1.5"><path d="M14.5 2c-1.1 0-2 .9-2 2v4c0 1.1.9 2 2 2h4c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2h-4Z"/></svg>
        <h3 style="margin:0">Slack Setup</h3>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <button onClick=${connectSlack} style="padding:8px 18px;font-size:13px">Install to Slack</button>
        <input id="cl-slack-channel" placeholder="#finance-approvals" value=${slack.approval_channel || ''} style="flex:1;min-width:160px" />
        <button class="alt" onClick=${saveChannel} style="padding:8px 14px;font-size:13px">Save Channel</button>
        <button class="alt" onClick=${testSlackMsg} style="padding:8px 14px;font-size:13px">Send Test</button>
      </div>
    </div>

    ${/* Teams Setup */''}
    <div class="panel">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--ink)" stroke-width="1.5"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
        <h3 style="margin:0">Teams Setup</h3>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <input id="cl-teams-webhook" placeholder="https://.../incomingwebhook/..." value=${teams.webhook_url || ''} style="flex:1;min-width:200px" />
        <button class="alt" onClick=${saveWebhook} style="padding:8px 14px;font-size:13px">Save Webhook</button>
        <button class="alt" onClick=${testTeamsMsg} disabled=${!teams.connected} style="padding:8px 14px;font-size:13px">Send Test</button>
      </div>
    </div>
  `;
}
