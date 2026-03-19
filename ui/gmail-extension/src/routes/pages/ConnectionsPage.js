/**
 * Connections Page — occasional setup surface for AP blockers.
 */
import { h } from 'preact';
import htm from 'htm';
import { integrationByName, humanizeStatus, humanizeMode, useAction } from '../route-helpers.js';

const html = htm.bind(h);

function ConnectionRow({ label, status, detail, actionLabel = '', onAction, pending = false }) {
  const connected = String(status || '').trim().toLowerCase() === 'connected';
  return html`<div style="
    display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;
    padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);
  ">
    <div>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
        <strong style="font-size:14px">${label}</strong>
        <span class=${`status-badge ${connected ? 'connected' : ''}`}>${humanizeStatus(status || 'unknown')}</span>
      </div>
      <div class="muted" style="font-size:12px">${detail}</div>
    </div>
    ${actionLabel
      ? html`<button class="alt" onClick=${onAction} disabled=${pending} style="padding:8px 12px;font-size:12px">${pending ? 'Working…' : actionLabel}</button>`
      : null}
  </div>`;
}

function ApprovalSurfaceCard({ title, status, detail, children }) {
  return html`<div class="panel" style="margin-bottom:0">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px">
      <div>
        <h3 style="margin:0 0 4px">${title}</h3>
        <p class="muted" style="margin:0">${detail}</p>
      </div>
      <span class=${`status-badge ${status === 'connected' ? 'connected' : ''}`}>${humanizeStatus(status || 'unknown')}</span>
    </div>
    ${children}
  </div>`;
}

