import { h, render, Component } from 'preact';
import { useState, useEffect, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

// ==================== ERROR BOUNDARY ====================

class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(e, info) { console.error('[Clearledgr]', e, info?.componentStack || ''); }
  render() {
    if (this.state.error) {
      return html`<div class="panel"><p class="muted">${this.props.fallback || 'Something went wrong.'}</p>
        <button class="alt" onClick=${() => this.setState({ error: null })}>Retry</button></div>`;
    }
    return this.props.children;
  }
}

// ==================== CONFIG ====================

const PAGES = [
  { id: 'setup', title: 'Setup', subtitle: 'Connect your tools and start processing invoices.' },
  { id: 'activity', title: 'Activity', subtitle: 'See what Clearledgr processed and what needs attention.' },
  { id: 'ops', title: 'Ops', subtitle: 'Monitoring, batch actions, and operational recovery controls.' },
  { id: 'integrations', title: 'Integrations', subtitle: 'Connect and verify Gmail, Slack, and ERP.' },
  { id: 'organization', title: 'Organization', subtitle: 'Manage organization profile and runtime settings.' },
  { id: 'policies', title: 'AP Policies', subtitle: 'Configure policy behavior for AP decisioning.' },
  { id: 'team', title: 'Team', subtitle: 'Invite teammates and manage access.' },
  { id: 'plan', title: 'Plan & Usage', subtitle: 'Track plan, trial, onboarding, and usage.' },
  { id: 'health', title: 'Health', subtitle: 'See required actions before production rollout.' },
];

const TZ = 'Europe/London';
const LOCALE = 'en-GB';

// ==================== AUTH / API ====================

function clearSession() {
  document.cookie = 'clearledgr_admin_access=; Max-Age=0; path=/';
  document.cookie = 'clearledgr_admin_refresh=; Max-Age=0; path=/';
  document.cookie = 'clearledgr_admin_csrf=; Max-Age=0; path=/';
}

function readCookie(name) {
  const prefix = `${name}=`;
  const match = String(document.cookie || '').split(';').map(s => s.trim()).find(s => s.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : '';
}

let _refreshInFlight = false;
let _refreshFailed = false;

async function refreshAdminSession() {
  if (_refreshFailed || _refreshInFlight) return false;
  _refreshInFlight = true;
  try {
    const resp = await fetch('/auth/refresh', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ refresh_token: readCookie('clearledgr_admin_refresh') || '' }),
    });
    if (!resp.ok) { _refreshFailed = true; return false; }
    return true;
  } catch { _refreshFailed = true; return false; }
  finally { _refreshInFlight = false; }
}

let _toastFn = null;
function setToastFn(fn) { _toastFn = fn; }

async function api(path, options = {}) {
  const method = String(options.method || 'GET').trim().toUpperCase();
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = readCookie('clearledgr_admin_csrf');
    if (csrf) headers['X-CSRF-Token'] = csrf;
  }
  const response = await fetch(path, { ...options, headers, credentials: 'include' });
  if (response.status === 401 && !options.__skipRefresh) {
    const ok = await refreshAdminSession();
    if (ok) return api(path, { ...options, __skipRefresh: true });
    clearSession();
    throw new Error('Session expired');
  }
  if (!response.ok) {
    const text = await response.text();
    const err = new Error(text || `HTTP ${response.status}`);
    err.status = response.status;
    if (!options.silent) _toastFn?.(`Request failed: ${err.message}`, 'error');
    throw err;
  }
  if (response.status === 204) return {};
  return response.json();
}

// ==================== HELPERS ====================

function fmtDateTime(v) {
  if (!v) return '';
  const d = new Date(v);
  if (isNaN(d.getTime())) return '';
  try { return d.toLocaleString(LOCALE, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TZ }); }
  catch { return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
}
function fmtDate(v) {
  if (!v) return '';
  const d = new Date(v);
  if (isNaN(d.getTime())) return '';
  try { return d.toLocaleDateString(LOCALE, { day: '2-digit', month: 'short', timeZone: TZ }); }
  catch { return d.toLocaleDateString([], { month: 'short', day: 'numeric' }); }
}
function fmtTime(v) {
  if (!v) return '';
  const d = new Date(v);
  if (isNaN(d.getTime())) return '';
  try { return d.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TZ }); }
  catch { return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
}
function fmtRate(v) { const n = Number(v); return isFinite(n) ? `${n.toFixed(1)}%` : '0.0%'; }
function fmtDollar(v) { return '$' + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 }); }

function hasOpsAccess(bootstrap) {
  return ['admin', 'owner', 'operator'].includes(String(bootstrap?.current_user?.role || '').trim().toLowerCase());
}

function integrationByName(bootstrap, name) {
  return (bootstrap?.integrations || []).find(i => i.name === name) || {};
}

function statusBadge(ok) {
  return html`<span class="status-badge ${ok ? 'connected' : ''}">${ok ? 'Connected' : 'Not connected'}</span>`;
}

function checkMark(ok) {
  return ok
    ? html`<span class="check-ok">Connected</span>`
    : html`<span class="check-no">Not connected</span>`;
}

function eventBadge(eventType) {
  const t = (eventType || '').toLowerCase();
  if (t.includes('posted') || t.includes('closed')) return { label: 'Posted', cls: 'ev-posted' };
  if (t.includes('approved') || t.includes('auto_approved')) return { label: 'Approved', cls: 'ev-approved' };
  if (t.includes('rejected')) return { label: 'Rejected', cls: 'ev-rejected' };
  if (t.includes('needs_approval') || t.includes('pending')) return { label: 'Pending review', cls: 'ev-pending' };
  if (t.includes('received') || t.includes('classified')) return { label: 'Received', cls: 'ev-received' };
  if (t.includes('validated')) return { label: 'Validated', cls: 'ev-validated' };
  if (t.includes('failed') || t.includes('error')) return { label: 'Error', cls: 'ev-error' };
  return { label: eventType, cls: '' };
}

function resolveRef(item) { return String(item?.thread_id || item?.message_id || item?.id || '').trim(); }
function currentEmail(bootstrap) { return String(bootstrap?.current_user?.email || '').trim() || 'admin_console'; }

