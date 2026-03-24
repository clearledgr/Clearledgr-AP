/**
 * Upcoming Page — AP follow-up list modeled on Streak's Upcoming surface.
 * Keeps due approvals, vendor replies, and posting retries visible without
 * turning Gmail into a generic task dashboard.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDateTime, useAction } from '../route-helpers.js';
import { openSourceEmail } from '../../utils/formatters.js';
import { navigateToRecordDetail } from '../../utils/record-route.js';
import {
  activatePipelineSlice,
  clearPipelineNavigation,
  focusPipelineItem,
} from '../pipeline-views.js';

const html = htm.bind(h);

const STATUS_STYLES = {
  overdue: { bg: '#FEF2F2', text: '#B91C1C', label: 'Overdue' },
  today: { bg: '#FEF3C7', text: '#92400E', label: 'Today' },
  this_week: { bg: '#EFF6FF', text: '#1D4ED8', label: 'This week' },
  later: { bg: '#F8FAFC', text: '#475569', label: 'Later' },
  queued: { bg: '#F8FAFC', text: '#475569', label: 'Queued' },
};

const KIND_LABELS = {
  approval_follow_up: 'Approval',
  vendor_follow_up: 'Vendor reply',
  erp_retry: 'ERP retry',
  post_invoice: 'Posting',
  review_blocker: 'Blocker review',
};

function formatMoney(amount, currency = 'USD') {
  const value = Number(amount);
  if (!Number.isFinite(value)) return 'Amount unavailable';
  return `${currency} ${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function buildTaskLocator(task = {}) {
  return {
    id: task.ap_item_id,
    thread_id: task.thread_id,
    message_id: task.message_id,
    state: task.state,
  };
}

function StatusPill({ status }) {
  const tone = STATUS_STYLES[String(status || '').trim().toLowerCase()] || STATUS_STYLES.queued;
  return html`<span style="
    display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;
    background:${tone.bg};color:${tone.text};font-size:11px;font-weight:700;letter-spacing:0.02em;text-transform:uppercase;
  ">${tone.label}</span>`;
}

function SummaryCard({ label, value, tone = 'default' }) {
  const accent = tone === 'danger'
    ? '#B91C1C'
    : tone === 'warning'
      ? '#92400E'
      : tone === 'success'
        ? '#047857'
        : 'var(--ink)';
  return html`<div style="padding:18px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
    <div style="font-size:28px;font-weight:700;letter-spacing:-0.02em;color:${accent}">${Number(value || 0).toLocaleString()}</div>
    <div class="muted" style="font-size:12px;margin-top:4px">${label}</div>
  </div>`;
}

export default function UpcomingPage({ api, toast, orgId, userEmail, navigate }) {
  const pipelineScope = useMemo(() => ({ orgId, userEmail }), [orgId, userEmail]);
  const [payload, setPayload] = useState({ summary: {}, tasks: [] });
  const [loading, setLoading] = useState(true);

  const loadTasks = async ({ silent = false } = {}) => {
    setLoading(true);
    try {
      const data = await api(`/api/ap/items/upcoming?organization_id=${encodeURIComponent(orgId)}&limit=60`, { silent });
      setPayload({
        summary: data?.summary || {},
        tasks: Array.isArray(data?.tasks) ? data.tasks : [],
      });
    } catch {
      setPayload({ summary: {}, tasks: [] });
      if (!silent) toast?.('Could not load upcoming follow-ups.', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadTasks({ silent: true });
  }, [api, orgId]);

  const [refresh, refreshing] = useAction(async () => {
    await loadTasks();
    toast?.('Upcoming follow-ups refreshed.', 'success');
  });

  const openPipelineTask = (task) => {
    const sliceId = task?.recommended_slice || 'all_open';
    clearPipelineNavigation(pipelineScope);
    activatePipelineSlice(pipelineScope, sliceId);
    if (task?.ap_item_id) {
      focusPipelineItem(pipelineScope, buildTaskLocator(task), 'upcoming');
    }
    navigate('clearledgr/pipeline');
  };

  const openRecord = (task) => {
    if (!task?.ap_item_id) return;
    focusPipelineItem(pipelineScope, buildTaskLocator(task), 'upcoming');
    navigateToRecordDetail(navigate, task.ap_item_id);
  };

  const openEmail = (task) => {
    const ok = openSourceEmail({
      thread_id: task?.thread_id,
      message_id: task?.message_id,
      subject: task?.title || task?.invoice_number || 'Invoice follow-up',
    });
    if (!ok) {
      toast?.('Unable to open the source email thread.', 'error');
    }
  };

  const tasks = Array.isArray(payload?.tasks) ? payload.tasks : [];
  const summary = payload?.summary || {};
  const groupedCounts = Object.entries(summary.by_kind || {}).sort((left, right) => right[1] - left[1]);

  if (loading) {
    return html`<div class="panel" style="text-align:center;padding:48px"><p class="muted">Loading upcoming follow-ups…</p></div>`;
  }

  return html`
    <div class="secondary-banner">
      <div class="secondary-banner-copy">
        <h3>Upcoming follow-ups</h3>
        <p class="muted">See what needs attention next, then jump straight into the queue, the record, or the email.</p>
      </div>
      <div class="secondary-banner-actions">
        <button class="btn-secondary btn-sm" onClick=${refresh} disabled=${refreshing}>${refreshing ? 'Refreshing…' : 'Refresh'}</button>
        <button class="btn-primary btn-sm" onClick=${() => navigate('clearledgr/pipeline')}>Open pipeline</button>
      </div>
    </div>

    <div class="secondary-chip-row" style="margin:0 0 18px">
      <span class="secondary-chip">Total follow-ups ${summary.total || 0}</span>
      <span class="secondary-chip">Overdue ${summary.overdue || 0}</span>
      <span class="secondary-chip">Today ${summary.today || 0}</span>
      <span class="secondary-chip">This week ${summary.this_week || 0}</span>
    </div>

    <div class="panel">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px">
        <div>
          <h3 style="margin:0 0 4px">What is due</h3>
          <p class="muted" style="margin:0">Only the follow-ups that can move work forward show up here.</p>
        </div>
        ${groupedCounts.length > 0 && html`
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${groupedCounts.map(([kind, count]) => html`
              <span key=${kind} style="display:inline-flex;gap:6px;align-items:center;padding:5px 10px;border-radius:999px;border:1px solid var(--border);background:var(--bg);font-size:12px;font-weight:600;color:var(--ink-secondary)">
                ${KIND_LABELS[kind] || kind.replace(/_/g, ' ')}
                <strong style="color:var(--ink)">${count}</strong>
              </span>
            `)}
          </div>
        `}
      </div>

      ${tasks.length === 0
        ? html`<p class="muted" style="margin:0">Nothing is due right now.</p>`
        : html`<div style="display:flex;flex-direction:column;gap:12px">
            ${tasks.map((task) => html`
              <div key=${task.id} style="padding:14px 16px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface)">
                <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
                  <div style="min-width:0;flex:1">
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
                      <strong style="font-size:14px">${task.title || 'Follow-up'}</strong>
                      <${StatusPill} status=${task.status} />
                      <span class="muted" style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.02em">
                        ${KIND_LABELS[task.kind] || task.kind?.replace(/_/g, ' ') || 'Follow-up'}
                      </span>
                    </div>
                    <div style="font-size:13px;font-weight:600;color:var(--ink-secondary)">
                      ${task.vendor_name || 'Unknown vendor'} · ${task.invoice_number || 'No invoice #'} · ${formatMoney(task.amount, task.currency || 'USD')}
                    </div>
                    <div class="muted" style="font-size:12px;line-height:1.55;margin-top:6px">${task.detail}</div>
                    <div class="muted" style="font-size:12px;margin-top:8px">
                      ${task.due_at ? `Due ${fmtDateTime(task.due_at)}` : 'No explicit follow-up time'}
                    </div>
                  </div>
                  <div class="row-actions">
                    <button class="btn-secondary btn-sm" onClick=${() => openRecord(task)}>Open record</button>
                    <button class="btn-ghost btn-sm" onClick=${() => openPipelineTask(task)}>Open slice</button>
                    ${(task.thread_id || task.message_id) && html`<button class="btn-ghost btn-sm" onClick=${() => openEmail(task)}>Open email</button>`}
                  </div>
                </div>
              </div>
            `)}
          </div>`}
    </div>
  `;
}
