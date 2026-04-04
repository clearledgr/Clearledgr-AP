/**
 * Clearledgr Outlook Add-in — Office.js entry point.
 *
 * This replaces InboxSDK from the Gmail extension. It:
 * 1. Reads the current email context via Office.js mailbox API
 * 2. Authenticates with the Clearledgr backend
 * 3. Renders the sidebar UI (Preact, shared components)
 * 4. Handles actions (approve, reject, escalate, etc.)
 */

import { h, render } from 'https://esm.sh/preact@10.29.0';
import { useState, useEffect, useCallback } from 'https://esm.sh/preact@10.29.0/hooks';
import htm from 'https://esm.sh/htm@3.1.1';

const html = htm.bind(h);

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const API_BASE = (() => {
  const hostname = window.location.hostname;
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return 'http://localhost:8010';
  }
  return 'https://api.clearledgr.com';
})();

// ---------------------------------------------------------------------------
// Office.js bootstrap
// ---------------------------------------------------------------------------

Office.onReady((info) => {
  if (info.host === Office.HostType.Outlook) {
    render(html`<${App}/>`, document.getElementById('clearledgr-root'));
  }
});

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

let _authToken = null;
let _authTokenExpiry = 0;

async function backendFetch(path, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };
  if (_authToken) {
    headers['Authorization'] = `Bearer ${_authToken}`;
  }
  const resp = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API ${resp.status}: ${text.slice(0, 200)}`);
  }
  if (resp.status === 204) return {};
  return resp.json();
}

// ---------------------------------------------------------------------------
// Office.js email context reader
// ---------------------------------------------------------------------------

function readMailboxItem() {
  return new Promise((resolve) => {
    const item = Office.context.mailbox.item;
    if (!item) {
      resolve(null);
      return;
    }

    const context = {
      itemId: item.itemId,
      conversationId: item.conversationId,
      subject: item.subject || '',
      sender: '',
      dateReceived: item.dateTimeCreated ? item.dateTimeCreated.toISOString() : '',
      hasAttachments: false,
      attachments: [],
    };

    // Sender
    if (item.from) {
      context.sender = item.from.emailAddress || '';
    }

    // Attachments
    if (item.attachments && item.attachments.length > 0) {
      context.hasAttachments = true;
      context.attachments = item.attachments.map(a => ({
        id: a.id,
        name: a.name,
        contentType: a.contentType,
        size: a.size,
        isInline: a.isInline,
      }));
    }

    resolve(context);
  });
}

// ---------------------------------------------------------------------------
// Auth via backend token exchange
// ---------------------------------------------------------------------------

async function ensureAuth() {
  if (_authToken && Date.now() < _authTokenExpiry) {
    return true;
  }
  // Try to get token from Office SSO
  try {
    const ssoToken = await Office.auth.getAccessToken({ allowSignInPrompt: true });
    // Exchange SSO token for Clearledgr backend token
    const data = await fetch(`${API_BASE}/outlook/callback?code=${encodeURIComponent(ssoToken)}&state=outlook_sso`, {
      method: 'GET',
    });
    // For now, store the SSO token directly — backend validates it
    _authToken = ssoToken;
    _authTokenExpiry = Date.now() + 3600 * 1000;
    return true;
  } catch (err) {
    console.warn('Clearledgr: SSO auth failed, falling back to manual connect', err);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

function Header({ connected, email }) {
  return html`
    <div class="cl-header">
      <div class="cl-header-logo">
        <span class="cl-status-dot ${connected ? '' : 'error'}"></span>
        <span>Clearledgr</span>
      </div>
      <span style="font-size:11px;opacity:0.7">${email || ''}</span>
    </div>
  `;
}

function AuthPrompt({ onConnect }) {
  return html`
    <div class="cl-auth-prompt">
      <h3>Connect Clearledgr</h3>
      <p>Link your Microsoft 365 account to start processing invoices from your inbox.</p>
      <button class="cl-btn-primary" onClick=${onConnect}>
        Connect Microsoft 365
      </button>
    </div>
  `;
}

function StatePill({ state }) {
  const label = (state || '').replace(/_/g, ' ');
  return html`<span class="cl-state-pill ${state}">${label}</span>`;
}

function FieldCheck({ label, value, confidence }) {
  const cls = !value ? 'error' : (confidence && confidence < 0.85) ? 'warn' : 'ok';
  const icon = cls === 'ok' ? '\u2713' : cls === 'warn' ? '?' : '\u2717';
  return html`<div class="cl-field ${cls}">${icon} ${label}</div>`;
}

function InvoiceCard({ item, onAction }) {
  if (!item) return null;
  const fc = item.field_confidences || {};
  return html`
    <div class="cl-card">
      <div class="cl-card-header">
        <div>
          <div class="cl-vendor-name">${item.vendor_name || item.vendor || 'Unknown'}</div>
          <div style="font-size:12px;color:var(--cl-text-secondary)">
            ${item.invoice_number || ''} ${item.due_date ? `\u00B7 Due ${item.due_date}` : ''}
          </div>
        </div>
        <div style="text-align:right">
          <div class="cl-amount">${item.currency || 'USD'} ${(item.amount || 0).toLocaleString()}</div>
          <${StatePill} state=${item.state}/>
        </div>
      </div>
      <div class="cl-fields">
        <${FieldCheck} label="Vendor" value=${item.vendor_name} confidence=${fc.vendor}/>
        <${FieldCheck} label="Amount" value=${item.amount} confidence=${fc.amount}/>
        <${FieldCheck} label="Due date" value=${item.due_date} confidence=${fc.due_date}/>
        <${FieldCheck} label="PO" value=${item.po_number} confidence=${fc.po_number}/>
      </div>
      <div class="cl-actions">
        ${item.state === 'validated' && html`
          <button class="cl-btn-primary" onClick=${() => onAction('submit-for-approval', item)}>
            Submit for Approval
          </button>
        `}
        ${item.state === 'approved' && html`
          <button class="cl-btn-primary" onClick=${() => onAction('approve-and-post', item)}>
            Post to ERP
          </button>
        `}
        <button class="cl-btn-secondary" onClick=${() => onAction('reject', item)}>
          Reject
        </button>
      </div>
    </div>
  `;
}

function Timeline({ events }) {
  if (!events || !events.length) return null;
  return html`
    <div class="cl-timeline">
      <div style="font-weight:600;font-size:12px;margin-bottom:6px">Activity</div>
      ${events.slice(0, 8).map(ev => html`
        <div class="cl-timeline-event">
          <div class="cl-timeline-dot ${ev.status || 'completed'}"></div>
          <div>
            <div style="font-weight:500">${ev.title || ev.event_type || ''}</div>
            <div style="color:var(--cl-text-secondary)">${ev.detail || ev.decision_reason || ''}</div>
          </div>
        </div>
      `)}
    </div>
  `;
}

function WorkList({ items, onSelect }) {
  if (!items || !items.length) {
    return html`
      <div class="cl-empty">
        <h4>No invoices yet</h4>
        <div>Invoices from your inbox will appear here</div>
      </div>
    `;
  }
  return html`
    <div>
      ${items.map(item => html`
        <div class="cl-card" style="cursor:pointer" onClick=${() => onSelect(item)}>
          <div class="cl-card-header">
            <div>
              <div class="cl-vendor-name">${item.vendor_name || item.vendor || '?'}</div>
              <div style="font-size:11px;color:var(--cl-text-secondary)">${item.invoice_number || ''}</div>
            </div>
            <div style="text-align:right">
              <div class="cl-amount">${item.currency || 'USD'} ${(item.amount || 0).toLocaleString()}</div>
              <${StatePill} state=${item.state}/>
            </div>
          </div>
        </div>
      `)}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------