// ==================== HOOKS ====================

function useAction(fn) {
  const [pending, setPending] = useState(false);
  const exec = useCallback(async (...args) => {
    if (pending) return;
    setPending(true);
    try { await fn(...args); } finally { setPending(false); }
  }, [fn, pending]);
  return [exec, pending];
}

// ==================== TOAST ====================

function Toast() {
  const [items, setItems] = useState([]);
  const idRef = useRef(0);

  useEffect(() => {
    setToastFn((message, type = 'success') => {
      const id = ++idRef.current;
      setItems(prev => [...prev, { id, message, type, show: false }]);
      requestAnimationFrame(() => setItems(prev => prev.map(i => i.id === id ? { ...i, show: true } : i)));
      setTimeout(() => setItems(prev => prev.filter(i => i.id !== id)), 4300);
    });
    return () => setToastFn(null);
  }, []);

  return html`<div id="toast-container" aria-live="polite">
    ${items.map(i => html`<div key=${i.id} class="toast toast-${i.type} ${i.show ? 'show' : ''}">${i.message}</div>`)}
  </div>`;
}

function toast(msg, type) { _toastFn?.(msg, type); }

// ==================== AUTH SHELL ====================

function AuthShell({ onLogin, inviteToken }) {
  const [msg] = useState('');
  const [loginAction, loginPending] = useAction(async (e) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const login = await api('/auth/login', { method: 'POST', body: JSON.stringify({ email: fd.get('email'), password: fd.get('password') }), headers: {}, silent: true });
    if (!login?.access_token) throw new Error('No access token');
    onLogin();
  });
  const [googleAction, googlePending] = useAction(async () => {
    const orgId = new URLSearchParams(window.location.search).get('org') || localStorage.getItem('cl_admin_org') || 'default';
    const invitePart = inviteToken ? `&invite_token=${encodeURIComponent(inviteToken)}` : '';
    const p = await api(`/auth/google/start?organization_id=${encodeURIComponent(orgId)}&redirect_path=${encodeURIComponent('/console')}${invitePart}`, { headers: {}, silent: true });
    window.location.href = p.auth_url;
  });
  const [inviteAction, invitePending] = useAction(async (e) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const p = await api('/auth/invites/accept', { method: 'POST', body: JSON.stringify({ token: inviteToken, name: fd.get('name') || null, password: fd.get('password') || null }), headers: {}, silent: true });
    if (!p?.access_token) throw new Error('No access token');
    onLogin();
  });

  return html`<div class="auth-shell">
    <div class="auth-card">
      <h1>Clearledgr Admin Center</h1>
      <p>Sign in to manage setup, integrations, policies, and plan controls.</p>
      ${msg && html`<div class="muted">${msg}</div>`}
      <form onSubmit=${loginAction}>
        <label>Email</label><input type="email" name="email" required />
        <label>Password</label><input type="password" name="password" required />
        <button type="submit" disabled=${loginPending}>${loginPending ? 'Signing in...' : 'Sign in'}</button>
      </form>
      <button class="alt" onClick=${googleAction} disabled=${googlePending}>Continue with Google</button>
      ${inviteToken && html`
        <div class="invite-shell">
          <h3>Accept Team Invite</h3>
          <p>Set your name and password to join your organization.</p>
          <form onSubmit=${inviteAction}>
            <label>Name (optional)</label><input type="text" name="name" placeholder="Your name" />
            <label>Password</label><input type="password" name="password" minlength="8" required />
            <button type="submit" disabled=${invitePending}>${invitePending ? 'Accepting...' : 'Accept Invite'}</button>
          </form>
        </div>
      `}
    </div>
  </div>`;
}

// ==================== NAV ====================

function SideNav({ pages, active, onNav, orgLabel, onLogout }) {
  return html`<aside class="side-nav">
    <div class="brand">Clearledgr</div>
    <div class="org-chip">${orgLabel}</div>
    <nav>
      ${pages.map(p => html`<button key=${p.id} class="nav-btn ${active === p.id ? 'active' : ''}" onClick=${() => onNav(p.id)}>${p.title}</button>`)}
    </nav>
    <button class="logout" onClick=${onLogout}>Log out</button>
  </aside>`;
}

// ==================== PAGE COMPONENTS ====================

