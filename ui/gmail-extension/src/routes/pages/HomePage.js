/**
 * Home Page — work-first Gmail hub modeled after Streak Home.
 * Primary goal: help operators resume work quickly without turning Gmail
 * into a settings-heavy dashboard.
 */
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import {
  integrationByName,
  fmtDateTime,
  hasAdminAccess,
  hasOpsAccess,
  useAction,
} from '../route-helpers.js';
import {
  PIPELINE_BUILTIN_SLICES,
  activatePipelineSlice,
  getBootstrappedPipelinePreferences,
  clearPipelineNavigation,
  getPinnedPipelineViews,
  getStarterPipelineViews,
  hasMeaningfulPipelinePreferences,
  normalizePipelinePreferences,
  pipelinePreferencesEqual,
  readPipelinePreferences,
  writePipelinePreferences,
} from '../pipeline-views.js';

const html = htm.bind(h);

const HOME_PIPELINE_SHORTCUTS = [
  'waiting_on_approval',
  'ready_to_post',
  'blocked_exception',
  'needs_info',
];

function QuickLinkRow({ label, detail, actionLabel = 'Open', onClick }) {
  return html`<button
    onClick=${onClick}
    style="
      display:flex;align-items:center;justify-content:space-between;gap:14px;
      width:100%;padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);
      background:var(--surface);cursor:pointer;font-family:inherit;text-align:left;
    "
  >
    <span style="min-width:0;flex:1">
      <strong style="display:block;font-size:13px;margin-bottom:2px">${label}</strong>
      <span class="muted" style="display:block;font-size:12px;line-height:1.45">${detail}</span>
    </span>
    <span class="muted" style="font-size:12px;font-weight:700;white-space:nowrap">${actionLabel}</span>
  </button>`;
}

function QuickAccessCard({ label, detail, meta, onClick }) {
  return html`<button class="home-quick-card" onClick=${onClick}>
    <div>
      <div class="home-quick-meta">${meta || 'Quick access'}</div>
      <strong class="home-quick-title">${label}</strong>
      <div class="muted home-quick-detail">${detail}</div>
    </div>
    <div class="muted" style="font-size:10px;font-weight:700">Open</div>
  </button>`;
}

function RecentActivity({ entries = [], navigate, canOpenActivity = false }) {
  const rows = Array.isArray(entries) ? entries.slice(0, 5) : [];
  return html`<div class="panel">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px">
      <div>
        <h3 style="margin:0 0 4px">Recent updates</h3>
        <p class="muted" style="margin:0">A quick look at what changed recently.</p>
      </div>
      ${canOpenActivity
        ? html`<button class="btn-secondary btn-sm" onClick=${() => navigate('clearledgr/activity')}>Open activity</button>`
        : null}
    </div>
    ${rows.length
      ? html`<div style="display:grid;gap:8px">
          ${rows.map((entry, index) => html`<div key=${index} style="padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:4px">
              <strong style="font-size:13px">${entry?.title || entry?.action || 'Update'}</strong>
              ${entry?.timestamp || entry?.created_at
                ? html`<span class="muted" style="font-size:12px">${fmtDateTime(entry.timestamp || entry.created_at)}</span>`
                : null}
            </div>
            <div class="muted" style="font-size:12px;line-height:1.45">${entry?.detail || entry?.summary || 'Recent activity is available.'}</div>
          </div>`)}
        </div>`
      : html`<div class="muted" style="font-size:13px">No recent updates yet.</div>`}
  </div>`;
}

