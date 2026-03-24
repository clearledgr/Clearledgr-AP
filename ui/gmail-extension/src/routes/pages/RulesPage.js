import { h } from 'preact';
import htm from 'htm';
import { hasCapability, useAction } from '../route-helpers.js';

const html = htm.bind(h);

function parseThreshold(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

export default function RulesPage({ bootstrap, api, toast, orgId, onRefresh }) {
  const policy = bootstrap?.policyPayload || {};
  const configJson = (policy.policy || {}).config_json || {};
  const confidenceThreshold = Number(
    configJson.auto_approve_threshold
    ?? configJson.confidence_threshold
    ?? 0.95
  );
  const maxAutoAmount = Number(
    configJson.max_auto_approve_amount
    ?? configJson.auto_approve_max_amount
    ?? 0
  );
  const requirePO = configJson.require_po !== false;
  const canManageRules = hasCapability(bootstrap, 'manage_rules');

  const [savePolicy, saving] = useAction(async () => {
    if (!canManageRules) return;
    const nextConfidence = parseThreshold(document.getElementById('cl-policy-confidence')?.value, confidenceThreshold);
    const nextMaxAmount = parseThreshold(document.getElementById('cl-policy-max-amount')?.value, maxAutoAmount);
    const nextRequirePO = Boolean(document.getElementById('cl-policy-require-po')?.checked);

    const nextConfig = {
      ...configJson,
      auto_approve_threshold: nextConfidence,
      confidence_threshold: nextConfidence,
      max_auto_approve_amount: nextMaxAmount,
      auto_approve_max_amount: nextMaxAmount,
      require_po: nextRequirePO,
    };

    await api('/api/workspace/policies/ap', {
      method: 'PUT',
      body: JSON.stringify({
        organization_id: orgId,
        config: nextConfig,
        enabled: true,
      }),
    });
    toast('Approval rules updated.');
    onRefresh();
  });

  return html`
    <div class=${`secondary-banner ${canManageRules ? '' : 'warning'}`}>
      <div class="secondary-banner-copy">
        <h3>${canManageRules ? 'Control when invoices move automatically' : 'Approval behavior is visible here'}</h3>
        <p class="muted">${canManageRules ? 'Set the confidence, amount, and PO rules that decide when work keeps moving on its own.' : 'You can review the current approval rules here, but only admins can change them.'}</p>
      </div>
      <div class="secondary-banner-actions">
        <button class="btn-primary" onClick=${savePolicy} disabled=${saving || !canManageRules}>${saving ? 'Saving…' : 'Save rules'}</button>
      </div>
    </div>

    <div class="secondary-shell">
      <div class="secondary-main">
        <div class="panel">
          <h3 style="margin-top:0">Approval rules</h3>
          <p class="muted" style="margin:0 0 14px">These settings decide when an invoice keeps moving, waits for approval, or pauses for a PO check.</p>
          <div style="display:flex;flex-direction:column;gap:16px">
            <div>
              <label>Auto-approval confidence threshold</label>
              <input id="cl-policy-confidence" type="number" min="0" max="1" step="0.01" value=${String(confidenceThreshold)} disabled=${!canManageRules} />
              <div class="muted" style="margin-top:6px">Invoices below this confidence wait for a person to review them before approval or posting.</div>
            </div>
            <div>
              <label>Maximum auto-approve amount</label>
              <input id="cl-policy-max-amount" type="number" min="0" step="1" value=${String(maxAutoAmount)} disabled=${!canManageRules} />
              <div class="muted" style="margin-top:6px">Invoices above this amount always wait for human approval.</div>
            </div>
            <label style="display:flex;align-items:center;gap:10px;font-size:13px;font-weight:500">
              <input id="cl-policy-require-po" type="checkbox" checked=${requirePO} disabled=${!canManageRules} />
              Require PO match before approval routing
            </label>
          </div>
        </div>
      </div>

      <div class="secondary-side">
        <div class="panel">
          <h3 style="margin-top:0">Current behavior</h3>
          <div class="secondary-stat-grid" style="margin-top:12px">
            <div class="secondary-stat-card">
              <strong>Policy</strong>
              <span>${policy.policy_name || 'Default AP policy'}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Confidence</strong>
              <span>${confidenceThreshold}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Auto-approve cap</strong>
              <span>${maxAutoAmount > 0 ? `$${maxAutoAmount.toLocaleString()}` : 'No limit set'}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>PO required</strong>
              <span>${requirePO ? 'Yes' : 'No'}</span>
            </div>
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Editing access</h3>
          <div class="secondary-note">
            ${canManageRules
              ? 'You can change the rule thresholds from this page.'
              : 'This page stays readable for operators, but only admins can change the policy.'}
          </div>
        </div>
      </div>
    </div>
  `;
}
