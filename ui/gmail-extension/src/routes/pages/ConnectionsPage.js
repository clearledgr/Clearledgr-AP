/**
 * Connections Page — occasional setup surface for AP blockers.
 */
import { h } from 'preact';
import htm from 'htm';
import { hasCapability, integrationByName, humanizeStatus, humanizeMode, useAction } from '../route-helpers.js';

const html = htm.bind(h);

function ConnectionRow({ label, status, detail, actionLabel = '', onAction, pending = false, disabled = false }) {
  const connected = String(status || '').trim().toLowerCase() === 'connected';
  return html`<div class="secondary-row">
    <div class="secondary-row-copy">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
        <strong style="font-size:14px">${label}</strong>
        <span class=${`status-badge ${connected ? 'connected' : ''}`}>${humanizeStatus(status || 'unknown')}</span>
      </div>
      <div class="muted" style="font-size:12px">${detail}</div>
    </div>
    ${actionLabel
      ? html`<button class="btn-secondary btn-sm" onClick=${onAction} disabled=${pending || disabled}>${pending ? 'Working…' : actionLabel}</button>`
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
  const canManageConnections = hasCapability(bootstrap, 'manage_connections');
  const gmailReconnectRequired = Boolean(gmail.connected && (gmail.requires_reconnect || gmail.durable === false));

  const [connectGmail, gmailPending] = useAction(async () => {
    if (!canManageConnections) return;
    const payload = await api('/api/workspace/integrations/gmail/connect/start', {
      method: 'POST',
      body: JSON.stringify({ organization_id: orgId, redirect_path: '/workspace' }),
    });
    if (payload?.auth_url) {
      oauthBridge.startOAuth(payload.auth_url, 'gmail');
      return;
    }
    navigate?.('clearledgr/home');
  });

  const [connectSlack, slackPending] = useAction(async () => {
    if (!canManageConnections) return;
    const p = await api('/api/workspace/integrations/slack/install/start', { method: 'POST', body: JSON.stringify({ organization_id: orgId, mode: 'per_org', redirect_path: '/workspace' }) });
    oauthBridge.startOAuth(p.auth_url, 'slack');
  });
  const [saveChannel, saveChannelPending] = useAction(async () => {
    if (!canManageConnections) return;
    await api('/api/workspace/integrations/slack/channel', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: document.getElementById('cl-slack-channel')?.value?.trim() }) });
    toast('Channel saved.'); onRefresh();
  });
  const [testSlackMsg, testSlackPending] = useAction(async () => {
    if (!canManageConnections) return;
    await api('/api/workspace/integrations/slack/test', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: document.getElementById('cl-slack-channel')?.value?.trim() }) });
    toast('Test sent to Slack.');
  });
  const [saveWebhook, saveWebhookPending] = useAction(async () => {
    if (!canManageConnections) return;
    const wh = document.getElementById('cl-teams-webhook')?.value?.trim();
    if (!wh) { toast('Webhook URL required.', 'error'); return; }
    await api('/api/workspace/integrations/teams/webhook', { method: 'POST', body: JSON.stringify({ organization_id: orgId, webhook_url: wh }) });
    toast('Teams webhook saved.'); onRefresh();
  });
  const [testTeamsMsg, testTeamsPending] = useAction(async () => {
    if (!canManageConnections) return;
    await api('/api/workspace/integrations/teams/test', { method: 'POST', body: JSON.stringify({ organization_id: orgId }) });
    toast('Test sent to Teams.');
  });

  const approvalConnected = Boolean(slack.connected || teams.connected);
  const setupMode = approvalConnected && erp.connected && gmail.connected && !gmailReconnectRequired ? 'All core connections look ready.' : 'Finish any missing connection before invoices try to post.';

  return html`
    <div class=${`secondary-banner ${canManageConnections ? '' : 'warning'}`}>
      <div class="secondary-banner-copy">
        <h3>${canManageConnections ? 'Setup and reconnects live here' : 'Connection status is visible here'}</h3>
        <p class="muted">${canManageConnections ? setupMode : 'Admins can change Gmail, approval routing, and ERP setup. Everyone else can still see what is connected.'}</p>
      </div>
      <div class="secondary-banner-actions">
        ${gmail.connected || gmailReconnectRequired
          ? html`<button class="btn-primary btn-sm" onClick=${connectGmail} disabled=${gmailPending || !canManageConnections}>${gmailPending ? 'Working…' : (gmailReconnectRequired ? 'Reconnect Gmail' : 'Refresh Gmail auth')}</button>`
          : html`<button class="btn-primary btn-sm" onClick=${connectGmail} disabled=${gmailPending || !canManageConnections}>${gmailPending ? 'Working…' : 'Connect Gmail'}</button>`}
        <button class="btn-secondary btn-sm" onClick=${() => navigate?.('clearledgr/health')}>Open system status</button>
      </div>
    </div>

    <div class="secondary-shell">
      <div class="secondary-main">
        <div class="panel">
          <h3 style="margin-top:0">Workspace connections</h3>
          <p class="muted" style="margin:0 0 14px">Keep Gmail, approvals, and ERP ready. If one of these drops, work eventually stalls.</p>
          <div class="secondary-list">
            <${ConnectionRow}
              label="Gmail"
              status=${gmail.status || (gmail.connected ? 'connected' : 'disconnected')}
              detail=${gmail.connected
                ? (gmailReconnectRequired
                  ? 'Reconnect Gmail to keep this inbox connected.'
                  : 'Gmail is connected for this workspace.')
                : 'Connect Gmail from the prompt in Gmail.'}
              actionLabel=${gmail.connected ? (gmailReconnectRequired ? 'Reconnect Gmail' : '') : 'Connect Gmail'}
              onAction=${connectGmail}
              pending=${gmailPending}
              disabled=${!canManageConnections}
            />
            <${ConnectionRow}
              label="Slack"
              status=${slack.status || (slack.connected ? 'connected' : 'disconnected')}
              detail=${slack.connected
                ? `Approvals are ready${slack.approval_channel ? ` in ${slack.approval_channel}` : ''}.`
                : 'Install Slack to send approval requests there.'}
              actionLabel=${slack.connected ? '' : 'Install Slack'}
              onAction=${connectSlack}
              pending=${slackPending}
              disabled=${!canManageConnections}
            />
            <${ConnectionRow}
              label="Teams"
              status=${teams.status || (teams.connected ? 'connected' : 'disconnected')}
              detail=${teams.connected
                ? 'Teams approvals are connected.'
                : 'Save a Teams webhook to send approval requests there.'}
            />
            <${ConnectionRow}
              label="ERP"
              status=${erp.status || (erp.connected ? 'connected' : 'disconnected')}
              detail=${erp.connected
                ? `${erp.erp_type || 'ERP'} is connected.`
                : 'Connect an ERP before posting approved invoices.'}
              actionLabel=${erp.connected ? '' : 'Review status'}
              onAction=${() => navigate?.('clearledgr/health')}
            />
          </div>
        </div>

        <${ApprovalSurfaceCard}
          title="Slack approval routing"
          status=${slack.status || (slack.connected ? 'connected' : 'disconnected')}
          detail="Pick the Slack channel that should receive approval requests."
        >
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button class="btn-primary btn-sm" onClick=${connectSlack} disabled=${slackPending || !canManageConnections}>${slackPending ? 'Working…' : 'Install to Slack'}</button>
            <input id="cl-slack-channel" placeholder="#finance-approvals" value=${slack.approval_channel || ''} disabled=${!canManageConnections} style="flex:1;min-width:160px" />
            <button class="btn-secondary btn-sm" onClick=${saveChannel} disabled=${saveChannelPending || !canManageConnections}>${saveChannelPending ? 'Saving…' : 'Save channel'}</button>
            <button class="btn-ghost btn-sm" onClick=${testSlackMsg} disabled=${testSlackPending || !slack.connected || !canManageConnections}>${testSlackPending ? 'Sending…' : 'Send test'}</button>
          </div>
          <div class="muted" style="margin-top:10px">Mode: ${humanizeMode(slack.mode || '-')}</div>
        </${ApprovalSurfaceCard}>

        <${ApprovalSurfaceCard}
          title="Teams approval routing"
          status=${teams.status || (teams.connected ? 'connected' : 'disconnected')}
          detail="Use Teams instead when approval requests belong there."
        >
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <input id="cl-teams-webhook" placeholder="https://.../incomingwebhook/..." value=${teams.webhook_url || ''} disabled=${!canManageConnections} style="flex:1;min-width:220px" />
            <button class="btn-primary btn-sm" onClick=${saveWebhook} disabled=${saveWebhookPending || !canManageConnections}>${saveWebhookPending ? 'Saving…' : 'Save webhook'}</button>
            <button class="btn-ghost btn-sm" onClick=${testTeamsMsg} disabled=${testTeamsPending || !teams.connected || !canManageConnections}>${testTeamsPending ? 'Sending…' : 'Send test'}</button>
          </div>
          <div class="muted" style="margin-top:10px">Mode: ${humanizeMode(teams.mode || '-')}</div>
        </${ApprovalSurfaceCard}>
      </div>

      <div class="secondary-side">
        <div class="panel">
          <h3 style="margin-top:0">At a glance</h3>
          <div class="secondary-stat-grid" style="margin-top:12px">
            <div class="secondary-stat-card">
              <strong>Gmail</strong>
              <span>${gmailReconnectRequired ? 'Reconnect needed' : (gmail.connected ? 'Connected' : 'Not connected')}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Approvals</strong>
              <span>${approvalConnected ? 'Ready' : 'No approval surface yet'}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>ERP</strong>
              <span>${erp.connected ? (erp.erp_type || 'Connected') : 'Not connected'}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Routing mode</strong>
              <span>${slack.connected ? humanizeMode(slack.mode || '-') : teams.connected ? humanizeMode(teams.mode || '-') : 'Not set'}</span>
            </div>
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Who can edit this</h3>
          <div class="secondary-note">
            ${canManageConnections
              ? 'You can change connection setup from here.'
              : 'You can review status here, but only admins can reconnect Gmail, change approval routing, or update ERP setup.'}
          </div>
        </div>
      </div>
    </div>
  `;
}
