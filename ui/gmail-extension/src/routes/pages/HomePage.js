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
  const [onboardingBlockers, setOnboardingBlockers] = useState([]);
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

  // §6.1 Section 5 — Vendor Onboarding Blockers
  useEffect(() => {
    if (!workAccess) {
      setOnboardingBlockers([]);
      return;
    }
    api(`/api/ops/vendor-onboarding/sessions?organization_id=${encodeURIComponent(orgId)}&limit=200`, { silent: true })
      .then((data) => {
        const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
        const now = Date.now();
        const blockedStates = new Set(['invited', 'awaiting_kyc', 'awaiting_bank', 'microdeposit_pending', 'escalated']);
        const blocked = sessions.filter((s) => {
          if (!blockedStates.has(s.state)) return false;
          const elapsed = s.invited_at ? (now - new Date(s.invited_at).getTime()) / 3600000 : 0;
          return elapsed >= 48;
        }).map((s) => {
          const hours = s.invited_at ? Math.floor((now - new Date(s.invited_at).getTime()) / 3600000) : 0;
          const days = Math.floor(hours / 24);
          const reasons = [];
          if (s.state === 'awaiting_kyc') reasons.push('Missing KYC documents');
          else if (s.state === 'awaiting_bank') reasons.push('Bank details not submitted');
          else if (s.state === 'microdeposit_pending') reasons.push('Micro-deposit unconfirmed');
          else if (s.state === 'escalated') reasons.push('Escalated — needs manual resolution');
          else if (s.state === 'invited') reasons.push('Vendor has not responded');
          return { ...s, days, reason: reasons[0] || 'Blocked' };
        });
        setOnboardingBlockers(blocked);
      })
      .catch(() => {
        setOnboardingBlockers([]);
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

  // ── Thesis §6.1 data derivation ──
  // Partition queue items into the thesis-defined Home sections.
  const queue = Array.isArray(bootstrap?.queue) ? bootstrap.queue : [];

  const exceptionQueue = useMemo(() => queue
    .filter((item) => {
      const s = String(item?.state || '').toLowerCase();
      return s === 'needs_info' || s === 'failed_post' || s === 'reversed'
        || Boolean(item?.exception_code);
    })
    .sort((a, b) => {
      const dueA = a?.due_date || a?.payment_due_date || '9999';
      const dueB = b?.due_date || b?.payment_due_date || '9999';
      return dueA < dueB ? -1 : dueA > dueB ? 1 : 0;
    })
    .slice(0, 10),
  [queue]);

  const awaitingApproval = useMemo(() => queue
    .filter((item) => {
      const s = String(item?.state || '').toLowerCase();
      return s === 'needs_approval' || s === 'pending_approval';
    })
    .sort((a, b) => {
      const dueA = a?.due_date || a?.payment_due_date || '9999';
      const dueB = b?.due_date || b?.payment_due_date || '9999';
      return dueA < dueB ? -1 : dueA > dueB ? 1 : 0;
    })
    .slice(0, 10),
  [queue]);

  const dueThisWeek = useMemo(() => {
    const now = new Date();
    const weekFromNow = new Date(now.getTime() + 7 * 86400000);
    return queue
      .filter((item) => {
        const s = String(item?.state || '').toLowerCase();
        if (s !== 'approved' && s !== 'ready_to_post' && s !== 'posted_to_erp') return false;
        const due = item?.due_date || item?.payment_due_date;
        if (!due) return false;
        try {
          const d = new Date(due);
          return d >= now && d <= weekFromNow;
        } catch { return false; }
      })
      .sort((a, b) => {
        const dueA = a?.due_date || a?.payment_due_date || '';
        const dueB = b?.due_date || b?.payment_due_date || '';
        return dueA < dueB ? -1 : dueA > dueB ? 1 : 0;
      })
      .slice(0, 10);
  }, [queue]);

  const agentActionsToday = useMemo(() => {
    const midnightIso = new Date().toISOString().slice(0, 10);
    return (Array.isArray(recentAudit) ? recentAudit : [])
      .filter((e) => String(e?.ts || e?.created_at || '').slice(0, 10) >= midnightIso)
      .slice(0, 10);
  }, [recentAudit]);

  return html`
    <div class="topbar home-header-shell">
      <div class="home-header-copy">
        <div class="home-eyebrow">Clearledgr Home</div>
        <h2>${greeting}${firstName ? `, ${firstName}` : ''}</h2>
        <p class="muted">
          ${allReady
            ? 'What broke overnight, what needs approval, what is due soon, and what the agent is working on.'
            : gmailOk
              ? 'Gmail is active. Finish the remaining setup so approvals and posting can run.'
              : 'Connect Gmail to start processing invoices.'}
        </p>
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

    <!-- §6.1 Section 1: Exception Queue -->
    <${SectionPanel}
      title="Exception queue"
      detail=${exceptionQueue.length > 0
        ? `${exceptionQueue.length} exception${exceptionQueue.length === 1 ? '' : 's'} need resolution, ordered by due date.`
        : 'No exceptions right now. Everything is flowing.'}
      actionLabel=${exceptionQueue.length > 0 ? 'Open exceptions' : ''}
      onAction=${() => { openPipelineSlice('blocked_exception'); }}
      panelMinHeight=${120}
    >
      ${exceptionQueue.length > 0
        ? html`<div class="home-list-stack">
            ${exceptionQueue.map((item, i) => html`
              <${AuditEventRow}
                key=${item.id || i}
                entry=${{
                  title: item.vendor_name || item.vendor || 'Unknown vendor',
                  detail: item.exception_reason || item.last_error || String(item.state || '').replace(/_/g, ' '),
                  amount: item.amount,
                  currency: item.currency,
                  invoice_number: item.invoice_number,
                  vendor_name: item.vendor_name || item.vendor,
                  ts: item.due_date || item.payment_due_date || item.updated_at,
                  operator_severity: 'warning',
                }}
                actionLabel="Resolve"
                onAction=${() => openRecord(item.id, { id: item.id })}
              />
            `)}
          </div>`
        : html`<${EmptyPanelState} text="Exceptions that need resolution before their due date will appear here." />`}
    </${SectionPanel}>

    <!-- §6.1 Section 2: Awaiting Your Approval -->
    <${SectionPanel}
      title="Awaiting your approval"
      detail=${awaitingApproval.length > 0
        ? `${awaitingApproval.length} invoice${awaitingApproval.length === 1 ? '' : 's'} matched and waiting for sign-off.`
        : 'No invoices waiting on you.'}
      actionLabel=${awaitingApproval.length > 0 ? 'Open approvals' : ''}
      onAction=${() => { openPipelineSlice('waiting_on_approval'); }}
      panelMinHeight=${120}
    >
      ${awaitingApproval.length > 0
        ? html`<div class="home-list-stack">
            ${awaitingApproval.map((item, i) => html`
              <${AuditEventRow}
                key=${item.id || i}
                entry=${{
                  title: `${item.vendor_name || item.vendor || 'Unknown'} — ${formatAmount(item.amount, item.currency)}`,
                  detail: item.invoice_number ? `Invoice ${item.invoice_number}` : 'Approve or reject from here.',
                  ts: item.due_date || item.payment_due_date || item.updated_at,
                }}
                actionLabel="Review"
                onAction=${() => openRecord(item.id, { id: item.id })}
              />
            `)}
          </div>`
        : html`<${EmptyPanelState} text="Invoices the agent has matched and routed for your sign-off will appear here." />`}
    </${SectionPanel}>

    <div class="home-panel-grid">
      <!-- §6.1 Section 3: Due For Payment This Week -->
      <${SectionPanel}
        title="Due for payment this week"
        detail=${dueThisWeek.length > 0
          ? `${dueThisWeek.length} approved invoice${dueThisWeek.length === 1 ? '' : 's'} scheduled for payment in the next 7 days.`
          : 'No payments due this week.'}
        actionLabel=${dueThisWeek.length > 0 ? 'Open invoices' : ''}
        onAction=${() => navigate('clearledgr/invoices')}
        panelMinHeight=${160}
      >
        ${dueThisWeek.length > 0
          ? html`<div class="home-list-stack">
              ${dueThisWeek.map((item, i) => html`
                <${AuditEventRow}
                  key=${item.id || i}
                  entry=${{
                    title: `${item.vendor_name || item.vendor || 'Unknown'} — ${formatAmount(item.amount, item.currency)}`,
                    detail: `Due ${fmtDateTime(item.due_date || item.payment_due_date)}`,
                    ts: item.due_date || item.payment_due_date,
                    operator_severity: 'info',
                  }}
                  actionLabel="Open"
                  onAction=${() => openRecord(item.id, { id: item.id })}
                />
              `)}
            </div>`
          : html`<${EmptyPanelState} text="Approved invoices scheduled for payment in the next 7 days will appear here." />`}
      </${SectionPanel}>

      <!-- §6.1 Section 4: Agent Actions Today -->
      <${SectionPanel}
        title="Agent actions today"
        detail=${agentActionsToday.length > 0
          ? `${agentActionsToday.length} action${agentActionsToday.length === 1 ? '' : 's'} since midnight.`
          : 'No agent actions yet today.'}
        actionLabel="Open activity"
        onAction=${() => navigate('clearledgr/activity')}
        panelMinHeight=${160}
      >
        ${agentActionsToday.length > 0
          ? html`<div class="home-list-stack">
              ${agentActionsToday.map((entry, index) => {
                const recordId = String(entry?.ap_item_id || '').trim();
                const auditRow = buildAuditRow(entry);
                return html`<${AuditEventRow}
                  key=${entry?.id || `${entry?.ts || 'event'}:${index}`}
                  entry=${{ ...entry, ...auditRow, operator_severity: auditRow.severity }}
                  actionLabel=${recordId ? 'Open' : ''}
                  onAction=${() => recordId && openRecord(recordId, { id: recordId })}
                />`;
              })}
            </div>`
          : html`<${EmptyPanelState} text="The agent's actions since midnight will appear here — one line each." />`}
      </${SectionPanel}>
    </div>

    <div class="home-panel-grid">
      <!-- §6.1 Section 5: Vendor Onboarding Blockers -->
      <${SectionPanel}
        title="Vendor onboarding blockers"
        detail="Vendors stuck in onboarding for more than 48 hours."
        panelMinHeight=${120}
      >
        ${onboardingBlockers.length === 0
          ? html`<${EmptyPanelState} text="No blocked vendor onboarding engagements." />`
          : onboardingBlockers.map((b) => html`
            <div class="home-blocker-row" key=${b.id || b.vendor_name} onClick=${() => navigate('clearledgr/vendor/' + encodeURIComponent(b.vendor_name || ''))} style="cursor:pointer;padding:6px 0;border-bottom:1px solid #f0f0ed;display:flex;align-items:baseline;gap:8px;">
              <span style="font:600 13px/1.3 'DM Sans',sans-serif;color:#1b1b1b;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${b.vendor_name || 'Unknown vendor'}</span>
              <span style="font:500 11px/1 'DM Sans',sans-serif;color:#92400e;flex-shrink:0;">${b.state?.replace(/_/g, ' ')}</span>
              <span style="font:500 11px/1 'Geist Mono',monospace;color:#6b7280;flex-shrink:0;margin-left:auto;">${b.days}d</span>
            </div>
            <div style="font:400 11px/1.3 'DM Sans',sans-serif;color:#6b7280;padding:0 0 4px;">${b.reason}</div>
          `)}
      </${SectionPanel}>

      <!-- §6.1 Section 6: Quick Access -->
      <${SectionPanel}
        title="Quick access"
        detail="One-click shortcuts."
        panelMinHeight=${120}
      >
        <div class="home-quick-row">
          <${ToolbarAction} label="AP Invoices" detail="Open the invoice pipeline." meta="Pipeline" onClick=${() => navigate('clearledgr/invoices')} />
          <${ToolbarAction} label="Vendor Onboarding" detail="Open the vendor pipeline." meta="Pipeline" onClick=${() => navigate('clearledgr/vendor-onboarding')} />
          <${ToolbarAction} label="Agent Activity" detail="Full feed of all agent actions." meta="Feed" onClick=${() => navigate('clearledgr/activity')} />
        </div>
      </${SectionPanel}>
    </div>
  `;
}
