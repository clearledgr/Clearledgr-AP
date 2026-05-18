import { h } from 'preact';
import { useState, useEffect, useCallback, useMemo } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

const SCOPE_DESCRIPTIONS = {
  'records:read': 'Read AP items and other records via /v1/records.',
  'records:write': 'Create / update records (reserved — not exposed yet).',
  'intents:execute': 'Execute typed intents on /v1/intents (e.g. approve_invoice).',
  'intents:preview': 'Dry-run intents on /v1/intents/preview.',
  'audit:read': 'Read the org\'s audit chain via /v1/audit.',
  'webhooks:manage': 'CRUD outbound webhook subscriptions via /v1/webhooks.',
  // Legacy verb:noun — shown but deprioritised in the UI.
  'read:ap_items': 'Legacy — covered by records:read.',
  'write:ap_items': 'Legacy — covered by records:write + intents:execute.',
  'read:vendors': 'Legacy vendor read scope.',
  'write:vendors': 'Legacy vendor write scope.',
  'read:reports': 'Legacy report read scope.',
  'read:audit': 'Legacy — covered by audit:read.',
  'manage:webhooks': 'Legacy — covered by webhooks:manage.',
};

const NEW_VOCAB_SCOPES = new Set([
  'records:read', 'records:write',
  'intents:execute', 'intents:preview',
  'audit:read', 'webhooks:manage',
]);

export default function ApiKeysPage({ api, toast }) {
  const [keys, setKeys] = useState(null);
  const [scopeCatalog, setScopeCatalog] = useState([]);
  const [showRevoked, setShowRevoked] = useState(false);
  const [issuing, setIssuing] = useState(false);
  const [justIssued, setJustIssued] = useState(null);
  const [error, setError] = useState('');

  const fetchKeys = useCallback(async () => {
    try {
      const data = await api(
        `/api/workspace/api-keys${showRevoked ? '?include_revoked=true' : ''}`,
      );
      setKeys(Array.isArray(data?.api_keys) ? data.api_keys : []);
    } catch (e) {
      setError(e?.message || 'Failed to load API keys');
      setKeys([]);
    }
  }, [api, showRevoked]);

  useEffect(() => {
    fetchKeys();
    api('/api/workspace/api-keys/scopes/catalog')
      .then((d) => setScopeCatalog(Array.isArray(d?.scopes) ? d.scopes : []))
      .catch(() => setScopeCatalog([]));
  }, [fetchKeys]);

  const handleRevoke = useCallback(async (keyId, label) => {
    if (!window.confirm(
      `Revoke API key ${label || keyId}? Agents using this key will start failing immediately.`,
    )) return;
    try {
      await api(`/api/workspace/api-keys/${keyId}`, { method: 'DELETE' });
      toast('API key revoked', 'success');
      await fetchKeys();
    } catch (e) {
      toast(e?.message || 'Revoke failed', 'error');
    }
  }, [api, fetchKeys, toast]);

  const handleRotate = useCallback(async (keyId, label) => {
    if (!window.confirm(
      `Rotate API key ${label || keyId}? The old secret stops working immediately — every agent using it must redeploy with the new one.`,
    )) return;
    try {
      const result = await api(
        `/api/workspace/api-keys/${keyId}/rotate`,
        { method: 'POST' },
      );
      setJustIssued(result);
      toast('Key rotated — capture the new secret now', 'success');
      await fetchKeys();
    } catch (e) {
      toast(e?.message || 'Rotate failed', 'error');
    }
  }, [api, fetchKeys, toast]);

  return html`
    <div class="secondary-banner">
      <div class="secondary-banner-copy">
        <h3>API keys</h3>
        <p class="muted">
          Credentials your agents use to call <code>/v1/*</code>. Each key carries an agent identity, a scope set, and an optional expiry — every audit row written under it is attributed.
        </p>
      </div>
      <div class="secondary-banner-actions">
        <button class="primary" onClick=${() => { setIssuing(true); setJustIssued(null); }}>
          Issue new key
        </button>
      </div>
    </div>

    ${justIssued && html`<${SecretReveal} record=${justIssued} onDismiss=${() => setJustIssued(null)} />`}

    ${issuing && html`<${IssueKeyModal}
      scopeCatalog=${scopeCatalog}
      api=${api}
      onClose=${() => setIssuing(false)}
      onIssued=${(record) => { setIssuing(false); setJustIssued(record); fetchKeys(); }}
      onError=${(msg) => toast(msg, 'error')}
    />`}

    <div class="panel">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 style="margin:0">Active keys${showRevoked ? ' + revoked' : ''}</h3>
        <label style="font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center">
          <input type="checkbox" checked=${showRevoked} onChange=${(e) => setShowRevoked(e.target.checked)} />
          Show revoked
        </label>
      </div>

      ${error && html`<div class="secondary-empty" style="color:var(--danger)">${error}</div>`}
      ${!error && keys === null && html`<div class="secondary-empty">Loading…</div>`}
      ${!error && keys && keys.length === 0 && html`
        <div class="secondary-empty">No keys yet. Click <em>Issue new key</em> to create your first one.</div>
      `}

      ${keys && keys.length > 0 && html`
        <div class="secondary-list">
          ${keys.map((k) => html`<${KeyRow}
            key=${k.id}
            record=${k}
            onRevoke=${() => handleRevoke(k.id, k.label)}
            onRotate=${() => handleRotate(k.id, k.label)}
          />`)}
        </div>
      `}
    </div>
  `;
}

