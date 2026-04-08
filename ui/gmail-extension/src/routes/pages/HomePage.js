/**
 * Home Page — work-first Gmail hub modeled after Streak Home.
 * Primary goal: help operators resume work quickly without turning Gmail
 * into a settings-heavy dashboard.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import {
  integrationByName,
  fmtDateTime,
  fmtRate,
  hasAdminAccess,
  hasOpsAccess,
  useAction,
} from '../route-helpers.js';
import { buildAuditRow, formatAmount, openSourceEmail } from '../../utils/formatters.js';
import { navigateToRecordDetail } from '../../utils/record-route.js';
import { getRouteIconUrl } from '../route-icons.js';
import {
  PIPELINE_BUILTIN_SLICES,
  activatePipelineSlice,
  focusPipelineItem,
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

function UtilityIconAction({ label, detail, icon, onClick }) {
  const title = detail ? `${label} — ${detail}` : label;
  return html`<button
    class="home-utility-icon-button"
    onClick=${onClick}
    title=${title}
    aria-label=${label}
  >
    <span class="home-utility-icon" style=${`background-image:url(${icon || getRouteIconUrl('activity')})`}></span>
  </button>`;
}

function HomeStatusPill({ label, value, tone = 'default' }) {
  return html`<span class=${`home-status-pill ${tone}`}>
    <strong>${value}</strong>
    <span>${label}</span>
  </span>`;
}

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

function buildTaskLocator(task = {}) {
  return {
    id: task.ap_item_id,
    thread_id: task.thread_id,
    message_id: task.message_id,
    state: task.state,
  };
}

function ToolbarAction({ label, detail, meta = 'Open', onClick }) {
  return html`<button class="home-quick-card" onClick=${onClick}>
    <span class="home-quick-meta">${meta}</span>
    <strong class="home-quick-title">${label}</strong>
    <span class="home-quick-detail muted">${detail}</span>
  </button>`;
}

function AuditEventRow({ entry, actionLabel = 'Open record', onAction }) {
  const amountLabel = Number.isFinite(Number(entry?.amount))
    ? formatAmount(entry.amount, entry?.currency)
    : '';
  const metaLine = [
    entry?.vendor_name || entry?.vendor || '',
    entry?.invoice_number || '',
    amountLabel,
  ].filter(Boolean).join(' · ');

  return html`<div class="home-event-row">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div style="min-width:0;flex:1">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
          <strong style="font-size:13px">${entry?.title || entry?.operator_title || 'Update'}</strong>
          ${entry?.operator_severity
            ? html`<span style="
                font-size:10px;font-weight:700;padding:3px 7px;border-radius:999px;text-transform:uppercase;letter-spacing:0.04em;
                background:${entry.operator_severity === 'success' ? '#ECFDF5' : entry.operator_severity === 'warning' ? '#FEF3C7' : entry.operator_severity === 'error' ? '#FEF2F2' : '#EFF6FF'};
                color:${entry.operator_severity === 'success' ? '#166534' : entry.operator_severity === 'warning' ? '#92400E' : entry.operator_severity === 'error' ? '#B91C1C' : '#1D4ED8'};
              ">${entry.operator_severity}</span>`
            : null}
        </div>
        ${metaLine ? html`<div class="muted" style="font-size:12px;margin-bottom:4px">${metaLine}</div>` : null}
        <div class="muted" style="font-size:12px;line-height:1.5">${entry?.detail || entry?.operator_message || entry?.summary || 'Recent activity is available.'}</div>
      </div>
      <div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px">
        <span class="muted" style="font-size:12px;white-space:nowrap">${fmtDateTime(entry?.ts || entry?.timestamp || entry?.created_at)}</span>
        <button class="btn-ghost btn-sm" onClick=${onAction}>${actionLabel}</button>
      </div>
    </div>
  </div>`;
}

function UpcomingTaskActionRow({ task, onOpenRecord, onOpenSlice, onOpenEmail }) {
  const statusLabel = String(task?.status || '').toLowerCase() === 'overdue'
    ? 'Overdue'
    : String(task?.status || '').toLowerCase() === 'today'
      ? 'Today'
      : 'Queued';
  return html`<div class="home-event-row">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div style="min-width:0;flex:1">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
          <strong style="font-size:13px">${task?.title || 'Follow-up'}</strong>
          <span style="
            font-size:10px;font-weight:700;padding:3px 7px;border-radius:999px;text-transform:uppercase;letter-spacing:0.04em;
            background:${statusLabel === 'Overdue' ? '#FEF2F2' : statusLabel === 'Today' ? '#FEF3C7' : '#EFF6FF'};
            color:${statusLabel === 'Overdue' ? '#B91C1C' : statusLabel === 'Today' ? '#92400E' : '#1D4ED8'};
          ">${statusLabel}</span>
        </div>
        <div class="muted" style="font-size:12px;margin-bottom:4px">
          ${(task?.vendor_name || 'Unknown vendor')} · ${(task?.invoice_number || 'No invoice #')} · ${formatAmount(task?.amount, task?.currency)}
        </div>
        <div class="muted" style="font-size:12px;line-height:1.5">${task?.detail || 'Follow-up needed.'}</div>
        <div class="muted" style="font-size:12px;margin-top:6px">
          ${task?.due_at ? `Due ${fmtDateTime(task.due_at)}` : 'No explicit follow-up time'}
        </div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
        <button class="btn-secondary btn-sm" onClick=${onOpenRecord}>Open record</button>
        <button class="btn-ghost btn-sm" onClick=${onOpenSlice}>Open slice</button>
        ${(task?.thread_id || task?.message_id) ? html`<button class="btn-ghost btn-sm" onClick=${onOpenEmail}>Open email</button>` : null}
      </div>
    </div>
  </div>`;
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
        <span style="font-size:11px;font-weight:700;padding:4px 9px;border-radius:999px;background:${showGmailAction ? '#FEF3C7' : '#EFF6FF'};color:${showGmailAction ? '#A16207' : '#1D4ED8'}">
          ${showGmailAction ? 'Setup needed' : 'Optional setup'}
        </span>
      </div>
      <strong style="display:block;font-size:16px;line-height:1.35;margin-bottom:4px">
        ${showGmailAction ? 'Connect Gmail to start processing' : 'Connect more integrations'}
      </strong>
      <p class="muted" style="margin:0;max-width:none">
        ${adminAccess
          ? (showGmailAction
            ? `${missingSetup.join(', ')} still need attention before invoices can be processed.`
            : `${missingSetup.join(', ')} — connect these to unlock approvals, ERP posting, and full automation.`)
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
  return html`<div class="home-empty-state">
    <div class="home-empty-glyph"></div>
    <div class="home-empty-copy">${text}</div>
  </div>`;
}

function QuickAccessStrip({ items = [] }) {
  if (!Array.isArray(items) || items.length === 0) return null;
  return html`
    <div class="panel">
      <div class="panel-head compact">
        <div>
          <div class="home-eyebrow">Quick access</div>
          <p class="muted" style="margin:0">The work and tool shortcuts you should not have to hunt for in Gmail.</p>
        </div>
      </div>
      <div class="home-quick-row">
        ${items.map((item) => html`
          <${ToolbarAction}
            key=${item.key}
            label=${item.label}
            detail=${item.detail}
            meta=${item.meta || 'Open'}
            onClick=${item.onClick}
          />
        `)}
      </div>
    </div>
  `;
}

function InsightList({ items = [] }) {
  const rows = (Array.isArray(items) ? items : []).filter(Boolean);
  if (!rows.length) return null;
  return html`<div class="home-list-stack">
    ${rows.map((item, index) => html`
      <div key=${index} class="home-event-row">
        <div style="display:flex;align-items:flex-start;gap:10px">
          <span style="width:7px;height:7px;border-radius:999px;background:var(--accent);margin-top:7px;flex:0 0 auto"></span>
          <div class="muted" style="font-size:13px;line-height:1.55">${item}</div>
        </div>
      </div>
    `)}
  </div>`;
}

function SectionPanel({ title, detail, actionLabel = '', onAction, children, panelMinHeight = 0, className = '' }) {
  return html`<div class=${`panel home-surface-panel ${className}`.trim()}>
    <div class="home-surface-head">
      <div>
        <div class="home-section-label">${title}</div>
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
  const planName = String(bootstrap?.subscription?.plan || 'free').replace(/_/g, ' ');

  const rawName = (userEmail || '').split('@')[0] || '';
  const firstName = rawName ? rawName.charAt(0).toUpperCase() + rawName.slice(1).split(/[._-]/)[0] : '';
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';

  const policyConfig = bootstrap?.policyPayload?.policy?.config_json || {};
  const adminAccess = hasAdminAccess(bootstrap);
  const opsAccess = hasOpsAccess(bootstrap);
  const workAccess = opsAccess || adminAccess;
  const pipelineScope = { orgId, userEmail };
  const [pipelinePrefs, setPipelinePrefs] = useState(() => readPipelinePreferences(pipelineScope));
  const [upcomingPayload, setUpcomingPayload] = useState({ summary: {}, tasks: [] });
  const [recentAudit, setRecentAudit] = useState([]);
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
  const pilotSnapshot = dashboard?.pilot_snapshot || {};
  const agenticSnapshot = dashboard?.agentic_snapshot || {};

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
    if (!workAccess) {
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
  }, [api, orgId, workAccess]);

  useEffect(() => {
    if (!workAccess) {
      setRecentAudit([]);
      return;
    }
    api(`/api/ap/audit/recent?organization_id=${encodeURIComponent(orgId)}&limit=12`, { silent: true })
      .then((data) => {
        setRecentAudit(Array.isArray(data?.events) ? data.events.slice(0, 12) : []);
      })
      .catch(() => {
        setRecentAudit([]);
      });
  }, [api, orgId, workAccess]);

  const openPipelineSlice = (sliceId) => {
    clearPipelineNavigation(pipelineScope);
    activatePipelineSlice(pipelineScope, sliceId);
    setPipelinePrefs(readPipelinePreferences(pipelineScope));
    navigate('clearledgr/invoices');
  };

  const openSavedPipelineView = (view) => {
    if (!view?.snapshot) return;
    clearPipelineNavigation(pipelineScope);
    writePipelinePreferences(pipelineScope, view.snapshot);
    setPipelinePrefs(readPipelinePreferences(pipelineScope));
    navigate('clearledgr/invoices');
  };

  const openUpcomingTaskSlice = (task) => {
    const sliceId = task?.recommended_slice || 'all_open';
    clearPipelineNavigation(pipelineScope);
    activatePipelineSlice(pipelineScope, sliceId);
    if (task?.ap_item_id) focusPipelineItem(pipelineScope, buildTaskLocator(task), 'home');
    navigate('clearledgr/invoices');
  };

  const openRecord = (recordId, context = null) => {
    const normalizedId = String(recordId || '').trim();
    if (!normalizedId) return;
    if (context) focusPipelineItem(pipelineScope, context, 'home');
    navigateToRecordDetail(navigate, normalizedId);
  };

  const openUpcomingTaskRecord = (task) => {
    openRecord(task?.ap_item_id, buildTaskLocator(task));
  };

  const openUpcomingTaskEmail = (task) => {
    const ok = openSourceEmail({
      thread_id: task?.thread_id,
      message_id: task?.message_id,
      subject: task?.title || task?.invoice_number || 'Invoice follow-up',
    });
    if (!ok) toast?.('Unable to open the source email thread.', 'error');
  };

  const upcomingSummary = Number(upcomingPayload?.summary?.total || 0);
  const savedOrStarterViews = pinnedPipelineViews.length ? pinnedPipelineViews : starterSavedViews;
  const recentWork = useMemo(
    () => (Array.isArray(recentAudit) ? recentAudit.slice(0, 4) : []),
    [recentAudit],
  );
  const recentWins = useMemo(
    () => (Array.isArray(recentAudit) ? recentAudit.filter((event) => {
      const code = String(event?.operator_code || '').toLowerCase();
      const severity = String(event?.operator_severity || '').toLowerCase();
      const nextState = String(event?.new_state || event?.state || '').toLowerCase();
      return code === 'erp_posted' || code === 'retry_completed' || severity === 'success' || nextState === 'posted_to_erp' || nextState === 'closed';
    }).slice(0, 4) : []),
    [recentAudit],
  );
  const homeInsights = useMemo(() => {
    const items = [];
    const pilotHighlights = Array.isArray(pilotSnapshot?.highlights) ? pilotSnapshot.highlights : [];
    const topBlockers = Array.isArray(agenticSnapshot?.top_blockers) ? agenticSnapshot.top_blockers : [];
    if (Number(pilotSnapshot?.touchless_rate_pct) > 0) {
      items.push(`${fmtRate(pilotSnapshot.touchless_rate_pct)} touchless handling across the current pilot window.`);
    }
    if (Number(dashboard?.pending_approval) > 0) {
      items.push(`${Number(dashboard.pending_approval).toLocaleString()} invoices are currently waiting on approval.`);
    }
    if (Number(dashboard?.posted_today) > 0) {
      items.push(`${Number(dashboard.posted_today).toLocaleString()} invoices were posted or closed today.`);
    }
    pilotHighlights.forEach((entry) => items.push(String(entry || '').trim()));
    if (topBlockers.length > 0) {
      items.push(`Top blockers right now: ${topBlockers.join(', ')}.`);
    }
    if (Number(agenticSnapshot?.shadow_disagreement_count) > 0) {
      items.push(`${Number(agenticSnapshot.shadow_disagreement_count).toLocaleString()} shadow-decision disagreements still need review before wider autonomy.`);
    }
    return items.filter(Boolean).slice(0, 6);
  }, [agenticSnapshot, dashboard, pilotSnapshot]);

  const quickAccessItems = [
    {
      key: 'pipeline',
      label: 'Invoices',
      detail: 'Open the full invoice queue.',
      meta: 'Queue',
      onClick: () => navigate('clearledgr/invoices'),
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
          detail: upcomingSummary > 0 ? `${upcomingSummary} item${upcomingSummary === 1 ? '' : 's'} need attention next.` : 'No upcoming follow-ups yet.',
          meta: 'Follow-up',
          onClick: () => navigate('clearledgr/upcoming'),
        }
      : null,
    workAccess
      ? {
          key: 'activity',
          label: 'Recent work',
          detail: 'Open the latest audit trail and operator movement.',
          meta: 'Activity',
          onClick: () => navigate('clearledgr/activity'),
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

  const utilityItems = [
    adminAccess
      ? {
          key: 'connections-utility',
          label: 'Connections',
          detail: allReady ? 'Connections and routing are live.' : `${missingSetup.length} setup item${missingSetup.length === 1 ? '' : 's'} still need attention.`,
          icon: getRouteIconUrl('connections'),
          onClick: () => navigate('clearledgr/connections'),
        }
      : null,
    adminAccess
      ? {
          key: 'team-utility',
          label: 'Team',
          detail: 'Invites, roles, and operator access.',
          icon: getRouteIconUrl('team'),
          onClick: () => navigate('clearledgr/team'),
        }
      : null,
    adminAccess
      ? {
          key: 'plan-utility',
          label: 'Billing',
          detail: `${planName.charAt(0).toUpperCase()}${planName.slice(1)} plan and usage.`,
          icon: getRouteIconUrl('plan'),
          onClick: () => navigate('clearledgr/plan'),
        }
      : null,
    (adminAccess || workAccess)
      ? {
          key: 'settings-utility',
          label: 'Settings',
          detail: 'Workspace controls and finance defaults.',
          icon: getRouteIconUrl('settings'),
          onClick: () => navigate('clearledgr/settings'),
        }
      : null,
    adminAccess
      ? {
          key: 'reports-utility',
          label: 'Reports',
          detail: 'Lane health, risk, and pilot progress.',
          icon: getRouteIconUrl('reports'),
          onClick: () => navigate('clearledgr/reports'),
        }
      : workAccess
        ? {
            key: 'vendors-utility',
            label: 'Vendors',
            detail: 'History, recurring blockers, and context.',
            icon: getRouteIconUrl('vendors'),
            onClick: () => navigate('clearledgr/vendors'),
          }
        : null,
    workAccess
      ? {
          key: 'primary-utility',
          label: allReady ? 'Open invoices' : 'Finish setup',
          detail: allReady ? 'Jump back into the finance lane.' : 'Complete the remaining setup steps.',
          icon: getRouteIconUrl(allReady ? 'pipeline' : 'connections'),
          tone: 'accent',
          onClick: () => navigate(allReady ? 'clearledgr/invoices' : 'clearledgr/connections'),
        }
      : null,
  ].filter(Boolean).slice(0, 5);
  const primaryUtility = utilityItems.find((item) => item.tone === 'accent') || null;
  const secondaryUtilities = utilityItems.filter((item) => item.tone !== 'accent');

  return html`
    <div class="topbar home-header-shell">
      <div class="home-header-copy">
        <div class="home-eyebrow">Home</div>
        <h2>Welcome to Clearledgr</h2>
        <p>
          ${allReady
            ? 'Run the finance lane from Gmail: reopen work, clear blockers, watch follow-ups, and move invoices forward without hunting through the product.'
            : gmailOk
              ? `${greeting}${firstName ? `, ${firstName}` : ''}. Gmail is active. Finish the remaining setup so approvals, posting, and automation can run from the same place.`
              : `${greeting}${firstName ? `, ${firstName}` : ''}. Connect Gmail to start processing invoices from inside the workspace.`}
        </p>
        <div class="home-status-row">
          <${HomeStatusPill}
            label=${allReady ? 'Lane' : gmailOk ? 'Status' : 'Setup'}
            value=${allReady ? 'Active' : gmailOk ? 'Processing' : 'Needed'}
            tone=${allReady ? 'success' : gmailOk ? 'info' : 'warning'}
          />
          <${HomeStatusPill} label="Pending approval" value=${Number(dashboard?.pending_approval || 0).toLocaleString()} />
          <${HomeStatusPill} label="Posted today" value=${Number(dashboard?.posted_today || 0).toLocaleString()} />
          ${Number(dashboard?.auto_approved_rate || 0) > 0
            ? html`<${HomeStatusPill} label="Touchless" value=${fmtRate(dashboard.auto_approved_rate)} tone="success" />`
            : null}
          ${lastScanAt
            ? html`<${HomeStatusPill} label="Last scan" value=${fmtDateTime(lastScanAt)} />`
            : null}
        </div>
      </div>
      <div class="home-utility-rail">
        <div class="home-utility-strip">
          ${secondaryUtilities.map((item) => html`
            <${UtilityIconAction}
              key=${item.key}
              label=${item.label}
              detail=${item.detail}
              icon=${item.icon}
              onClick=${item.onClick}
            />
          `)}
        </div>
        ${primaryUtility
          ? html`<button class="home-utility-primary" onClick=${primaryUtility.onClick}>
              ${primaryUtility.label}
            </button>`
          : null}
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

    ${allReady
      ? html`<div class="home-banner">
          <div style="min-width:0;flex:1">
            <div class="home-eyebrow" style="margin-bottom:6px">Finance lane update</div>
            <p class="muted" style="margin:0">
              ${Number(dashboard?.pending_approval || 0).toLocaleString()} waiting on approval · ${Number(dashboard?.posted_today || 0).toLocaleString()} posted today
              ${Number(agenticSnapshot?.shadow_disagreement_count || 0) > 0
                ? ` · ${Number(agenticSnapshot.shadow_disagreement_count).toLocaleString()} shadow disagreements still under review`
                : ''}
            </p>
          </div>
          <div class="toolbar-actions">
            <button class="btn-secondary btn-sm" onClick=${() => navigate(adminAccess ? 'clearledgr/reports' : 'clearledgr/activity')}>
              ${adminAccess ? 'Open reports' : 'Open activity'}
            </button>
          </div>
        </div>`
      : null}

    <${QuickAccessStrip} items=${quickAccessItems} />

    <div class="home-main-grid">
      <${SectionPanel}
        title="Recent work"
        detail=${recentWork.length > 0
          ? 'Resume the records that moved most recently.'
          : 'Recent invoice movement will collect here as work happens.'}
        actionLabel=${workAccess ? 'Open activity' : ''}
        onAction=${() => navigate('clearledgr/activity')}
        panelMinHeight=${240}
        className="home-primary-panel"
      >
        ${recentWork.length > 0
          ? html`<div class="home-list-stack">
              ${recentWork.map((entry, index) => {
                const recordId = String(entry?.ap_item_id || '').trim();
                const auditRow = buildAuditRow(entry);
                return html`<${AuditEventRow}
                  key=${entry?.id || `${entry?.ts || 'event'}:${index}`}
                  entry=${{ ...entry, ...auditRow, operator_severity: auditRow.severity }}
                  actionLabel=${recordId ? 'Open record' : 'Open activity'}
                  onAction=${() => (recordId ? openRecord(recordId, { id: recordId }) : navigate('clearledgr/activity'))}
                />`;
              })}
            </div>`
          : html`<${EmptyPanelState} text="Recent AP activity will appear here once invoices start moving through the workflow." />`}
      </${SectionPanel}>

      <${SectionPanel}
        title="Recently posted"
        detail=${Number(dashboard?.posted_today || 0) > 0
          ? `${Number(dashboard.posted_today).toLocaleString()} record${Number(dashboard.posted_today) === 1 ? '' : 's'} posted or closed today.`
          : 'Posted invoices and completed records will show up here.'}
        actionLabel=${adminAccess ? 'Open reports' : 'Open invoices'}
        onAction=${() => navigate(adminAccess ? 'clearledgr/reports' : 'clearledgr/invoices')}
        panelMinHeight=${240}
        className="home-secondary-panel"
      >
        ${recentWins.length > 0
          ? html`<div class="home-list-stack">
              ${recentWins.map((entry, index) => {
                const recordId = String(entry?.ap_item_id || '').trim();
                const auditRow = buildAuditRow(entry);
                return html`<${AuditEventRow}
                  key=${entry?.id || `${entry?.ts || 'win'}:${index}`}
                  entry=${{ ...entry, ...auditRow, operator_severity: auditRow.severity }}
                  actionLabel=${recordId ? 'Open record' : 'Open reports'}
                  onAction=${() => (recordId ? openRecord(recordId, { id: recordId }) : navigate(adminAccess ? 'clearledgr/reports' : 'clearledgr/invoices'))}
                />`;
              })}
            </div>`
          : html`<${EmptyPanelState} text="Posted invoices, recovered posts, and other finance wins will appear here." />`}
      </${SectionPanel}>
    </div>

    <div class="home-panel-grid">
      <${SectionPanel}
        title="Upcoming tasks"
        detail=${upcomingSummary > 0
          ? `${upcomingSummary} thing${upcomingSummary === 1 ? '' : 's'} need attention next.`
          : 'No upcoming follow-ups yet.'}
        actionLabel=${workAccess ? 'Open upcoming' : ''}
        onAction=${() => navigate('clearledgr/upcoming')}
        panelMinHeight=${240}
      >
        ${Array.isArray(upcomingPayload?.tasks) && upcomingPayload.tasks.length > 0
          ? html`<div class="home-list-stack">
              ${upcomingPayload.tasks.map((task) => html`
                <${UpcomingTaskActionRow}
                  key=${task.id}
                  task=${task}
                  onOpenRecord=${() => openUpcomingTaskRecord(task)}
                  onOpenSlice=${() => openUpcomingTaskSlice(task)}
                  onOpenEmail=${() => openUpcomingTaskEmail(task)}
                />
              `)}
            </div>`
          : html`<${EmptyPanelState} text="Clearledgr will show the next approvals, vendor follow-ups, and posting retries here." />`}
      </${SectionPanel}>

      <${SectionPanel}
        title=${savedOrStarterViews.length > 0 ? 'Saved views and slices' : 'Queue slices'}
        detail=${savedOrStarterViews.length > 0
          ? 'The queue views operators come back to most.'
          : 'Jump straight to the queue segment you need.'}
        actionLabel="Open invoices"
        onAction=${() => navigate('clearledgr/invoices')}
        panelMinHeight=${240}
      >
        <div class="home-list-stack">
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

      <${SectionPanel}
        title="Highlights"
        detail="The current finance lane signals, pilot highlights, and blockers worth paying attention to."
        actionLabel=${adminAccess ? 'Open reports' : workAccess ? 'Open activity' : ''}
        onAction=${() => navigate(adminAccess ? 'clearledgr/reports' : 'clearledgr/activity')}
        className="home-panel-span"
      >
        <${InsightList} items=${homeInsights} />
        ${homeInsights.length === 0
          ? html`<${EmptyPanelState} text="Clearledgr will surface pilot highlights, blocker trends, and finance guidance here as the lane matures." />`
          : null}
      </${SectionPanel}>
    </div>
  `;
}
