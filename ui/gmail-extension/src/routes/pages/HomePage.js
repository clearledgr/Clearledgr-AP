/**
 * Home Page — lightweight Gmail-native launch hub.
 * Keeps setup reachable without turning Gmail into a separate dashboard.
 */
import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { integrationByName, fmtDateTime, hasOpsAccess, useAction } from '../route-helpers.js';
import {
  getRoutePreferenceState,
  getVisibleNavRoutes,
  hideRoute,
  pinRoute,
  resetRoutePreferences,
  showRoute,
  unpinRoute,
} from '../route-registry.js';
import { activatePipelineSlice, readPipelinePreferences, writePipelinePreferences } from '../pipeline-views.js';

const html = htm.bind(h);

const ICONS = {
  home: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 11.5 12 4l9 7.5"/><path d="M5 10.5V20h14v-9.5"/><path d="M9 20v-5h6v5"/></svg>`,
  pipeline: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 3v18"/></svg>`,
  activity: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="22,12 18,12 15,21 9,3 6,12 2,12"/></svg>`,
  vendors: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
  recon: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2v20M2 12h20"/><circle cx="12" cy="12" r="10"/></svg>`,
  settings: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>`,
  rules: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>`,
  team: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
  company: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 21h18"/><path d="M5 21V7l7-4 7 4v14"/><path d="M9 9h.01M9 13h.01M9 17h.01M15 9h.01M15 13h.01M15 17h.01"/></svg>`,
  plan: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.86L12 17.77 5.82 21l1.18-6.86-5-4.87 6.91-1.01z"/></svg>`,
  health: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>`,
  connections: html`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
};

function getRouteIcon(iconKey) {
  return ICONS[iconKey] || ICONS.settings;
}

function QuickAccessCard({ icon, label, detail, onClick }) {
  return html`<button onClick=${onClick} style="
    display:flex;flex-direction:column;align-items:flex-start;justify-content:space-between;gap:8px;
    padding:14px 14px;min-width:120px;min-height:108px;text-align:left;
    background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-md);
    cursor:pointer;transition:all 0.15s;color:var(--ink);font-family:inherit;
  " onMouseOver=${e => { e.currentTarget.style.borderColor = 'var(--accent)'; }}
     onMouseOut=${e => { e.currentTarget.style.borderColor = 'var(--border)'; }}>
    <div style="color:var(--ink-secondary)">${icon}</div>
    <div>
      <div style="font-size:13px;font-weight:600;margin-bottom:2px">${label}</div>
      <div style="font-size:12px;color:var(--ink-muted);line-height:1.4">${detail}</div>
    </div>
  </button>`;
}

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

function LaunchSummary({ allReady, approvalsReady, erpReady, lastScanAt, navigate }) {
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
        <button class="alt" onClick=${() => navigate('clearledgr/connections')}>Review connections</button>
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

function RecentActivity({ entries = [], navigate }) {
  const rows = Array.isArray(entries) ? entries.slice(0, 5) : [];
  return html`<div class="panel">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px">
      <div>
        <h3 style="margin:0 0 4px">Recent activity</h3>
        <p class="muted" style="margin:0">What changed recently in AP.</p>
      </div>
      <button class="alt" onClick=${() => navigate('clearledgr/activity')} style="padding:8px 12px;font-size:12px">Open activity</button>
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

export default function HomePage({
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
  const routeOptions = { includeAdmin: hasOpsAccess(bootstrap) };
  const pipelinePrefs = readPipelinePreferences(orgId);
  const savedPipelineViews = Array.isArray(pipelinePrefs?.customViews) ? pipelinePrefs.customViews.slice(0, 3) : [];
  const gmailOk = Boolean(gmail.connected);
  const slackOk = Boolean(slack.connected);
  const teamsOk = Boolean(teams.connected);
  const approvalSurfaceOk = slackOk || teamsOk;
  const erpOk = Boolean(erp.connected);
  const policyOk = Boolean(policyConfig && Object.keys(policyConfig).length > 0);
  const allReady = gmailOk && approvalSurfaceOk && erpOk && policyOk;
  const lastScanAt = dashboard?.last_scan_at || dashboard?.lastScanAt || bootstrap?.health?.last_scan_at || '';

  const quickAccessRoutes = getVisibleNavRoutes(routePreferences, routeOptions)
    .filter((route) => route.id !== 'clearledgr/home')
    .slice(0, 4);
  const customizableRoutes = availableRoutes.filter((route) => !route.adminOnly);

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

  async function applyRoutePreferences(nextPreferences, message) {
    if (typeof updateRoutePreferences !== 'function') return;
    await updateRoutePreferences(nextPreferences);
    if (message) toast(message);
  }

  const openPipelineSlice = (sliceId) => {
    activatePipelineSlice(orgId, sliceId);
    navigate('clearledgr/pipeline');
  };

  const openSavedPipelineView = (view) => {
    if (!view?.snapshot) return;
    writePipelinePreferences(orgId, view.snapshot);
    navigate('clearledgr/pipeline');
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
    />

    <div style="
      display:flex;gap:10px;overflow-x:auto;padding:4px 0 8px;
      border-bottom:1px solid var(--border);margin-bottom:20px;
    ">
      ${quickAccessRoutes.map((route) => html`
        <${QuickAccessCard}
          icon=${getRouteIcon(route.icon)}
          label=${route.title}
          detail=${route.subtitle}
          onClick=${() => navigate(route.id)}
        />
      `)}
      <${QuickAccessCard}
        icon=${ICONS.settings}
        label="Customize"
        detail="Pin or hide Gmail pages."
        onClick=${() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })}
      />
    </div>

    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px">
        <div>
          <h3 style="margin:0 0 4px">Queue shortcuts</h3>
          <p class="muted" style="margin:0">Jump into the AP slice you need without browsing the whole queue first.</p>
        </div>
        <button class="alt" onClick=${() => navigate('clearledgr/pipeline')} style="padding:8px 12px;font-size:12px">Open pipeline</button>
      </div>
      <div style="display:grid;gap:10px">
        <${QueueShortcutRow}
          label="Approval backlog"
          detail="Open invoices waiting on approvers."
          onClick=${() => openPipelineSlice('approval_backlog')}
        />
        <${QueueShortcutRow}
          label="Ready to post"
          detail="Go straight to invoices that can move to ERP."
          onClick=${() => openPipelineSlice('ready_to_post')}
        />
        <${QueueShortcutRow}
          label="Exceptions"
          detail="Review policy, confidence, and posting blockers."
          onClick=${() => openPipelineSlice('exceptions')}
        />
        ${savedPipelineViews.map((view) => html`
          <${QueueShortcutRow}
            key=${view.id}
            label=${view.name || 'Saved view'}
            detail="Open a saved AP queue view."
            onClick=${() => openSavedPipelineView(view)}
          />
        `)}
      </div>
    </div>

    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px">
        <div>
          <h3 style="margin:0 0 4px">What still needs setup</h3>
          <p class="muted" style="margin:0">Only the steps that matter for AP launch.</p>
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
    </div>

    <${RecentActivity} entries=${recentActivity} navigate=${navigate} />

    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px">
        <div>
          <h3 style="margin:0 0 4px">Customize your left sidebar</h3>
          <p class="muted" style="margin:0">Keep daily pages pinned. Leave the rest available without clutter.</p>
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
  `;
}
