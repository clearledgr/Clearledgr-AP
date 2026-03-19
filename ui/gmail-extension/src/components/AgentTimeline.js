/** AgentTimeline — grouped timeline entries for agent + audit events */
import { h } from 'preact';
import htm from 'htm';
import {
  getStateLabel, formatTimestamp, trimText, prettifyEventType,
  getAuditEventPayload, getAuditEventTimestamp, normalizeAuditEventType,
} from '../utils/formatters.js';

const html = htm.bind(h);

const BUCKETS = [
  { id: 'blocked', label: 'Blocked / Failed' },
  { id: 'awaiting_approval', label: 'Awaiting Approval' },
  { id: 'executing', label: 'Executing' },
  { id: 'planned', label: 'Planned' },
  { id: 'completed', label: 'Completed' },
];

function classifyBucketFromState(state) {
  const s = String(state || '').toLowerCase();
  if (s === 'received') return 'planned';
  if (s === 'needs_approval') return 'awaiting_approval';
  if (['needs_info', 'failed_post', 'rejected'].includes(s)) return 'blocked';
  if (['validated', 'approved', 'ready_to_post'].includes(s)) return 'executing';
  return 'completed';
}

function classifyAgentBucket(event) {
  const s = String(event?.status || '').toLowerCase();
  if (s === 'blocked_for_approval') return 'awaiting_approval';
  if (['queued', 'preview'].includes(s)) return 'planned';
  if (['running', 'submitted'].includes(s)) return 'executing';
  if (['failed', 'denied'].includes(s)) return 'blocked';
  return 'completed';
}

function classifyAuditBucket(event) {
  const eventType = normalizeAuditEventType(event?.event_type || event?.eventType || '');
  const payload = getAuditEventPayload(event);
  const toState = event?.to_state || payload?.to_state || payload?.toState;
  if (toState) return classifyBucketFromState(toState);
  if (eventType.includes('failed') || eventType.includes('rejected') || eventType.includes('invalid')) return 'blocked';
  if (eventType.includes('approval')) return eventType.includes('processed') || eventType.includes('approved') ? 'completed' : 'awaiting_approval';
  if (eventType.includes('preview') || eventType.includes('dispatch') || eventType.includes('retry')) return 'executing';
  return 'completed';
}

function buildEntries(agentEvents, auditEvents, maxEntries = 14) {
  const entries = [];

  (Array.isArray(agentEvents) ? agentEvents : []).forEach((event, i) => {
    const requestPayload = event?.request_payload || event?.requestPayload || {};
    entries.push({
      key: `agent:${event?.command_id || i}`,
      source: 'Agent',
      bucket: classifyAgentBucket(event),
      title: trimText(requestPayload?.step || String(event?.tool_name || 'agent action').replace(/_/g, ' '), 72),
      status: String(event?.status || 'queued').replace(/_/g, ' '),
      detail: trimText(event?.result_payload?.summary || requestPayload?.detail || '', 120),
      ts: getAuditEventTimestamp(event),
      timeLabel: formatTimestamp(event?.updated_at || event?.created_at),
    });
  });

  (Array.isArray(auditEvents) ? auditEvents : []).forEach((event, i) => {
    const payload = getAuditEventPayload(event);
    const eventType = normalizeAuditEventType(event?.event_type || event?.eventType || '');
    const fromState = String(event?.from_state || payload?.from_state || '').trim();
    const toState = String(event?.to_state || payload?.to_state || '').trim();
    const title = (fromState || toState)
      ? `${getStateLabel(fromState || 'received')} \u2192 ${getStateLabel(toState || fromState || 'received')}`
      : prettifyEventType(eventType || 'audit_event');
    const reason = String(event?.decision_reason || event?.reason || payload?.reason || payload?.error_message || '').trim();

    entries.push({
      key: `audit:${event?.id || i}`,
      source: 'Audit',
      bucket: classifyAuditBucket(event),
      title: trimText(title, 72),
      status: trimText(String(toState || eventType || 'audit').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()), 36),
      detail: trimText(reason, 120),
      ts: getAuditEventTimestamp(event),
      timeLabel: formatTimestamp(event?.ts || event?.created_at),
    });
  });

  return entries.sort((a, b) => (b.ts || 0) - (a.ts || 0)).slice(0, maxEntries);
}

export default function AgentTimeline({ agentEvents, auditEvents, loading }) {
  const entries = buildEntries(agentEvents, auditEvents);

  if (!entries.length && loading) {
    return html`<div class="cl-agent-timeline-empty">Loading timeline\u2026</div>`;
  }
  if (!entries.length) {
    return html`<div class="cl-agent-timeline-empty">No timeline events yet.</div>`;
  }

  const grouped = new Map();
  BUCKETS.forEach(b => grouped.set(b.id, []));
  entries.forEach(e => {
    const list = grouped.get(e.bucket) || grouped.get('completed');
    list.push(e);
  });

  return html`
    ${BUCKETS.map(bucket => {
      const items = grouped.get(bucket.id) || [];
      if (!items.length) return null;
      return html`
        <div key=${bucket.id} class="cl-agent-group">
          <div class="cl-agent-group-title">${bucket.label}</div>
          <div class="cl-agent-list">
            ${items.slice(0, 3).map(entry => html`
              <div key=${entry.key} class="cl-agent-row cl-agent-row-timeline" data-source=${entry.source.toLowerCase()}>
                <div class="cl-agent-row-main">
                  <span class="cl-agent-tool">${entry.title}</span>
                  <span class="cl-agent-status">${entry.status}</span>
                </div>
                <div class="cl-agent-timeline-meta">
                  <span class="cl-agent-source">${entry.source}</span>
                  ${entry.timeLabel && html`<span class="cl-agent-time">${entry.timeLabel}</span>`}
                </div>
                ${entry.detail && html`<div class="cl-agent-detail">${entry.detail}</div>`}
              </div>
            `)}
          </div>
        </div>
      `;
    })}
  `;
}