function SetupPage({ bootstrap, orgId, onNav, onRefresh }) {
  const gmail = integrationByName(bootstrap, 'gmail');
  const slack = integrationByName(bootstrap, 'slack');
  const teams = integrationByName(bootstrap, 'teams');
  const erp = integrationByName(bootstrap, 'erp');
  const policyConfig = bootstrap?.policyPayload?.policy?.config_json || {};
  const gmailOk = !!gmail.connected, slackOk = !!slack.connected, teamsOk = !!teams.connected, erpOk = !!erp.connected;
  const channelOk = slackOk && !!slack.approval_channel;
  const policyOk = policyConfig && Object.keys(policyConfig).length > 0;
  const allReady = gmailOk && slackOk && teamsOk && erpOk && channelOk && policyOk;
  const erpType = erp.erp_type || '';
  const [nsVisible, setNsVisible] = useState(false);
  const [sapVisible, setSapVisible] = useState(false);

  const [connectGmail, gmailPending] = useAction(async () => {
    const p = await api('/api/admin/integrations/gmail/connect/start', { method: 'POST', body: JSON.stringify({ organization_id: orgId, redirect_path: `/console?org=${encodeURIComponent(orgId)}&page=integrations` }) });
    if (p?.auth_url) window.location.href = p.auth_url;
  });
  const [connectSlack] = useAction(async () => {
    const p = await api('/api/admin/integrations/slack/install/start', { method: 'POST', body: JSON.stringify({ organization_id: orgId, mode: 'per_org', redirect_path: '/console' }) });
    window.location.href = p.auth_url;
  });
  const [connectErp] = useAction(async (erpType) => {
    const p = await api('/api/admin/integrations/erp/connect/start', { method: 'POST', body: JSON.stringify({ organization_id: orgId, erp_type: erpType }) });
    if (p.method === 'oauth') window.location.href = p.auth_url;
  });
  const [saveChannel, channelPending] = useAction(async () => {
    const ch = document.getElementById('slack-channel-input')?.value?.trim();
    await api('/api/admin/integrations/slack/channel', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: ch }) });
    toast('Approval channel saved.'); onRefresh();
  });
  const [testSlack] = useAction(async () => {
    const ch = document.getElementById('slack-channel-input')?.value?.trim();
    await api('/api/admin/integrations/slack/test', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: ch }) });
    toast('Test message sent to Slack.');
  });
  const [connectNs, nsPending] = useAction(async () => {
    const g = id => document.getElementById(id)?.value?.trim() || '';
    await api('/api/admin/integrations/erp/connect/netsuite', { method: 'POST', body: JSON.stringify({ organization_id: orgId, account_id: g('ns-account-id'), consumer_key: g('ns-consumer-key'), consumer_secret: g('ns-consumer-secret'), token_id: g('ns-token-id'), token_secret: g('ns-token-secret') }) });
    toast('NetSuite connected!'); setNsVisible(false); onRefresh();
  });
  const [connectSap, sapPending] = useAction(async () => {
    const g = id => document.getElementById(id)?.value?.trim() || '';
    await api('/api/admin/integrations/erp/connect/sap', { method: 'POST', body: JSON.stringify({ organization_id: orgId, base_url: g('sap-base-url'), username: g('sap-username'), password: g('sap-password') }) });
    toast('SAP connected!'); setSapVisible(false); onRefresh();
  });
  const [launch, launchPending] = useAction(async () => {
    await api('/api/admin/onboarding/step', { method: 'POST', body: JSON.stringify({ organization_id: orgId, step: 5 }) });
    toast('Clearledgr is live! Invoices will now be processed automatically.', 'success'); onRefresh();
  });

  const ws = gmail.watch_status || 'unknown';
  const wsLabel = ws === 'active' ? 'Push (real-time)' : ws === 'polling' ? 'Polling (60s)' : 'Disconnected';
  const wsClass = ws === 'active' ? 'ap-active' : ws === 'polling' ? 'ap-polling' : 'ap-off';
  const dash = bootstrap?.dashboard || {};

  return html`
    <div class="panel"><h3>Setup steps</h3>
      <div class="readiness-list">
        <div class="readiness-item">${checkMark(gmailOk && slackOk && teamsOk && erpOk)} 1) Integrations</div>
        <div class="readiness-item">${checkMark(channelOk)} 2) Approval channel</div>
        <div class="readiness-item">${checkMark(policyOk)} 3) AP policies</div>
        <div class="readiness-item">${checkMark(allReady)} 4) Launch</div>
      </div>
      <p class="muted">Follow this order: Integrations → Channel → Policies → Launch.</p>
    </div>

    <div class="panel"><h3>Step 1: Connect integrations</h3>
      <p class="muted">One-click setup for each service.</p>
      <div class="connector-grid">
        <div class="connector-card ${gmailOk ? 'done' : ''}">
          <div class="connector-header"><strong>Gmail</strong>${statusBadge(gmailOk)}</div>
          <p class="muted">Reads invoices from your inbox.</p>
          ${gmailOk ? html`<p class="connector-detail">Auto-connected via extension</p>` : html`<button class="connector-btn" onClick=${connectGmail} disabled=${gmailPending}>Connect Gmail</button>`}
        </div>
        <div class="connector-card ${slackOk ? 'done' : ''}">
          <div class="connector-header"><strong>Slack</strong>${statusBadge(slackOk)}</div>
          <p class="muted">Sends approval requests to your team.</p>
          ${slackOk ? html`<p class="connector-detail">Workspace: ${slack.team_name || 'connected'}</p>` : html`<button class="connector-btn" onClick=${connectSlack}>Connect Slack</button>`}
        </div>
        <div class="connector-card ${teamsOk ? 'done' : ''}">
          <div class="connector-header"><strong>Teams</strong>${statusBadge(teamsOk)}</div>
          <p class="muted">Sends approval cards to Microsoft Teams.</p>
          ${teamsOk ? html`<p class="connector-detail">Webhook: configured</p>` : html`<button class="connector-btn" onClick=${() => onNav('integrations')}>Configure Teams</button>`}
        </div>
      </div>
    </div>

    <div class="panel"><h3>Step 1b: Choose your ERP</h3>
      <p class="muted">Connect the accounting system where bills get posted.</p>
      <div class="connector-grid connector-grid-3">
        ${['quickbooks', 'xero'].map(t => html`
          <div class="connector-card ${erpOk && erpType === t ? 'done' : ''}">
            <div class="connector-header"><strong>${t.charAt(0).toUpperCase() + t.slice(1)}</strong>${erpOk && erpType === t ? statusBadge(true) : ''}</div>
            <p class="muted">${t === 'quickbooks' ? 'QuickBooks Online via OAuth.' : 'Xero via OAuth.'}</p>
            ${erpOk && erpType === t ? html`<p class="connector-detail">Connected</p>` : html`<button class="connector-btn" onClick=${() => connectErp(t)}>Connect</button>`}
          </div>
        `)}
        <div class="connector-card ${erpOk && erpType === 'netsuite' ? 'done' : ''}">
          <div class="connector-header"><strong>NetSuite</strong>${erpOk && erpType === 'netsuite' ? statusBadge(true) : ''}</div>
          <p class="muted">Token-Based Authentication.</p>
          ${erpOk && erpType === 'netsuite' ? html`<p class="connector-detail">Connected</p>` : html`<button class="connector-btn" onClick=${() => setNsVisible(true)}>Setup</button>`}
        </div>
        <div class="connector-card ${erpOk && erpType === 'sap' ? 'done' : ''}">
          <div class="connector-header"><strong>SAP</strong>${erpOk && erpType === 'sap' ? statusBadge(true) : ''}</div>
          <p class="muted">SAP via service-account credentials.</p>
          ${erpOk && erpType === 'sap' ? html`<p class="connector-detail">Connected</p>` : html`<button class="connector-btn" onClick=${() => setSapVisible(true)}>Setup</button>`}
        </div>
      </div>
      ${nsVisible && html`<div class="netsuite-form-panel"><h4>NetSuite Credentials</h4>
        <div class="form-grid">
          <label>Account ID</label><input id="ns-account-id" type="text" placeholder="1234567" />
          <label>Consumer Key</label><input id="ns-consumer-key" type="text" />
          <label>Consumer Secret</label><input id="ns-consumer-secret" type="password" />
          <label>Token ID</label><input id="ns-token-id" type="text" />
          <label>Token Secret</label><input id="ns-token-secret" type="password" />
        </div>
        <div class="row mt-10">
          <button class="connector-btn" onClick=${connectNs} disabled=${nsPending}>${nsPending ? 'Testing...' : 'Test & Connect'}</button>
          <button class="alt" onClick=${() => setNsVisible(false)}>Cancel</button>
        </div>
      </div>`}
      ${sapVisible && html`<div class="netsuite-form-panel"><h4>SAP Credentials</h4>
        <div class="form-grid">
          <label>Base URL</label><input id="sap-base-url" type="text" placeholder="https://..." />
          <label>Username</label><input id="sap-username" type="text" />
          <label>Password</label><input id="sap-password" type="password" />
        </div>
        <div class="row mt-10">
          <button class="connector-btn" onClick=${connectSap} disabled=${sapPending}>${sapPending ? 'Testing...' : 'Test & Connect'}</button>
          <button class="alt" onClick=${() => setSapVisible(false)}>Cancel</button>
        </div>
      </div>`}
    </div>

    <div class="panel ${slackOk ? '' : 'panel-disabled'}"><h3>Step 2: Approval channel</h3>
      <p class="muted">Where should Clearledgr send invoice approval requests in Slack?</p>
      <div class="row">
        <input id="slack-channel-input" placeholder="#finance-approvals" value=${slack.approval_channel || ''} disabled=${!slackOk} />
        <button class="alt" onClick=${saveChannel} disabled=${!slackOk || channelPending}>Save Channel</button>
        ${slackOk && html`<button class="alt" onClick=${testSlack}>Test</button>`}
      </div>
    </div>

    <div class="panel"><h3>Step 3: AP policy readiness</h3>
      <p class="muted">Review policy thresholds before launch.</p>
      <div class="readiness-list"><div class="readiness-item">${checkMark(policyOk)} Policy config loaded</div></div>
      <div class="row"><button class="alt" onClick=${() => onNav('policies')}>Open AP Policies</button></div>
    </div>

    ${gmail.connected && html`<div class="panel autopilot-panel"><h3>Autopilot Status</h3>
      <div class="autopilot-grid">
        <div class="autopilot-item"><span class="autopilot-dot ${wsClass}"></span><div><strong>Gmail Watch</strong><span class="muted">${wsLabel}</span></div></div>
        <div class="autopilot-item"><span class="autopilot-dot ${(dash.total_invoices || 0) > 0 ? 'ap-active' : 'ap-polling'}"></span><div><strong>${dash.total_invoices || 0} invoices</strong><span class="muted">processed</span></div></div>
        <div class="autopilot-item"><div><strong>Last scan</strong><span class="muted">${gmail.last_sync_at ? fmtDateTime(gmail.last_sync_at) : 'Never'}</span></div></div>
        <div class="autopilot-item"><div><strong>Email</strong><span class="muted">${gmail.email || '—'}</span></div></div>
      </div>
    </div>`}

    <div class="panel"><h3>Step 4: Launch readiness</h3>
      <div class="readiness-list">
        <div class="readiness-item">${checkMark(gmailOk)} Gmail</div>
        <div class="readiness-item">${checkMark(slackOk)} Slack</div>
        <div class="readiness-item">${checkMark(erpOk)} ERP (${erpType || 'none'})</div>
        <div class="readiness-item">${checkMark(channelOk)} Approval channel</div>
        <div class="readiness-item">${checkMark(policyOk)} AP policies</div>
      </div>
      <button class="launch-btn" disabled=${!allReady || launchPending} onClick=${launch}>
        ${allReady ? (launchPending ? 'Launching...' : 'Launch Clearledgr') : 'Complete setup above to launch'}
      </button>
    </div>
  `;
}