function App() {
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [email, setEmail] = useState('');
  const [mailContext, setMailContext] = useState(null);
  const [currentItem, setCurrentItem] = useState(null);
  const [worklist, setWorklist] = useState([]);
  const [timeline, setTimeline] = useState([]);
  const [error, setError] = useState(null);

  // Initialize
  useEffect(() => {
    (async () => {
      try {
        const authed = await ensureAuth();
        setAuthenticated(authed);

        if (authed) {
          // Read current email context
          const ctx = await readMailboxItem();
          setMailContext(ctx);

          // Load worklist
          try {
            const wl = await backendFetch('/extension/worklist?limit=50');
            setWorklist(wl.items || []);
          } catch (e) {
            console.warn('Worklist load failed:', e);
          }

          // If we have a current email, try to find its AP item
          if (ctx && ctx.itemId) {
            try {
              const item = await backendFetch(`/extension/by-thread/${encodeURIComponent(ctx.conversationId || ctx.itemId)}`);
              if (item && item.id) {
                setCurrentItem(item);
                // Load timeline
                try {
                  const audit = await backendFetch(`/api/ap/items/${item.id}/audit`);
                  setTimeline(audit.events || []);
                } catch (e) {
                  console.warn('Timeline load failed:', e);
                }
              }
            } catch (e) {
              // No AP item for this email — that's fine
            }
          }
        }
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleConnect = useCallback(() => {
    // Open auth in dialog
    Office.context.ui.displayDialogAsync(
      `${API_BASE}/outlook/connect/start`,
      { height: 60, width: 40 },
      (result) => {
        if (result.status === Office.AsyncResultStatus.Succeeded) {
          const dialog = result.value;
          dialog.addEventHandler(Office.EventType.DialogMessageReceived, () => {
            dialog.close();
            window.location.reload();
          });
        }
      }
    );
  }, []);

  const handleAction = useCallback(async (actionType, item) => {
    try {
      const endpoint = `/extension/${actionType}`;
      await backendFetch(endpoint, {
        method: 'POST',
        body: JSON.stringify({
          email_id: item.message_id || item.email_id || mailContext?.itemId,
          ap_item_id: item.id,
          vendor: item.vendor_name || item.vendor,
          amount: item.amount,
          currency: item.currency,
          invoice_number: item.invoice_number,
          confidence: item.confidence || 0.9,
          organization_id: item.organization_id,
        }),
      });
      // Refresh
      const wl = await backendFetch('/extension/worklist?limit=50');
      setWorklist(wl.items || []);
      setCurrentItem(null);
    } catch (e) {
      setError(e.message);
    }
  }, [mailContext]);

  if (loading) {
    return html`
      <${Header} connected=${false}/>
      <div class="cl-loading">
        <div class="cl-spinner"></div>
        <span>Loading...</span>
      </div>
    `;
  }

  if (!authenticated) {
    return html`
      <${Header} connected=${false}/>
      <${AuthPrompt} onConnect=${handleConnect}/>
    `;
  }

  return html`
    <${Header} connected=${true} email=${email}/>
    <div class="cl-content">
      ${currentItem
        ? html`
          <${InvoiceCard} item=${currentItem} onAction=${handleAction}/>
          <${Timeline} events=${timeline}/>
        `
        : html`
          <${WorkList} items=${worklist} onSelect=${(item) => setCurrentItem(item)}/>
        `
      }
      ${error && html`
        <div style="padding:12px;color:var(--cl-error);font-size:12px">${error}</div>
      `}
    </div>
  `;
}
