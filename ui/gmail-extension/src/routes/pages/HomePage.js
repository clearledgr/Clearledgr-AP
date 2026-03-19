/**
 * Home Page — lightweight Gmail-native launch hub.
 * Keeps setup reachable without turning Gmail into a separate dashboard.
 */
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { integrationByName, fmtDateTime, hasAdminAccess, hasOpsAccess, useAction } from '../route-helpers.js';
import {
  getRoutePreferenceState,
  getVisibleNavRoutes,
  hideRoute,
  pinRoute,
  resetRoutePreferences,
  showRoute,
  unpinRoute,
} from '../route-registry.js';
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
  'needs_info',
  'blocked_exception',
  'failed_post',
  'due_soon',
  'overdue',
];

const WORKFLOW_SURFACE_ROUTE_IDS = [
  'clearledgr/upcoming',
  'clearledgr/vendors',
  'clearledgr/templates',
  'clearledgr/reports',
];

function StatusRow({ label, ready, detail, actionLabel, onAction, pending = false }) {
  return html`<div style="
    display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;
    padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);
  ">
    <div>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap">
        <strong style="font-size:14px">${label}</strong>
        <span style="
          font-size:11px;font-weight:700;padding:3px 8px;border-radius:999px;
          background:${ready ? '#ECFDF5' : '#FEFCE8'};
          color:${ready ? '#047857' : '#A16207'};
        ">${ready ? 'Ready' : 'Needs setup'}</span>
      </div>
      <div class="muted" style="font-size:12px">${detail}</div>
    </div>
    ${actionLabel
      ? html`<button class="alt" onClick=${onAction} disabled=${pending} style="padding:8px 12px;font-size:12px">${pending ? 'Working…' : actionLabel}</button>`
      : null}
  </div>`;
}

function RoutePreferenceRow({ route, preferenceState, onPin, onUnpin, onHide, onShow }) {
  const badgeLabel = preferenceState.hidden
    ? 'Hidden'
    : preferenceState.pinned
      ? 'Pinned'
      : preferenceState.defaultPinned
        ? 'Default'
        : 'Available';
  const badgeTone = preferenceState.hidden
    ? 'background:#FEF2F2;color:#B91C1C;border-color:#FECACA;'
    : preferenceState.visible
      ? 'background:#ECFDF5;color:#047857;border-color:#A7F3D0;'
      : 'background:#F8FAFC;color:#475569;border-color:#CBD5E1;';

  return html`<div style="
    display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;
    padding:14px 16px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);
  ">
    <div style="min-width:0">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap">
        <strong style="font-size:14px">${route.title}</strong>
        <span style="font-size:11px;font-weight:600;padding:4px 8px;border:1px solid var(--border);border-radius:999px;${badgeTone}">${badgeLabel}</span>
      </div>
      <div class="muted" style="font-size:12px">${route.subtitle}</div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
      ${preferenceState.hidden
        ? html`<button class="alt" onClick=${onShow} style="padding:8px 12px;font-size:12px">Show</button>`
        : preferenceState.pinned
          ? html`<button class="alt" onClick=${onUnpin} style="padding:8px 12px;font-size:12px">Unpin</button>`
          : !preferenceState.visible
            ? html`<button class="alt" onClick=${onPin} style="padding:8px 12px;font-size:12px">Pin</button>`
            : null}
      ${preferenceState.canHide && preferenceState.visible
        ? html`<button class="alt" onClick=${onHide} style="padding:8px 12px;font-size:12px">Hide</button>`
        : null}
      ${!preferenceState.canHide && preferenceState.visible
        ? html`<span class="muted" style="font-size:12px;font-weight:600">Always shown</span>`
        : null}
    </div>
  </div>`;
}