function ActivityPage({ bootstrap, onRefresh }) {
  const dash = bootstrap?.dashboard || {};
  const events = bootstrap?.recentActivity || [];
  const [refresh, refreshing] = useAction(onRefresh);

  return html`
    <div class="kpi-row">
      <div class="kpi-card"><strong>${dash.total_invoices || 0}</strong><span>Total invoices</span></div>
      <div class="kpi-card kpi-warning"><strong>${dash.pending_approval || 0}</strong><span>Pending approval</span></div>
      <div class="kpi-card kpi-success"><strong>${dash.posted_today || 0}</strong><span>Posted today</span></div>
      <div class="kpi-card kpi-danger"><strong>${dash.rejected_today || 0}</strong><span>Rejected today</span></div>
    </div>
    <div class="kpi-row mt-0">
      <div class="kpi-card"><strong>${dash.auto_approved_rate ? (dash.auto_approved_rate * 100).toFixed(0) + '%' : '—'}</strong><span>Auto-approved</span></div>
      <div class="kpi-card"><strong>${dash.avg_processing_time_hours ? dash.avg_processing_time_hours.toFixed(1) + 'h' : '—'}</strong><span>Avg processing</span></div>
      <div class="kpi-card"><strong>${fmtDollar(dash.total_amount_pending)}</strong><span>Pending amount</span></div>
      <div class="kpi-card kpi-success"><strong>${fmtDollar(dash.total_amount_posted_today)}</strong><span>Posted today</span></div>
    </div>
    <div class="panel"><h3>Recent Activity</h3>
      ${events.length === 0 ? html`<p class="muted">No activity yet. Invoices will appear here once they start processing.</p>` :
        html`<div class="activity-timeline">
          ${events.map((ev, i) => {
            const ts = ev.ts || ev.timestamp || '';
            const badge = eventBadge(ev.event_type || ev.new_state || 'event');
            const payload = typeof ev.payload_json === 'string' ? (() => { try { return JSON.parse(ev.payload_json); } catch { return {}; } })() : (ev.payload_json || {});
            const vendor = ev.vendor_name || payload.vendor_name || '';
            const amount = ev.amount || payload.amount;
            return html`<div key=${i} class="activity-row">
              <div class="activity-time"><span class="activity-date">${fmtDate(ts)}</span> ${fmtTime(ts)}</div>
              <div class="activity-dot ${badge.cls}"></div>
              <div class="activity-body">
                <span class="activity-vendor">${vendor}</span>
                ${amount ? html`<span class="activity-amount">$${Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>` : null}
                <span class="activity-badge ${badge.cls}">${badge.label}</span>
              </div>
            </div>`;
          })}
        </div>`}
      <div class="row mt-10"><button class="alt" onClick=${refresh} disabled=${refreshing}>${refreshing ? 'Loading...' : 'Refresh'}</button></div>
    </div>
  `;
}

function OpsPage({ bootstrap, orgId, onRefresh }) {
  if (!hasOpsAccess(bootstrap)) return html`<div class="panel"><h3>Access required</h3><p class="muted">Ops Console is limited to admin/operator roles.</p></div>`;
  const ops = bootstrap?.ops || {};
  const health = ops.health || {};
  const kpis = ops.kpis || {};
  const retryQueue = Array.isArray(ops.retryQueue) ? ops.retryQueue : [];
  const worklist = Array.isArray(ops.worklist) ? ops.worklist : [];
  const connectorReadiness = ops.connectorReadiness || {};
  const connectorRows = Array.isArray(connectorReadiness?.connectors) ? connectorReadiness.connectors : [];
  const learning = ops.learningCalibration || {};
  const queueLag = Number(health?.queue_lag?.avg_minutes || 0);
  const stuckCount = Number(health?.workflow_stuck_count?.count || 0);
  const approvalLat = Number(health?.approval_latency?.avg_minutes || 0);
  const failRate = Number(health?.post_failure_rate?.rate_24h || 0) * 100;
  const topBlockers = Array.isArray(kpis?.agentic_telemetry?.top_blocker_reasons?.top_reasons) ? kpis.agentic_telemetry.top_blocker_reasons.top_reasons.slice(0, 3) : [];

  const items = worklist;
  const retryFailed = items.filter(i => String(i?.state || '').toLowerCase() === 'failed_post');
  const nudgeApprovals = items.filter(i => ['needs_approval', 'pending_approval'].includes(String(i?.state || '').toLowerCase()));
  const routeLowRisk = items.filter(i => String(i?.state || '').toLowerCase() === 'validated' && Number(i?.confidence || 0) >= 0.95 && !i?.exception_code && !i?.requires_field_review);

  const [runBatch, batchPending] = useAction(async (action) => {
    let selected = [];
    if (action === 'retry_failed_posts') selected = retryFailed;
    if (action === 'nudge_approvals') selected = nudgeApprovals;
    if (action === 'route_low_risk') selected = routeLowRisk;
    if (!selected.length) { toast('No matching invoices.', 'error'); return; }
    const org = encodeURIComponent(orgId);
    let ok = 0, fail = 0;
    for (const item of selected.slice(0, 25)) {
      try {
        if (action === 'retry_failed_posts') await api(`/api/ap/items/${encodeURIComponent(item.id)}/retry-post?organization_id=${org}`, { method: 'POST' });
        else if (action === 'nudge_approvals') await api('/extension/approval-nudge', { method: 'POST', body: JSON.stringify({ email_id: resolveRef(item), organization_id: orgId, user_email: currentEmail(bootstrap), message: 'Admin Ops nudge' }) });
        else if (action === 'route_low_risk') await api('/extension/route-low-risk-approval', { method: 'POST', body: JSON.stringify({ email_id: resolveRef(item), organization_id: orgId, user_email: currentEmail(bootstrap), reason: 'admin_ops_batch' }) });
        ok++;
      } catch { fail++; }
    }
    toast(`Batch: ${ok} succeeded, ${fail} failed`, fail > 0 ? 'error' : 'success');
    onRefresh();
  });

  const [recomputeCalib, calibPending] = useAction(async () => {
    await api('/api/admin/ops/learning-calibration/recompute', { method: 'POST', body: JSON.stringify({ organization_id: orgId, window_days: 180, min_feedback: 20, limit: 5000 }) });
    toast('Learning calibration recomputed.'); onRefresh();
  });

  const [retryJob] = useAction(async (jobId) => {
    await api(`/api/ops/retry-queue/${encodeURIComponent(jobId)}/retry`, { method: 'POST' });
    toast(`Retry requested for job ${jobId}.`); onRefresh();
  });
  const [skipJob] = useAction(async (jobId) => {
    await api(`/api/ops/retry-queue/${encodeURIComponent(jobId)}/skip`, { method: 'POST' });
    toast(`Job ${jobId} skipped.`); onRefresh();
  });
  const [refreshOps, refreshing] = useAction(onRefresh);

  return html`
    <div class="kpi-row">
      <div class="kpi-card"><strong>${queueLag.toFixed(1)}m</strong><span>Queue lag (avg)</span></div>
      <div class="kpi-card"><strong>${approvalLat.toFixed(1)}m</strong><span>Approval latency</span></div>
      <div class="kpi-card"><strong>${fmtRate(failRate)}</strong><span>Posting failure (24h)</span></div>
      <div class="kpi-card ${stuckCount > 0 ? 'kpi-warning' : 'kpi-success'}"><strong>${stuckCount}</strong><span>Stuck workflows</span></div>
    </div>
    <div class="panel"><h3>Top blockers</h3>
      ${topBlockers.length ? html`<ul>${topBlockers.map(e => html`<li>${String(e?.reason || '').replace(/_/g, ' ')} (${e?.count || 0})</li>`)}</ul>` : html`<p class="muted">No blocker telemetry.</p>`}
    </div>
    <div class="panel"><h3>Batch operations</h3>
      <div class="row">
        <button onClick=${() => runBatch('retry_failed_posts')} disabled=${batchPending}>Retry failed (${retryFailed.length})</button>
        <button class="alt" onClick=${() => runBatch('nudge_approvals')} disabled=${batchPending}>Nudge approvals (${nudgeApprovals.length})</button>
        <button class="alt" onClick=${() => runBatch('route_low_risk')} disabled=${batchPending}>Route low-risk (${routeLowRisk.length})</button>
      </div>
    </div>
    <div class="panel"><h3>ERP connector readiness</h3>
      <table class="table"><thead><tr><th>Connector</th><th>Readiness</th><th>Connected</th></tr></thead>
        <tbody>${connectorRows.length ? connectorRows.map(r => html`<tr><td>${r.erp_type}</td><td>${r.readiness_status}</td><td>${r.connection_present ? 'yes' : 'no'}</td></tr>`) : html`<tr><td colspan="3">No data.</td></tr>`}</tbody>
      </table>
    </div>
    <div class="panel"><h3>Learning calibration</h3>
      <p class="muted">Status: <strong>${learning.status || 'not_calibrated'}</strong></p>
      <button class="alt" onClick=${recomputeCalib} disabled=${calibPending}>${calibPending ? 'Recomputing...' : 'Recompute calibration'}</button>
    </div>
    <div class="panel"><h3>Retry queue</h3>
      <table class="table"><thead><tr><th>Job</th><th>Status</th><th>Retries</th><th>Action</th></tr></thead>
        <tbody>${retryQueue.length ? retryQueue.slice(0, 20).map(j => html`<tr><td>${j.id}</td><td>${j.status}</td><td>${j.retry_count || 0}/${j.max_retries || 0}</td>
          <td><button class="alt" onClick=${() => retryJob(j.id)}>Retry</button> <button class="alt" onClick=${() => skipJob(j.id)}>Skip</button></td></tr>`) : html`<tr><td colspan="4">No retry jobs.</td></tr>`}</tbody>
      </table>
    </div>
    <div class="panel"><h3>Debug</h3><button class="alt" onClick=${refreshOps} disabled=${refreshing}>${refreshing ? 'Refreshing...' : 'Refresh Ops'}</button></div>
  `;
}

function IntegrationsPage({ bootstrap, orgId, onRefresh }) {
  const integrations = bootstrap?.integrations || [];
  const slack = integrationByName(bootstrap, 'slack');
  const teams = integrationByName(bootstrap, 'teams');
  const [connectSlack] = useAction(async () => {
    const p = await api('/api/admin/integrations/slack/install/start', { method: 'POST', body: JSON.stringify({ organization_id: orgId, mode: 'per_org', redirect_path: '/console' }) });
    window.location.href = p.auth_url;
  });
  const [saveChannel] = useAction(async () => {
    await api('/api/admin/integrations/slack/channel', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: document.getElementById('slack-channel-input')?.value?.trim() }) });
    toast('Channel saved.'); onRefresh();
  });
  const [testSlackMsg] = useAction(async () => {
    await api('/api/admin/integrations/slack/test', { method: 'POST', body: JSON.stringify({ organization_id: orgId, channel_id: document.getElementById('slack-channel-input')?.value?.trim() }) });
    toast('Test sent to Slack.');
  });
  const [saveWebhook] = useAction(async () => {
    const wh = document.getElementById('teams-webhook-input')?.value?.trim();
    if (!wh) { toast('Webhook URL required.', 'error'); return; }
    await api('/api/admin/integrations/teams/webhook', { method: 'POST', body: JSON.stringify({ organization_id: orgId, webhook_url: wh }) });
    toast('Teams webhook saved.'); onRefresh();
  });
  const [testTeamsMsg] = useAction(async () => {
    await api('/api/admin/integrations/teams/test', { method: 'POST', body: JSON.stringify({ organization_id: orgId }) });
    toast('Test sent to Teams.');
  });

  return html`
    <div class="panel"><table class="table"><thead><tr><th>Integration</th><th>Status</th><th>Mode</th><th>Last sync</th></tr></thead>
      <tbody>${integrations.map(i => html`<tr><td>${i.name}</td><td>${i.status || 'unknown'}</td><td>${i.mode || '-'}</td><td>${i.last_sync_at || '-'}</td></tr>`)}</tbody></table></div>
    <div class="panel"><h3>Slack Setup</h3>
      <div class="row">
        <button onClick=${connectSlack}>Install to Slack</button>
        <input id="slack-channel-input" placeholder="#finance-approvals" value=${slack.approval_channel || ''} />
        <button class="alt" onClick=${saveChannel}>Save Channel</button>
        <button class="alt" onClick=${testSlackMsg}>Send Test Card</button>
      </div>
    </div>
    <div class="panel"><h3>Teams Setup</h3>
      <div class="row">
        <input id="teams-webhook-input" placeholder="https://.../incomingwebhook/..." value=${teams.webhook_url || ''} />
        <button class="alt" onClick=${saveWebhook}>Save Webhook</button>
        <button class="alt" onClick=${testTeamsMsg} disabled=${!teams.connected}>Send Test Card</button>
      </div>
    </div>
  `;
}

