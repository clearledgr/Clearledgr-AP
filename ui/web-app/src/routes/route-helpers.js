/**
 * Shared helpers for SPA routes. Lifted from
 * `ui/gmail-extension/src/routes/route-helpers.js` with two changes:
 *   1) Capability lookups read from the bootstrap context provided by
 *      the SPA shell instead of being passed through every page prop.
 *   2) Action helpers (`useAction`) are unchanged — pure pending-state
 *      wrapper, no auth coupling.
 *
 * Keep this file in sync with the extension version where the
 * formatters/badges are concerned, so deep-linked state strings render
 * identically across surfaces.
 */
import { h } from 'preact';
import { useState, useCallback } from 'preact/hooks';
import htm from 'htm';
import {
  getCapabilities,
  hasAdminCapability,
  hasCapability,
  hasOpsCapability,
} from '../utils/capabilities.js';

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

// Money formatting lives in utils/formatters.js — `formatAmount`. Pass
// `{ decimals: 0 }` for aggregate / round-figure displays. Don't add
// a money helper here.

export function hasOpsAccess(bootstrap) { return hasOpsCapability(bootstrap); }
export function hasAdminAccess(bootstrap) { return hasAdminCapability(bootstrap); }
export { getCapabilities, hasCapability };

export function integrationByName(bootstrap, name) {
  return (bootstrap?.integrations || []).find((i) => i.name === name) || {};
}

export function statusBadge(ok) {
  return html`<span class=${`status-badge ${ok ? 'connected' : ''}`}>${ok ? 'Connected' : 'Not connected'}</span>`;
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
  return { label: (eventType || '').replace(/_/g, ' ').toLowerCase(), cls: '' };
}

export function humanizeStatus(raw) {
  const token = String(raw || '').trim().toLowerCase();
  const map = {
    connected: 'Connected',
    disconnected: 'Not connected',
    reconnect_required: 'Reconnect needed',
    reauthorization_required: 'Reconnect needed',
    degraded: 'Needs attention',
    error: 'Needs attention',
    unknown: 'Unknown',
  };
  return map[token] || String(raw || '').replace(/_/g, ' ');
}

export function humanizeMode(raw) {
  const token = String(raw || '').trim().toLowerCase();
  const map = {
    oauth: 'OAuth sign-in',
    shared: 'Shared workspace',
    per_org: 'Per workspace',
    '': 'Not set',
    '-': 'Not set',
  };
  return map[token] || String(raw || '').replace(/_/g, ' ');
}

export function resolveRef(item) { return String(item?.thread_id || item?.message_id || item?.id || '').trim(); }

export function useAction(fn) {
  const [pending, setPending] = useState(false);
  const exec = useCallback(async (...args) => {
    if (pending) return;
    setPending(true);
    try {
      await fn(...args);
    } catch (err) {
      // Surface failures instead of swallowing them. The earlier
      // shape had no catch, which meant every 4xx/5xx from the
      // wrapped api() call disappeared with zero UI feedback — the
      // button looked like a no-op even when the server returned
      // a real validation error. Always log; dispatch a global
      // toast event the AppShell listens for so the user sees the
      // failure even if the call site forgot a try/catch.
      // eslint-disable-next-line no-console
      console.error('useAction error:', err);
      if (typeof window !== 'undefined') {
        try {
          window.dispatchEvent(
            new CustomEvent('solden:action-error', {
              detail: {
                message: _errorMessage(err),
                status: err?.status ?? null,
                payload: err?.payload ?? null,
              },
            }),
          );
        } catch { /* old browsers / no-op */ }
      }
      // Re-throw so call sites that DO have their own try/catch
      // still see the error and can take action on it.
      throw err;
    } finally {
      setPending(false);
    }
  }, [fn, pending]);
  return [exec, pending];
}

function _errorMessage(err) {
  if (!err) return 'Something went wrong.';
  // ApiError shape (see ui/web-app/src/api/client.js): the parsed
  // JSON body lives on .payload, status code on .status. FastAPI
  // detail can be a string or a structured object — handle both.
  const payload = err.payload;
  if (payload && typeof payload === 'object') {
    if (typeof payload.detail === 'string') return payload.detail;
    if (payload.detail && typeof payload.detail === 'object') {
      if (typeof payload.detail.reason === 'string') return payload.detail.reason;
      if (typeof payload.detail.message === 'string') return payload.detail.message;
    }
    if (typeof payload.message === 'string') return payload.message;
  }
  return err.message || String(err);
}
