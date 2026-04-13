/**
 * Connections Page — occasional setup surface for AP blockers.
 */
import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { hasCapability, integrationByName, humanizeStatus, humanizeMode, useAction } from '../route-helpers.js';

const html = htm.bind(h);
const ERP_OPTIONS = [
  { value: 'quickbooks', label: 'QuickBooks' },
  { value: 'xero', label: 'Xero' },
  { value: 'netsuite', label: 'NetSuite' },
  { value: 'sap', label: 'SAP' },
];

function getErpOptionLabel(value) {
  const token = String(value || '').trim().toLowerCase();
  return ERP_OPTIONS.find((option) => option.value === token)?.label || 'ERP';
}

function ConnectionRow({ label, status, detail, actionLabel = '', onAction, pending = false, disabled = false }) {
  const connected = String(status || '').trim().toLowerCase() === 'connected';
  return html`<div class="secondary-row">
    <div class="secondary-row-copy">
      <div class="secondary-chip-row" style="margin-bottom:4px">
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
    <div class="panel-head compact">
      <div>
        <h3 style="margin:0 0 4px">${title}</h3>
        <p class="muted" style="margin:0">${detail}</p>
      </div>
      <span class=${`status-badge ${status === 'connected' ? 'connected' : ''}`}>${humanizeStatus(status || 'unknown')}</span>
    </div>
    ${children}
  </div>`;
}

function getApprovalSummary(slack = {}, teams = {}) {
  if (slack.connected && slack.requires_reauthorization) return 'Reconnect Slack';
  if (slack.connected) return 'Slack ready';
  if (teams.connected) return 'Teams ready';
  return 'Set up Slack or Teams';
}

function getRoutingModeSummary(slack = {}, teams = {}) {
  if (slack.connected) return humanizeMode(slack.mode || '-');
  if (teams.connected) return humanizeMode(teams.mode || '-');
  return 'Set after approval setup';
}

function getSetupSummary({ gmail, gmailReconnectRequired, approvalConnected, slack, erp }) {
  const missing = [];
  if (!gmail.connected || gmailReconnectRequired) missing.push('Gmail');
  if (!approvalConnected || slack.requires_reauthorization) missing.push('Slack or Teams approvals');
  if (!erp.connected) missing.push('ERP');
  if (missing.length === 0) return 'Gmail, approvals, and ERP are ready for this workspace.';
  if (missing.length === 1) return `Finish ${missing[0]} before Clearledgr can run the full AP flow.`;
  return `Finish ${missing.slice(0, -1).join(', ')}, and ${missing[missing.length - 1]} before Clearledgr can run the full AP flow.`;
}

function getSlackConnectionDetail(slack = {}) {
  if (slack.connected && slack.requires_reauthorization) {
    return 'Reconnect Slack to restore approval actions and approver email matching.';
  }
  if (slack.connected && slack.approval_channel) {
    return `Approvals are ready in ${slack.approval_channel}.`;
  }
  if (slack.connected) {
    return 'Slack is connected. Pick the approval channel below.';
  }
  return 'Install Slack to send approval requests there.';
}