function OrganizationPage({ bootstrap, orgId, onRefresh }) {
  const org = bootstrap?.organization || {};
  const [saveOrg] = useAction(async () => {
    await api('/api/admin/org/settings', { method: 'PATCH', body: JSON.stringify({ organization_id: orgId, patch: { organization_name: document.getElementById('org-name-input')?.value?.trim(), domain: document.getElementById('org-domain-input')?.value?.trim(), integration_mode: document.getElementById('org-mode-input')?.value } }) });
    toast('Organization saved.'); onRefresh();
  });
  const [saveJson] = useAction(async () => {
    const patch = JSON.parse(document.getElementById('org-settings-json')?.value);
    await api('/api/admin/org/settings', { method: 'PATCH', body: JSON.stringify({ organization_id: orgId, patch }) });
    toast('Settings JSON saved.'); onRefresh();
  });

  return html`
    <div class="panel"><h3>Organization</h3>
      <label>Name</label><input id="org-name-input" value=${org.name || ''} />
      <label>Domain</label><input id="org-domain-input" value=${org.domain || ''} />
      <label>Integration mode</label>
      <select id="org-mode-input">
        <option value="shared" selected=${org.integration_mode === 'shared'}>shared</option>
        <option value="per_org" selected=${org.integration_mode === 'per_org'}>per_org</option>
      </select>
      <div class="row"><button onClick=${saveOrg}>Save Organization</button></div>
    </div>
    <div class="panel"><h3>Raw Settings JSON</h3>
      <textarea id="org-settings-json">${JSON.stringify(org.settings || {}, null, 2)}</textarea>
      <div class="row"><button class="alt" onClick=${saveJson}>Save Settings JSON</button></div>
    </div>
  `;
}

