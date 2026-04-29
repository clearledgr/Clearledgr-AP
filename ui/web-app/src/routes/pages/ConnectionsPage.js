/**
 * Connections Page — occasional setup surface for AP blockers.
 */
import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';
import { hasCapability, integrationByName, humanizeStatus, humanizeMode, useAction, fmtDateTime } from '../route-helpers.js';

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
        <${ConnectionHealthPanel} api=${api} orgId=${orgId} />

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

        <${FieldMappingPanel}
          api=${api}
          orgId=${orgId}
          erpType=${erpType}
          erpConnected=${erp.connected}
          canManage=${canEditConnections}
          toast=${toast}
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

        <${WebhooksPanel} api=${api} canManage=${canEditConnections} toast=${toast} />
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

function WebhooksPanel({ api, canManage, toast }) {
  const [webhooks, setWebhooks] = useState([]);
  const [url, setUrl] = useState('');
  const [events, setEvents] = useState('*');
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api('/api/workspace/webhooks').then((d) => setWebhooks(d?.webhooks || [])).catch(() => {});
  }, []);

  const isValidUrl = (raw) => {
    try {
      const u = new URL(String(raw || '').trim());
      return u.protocol === 'https:' || u.protocol === 'http:';
    } catch {
      return false;
    }
  };

  const addWebhook = async () => {
    setError('');
    const trimmed = url.trim();
    if (!trimmed) {
      setError('Webhook URL is required.');
      return;
    }
    if (!isValidUrl(trimmed)) {
      setError('Enter a valid http(s):// URL.');
      return;
    }
    setAdding(true);
    try {
      const result = await api('/api/workspace/webhooks', {
        method: 'POST',
        body: JSON.stringify({ url: trimmed, event_types: events.split(',').map((e) => e.trim()).filter(Boolean) }),
      });
      if (result?.id) setWebhooks((prev) => [...prev, result]);
      setUrl('');
    } catch (e) {
      setError(e?.message || 'Could not add webhook. Check the URL and try again.');
    }
    setAdding(false);
  };
  const removeWebhook = async (id) => {
    try {
      await api(`/api/workspace/webhooks/${id}`, { method: 'DELETE' });
      setWebhooks((prev) => prev.filter((w) => w.id !== id));
    } catch (e) {
      toast?.(e?.message || 'Could not remove webhook.', 'error');
    }
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
          ${error && html`<div class="form-error">${error}</div>`}
          <div class="secondary-inline-actions">
            <button class="btn-secondary btn-sm" onClick=${addWebhook} disabled=${adding || !url.trim()}>${adding ? 'Adding…' : 'Add webhook'}</button>
          </div>
        </div>
        <div class="secondary-note" style="margin-top:10px">Events can be &quot;*&quot; for all AP events, or a comma-separated list like &quot;invoice.approved, invoice.posted_to_erp&quot;.</div>
      `}
    </div>
  `;
}


