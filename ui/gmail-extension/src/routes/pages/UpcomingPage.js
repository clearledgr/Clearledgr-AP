/**
 * Upcoming Page — AP follow-up list modeled on Streak's Upcoming surface.
 * Keeps due approvals, vendor replies, and posting retries visible without
 * turning Gmail into a generic task dashboard.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDateTime, useAction } from '../route-helpers.js';
import { formatAmount, openSourceEmail } from '../../utils/formatters.js';
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
  return html`<div class="reports-metric-card">
    <div class="reports-metric-value" style=${`color:${accent}`}>${Number(value || 0).toLocaleString()}</div>
    <div class="reports-metric-detail" style="margin-top:4px">${label}</div>
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
    navigate('clearledgr/invoices');
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
        <button class="btn-primary btn-sm" onClick=${() => navigate('clearledgr/invoices')}>Open invoices</button>
      </div>
    </div>

    <div class="secondary-stat-grid" style="margin:0 0 18px">
      <${SummaryCard} label="Total follow-ups" value=${summary.total || 0} />
      <${SummaryCard} label="Overdue" value=${summary.overdue || 0} tone="danger" />
      <${SummaryCard} label="Today" value=${summary.today || 0} tone="warning" />
      <${SummaryCard} label="This week" value=${summary.this_week || 0} tone="success" />
    </div>

    <div class="panel">
      <div class="panel-head compact">
        <div>
          <h3 style="margin:0 0 4px">What is due</h3>
          <p class="muted" style="margin:0">Only the follow-ups that can move work forward show up here.</p>
        </div>
        ${groupedCounts.length > 0 && html`
          <div class="reports-chip-wrap">
            ${groupedCounts.map(([kind, count]) => html`
              <span key=${kind} class="secondary-chip" style="font-size:12px">
                ${KIND_LABELS[kind] || kind.replace(/_/g, ' ')}
                <strong style="color:var(--ink)">${count}</strong>
              </span>
            `)}
          </div>
        `}
      </div>

      ${tasks.length === 0
        ? html`<p class="muted" style="margin:0">No upcoming follow-ups yet.</p>`
        : html`<div class="secondary-card-list">
            ${tasks.map((task) => html`
              <div key=${task.id} class="secondary-card">
                <div class="secondary-card-head">
                  <div class="secondary-card-copy">
                    <div class="secondary-inline-actions" style="margin-bottom:4px">
                      <strong class="secondary-card-title">${task.title || 'Follow-up'}</strong>
                      <${StatusPill} status=${task.status} />
                      <span class="muted" style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.02em">
                        ${KIND_LABELS[task.kind] || task.kind?.replace(/_/g, ' ') || 'Follow-up'}
                      </span>
                    </div>
                    <div class="secondary-card-meta" style="font-size:13px;font-weight:600;color:var(--ink-secondary)">
                      ${task.vendor_name || 'Unknown vendor'} · ${task.invoice_number || 'No invoice #'} · ${formatAmount(task.amount, task.currency)}
                    </div>
                    <div class="secondary-card-meta" style="margin-top:6px">${task.detail}</div>
                    <div class="secondary-card-meta" style="margin-top:8px">
                      ${task.due_at ? `Due ${fmtDateTime(task.due_at)}` : 'No explicit follow-up time'}
                    </div>
                  </div>
                  <div class="secondary-card-actions" style="margin-top:0">
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