function PoliciesPage({ bootstrap, orgId, onRefresh }) {
  const policy = bootstrap?.policyPayload || {};
  const configJson = (policy.policy || {}).config_json || {};
  const [savePolicy] = useAction(async () => {
    const config = JSON.parse(document.getElementById('policy-json')?.value);
    await api('/api/admin/policies/ap', { method: 'PUT', body: JSON.stringify({ organization_id: orgId, config, enabled: true }) });
    toast('Policy updated.'); onRefresh();
  });

  return html`<div class="panel"><h3>AP Policy (${policy.policy_name || 'ap_business_v1'})</h3>
    <textarea id="policy-json">${JSON.stringify(configJson, null, 2)}</textarea>
    <div class="row"><button onClick=${savePolicy}>Save Policy</button></div>
  </div>`;
}

function TeamPage({ bootstrap, orgId, onRefresh }) {
  const invites = bootstrap?.teamInvites || [];
  const [createInvite] = useAction(async () => {
    const email = document.getElementById('invite-email')?.value?.trim();
    const role = document.getElementById('invite-role')?.value;
    await api('/api/admin/team/invites', { method: 'POST', body: JSON.stringify({ organization_id: orgId, email, role }) });
    toast(`Invite sent to ${email}.`); onRefresh();
  });
  const [revokeInvite] = useAction(async (id) => {
    await api(`/api/admin/team/invites/${id}/revoke?organization_id=${encodeURIComponent(orgId)}`, { method: 'POST' });
    toast('Invite revoked.'); onRefresh();
  });

  return html`
    <div class="panel"><h3>Invite Teammate</h3>
      <div class="row">
        <input id="invite-email" placeholder="teammate@company.com" />
        <select id="invite-role"><option value="member">member</option><option value="admin">admin</option><option value="viewer">viewer</option></select>
        <button onClick=${createInvite}>Create Invite</button>
      </div>
    </div>
    <div class="panel"><h3>Active Invites</h3>
      <table class="table"><thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Link</th><th></th></tr></thead>
        <tbody>${invites.length ? invites.map(inv => html`<tr><td>${inv.email}</td><td>${inv.role}</td><td>${inv.status}</td>
          <td><a href=${inv.invite_link} target="_blank">Open</a></td>
          <td>${inv.status === 'pending' ? html`<button class="alt" onClick=${() => revokeInvite(inv.id)}>Revoke</button>` : null}</td></tr>`) : html`<tr><td colspan="5">No invites yet.</td></tr>`}</tbody>
      </table>
    </div>
  `;
}