export default function ConnectionsPage({ bootstrap, api, toast, orgId, onRefresh, oauthBridge, navigate }) {
  const gmail = integrationByName(bootstrap, 'gmail');
  const erp = integrationByName(bootstrap, 'erp');
  const slack = integrationByName(bootstrap, 'slack');
  const teams = integrationByName(bootstrap, 'teams');
  const gmailReconnectRequired = Boolean(gmail.connected && (gmail.requires_reconnect || gmail.durable === false));

  const [connectGmail, gmailPending] = useAction(async () => {
    const authUrl = bootstrap?.gmail_auth_url || bootstrap?.integrations?.find?.((it) => it.type === 'gmail')?.auth_url;
    if (authUrl) {
      oauthBridge.startOAuth(authUrl, 'gmail');
      return;
    }
    navigate?.('clearledgr/home');
  });

  const [connectSlack, slackPending] = useAction(async () => {
    const p = await api('/api/workspace/integrations/slack/install/start', { method: 'POST', body: JSON.stringify({ organization_id: orgId, mode: 'per_org', redirect_path: '/workspace' }) });
    oauthBridge.startOAuth(p.auth_url, 'slack');
  });
  const [saveChannel, saveChannelPending] = useAction(async () => {
    await api('/api/workspace/integrations/slack/channel', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: document.getElementById('cl-slack-channel')?.value?.trim() }) });
    toast('Channel saved.'); onRefresh();
  });
  const [testSlackMsg, testSlackPending] = useAction(async () => {
    await api('/api/workspace/integrations/slack/test', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: document.getElementById('cl-slack-channel')?.value?.trim() }) });
    toast('Test sent to Slack.');
  });
  const [saveWebhook, saveWebhookPending] = useAction(async () => {
    const wh = document.getElementById('cl-teams-webhook')?.value?.trim();
    if (!wh) { toast('Webhook URL required.', 'error'); return; }
    await api('/api/workspace/integrations/teams/webhook', { method: 'POST', body: JSON.stringify({ organization_id: orgId, webhook_url: wh }) });
    toast('Teams webhook saved.'); onRefresh();
  });
  const [testTeamsMsg, testTeamsPending] = useAction(async () => {
    await api('/api/workspace/integrations/teams/test', { method: 'POST', body: JSON.stringify({ organization_id: orgId }) });
    toast('Test sent to Teams.');
  });

  return html`
    <div class="panel">
      <h3 style="margin:0 0 6px">Use this page only when setup is blocking AP work</h3>
      <p class="muted" style="margin:0 0 16px">Connections are occasional admin tasks. Operators should spend their time in Pipeline and the thread card, not here.</p>
      <div style="display:grid;gap:10px">
        <${ConnectionRow}
          label="Gmail"
          status=${gmail.status || (gmail.connected ? 'connected' : 'disconnected')}
          detail=${gmail.connected
            ? (gmailReconnectRequired
              ? 'Reconnect Gmail to restore durable background monitoring for this workspace.'
              : 'Gmail monitoring is connected for this workspace.')
            : 'Connect Gmail from the thread prompt or the first-run setup flow.'}
          actionLabel=${gmail.connected ? (gmailReconnectRequired ? 'Reconnect Gmail' : '') : 'Connect Gmail'}
          onAction=${connectGmail}
          pending=${gmailPending}
        />
        <${ConnectionRow}
          label="Slack"
          status=${slack.status || (slack.connected ? 'connected' : 'disconnected')}
          detail=${slack.connected
            ? `Approval routing is ready${slack.approval_channel ? ` in ${slack.approval_channel}` : ''}.`
            : 'Install Slack if approvals should be handled in a Slack channel.'}
          actionLabel=${slack.connected ? '' : 'Install Slack'}
          onAction=${connectSlack}
          pending=${slackPending}
        />
        <${ConnectionRow}
          label="Teams"
          status=${teams.status || (teams.connected ? 'connected' : 'disconnected')}
          detail=${teams.connected
            ? 'Teams approval routing is connected.'
            : 'Save a Teams webhook if approvals should be handled in Teams.'}
        />
        <${ConnectionRow}
          label="ERP"
          status=${erp.status || (erp.connected ? 'connected' : 'disconnected')}
          detail=${erp.connected
            ? `${erp.erp_type || 'ERP'} posting is available.`
            : 'Posting stays blocked until an ERP connector is configured.'}
          actionLabel=${erp.connected ? '' : 'Review status'}
          onAction=${() => navigate?.('clearledgr/health')}
        />
      </div>
    </div>

    <div style="display:grid;gap:16px">
      <${ApprovalSurfaceCard}
        title="Slack approval routing"
        status=${slack.status || (slack.connected ? 'connected' : 'disconnected')}
        detail="Point invoice approvals at the Slack channel operators actually use."
      >
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button onClick=${connectSlack} disabled=${slackPending} style="padding:8px 18px;font-size:13px">${slackPending ? 'Working…' : 'Install to Slack'}</button>
          <input id="cl-slack-channel" placeholder="#finance-approvals" value=${slack.approval_channel || ''} style="flex:1;min-width:160px" />
          <button class="alt" onClick=${saveChannel} disabled=${saveChannelPending} style="padding:8px 14px;font-size:13px">${saveChannelPending ? 'Saving…' : 'Save channel'}</button>
          <button class="alt" onClick=${testSlackMsg} disabled=${testSlackPending || !slack.connected} style="padding:8px 14px;font-size:13px">${testSlackPending ? 'Sending…' : 'Send test'}</button>
        </div>
        <div class="muted" style="margin-top:10px">Mode: ${humanizeMode(slack.mode || '-')}</div>
      </${ApprovalSurfaceCard}>

      <${ApprovalSurfaceCard}
        title="Teams approval routing"
        status=${teams.status || (teams.connected ? 'connected' : 'disconnected')}
        detail="Use Teams only if the finance approval path lives there."
      >
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input id="cl-teams-webhook" placeholder="https://.../incomingwebhook/..." value=${teams.webhook_url || ''} style="flex:1;min-width:220px" />
          <button class="alt" onClick=${saveWebhook} disabled=${saveWebhookPending} style="padding:8px 14px;font-size:13px">${saveWebhookPending ? 'Saving…' : 'Save webhook'}</button>
          <button class="alt" onClick=${testTeamsMsg} disabled=${testTeamsPending || !teams.connected} style="padding:8px 14px;font-size:13px">${testTeamsPending ? 'Sending…' : 'Send test'}</button>
        </div>
        <div class="muted" style="margin-top:10px">Mode: ${humanizeMode(teams.mode || '-')}</div>
      </${ApprovalSurfaceCard}>
    </div>
  `;
}