export default function ConnectionsPage({ bootstrap, api, toast, orgId, onRefresh, oauthBridge, navigate }) {
  const gmail = integrationByName(bootstrap, 'gmail');
  const erp = integrationByName(bootstrap, 'erp');
  const slack = integrationByName(bootstrap, 'slack');
  const teams = integrationByName(bootstrap, 'teams');
  // Capability comes directly from the authenticated bootstrap response
  // (workspace_shell._workspace_capabilities).  The previous code probed
  // /api/workspace/team/invites with silent:true to infer admin status
  // from a 200 vs 403 — that conflated "not admin" with "permission
  // denied / auth expired / network error" and hid real errors.
  const canEditConnections = hasCapability(bootstrap, 'manage_connections');

  const [connectGmail, gmailPending] = useAction(async () => {
    if (!canEditConnections) return;
    const payload = await api('/api/workspace/integrations/gmail/connect/start', {
      method: 'POST',
      body: JSON.stringify({ organization_id: orgId, redirect_path: '/workspace' }),
    });
    if (payload?.auth_url) {
      oauthBridge.startOAuth(payload.auth_url, 'gmail');
      return;
    }
    navigate?.('clearledgr/invoices');
  });

  const [connectSlack, slackPending] = useAction(async () => {
    if (!canEditConnections) return;
    const p = await api('/api/workspace/integrations/slack/install/start', { method: 'POST', body: JSON.stringify({ organization_id: orgId, mode: 'per_org', redirect_path: '/workspace' }) });
    oauthBridge.startOAuth(p.auth_url, 'slack');
  });
  const [saveChannel, saveChannelPending] = useAction(async () => {
    if (!canEditConnections) return;
    await api('/api/workspace/integrations/slack/channel', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: document.getElementById('cl-slack-channel')?.value?.trim() }) });
    toast('Channel saved.'); onRefresh();
  });
  const [testSlackMsg, testSlackPending] = useAction(async () => {
    if (!canEditConnections) return;
    await api('/api/workspace/integrations/slack/test', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: document.getElementById('cl-slack-channel')?.value?.trim() }) });
    toast('Slack connection verified.');
  });
  const [saveWebhook, saveWebhookPending] = useAction(async () => {
    if (!canEditConnections) return;
    const wh = document.getElementById('cl-teams-webhook')?.value?.trim();
    if (!wh) { toast('Webhook URL required.', 'error'); return; }
    await api('/api/workspace/integrations/teams/webhook', { method: 'POST', body: JSON.stringify({ organization_id: orgId, webhook_url: wh }) });
    toast('Teams webhook saved.'); onRefresh();
  });
  const [testTeamsMsg, testTeamsPending] = useAction(async () => {
    if (!canEditConnections) return;
    await api('/api/workspace/integrations/teams/test', { method: 'POST', body: JSON.stringify({ organization_id: orgId }) });
    toast('Test sent to Teams.');
  });

  const approvalConnected = Boolean(slack.connected || teams.connected);
  const setupMode = getSetupSummary({ gmail, gmailReconnectRequired, approvalConnected, slack, erp });
  const [erpType, setErpType] = useState(String(erp.erp_type || 'quickbooks').trim().toLowerCase() || 'quickbooks');
  const [erpFormSpec, setErpFormSpec] = useState(null);
  const [erpFormValues, setErpFormValues] = useState({});

  return html`
    <div class=${`secondary-banner ${canEditConnections ? '' : 'warning'}`}>
      <div class="secondary-banner-copy">
        <h3>${canEditConnections ? 'Setup and reconnects live here' : 'Connection status is visible here'}</h3>
        <p class="muted">${canEditConnections ? setupMode : 'Admins can change Gmail, approval routing, and ERP setup. Everyone else can still see what is connected.'}</p>
      </div>
      <div class="secondary-banner-actions">
        ${gmail.connected || gmailReconnectRequired
          ? html`<button class="btn-primary btn-sm" onClick=${connectGmail} disabled=${gmailPending || !canEditConnections}>${gmailPending ? 'Working…' : (gmailReconnectRequired ? 'Reconnect Gmail' : 'Refresh Gmail auth')}</button>`
          : html`<button class="btn-primary btn-sm" onClick=${connectGmail} disabled=${gmailPending || !canEditConnections}>${gmailPending ? 'Working…' : 'Connect Gmail'}</button>`}
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
              disabled=${!canEditConnections}
            />
            <${ConnectionRow}
              label="Slack"
              status=${slack.status || (slack.connected ? 'connected' : 'disconnected')}
              detail=${getSlackConnectionDetail(slack)}
              actionLabel=${slack.connected ? (slack.requires_reauthorization ? 'Reconnect Slack' : '') : 'Install Slack'}
              onAction=${connectSlack}
              pending=${slackPending}
              disabled=${!canEditConnections}
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
                : `Choose ${getErpOptionLabel(erpType)} or another ERP below before posting approved invoices.`}
              actionLabel=${erp.connected ? '' : 'Connect ERP'}
              onAction=${() => document.getElementById('cl-erp-connect-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
              disabled=${!canEditConnections}
            />
          </div>
        </div>

        <${ERPConnectionCard}
          id="cl-erp-connect-card"
          erp=${erp}
          erpType=${erpType}
          setErpType=${setErpType}
          erpFormSpec=${erpFormSpec}
          erpFormValues=${erpFormValues}
          setErpFormValues=${setErpFormValues}
          api=${api}
          toast=${toast}
          orgId=${orgId}
          onRefresh=${onRefresh}
          oauthBridge=${oauthBridge}
          canManageConnections=${canEditConnections}
        />

        <${ApprovalSurfaceCard}
          title="Slack approval routing"
          status=${slack.status || (slack.connected ? 'connected' : 'disconnected')}
          detail="Pick the Slack channel that should receive approval requests."
        >
          <div class="secondary-inline-actions">
            <button class="btn-primary btn-sm" onClick=${connectSlack} disabled=${slackPending || !canEditConnections}>${slackPending ? 'Working…' : (slack.connected ? 'Reconnect Slack' : 'Install Slack')}</button>
            <input id="cl-slack-channel" placeholder="#finance-approvals" value=${slack.approval_channel || ''} disabled=${!canEditConnections || !slack.connected} style="flex:1;min-width:180px" />
            <button class="btn-secondary btn-sm" onClick=${saveChannel} disabled=${saveChannelPending || !canEditConnections || !slack.connected}>${saveChannelPending ? 'Saving…' : 'Save channel'}</button>
            <button class="btn-ghost btn-sm" onClick=${testSlackMsg} disabled=${testSlackPending || !slack.connected || !canEditConnections}>${testSlackPending ? 'Verifying…' : 'Verify Slack'}</button>
          </div>
          <div class="secondary-note" style="margin-top:12px">
            ${slack.connected
              ? `Mode: ${humanizeMode(slack.mode || '-')} · Verification sends a private test instead of posting a live approval request.`
              : 'Install Slack first, then choose the approval channel and run a private verification test.'}
          </div>
        </${ApprovalSurfaceCard}>

        <${ApprovalSurfaceCard}
          title="Teams approval routing"
          status=${teams.status || (teams.connected ? 'connected' : 'disconnected')}
          detail="Use Teams instead when approval requests belong there."
        >
          <div class="secondary-inline-actions">
            <input id="cl-teams-webhook" placeholder="https://.../incomingwebhook/..." value=${teams.webhook_url || ''} disabled=${!canEditConnections} style="flex:1;min-width:240px" />
            <button class="btn-primary btn-sm" onClick=${saveWebhook} disabled=${saveWebhookPending || !canEditConnections}>${saveWebhookPending ? 'Saving…' : 'Save webhook'}</button>
            <button class="btn-ghost btn-sm" onClick=${testTeamsMsg} disabled=${testTeamsPending || !teams.connected || !canEditConnections}>${testTeamsPending ? 'Sending…' : 'Send test'}</button>
          </div>
          <div class="secondary-note" style="margin-top:12px">Mode: ${humanizeMode(teams.mode || '-')}</div>
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
              <span>${getApprovalSummary(slack, teams)}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>ERP</strong>
              <span>${erp.connected ? (erp.erp_type || 'Connected') : 'Not connected'}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Routing mode</strong>
              <span>${getRoutingModeSummary(slack, teams)}</span>
            </div>
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Who can edit this</h3>
          <div class="secondary-note">
            ${canEditConnections
              ? 'You can change connection setup from here.'
              : 'You can review status here, but only admins can reconnect Gmail, change approval routing, or update ERP setup.'}
          </div>
        </div>

        <${WebhooksPanel} api=${api} canManage=${canEditConnections} />
      </div>
    </div>
  `;
}