function LaunchSummary({ allReady, approvalsReady, erpReady, lastScanAt, navigate, adminAccess }) {
  const summary = allReady
    ? 'Clearledgr is ready to work invoices from Gmail through approval and ERP posting.'
    : 'Finish the missing AP setup steps so operators can stay in Gmail and work invoices end-to-end.';
  return html`<div class="panel" style="padding:18px 20px">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap">
      <div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">
          <span style="
            font-size:11px;font-weight:700;padding:4px 9px;border-radius:999px;
            background:${allReady ? '#ECFDF5' : '#FEFCE8'};
            color:${allReady ? '#047857' : '#A16207'};
          ">${allReady ? 'AP live in Gmail' : 'AP setup in progress'}</span>
          ${lastScanAt ? html`<span class="muted" style="font-size:12px">Last scan ${fmtDateTime(lastScanAt)}</span>` : null}
        </div>
        <h3 style="margin:0 0 6px">Run AP from Gmail</h3>
        <p class="muted" style="margin:0;max-width:560px">${summary}</p>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button onClick=${() => navigate('clearledgr/pipeline')}>Open pipeline</button>
        ${adminAccess && html`<button class="alt" onClick=${() => navigate('clearledgr/connections')}>Review connections</button>`}
      </div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
      <span style="font-size:12px;padding:4px 10px;border-radius:999px;background:${approvalsReady ? '#ECFDF5' : '#FEFCE8'};color:${approvalsReady ? '#047857' : '#A16207'}">
        ${approvalsReady ? 'Approval surface ready' : 'Approval surface missing'}
      </span>
      <span style="font-size:12px;padding:4px 10px;border-radius:999px;background:${erpReady ? '#ECFDF5' : '#FEFCE8'};color:${erpReady ? '#047857' : '#A16207'}">
        ${erpReady ? 'ERP ready' : 'ERP missing'}
      </span>
    </div>
  </div>`;
}

function RecentActivity({ entries = [], navigate, canOpenActivity = false }) {
  const rows = Array.isArray(entries) ? entries.slice(0, 5) : [];
  return html`<div class="panel">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px">
      <div>
        <h3 style="margin:0 0 4px">Recent activity</h3>
        <p class="muted" style="margin:0">What changed recently in AP.</p>
      </div>
      ${canOpenActivity && html`<button class="alt" onClick=${() => navigate('clearledgr/activity')} style="padding:8px 12px;font-size:12px">Open activity</button>`}
    </div>
    ${rows.length
      ? html`<div style="display:grid;gap:8px">
          ${rows.map((entry, index) => html`<div key=${index} style="padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:4px">
              <strong style="font-size:13px">${entry?.title || entry?.action || 'AP update'}</strong>
              ${entry?.timestamp || entry?.created_at
                ? html`<span class="muted" style="font-size:12px">${fmtDateTime(entry.timestamp || entry.created_at)}</span>`
                : null}
            </div>
            <div class="muted" style="font-size:12px;line-height:1.45">${entry?.detail || entry?.summary || 'Recent AP activity is available.'}</div>
          </div>`)}
        </div>`
      : html`<div class="muted" style="font-size:13px">No recent activity yet.</div>`}
  </div>`;
}

function QueueShortcutRow({ label, detail, onClick }) {
  return html`<button
    onClick=${onClick}
    style="
      display:flex;align-items:center;justify-content:space-between;gap:14px;
      width:100%;padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);
      background:var(--surface);cursor:pointer;font-family:inherit;text-align:left;
    "
  >
    <span>
      <strong style="display:block;font-size:13px;margin-bottom:2px">${label}</strong>
      <span class="muted" style="font-size:12px">${detail}</span>
    </span>
    <span class="muted" style="font-size:12px;font-weight:700">Open</span>
  </button>`;
}

function SupportPageRow({ label, detail, onClick }) {
  return html`<div style="
    display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;
    padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);
  ">
    <div>
      <strong style="display:block;font-size:13px;margin-bottom:2px">${label}</strong>
      <span class="muted" style="font-size:12px">${detail}</span>
    </div>
    <button class="alt" onClick=${onClick} style="padding:8px 12px;font-size:12px">Open</button>
  </div>`;
}