// ─── Module 5 Pass A — Custom field mapping panel ────────────────────
// Sits below the ERP connection card on the Connections page. Reads
// the bounded catalog from the workspace shell, renders one input per
// catalog entry, persists user-supplied overrides on save. The catalog
// is per-ERP — switching the ERP picker above re-fetches.
//
// Backed by:
//   GET /api/workspace/erp/field-mappings?erp_type=&organization_id=
//   PUT /api/workspace/erp/field-mappings?organization_id=  body={erp_type, mappings}
//
// The default field id is rendered as the input placeholder so the
// operator sees what Clearledgr will fall back to if they don't
// override. A "Reset" link reverts a single field to the default.
function FieldMappingPanel({ api, orgId, erpType, erpConnected, canManage, toast }) {
  const [catalog, setCatalog] = useState(null);
  const [supportedErps, setSupportedErps] = useState([]);
  const [mappings, setMappings] = useState({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [dirty, setDirty] = useState(false);

  const erpKey = String(erpType || '').trim().toLowerCase();

  // Reload the catalog + persisted mapping whenever the ERP picker
  // above changes. Catalog is per-ERP (NetSuite custom-bodies vs
  // SAP Z-fields) so we can't render a single combined view.
  useEffect(() => {
    if (!api || !orgId || !erpKey) return;
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const resp = await api(
          `/api/workspace/erp/field-mappings?erp_type=${encodeURIComponent(erpKey)}&organization_id=${encodeURIComponent(orgId)}`
        );
        if (cancelled) return;
        setCatalog(Array.isArray(resp?.catalog) ? resp.catalog : []);
        setSupportedErps(Array.isArray(resp?.supported_erps) ? resp.supported_erps : []);
        setMappings(resp?.mappings || {});
        setDirty(false);
      } catch (exc) {
        if (cancelled) return;
        // Treat 400 unsupported_erp as "no catalog for this ERP" rather
        // than an error — the panel renders an empty-state hint instead
        // of a red banner. Anything else is a real error.
        const msg = String(exc?.message || exc);
        if (msg.includes('unsupported_erp_type')) {
          setCatalog([]);
          setMappings({});
        } else {
          setError(msg);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [api, orgId, erpKey]);

  const setField = (key, value) => {
    setMappings((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  };

  const resetField = (key) => {
    setMappings((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
    setDirty(true);
  };

  const validateClientSide = () => {
    // Match the regex the server enforces. Client-side check exists to
    // give immediate feedback; the server is still the source of truth.
    const errors = [];
    for (const entry of catalog || []) {
      const value = String(mappings[entry.key] || '').trim();
      if (!value) continue;
      try {
        const re = new RegExp(entry.pattern);
        if (!re.test(value)) {
          errors.push(`${entry.label}: '${value}' doesn't match ${entry.pattern}`);
        }
      } catch (exc) {
        // If the pattern itself fails to compile (shouldn't happen),
        // skip — server validation will catch any real issue.
      }
    }
    return errors;
  };

  const onSave = async () => {
    if (!canManage) return;
    setError(null);
    const clientErrors = validateClientSide();
    if (clientErrors.length > 0) {
      setError(clientErrors.join('  •  '));
      return;
    }
    // Only send non-empty values. The server treats empty == revert
    // to default, but sending fewer keys is a cleaner audit diff.
    const payloadMappings = Object.fromEntries(
      Object.entries(mappings)
        .map(([k, v]) => [k, String(v || '').trim()])
        .filter(([, v]) => v.length > 0)
    );
    setSaving(true);
    try {
      const resp = await api(
        `/api/workspace/erp/field-mappings?organization_id=${encodeURIComponent(orgId)}`,
        {
          method: 'PUT',
          body: JSON.stringify({ erp_type: erpKey, mappings: payloadMappings }),
        }
      );
      setMappings(resp?.mappings || {});
      setDirty(false);
      toast?.('Custom field mappings saved.', 'success');
    } catch (exc) {
      const msg = String(exc?.message || exc);
      // The 422 detail surfaces the server-side validation list.
      // Surface it raw so the operator sees the exact field violation.
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  const erpLabel = (() => {
    const map = { netsuite: 'NetSuite', sap: 'SAP', quickbooks: 'QuickBooks', xero: 'Xero' };
    return map[erpKey] || (erpKey ? erpKey.charAt(0).toUpperCase() + erpKey.slice(1) : 'ERP');
  })();

  // Group catalog entries by category for the UI grouping headers.
  const grouped = (() => {
    const buckets = {};
    for (const entry of catalog || []) {
      const cat = entry.category || 'other';
      if (!buckets[cat]) buckets[cat] = [];
      buckets[cat].push(entry);
    }
    return buckets;
  })();

  const CATEGORY_LABELS = {
    identity: 'Identity fields',
    workflow: 'Workflow fields',
    dimension: 'Dimension fields',
    other: 'Other fields',
  };

  return html`
    <div class="panel cl-field-mapping-panel">
      <div class="panel-head compact">
        <div>
          <h3 style="margin:0">Custom field mapping</h3>
          <p class="muted" style="margin:4px 0 0;font-size:12px">
            Override the default ${erpLabel} field IDs that Clearledgr writes
            to. Leave a field blank to use the default.
          </p>
        </div>
        ${supportedErps.length > 0 && !supportedErps.includes(erpKey) ? html`
          <span class="secondary-chip">Not configurable for ${erpLabel}</span>
        ` : null}
      </div>

      ${loading ? html`<div class="muted" style="padding:8px 0">Loading catalog…</div>` : null}

      ${!loading && (catalog?.length || 0) === 0 ? html`
        <div class="secondary-empty" style="padding:8px 0">
          ${supportedErps.includes(erpKey)
            ? `No custom fields are mapped for ${erpLabel} yet.`
            : `Custom field mapping is not available for ${erpLabel}. Switch ERP above to configure NetSuite or SAP.`}
        </div>
      ` : null}

      ${(catalog?.length || 0) > 0 ? html`
        <div class="cl-field-mapping-body">
          ${Object.keys(CATEGORY_LABELS).map((cat) => {
            const entries = grouped[cat];
            if (!entries || entries.length === 0) return null;
            return html`
              <fieldset class="cl-field-mapping-group" key=${cat}>
                <legend>${CATEGORY_LABELS[cat]}</legend>
                <div class="cl-field-mapping-rows">
                  ${entries.map((entry) => {
                    const value = mappings[entry.key] || '';
                    const usingDefault = !value;
                    return html`
                      <div class="cl-field-mapping-row" key=${entry.key}>
                        <div class="cl-field-mapping-meta">
                          <strong>${entry.label}</strong>
                          <span class="muted">${entry.description}</span>
                          ${entry.default ? html`
                            <span class="cl-field-mapping-default">
                              Default: <code>${entry.default}</code>
                            </span>
                          ` : null}
                        </div>
                        <div class="cl-field-mapping-input">
                          <input
                            type="text"
                            placeholder=${entry.default || `${erpLabel} field id`}
                            value=${value}
                            onInput=${(e) => setField(entry.key, e.target.value)}
                            disabled=${!canManage || saving} />
                          ${!usingDefault ? html`
                            <button
                              class="btn-ghost btn-sm"
                              type="button"
                              onClick=${() => resetField(entry.key)}
                              disabled=${!canManage || saving}>
                              Reset
                            </button>
                          ` : null}
                        </div>
                      </div>`;
                  })}
                </div>
              </fieldset>`;
          })}
        </div>
        ${error ? html`<div class="form-error">${error}</div>` : null}
        ${canManage ? html`
          <div class="secondary-inline-actions" style="margin-top:12px">
            <button
              class="btn-primary btn-sm"
              onClick=${onSave}
              disabled=${saving || !dirty}>
              ${saving ? 'Saving…' : (dirty ? 'Save mappings' : 'No changes')}
            </button>
            ${!erpConnected ? html`
              <span class="muted" style="font-size:12px">
                ${erpLabel} is not connected yet — mappings will apply once you connect.
              </span>
            ` : null}
          </div>
        ` : html`
          <div class="secondary-note" style="margin-top:10px">
            Only admins can change custom field mappings.
          </div>
        `}
      ` : null}
    </div>
  `;
}


// ─── Module 5 Pass B — Connection health panel ───────────────────────
// Lives at the top of secondary-main on the Connections page so a
// leader landing here gets the live signal first, before the static
// connect/disconnect rows below.
//
// Backed by:
//   GET /api/workspace/connections/health?organization_id=&window_hours=
//
// Auto-refreshes every 30s. The 30s cadence is a tradeoff between
// "leader sees breakage in 10 min" (the scope's acceptance criterion)
// and not hammering the api endpoint for an open tab.
function ConnectionHealthPanel({ api, orgId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [windowHours, setWindowHours] = useState(24);
  const [expandedKey, setExpandedKey] = useState(null);

  const load = useCallback(async () => {
    if (!api || !orgId) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set('organization_id', orgId);
      params.set('window_hours', String(windowHours));
      const resp = await api(`/api/workspace/connections/health?${params.toString()}`);
      setData(resp);
    } catch (exc) {
      setError(String(exc?.message || exc));
    } finally {
      setLoading(false);
    }
  }, [api, orgId, windowHours]);

  // Auto-refresh loop. Cleans up on unmount + on window-hours change
  // (the new request fires immediately via the load() in the deps).
  useEffect(() => {
    let cancelled = false;
    load();
    const id = setInterval(() => {
      if (!cancelled) load();
    }, 30000);
    return () => { cancelled = true; clearInterval(id); };
  }, [load]);

  const integrations = data?.integrations || [];
  const webhooks = data?.webhooks || { delivered: 0, failed: 0, retrying: 0 };
  const computedAt = data?.computed_at;

  // Derive the rollup status — the worst per-integration status
  // wins so the panel header tells the leader the overall picture
  // without making them scan every tile.
  const rollup = (() => {
    if (integrations.some((i) => i.status === 'down')) return 'down';
    if (integrations.some((i) => i.status === 'degraded')) return 'degraded';
    if (integrations.some((i) => i.status === 'healthy')) return 'healthy';
    return 'not_configured';
  })();

  return html`
    <div class="panel cl-conn-health-panel">
      <div class="panel-head compact">
        <div>
          <h3 style="margin:0">Connection health</h3>
          <p class="muted" style="margin:4px 0 0;font-size:12px">
            Live signal from the agent's audit log. Refreshes every 30 seconds.
            ${computedAt ? html` Last update <span class="cl-conn-health-ts">${fmtDateTime(computedAt)}</span>.` : null}
          </p>
        </div>
        <div class="cl-conn-health-controls">
          <span class=${`cl-conn-health-chip cl-conn-health-${rollup}`}>
            ${rollup === 'down' ? 'Action needed'
              : rollup === 'degraded' ? 'Investigating'
              : rollup === 'healthy' ? 'All systems go'
              : 'Not configured'}
          </span>
          <select
            value=${windowHours}
            onChange=${(e) => setWindowHours(parseInt(e.target.value, 10))}
            disabled=${loading}>
            <option value="1">Last 1h</option>
            <option value="24">Last 24h</option>
            <option value="72">Last 72h</option>
            <option value="168">Last 7d</option>
          </select>
          <button
            class="btn-ghost btn-sm"
            onClick=${load}
            disabled=${loading}>
            ${loading ? '…' : 'Refresh'}
          </button>
        </div>
      </div>

      ${error ? html`<div class="form-error" style="margin-top:8px">${error}</div>` : null}

      ${integrations.length > 0 ? html`
        <div class="cl-conn-health-grid">
          ${integrations.map((row) => {
            const expanded = expandedKey === row.integration_type;
            const hasError = !!row.latest_error;
            return html`
              <div
                class=${`cl-conn-health-tile cl-conn-health-${row.status}`}
                key=${row.integration_type}>
                <div class="cl-conn-health-tile-head">
                  <div class="cl-conn-health-tile-meta">
                    <strong>${row.label}</strong>
                    <span class=${`cl-conn-health-chip cl-conn-health-${row.status}`}>
                      ${row.status === 'healthy' ? 'Healthy'
                        : row.status === 'degraded' ? 'Degraded'
                        : row.status === 'down' ? 'Down'
                        : 'Not configured'}
                    </span>
                  </div>
                  ${hasError ? html`
                    <button
                      class="btn-ghost btn-sm"
                      onClick=${() => setExpandedKey(expanded ? null : row.integration_type)}>
                      ${expanded ? 'Hide error' : 'View error'}
                    </button>
                  ` : null}
                </div>
                <div class="cl-conn-health-tile-stats">
                  <div>
                    <span class="muted">Last sync</span>
                    <span>${row.last_sync_at ? fmtDateTime(row.last_sync_at) : '—'}</span>
                  </div>
                  <div>
                    <span class="muted">Events</span>
                    <span>${row.events_24h.toLocaleString()}</span>
                  </div>
                  <div>
                    <span class="muted">Errors</span>
                    <span class=${row.errors_24h > 0 ? 'cl-conn-health-errors' : ''}>
                      ${row.errors_24h.toLocaleString()}
                    </span>
                  </div>
                </div>
                ${expanded && hasError ? html`
                  <div class="cl-conn-health-error-detail">
                    <div class="cl-conn-health-error-head">
                      <code>${row.latest_error.event_type}</code>
                      <span class="muted">${fmtDateTime(row.latest_error.ts)}</span>
                    </div>
                    ${row.latest_error.message ? html`
                      <div class="cl-conn-health-error-msg">${row.latest_error.message}</div>
                    ` : null}
                  </div>
                ` : null}
              </div>`;
          })}

          <div class="cl-conn-health-tile cl-conn-health-webhooks">
            <div class="cl-conn-health-tile-head">
              <div class="cl-conn-health-tile-meta">
                <strong>Outgoing webhooks</strong>
                <span class=${`cl-conn-health-chip ${webhooks.failed > 0 ? 'cl-conn-health-degraded' : 'cl-conn-health-healthy'}`}>
                  ${webhooks.failed > 0 ? 'Failures' : 'OK'}
                </span>
              </div>
            </div>
            <div class="cl-conn-health-tile-stats">
              <div>
                <span class="muted">Delivered</span>
                <span>${webhooks.delivered.toLocaleString()}</span>
              </div>
              <div>
                <span class="muted">Retrying</span>
                <span>${webhooks.retrying.toLocaleString()}</span>
              </div>
              <div>
                <span class="muted">Failed</span>
                <span class=${webhooks.failed > 0 ? 'cl-conn-health-errors' : ''}>
                  ${webhooks.failed.toLocaleString()}
                </span>
              </div>
            </div>
          </div>
        </div>
      ` : (loading ? null : html`
        <div class="muted" style="padding:12px 0">No integrations to summarise yet.</div>
      `)}
    </div>
  `;
}