function KeyRow({ record, onRevoke, onRotate }) {
  const revoked = !record.is_active;
  const scopes = Array.isArray(record.scopes) ? record.scopes : [];
  const expires = record.expires_at;
  const expiresSoon = useMemo(() => {
    if (!expires) return false;
    try {
      const ms = new Date(expires).getTime() - Date.now();
      return ms > 0 && ms < 7 * 24 * 60 * 60 * 1000;
    } catch (_e) { return false; }
  }, [expires]);

  return html`
    <div class="secondary-row" style=${revoked ? 'opacity:0.55' : ''}>
      <div class="secondary-row-copy" style="flex:1">
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
          <strong>${record.label || '(no label)'}</strong>
          ${record.agent_id && html`<span class="status-badge">${record.agent_id}</span>`}
          ${record.agent_version && html`<span class="muted" style="font-size:11px">v${record.agent_version}</span>`}
          ${revoked && html`<span class="status-badge" style="color:var(--danger)">revoked</span>`}
          ${expiresSoon && html`<span class="status-badge" style="color:var(--amber)">expires soon</span>`}
        </div>
        <div class="muted" style="font-size:12px;margin-top:4px">
          <code>${record.key_prefix}</code>
          ${' · created '}${formatDate(record.created_at)}
          ${record.last_used_at && html` · last used ${formatDate(record.last_used_at)}`}
          ${expires && html` · expires ${formatDate(expires)}`}
          ${record.revoked_at && html` · revoked ${formatDate(record.revoked_at)}`}
        </div>
        ${scopes.length > 0 && html`
          <div style="margin-top:6px;display:flex;gap:4px;flex-wrap:wrap">
            ${scopes.map((s) => html`<span key=${s} class="status-badge">${s}</span>`)}
          </div>
        `}
      </div>
      ${!revoked && html`
        <div style="display:flex;gap:8px">
          <button onClick=${onRotate}>Rotate</button>
          <button onClick=${onRevoke} style="color:var(--danger)">Revoke</button>
        </div>
      `}
    </div>
  `;
}

function SecretReveal({ record, onDismiss }) {
  const [copied, setCopied] = useState(false);
  const raw = record?.raw_key || '';
  const copy = useCallback(() => {
    if (!navigator?.clipboard) return;
    navigator.clipboard.writeText(raw).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    });
  }, [raw]);
  return html`
    <div class="secondary-banner warning" style="margin-bottom:16px">
      <div class="secondary-banner-copy" style="flex:1">
        <h3>Capture this secret now — it will not be shown again</h3>
        <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
          <code style="flex:1;padding:8px;background:rgba(0,0,0,0.05);border-radius:6px;font-size:13px;word-break:break-all">${raw}</code>
          <button class="primary" onClick=${copy}>${copied ? 'Copied!' : 'Copy'}</button>
        </div>
        <p class="muted" style="margin-top:8px">
          Solden stores only a SHA-256 hash. Lose this and the only path back is to rotate the key.
        </p>
      </div>
      <div class="secondary-banner-actions">
        <button onClick=${onDismiss}>Dismiss</button>
      </div>
    </div>
  `;
}