function UpcomingTaskRow({ task, onClick }) {
  const amount = Number(task?.amount);
  const amountLabel = Number.isFinite(amount)
    ? `${task?.currency || 'USD'} ${amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : 'Amount unavailable';
  return html`<button
    onClick=${onClick}
    style="
      display:flex;align-items:flex-start;justify-content:space-between;gap:14px;
      width:100%;padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);
      background:var(--surface);cursor:pointer;font-family:inherit;text-align:left;
    "
  >
    <span style="min-width:0;flex:1">
      <strong style="display:block;font-size:13px;margin-bottom:2px">${task?.title || 'AP follow-up'}</strong>
      <span class="muted" style="display:block;font-size:12px;line-height:1.45">
        ${task?.vendor_name || 'Unknown vendor'} · ${task?.invoice_number || 'No invoice #'} · ${amountLabel}
      </span>
      <span class="muted" style="display:block;font-size:12px;line-height:1.45;margin-top:4px">${task?.detail || 'Open this follow-up in Upcoming.'}</span>
    </span>
    <span class="muted" style="font-size:12px;font-weight:700;white-space:nowrap">${task?.status === 'overdue' ? 'Overdue' : 'Open'}</span>
  </button>`;
}

export default function HomePage({
  api,
  bootstrap,
  toast,
  orgId,
  userEmail,
  oauthBridge,
  navigate,
  routePreferences = { pinned: [], hidden: [] },
  availableRoutes = [],
  updateRoutePreferences,
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
  const routeOptions = {
    includeAdmin: adminAccess,
    includeOps: hasOpsAccess(bootstrap),
  };
  const pipelineScope = { orgId, userEmail };
  const [pipelinePrefs, setPipelinePrefs] = useState(() => readPipelinePreferences(pipelineScope));
  const [upcomingPayload, setUpcomingPayload] = useState({ summary: {}, tasks: [] });
  const bootstrapPipelinePrefs = getBootstrappedPipelinePreferences(bootstrap);
  const pinnedPipelineViews = getPinnedPipelineViews(pipelinePrefs).slice(0, 4);
  const starterSavedViews = getStarterPipelineViews(pipelinePrefs)
    .filter((view) => !pinnedPipelineViews.some((pinnedView) => pinnedView.id === view.id && pinnedView.scope === view.scope))
    .slice(0, 3);
  const starterPipelineSlices = HOME_PIPELINE_SHORTCUTS
    .map((sliceId) => PIPELINE_BUILTIN_SLICES.find((slice) => slice.id === sliceId))
    .filter(Boolean);
  const gmailOk = Boolean(gmail.connected);
  const slackOk = Boolean(slack.connected);
  const teamsOk = Boolean(teams.connected);
  const approvalSurfaceOk = slackOk || teamsOk;
  const erpOk = Boolean(erp.connected);
  const policyOk = Boolean(policyConfig && Object.keys(policyConfig).length > 0);
  const allReady = gmailOk && approvalSurfaceOk && erpOk && policyOk;
  const lastScanAt = dashboard?.last_scan_at || dashboard?.lastScanAt || bootstrap?.health?.last_scan_at || '';

  const supportRoutes = getVisibleNavRoutes(routePreferences, routeOptions)
    .filter((route) => !['clearledgr/home', 'clearledgr/pipeline'].includes(route.id))
    .slice(0, 4);
  const workflowSupportRoutes = availableRoutes
    .filter((route) => WORKFLOW_SURFACE_ROUTE_IDS.includes(route.id))
    .filter((route) => !route.adminOnly || adminAccess);
  const customizableRoutes = availableRoutes;

  const [connectGmail, gmailPending] = useAction(async () => {
    const authUrl = bootstrap?.gmail_auth_url || bootstrap?.integrations?.find?.((it) => it.type === 'gmail')?.auth_url;
    if (authUrl) {
      oauthBridge.startOAuth(authUrl, 'gmail');
      return;
    }
    navigate('clearledgr/connections');
  });
  const [connectSlack, slackPending] = useAction(async () => {
    const authUrl = bootstrap?.slack_auth_url || bootstrap?.integrations?.find?.((it) => it.type === 'slack')?.auth_url;
    if (authUrl) {
      oauthBridge.startOAuth(authUrl, 'slack');
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
    if (!routeOptions.includeOps) {
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
  }, [api, orgId, routeOptions.includeOps]);

  async function applyRoutePreferences(nextPreferences, message) {
    if (typeof updateRoutePreferences !== 'function') return;
    await updateRoutePreferences(nextPreferences);
    if (message) toast(message);
  }

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

  const openUpcoming = () => {
    navigate('clearledgr/upcoming');
  };

  return html`
    <div style="margin-bottom:22px">
      <h2 style="font-family:var(--font-display);font-size:24px;font-weight:700;letter-spacing:-0.02em;margin:0 0 4px;color:var(--ink)">
        ${greeting}${firstName ? ', ' + firstName : ''}
      </h2>
      <p style="font-size:13px;color:var(--ink-muted);margin:0">Clearledgr keeps AP work inside Gmail.</p>
    </div>

    <${LaunchSummary}
      allReady=${allReady}
      approvalsReady=${approvalSurfaceOk}
      erpReady=${erpOk}
      lastScanAt=${lastScanAt}
      navigate=${navigate}
      adminAccess=${adminAccess}
    />

    ${routeOptions.includeOps && html`
      <div class="panel">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px">
          <div>
            <h3 style="margin:0 0 4px">Upcoming follow-ups</h3>
            <p class="muted" style="margin:0">
              ${Number(upcomingPayload?.summary?.total || 0) > 0
                ? `${Number(upcomingPayload.summary.total || 0).toLocaleString()} follow-ups are due across approvals, vendor replies, posting, and blockers.`
                : 'No AP follow-ups are due right now.'}
            </p>
          </div>
          <button class="alt" onClick=${openUpcoming} style="padding:8px 12px;font-size:12px">Open Upcoming</button>
        </div>
        ${Array.isArray(upcomingPayload?.tasks) && upcomingPayload.tasks.length > 0
          ? html`<div style="display:grid;gap:10px">
              ${upcomingPayload.tasks.map((task) => html`
                <${UpcomingTaskRow}
                  key=${task.id}
                  task=${task}
                  onClick=${openUpcoming}
                />
              `)}
            </div>`
          : html`<div class="muted" style="font-size:13px">Clearledgr will surface due follow-ups here when approvals, vendor replies, or posting retries need attention.</div>`}
      </div>
    `}

    ${supportRoutes.length > 0 && html`
      <div class="panel">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px">
          <div>
            <h3 style="margin:0 0 4px">Support surfaces</h3>
            <p class="muted" style="margin:0">Secondary pages stay available without taking attention from Pipeline or the thread card.</p>
          </div>
          ${adminAccess && html`<button class="alt" onClick=${() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })} style="padding:8px 12px;font-size:12px">Customize</button>`}
        </div>
        <div style="display:grid;gap:10px">
          ${supportRoutes.map((route) => html`
            <${SupportPageRow}
              key=${route.id}
              label=${route.title}
              detail=${route.subtitle}
              onClick=${() => navigate(route.id)}
            />
          `)}
        </div>
      </div>
    `}

    ${workflowSupportRoutes.length > 0 && html`
      <div class="panel">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px">
          <div>
            <h3 style="margin:0 0 4px">Workflow tools</h3>
            <p class="muted" style="margin:0">Deeper AP support surfaces stay reachable from Home without inflating the default Gmail nav.</p>
          </div>
        </div>
        <div style="display:grid;gap:10px">
          ${workflowSupportRoutes.map((route) => html`
            <${SupportPageRow}
              key=${route.id}
              label=${route.title}
              detail=${route.subtitle}
              onClick=${() => navigate(route.id)}
            />
          `)}
        </div>
      </div>
    `}

    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px">
        <div>
          <h3 style="margin:0 0 4px">Queue shortcuts</h3>
          <p class="muted" style="margin:0">Jump into the AP slice you need without browsing the whole queue first.</p>
        </div>
        <button class="alt" onClick=${() => navigate('clearledgr/pipeline')} style="padding:8px 12px;font-size:12px">Open pipeline</button>
      </div>
      <div style="display:grid;gap:10px">
        ${starterPipelineSlices.map((slice) => html`
          <${QueueShortcutRow}
            key=${slice.id}
            label=${slice.label}
            detail=${slice.description}
            onClick=${() => openPipelineSlice(slice.id)}
          />
        `)}
      </div>
    </div>

    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px">
        <div>
          <h3 style="margin:0 0 4px">Saved views</h3>
          <p class="muted" style="margin:0">
            ${pinnedPipelineViews.length
              ? 'Your pinned pipeline views are ready from Home.'
              : 'Finance-native starter views are ready until you pin your own favorites.'}
          </p>
        </div>
        <button class="alt" onClick=${() => navigate('clearledgr/pipeline')} style="padding:8px 12px;font-size:12px">Manage views</button>
      </div>
      <div style="display:grid;gap:10px">
        ${pinnedPipelineViews.map((view) => html`
          <${QueueShortcutRow}
            key=${`${view.scope || 'user'}:${view.id}`}
            label=${view.name || 'Saved view'}
            detail=${view.description || 'Open a pinned AP queue view.'}
            onClick=${() => openSavedPipelineView(view)}
          />
        `)}
        ${pinnedPipelineViews.length === 0 && starterSavedViews.map((view) => html`
          <${QueueShortcutRow}
            key=${`${view.scope || 'starter'}:${view.id}`}
            label=${view.name || 'Starter view'}
            detail=${view.description || 'Open a finance-native starter view.'}
            onClick=${() => openSavedPipelineView(view)}
          />
        `)}
      </div>
    </div>

    ${adminAccess
      ? html`<div class="panel">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px">
            <div>
              <h3 style="margin:0 0 4px">What still needs setup</h3>
              <p class="muted" style="margin:0">Only the few setup steps that can block AP work in Gmail.</p>
            </div>
            <button class="alt" onClick=${() => navigate('clearledgr/connections')} style="padding:8px 12px;font-size:12px">Open connections</button>
          </div>
          <div style="display:grid;gap:10px">
            <${StatusRow}
              label="Gmail"
              ready=${gmailOk}
              detail=${gmailOk ? 'Gmail monitoring is connected.' : 'Connect Gmail so Clearledgr can detect invoice threads.'}
              actionLabel=${gmailOk ? '' : 'Connect'}
              onAction=${connectGmail}
              pending=${gmailPending}
            />
            <${StatusRow}
              label="Approvals"
              ready=${approvalSurfaceOk}
              detail=${approvalSurfaceOk
                ? (slackOk ? `Slack ready${slack?.approval_channel ? ` · ${slack.approval_channel}` : ''}` : 'Teams ready')
                : 'Connect Slack or Teams so Clearledgr can route approval requests.'}
              actionLabel=${approvalSurfaceOk ? '' : 'Connect'}
              onAction=${slackOk || teamsOk ? null : connectSlack}
              pending=${slackPending}
            />
            <${StatusRow}
              label="ERP"
              ready=${erpOk}
              detail=${erpOk ? `${erp.erp_type || 'ERP'} is connected.` : 'Connect an ERP before posting approved invoices.'}
              actionLabel=${erpOk ? '' : 'Connect'}
              onAction=${() => navigate('clearledgr/connections')}
            />
            <${StatusRow}
              label="Approval rules"
              ready=${policyOk}
              detail=${policyOk ? 'Approval rules are configured.' : 'Review the approval policy before going live.'}
              actionLabel=${policyOk ? '' : 'Review rules'}
              onAction=${() => navigate('clearledgr/rules')}
            />
          </div>
        </div>`
      : html`<div class="panel">
          <h3 style="margin:0 0 6px">Workspace readiness</h3>
          <p class="muted" style="margin:0">Setup pages are reserved for admins. If Gmail, approvals, or ERP are not ready, ask an admin to review Connections and Approval Rules.</p>
        </div>`}

    <${RecentActivity} entries=${recentActivity} navigate=${navigate} canOpenActivity=${routeOptions.includeOps} />

    ${adminAccess && html`
      <div class="panel">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px">
          <div>
            <h3 style="margin:0 0 4px">Customize your left sidebar</h3>
            <p class="muted" style="margin:0">Keep daily pages pinned. Leave the rest available without turning Gmail into a dashboard.</p>
          </div>
          <button class="alt" onClick=${() => applyRoutePreferences(resetRoutePreferences(routeOptions), 'Navigation reset to defaults.')} style="padding:8px 12px;font-size:12px">Reset</button>
        </div>
        <div style="display:grid;gap:10px">
          ${customizableRoutes.map((route) => {
            const preferenceState = getRoutePreferenceState(route.id, routePreferences, routeOptions);
            return html`<${RoutePreferenceRow}
              route=${route}
              preferenceState=${preferenceState}
              onPin=${() => applyRoutePreferences(pinRoute(route.id, routePreferences, routeOptions), `${route.title} pinned to the sidebar.`)}
              onUnpin=${() => applyRoutePreferences(unpinRoute(route.id, routePreferences, routeOptions), `${route.title} removed from pinned pages.`)}
              onHide=${() => applyRoutePreferences(hideRoute(route.id, routePreferences, routeOptions), `${route.title} hidden from the sidebar.`)}
              onShow=${() => applyRoutePreferences(showRoute(route.id, routePreferences, routeOptions), `${route.title} restored to the sidebar.`)}
            />`;
          })}
        </div>
      </div>
    `}
  `;
}