function PlanPage({ bootstrap, orgId, onRefresh }) {
  const sub = bootstrap?.subscription || {};
  const usage = sub.usage || {};
  const usageKeys = Object.keys(usage);
  const [changePlan] = useAction(async (plan) => {
    await api('/api/admin/subscription/plan', { method: 'PATCH', body: JSON.stringify({ organization_id: orgId, plan }) });
    toast(`Plan updated to ${plan}.`); onRefresh();
  });

  return html`
    <div class="panel"><h3>Plan</h3>
      <p><strong>${(sub.plan || 'free').toUpperCase()}</strong> (${sub.status || 'active'})</p>
      <div class="row">
        ${['free', 'trial', 'pro', 'enterprise'].map(p => html`<button class=${p === 'pro' || p === 'enterprise' ? '' : 'alt'} onClick=${() => changePlan(p)}>${p.charAt(0).toUpperCase() + p.slice(1)}</button>`)}
      </div>
    </div>
    <div class="panel"><h3>Usage</h3>
      ${usageKeys.length ? html`<div class="kpi-row">${usageKeys.map(k => html`<div class="kpi-card"><strong>${typeof usage[k] === 'number' ? usage[k].toLocaleString() : usage[k]}</strong><span>${k.replace(/_/g, ' ')}</span></div>`)}</div>` : html`<p class="muted">No usage data yet.</p>`}
    </div>
  `;
}

function HealthPage({ bootstrap }) {
  const health = bootstrap?.health || {};
  return html`
    <div class="panel"><h3>Diagnostics</h3>
      <table class="table"><thead><tr><th>Integration</th><th>Status</th></tr></thead>
        <tbody>${Object.entries(health.integrations || {}).map(([k, v]) => html`<tr><td>${k}</td><td>${typeof v === 'object' ? JSON.stringify(v) : String(v)}</td></tr>`)}</tbody>
      </table>
    </div>
    <div class="panel"><h3>Required actions</h3>
      <ul>${(health.required_actions || []).map(a => html`<li>${a.message}</li>`)}</ul>
      ${!(health.required_actions || []).length && html`<p class="muted">None</p>`}
    </div>
  `;
}

// ==================== MAIN APP ====================

const PAGE_MAP = { setup: SetupPage, activity: ActivityPage, ops: OpsPage, integrations: IntegrationsPage, organization: OrganizationPage, policies: PoliciesPage, team: TeamPage, plan: PlanPage, health: HealthPage };