function IssueKeyModal({ scopeCatalog, api, onClose, onIssued, onError }) {
  const [label, setLabel] = useState('');
  const [agentId, setAgentId] = useState('');
  const [agentVersion, setAgentVersion] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const [selectedScopes, setSelectedScopes] = useState(new Set());
  const [submitting, setSubmitting] = useState(false);

  const toggleScope = (scope) => {
    const next = new Set(selectedScopes);
    if (next.has(scope)) next.delete(scope); else next.add(scope);
    setSelectedScopes(next);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const body = {
        label,
        scopes: Array.from(selectedScopes),
      };
      if (agentId.trim()) body.agent_id = agentId.trim();
      if (agentVersion.trim()) body.agent_version = agentVersion.trim();
      if (expiresAt.trim()) {
        // The native datetime-local input gives us YYYY-MM-DDTHH:mm — append
        // :00Z so the backend can parse it as ISO-8601.
        body.expires_at = expiresAt.includes('Z')
          ? expiresAt
          : `${expiresAt}:00Z`;
      }
      const record = await api('/api/workspace/api-keys', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      onIssued(record);
    } catch (err) {
      onError(err?.message || 'Failed to issue API key');
    } finally {
      setSubmitting(false);
    }
  };

  const newScopes = scopeCatalog.filter((s) => NEW_VOCAB_SCOPES.has(s));
  const legacyScopes = scopeCatalog.filter((s) => !NEW_VOCAB_SCOPES.has(s));

  return html`
    <div class="modal-overlay" onClick=${onClose}>
      <div class="modal-card" onClick=${(e) => e.stopPropagation()} style="max-width:560px">
        <h3 style="margin-top:0">Issue API key</h3>
        <form onSubmit=${handleSubmit}>
          <label class="form-label">Label</label>
          <input
            type="text"
            value=${label}
            onInput=${(e) => setLabel(e.target.value)}
            placeholder="e.g. CS bot — prod"
            class="form-input"
            required
          />

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px">
            <div>
              <label class="form-label">Agent ID (optional)</label>
              <input
                type="text"
                value=${agentId}
                onInput=${(e) => setAgentId(e.target.value)}
                placeholder="agent:cs-bot"
                class="form-input"
              />
            </div>
            <div>
              <label class="form-label">Agent version</label>
              <input
                type="text"
                value=${agentVersion}
                onInput=${(e) => setAgentVersion(e.target.value)}
                placeholder="2.4.1"
                class="form-input"
              />
            </div>
          </div>

          <label class="form-label" style="margin-top:12px">Expires (optional)</label>
          <input
            type="datetime-local"
            value=${expiresAt}
            onInput=${(e) => setExpiresAt(e.target.value)}
            class="form-input"
          />

          <label class="form-label" style="margin-top:16px">Scopes</label>
          <p class="muted" style="font-size:12px;margin-top:0">
            A key with no scopes is rejected by every protected endpoint. Pick at least one.
          </p>
          ${newScopes.length > 0 && html`
            <div style="display:flex;flex-direction:column;gap:6px">
              ${newScopes.map((s) => html`
                <label key=${s} style="display:flex;gap:8px;align-items:flex-start;font-size:13px">
                  <input
                    type="checkbox"
                    checked=${selectedScopes.has(s)}
                    onChange=${() => toggleScope(s)}
                  />
                  <span>
                    <code>${s}</code>
                    <span class="muted" style="display:block;font-size:11px">${SCOPE_DESCRIPTIONS[s] || ''}</span>
                  </span>
                </label>
              `)}
            </div>
          `}
          ${legacyScopes.length > 0 && html`
            <details style="margin-top:12px">
              <summary style="cursor:pointer;font-size:12px;color:var(--muted)">
                Show legacy scopes (verb:noun vocab)
              </summary>
              <div style="display:flex;flex-direction:column;gap:6px;margin-top:8px">
                ${legacyScopes.map((s) => html`
                  <label key=${s} style="display:flex;gap:8px;align-items:flex-start;font-size:13px">
                    <input
                      type="checkbox"
                      checked=${selectedScopes.has(s)}
                      onChange=${() => toggleScope(s)}
                    />
                    <span>
                      <code>${s}</code>
                      <span class="muted" style="display:block;font-size:11px">${SCOPE_DESCRIPTIONS[s] || ''}</span>
                    </span>
                  </label>
                `)}
              </div>
            </details>
          `}

          <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:20px">
            <button type="button" onClick=${onClose}>Cancel</button>
            <button
              type="submit"
              class="primary"
              disabled=${submitting || selectedScopes.size === 0 || !label.trim()}
            >
              ${submitting ? 'Issuing…' : 'Issue key'}
            </button>
          </div>
        </form>
      </div>
    </div>
  `;
}

function formatDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString();
  } catch (_e) {
    return iso;
  }
}
