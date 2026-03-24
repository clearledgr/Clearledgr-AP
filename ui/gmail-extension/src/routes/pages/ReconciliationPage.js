/**
 * Reconciliation Page — optional groundwork for future skill expansion.
 * AP remains the primary production workflow.
 */
import { h } from 'preact';
import { useState, useCallback } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

function Step({ number, text }) {
  return html`<div style="display:flex;align-items:flex-start;gap:10px;padding:8px 0">
    <div style="
      width:24px;height:24px;border-radius:50%;flex-shrink:0;
      display:flex;align-items:center;justify-content:center;
      background:var(--accent);color:var(--navy);font-size:12px;font-weight:700;
      font-family:var(--font-display);
    ">${number}</div>
    <span style="font-size:13px;color:var(--ink-secondary);padding-top:2px">${text}</span>
  </div>`;
}

export default function ReconciliationPage({ api, toast, orgId, onRefresh }) {
  const [sheetUrl, setSheetUrl] = useState('');
  const [range, setRange] = useState('Sheet1!A:F');
  const [starting, setStarting] = useState(false);
  const [result, setResult] = useState(null);

  const startRecon = useCallback(async () => {
    if (!sheetUrl.trim()) return;
    setStarting(true);
    try {
      const match = sheetUrl.match(/\/spreadsheets\/d\/([a-zA-Z0-9_-]+)/);
      const spreadsheetId = match ? match[1] : sheetUrl.trim();
      const r = await api('/api/agent/execute-intent', {
        method: 'POST',
        body: JSON.stringify({
          intent: 'start_reconciliation',
          organization_id: orgId,
          payload: { spreadsheet_id: spreadsheetId, range: range.trim() },
        }),
      });
      setResult(r);
      toast('Reconciliation started.', 'success');
      onRefresh();
    } catch (e) {
      toast('Failed: ' + e.message, 'error');
    } finally {
      setStarting(false);
    }
  }, [sheetUrl, range, orgId, api, toast, onRefresh]);

  return html`
    <div class="secondary-banner">
      <div class="secondary-banner-copy">
        <h3>Reconciliation tools</h3>
        <p class="muted">Use this page when you want to test or run reconciliation work from a spreadsheet.</p>
      </div>
    </div>

    <div class="secondary-shell">
      <div class="secondary-main">
        <div class="panel">
          <h3 style="margin:0 0 10px">Start a reconciliation run</h3>
          <div style="display:flex;flex-direction:column;gap:14px">
            <div>
              <label style="display:block;margin-bottom:4px">Google Sheet URL</label>
              <input placeholder="https://docs.google.com/spreadsheets/d/..." value=${sheetUrl} onInput=${e => setSheetUrl(e.target.value)} />
            </div>
            <div>
              <label style="display:block;margin-bottom:4px">Sheet range</label>
              <input placeholder="Sheet1!A:F" value=${range} onInput=${e => setRange(e.target.value)} />
            </div>
            <button onClick=${startRecon} disabled=${starting || !sheetUrl.trim()} style="padding:12px;font-size:14px;font-weight:600;font-family:var(--font-display)">
              ${starting ? 'Starting\u2026' : 'Start run'}
            </button>
          </div>

          ${result && html`
            <div style="margin-top:16px;padding:14px;background:#ECFDF5;border:1px solid #A7F3D0;border-radius:var(--radius-sm)">
              <div style="font-weight:600;font-size:13px;color:#059669;margin-bottom:4px">Session started</div>
              <div style="font-family:var(--font-mono);font-size:12px;color:var(--ink-secondary)">${result.details?.session_id || 'Created'}</div>
              <div class="muted" style="margin-top:4px">${result.details?.next_step || 'Agent will import and match transactions.'}</div>
            </div>
          `}
        </div>
      </div>

      <div class="secondary-side">
        <div class="panel" style="background:var(--bg)">
          <h3 style="font-size:14px;margin-bottom:12px">What this run does</h3>
          <${Step} number="1" text="Import transactions from your Google Sheet" />
          <${Step} number="2" text="Match each transaction against posted invoices by amount, date, vendor, and reference" />
          <${Step} number="3" text="Flag exceptions for human review" />
          <${Step} number="4" text="Write reconciliation results back to your sheet" />
          <div style="margin-top:16px;padding:12px;background:var(--surface);border-radius:var(--radius-sm);border:1px solid var(--border)">
            <div style="font-size:12px;font-weight:500;color:var(--ink-secondary);margin-bottom:4px">Matching signals</div>
            <div style="font-size:11px;color:var(--ink-muted);line-height:1.6">
              Amount (35%) \u00B7 Date (25%) \u00B7 Vendor name (25%) \u00B7 Reference # (15%)
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}
