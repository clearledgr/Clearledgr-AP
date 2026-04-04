import { h } from 'preact';
import htm from 'htm';
import { hasCapability, useAction } from '../route-helpers.js';

const html = htm.bind(h);

function parseThreshold(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function parseWholeNumber(value, fallback, minimum = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(minimum, Math.round(numeric));
}

export function getApprovalAutomationConfig(configJson = {}, approvalAutomation = null) {
  const nested = configJson?.approval_automation && typeof configJson.approval_automation === 'object'
    ? configJson.approval_automation
    : {};
  const policy = approvalAutomation && typeof approvalAutomation === 'object'
    ? approvalAutomation
    : {};
  const reminderHours = parseWholeNumber(
    policy.reminder_hours ?? nested.reminder_hours,
    4,
  );
  const escalationHours = Math.max(
    reminderHours,
    parseWholeNumber(
      policy.escalation_hours ?? nested.escalation_hours,
      24,
    ),
  );
  const escalationChannel = String(
    policy.escalation_channel
    ?? nested.escalation_channel
    ?? '',
  ).trim();
  return {
    reminderHours,
    escalationHours,
    escalationChannel,
  };
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
  const approvalAutomation = getApprovalAutomationConfig(configJson, policy.approval_automation);
  const canManageRules = hasCapability(bootstrap, 'manage_rules');

  const [savePolicy, saving] = useAction(async () => {
    if (!canManageRules) return;
    const nextConfidence = parseThreshold(document.getElementById('cl-policy-confidence')?.value, confidenceThreshold);
    const nextMaxAmount = parseThreshold(document.getElementById('cl-policy-max-amount')?.value, maxAutoAmount);
    const nextRequirePO = Boolean(document.getElementById('cl-policy-require-po')?.checked);
    const nextReminderHours = parseWholeNumber(
      document.getElementById('cl-policy-approval-reminder-hours')?.value,
      approvalAutomation.reminderHours,
    );
    const nextEscalationHours = Math.max(
      nextReminderHours,
      parseWholeNumber(
        document.getElementById('cl-policy-approval-escalation-hours')?.value,
        approvalAutomation.escalationHours,
      ),
    );
    const nextEscalationChannel = String(
      document.getElementById('cl-policy-approval-escalation-channel')?.value
      ?? approvalAutomation.escalationChannel
      ?? '',
    ).trim();

    const nextConfig = {
      ...configJson,
      auto_approve_threshold: nextConfidence,
      confidence_threshold: nextConfidence,
      max_auto_approve_amount: nextMaxAmount,
      auto_approve_max_amount: nextMaxAmount,
      require_po: nextRequirePO,
      approval_automation: {
        ...(configJson.approval_automation && typeof configJson.approval_automation === 'object'
          ? configJson.approval_automation
          : {}),
        reminder_hours: nextReminderHours,
        escalation_hours: nextEscalationHours,
        escalation_channel: nextEscalationChannel,
      },
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
        <p class="muted">${canManageRules ? 'Set approval guardrails and decide when Clearledgr nudges or escalates pending approvals automatically.' : 'You can review the current approval rules here, but only admins can change them.'}</p>
      </div>
      <div class="secondary-banner-actions">
        <button class="btn-primary" onClick=${savePolicy} disabled=${saving || !canManageRules}>${saving ? 'Saving…' : 'Save rules'}</button>
      </div>
    </div>

    <div class="secondary-shell">
      <div class="secondary-main">
        <div class="panel">
          <h3 style="margin-top:0">Approval rules</h3>
          <p class="muted" style="margin:0 0 14px">These settings decide when an invoice keeps moving, waits for approval, pauses for a PO check, and when Clearledgr starts chasing overdue approvals.</p>
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
            <div>
              <label>Approval reminder SLA (hours)</label>
              <input id="cl-policy-approval-reminder-hours" type="number" min="1" step="1" value=${String(approvalAutomation.reminderHours)} disabled=${!canManageRules} />
              <div class="muted" style="margin-top:6px">Once an approval waits this long, Clearledgr marks it as due for follow-up and nudges the pending approver.</div>
            </div>
            <div>
              <label>Approval escalation after (hours)</label>
              <input id="cl-policy-approval-escalation-hours" type="number" min="1" step="1" value=${String(approvalAutomation.escalationHours)} disabled=${!canManageRules} />
              <div class="muted" style="margin-top:6px">Once this threshold is reached, Clearledgr escalates the approval instead of only nudging.</div>
            </div>
            <div>
              <label>Escalation channel override</label>
              <input id="cl-policy-approval-escalation-channel" type="text" value=${approvalAutomation.escalationChannel} placeholder="#finance-approvals" disabled=${!canManageRules} />
              <div class="muted" style="margin-top:6px">Optional. Leave blank to use the workspace default approval channel.</div>
            </div>
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
            <div class="secondary-stat-card">
              <strong>Reminder SLA</strong>
              <span>${approvalAutomation.reminderHours}h</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Escalation</strong>
              <span>${approvalAutomation.escalationHours}h</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Escalation channel</strong>
              <span>${approvalAutomation.escalationChannel || 'Workspace default'}</span>
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

        <${DelegationPanel} api=${api} canManage=${canManageRules} />
      </div>
    </div>
  `;
}

function DelegationPanel({ api, canManage }) {
  const [rules, setRules] = useState([]);
  const [delegator, setDelegator] = useState('');
  const [delegate, setDelegate] = useState('');
  const [reason, setReason] = useState('');
  const [adding, setAdding] = useState(false);
  useEffect(() => {
    api.fetch('/api/workspace/delegation-rules').then((d) => setRules(d?.rules || [])).catch(() => {});
  }, []);
  const addRule = async () => {
    if (!delegator.trim() || !delegate.trim()) return;
    setAdding(true);
    try {
      const result = await api.fetch('/api/workspace/delegation-rules', {
        method: 'POST',
        body: JSON.stringify({ delegator_email: delegator.trim(), delegate_email: delegate.trim(), reason: reason.trim() }),
      });
      if (result?.id) setRules((prev) => [...prev, result]);
      setDelegator('');
      setDelegate('');
      setReason('');
    } catch (e) { console.warn('Add delegation failed:', e); }
    setAdding(false);
  };
  const deactivate = async (id) => {
    try {
      await api.fetch(`/api/workspace/delegation-rules/${id}/deactivate`, { method: 'POST' });
      setRules((prev) => prev.filter((r) => r.id !== id));
    } catch (e) { console.warn('Deactivate failed:', e); }
  };
  return html`
    <div class="panel">
      <h3 style="margin-top:0">Approval delegation</h3>
      <p class="muted" style="margin:0 0 8px;font-size:12px">When an approver is OOO, their pending approvals route to their delegate.</p>
      ${rules.length === 0 && html`<div class="muted" style="font-size:12px;padding:8px 0">No active delegation rules</div>`}
      ${rules.map((r) => html`
        <div key=${r.id} style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);font-size:12px">
          <div>
            <div><strong>${r.delegator_email}</strong> → ${r.delegate_email}</div>
            ${r.reason && html`<div class="muted">${r.reason}</div>`}
          </div>
          ${canManage && html`<button class="btn-secondary btn-sm" onClick=${() => deactivate(r.id)}>Remove</button>`}
        </div>
      `)}
      ${canManage && html`
        <div style="display:flex;flex-direction:column;gap:6px;margin-top:10px">
          <div style="display:flex;gap:6px">
            <input type="email" placeholder="Approver email" value=${delegator} onInput=${(e) => setDelegator(e.target.value)} style="flex:1;padding:6px 8px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px" />
            <input type="email" placeholder="Delegate email" value=${delegate} onInput=${(e) => setDelegate(e.target.value)} style="flex:1;padding:6px 8px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px" />
          </div>
          <div style="display:flex;gap:6px">
            <input type="text" placeholder="Reason (optional)" value=${reason} onInput=${(e) => setReason(e.target.value)} style="flex:1;padding:6px 8px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px" />
            <button class="btn-secondary btn-sm" onClick=${addRule} disabled=${adding || !delegator.trim() || !delegate.trim()}>${adding ? '...' : 'Add'}</button>
          </div>
        </div>
      `}
    </div>
  `;
}