function AdminApp() {
  const [authed, setAuthed] = useState(false);
  const [bootstrap, setBootstrap] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activePage, setActivePage] = useState(() => {
    const hash = window.location.hash.slice(1);
    return PAGES.some(p => p.id === hash) ? hash : 'setup';
  });

  const orgIdRef = useRef(new URLSearchParams(window.location.search).get('org') || localStorage.getItem('cl_admin_org') || 'default');
  const inviteToken = new URLSearchParams(window.location.search).get('invite_token');

  const navigate = useCallback((pageId) => {
    window.location.hash = pageId;
    setActivePage(pageId);
  }, []);

  useEffect(() => {
    const onHash = () => {
      const h = window.location.hash.slice(1);
      if (PAGES.some(p => p.id === h)) setActivePage(h);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const refreshAll = useCallback(async () => {
    const orgId = orgIdRef.current;
    const org = encodeURIComponent(orgId);
    const data = await api(`/api/admin/bootstrap?organization_id=${org}`);
    const [policyR, invitesR, auditR] = await Promise.allSettled([
      api(`/api/admin/policies/ap?organization_id=${org}`),
      api(`/api/admin/team/invites?organization_id=${org}`),
      api(`/api/ap/audit/recent?organization_id=${org}&limit=30`),
    ]);
    data.policyPayload = policyR.status === 'fulfilled' ? policyR.value : {};
    data.teamInvites = invitesR.status === 'fulfilled' ? (invitesR.value.invites || []) : [];
    data.recentActivity = auditR.status === 'fulfilled' ? (auditR.value.events || auditR.value || []) : [];

    if (hasOpsAccess(data)) {
      const [hR, kR, rR, wR, cR, lR] = await Promise.allSettled([
        api(`/api/ops/tenant-health?organization_id=${org}`),
        api(`/api/ops/ap-kpis?organization_id=${org}`),
        api(`/api/ops/retry-queue?organization_id=${org}&status=all&limit=200`),
        api(`/extension/worklist?organization_id=${org}`),
        api(`/api/admin/ops/connector-readiness?organization_id=${org}`),
        api(`/api/admin/ops/learning-calibration?organization_id=${org}`),
      ]);
      data.ops = {
        health: hR.status === 'fulfilled' ? (hR.value?.health || {}) : {},
        kpis: kR.status === 'fulfilled' ? (kR.value?.kpis || {}) : {},
        retryQueue: rR.status === 'fulfilled' ? (Array.isArray(rR.value?.jobs) ? rR.value.jobs : []) : [],
        worklist: wR.status === 'fulfilled' ? (Array.isArray(wR.value?.items) ? wR.value.items : []) : [],
        connectorReadiness: cR.status === 'fulfilled' ? (cR.value?.connector_readiness || {}) : {},
        learningCalibration: lR.status === 'fulfilled' ? (lR.value?.snapshot || {}) : {},
      };
    } else {
      data.ops = {};
    }

    setBootstrap(data);
    return data;
  }, []);

  // Boot
  useEffect(() => {
    const url = new URLSearchParams(window.location.search);
    const orgId = url.get('org') || localStorage.getItem('cl_admin_org') || 'default';
    orgIdRef.current = orgId;
    localStorage.setItem('cl_admin_org', orgId);

    // Handle page param
    const requestedPage = String(url.get('page') || '').trim().toLowerCase();
    if (requestedPage && PAGES.some(p => p.id === requestedPage)) navigate(requestedPage);

    // Handle OAuth code exchange
    const authCode = url.get('auth_code');
    const bootFlow = async () => {
      if (authCode) {
        try {
          const ex = await api('/auth/google/exchange', { method: 'POST', body: JSON.stringify({ auth_code: authCode }), headers: {}, silent: true });
          if (!ex?.access_token) throw new Error('No token');
          const clean = new URL(window.location.href);
          clean.searchParams.delete('auth_code');
          window.history.replaceState({}, '', clean.toString());
        } catch {
          clearSession();
          setLoading(false);
          return;
        }
      }
      // Handle post-OAuth toasts
      const connected = url.get('connected');
      if (connected) {
        const clean = new URL(window.location.href);
        ['connected', 'org'].forEach(k => clean.searchParams.delete(k));
        window.history.replaceState({}, '', clean.toString());
        setTimeout(() => toast(`${connected.charAt(0).toUpperCase() + connected.slice(1)} connected!`, 'success'), 500);
      }

      try {
        await refreshAll();
        setAuthed(true);
      } catch {
        clearSession();
      }
      setLoading(false);
    };
    bootFlow();
  }, []);

  const handleLogin = useCallback(async () => {
    orgIdRef.current = new URLSearchParams(window.location.search).get('org') || orgIdRef.current;
    localStorage.setItem('cl_admin_org', orgIdRef.current);
    try {
      await refreshAll();
      setAuthed(true);
    } catch {
      clearSession();
    }
  }, [refreshAll]);

  const handleLogout = useCallback(() => {
    api('/auth/logout', { method: 'POST', silent: true }).catch(() => {});
    clearSession();
    setAuthed(false);
    setBootstrap(null);
  }, []);

  if (loading) return html`<div class="auth-shell"><div class="auth-card"><p class="muted">Loading...</p></div></div>`;

  if (!authed) return html`<${AuthShell} onLogin=${handleLogin} inviteToken=${inviteToken} />`;

  const visiblePages = PAGES.filter(p => !(p.id === 'ops' && !hasOpsAccess(bootstrap)));
  const page = visiblePages.find(p => p.id === activePage) || visiblePages[0];
  const PageComponent = PAGE_MAP[page.id] || SetupPage;
  const orgId = orgIdRef.current;
  const orgLabel = bootstrap ? `${bootstrap.organization?.name || orgId} (${bootstrap.organization?.id || orgId})` : orgId;

  return html`
    <div class="shell">
      <${SideNav} pages=${visiblePages} active=${page.id} onNav=${navigate} orgLabel=${orgLabel} onLogout=${handleLogout} />
      <main class="content">
        <header class="topbar"><h2>${page.title}</h2><p>${page.subtitle}</p></header>
        <section>
          <${ErrorBoundary} fallback="This page encountered an error.">
            <${PageComponent} bootstrap=${bootstrap} orgId=${orgId} onRefresh=${refreshAll} onNav=${navigate} />
          <//>
        </section>
      </main>
    </div>
  `;
}

// ==================== MOUNT ====================

function App() {
  return html`
    <${ErrorBoundary} fallback="Clearledgr Admin failed to load.">
      <${AdminApp} />
    <//>
    <${Toast} />
  `;
}

render(html`<${App} />`, document.getElementById('app'));
