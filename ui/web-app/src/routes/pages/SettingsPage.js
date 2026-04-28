import { h } from 'preact';
import { useRef, useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { hasCapability, useAction } from '../route-helpers.js';

const html = htm.bind(h);

function formatDisplayDate(value) {
  if (!value) return 'Not set';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Not set';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

// Per-tenant GL account categories the AP pipeline reads from
// ``settings_json["gl_account_map"]``. ``expenses`` is the only one
// strictly required for AP bill posting; the rest configure optional
// downstream workflows (payment execution, FX reconciliation).
const AP_GL_CATEGORIES = [
  {
    key: 'expenses',
    label: 'Expenses (AP debit)',
    required: true,
    placeholder: 'e.g., 6100',
    help: 'Default account debited when posting a vendor bill. Required.',
    accountType: 'expense',
  },
  {
    key: 'accounts_payable',
    label: 'Accounts Payable',
    required: false,
    placeholder: 'e.g., 2000',
    help: 'Liability account credited on bill post. Most ERPs infer this automatically for Vendor Bills.',
    accountType: 'liability',
  },
  {
    key: 'cash',
    label: 'Cash',
    required: false,
    placeholder: 'e.g., 1000',
    help: 'Used for payment execution and bank reconciliation.',
    accountType: 'asset',
  },
  {
    key: 'payment_fees',
    label: 'Payment fees',
    required: false,
    placeholder: 'e.g., 6800',
    help: 'Bank service charges and payment processor fees.',
    accountType: 'expense',
  },
  {
    key: 'fx_gain_loss',
    label: 'FX gain/loss',
    required: false,
    placeholder: 'e.g., 7000',
    help: 'Foreign exchange adjustments when invoice currency differs from functional currency.',
    accountType: 'expense',
  },
];


function InviteRow({ invite, onRevoke, canManage }) {
  return html`<div class="secondary-row">
    <div class="secondary-row-copy">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
        <strong style="font-size:14px">${invite.email}</strong>
        <span class="status-badge ${invite.status === 'pending' ? '' : 'connected'}">${invite.status || 'pending'}</span>
      </div>
      <div class="muted" style="font-size:12px">Role: ${
        { ap_clerk: 'AP Clerk', ap_manager: 'AP Manager', financial_controller: 'Financial Controller', cfo: 'CFO', read_only: 'Read Only', member: 'AP Clerk', admin: 'Financial Controller', viewer: 'Read Only', operator: 'AP Manager' }[invite.role] || invite.role || 'AP Clerk'
      }</div>
    </div>
    ${invite.status === 'pending'
      ? html`<button class="btn-danger btn-sm" onClick=${() => onRevoke(invite.id)} disabled=${!canManage}>Revoke</button>`
      : null}
  </div>`;
}

export default function SettingsPage({ bootstrap, api, toast, orgId, onRefresh, routeId, navigate }) {
  const invites = bootstrap?.teamInvites || [];
  const org = bootstrap?.organization || {};
  const sub = bootstrap?.subscription || {};
  const usage = sub.usage || {};
  const usageKeys = Object.keys(usage);
  const planName = (sub.plan || 'free').charAt(0).toUpperCase() + (sub.plan || 'free').slice(1);

  const canManageTeam = hasCapability(bootstrap, 'manage_team');
  const canManageCompany = hasCapability(bootstrap, 'manage_company');
  const canManagePlan = hasCapability(bootstrap, 'manage_plan');
  const canManageAny = canManageTeam || canManageCompany || canManagePlan;

  const erpRef = useRef(null);
  const glMappingRef = useRef(null);
  const policyRef = useRef(null);
  const approvalRef = useRef(null);
  const vendorPolicyRef = useRef(null);
  const autonomyRef = useRef(null);
  const teamRef = useRef(null);
  const billingRef = useRef(null);

  // ERP + integration state from bootstrap
  const integrations = bootstrap?.integrations || [];
  const gmail = integrations.find((i) => i.type === 'gmail') || {};
  const slack = integrations.find((i) => i.type === 'slack') || {};
  const teams = integrations.find((i) => i.type === 'teams') || {};
  const erp = integrations.find((i) => i.type === 'erp') || {};
  const erpType = (erp.erp_type || '').charAt(0).toUpperCase() + (erp.erp_type || '').slice(1);

  const [activeSection, setActiveSection] = useState('erp');
  const scrollToSection = (ref, key) => {
    if (key) setActiveSection(key);
    try {
      ref?.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    } catch {
      ref?.current?.scrollIntoView?.();
    }
  };

  const goToConnections = () => {
    if (typeof navigate === 'function') navigate('connections');
  };

  const [createInvite, creatingInvite] = useAction(async () => {
    if (!canManageTeam) return;
    const email = document.getElementById('cl-invite-email')?.value?.trim();
    const role = document.getElementById('cl-invite-role')?.value;
    if (!email) {
      toast?.('Enter an email before sending the invite.', 'error');
      return;
    }
    await api('/api/workspace/team/invites', {
      method: 'POST',
      body: JSON.stringify({ organization_id: orgId, email, role }),
    });
    toast?.(`Invite sent to ${email}.`, 'success');
    onRefresh?.();
  });

  const [revokeInvite, revokingInvite] = useAction(async (id) => {
    if (!canManageTeam) return;
    await api(`/api/workspace/team/invites/${id}/revoke?organization_id=${encodeURIComponent(orgId)}`, { method: 'POST' });
    toast?.('Invite revoked.', 'success');
    onRefresh?.();
  });

  // --- Approval Rules state ---
  const [approvalRules, setApprovalRules] = useState([]);
  const [billingSummary, setBillingSummary] = useState(null);
  const [implStatus, setImplStatus] = useState(null);

  // --- GL Mapping state ---
  // The AP pipeline posts bills against per-tenant GL codes stored in
  // ``settings_json["gl_account_map"]``. Reads via GET /erp/gl-map,
  // writes via PUT /erp/gl-map. Chart of accounts from the connected
  // ERP powers the dropdowns via GET /api/workspace/chart-of-accounts.
  const [glMap, setGlMap] = useState({});
  const [glMapOriginal, setGlMapOriginal] = useState({});
  const [chartAccounts, setChartAccounts] = useState([]);
  const [loadingChart, setLoadingChart] = useState(false);
  const glMapDirty = JSON.stringify(glMap) !== JSON.stringify(glMapOriginal);

  useEffect(() => {
    if (!orgId) return;
    let cancelled = false;
    api(`/erp/gl-map?organization_id=${encodeURIComponent(orgId)}`, { silent: true })
      .then((res) => {
        if (cancelled) return;
        const mapping = res?.gl_account_map || {};
        setGlMap(mapping);
        setGlMapOriginal(mapping);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [orgId]);

  const fetchChart = async (force = false) => {
    if (!erp.connected) {
      toast?.('Connect your ERP first.', 'error');
      return;
    }
    setLoadingChart(true);
    try {
      const qs = new URLSearchParams({
        organization_id: orgId,
        active_only: 'true',
        ...(force ? { force_refresh: 'true' } : {}),
      });
      const res = await api(`/api/workspace/chart-of-accounts?${qs.toString()}`);
      setChartAccounts(Array.isArray(res?.accounts) ? res.accounts : []);
      const count = res?.account_count ?? 0;
      toast?.(`Loaded ${count} accounts from ${res?.erp_type || 'ERP'}.`, 'success');
    } catch (exc) {
      toast?.('Could not load chart of accounts.', 'error');
    } finally {
      setLoadingChart(false);
    }
  };

  const updateGlMap = (key, value) => {
    const trimmed = String(value || '').trim();
    setGlMap((prev) => {
      const next = { ...prev };
      if (trimmed) next[key] = trimmed;
      else delete next[key];
      return next;
    });
  };

  const [saveGlMap, savingGlMap] = useAction(async () => {
    if (!canManageCompany) return;
    await api(`/erp/gl-map?organization_id=${encodeURIComponent(orgId)}`, {
      method: 'PUT',
      body: JSON.stringify({ gl_account_map: glMap }),
    });
    setGlMapOriginal(glMap);
    toast?.('GL mapping saved.', 'success');
  });

  // §13: Fetch metered billing summary + implementation status
  useEffect(() => {
    if (!orgId) return;
    api(`/api/workspace/subscription/billing-summary?organization_id=${encodeURIComponent(orgId)}`, { silent: true })
      .then((data) => setBillingSummary(data))
      .catch(() => {});
    api(`/api/workspace/implementation/status?organization_id=${encodeURIComponent(orgId)}`, { silent: true })
      .then((data) => setImplStatus(data))
      .catch(() => {});
  }, [orgId]);
  const [showAddRule, setShowAddRule] = useState(false);

  useEffect(() => {
    if (!orgId) return;
    let cancelled = false;
    api(`/settings/${encodeURIComponent(orgId)}`)
      .then((res) => {
        if (!cancelled && Array.isArray(res?.approval_thresholds)) {
          setApprovalRules(res.approval_thresholds);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [orgId]);

  const [saveApprovalRules, savingApprovalRules] = useAction(async (rules) => {
    if (!canManageCompany) return;
    await api(`/settings/${encodeURIComponent(orgId)}/approval-thresholds`, {
      method: 'PUT',
      body: JSON.stringify({ approval_thresholds: rules }),
    });
    toast?.('Approval rules saved.', 'success');
  });

  const resetFieldBorder = (e) => { e.target.style.borderColor = ''; };

  const addApprovalRule = async () => {
    const channel = document.getElementById('cl-rule-channel')?.value?.trim();
    if (!channel) {
      document.getElementById('cl-rule-channel')?.style?.setProperty('border-color', '#DC2626');
      toast?.('Approver channel is required.', 'error');
      return;
    }
    const minAmt = parseFloat(document.getElementById('cl-rule-min')?.value || '0');
    const maxRaw = document.getElementById('cl-rule-max')?.value?.trim();
    if (maxRaw && parseFloat(maxRaw) <= minAmt) {
      document.getElementById('cl-rule-max')?.style?.setProperty('border-color', '#DC2626');
      toast?.('Max amount must be greater than min amount.', 'error');
      return;
    }
    const min = minAmt;
    const max = parseFloat(maxRaw) || 0;
    const approvers = (document.getElementById('cl-rule-approvers')?.value || '').split(',').map((s) => s.trim()).filter(Boolean);
    const glCodes = (document.getElementById('cl-rule-gl')?.value || '').split(',').map((s) => s.trim()).filter(Boolean);
    const departments = (document.getElementById('cl-rule-depts')?.value || '').split(',').map((s) => s.trim()).filter(Boolean);
    const vendors = (document.getElementById('cl-rule-vendors')?.value || '').split(',').map((s) => s.trim()).filter(Boolean);
    const approvalType = document.getElementById('cl-rule-type')?.value || 'any';

    if (!approvers.length) {
      toast?.('Add at least one approver email.', 'error');
      return;
    }

    // Validate approver emails before save. A misspelled address
    // ("alice@co") silently breaks routing — the callback never
    // reaches a real inbox and the invoice stalls. Backend runs the
    // same validation (email-validator) but catching it here gives
    // the user fast feedback before the Save round-trip.
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
    const invalidEmails = approvers.filter((a) => !emailRegex.test(a));
    if (invalidEmails.length) {
      document.getElementById('cl-rule-approvers')?.style?.setProperty('border-color', '#DC2626');
      toast?.(`Invalid approver email: ${invalidEmails.join(', ')}`, 'error');
      return;
    }

    const newRule = { min_amount: min, max_amount: max, approver_channel: channel, approvers, gl_codes: glCodes, departments, vendors, approval_type: approvalType };
    const updated = [...approvalRules, newRule];
    setApprovalRules(updated);
    setShowAddRule(false);
    await saveApprovalRules(updated);
  };

  const deleteApprovalRule = async (index) => {
    const updated = approvalRules.filter((_, i) => i !== index);
    setApprovalRules(updated);
    await saveApprovalRules(updated);
  };

  const billingPreview = [
    { label: 'Plan', value: planName },
    { label: 'Status', value: sub.status || 'Active' },
    {
      label: 'Billing cycle',
      value: String(sub.billing_cycle || 'monthly').toLowerCase() === 'yearly' ? 'Annual' : 'Monthly',
    },
    {
      label: sub.status === 'trialing' ? 'Trial ends' : 'Current period',
      value: formatDisplayDate(sub.status === 'trialing' ? sub.trial_ends_at : sub.current_period_end),
    },
  ];
  const billingUsagePreview = [
    { label: 'Invoices', value: Number(usage.invoices_this_month || 0).toLocaleString() },
    { label: 'AI credits', value: Number(usage.ai_credits_this_month || 0).toLocaleString() },
    { label: 'Users', value: Number(usage.users_count || 0).toLocaleString() },
  ];

  return html`
    <div class=${`secondary-banner ${canManageAny ? '' : 'warning'}`}>
      <div class="secondary-banner-copy">
        <h3>Settings</h3>
        <p class="muted">
          ERP, policies, approvals, team, and billing.
        </p>
      </div>
      <div class="secondary-banner-actions" style="flex-wrap:wrap">
        <button class=${`segmented-button btn-sm${activeSection === 'erp' ? ' is-active' : ''}`} onClick=${() => scrollToSection(erpRef, 'erp')}>ERP Connection</button>
        <button class=${`segmented-button btn-sm${activeSection === 'gl' ? ' is-active' : ''}`} onClick=${() => scrollToSection(glMappingRef, 'gl')}>GL Mapping</button>
        <button class=${`segmented-button btn-sm${activeSection === 'policy' ? ' is-active' : ''}`} onClick=${() => scrollToSection(policyRef, 'policy')}>AP Policy</button>
        <button class=${`segmented-button btn-sm${activeSection === 'approval' ? ' is-active' : ''}`} onClick=${() => scrollToSection(approvalRef, 'approval')}>Approval Routing</button>
        <button class=${`segmented-button btn-sm${activeSection === 'vendor' ? ' is-active' : ''}`} onClick=${() => scrollToSection(vendorPolicyRef, 'vendor')}>Vendor Onboarding</button>
        <button class=${`segmented-button btn-sm${activeSection === 'autonomy' ? ' is-active' : ''}`} onClick=${() => scrollToSection(autonomyRef, 'autonomy')}>Autonomy</button>
        <button class=${`segmented-button btn-sm${activeSection === 'team' ? ' is-active' : ''}`} onClick=${() => scrollToSection(teamRef, 'team')}>Team</button>
        <button class=${`segmented-button btn-sm${activeSection === 'billing' ? ' is-active' : ''}`} onClick=${() => scrollToSection(billingRef, 'billing')}>Billing</button>
      </div>
    </div>

    <div class="settings-summary-grid" style="margin-bottom:20px">
      <div class="settings-summary-card">
        <strong>Pending invites</strong>
        <span>${Number(invites.filter((invite) => invite.status === 'pending').length).toLocaleString()} waiting for a response.</span>
      </div>
      <div class="settings-summary-card">
        <strong>Workspace</strong>
        <span>${org.domain || 'Domain not set'} · ${org.integration_mode === 'per_org' ? 'Per organization' : 'Shared workspace'}</span>
      </div>
      <div class="settings-summary-card">
        <strong>Plan</strong>
        <span>${planName} · ${sub.status || 'Active'}</span>
      </div>
      <div class="settings-summary-card">
        <strong>Access model</strong>
        <span>Admins manage setup. Operators work the queue. Read-only teammates can follow records without making changes.</span>
      </div>
    </div>

    <div class="secondary-main">
      <!-- §16.1 ERP Connection -->
      <div class="panel" ref=${erpRef}>
        <div class="panel-head compact">
          <div>
            <h3 >ERP Connection</h3>
            <p class="muted" >Connect your accounting system. Clearledgr posts approved invoices here.</p>
          </div>
        </div>
        <div class="settings-summary-grid">
          <div class="settings-summary-card">
            <strong>ERP</strong>
            ${erp.connected
              ? html`<span><span class="status-badge connected">${erpType || 'Connected'}</span></span>`
              : html`<button class="empty-cta" onClick=${goToConnections}>Connect an ERP →</button>`}
          </div>
          <div class="settings-summary-card">
            <strong>Gmail</strong>
            ${gmail.connected
              ? html`<span><span class="status-badge connected">Connected</span></span>`
              : html`<button class="empty-cta" onClick=${goToConnections}>Connect Gmail →</button>`}
          </div>
          <div class="settings-summary-card">
            <strong>Approval surface</strong>
            ${slack.connected
              ? html`<span><span class="status-badge connected">Slack</span></span>`
              : teams.connected
                ? html`<span><span class="status-badge connected">Teams</span></span>`
                : html`<button class="empty-cta" onClick=${goToConnections}>Choose Slack or Teams →</button>`}
          </div>
          <div class="settings-summary-card">
            <strong>Write permissions</strong>
            <span>${erp.connected ? 'Auto-post enabled' : 'Not configured'}</span>
          </div>
          <div class="settings-summary-card">
            <strong>Data scope</strong>
            <span>PO lines, GRN records, vendor master, chart of accounts</span>
          </div>
          <div class="settings-summary-card">
            <strong>Last sync</strong>
            <span>${erp.last_sync_at ? new Date(erp.last_sync_at).toLocaleString() : 'Never'}</span>
          </div>
        </div>
      </div>

      <!-- §16.1b GL Account Mapping -->
      <div class="panel" ref=${glMappingRef}>
        <div class="panel-head compact">
          <div>
            <h3 >GL Account Mapping</h3>
            <p class="muted" >
              Map Clearledgr's AP categories to the GL codes in your ${erpType || 'ERP'}. Bills post to these accounts when approved.
            </p>
          </div>
          ${erp.connected ? html`
            <button
              class="segmented-button btn-sm"
              onClick=${() => fetchChart(chartAccounts.length > 0)}
              disabled=${loadingChart}
            >
              ${loadingChart ? 'Loading…' : (chartAccounts.length ? 'Refresh chart' : 'Load from ERP')}
            </button>
          ` : null}
        </div>

        ${!erp.connected ? html`
          <div class="secondary-empty">Connect your ERP above, then return here to map accounts.</div>
        ` : html`
          <div class="secondary-form-grid">
            ${AP_GL_CATEGORIES.map((cat) => {
              const currentValue = glMap[cat.key] || '';
              const matchingAccounts = chartAccounts.filter((a) => {
                if (!cat.accountType) return true;
                const t = String(a.type || a.account_type || '').toLowerCase();
                return t.includes(cat.accountType);
              });
              return html`
                <div class="field-row">
                  <label>
                    ${cat.label}${cat.required ? html`<span class="required">*</span>` : null}
                  </label>
                  ${chartAccounts.length ? html`
                    <select
                      value=${currentValue}
                      onChange=${(e) => updateGlMap(cat.key, e.target.value)}
                    >
                      <option value="">— Select account —</option>
                      ${matchingAccounts.map((a) => {
                        const code = a.code || a.number || a.id || '';
                        const name = a.name || a.label || '';
                        return html`<option value=${code}>${code}${name ? ' — ' + name : ''}</option>`;
                      })}
                      ${currentValue && !matchingAccounts.some((a) => (a.code || a.number || a.id) === currentValue) ? html`
                        <option value=${currentValue}>${currentValue} (not in chart)</option>
                      ` : null}
                    </select>
                  ` : html`
                    <input
                      type="text"
                      value=${currentValue}
                      placeholder=${cat.placeholder}
                      onChange=${(e) => updateGlMap(cat.key, e.target.value)}
                    />
                  `}
                  <div class="form-help">${cat.help}</div>
                </div>
              `;
            })}
          </div>

          <div class="form-actions-row">
            <div class="form-help">
              ${(() => {
                const required = AP_GL_CATEGORIES.filter((c) => c.required);
                const requiredSet = required.filter((c) => glMap[c.key]).length;
                const totalSet = AP_GL_CATEGORIES.filter((c) => glMap[c.key]).length;
                if (requiredSet < required.length) {
                  return html`<span class="form-error">⚠ ${required.length - requiredSet} required category still unmapped.</span>`;
                }
                return `${totalSet} of ${AP_GL_CATEGORIES.length} categories mapped.`;
              })()}
            </div>
            <button
              class="btn-primary btn-sm"
              onClick=${saveGlMap}
              disabled=${!glMapDirty || savingGlMap || !canManageCompany}
            >
              ${savingGlMap ? 'Saving…' : 'Save mapping'}
            </button>
          </div>
        `}
      </div>

      <!-- §16.2 AP Policy -->
      <div class="panel" ref=${policyRef}>
        <div class="panel-head compact">
          <div>
            <h3 >AP Policy</h3>
            <p class="muted" >These controls reflect your documented finance policy, not generic defaults.</p>
          </div>
        </div>
        <div class="secondary-form-grid">
          <div class="field-row">
            <label>Auto-approve threshold</label>
            <input
              type="number" step="100" min="0" placeholder="500"
              value=${bootstrap?.organization?.settings?.auto_approve_amount_threshold || ''}
              onChange=${(e) => api(`/settings/${encodeURIComponent(orgId)}`, { method: 'PUT', body: JSON.stringify({ auto_approve_amount_threshold: parseFloat(e.target.value) || 0 }) }).then(() => toast('Threshold saved', 'success')).catch(() => toast('Save failed', 'error'))}
            />
            <div class="form-help">Invoices below this amount with passed 3-way match auto-approve. Default: £0 (all require approval).</div>
          </div>
          <div class="field-row">
            <label>Match tolerance</label>
            <input
              type="number" step="0.5" min="0" max="10" placeholder="2"
              value=${bootstrap?.organization?.settings?.match_tolerance_pct || ''}
              onChange=${(e) => api(`/settings/${encodeURIComponent(orgId)}`, { method: 'PUT', body: JSON.stringify({ match_tolerance_pct: parseFloat(e.target.value) || 2 }) }).then(() => toast('Tolerance saved', 'success')).catch(() => toast('Save failed', 'error'))}
            />
            <div class="form-help">% delta between invoice and GRN before exception is raised. Default: 2%.</div>
          </div>
          <div class="field-row">
            <label>Duplicate detection window</label>
            <input
              type="number" step="10" min="30" max="365" placeholder="90"
              value=${bootstrap?.organization?.settings?.duplicate_window_days || ''}
              onChange=${(e) => api(`/settings/${encodeURIComponent(orgId)}`, { method: 'PUT', body: JSON.stringify({ duplicate_window_days: parseInt(e.target.value) || 90 }) }).then(() => toast('Window saved', 'success')).catch(() => toast('Save failed', 'error'))}
            />
            <div class="form-help">Days to look back for vendor+amount+reference duplicate matches. Default: 90.</div>
          </div>
          <div class="field-row">
            <label>Payment ceiling</label>
            <input
              type="number" step="1000" min="0" placeholder="10000"
              value=${bootstrap?.organization?.settings?.payment_ceiling || ''}
              onChange=${(e) => api(`/settings/${encodeURIComponent(orgId)}`, { method: 'PUT', body: JSON.stringify({ payment_ceiling: parseFloat(e.target.value) || 10000 }) }).then(() => toast('Ceiling saved', 'success')).catch(() => toast('Save failed', 'error'))}
            />
            <div class="form-help">No autonomous payment above this amount without CFO approval. Default: £10,000.</div>
          </div>
        </div>
      </div>

      <!-- §16.4 Vendor Onboarding Policy -->
      <div class="panel" ref=${vendorPolicyRef}>
        <div class="panel-head compact">
          <div>
            <h3 >Vendor Onboarding Policy</h3>
            <p class="muted" >Control how the agent chases and verifies new vendors.</p>
          </div>
        </div>
        <div class="secondary-form-grid">
          <div>
            <label >First chase delay</label>
            <div class="field-value">24 hours</div>
            <div class="muted" >Agent chases unresponsive vendors after 24h. Preview shown in Slack before sending.</div>
          </div>
          <div>
            <label >Escalation window</label>
            <div class="field-value">72 hours</div>
            <div class="muted" >Escalates to AP Manager after 72h with no vendor response.</div>
          </div>
          <div>
            <label >Bank verification</label>
            <div class="field-value">Open banking</div>
            <div class="muted" >Vendor confirms ownership through the configured open banking provider via the onboarding portal.</div>
          </div>
          <div>
            <label >Abandonment</label>
            <div class="field-value">30 days</div>
            <div class="muted" >Sessions with no activity for 30 days are automatically abandoned.</div>
          </div>
        </div>
      </div>

      <!-- §16.5 Autonomy Configuration -->
      <div class="panel" ref=${autonomyRef}>
        <div class="panel-head compact">
          <div>
            <h3 >Autonomy Configuration</h3>
            <p class="muted" >Controls how much the agent does on its own.</p>
          </div>
        </div>
        <div class="secondary-form-grid">
          <div>
            <label >Processing tier</label>
            <div class="field-value">
              ${bootstrap?.trust_arc?.phase === 'week1_observation' ? 'Supervised (Week 1)' : bootstrap?.trust_arc?.phase === 'ongoing_weekly_signal' ? 'Autonomous' : 'Supervised'}
            </div>
            <div class="muted" >Progresses through the trust-building arc. Day 30 tier expansion recommendation.</div>
          </div>
          <div>
            <label >Override window</label>
            <input
              type="number" step="5" min="5" max="60" placeholder="15"
              value=${bootstrap?.organization?.settings?.workflow_controls?.override_window_minutes?.default || ''}
                            onChange=${(e) => api(`/settings/${encodeURIComponent(orgId)}`, { method: 'PUT', body: JSON.stringify({ workflow_controls: { override_window_minutes: { default: parseInt(e.target.value) || 15 } } }) }).then(() => toast('Window saved', 'success')).catch(() => toast('Save failed', 'error'))}
            />
            <div class="muted" >Minutes to undo an autonomous ERP post. Default: 15.</div>
          </div>
          <div>
            <label >Confidence threshold</label>
            <input
              type="number" step="1" min="50" max="100" placeholder="95"
              value=${Math.round((bootstrap?.organization?.settings?.auto_approve_confidence_threshold || 0.95) * 100)}
                            onChange=${(e) => api(`/settings/${encodeURIComponent(orgId)}`, { method: 'PUT', body: JSON.stringify({ auto_approve_confidence_threshold: (parseInt(e.target.value) || 95) / 100 }) }).then(() => toast('Threshold saved', 'success')).catch(() => toast('Save failed', 'error'))}
            />
            <div class="muted" >% extraction confidence required for autonomous action. Default: 95%.</div>
          </div>
          <div>
            <label >Migration status</label>
            <div class="field-value">
              ${bootstrap?.organization?.settings?.migration_status || 'Live'}
            </div>
            <div class="muted" >Parallel mode suppresses autonomous actions for comparison with existing AP system.</div>
          </div>
        </div>
      </div>

      <div class="panel" ref=${teamRef}>
        <div class="panel-head compact">
          <div>
            <h3 >Team${!canManageTeam ? html`<span class="status-badge" style="font-size:10px;margin-left:8px">Read-only</span>` : null}</h3>
            <p class="muted" >Invite the people who need to work or monitor finance operations.</p>
          </div>
        </div>
        <div class="settings-section-grid">
          <div>
            <div class="secondary-form-grid">
              <input id="cl-invite-email" placeholder="teammate@company.com" disabled=${!canManageTeam} />
              <select id="cl-invite-role" disabled=${!canManageTeam}>
                <option value="ap_clerk">AP Clerk</option>
                <option value="ap_manager">AP Manager</option>
                <option value="financial_controller">Financial Controller</option>
                <option value="cfo">CFO</option>
                <option value="read_only">Read Only</option>
              </select>
            </div>
            <div class="row-actions" style="justify-content:flex-start;margin-top:14px">
              <button class="btn-primary" onClick=${createInvite} disabled=${!canManageTeam || creatingInvite}>
                ${creatingInvite ? 'Sending…' : 'Send invite'}
              </button>
            </div>
          </div>
          <div class="secondary-note">
            AP Clerks process invoices within auto-approve threshold. AP Managers approve and manage vendor onboarding. Financial Controllers modify AP policy. CFOs connect/disconnect ERP and set autonomy tiers. Read Only is for external auditors.
          </div>
        </div>
        <div style="margin-top:18px">
          ${invites.length
            ? html`<div class="secondary-list">
                ${invites.map((invite) => html`<${InviteRow} key=${invite.id} invite=${invite} onRevoke=${revokeInvite} canManage=${canManageTeam && !revokingInvite} />`)}
              </div>`
            : html`<div class="secondary-empty">No invites yet. Send one when someone needs access.</div>`}
        </div>
      </div>

      <div class="panel" ref=${billingRef}>
        <div class="panel-head compact">
          <div>
            <h3 >Billing${!canManagePlan ? html`<span class="status-badge" style="font-size:10px;margin-left:8px">Read-only</span>` : null}</h3>
            <p class="muted" >Plan, usage, and subscription — managed here inside Gmail.</p>
          </div>
        </div>

        <!-- Current plan + usage against limits -->
        <div class="settings-section-grid">
          <div>
            <div class="settings-summary-grid">
              ${billingPreview.map((entry) => html`
                <div class="settings-summary-card" key=${entry.label}>
                  <strong>${entry.label}</strong>
                  <span>${entry.value}</span>
                </div>
              `)}
            </div>
          </div>
          <div>
            <div class="settings-summary-grid">
              <div class="settings-summary-card">
                <strong>Seats</strong>
                <span>${billingSummary ? `${billingSummary.active_seats} active + ${billingSummary.read_only_seats} read-only` : `${Number(usage.users_count || 0)} users`}</span>
              </div>
              <div class="settings-summary-card">
                <strong>Invoices</strong>
                <span>${billingSummary ? `${billingSummary.invoices_this_month} (${billingSummary.invoice_volume_band})` : `${Number(usage.invoices_this_month || 0).toLocaleString()} this month`}${billingSummary?.invoice_overage_count > 0 ? ` · ${billingSummary.invoice_overage_count} overage` : ''}</span>
              </div>
              <div class="settings-summary-card">
                <strong>Agent credits</strong>
                <span>${billingSummary ? `${billingSummary.ai_credits_used} used · ${billingSummary.ai_credits_remaining} remaining` : `${Number(usage.ai_credits_this_month || 0).toLocaleString()} this month`}</span>
              </div>
              ${billingSummary ? html`
                <div class="settings-summary-card">
                  <strong>Estimated total</strong>
                  <span style="font:600 14px/1 'Geist Mono',monospace;">$${billingSummary.estimated_total?.toLocaleString()}/mo</span>
                </div>
              ` : ''}
            </div>
          </div>
        </div>

        <!-- §13: Plan comparison + upgrade inside Gmail -->
        ${canManagePlan ? html`
          <div style="margin-top:16px;border-top:1px solid var(--cl-border, #e2e8f0);padding-top:16px;">
            <strong style="font-size:13px;">Change plan</strong>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px;">
              ${[
                { id: 'starter', name: 'Starter', price: '$79/mo', annual: '$65/mo annual', desc: 'Up to 500 invoices/mo. One ERP, Slack integration, core AP and Vendor Onboarding. Go live in under 30 minutes.' },
                { id: 'professional', name: 'Professional', price: '$149/mo', annual: '$125/mo annual', desc: 'Per seat plus invoice volume. Multi-entity, 3-way match, advanced reporting, API access, priority support.' },
                { id: 'enterprise', name: 'Enterprise', price: '$299/mo', annual: '$249/mo annual', desc: 'NetSuite/SAP custom. Unlimited users, custom ERP integrations, SSO, data residency. Contract.' },
              ].map((tier) => html`
                <div key=${tier.id} style="border:1px solid ${(sub.plan || '').toLowerCase() === tier.id ? '#00D67E' : '#E2E8F0'};border-radius:8px;padding:12px;${(sub.plan || '').toLowerCase() === tier.id ? 'background:#ECFDF5;' : ''}">
                  <strong style="font-size:14px;">${tier.name}</strong>
                  <div style="font:600 16px/1.2 'Geist Mono',monospace;color:#0A1628;margin:4px 0;">${tier.price}</div>
                  <div style="font:400 11px/1 'DM Sans',sans-serif;color:#94A3B8;margin-bottom:4px;">${tier.annual}</div>
                  <div class="muted" style="font-size:11px;margin-bottom:8px;">${tier.desc}</div>
                  ${(sub.plan || '').toLowerCase() === tier.id
                    ? html`<span style="font-size:11px;color:#00D67E;font-weight:600;">Current plan</span>`
                    : html`<button class="btn-secondary btn-sm" onClick=${() => {
                        api('/api/workspace/subscription/plan', {
                          method: 'POST',
                          body: JSON.stringify({ organization_id: orgId, plan: tier.id }),
                        }).then(() => { toast('Plan updated to ' + tier.name, 'success'); onRefresh?.(); })
                          .catch(() => toast('Plan change failed', 'error'));
                      }}>Switch to ${tier.name}</button>`
                  }
                </div>
              `)}
            </div>
          </div>
        ` : ''}
      </div>

      ${implStatus?.steps ? html`
        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 >Implementation checklist</h3>
              <p class="muted" >${implStatus.completed_count || 0} of ${implStatus.total_count || 0} steps complete</p>
            </div>
          </div>
          <div style="display:flex;flex-direction:column;gap:8px;">
            ${(implStatus.steps || []).map((step) => html`
              <div key=${step.key} style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:${step.completed ? '#ECFDF5' : '#FBFCFD'};border:1px solid ${step.completed ? '#BBF7D0' : '#E2E8F0'};border-radius:6px;">
                <span style="font-size:14px;">${step.completed ? '\u2705' : '\u2B1C'}</span>
                <div style="flex:1;">
                  <div style="font:500 13px/1.3 'DM Sans',sans-serif;color:#0A1628;">${step.label}</div>
                  ${step.description ? html`<div style="font:400 11px/1.3 'DM Sans',sans-serif;color:#5C6B7A;">${step.description}</div>` : ''}
                </div>
                ${!step.completed && canManageCompany ? html`
                  <button class="btn-outline btn-sm" onClick=${() => {
                    api('/api/workspace/implementation/complete-step', {
                      method: 'POST',
                      body: JSON.stringify({ step_key: step.key, organization_id: orgId }),
                    }).then(() => {
                      setImplStatus((prev) => ({
                        ...prev,
                        completed_count: (prev?.completed_count || 0) + 1,
                        steps: (prev?.steps || []).map((s) => s.key === step.key ? { ...s, completed: true } : s),
                      }));
                      toast?.('Step completed', 'success');
                    }).catch(() => toast?.('Failed to mark step', 'error'));
                  }}>Mark done</button>
                ` : ''}
              </div>
            `)}
          </div>
        </div>
      ` : ''}

      <div class="panel" ref=${approvalRef}>
        <div class="panel-head compact">
          <div>
            <h3 >Approval rules${!canManageCompany ? html`<span class="status-badge" style="font-size:10px;margin-left:8px">Read-only</span>` : null}</h3>
            <p class="muted" >Define who approves invoices based on amount, GL code, department, or vendor.</p>
          </div>
          <div class="row-actions">
            ${canManageCompany ? html`
              <button class="btn-primary" onClick=${() => setShowAddRule(!showAddRule)} disabled=${savingApprovalRules}>
                ${showAddRule ? 'Cancel' : 'Add rule'}
              </button>
            ` : null}
          </div>
        </div>

        ${showAddRule && canManageCompany ? html`
          <div style="padding:16px 0;border-bottom:1px solid var(--cl-border, #e2e8f0)">
            <div class="secondary-form-stack">
              <div class="secondary-form-grid" style="gap:12px">
                <div><label>Min amount</label><input id="cl-rule-min" type="number" placeholder="0" step="0.01" onFocus=${resetFieldBorder} /></div>
                <div><label>Max amount</label><input id="cl-rule-max" type="number" placeholder="10000" step="0.01" onFocus=${resetFieldBorder} /></div>
              </div>
              <div class="secondary-form-grid" style="gap:12px">
                <div>
                  <label>Channel</label>
                  <select id="cl-rule-channel" onFocus=${resetFieldBorder}>
                    <option value="slack">Slack</option>
                    <option value="teams">Teams</option>
                    <option value="email">Email</option>
                  </select>
                </div>
                <div>
                  <label>Approval type</label>
                  <select id="cl-rule-type">
                    <option value="any">Any approver</option>
                    <option value="all">All approvers</option>
                  </select>
                </div>
              </div>
              <div><label>Approvers</label><input id="cl-rule-approvers" placeholder="alice@co.com, bob@co.com" /></div>
              <div><label>GL codes</label><input id="cl-rule-gl" placeholder="6000, 6100 (optional)" /></div>
              <div><label>Departments</label><input id="cl-rule-depts" placeholder="engineering, marketing (optional)" /></div>
              <div><label>Vendors</label><input id="cl-rule-vendors" placeholder="Acme Corp, Widgets Inc (optional)" /></div>
            </div>
            <div class="row-actions" style="justify-content:flex-start;margin-top:14px">
              <button class="btn-primary" onClick=${addApprovalRule} disabled=${savingApprovalRules}>
                ${savingApprovalRules ? 'Saving...' : 'Save rule'}
              </button>
            </div>
          </div>
        ` : null}

        <div style="margin-top:18px">
          ${approvalRules.length
            ? html`<div class="secondary-list">
                ${approvalRules.map((rule, idx) => html`
                  <div class="secondary-row" key=${idx}>
                    <div class="secondary-row-copy">
                      <div class="secondary-inline-actions" style="margin-bottom:4px">
                        <strong style="font-size:14px;margin-right:2px">
                          $${Number(rule.min_amount || 0).toLocaleString()} – $${rule.max_amount ? Number(rule.max_amount).toLocaleString() : 'No limit'}
                        </strong>
                        <span class="status-badge">${rule.approver_channel || 'slack'}</span>
                        <span class="status-badge connected">${rule.approval_type === 'all' ? 'All must approve' : 'Any can approve'}</span>
                      </div>
                      <div class="muted" style="font-size:12px">
                        Approvers: ${(rule.approvers || []).join(', ') || 'None'}
                      </div>
                      ${(rule.gl_codes || []).length ? html`<div class="muted" style="font-size:12px">GL codes: ${rule.gl_codes.join(', ')}</div>` : null}
                      ${(rule.departments || []).length ? html`<div class="muted" style="font-size:12px">Departments: ${rule.departments.join(', ')}</div>` : null}
                      ${(rule.vendors || []).length ? html`<div class="muted" style="font-size:12px">Vendors: ${rule.vendors.join(', ')}</div>` : null}
                    </div>
                    ${canManageCompany ? html`
                      <button class="btn-danger btn-sm" onClick=${() => deleteApprovalRule(idx)} disabled=${savingApprovalRules}>Delete</button>
                    ` : null}
                  </div>
                `)}
              </div>`
            : html`<div class="secondary-empty">No approval rules yet. Add one to route invoices for review based on amount or category.</div>`}
        </div>

        <div class="secondary-note" style="margin-top:14px">
          Rules are evaluated in order. The first rule whose amount range, GL codes, departments, and vendors match the invoice will be used to route the approval request.
        </div>
      </div>
    </div>
  `;
}