function UpcomingTaskRow({ task, onClick }) {
  const amount = Number(task?.amount);
  const amountLabel = Number.isFinite(amount)
    ? `${task?.currency || 'USD'} ${amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : 'Amount unavailable';
  const statusLabel = String(task?.status || '').toLowerCase() === 'overdue'
    ? 'Overdue'
    : String(task?.status || '').toLowerCase() === 'today'
      ? 'Today'
      : 'Open';

  return html`<${QuickLinkRow}
    label=${task?.title || 'Follow-up'}
    detail=${`${task?.vendor_name || 'Unknown vendor'} · ${task?.invoice_number || 'No invoice #'} · ${amountLabel}${task?.detail ? ` · ${task.detail}` : ''}`}
    actionLabel=${statusLabel}
    onClick=${onClick}
  />`;
}

function SetupNotice({
  adminAccess,
  missingSetup,
  navigate,
  connectGmail,
  gmailPending,
  showGmailAction,
  showRulesAction,
}) {
  if (!Array.isArray(missingSetup) || missingSetup.length === 0) return null;
  return html`<div class="home-banner warning">
    <div style="min-width:0;flex:1">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">
        <span style="font-size:11px;font-weight:700;padding:4px 9px;border-radius:999px;background:#FEF3C7;color:#A16207">Setup still needed</span>
      </div>
      <strong style="display:block;font-size:16px;line-height:1.35;margin-bottom:4px">Finish setup to keep invoices moving</strong>
      <p class="muted" style="margin:0;max-width:none">
        ${adminAccess
          ? `${missingSetup.join(', ')} still need attention before invoices can move all the way through the flow.`
          : `${missingSetup.join(', ')} still need attention. Ask an admin to finish setup.`}
      </p>
    </div>
    ${adminAccess && html`<div style="display:flex;gap:8px;flex-wrap:wrap">
      ${showGmailAction
        ? html`<button class="btn-primary btn-sm" onClick=${connectGmail} disabled=${gmailPending}>${gmailPending ? 'Working…' : 'Connect Gmail'}</button>`
        : null}
      <button class="btn-secondary btn-sm" onClick=${() => navigate('clearledgr/connections')}>Open connections</button>
      ${showRulesAction
        ? html`<button class="btn-secondary btn-sm" onClick=${() => navigate('clearledgr/rules')}>Review rules</button>`
        : null}
    </div>`}
  </div>`;
}

function EmptyPanelState({ text }) {
  return html`<div style="
    min-height:220px;display:flex;align-items:center;justify-content:center;text-align:center;
    border:1px solid var(--border);border-radius:var(--radius-md);background:linear-gradient(180deg,#FFFFFF 0%, #FAFAF8 100%);
    color:var(--ink-secondary);font-size:14px;padding:24px;
  ">
    ${text}
  </div>`;
}

function QuickAccessStrip({ items = [] }) {
  if (!Array.isArray(items) || items.length === 0) return null;
  return html`
    <div class="home-eyebrow">Quick access</div>
    <div class="home-quick-row">
      ${items.map((item) => html`
        <${QuickAccessCard}
          key=${item.key}
          label=${item.label}
          detail=${item.detail}
          meta=${item.meta}
          onClick=${item.onClick}
        />
      `)}
    </div>
  `;
}

function SectionPanel({ title, detail, actionLabel = '', onAction, children, panelMinHeight = 0 }) {
  return html`<div class="panel">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px;flex-wrap:wrap">
      <div>
        <h3 style="margin:0 0 4px">${title}</h3>
        ${detail ? html`<p class="muted" style="margin:0">${detail}</p>` : null}
      </div>
      ${actionLabel
        ? html`<button class="btn-secondary btn-sm" onClick=${onAction}>${actionLabel}</button>`
        : null}
    </div>
    <div style=${panelMinHeight ? `min-height:${panelMinHeight}px` : ''}>${children}</div>
  </div>`;
}

export default function HomePage({
  api,
  bootstrap,
  toast,
  orgId,
  userEmail,
  oauthBridge,
  navigate,
}) {
  const gmail = integrationByName(bootstrap, 'gmail');
  const slack = integrationByName(bootstrap, 'slack');
  const teams = integrationByName(bootstrap, 'teams');
  const erp = integrationByName(bootstrap, 'erp');
  const dashboard = bootstrap?.dashboard || {};
  const recentActivity = bootstrap?.recentActivity || dashboard?.recent_activity || [];

  const rawName = (userEmail || '').split('@')[0] || '';
  const firstName = rawName ? rawName.charAt(0).toUpperCase() + rawName.slice(1).split(/[._-]/)[0] : '';
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';

  const policyConfig = bootstrap?.policyPayload?.policy?.config_json || {};
  const adminAccess = hasAdminAccess(bootstrap);
  const opsAccess = hasOpsAccess(bootstrap);
  const pipelineScope = { orgId, userEmail };
  const [pipelinePrefs, setPipelinePrefs] = useState(() => readPipelinePreferences(pipelineScope));
  const [upcomingPayload, setUpcomingPayload] = useState({ summary: {}, tasks: [] });
  const bootstrapPipelinePrefs = getBootstrappedPipelinePreferences(bootstrap);

  const pinnedPipelineViews = getPinnedPipelineViews(pipelinePrefs).slice(0, 3);
  const starterSavedViews = getStarterPipelineViews(pipelinePrefs)
    .filter((view) => !pinnedPipelineViews.some((pinnedView) => pinnedView.id === view.id && pinnedView.scope === view.scope))
    .slice(0, 3);
  const starterPipelineSlices = HOME_PIPELINE_SHORTCUTS
    .map((sliceId) => PIPELINE_BUILTIN_SLICES.find((slice) => slice.id === sliceId))
    .filter(Boolean);

  const gmailReconnectRequired = Boolean(gmail.connected && (gmail.requires_reconnect || gmail.durable === false));
  const gmailOk = Boolean(gmail.connected && !gmailReconnectRequired);
  const slackOk = Boolean(slack.connected);
  const teamsOk = Boolean(teams.connected);
  const approvalSurfaceOk = slackOk || teamsOk;
  const erpOk = Boolean(erp.connected);
  const policyOk = Boolean(policyConfig && Object.keys(policyConfig).length > 0);
  const allReady = gmailOk && approvalSurfaceOk && erpOk && policyOk;
  const lastScanAt = dashboard?.last_scan_at || dashboard?.lastScanAt || bootstrap?.health?.last_scan_at || '';

  const missingSetup = [];
  if (!gmailOk) missingSetup.push(gmailReconnectRequired ? 'Gmail reconnect' : 'Gmail');
  if (!approvalSurfaceOk) missingSetup.push('Approval channel');
  if (!erpOk) missingSetup.push('ERP');
  if (!policyOk) missingSetup.push('Approval rules');

  const [connectGmail, gmailPending] = useAction(async () => {
    const payload = await api('/api/workspace/integrations/gmail/connect/start', {
      method: 'POST',
      body: JSON.stringify({ organization_id: orgId, redirect_path: '/workspace' }),
    });
    if (payload?.auth_url) {
      oauthBridge.startOAuth(payload.auth_url, 'gmail');
      return;
    }
    navigate('clearledgr/connections');
  });

  useEffect(() => {
    setPipelinePrefs(readPipelinePreferences(pipelineScope));
  }, [pipelineScope]);

  useEffect(() => {
    const local = readPipelinePreferences(pipelineScope);
    const remote = bootstrapPipelinePrefs ? normalizePipelinePreferences(bootstrapPipelinePrefs) : null;
    if (remote && hasMeaningfulPipelinePreferences(remote) && !pipelinePreferencesEqual(local, remote)) {
      const next = writePipelinePreferences(pipelineScope, remote);
      setPipelinePrefs(next);
      return;
    }
    setPipelinePrefs(local);
  }, [bootstrapPipelinePrefs, pipelineScope]);

  useEffect(() => {
    if (!opsAccess) {
      setUpcomingPayload({ summary: {}, tasks: [] });
      return;
    }
    api(`/api/ap/items/upcoming?organization_id=${encodeURIComponent(orgId)}&limit=4`, { silent: true })
      .then((data) => {
        setUpcomingPayload({
          summary: data?.summary || {},
          tasks: Array.isArray(data?.tasks) ? data.tasks.slice(0, 4) : [],
        });
      })
      .catch(() => {
        setUpcomingPayload({ summary: {}, tasks: [] });
      });
  }, [api, orgId, opsAccess]);

  const openPipelineSlice = (sliceId) => {
    clearPipelineNavigation(pipelineScope);
    activatePipelineSlice(pipelineScope, sliceId);
    setPipelinePrefs(readPipelinePreferences(pipelineScope));
    navigate('clearledgr/pipeline');
  };

  const openSavedPipelineView = (view) => {
    if (!view?.snapshot) return;
    clearPipelineNavigation(pipelineScope);
    writePipelinePreferences(pipelineScope, view.snapshot);
    setPipelinePrefs(readPipelinePreferences(pipelineScope));
    navigate('clearledgr/pipeline');
  };

  const upcomingSummary = Number(upcomingPayload?.summary?.total || 0);
  const savedOrStarterViews = pinnedPipelineViews.length ? pinnedPipelineViews : starterSavedViews;
  const quickAccessItems = [
    {
      key: 'pipeline',
      label: 'Pipeline',
      detail: 'Open the full invoice queue.',
      meta: 'Queue',
      onClick: () => navigate('clearledgr/pipeline'),
    },
    opsAccess
      ? {
          key: 'review',
          label: 'Review',
          detail: 'Work the records that need a closer look.',
          meta: 'Queue',
          onClick: () => navigate('clearledgr/review'),
        }
      : null,
    opsAccess
      ? {
          key: 'upcoming',
          label: 'Upcoming',
          detail: upcomingSummary > 0 ? `${upcomingSummary} item${upcomingSummary === 1 ? '' : 's'} need attention next.` : 'Nothing is due right now.',
          meta: 'Follow-up',
          onClick: () => navigate('clearledgr/upcoming'),
        }
      : null,
    ...savedOrStarterViews.slice(0, 2).map((view) => ({
      key: `view:${view.scope || 'starter'}:${view.id}`,
      label: view.name || 'Saved view',
      detail: view.description || 'Open this saved view.',
      meta: 'View',
      onClick: () => openSavedPipelineView(view),
    })),
    ...starterPipelineSlices.slice(0, 3).map((slice) => ({
      key: `slice:${slice.id}`,
      label: slice.label,
      detail: slice.description,
      meta: 'Slice',
      onClick: () => openPipelineSlice(slice.id),
    })),
  ].filter(Boolean).slice(0, 7);

  return html`
    <div class="home-hero">
      <div class="home-hero-copy">
        <h2 style="font-family:var(--font-display);font-size:40px;font-weight:600;letter-spacing:-0.03em;line-height:1.05;margin:0 0 8px;color:var(--ink)">
          Welcome to Clearledgr
        </h2>
        <p style="font-size:15px;color:var(--ink-secondary);margin:0 0 12px">
          ${allReady
            ? 'Pipeline is your AP control plane. Use Gmail for the active record when context matters.'
            : `${greeting}${firstName ? `, ${firstName}` : ''}. Finish setup, then use Pipeline to pick up invoice work.`}
        </p>
        <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
          <span style="font-size:12px;padding:4px 10px;border-radius:999px;background:${allReady ? '#ECFDF5' : '#FEFCE8'};color:${allReady ? '#047857' : '#A16207'}">
            ${allReady ? 'Ready to work' : 'Setup incomplete'}
          </span>
          ${lastScanAt
            ? html`<span style="font-size:12px;padding:4px 10px;border-radius:999px;background:var(--bg);color:var(--ink-secondary)">Last scan ${fmtDateTime(lastScanAt)}</span>`
            : null}
        </div>
      </div>
    </div>

    ${!allReady
      ? html`<${SetupNotice}
          adminAccess=${adminAccess}
          missingSetup=${missingSetup}
          navigate=${navigate}
          connectGmail=${connectGmail}
          gmailPending=${gmailPending}
          showGmailAction=${!gmailOk}
          showRulesAction=${!policyOk}
        />`
      : null}

    <${QuickAccessStrip} items=${quickAccessItems} />

    <div class="home-panel-grid">
      ${opsAccess
        ? html`<${SectionPanel}
            title="Upcoming"
            detail=${upcomingSummary > 0
              ? `${upcomingSummary} thing${upcomingSummary === 1 ? '' : 's'} need attention next.`
              : 'Nothing is due right now.'}
            actionLabel="Open upcoming"
            onAction=${() => navigate('clearledgr/upcoming')}
            panelMinHeight=${220}
          >
            ${Array.isArray(upcomingPayload?.tasks) && upcomingPayload.tasks.length > 0
              ? html`<div style="display:grid;gap:10px">
                  ${upcomingPayload.tasks.map((task) => html`
                    <${UpcomingTaskRow}
                      key=${task.id}
                      task=${task}
                      onClick=${() => navigate('clearledgr/upcoming')}
                    />
                  `)}
                </div>`
              : html`<${EmptyPanelState} text="Clearledgr will show the next items that need attention here." />`}
          </${SectionPanel}>`
        : null}

      <${SectionPanel}
        title=${savedOrStarterViews.length > 0 ? 'Saved views' : 'Queue slices'}
        detail=${savedOrStarterViews.length > 0
          ? 'Open the views you come back to most.'
          : 'Jump straight to the part of the queue you need.'}
        actionLabel="Open pipeline"
        onAction=${() => navigate('clearledgr/pipeline')}
        panelMinHeight=${220}
      >
        <div style="display:grid;gap:10px">
          ${(savedOrStarterViews.length > 0 ? savedOrStarterViews : starterPipelineSlices).map((entry) => html`
            <${QuickLinkRow}
              key=${savedOrStarterViews.length > 0 ? `${entry.scope || 'starter'}:${entry.id}` : entry.id}
              label=${savedOrStarterViews.length > 0 ? (entry.name || 'Saved view') : entry.label}
              detail=${savedOrStarterViews.length > 0 ? (entry.description || 'Open this saved view.') : entry.description}
              onClick=${() => (savedOrStarterViews.length > 0 ? openSavedPipelineView(entry) : openPipelineSlice(entry.id))}
            />
          `)}
        </div>
      </${SectionPanel}>

      <div class="home-panel-span">
        <${RecentActivity}
          entries=${recentActivity}
          navigate=${navigate}
          canOpenActivity=${opsAccess}
        />
      </div>
    </div>
  `;
}
