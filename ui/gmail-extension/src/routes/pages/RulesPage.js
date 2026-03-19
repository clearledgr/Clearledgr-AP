import { h } from 'preact';
import htm from 'htm';
import { useAction } from '../route-helpers.js';

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

  const [savePolicy, saving] = useAction(async () => {
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

    await api('/api/admin/policies/ap', {
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
    <div class="panel">
      <h3>How invoices are handled</h3>
      <p class="muted" style="margin-top:0">Keep the core AP controls simple here. Detailed policy authoring stays outside the daily Gmail workflow.</p>
      <div style="display:flex;flex-direction:column;gap:16px;margin-top:8px">
        <div>
          <label>Auto-approval confidence threshold</label>
          <input id="cl-policy-confidence" type="number" min="0" max="1" step="0.01" value=${String(confidenceThreshold)} />
        </div>
        <div>
          <label>Maximum auto-approve amount</label>
          <input id="cl-policy-max-amount" type="number" min="0" step="1" value=${String(maxAutoAmount)} />
        </div>
        <label style="display:flex;align-items:center;gap:10px;font-size:13px;font-weight:500">
          <input id="cl-policy-require-po" type="checkbox" checked=${requirePO} />
          Require PO match before approval routing
        </label>
      </div>
      <div class="row" style="margin-top:20px">
        <button onClick=${savePolicy} disabled=${saving}>${saving ? 'Saving…' : 'Save rules'}</button>
      </div>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">Current policy</h3>
      <div class="readiness-list" style="margin-top:12px">
        <div class="readiness-item"><strong>Policy name:</strong> ${policy.policy_name || 'Default AP policy'}</div>
        <div class="readiness-item"><strong>Confidence threshold:</strong> ${confidenceThreshold}</div>
        <div class="readiness-item"><strong>Max auto-approve amount:</strong> ${maxAutoAmount > 0 ? `$${maxAutoAmount.toLocaleString()}` : 'No limit set'}</div>
        <div class="readiness-item"><strong>PO required:</strong> ${requirePO ? 'Yes' : 'No'}</div>
      </div>
    </div>
  `;
}
