/**
 * Shared helpers for admin route pages.
 * Extracted from static/console/app.js to be reusable across
 * InboxSDK routes and (future) Outlook/Sheets surfaces.
 */
import { h } from 'preact';
import { useState, useCallback } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

const TZ = 'Europe/London';
const LOCALE = 'en-GB';

export function fmtDateTime(v) {
  if (!v) return '';
  const d = new Date(v);
  if (isNaN(d.getTime())) return '';
  try { return d.toLocaleString(LOCALE, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TZ }); }
  catch { return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
}

export function fmtDate(v) {
  if (!v) return '';
  const d = new Date(v);
  if (isNaN(d.getTime())) return '';
  try { return d.toLocaleDateString(LOCALE, { day: '2-digit', month: 'short', timeZone: TZ }); }
  catch { return d.toLocaleDateString([], { month: 'short', day: 'numeric' }); }
}

export function fmtTime(v) {
  if (!v) return '';
  const d = new Date(v);
  if (isNaN(d.getTime())) return '';
  try { return d.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TZ }); }
  catch { return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
}

export function fmtRate(v) { const n = Number(v); return isFinite(n) ? `${n.toFixed(1)}%` : '0.0%'; }
export function fmtDollar(v) { return '$' + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 }); }

export function hasOpsAccess(bootstrap) {
  return ['admin', 'owner', 'operator'].includes(String(bootstrap?.current_user?.role || '').trim().toLowerCase());
}

export function integrationByName(bootstrap, name) {
  return (bootstrap?.integrations || []).find(i => i.name === name) || {};
}

export function statusBadge(ok) {
  return html`<span class="status-badge ${ok ? 'connected' : ''}">${ok ? 'Connected' : 'Not connected'}</span>`;
}

export function checkMark(ok) {
  return ok
    ? html`<span class="check-ok">Connected</span>`
    : html`<span class="check-no">Not connected</span>`;
}

export function eventBadge(eventType) {
  const t = (eventType || '').toLowerCase();
  if (t.includes('posted') || t.includes('closed')) return { label: 'Posted', cls: 'ev-posted' };
  if (t.includes('approved') || t.includes('auto_approved')) return { label: 'Approved', cls: 'ev-approved' };
  if (t.includes('rejected')) return { label: 'Rejected', cls: 'ev-rejected' };
  if (t.includes('needs_approval') || t.includes('pending')) return { label: 'Pending review', cls: 'ev-pending' };
  if (t.includes('received') || t.includes('classified')) return { label: 'Received', cls: 'ev-received' };
  if (t.includes('validated')) return { label: 'Validated', cls: 'ev-validated' };
  if (t.includes('failed') || t.includes('error')) return { label: 'Error', cls: 'ev-error' };
  if (t === 'state_transition') return { label: 'Status changed', cls: 'ev-received' };
  if (t === 'decision_made') return { label: 'Decision recorded', cls: 'ev-approved' };
  if (t === 'invoice_created') return { label: 'Invoice created', cls: 'ev-received' };
  if (t === 'enrichment_complete') return { label: 'Data extracted', cls: 'ev-validated' };
  return { label: eventType.replace(/_/g, ' ').toLowerCase(), cls: '' };
}

export function humanizeStatus(raw) {
  const map = { connected: 'Connected', disconnected: 'Not connected', unknown: 'Unknown' };
  return map[raw] || raw;
}

export function humanizeMode(raw) {
  const map = { oauth: 'OAuth sign-in', shared: 'Shared workspace', per_org: 'Per-organization', '': '-', '-': '-' };
  return map[raw] || raw;
}

export function resolveRef(item) { return String(item?.thread_id || item?.message_id || item?.id || '').trim(); }

export function useAction(fn) {
  const [pending, setPending] = useState(false);
  const exec = useCallback(async (...args) => {
    if (pending) return;
    setPending(true);
    try { await fn(...args); }
    finally { setPending(false); }
  }, [fn, pending]);
  return [exec, pending];
}