function ERPConnectionCard({
  id = '',
  erp,
  erpType,
  setErpType,
  erpFormSpec,
  erpFormValues,
  setErpFormValues,
  api,
  toast,
  orgId,
  onRefresh,
  oauthBridge,
  canManageConnections,
}) {
  const [startErpConnect, erpConnectPending] = useAction(async () => {
    if (!canManageConnections) return;
    const payload = await api('/api/workspace/integrations/erp/connect/start', {
      method: 'POST',
      body: JSON.stringify({ organization_id: orgId, erp_type: erpType }),
    });
    if (payload?.method === 'oauth' && payload?.auth_url) {
      setErpFormValues({});
      oauthBridge.startOAuth(payload.auth_url, erpType);
      return;
    }
    if (payload?.method === 'form' && Array.isArray(payload?.fields)) {
      setErpFormValues(Object.fromEntries(payload.fields.map((field) => [field.name, ''])));
      setErpFormSpec(payload);
      toast?.(`Enter your ${getErpOptionLabel(erpType)} connection details below.`, 'info');
      return;
    }
    toast?.('Could not start the ERP connection flow.', 'error');
  });

  const [submitErpForm, erpSubmitPending] = useAction(async () => {
    if (!canManageConnections || !erpFormSpec?.submit_url) return;
    const payload = await api(erpFormSpec.submit_url, {
      method: 'POST',
      body: JSON.stringify({ organization_id: orgId, ...erpFormValues }),
    });
    if (payload?.success) {
      setErpFormSpec(null);
      setErpFormValues({});
      toast?.(`${getErpOptionLabel(payload?.erp_type || erpType)} connected.`, 'success');
      onRefresh?.();
      return;
    }
    toast?.('Could not finish the ERP connection.', 'error');
  });

  return html`<div id=${id}>
    <${ApprovalSurfaceCard}
      title="ERP posting connection"
      status=${erp.status || (erp.connected ? 'connected' : 'disconnected')}
      detail="Choose the ERP Clearledgr should post into. OAuth ERPs open a connect flow; NetSuite and SAP finish here with credentials."
    >
      <div class="secondary-inline-actions">
        <select value=${erpType} onChange=${(event) => setErpType(event.target.value)} disabled=${!canManageConnections || erpConnectPending || erpSubmitPending} style="min-width:170px">
          ${ERP_OPTIONS.map((option) => html`<option key=${option.value} value=${option.value}>${option.label}</option>`)}
        </select>
        <button class="btn-primary btn-sm" onClick=${startErpConnect} disabled=${erpConnectPending || !canManageConnections}>
          ${erpConnectPending ? 'Working…' : `Connect ${getErpOptionLabel(erpType)}`}
        </button>
        ${erp.connected && html`<span class="secondary-chip">${getErpOptionLabel(erp.erp_type || erpType)} connected</span>`}
      </div>
      ${erpFormSpec?.help_text && html`<div class="secondary-note" style="margin-top:12px">${erpFormSpec.help_text}</div>`}
      ${Array.isArray(erpFormSpec?.fields) && erpFormSpec.fields.length > 0 && html`
        <div class="secondary-card" style="margin-top:14px">
          <div class="secondary-card-head">
            <div class="secondary-card-copy">
              <strong class="secondary-card-title">Finish ${getErpOptionLabel(erpType)} setup</strong>
              <div class="secondary-card-meta">Clearledgr will test the connection before saving it for this workspace.</div>
            </div>
          </div>
          <div class="secondary-card-body" style="display:grid;gap:12px">
            ${erpFormSpec.fields.map((field) => html`
              <label key=${field.name} style="display:grid;gap:6px">
                <span style="font-size:12px;font-weight:700;color:var(--ink)">${field.label}</span>
                <input
                  type=${field.type === 'password' ? 'password' : 'text'}
                  placeholder=${field.placeholder || ''}
                  value=${erpFormValues?.[field.name] || ''}
                  onInput=${(event) => setErpFormValues((current) => ({ ...current, [field.name]: event.target.value }))}
                  disabled=${erpSubmitPending || !canManageConnections}
                />
              </label>
            `)}
            <div class="secondary-inline-actions">
              <button class="btn-primary btn-sm" onClick=${submitErpForm} disabled=${erpSubmitPending || !canManageConnections}>
                ${erpSubmitPending ? 'Connecting…' : `Save ${getErpOptionLabel(erpType)} connection`}
              </button>
              <button class="btn-ghost btn-sm" onClick=${() => { setErpFormSpec(null); setErpFormValues({}); }} disabled=${erpSubmitPending}>Cancel</button>
            </div>
          </div>
        </div>
      `}
    </${ApprovalSurfaceCard}>
  </div>`;
}

