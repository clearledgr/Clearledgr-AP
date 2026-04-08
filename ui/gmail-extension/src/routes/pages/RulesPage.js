import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
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

function buildDefaultDraftRule() {
  return {
    min_amount: 0,
    max_amount: '',
    approver_channel: '',
    approval_type: 'any',
    approver_targets: [],
    gl_codes: '',
    departments: '',
    vendors: '',
  };
}

function normalizeDelimitedList(value) {
  return String(value || '')
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function formatCurrencyAmount(value) {
  const numeric = Number(value || 0);
  return `$${numeric.toLocaleString()}`;
}

function formatThreshold(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function toTitleCase(value) {
  return String(value || '').replace(/\b\w/g, (match) => match.toUpperCase());
}

function formatPolicyLabel(rawName) {
  const value = String(rawName || '').trim();
  if (!value) return 'Default AP approval policy';
  if (value === 'ap_business_v1') return 'Default AP approval policy';

  const normalized = value
    .replace(/^ap[_-]/i, 'accounts payable ')
    .replace(/[_-]+/g, ' ')
    .replace(/\bv\d+\b/gi, '')
    .replace(/\s+/g, ' ')
    .trim();

  if (!normalized) return 'Default AP approval policy';

  return toTitleCase(normalized)
    .replace(/\bAp\b/g, 'AP')
    .replace(/\bPo\b/g, 'PO')
    .replace(/\bErp\b/g, 'ERP')
    .replace(/\bAi\b/g, 'AI')
    .replace(/\bAccounts Payable Business\b/g, 'Accounts payable');
}

function formatPolicyActionLabel(action) {
  const value = String(action || '').trim();
  if (!value) return '';
  return toTitleCase(value.replace(/_/g, ' '));
}

function formatApproverList(approvers = []) {
  const values = Array.isArray(approvers) ? approvers.filter(Boolean) : [];
  if (!values.length) return '';
  return values.join(', ');
}

function normalizeApproverTarget(target, directoryIndex = {}) {
  if (!target) return null;
  const raw = typeof target === 'string' ? { email: target } : target;
  const email = String(raw.email || '').trim().toLowerCase();
  const directoryMatch = email ? directoryIndex[email] : null;
  const slackUserId = String(
    raw.slack_user_id
    || raw.slackUserId
    || (directoryMatch && directoryMatch.slack_user_id)
    || '',
  ).trim();
  const slackResolution = String(
    raw.slack_resolution
    || raw.slackResolution
    || (slackUserId ? 'resolved' : '')
    || (directoryMatch && directoryMatch.slack_resolution)
    || 'not_found',
  ).trim();
  const displayName = String(
    raw.display_name
    || raw.displayName
    || raw.name
    || (directoryMatch && directoryMatch.display_name)
    || (directoryMatch && directoryMatch.name)
    || email
    || slackUserId,
  ).trim();
  if (!email && !slackUserId) return null;
  return {
    email,
    display_name: displayName || email || slackUserId,
    slack_user_id: slackUserId,
    slack_resolution: slackResolution || (slackUserId ? 'resolved' : 'not_found'),
    approval_ready: Boolean(slackUserId),
  };
}

function buildApproverDirectoryIndex(entries = []) {
  return Object.fromEntries(
    (Array.isArray(entries) ? entries : [])
      .map((entry) => normalizeApproverTarget(entry, {}))
      .filter(Boolean)
      .map((entry) => [entry.email, entry]),
  );
}

function mergeRuleApproverTargets(rule, directoryIndex = {}) {
  const structured = Array.isArray(rule?.approver_targets) ? rule.approver_targets : [];
  if (structured.length) {
    return structured
      .map((entry) => normalizeApproverTarget(entry, directoryIndex))
      .filter(Boolean);
  }
  const rawApprovers = Array.isArray(rule?.approvers) ? rule.approvers : [];
  return rawApprovers
    .map((entry) => normalizeApproverTarget(entry, directoryIndex))
    .filter(Boolean);
}

function formatApproverStatus(status) {
  const normalized = String(status || '').trim().toLowerCase();
  if (normalized === 'resolved') return 'Slack ready';
  if (normalized === 'not_connected') return 'Slack not connected';
  if (normalized === 'not_found') return 'Not in Slack';
  if (normalized === 'lookup_failed') return 'Slack lookup failed';
  return 'Needs resolution';
}

function getApprovalAutomationConfig(configJson = {}, approvalAutomation = null) {
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

function DraftRuleForm({
  draftRule,
  approverDirectory,
  selectedApproverEmail,
  onChange,
  onSelectedApproverChange,
  onAddApprover,
  onRemoveApprover,
  onSave,
  onCancel,
  saving,
  slackConnected,
}) {
  const draftApproverTargets = Array.isArray(draftRule.approver_targets) ? draftRule.approver_targets : [];
  const selectedApprover = approverDirectory.find((entry) => entry.email === selectedApproverEmail) || null;
  return html`
    <div style="padding:16px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--bg)">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:14px">
        <div>
          <h3 style="margin:0 0 4px">Add routing rule</h3>
          <p class="muted" style="margin:0">Route invoices to approvers based on amount, vendor, GL code, or department. First matching rule wins.</p>
        </div>
        <div class="row-actions">
          <button class="btn-ghost btn-sm" onClick=${onCancel}>Cancel</button>
          <button class="btn-primary btn-sm" onClick=${onSave} disabled=${saving}>${saving ? 'Saving…' : 'Save rule'}</button>
        </div>
      </div>

      <div class="secondary-form-grid" style="gap:12px">
        <div style="display:flex;gap:8px;align-items:flex-end">
          <div style="flex:1">
            <label>Min amount</label>
            <input
              type="number"
              min="0"
              step="0.01"
              value=${String(draftRule.min_amount)}
              onInput=${(event) => onChange('min_amount', event.target.value)}
            />
          </div>
          <div style="flex:1">
            <label>Max amount</label>
            <input
              type="number"
              min="0"
              step="0.01"
              placeholder="No limit"
              value=${String(draftRule.max_amount)}
              onInput=${(event) => onChange('max_amount', event.target.value)}
            />
          </div>
        </div>

        <div style="display:flex;gap:8px;align-items:flex-end">
          <div style="flex:1">
            <label>Slack channel</label>
            <input
              type="text"
              placeholder="#finance-approvals"
              value=${draftRule.approver_channel}
              onInput=${(event) => onChange('approver_channel', event.target.value)}
            />
          </div>
          <div style="flex:1">
            <label>Approval type</label>
            <select
              value=${draftRule.approval_type}
              onChange=${(event) => onChange('approval_type', event.target.value)}
            >
              <option value="any">Any approver</option>
              <option value="all">All approvers</option>
            </select>
          </div>
        </div>

        <div>
          <label>Approvers</label>
          <div style="display:flex;gap:8px;align-items:flex-end">
            <div style="flex:1">
              <select
                value=${selectedApproverEmail}
                onChange=${(event) => onSelectedApproverChange(event.target.value)}
              >
                <option value="">Select workspace approver</option>
                ${approverDirectory.map((entry) => html`
                  <option key=${entry.email} value=${entry.email}>
                    ${entry.display_name} · ${entry.email} · ${formatApproverStatus(entry.slack_resolution)}
                  </option>
                `)}
              </select>
            </div>
            <button
              class="btn-secondary btn-sm"
              type="button"
              onClick=${onAddApprover}
              disabled=${!selectedApproverEmail || !selectedApprover?.approval_ready}
            >
              Add
            </button>
          </div>
          <div class="muted" style="margin-top:6px">
            ${slackConnected
              ? 'Approvers come from your workspace team. Only Slack-resolved people can be used for named Slack approval rules.'
              : 'Slack is not connected yet, so named approvers cannot be resolved for reminders and direct mentions.'}
          </div>
          ${draftApproverTargets.length
            ? html`<div class="secondary-list" style="margin-top:10px">
                ${draftApproverTargets.map((entry) => html`
                  <div key=${entry.email || entry.slack_user_id} class="secondary-row">
                    <div class="secondary-row-copy">
                      <strong>${entry.display_name || entry.email}</strong>
                      <p>${entry.email || 'No email available'}</p>
                    </div>
                    <div class="secondary-chip-row">
                      <span class=${`status-badge ${entry.approval_ready ? 'connected' : ''}`}>
                        ${formatApproverStatus(entry.slack_resolution)}
                      </span>
                      <button class="btn-ghost btn-sm" type="button" onClick=${() => onRemoveApprover(entry.email || entry.slack_user_id)}>
                        Remove
                      </button>
                    </div>
                  </div>
                `)}
              </div>`
            : html`<div class="secondary-empty" style="margin-top:10px">No approvers added yet.</div>`}
        </div>
        <div>
          <label>GL codes</label>
          <input
            type="text"
            placeholder="6000, 6100"
            value=${draftRule.gl_codes}
            onInput=${(event) => onChange('gl_codes', event.target.value)}
          />
        </div>
        <div>
          <label>Departments</label>
          <input
            type="text"
            placeholder="operations, marketing"
            value=${draftRule.departments}
            onInput=${(event) => onChange('departments', event.target.value)}
          />
        </div>
        <div>
          <label>Vendors</label>
          <input
            type="text"
            placeholder="Acme Corp, Widget Supply"
            value=${draftRule.vendors}
            onInput=${(event) => onChange('vendors', event.target.value)}
          />
        </div>
      </div>
    </div>
  `;
}

function RoutingRuleRow({ rule, index, approverDirectoryIndex, canManageRules, onDelete, deleting }) {
  const amountLabel = `${formatCurrencyAmount(rule.min_amount || 0)} – ${rule.max_amount ? formatCurrencyAmount(rule.max_amount) : 'No limit'}`;
  const approverTargets = mergeRuleApproverTargets(rule, approverDirectoryIndex);
  const unresolvedTargets = approverTargets.filter((entry) => !entry.approval_ready);
  return html`
    <div class="secondary-row">
      <div class="secondary-row-copy">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
          <strong>Rule ${index + 1} · ${amountLabel}</strong>
          <span class="status-badge">${rule.approver_channel || 'Workspace default'}</span>
          <span class="status-badge connected">${rule.approval_type === 'all' ? 'All must approve' : 'Any can approve'}</span>
        </div>
        <p>Approvers: ${approverTargets.length ? approverTargets.map((entry) => entry.display_name || entry.email).join(', ') : 'None set'}</p>
        ${unresolvedTargets.length
          ? html`<p>Needs Slack resolution: ${unresolvedTargets.map((entry) => entry.display_name || entry.email).join(', ')}</p>`
          : null}
        ${(rule.gl_codes || []).length
          ? html`<p>GL codes: ${(rule.gl_codes || []).join(', ')}</p>`
          : null}
        ${(rule.departments || []).length
          ? html`<p>Departments: ${(rule.departments || []).join(', ')}</p>`
          : null}
        ${(rule.vendors || []).length
          ? html`<p>Vendors: ${(rule.vendors || []).join(', ')}</p>`
          : null}
      </div>
      ${canManageRules
        ? html`<button class="btn-danger btn-sm" onClick=${() => onDelete(index)} disabled=${deleting}>Delete</button>`
        : null}
    </div>
  `;
}

function DelegationPanel({ api, canManageRules, orgId }) {
  const [rules, setRules] = useState([]);
  const [delegator, setDelegator] = useState('');
  const [delegate, setDelegate] = useState('');
  const [reason, setReason] = useState('');

  useEffect(() => {
    if (!api) return;
    api(`/api/workspace/delegation-rules?organization_id=${encodeURIComponent(orgId)}`, { silent: true })
      .then((data) => setRules(Array.isArray(data?.rules) ? data.rules : []))
      .catch(() => setRules([]));
  }, [api, orgId]);

  const [addRule, adding] = useAction(async () => {
    if (!canManageRules) return;
    const delegatorEmail = String(delegator || '').trim();
    const delegateEmail = String(delegate || '').trim();
    if (!delegatorEmail || !delegateEmail) return;
    const result = await api('/api/workspace/delegation-rules', {
      method: 'POST',
      body: JSON.stringify({
        organization_id: orgId,
        delegator_email: delegatorEmail,
        delegate_email: delegateEmail,
        reason: String(reason || '').trim() || undefined,
      }),
    });
    if (result?.id) setRules((prev) => [...prev, result]);
    setDelegator('');
    setDelegate('');
    setReason('');
  });

  const [deactivateRule, removing] = useAction(async (id) => {
    if (!canManageRules) return;
    await api(`/api/workspace/delegation-rules/${id}/deactivate?organization_id=${encodeURIComponent(orgId)}`, { method: 'POST' });
    setRules((prev) => prev.filter((entry) => entry.id !== id));
  });

  return html`
    <div>
      <div class="panel-head compact">
        <div>
          <h3 style="margin-top:0">Approval delegation</h3>
          <p class="muted" style="margin:0">When an approver is out, delegate their pending approvals without changing the policy itself.</p>
        </div>
      </div>

      ${rules.length > 0
        ? html`<div class="secondary-list" style="margin-bottom:14px">
            ${rules.map((rule) => html`
              <div key=${rule.id} class="secondary-row">
                <div class="secondary-row-copy">
                  <strong>${rule.delegator_email} → ${rule.delegate_email}</strong>
                  ${rule.reason ? html`<p>${rule.reason}</p>` : html`<p>No reason added.</p>`}
                </div>
                ${canManageRules
                  ? html`<button class="btn-secondary btn-sm" onClick=${() => deactivateRule(rule.id)} disabled=${removing}>Remove</button>`
                  : null}
              </div>
            `)}
          </div>`
        : html`<div class="secondary-empty" style="margin-bottom:14px">No active delegation rules.</div>`}

      ${canManageRules
        ? html`
            <div class="secondary-form-grid" style="gap:10px">
              <div>
                <label>Approver</label>
                <input type="email" placeholder="approver@company.com" value=${delegator} onInput=${(event) => setDelegator(event.target.value)} />
              </div>
              <div>
                <label>Delegate</label>
                <input type="email" placeholder="delegate@company.com" value=${delegate} onInput=${(event) => setDelegate(event.target.value)} />
              </div>
            </div>
            <div style="margin-top:10px">
              <label>Reason</label>
              <input type="text" placeholder="OOO for week of close" value=${reason} onInput=${(event) => setReason(event.target.value)} />
            </div>
            <div class="row-actions" style="justify-content:flex-start;margin-top:12px">
              <button class="btn-primary btn-sm" onClick=${addRule} disabled=${adding || !String(delegator || '').trim() || !String(delegate || '').trim()}>
                ${adding ? 'Saving…' : 'Add delegation'}
              </button>
            </div>
          `
        : null}
    </div>
  `;
}

export default function RulesPage({ bootstrap, api, toast, orgId, onRefresh }) {
  const policy = bootstrap?.policyPayload || {};
  const configJson = (policy.policy || {}).config_json || {};
  const approvalAutomation = getApprovalAutomationConfig(configJson, policy.approval_automation);
  const canManageRules = hasCapability(bootstrap, 'manage_rules');
  const policyLabel = formatPolicyLabel(
    policy.policy_display_name
    || policy.display_name
    || policy.policy_label
    || policy.policy_name,
  );
  const effectivePolicies = Array.isArray(policy.effective_policies) ? policy.effective_policies : [];

  const [confidenceThreshold, setConfidenceThreshold] = useState(String(
    Number(configJson.auto_approve_threshold ?? configJson.confidence_threshold ?? 0.95),
  ));
  const [maxAutoAmount, setMaxAutoAmount] = useState(String(
    Number(configJson.max_auto_approve_amount ?? configJson.auto_approve_max_amount ?? 0),
  ));
  const [requirePO, setRequirePO] = useState(configJson.require_po !== false);
  const [reminderHours, setReminderHours] = useState(String(approvalAutomation.reminderHours));
  const [escalationHours, setEscalationHours] = useState(String(approvalAutomation.escalationHours));
  const [escalationChannel, setEscalationChannel] = useState(approvalAutomation.escalationChannel);

  const [approvalRules, setApprovalRules] = useState([]);
  const [showAddRule, setShowAddRule] = useState(false);
  const [draftRule, setDraftRule] = useState(buildDefaultDraftRule());
  const [approverDirectory, setApproverDirectory] = useState([]);
  const [slackConnected, setSlackConnected] = useState(false);
  const [selectedApproverEmail, setSelectedApproverEmail] = useState('');

  const approverDirectoryIndex = useMemo(
    () => buildApproverDirectoryIndex(approverDirectory),
    [approverDirectory],
  );

  useEffect(() => {
    if (!api || !orgId) return;
    api(`/settings/${encodeURIComponent(orgId)}`, { silent: true })
      .then((response) => {
        setApprovalRules(Array.isArray(response?.approval_thresholds) ? response.approval_thresholds : []);
      })
      .catch(() => setApprovalRules([]));
  }, [api, orgId]);

  useEffect(() => {
    if (!api || !orgId || !canManageRules) {
      setApproverDirectory([]);
      setSlackConnected(false);
      return;
    }
    api(`/api/workspace/team/approvers?organization_id=${encodeURIComponent(orgId)}`, { silent: true })
      .then((response) => {
        const nextApprovers = Array.isArray(response?.approvers)
          ? response.approvers
            .map((entry) => normalizeApproverTarget(entry, {}))
            .filter(Boolean)
          : [];
        setApproverDirectory(nextApprovers);
        setSlackConnected(Boolean(response?.slack_connected));
      })
      .catch(() => {
        setApproverDirectory([]);
        setSlackConnected(false);
      });
  }, [api, canManageRules, orgId]);

  const summary = useMemo(() => {
    const threshold = parseThreshold(confidenceThreshold, 0.95);
    const amountCap = parseThreshold(maxAutoAmount, 0);
    const nextReminder = parseWholeNumber(reminderHours, approvalAutomation.reminderHours);
    const nextEscalation = Math.max(nextReminder, parseWholeNumber(escalationHours, approvalAutomation.escalationHours));
    return {
      threshold,
      amountCap,
      nextReminder,
      nextEscalation,
      ruleCount: approvalRules.length,
    };
  }, [approvalRules.length, approvalAutomation.escalationHours, approvalAutomation.reminderHours, confidenceThreshold, escalationHours, maxAutoAmount, reminderHours]);

  const [savePolicy, savingPolicy] = useAction(async () => {
    if (!canManageRules) return;
    const nextConfidence = parseThreshold(confidenceThreshold, 0.95);
    const nextMaxAmount = parseThreshold(maxAutoAmount, 0);
    const nextReminder = parseWholeNumber(reminderHours, approvalAutomation.reminderHours);
    const nextEscalation = Math.max(nextReminder, parseWholeNumber(escalationHours, approvalAutomation.escalationHours));
    const nextConfig = {
      ...configJson,
      auto_approve_threshold: nextConfidence,
      confidence_threshold: nextConfidence,
      max_auto_approve_amount: nextMaxAmount,
      auto_approve_max_amount: nextMaxAmount,
      require_po: Boolean(requirePO),
      approval_automation: {
        ...(configJson.approval_automation && typeof configJson.approval_automation === 'object'
          ? configJson.approval_automation
          : {}),
        reminder_hours: nextReminder,
        escalation_hours: nextEscalation,
        escalation_channel: String(escalationChannel || '').trim(),
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
    toast?.('Approval rules updated.', 'success');
    onRefresh?.();
  });

  const [saveApprovalRules, savingRules] = useAction(async (rules) => {
    if (!canManageRules) return;
    await api(`/settings/${encodeURIComponent(orgId)}/approval-thresholds`, {
      method: 'PUT',
      body: JSON.stringify({ approval_thresholds: rules }),
    });
    setApprovalRules(rules);
    toast?.('Routing rules updated.', 'success');
  });

  const [addApprovalRule, addingRule] = useAction(async () => {
    if (!canManageRules) return;
    const approverTargets = mergeRuleApproverTargets(draftRule, approverDirectoryIndex);
    const unresolvedTargets = approverTargets.filter((entry) => !entry.approval_ready);
    if (!approverTargets.length) {
      toast?.('Select at least one workspace approver before saving the rule.', 'error');
      return;
    }
    if (unresolvedTargets.length) {
      toast?.(
        `Resolve Slack access for ${unresolvedTargets.map((entry) => entry.display_name || entry.email).join(', ')} before saving this rule.`,
        'error',
      );
      return;
    }
    const nextRule = {
      min_amount: parseThreshold(draftRule.min_amount, 0),
      max_amount: draftRule.max_amount === '' ? null : parseThreshold(draftRule.max_amount, 0),
      approver_channel: String(draftRule.approver_channel || '').trim(),
      approval_type: draftRule.approval_type,
      approvers: approverTargets.map((entry) => entry.email).filter(Boolean),
      approver_targets: approverTargets.map((entry) => ({
        email: entry.email,
        display_name: entry.display_name,
        slack_user_id: entry.slack_user_id,
        slack_resolution: entry.slack_resolution,
      })),
      gl_codes: normalizeDelimitedList(draftRule.gl_codes),
      departments: normalizeDelimitedList(draftRule.departments),
      vendors: normalizeDelimitedList(draftRule.vendors),
    };
    const nextRules = [...approvalRules, nextRule];
    await saveApprovalRules(nextRules);
    setDraftRule(buildDefaultDraftRule());
    setShowAddRule(false);
    setSelectedApproverEmail('');
  });

  const [deleteApprovalRule, deletingRule] = useAction(async (index) => {
    if (!canManageRules) return;
    const nextRules = approvalRules.filter((_, ruleIndex) => ruleIndex !== index);
    await saveApprovalRules(nextRules);
  });

  const updateDraftRule = (field, value) => {
    setDraftRule((prev) => ({ ...prev, [field]: value }));
  };

  const addDraftApprover = () => {
    const selectedEntry = normalizeApproverTarget(
      approverDirectoryIndex[selectedApproverEmail],
      approverDirectoryIndex,
    );
    if (!selectedEntry) {
      return;
    }
    if (!selectedEntry.approval_ready) {
      toast?.(
        `${selectedEntry.display_name || selectedEntry.email} is not mapped to Slack yet.`,
        'error',
      );
      return;
    }
    setDraftRule((prev) => {
      const currentTargets = mergeRuleApproverTargets(prev, approverDirectoryIndex);
      const alreadyIncluded = currentTargets.some(
        (entry) => entry.email === selectedEntry.email || entry.slack_user_id === selectedEntry.slack_user_id,
      );
      if (alreadyIncluded) return prev;
      return {
        ...prev,
        approver_targets: [...currentTargets, selectedEntry],
      };
    });
    setSelectedApproverEmail('');
  };

  const removeDraftApprover = (targetKey) => {
    setDraftRule((prev) => ({
      ...prev,
      approver_targets: mergeRuleApproverTargets(prev, approverDirectoryIndex).filter(
        (entry) => entry.email !== targetKey && entry.slack_user_id !== targetKey,
      ),
    }));
  };

  return html`
    <div class=${`secondary-banner rules-hero ${canManageRules ? '' : 'warning'}`}>
      <div class="secondary-banner-copy">
        <h3>${canManageRules ? 'Control invoice approvals end to end' : 'Approval rules are visible here'}</h3>
        <p class="muted">
          ${canManageRules
            ? 'Manage approval routing, auto-approval guardrails, reminder timing, escalation behavior, and delegation from one place.'
            : 'You can review the current approval policy here, but only admins can change how approvals route and escalate.'}
        </p>
        <div class="rules-hero-summary">
          <div class="rules-hero-stat">
            <strong>${summary.ruleCount ? `${summary.ruleCount}` : '0'}</strong>
            <span>Routing rules</span>
          </div>
          <div class="rules-hero-stat">
            <strong>${formatThreshold(summary.threshold)}</strong>
            <span>Confidence floor</span>
          </div>
          <div class="rules-hero-stat">
            <strong>${summary.amountCap > 0 ? formatCurrencyAmount(summary.amountCap) : 'No cap'}</strong>
            <span>Auto-approve cap</span>
          </div>
          <div class="rules-hero-stat">
            <strong>${summary.nextReminder}h</strong>
            <span>Reminder SLA</span>
          </div>
          <div class="rules-hero-stat">
            <strong>${summary.nextEscalation}h</strong>
            <span>Escalation</span>
          </div>
          <div class="rules-hero-stat">
            <strong>${requirePO ? 'Required' : 'Optional'}</strong>
            <span>PO match</span>
          </div>
        </div>
      </div>
      <div class="secondary-banner-actions">
        <button class="btn-primary" onClick=${savePolicy} disabled=${savingPolicy || !canManageRules}>
          ${savingPolicy ? 'Saving…' : 'Save policy'}
        </button>
      </div>
    </div>

    <div class="rules-workspace-grid">
      <div class="rules-main-stack">
        <div class="panel">
          <div class="panel-head compact">
            <div>
              <div class="home-section-label">Routing</div>
              <h3 style="margin-top:0">Approval routing${!canManageRules ? html`<span class="status-badge" style="font-size:10px;margin-left:8px">Read-only</span>` : null}</h3>
              <p class="muted" style="margin:0">Define who approves invoices by amount, GL code, department, or vendor. The first matching rule wins.</p>
            </div>
            ${canManageRules
              ? html`<button class="btn-primary btn-sm" onClick=${() => setShowAddRule((current) => !current)} disabled=${savingRules || addingRule}>
                  ${showAddRule ? 'Cancel' : 'Add rule'}
                </button>`
              : null}
          </div>

          <div class="rules-inline-summary">
            <span class="secondary-chip">${policyLabel}</span>
            <span class="secondary-chip">${summary.ruleCount ? `${summary.ruleCount} custom rule${summary.ruleCount === 1 ? '' : 's'}` : 'Workspace default routing'}</span>
            <span class="secondary-chip">${String(escalationChannel || '').trim() || 'Workspace default channel'}</span>
          </div>

          ${showAddRule && canManageRules
            ? html`<${DraftRuleForm}
                draftRule=${draftRule}
                approverDirectory=${approverDirectory}
                selectedApproverEmail=${selectedApproverEmail}
                onChange=${updateDraftRule}
                onSelectedApproverChange=${setSelectedApproverEmail}
                onAddApprover=${addDraftApprover}
                onRemoveApprover=${removeDraftApprover}
                onSave=${addApprovalRule}
                onCancel=${() => {
                  setDraftRule(buildDefaultDraftRule());
                  setShowAddRule(false);
                  setSelectedApproverEmail('');
                }}
                saving=${addingRule}
                slackConnected=${slackConnected}
              />`
            : null}

          <div style=${showAddRule ? 'margin-top:16px' : ''}>
            ${approvalRules.length > 0
              ? html`<div class="secondary-list">
                  ${approvalRules.map((rule, index) => html`
                    <${RoutingRuleRow}
                      key=${`${rule.approver_channel || 'channel'}:${index}`}
                      rule=${rule}
                      index=${index}
                      approverDirectoryIndex=${approverDirectoryIndex}
                      canManageRules=${canManageRules}
                      onDelete=${deleteApprovalRule}
                      deleting=${deletingRule || savingRules}
                    />
                  `)}
                </div>`
              : html`<div class="secondary-empty">No routing rules yet. Add one so invoices route to the right approver set instead of waiting on defaults.</div>`}
          </div>

          <div class="secondary-note" style="margin-top:14px">
            Clearledgr evaluates routing rules in order. If no rule matches, the workspace default approval channel and policy thresholds still apply.
          </div>
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <div class="home-section-label">Automation</div>
              <h3 style="margin-top:0">Automation and guardrails</h3>
              <p class="muted" style="margin:0">Decide when invoices can move automatically and when Clearledgr starts nudging or escalating approvals.</p>
            </div>
          </div>

          <div class="secondary-form-grid">
            <div>
              <label>Auto-approval confidence threshold</label>
              <input
                id="cl-policy-confidence"
                type="number"
                min="0"
                max="1"
                step="0.01"
                value=${confidenceThreshold}
                disabled=${!canManageRules}
                onInput=${(event) => setConfidenceThreshold(event.target.value)}
              />
              <div class="muted" style="margin-top:6px">Invoices below this confidence always wait for review.</div>
            </div>
            <div>
              <label>Maximum auto-approve amount</label>
              <input
                id="cl-policy-max-amount"
                type="number"
                min="0"
                step="1"
                value=${maxAutoAmount}
                disabled=${!canManageRules}
                onInput=${(event) => setMaxAutoAmount(event.target.value)}
              />
              <div class="muted" style="margin-top:6px">Invoices above this amount always wait for a person.</div>
            </div>
            <div>
              <label>Approval reminder SLA (hours)</label>
              <input
                id="cl-policy-approval-reminder-hours"
                type="number"
                min="1"
                step="1"
                value=${reminderHours}
                disabled=${!canManageRules}
                onInput=${(event) => setReminderHours(event.target.value)}
              />
              <div class="muted" style="margin-top:6px">Clearledgr nudges the approver after this many hours.</div>
            </div>
            <div>
              <label>Approval escalation after (hours)</label>
              <input
                id="cl-policy-approval-escalation-hours"
                type="number"
                min="1"
                step="1"
                value=${escalationHours}
                disabled=${!canManageRules}
                onInput=${(event) => setEscalationHours(event.target.value)}
              />
              <div class="muted" style="margin-top:6px">After this, Clearledgr escalates instead of only nudging.</div>
            </div>
          </div>

          <div style="margin-top:14px">
            <label>Escalation channel override</label>
            <input
              id="cl-policy-approval-escalation-channel"
              type="text"
              placeholder="#finance-approvals"
              value=${escalationChannel}
              disabled=${!canManageRules}
              onInput=${(event) => setEscalationChannel(event.target.value)}
            />
            <div class="muted" style="margin-top:6px">Leave blank to keep using the workspace default approval destination.</div>
          </div>

          <label style="display:flex;align-items:center;gap:10px;font-size:13px;font-weight:500;margin-top:16px">
            <input
              id="cl-policy-require-po"
              type="checkbox"
              checked=${requirePO}
              disabled=${!canManageRules}
              onChange=${(event) => setRequirePO(Boolean(event.target.checked))}
            />
            Require PO match before approval routing
          </label>

          <div class="secondary-note rules-guardrail-note">
            These settings decide when invoices can move automatically and when Clearledgr pauses, nudges, or escalates after the approval path begins.
          </div>
          <div class="rules-inline-summary" style="margin-top:12px">
            <span class="secondary-chip">${requirePO ? 'PO match required' : 'PO match optional'}</span>
            <span class="secondary-chip">${String(escalationChannel || '').trim() || 'Escalation uses workspace default'}</span>
          </div>
        </div>
      </div>

      <div class="rules-side-stack">
        <div class="panel">
          <div class="panel-head compact">
            <div>
              <div class="home-section-label">Default policy</div>
              <h3 style="margin-top:0">What this policy includes</h3>
              <p class="muted" style="margin:0">The built-in approval checks and approver requirements behind the current default policy.</p>
            </div>
          </div>
          ${effectivePolicies.length
            ? html`<div class="secondary-list rules-effective-list" style="margin-top:12px">
                ${effectivePolicies.slice(0, 6).map((entry, index) => html`
                  <div key=${entry.policy_id || `effective:${index}`} class="secondary-row">
                    <div class="secondary-row-copy">
                      <strong>${entry.name || `Rule ${index + 1}`}</strong>
                      <p>${entry.description || 'Approval rule is active for this workspace.'}</p>
                      ${formatApproverList(entry.required_approvers)
                        ? html`<p>Approvers: ${formatApproverList(entry.required_approvers)}</p>`
                        : null}
                    </div>
                    <div class="secondary-chip-row" style="justify-content:flex-end">
                      ${entry.action
                        ? html`<span class="secondary-chip">${formatPolicyActionLabel(entry.action)}</span>`
                        : null}
                    </div>
                  </div>
                `)}
              </div>`
            : html`<div class="secondary-note" style="margin-top:12px">No effective rules are configured yet. Add routing rules or policy thresholds to define the approval path.</div>`}
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <div class="home-section-label">Coverage</div>
              <h3 style="margin-top:0">Delegation and cover</h3>
              <p class="muted" style="margin:0">Keep approvals moving when an approver is out instead of editing the policy for temporary absence.</p>
            </div>
          </div>
          <${DelegationPanel} api=${api} canManageRules=${canManageRules} orgId=${orgId} />
        </div>
      </div>
    </div>
  `;
}