function WebhooksPanel({ api, canManage }) {
  const [webhooks, setWebhooks] = useState([]);
  const [url, setUrl] = useState('');
  const [events, setEvents] = useState('*');
  const [adding, setAdding] = useState(false);
  useEffect(() => {
    api('/api/workspace/webhooks').then((d) => setWebhooks(d?.webhooks || [])).catch(() => {});
  }, []);
  const addWebhook = async () => {
    if (!url.trim()) return;
    setAdding(true);
    try {
      const result = await api('/api/workspace/webhooks', {
        method: 'POST',
        body: JSON.stringify({ url: url.trim(), event_types: events.split(',').map((e) => e.trim()).filter(Boolean) }),
      });
      if (result?.id) setWebhooks((prev) => [...prev, result]);
      setUrl('');
    } catch (e) { console.warn('Add webhook failed:', e); }
    setAdding(false);
  };
  const removeWebhook = async (id) => {
    try {
      await api(`/api/workspace/webhooks/${id}`, { method: 'DELETE' });
      setWebhooks((prev) => prev.filter((w) => w.id !== id));
    } catch (e) { console.warn('Remove webhook failed:', e); }
  };
  return html`
    <div class="panel">
      <div class="panel-head compact">
        <div>
          <h3 style="margin:0">Outgoing webhooks</h3>
          <p class="muted" style="margin:4px 0 0;font-size:12px">Notify external systems when AP events happen like approvals, retries, and posting outcomes.</p>
        </div>
      </div>
      ${webhooks.length === 0 && html`<div class="secondary-empty" style="padding:8px 0">No webhooks configured</div>`}
      ${webhooks.length > 0 && html`
        <div class="secondary-card-list">
          ${webhooks.map((wh) => html`
            <div key=${wh.id} class="secondary-card">
              <div class="secondary-card-head">
                <div class="secondary-card-copy">
                  <span class="secondary-card-title">${wh.url}</span>
                  <div class="secondary-card-meta">${Array.isArray(wh.event_types) && wh.event_types.length ? wh.event_types.join(', ') : '*'}</div>
                </div>
                ${canManage && html`<div class="secondary-inline-actions"><button class="btn-secondary btn-sm" onClick=${() => removeWebhook(wh.id)}>Remove</button></div>`}
              </div>
            </div>
          `)}
        </div>
      `}
      ${canManage && html`
        <div class="secondary-form-stack" style="margin-top:12px">
          <label>
            <span class="templates-field-label">Webhook URL</span>
            <input type="text" placeholder="https://..." value=${url} onInput=${(e) => setUrl(e.target.value)} />
          </label>
          <label>
            <span class="templates-field-label">Events</span>
            <input type="text" placeholder="* or invoice.approved, invoice.posted_to_erp" value=${events} onInput=${(e) => setEvents(e.target.value)} />
          </label>
          <div class="secondary-inline-actions">
            <button class="btn-secondary btn-sm" onClick=${addWebhook} disabled=${adding || !url.trim()}>${adding ? 'Adding…' : 'Add webhook'}</button>
          </div>
        </div>
        <div class="secondary-note" style="margin-top:10px">Events can be &quot;*&quot; for all AP events, or a comma-separated list like &quot;invoice.approved, invoice.posted_to_erp&quot;.</div>
      `}
    </div>
  `;
}
