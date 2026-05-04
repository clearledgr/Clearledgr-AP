import { h } from 'preact';
import { useRef, useState, useEffect, useCallback, useMemo } from 'preact/hooks';
import htm from 'htm';
import { hasCapability, useAction } from '../route-helpers.js';

const html = htm.bind(h);

function formatDisplayDate(value) {
  if (!value) return 'Not set';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Not set';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

// Module 6 spec line 214 — "last active". Render as relative time
// when recent, fall back to date when older. NULL/empty → "—".
function formatLastActive(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const now = Date.now();
  const diffMs = now - date.getTime();
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return 'just now';
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  if (sec < 604800) return `${Math.round(sec / 86400)}d ago`;
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
  // Team invites aren't part of the bootstrap response — fetch from
  // /api/workspace/team/invites on mount and refetch when orgId
  // changes. The previous code read bootstrap?.teamInvites which
  // was always undefined, so the invite list rendered empty even
  // when invites existed in the DB.
  const [invites, setInvites] = useState([]);
  useEffect(() => {
    let cancelled = false;
    api(`/api/workspace/team/invites?organization_id=${encodeURIComponent(orgId)}`)
      .then((res) => { if (!cancelled) setInvites(Array.isArray(res?.invites) ? res.invites : []); })
      .catch(() => { if (!cancelled) setInvites([]); });
    return () => { cancelled = true; };
  }, [api, orgId]);
  const org = bootstrap?.organization || {};
  const sub = bootstrap?.subscription || {};
  const usage = sub.usage || {};
  const usageKeys = Object.keys(usage);
  const planName = (sub.plan || 'free').charAt(0).toUpperCase() + (sub.plan || 'free').slice(1);

  const canManageTeam = hasCapability(bootstrap, 'manage_team');
  const canManageCompany = hasCapability(bootstrap, 'manage_company');
  const canManagePlan = hasCapability(bootstrap, 'manage_plan');
  const canManageAny = canManageTeam || canManageCompany || canManagePlan;

  const workspaceRef = useRef(null);
  const erpRef = useRef(null);
  const glMappingRef = useRef(null);
  const policyRef = useRef(null);
  const approvalRef = useRef(null);
  const vendorPolicyRef = useRef(null);
  const autonomyRef = useRef(null);
  const teamRef = useRef(null);
  const rolesRef = useRef(null);
  const billingRef = useRef(null);
  // Module 11 — three new sections.
  const apiKeysRef = useRef(null);
  const escalationRef = useRef(null);
  const notificationsRef = useRef(null);
  // Module 9 — FX rates section.
  const fxRatesRef = useRef(null);

  // ERP + integration state from bootstrap. Backend identifies each
  // integration via `name` (workspace_shell.py:_*_status_for_org);
  // ERP type lives on the first item of `connections[]` rather than
  // top-level on the erp integration object.
  const integrations = bootstrap?.integrations || [];
  const gmail = integrations.find((i) => i.name === 'gmail') || {};
  const slack = integrations.find((i) => i.name === 'slack') || {};
  const teams = integrations.find((i) => i.name === 'teams') || {};
  const erp = integrations.find((i) => i.name === 'erp') || {};
  const erpKind = erp?.connections?.[0]?.erp_type || '';
  const erpType = erpKind.charAt(0).toUpperCase() + erpKind.slice(1);

  const [activeSection, setActiveSection] = useState('workspace');
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
    // Module 6 Pass D — optional per-entity scope. The
    // <select multiple> below collects entity_ids the admin wants
    // to scope the invitee to. Empty selection = workspace-wide
    // access, which matches the legacy behaviour.
    const entitySelect = document.getElementById('cl-invite-entities');
    const entityRestrictions = entitySelect
      ? Array.from(entitySelect.selectedOptions).map((o) => o.value).filter(Boolean)
      : [];
    const body = { organization_id: orgId, email, role };
    if (entityRestrictions.length > 0) {
      body.entity_restrictions = entityRestrictions;
    }
    await api('/api/workspace/team/invites', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    const scope = entityRestrictions.length > 0
      ? ` (scoped to ${entityRestrictions.length} entit${entityRestrictions.length === 1 ? 'y' : 'ies'})`
      : '';
    toast?.(`Invite sent to ${email}${scope}.`, 'success');
    onRefresh?.();
  });

  const [revokeInvite, revokingInvite] = useAction(async (id) => {
    if (!canManageTeam) return;
    await api(`/api/workspace/team/invites/${id}/revoke?organization_id=${encodeURIComponent(orgId)}`, { method: 'POST' });
    toast?.('Invite revoked.', 'success');
    onRefresh?.();
  });

  // ── Workspace name inline edit ──
  // Backed by PATCH /api/workspace/org/settings with patch.organization_name.
  // Server-side: admin role gate, length 1-128, no control chars, audit_event
  // emitted with event_type='organization_renamed'. Topbar picks up the new
  // name on the next bootstrap fetch we trigger via onRefresh().
  const [editingOrgName, setEditingOrgName] = useState(false);
  const [orgNameDraft, setOrgNameDraft] = useState(org.name || '');
  // Map server validation tokens back to inline form copy.
  const _ORG_RENAME_ERROR_COPY = {
    organization_name_required: 'Workspace name is required.',
    organization_name_too_long: 'Workspace name is too long (max 128 characters).',
    organization_name_invalid_characters: 'Workspace name contains invalid characters.',
    admin_required: 'Only owners and admins can rename the workspace.',
    org_mismatch: 'Cross-organization rename is not allowed.',
  };

  const beginEditOrgName = () => {
    if (!canManageCompany) return;
    setOrgNameDraft(org.name || '');
    setEditingOrgName(true);
  };
  const cancelEditOrgName = () => {
    setEditingOrgName(false);
    setOrgNameDraft(org.name || '');
  };

  const [saveOrgName, savingOrgName] = useAction(async () => {
    if (!canManageCompany) return;
    const next = String(orgNameDraft || '').trim();
    if (!next) {
      toast?.('Workspace name is required.', 'error');
      return;
    }
    if (next === (org.name || '').trim()) {
      // No-op edit. Close the editor without round-tripping.
      setEditingOrgName(false);
      return;
    }
    try {
      await api('/api/workspace/org/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          organization_id: orgId,
          patch: { organization_name: next },
        }),
      });
    } catch (err) {
      const detail = err?.detail || err?.body?.detail;
      const copy = _ORG_RENAME_ERROR_COPY[detail] || 'Could not save workspace name.';
      toast?.(copy, 'error');
      return;
    }
    toast?.(`Workspace renamed to ${next}.`, 'success');
    setEditingOrgName(false);
    onRefresh?.();
  });

  // ── Workspace domain inline edit ──
  // Same PATCH endpoint, patch.organization_domain. Domain is optional —
  // empty string clears it server-side. Used for SAML/SCIM matching and
  // the "Setup connection" sender allowlist on outbound vendor email.
  const [editingOrgDomain, setEditingOrgDomain] = useState(false);
  const [orgDomainDraft, setOrgDomainDraft] = useState(org.domain || '');
  const _ORG_DOMAIN_ERROR_COPY = {
    organization_domain_too_long: 'Domain is too long.',
    organization_domain_invalid_characters: 'Domain contains invalid characters.',
    admin_required: 'Only owners and admins can edit the workspace domain.',
    org_mismatch: 'Cross-organization edit is not allowed.',
  };
  const beginEditOrgDomain = () => {
    if (!canManageCompany) return;
    setOrgDomainDraft(org.domain || '');
    setEditingOrgDomain(true);
  };
  const cancelEditOrgDomain = () => {
    setEditingOrgDomain(false);
    setOrgDomainDraft(org.domain || '');
  };
  const [saveOrgDomain, savingOrgDomain] = useAction(async () => {
    if (!canManageCompany) return;
    const next = String(orgDomainDraft || '').trim();
    if (next === (org.domain || '').trim()) {
      setEditingOrgDomain(false);
      return;
    }
    try {
      await api('/api/workspace/org/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          organization_id: orgId,
          patch: { organization_domain: next },
        }),
      });
    } catch (err) {
      const detail = err?.detail || err?.body?.detail;
      const copy = _ORG_DOMAIN_ERROR_COPY[detail] || 'Could not save workspace domain.';
      toast?.(copy, 'error');
      return;
    }
    toast?.(next ? `Domain set to ${next}.` : 'Domain cleared.', 'success');
    setEditingOrgDomain(false);
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
        <button class=${`segmented-button btn-sm${activeSection === 'workspace' ? ' is-active' : ''}`} onClick=${() => scrollToSection(workspaceRef, 'workspace')}>Workspace</button>
        <button class=${`segmented-button btn-sm${activeSection === 'erp' ? ' is-active' : ''}`} onClick=${() => scrollToSection(erpRef, 'erp')}>ERP Connection</button>
        <button class=${`segmented-button btn-sm${activeSection === 'gl' ? ' is-active' : ''}`} onClick=${() => scrollToSection(glMappingRef, 'gl')}>GL Mapping</button>
        <button class=${`segmented-button btn-sm${activeSection === 'policy' ? ' is-active' : ''}`} onClick=${() => scrollToSection(policyRef, 'policy')}>AP Policy</button>
        <button class=${`segmented-button btn-sm${activeSection === 'approval' ? ' is-active' : ''}`} onClick=${() => scrollToSection(approvalRef, 'approval')}>Approval Routing</button>
        <button class=${`segmented-button btn-sm${activeSection === 'vendor' ? ' is-active' : ''}`} onClick=${() => scrollToSection(vendorPolicyRef, 'vendor')}>Vendor Onboarding</button>
        <button class=${`segmented-button btn-sm${activeSection === 'autonomy' ? ' is-active' : ''}`} onClick=${() => scrollToSection(autonomyRef, 'autonomy')}>Autonomy</button>
        <button class=${`segmented-button btn-sm${activeSection === 'team' ? ' is-active' : ''}`} onClick=${() => scrollToSection(teamRef, 'team')}>Team</button>
        <button class=${`segmented-button btn-sm${activeSection === 'roles' ? ' is-active' : ''}`} onClick=${() => scrollToSection(rolesRef, 'roles')}>Roles</button>
        <button class=${`segmented-button btn-sm${activeSection === 'billing' ? ' is-active' : ''}`} onClick=${() => scrollToSection(billingRef, 'billing')}>Billing</button>
        <button class=${`segmented-button btn-sm${activeSection === 'api-keys' ? ' is-active' : ''}`} onClick=${() => scrollToSection(apiKeysRef, 'api-keys')}>API keys</button>
        <button class=${`segmented-button btn-sm${activeSection === 'escalation' ? ' is-active' : ''}`} onClick=${() => scrollToSection(escalationRef, 'escalation')}>Escalation</button>
        <button class=${`segmented-button btn-sm${activeSection === 'notifications' ? ' is-active' : ''}`} onClick=${() => scrollToSection(notificationsRef, 'notifications')}>Notifications</button>
        <button class=${`segmented-button btn-sm${activeSection === 'fx' ? ' is-active' : ''}`} onClick=${() => scrollToSection(fxRatesRef, 'fx')}>FX rates</button>
      </div>
    </div>

    <div class="settings-summary-grid" style="margin-bottom:20px">
      <div class="settings-summary-card">
        <strong>Pending invites</strong>
        <span>${Number(invites.filter((invite) => invite.status === 'pending').length).toLocaleString()} waiting for a response.</span>
      </div>
      <div class="settings-summary-card">
        <strong>Workspace</strong>
        ${editingOrgName
          ? html`
            <div class="cl-inline-edit">
              <input
                type="text"
                class="cl-inline-edit-input"
                value=${orgNameDraft}
                maxLength=${128}
                disabled=${savingOrgName}
                onInput=${(e) => setOrgNameDraft(e.target.value)}
                onKeyDown=${(e) => {
                  if (e.key === 'Enter') saveOrgName();
                  if (e.key === 'Escape') cancelEditOrgName();
                }}
                aria-label="Workspace display name" />
              <div class="cl-inline-edit-actions">
                <button
                  class="btn btn-sm btn-primary"
                  onClick=${saveOrgName}
                  disabled=${savingOrgName}>
                  ${savingOrgName ? 'Saving…' : 'Save'}
                </button>
                <button
                  class="btn btn-sm btn-tertiary"
                  onClick=${cancelEditOrgName}
                  disabled=${savingOrgName}>
                  Cancel
                </button>
              </div>
            </div>`
          : html`
            <span class="cl-inline-edit-display">
              <span class="cl-inline-edit-value">${org.name || 'Untitled'}</span>
              ${canManageCompany
                ? html`<button
                    class="cl-inline-edit-trigger"
                    type="button"
                    onClick=${beginEditOrgName}
                    aria-label="Rename workspace">
                    Rename
                  </button>`
                : null}
            </span>
            <span class="muted small">${org.domain || 'Domain not set'} · ${org.integration_mode === 'per_org' ? 'Per organization' : 'Shared workspace'}</span>`}
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
      <!-- Workspace identity (org name + domain). The topbar reads
           bootstrap.organization.name; without this panel the auto-
           provisioned default ("default") sticks because the rename
           UI was previously hidden in a summary card. -->
      <div class="panel" ref=${workspaceRef}>
        <div class="panel-head compact">
          <div>
            <h3>Workspace</h3>
            <p class="muted">The display name and domain for this organization. The name appears in the topbar and on every email the agent sends.</p>
          </div>
        </div>
        <div class="settings-section-grid" style="grid-template-columns: 1fr 1fr; gap:20px">
          <div>
            <label class="muted small" style="display:block;margin-bottom:6px;letter-spacing:0.04em;text-transform:uppercase">Workspace name</label>
            ${editingOrgName
              ? html`
                <div class="cl-inline-edit">
                  <input
                    type="text"
                    class="cl-inline-edit-input"
                    value=${orgNameDraft}
                    maxLength=${128}
                    disabled=${savingOrgName}
                    onInput=${(e) => setOrgNameDraft(e.target.value)}
                    onKeyDown=${(e) => {
                      if (e.key === 'Enter') saveOrgName();
                      if (e.key === 'Escape') cancelEditOrgName();
                    }}
                    autoFocus
                    aria-label="Workspace display name" />
                  <div class="cl-inline-edit-actions" style="margin-top:8px">
                    <button class="btn btn-sm btn-primary" onClick=${saveOrgName} disabled=${savingOrgName}>
                      ${savingOrgName ? 'Saving…' : 'Save'}
                    </button>
                    <button class="btn btn-sm btn-tertiary" onClick=${cancelEditOrgName} disabled=${savingOrgName}>
                      Cancel
                    </button>
                  </div>
                </div>`
              : html`
                <div style="display:flex;align-items:center;gap:12px">
                  <span style="font-size:16px;font-weight:600">${org.name || 'Untitled'}</span>
                  ${canManageCompany
                    ? html`<button class="btn btn-sm btn-tertiary" onClick=${beginEditOrgName} aria-label="Rename workspace">Rename</button>`
                    : null}
                </div>
                ${(org.name || '').trim().toLowerCase() === 'default'
                  ? html`<p class="muted small" style="margin-top:6px;color:var(--warning)">This is the placeholder name. Rename it to your company so it shows correctly in the topbar and in vendor emails.</p>`
                  : null}`}
          </div>
          <div>
            <label class="muted small" style="display:block;margin-bottom:6px;letter-spacing:0.04em;text-transform:uppercase">Email domain</label>
            ${editingOrgDomain
              ? html`
                <div class="cl-inline-edit">
                  <input
                    type="text"
                    class="cl-inline-edit-input"
                    value=${orgDomainDraft}
                    maxLength=${253}
                    placeholder="acme.com"
                    disabled=${savingOrgDomain}
                    onInput=${(e) => setOrgDomainDraft(e.target.value)}
                    onKeyDown=${(e) => {
                      if (e.key === 'Enter') saveOrgDomain();
                      if (e.key === 'Escape') cancelEditOrgDomain();
                    }}
                    autoFocus
                    aria-label="Workspace email domain" />
                  <div class="cl-inline-edit-actions" style="margin-top:8px">
                    <button class="btn btn-sm btn-primary" onClick=${saveOrgDomain} disabled=${savingOrgDomain}>
                      ${savingOrgDomain ? 'Saving…' : 'Save'}
                    </button>
                    <button class="btn btn-sm btn-tertiary" onClick=${cancelEditOrgDomain} disabled=${savingOrgDomain}>
                      Cancel
                    </button>
                  </div>
                </div>`
              : html`
                <div style="display:flex;align-items:center;gap:12px">
                  <span style="font-size:14px">${org.domain || html`<span class="muted">Not set</span>`}</span>
                  ${canManageCompany
                    ? html`<button class="btn btn-sm btn-tertiary" onClick=${beginEditOrgDomain} aria-label="Edit workspace domain">${org.domain ? 'Edit' : 'Add'}</button>`
                    : null}
                </div>
                <p class="muted small" style="margin-top:6px">Used for SAML/SCIM matching and outbound vendor email.</p>`}
          </div>
        </div>
        <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--cl-line);font-size:12px;color:var(--cl-ink-muted);display:flex;gap:24px;flex-wrap:wrap">
          <span><strong style="color:var(--cl-ink-secondary)">Organization ID</strong> · <code style="font-family:var(--cl-font-mono)">${org.id || orgId}</code></span>
          <span><strong style="color:var(--cl-ink-secondary)">Mode</strong> · ${org.integration_mode === 'per_org' ? 'Per organization' : 'Shared workspace'}</span>
        </div>
      </div>

      <!-- §16.1 ERP Connection -->
      <div class="panel" ref=${erpRef}>
        <div class="panel-head compact">
          <div>
            <h3 >ERP Connection</h3>
            <p class="muted" >Connect your accounting system. Solden posts approved invoices here.</p>
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
              Map Solden's AP categories to the GL codes in your ${erpType || 'ERP'}. Bills post to these accounts when approved.
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
            <${InviteEntityScope}
              api=${api}
              orgId=${orgId}
              canManage=${canManageTeam} />
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

        <${TeamMembersPanel}
          api=${api}
          toast=${toast}
          orgId=${orgId}
          actorEmail=${(bootstrap?.current_user?.email || '').toLowerCase()}
          canManage=${canManageTeam} />
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
                          method: 'PATCH',
                          body: { organization_id: orgId, plan: tier.id },
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

      <${CustomRolesPanel}
        api=${api}
        orgId=${orgId}
        toast=${toast}
        canManage=${canManageTeam}
        panelRef=${rolesRef} />

      <${EntityRolesPanel}
        api=${api}
        orgId=${orgId}
        toast=${toast}
        canManage=${canManageTeam} />

      <${SAMLPanel}
        api=${api}
        orgId=${orgId}
        toast=${toast}
        canManage=${canManageCompany} />

      <${ApiKeysPanel}
        api=${api}
        toast=${toast}
        panelRef=${apiKeysRef} />

      <${EscalationPoliciesPanel}
        api=${api}
        toast=${toast}
        panelRef=${escalationRef} />

      <${NotificationPreferencesPanel}
        api=${api}
        toast=${toast}
        panelRef=${notificationsRef} />

      <${FraudThresholdsPanel}
        api=${api}
        orgId=${orgId}
        toast=${toast}
        canManage=${canManageCompany} />

      <${DataExportPanel}
        orgId=${orgId}
        toast=${toast}
        canManage=${canManageCompany} />

      <${FxRatesPanel}
        api=${api}
        toast=${toast}
        panelRef=${fxRatesRef} />

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


// ─── Module 6 Pass A — Custom roles panel ─────────────────────────────
// Lives on SettingsPage between Team and Billing. Lets admins compose
// up to 10 custom roles per workspace from the canonical permission
// catalog. Backed by:
//   GET /api/workspace/permissions/catalog
//   GET/POST/PUT/DELETE /api/workspace/roles/custom
function CustomRolesPanel({ api, orgId, toast, canManage, panelRef }) {
  const [catalog, setCatalog] = useState(null);
  const [roles, setRoles] = useState([]);
  const [limit, setLimit] = useState(10);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  // editorState: null | { mode: 'create' | 'edit', role?: row }
  const [editorState, setEditorState] = useState(null);

  const loadAll = async () => {
    if (!api || !orgId) return;
    setLoading(true);
    setErr(null);
    try {
      const [cat, list] = await Promise.all([
        api('/api/workspace/permissions/catalog'),
        api(`/api/workspace/roles/custom?organization_id=${encodeURIComponent(orgId)}`),
      ]);
      setCatalog(cat);
      setRoles(Array.isArray(list?.custom_roles) ? list.custom_roles : []);
      setLimit(list?.limit || 10);
    } catch (exc) {
      setErr(String(exc?.message || exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadAll(); }, [api, orgId]);

  const onCreate = async (payload) => {
    try {
      await api(
        `/api/workspace/roles/custom?organization_id=${encodeURIComponent(orgId)}`,
        { method: 'POST', body: JSON.stringify(payload) },
      );
      toast?.(`Custom role “${payload.name}” created.`, 'success');
      setEditorState(null);
      await loadAll();
    } catch (exc) {
      const detail = exc?.detail || exc?.body?.detail || {};
      const reason = (typeof detail === 'object' ? detail.reason : null) || 'unknown';
      const msg =
        reason === 'custom_role_limit'
          ? `Workspace has reached the ${limit}-custom-role limit.`
          : reason === 'name_taken'
          ? 'A role with that name already exists.'
          : reason === 'validation_failed'
          ? 'Pick at least one valid permission.'
          : exc?.message || 'Could not save the role.';
      toast?.(msg, 'error');
    }
  };

  const onUpdate = async (roleId, payload) => {
    try {
      await api(
        `/api/workspace/roles/custom/${encodeURIComponent(roleId)}?organization_id=${encodeURIComponent(orgId)}`,
        { method: 'PUT', body: JSON.stringify(payload) },
      );
      toast?.('Role updated.', 'success');
      setEditorState(null);
      await loadAll();
    } catch (exc) {
      const detail = exc?.detail || exc?.body?.detail || {};
      const reason = typeof detail === 'object' ? detail.reason : null;
      const msg =
        reason === 'name_taken'
          ? 'A role with that name already exists.'
          : reason === 'validation_failed'
          ? 'Pick at least one valid permission.'
          : exc?.message || 'Could not save the role.';
      toast?.(msg, 'error');
    }
  };

  const onDelete = async (role) => {
    if (!window.confirm(`Delete custom role “${role.name}”?\n\nUsers assigned to it will fall back to their standard role.`)) return;
    try {
      await api(
        `/api/workspace/roles/custom/${encodeURIComponent(role.id)}?organization_id=${encodeURIComponent(orgId)}`,
        { method: 'DELETE' },
      );
      toast?.('Role deleted.', 'success');
      await loadAll();
    } catch (exc) {
      toast?.(exc?.message || 'Could not delete the role.', 'error');
    }
  };

  const permissionEntries = catalog?.permissions || [];
  const standardRoles = catalog?.standard_roles || {};

  return html`
    <div class="panel" ref=${panelRef}>
      <div class="panel-head compact">
        <div>
          <h3>Roles & permissions${!canManage ? html`<span class="status-badge" style="font-size:10px;margin-left:8px">Read-only</span>` : null}</h3>
          <p class="muted">Six standard roles cover most cases. Compose up to ${limit} custom roles for finer-grained control.</p>
        </div>
        ${canManage ? html`
          <button
            class="btn-primary btn-sm"
            disabled=${loading || roles.length >= limit}
            onClick=${() => setEditorState({ mode: 'create' })}>
            ${roles.length >= limit ? 'Limit reached' : '+ New custom role'}
          </button>
        ` : null}
      </div>

      ${err ? html`<div class="form-error">${err}</div>` : null}

      <details class="cl-roles-standard">
        <summary>Standard role permissions</summary>
        ${permissionEntries.length === 0
          ? html`<div class="muted">Loading…</div>`
          : html`
            <table class="cl-roles-matrix">
              <thead>
                <tr>
                  <th>Permission</th>
                  ${Object.keys(standardRoles).map((role) => html`<th>${role}</th>`)}
                </tr>
              </thead>
              <tbody>
                ${permissionEntries.map((p) => html`
                  <tr key=${p.key}>
                    <td>
                      <strong>${p.key}</strong>
                      <div class="muted" style="font-size:11px">${p.description}</div>
                    </td>
                    ${Object.entries(standardRoles).map(([role, perms]) => html`
                      <td class="cl-roles-cell">
                        ${(perms || []).includes(p.key) ? '✓' : '—'}
                      </td>
                    `)}
                  </tr>
                `)}
              </tbody>
            </table>`}
      </details>

      <div class="cl-roles-list" style="margin-top:14px">
        ${roles.length === 0 && !loading
          ? html`<div class="muted">No custom roles yet. Standard roles cover most cases.</div>`
          : roles.map((r) => html`
            <div class="cl-roles-row" key=${r.id}>
              <div>
                <strong>${r.name}</strong>
                ${r.description ? html`<div class="muted" style="font-size:12px">${r.description}</div>` : null}
                <div class="cl-roles-perms">
                  ${(r.permissions || []).map((p) => html`<span class="cl-roles-chip">${p}</span>`)}
                </div>
              </div>
              ${canManage ? html`
                <div class="row-actions">
                  <button class="btn-secondary btn-sm" onClick=${() => setEditorState({ mode: 'edit', role: r })}>Edit</button>
                  <button class="btn-danger btn-sm" onClick=${() => onDelete(r)}>Delete</button>
                </div>
              ` : null}
            </div>
          `)}
      </div>

      ${editorState ? html`
        <${CustomRoleEditor}
          permissionEntries=${permissionEntries}
          mode=${editorState.mode}
          initial=${editorState.role}
          onCancel=${() => setEditorState(null)}
          onSubmit=${(payload) => editorState.mode === 'edit'
            ? onUpdate(editorState.role.id, payload)
            : onCreate(payload)} />
      ` : null}
    </div>
  `;
}


function CustomRoleEditor({ permissionEntries, mode, initial, onCancel, onSubmit }) {
  const [name, setName] = useState(initial?.name || '');
  const [description, setDescription] = useState(initial?.description || '');
  const [selected, setSelected] = useState(new Set(initial?.permissions || []));
  const [submitting, setSubmitting] = useState(false);

  const togglePerm = (key) => {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setSelected(next);
  };

  const handleSubmit = async (e) => {
    e?.preventDefault?.();
    if (submitting) return;
    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim(),
        description: description.trim() || null,
        permissions: Array.from(selected),
      });
    } finally {
      setSubmitting(false);
    }
  };

  const isCreate = mode === 'create';
  return html`
    <form class="cl-role-editor" onSubmit=${handleSubmit}>
      <h4>${isCreate ? 'New custom role' : `Edit “${initial?.name || ''}”`}</h4>
      <label>
        <span class="cl-field-label">Name</span>
        <input
          type="text"
          value=${name}
          onInput=${(e) => setName(e.target.value)}
          placeholder="e.g., Procurement Reviewer"
          maxlength="80"
          required />
      </label>
      <label>
        <span class="cl-field-label">Description (optional)</span>
        <input
          type="text"
          value=${description}
          onInput=${(e) => setDescription(e.target.value)}
          placeholder="Approves invoices but cannot configure rules"
          maxlength="300" />
      </label>
      <div class="cl-role-editor-perms">
        <div class="cl-field-label">Permissions (${selected.size} selected)</div>
        <div class="cl-perm-grid">
          ${permissionEntries.map((p) => html`
            <label key=${p.key} class="cl-perm-checkbox">
              <input
                type="checkbox"
                checked=${selected.has(p.key)}
                onChange=${() => togglePerm(p.key)} />
              <div>
                <strong>${p.key}</strong>
                <div class="muted" style="font-size:11px">${p.description}</div>
              </div>
            </label>
          `)}
        </div>
      </div>
      <div class="row-actions" style="justify-content:flex-end">
        <button type="button" class="btn-tertiary btn-sm" onClick=${onCancel} disabled=${submitting}>Cancel</button>
        <button type="submit" class="btn-primary btn-sm" disabled=${submitting || selected.size === 0 || !name.trim()}>
          ${submitting ? 'Saving…' : (isCreate ? 'Create role' : 'Save changes')}
        </button>
      </div>
    </form>
  `;
}


// ─── Module 6 Pass D — Invite entity scope picker ────────────────────
// Sits below the email/role inputs. Multi-select of legal entities;
// empty selection = workspace-wide invite (legacy behaviour). Loaded
// from /api/workspace/entities on mount; hidden when the workspace
// has zero entities (single-entity tenants don't need this).
function InviteEntityScope({ api, orgId, canManage }) {
  const [entities, setEntities] = useState([]);
  useEffect(() => {
    if (!api || !orgId) return;
    let cancelled = false;
    api(`/api/workspace/entities?organization_id=${encodeURIComponent(orgId)}`)
      .then((resp) => {
        if (cancelled) return;
        const list = Array.isArray(resp?.entities) ? resp.entities : [];
        setEntities(list);
      })
      .catch(() => { /* silent */ });
    return () => { cancelled = true; };
  }, [api, orgId]);

  if (entities.length === 0) return null;

  return html`
    <div style="margin-top:12px">
      <label class="cl-field-label" for="cl-invite-entities">
        Entity scope (optional — hold ⌘ / Ctrl to multi-select)
      </label>
      <select
        id="cl-invite-entities"
        multiple
        disabled=${!canManage}
        size=${Math.min(Math.max(entities.length, 3), 6)}
        style="width:100%">
        ${entities.map((e) => html`
          <option key=${e.id} value=${e.id}>${e.name} ${e.code ? html`(${e.code})` : null}</option>
        `)}
      </select>
      <div class="muted" style="font-size:11px;margin-top:4px">
        Leave empty for workspace-wide access. Selected entities will get a per-entity role override on first login.
      </div>
    </div>
  `;
}



// ─── Module 6 Pass B — Per-entity role assignments ────────────────────
// Lives directly under the CustomRolesPanel on SettingsPage. Each
// active workspace user expands into an inline editor where the
// admin can override their org-level role per legal entity and set
// an optional approval ceiling. Backed by:
//   GET /api/workspace/team/users
//   GET /api/workspace/entities
//   GET /api/workspace/users/{id}/entity-roles
//   PUT /api/workspace/users/{id}/entity-roles
//   GET /api/workspace/roles/custom (for custom-role tokens)
function EntityRolesPanel({ api, orgId, toast, canManage }) {
  const [users, setUsers] = useState([]);
  const [entities, setEntities] = useState([]);
  const [customRoles, setCustomRoles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [openUserId, setOpenUserId] = useState(null);

  const loadAll = async () => {
    if (!api || !orgId) return;
    setLoading(true);
    setErr(null);
    try {
      const [usersResp, entitiesResp, rolesResp] = await Promise.all([
        api(`/api/workspace/team/users?organization_id=${encodeURIComponent(orgId)}`),
        api(`/api/workspace/entities?organization_id=${encodeURIComponent(orgId)}`),
        api(`/api/workspace/roles/custom?organization_id=${encodeURIComponent(orgId)}`),
      ]);
      setUsers(Array.isArray(usersResp?.users) ? usersResp.users : []);
      setEntities(Array.isArray(entitiesResp?.entities) ? entitiesResp.entities : []);
      setCustomRoles(Array.isArray(rolesResp?.custom_roles) ? rolesResp.custom_roles : []);
    } catch (exc) {
      setErr(String(exc?.message || exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadAll(); }, [api, orgId]);

  return html`
    <div class="panel">
      <div class="panel-head compact">
        <div>
          <h3>Per-entity role overrides${!canManage ? html`<span class="status-badge" style="font-size:10px;margin-left:8px">Read-only</span>` : null}</h3>
          <p class="muted">Override a user's role inside a specific legal entity. Per-amount approval ceilings compose with their effective role.</p>
        </div>
      </div>

      ${err ? html`<div class="form-error">${err}</div>` : null}
      ${loading ? html`<div class="muted">Loading…</div>` : null}
      ${!loading && entities.length === 0 ? html`
        <div class="muted">Add legal entities under Settings → Entities to start setting per-entity overrides.</div>
      ` : null}

      ${!loading && users.length > 0 && entities.length > 0 ? html`
        <div class="cl-entity-roles-list">
          ${users.map((u) => html`
            <div class="cl-entity-roles-row" key=${u.id}>
              <div class="cl-entity-roles-head">
                <div>
                  <strong>${u.name}</strong>
                  <span class="muted" style="margin-left:6px;font-size:12px">${u.email}</span>
                </div>
                <div class="row-actions">
                  <span class="cl-roles-chip">Org role: ${u.role}</span>
                  <button
                    class="btn-secondary btn-sm"
                    onClick=${() => setOpenUserId(openUserId === u.id ? null : u.id)}>
                    ${openUserId === u.id ? 'Close' : 'Per-entity overrides'}
                  </button>
                </div>
              </div>

              ${openUserId === u.id ? html`
                <${EntityRolesEditor}
                  api=${api}
                  orgId=${orgId}
                  toast=${toast}
                  user=${u}
                  entities=${entities}
                  customRoles=${customRoles}
                  canManage=${canManage}
                  onSaved=${() => setOpenUserId(null)} />
              ` : null}
            </div>
          `)}
        </div>
      ` : null}
    </div>
  `;
}


function EntityRolesEditor({ api, orgId, toast, user, entities, customRoles, canManage, onSaved }) {
  const [assignments, setAssignments] = useState({}); // { entity_id: {role, approval_ceiling} }
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const STANDARD_ROLES = [
    { value: '', label: 'Use org role' },
    { value: 'owner', label: 'Owner' },
    { value: 'cfo', label: 'CFO' },
    { value: 'financial_controller', label: 'Financial Controller' },
    { value: 'ap_manager', label: 'AP Manager' },
    { value: 'ap_clerk', label: 'AP Clerk' },
    { value: 'read_only', label: 'Read-only' },
  ];

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!api || !orgId || !user?.id) return;
      setLoading(true);
      try {
        const resp = await api(
          `/api/workspace/users/${encodeURIComponent(user.id)}/entity-roles?organization_id=${encodeURIComponent(orgId)}`,
        );
        if (cancelled) return;
        const map = {};
        (resp?.assignments || []).forEach((a) => {
          map[a.entity_id] = {
            role: a.role,
            approval_ceiling: a.approval_ceiling || '',
          };
        });
        setAssignments(map);
      } catch (exc) {
        toast?.(`Could not load assignments: ${exc?.message || exc}`, 'error');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [api, orgId, user?.id]);

  const setField = (entityId, field, value) => {
    setAssignments((prev) => {
      const next = { ...prev };
      const row = { ...(next[entityId] || {}), [field]: value };
      // Clear-out a row when role goes back to "use org role" with no
      // ceiling — it shouldn't exist server-side.
      if (!row.role && !row.approval_ceiling) {
        delete next[entityId];
      } else {
        next[entityId] = row;
      }
      return next;
    });
  };

  const onSave = async () => {
    if (!canManage) return;
    setSubmitting(true);
    try {
      const payload = {
        assignments: Object.entries(assignments)
          .filter(([_eid, row]) => row.role)
          .map(([entity_id, row]) => ({
            entity_id,
            role: row.role,
            approval_ceiling: row.approval_ceiling
              ? Number(row.approval_ceiling)
              : null,
          })),
      };
      await api(
        `/api/workspace/users/${encodeURIComponent(user.id)}/entity-roles?organization_id=${encodeURIComponent(orgId)}`,
        { method: 'PUT', body: JSON.stringify(payload) },
      );
      toast?.('Per-entity roles saved.', 'success');
      onSaved?.();
    } catch (exc) {
      const detail = exc?.detail || exc?.body?.detail || {};
      const reason = typeof detail === 'object' ? detail.reason : null;
      const msg =
        reason === 'invalid_role'
          ? `Unknown role: ${detail.role}`
          : reason === 'validation_failed'
          ? detail.message || 'Validation failed.'
          : exc?.message || 'Could not save assignments.';
      toast?.(msg, 'error');
    } finally {
      setSubmitting(false);
    }
  };

  return html`
    <div class="cl-entity-roles-editor">
      ${loading ? html`<div class="muted">Loading current assignments…</div>` : null}
      ${!loading ? html`
        <table class="cl-entity-roles-table">
          <thead>
            <tr>
              <th>Entity</th>
              <th>Role override</th>
              <th>Approval ceiling</th>
            </tr>
          </thead>
          <tbody>
            ${entities.map((e) => {
              const row = assignments[e.id] || { role: '', approval_ceiling: '' };
              return html`
                <tr key=${e.id}>
                  <td>
                    <strong>${e.name}</strong>
                    <div class="muted" style="font-size:11px">${e.code || e.id}</div>
                  </td>
                  <td>
                    <select
                      value=${row.role}
                      onChange=${(ev) => setField(e.id, 'role', ev.target.value)}
                      disabled=${!canManage}>
                      <optgroup label="Standard">
                        ${STANDARD_ROLES.map((r) => html`<option value=${r.value}>${r.label}</option>`)}
                      </optgroup>
                      ${customRoles.length > 0 ? html`
                        <optgroup label="Custom">
                          ${customRoles.map((cr) => html`<option value=${cr.id}>${cr.name}</option>`)}
                        </optgroup>
                      ` : null}
                    </select>
                  </td>
                  <td>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      placeholder="No ceiling"
                      value=${row.approval_ceiling}
                      onInput=${(ev) => setField(e.id, 'approval_ceiling', ev.target.value)}
                      disabled=${!canManage} />
                  </td>
                </tr>`;
            })}
          </tbody>
        </table>
        <div class="row-actions" style="justify-content:flex-end;margin-top:10px">
          <button
            class="btn-primary btn-sm"
            onClick=${onSave}
            disabled=${submitting || !canManage}>
            ${submitting ? 'Saving…' : 'Save assignments'}
          </button>
        </div>
      ` : null}
    </div>
  `;
}





// ─── Module 11 — API keys panel ─────────────────────────────────────
//
// Show-once-on-create raw key (Stripe/GitHub-style). Subsequent views
// only ever see the prefix. Revocation is a soft delete; rotation
// revokes + issues atomically.

// ─── Module 6 — SAML SSO config panel ───────────────────────────
//
// Spec line 220: Azure AD + Okta + Google Workspace SAML + OneLogin +
// generic SAML 2.0. Backend at clearledgr/api/saml.py is complete
// (CRUD + SP metadata + ACS POST + JIT provisioning + audit). This
// panel wires the admin UI: load current config, show SP metadata
// URL for IdP-side setup, edit IdP fields (entity ID, SSO URL,
// X.509 cert), pick attribute mappings, JIT provisioning toggle,
// enable/disable. Tier-gated: only customers on Growth+ see SSO,
// but the panel renders for everyone with manage_company so we
// don't have to thread the entitlement check here — backend
// rejects with 403 if the org's plan doesn't include it.

function SAMLPanel({ api, orgId, toast, canManage }) {
  const [config, setConfig] = useState(null);
  const [configured, setConfigured] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    enabled: true,
    idp_entity_id: '',
    idp_sso_url: '',
    idp_certificate_pem: '',
    sp_entity_id: '',
    sp_acs_url: '',
    attribute_email: 'email',
    attribute_role: '',
    attribute_entity: '',
    default_role: 'ap_clerk',
    default_entity_id: '',
    jit_provisioning: true,
    idp_slo_url: '',
    sp_slo_url: '',
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api(`/api/workspace/saml/config?organization_id=${encodeURIComponent(orgId)}`);
      setConfigured(!!resp.configured);
      const c = resp.config || {};
      setConfig(c);
      setForm((prev) => ({
        ...prev,
        enabled: c.enabled ?? true,
        idp_entity_id: c.idp_entity_id || '',
        idp_sso_url: c.idp_sso_url || '',
        sp_entity_id: c.sp_entity_id || `https://workspace.clearledgr.com/saml/${orgId}/sp`,
        sp_acs_url: c.sp_acs_url || `https://api.clearledgr.com/saml/${orgId}/acs`,
        attribute_email: c.attribute_email || 'email',
        attribute_role: c.attribute_role || '',
        attribute_entity: c.attribute_entity || '',
        default_role: c.default_role || 'ap_clerk',
        default_entity_id: c.default_entity_id || '',
        jit_provisioning: c.jit_provisioning ?? true,
        idp_slo_url: c.idp_slo_url || '',
        sp_slo_url: c.sp_slo_url || '',
      }));
    } catch (exc) {
      toast?.(`Could not load SAML config: ${exc?.message || exc}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [api, orgId, toast]);

  useEffect(() => { void load(); }, [load]);

  const setField = (key) => (e) => {
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const onSave = useCallback(async (e) => {
    e?.preventDefault?.();
    if (!canManage) return;
    setSaving(true);
    try {
      const body = {
        ...form,
        // PEM is the only credential field; if the user hasn't
        // pasted a new one and there's an existing fingerprint, we
        // skip sending it. Backend validates & re-saves the
        // existing record. (The API requires PEM on every PUT, so
        // the user MUST paste the cert to save — surface this in
        // the helper text below.)
      };
      const resp = await api(`/api/workspace/saml/config?organization_id=${encodeURIComponent(orgId)}`, {
        method: 'PUT',
        body,
      });
      setConfigured(true);
      setConfig(resp.config);
      toast?.('SAML config saved.', 'success');
    } catch (exc) {
      const detail = exc?.payload?.detail;
      const msg = (detail && typeof detail === 'object' && detail.message) || exc?.message || 'Save failed';
      toast?.(`Could not save SAML config: ${msg}`, 'error');
    } finally {
      setSaving(false);
    }
  }, [api, orgId, form, canManage, toast]);

  const onDelete = useCallback(async () => {
    if (!canManage) return;
    if (!window.confirm('Remove SAML config? Users will fall back to password / OAuth login.')) return;
    setSaving(true);
    try {
      await api(`/api/workspace/saml/config?organization_id=${encodeURIComponent(orgId)}`, { method: 'DELETE' });
      setConfigured(false);
      setConfig(null);
      toast?.('SAML config removed.', 'success');
    } catch (exc) {
      toast?.(`Could not remove SAML config: ${exc?.message || exc}`, 'error');
    } finally {
      setSaving(false);
    }
  }, [api, orgId, canManage, toast]);

  const spMetadataUrl = `https://api.clearledgr.com/saml/${encodeURIComponent(orgId)}/sp-metadata`;
  const fingerprint = config?.idp_certificate?.fingerprint_sha256;
  const statusTone = !configured ? 'idle' : (form.enabled ? 'on' : 'off');
  const statusLabel = !configured
    ? 'Not configured'
    : (form.enabled ? 'Active' : 'Disabled');

  const copy = (value, label) => {
    navigator.clipboard?.writeText(value);
    toast?.(`${label} copied.`, 'success');
  };

  return html`
    <div class="panel cl-saml-panel">
      <header class="cl-saml-header">
        <div class="cl-saml-header-copy">
          <h3>SAML SSO</h3>
          <p class="muted">
            Federate sign-in through your identity provider — Azure AD, Okta, Google Workspace, OneLogin, or any SAML 2.0 IdP.
          </p>
        </div>
        <span class=${`cl-saml-status cl-saml-status-${statusTone}`}>
          <span class="cl-saml-status-dot"></span>
          ${statusLabel}
        </span>
      </header>

      ${loading ? html`<div class="muted" style="padding:24px 0">Loading SAML config…</div>` : null}

      ${!loading ? html`
        <div class="cl-saml-flow">

          <!-- STEP 1: SP-side values to register Solden in the IdP. -->
          <section class="cl-saml-step">
            <div class="cl-saml-step-head">
              <span class="cl-saml-step-marker">1</span>
              <div>
                <h4>Register Solden with your IdP</h4>
                <p class="muted">Send your IT team the metadata URL — or paste these three values into the IdP's SAML application form.</p>
              </div>
            </div>
            <div class="cl-saml-readonly-stack">
              <${SamlReadonlyField}
                label="Metadata URL (recommended)"
                value=${spMetadataUrl}
                onCopy=${() => copy(spMetadataUrl, 'Metadata URL')}
                openHref=${spMetadataUrl}
              />
              <${SamlReadonlyField}
                label="SP entity ID"
                value=${form.sp_entity_id}
                onCopy=${() => copy(form.sp_entity_id, 'SP entity ID')}
              />
              <${SamlReadonlyField}
                label="ACS URL (HTTP-POST)"
                value=${form.sp_acs_url}
                onCopy=${() => copy(form.sp_acs_url, 'ACS URL')}
              />
            </div>
          </section>

          <form onSubmit=${onSave} class="cl-saml-flow-form">

            <!-- STEP 2: Paste IdP-side values back into Solden. -->
            <section class="cl-saml-step">
              <div class="cl-saml-step-head">
                <span class="cl-saml-step-marker">2</span>
                <div>
                  <h4>Paste your IdP's settings</h4>
                  <p class="muted">Find these in your IdP's SAML application overview after creating it.</p>
                </div>
              </div>
              <div class="cl-saml-fields">
                <label class="cl-saml-field">
                  <span class="cl-saml-field-label">IdP entity ID</span>
                  <input
                    type="text"
                    placeholder="https://sts.windows.net/<tenant-id>/"
                    value=${form.idp_entity_id}
                    onInput=${setField('idp_entity_id')}
                    disabled=${!canManage}
                    required />
                </label>
                <label class="cl-saml-field">
                  <span class="cl-saml-field-label">IdP SSO URL (HTTP-POST)</span>
                  <input
                    type="url"
                    placeholder="https://login.microsoftonline.com/<tenant-id>/saml2"
                    value=${form.idp_sso_url}
                    onInput=${setField('idp_sso_url')}
                    disabled=${!canManage}
                    required />
                </label>
                <label class="cl-saml-field">
                  <span class="cl-saml-field-label">IdP X.509 certificate (PEM)</span>
                  <textarea
                    class="cl-saml-cert"
                    rows="5"
                    placeholder="-----BEGIN CERTIFICATE-----&#10;MIIC8TCCAdkC..."
                    value=${form.idp_certificate_pem}
                    onInput=${setField('idp_certificate_pem')}
                    disabled=${!canManage}
                    required></textarea>
                  ${fingerprint ? html`
                    <small class="cl-saml-field-hint">
                      Current fingerprint <code>${fingerprint.slice(0, 32)}…</code> · paste a fresh PEM to rotate.
                    </small>
                  ` : html`
                    <small class="cl-saml-field-hint">
                      Paste the entire signing cert including the BEGIN/END lines.
                    </small>
                  `}
                </label>
              </div>
            </section>

            <!-- STEP 3: Attribute mapping with sensible defaults + IdP-specific guidance. -->
            <section class="cl-saml-step">
              <div class="cl-saml-step-head">
                <span class="cl-saml-step-marker">3</span>
                <div>
                  <h4>Map IdP attributes</h4>
                  <p class="muted">Tell Solden which SAML claim carries the user's email, role, and (if multi-entity) which subsidiary they belong to.</p>
                </div>
              </div>
              <div class="cl-saml-fields">
                <label class="cl-saml-field">
                  <span class="cl-saml-field-label">Email <span class="cl-saml-field-required">(required)</span></span>
                  <input
                    type="text"
                    value=${form.attribute_email}
                    onInput=${setField('attribute_email')}
                    disabled=${!canManage}
                    placeholder="email" />
                  <small class="cl-saml-field-hint">Common values — Azure AD: <code>http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress</code> · Okta: <code>email</code> · Google: <code>email</code>.</small>
                </label>
                <label class="cl-saml-field">
                  <span class="cl-saml-field-label">Role <span class="muted">(optional)</span></span>
                  <input
                    type="text"
                    value=${form.attribute_role}
                    onInput=${setField('attribute_role')}
                    disabled=${!canManage}
                    placeholder="role" />
                  <small class="cl-saml-field-hint">If your IdP sends a role/group claim, Solden will map it to AP Clerk / Manager / etc. Leave blank to assign the default role to everyone.</small>
                </label>
                <label class="cl-saml-field">
                  <span class="cl-saml-field-label">Entity <span class="muted">(optional, multi-entity only)</span></span>
                  <input
                    type="text"
                    value=${form.attribute_entity}
                    onInput=${setField('attribute_entity')}
                    disabled=${!canManage}
                    placeholder="subsidiary" />
                  <small class="cl-saml-field-hint">For groups with multiple legal entities. Solden routes the user to the entity scope your IdP sends.</small>
                </label>
              </div>
            </section>

            <!-- STEP 4: Provisioning + enablement. -->
            <section class="cl-saml-step">
              <div class="cl-saml-step-head">
                <span class="cl-saml-step-marker">4</span>
                <div>
                  <h4>Provisioning & enable</h4>
                  <p class="muted">What happens when a new SAML user lands on Solden.</p>
                </div>
              </div>
              <div class="cl-saml-fields">
                <label class="cl-saml-field">
                  <span class="cl-saml-field-label">Default role for new users</span>
                  <select value=${form.default_role} onChange=${setField('default_role')} disabled=${!canManage}>
                    <option value="ap_clerk">AP Clerk</option>
                    <option value="ap_manager">AP Manager</option>
                    <option value="financial_controller">Financial Controller</option>
                    <option value="cfo">CFO</option>
                    <option value="read_only">Read-only</option>
                  </select>
                </label>
                <label class="cl-saml-toggle">
                  <input type="checkbox" checked=${form.jit_provisioning} onChange=${setField('jit_provisioning')} disabled=${!canManage} />
                  <span>
                    <strong>Just-in-time provisioning</strong>
                    <span class="muted small">Auto-create the user on their first SSO login. Off → only pre-invited users can sign in.</span>
                  </span>
                </label>
                <label class="cl-saml-toggle">
                  <input type="checkbox" checked=${form.enabled} onChange=${setField('enabled')} disabled=${!canManage} />
                  <span>
                    <strong>SAML enabled</strong>
                    <span class="muted small">When off, users fall back to email/password or OAuth.</span>
                  </span>
                </label>
              </div>
            </section>

            <details class="cl-saml-advanced">
              <summary>Single Logout (SLO) — optional</summary>
              <div class="cl-saml-fields" style="margin-top:12px">
                <label class="cl-saml-field">
                  <span class="cl-saml-field-label">IdP SLO URL</span>
                  <input type="url" value=${form.idp_slo_url} onInput=${setField('idp_slo_url')} disabled=${!canManage} placeholder="https://..." />
                </label>
                <label class="cl-saml-field">
                  <span class="cl-saml-field-label">SP SLO URL (echo back to IdP)</span>
                  <input type="url" value=${form.sp_slo_url} onInput=${setField('sp_slo_url')} disabled=${!canManage} placeholder="https://..." />
                </label>
              </div>
            </details>

            <footer class="cl-saml-footer">
              <button type="submit" class="btn-primary" disabled=${!canManage || saving}>
                ${saving ? 'Saving…' : (configured ? 'Save changes' : 'Save and enable SAML')}
              </button>
              ${configured ? html`
                <button
                  type="button"
                  class="btn-secondary"
                  onClick=${() => window.open(`/auth/saml/${encodeURIComponent(orgId)}/initiate?return_to=/`, '_blank')}
                  disabled=${!canManage || saving}>
                  Test SSO sign-in
                </button>
                <button type="button" class="btn-danger" onClick=${onDelete} disabled=${!canManage || saving}>
                  Remove SAML
                </button>
              ` : null}
            </footer>
          </form>
        </div>
      ` : null}
    </div>
  `;
}


function SamlReadonlyField({ label, value, onCopy, openHref }) {
  return html`
    <div class="cl-saml-readonly">
      <div class="cl-saml-readonly-label">${label}</div>
      <div class="cl-saml-readonly-row">
        <code class="cl-saml-readonly-value">${value}</code>
        <div class="cl-saml-readonly-actions">
          <button type="button" class="btn-ghost btn-sm" onClick=${onCopy}>Copy</button>
          ${openHref ? html`<a class="btn-ghost btn-sm" href=${openHref} target="_blank" rel="noreferrer">Open</a>` : null}
        </div>
      </div>
    </div>
  `;
}

function ApiKeysPanel({ api, toast, panelRef }) {
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [label, setLabel] = useState('');
  const [revealedKey, setRevealedKey] = useState(null);
  // Module 11 spec line 353 — scopes. Default to read-only across
  // AP items + vendors + reports so a fresh key is least-privilege.
  const [scopeCatalog, setScopeCatalog] = useState([]);
  const [selectedScopes, setSelectedScopes] = useState(['read:ap_items', 'read:vendors', 'read:reports']);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api('/api/workspace/api-keys');
      setKeys(resp?.api_keys || []);
    } catch (exc) {
      toast?.(`Failed to load keys: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [api, toast]);

  useEffect(() => { load(); }, [load]);

  // Pull the canonical scope catalog from the backend so the UI
  // stays in lockstep with _SCOPE_CATALOG without redeploying both.
  useEffect(() => {
    api('/api/workspace/api-keys/scopes/catalog')
      .then((resp) => setScopeCatalog(Array.isArray(resp?.scopes) ? resp.scopes : []))
      .catch(() => setScopeCatalog([]));
  }, [api]);

  const toggleScope = (scope) => {
    setSelectedScopes((prev) => prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]);
  };

  const onCreate = useCallback(async (e) => {
    e?.preventDefault?.();
    setCreating(true);
    try {
      const resp = await api('/api/workspace/api-keys', {
        method: 'POST',
        body: { label: label.trim(), scopes: selectedScopes },
      });
      setRevealedKey(resp);
      setLabel('');
      toast?.('API key created. Copy it now — it won’t be shown again.', 'success');
      await load();
    } catch (exc) {
      const detail = exc?.payload?.detail;
      const msg = (detail && typeof detail === 'object' && detail.scope) ? `Unknown scope: ${detail.scope}` : (exc?.message || 'Create failed');
      toast?.(msg, 'error');
    } finally {
      setCreating(false);
    }
  }, [api, label, selectedScopes, toast, load]);

  const onRotate = useCallback(async (key) => {
    if (!window.confirm(
      `Rotate key '${key.label || key.id}'? The old key stops working immediately.`,
    )) return;
    try {
      const resp = await api(`/api/workspace/api-keys/${key.id}/rotate`, {
        method: 'POST',
      });
      setRevealedKey(resp);
      toast?.('Key rotated. Copy the new key now.', 'success');
      await load();
    } catch (exc) {
      toast?.(`Rotate failed: ${String(exc?.message || exc)}`, 'error');
    }
  }, [api, toast, load]);

  const onRevoke = useCallback(async (key) => {
    if (!window.confirm(`Revoke key '${key.label || key.id}'? This cannot be undone.`)) return;
    try {
      await api(`/api/workspace/api-keys/${key.id}`, { method: 'DELETE' });
      toast?.('Key revoked.', 'success');
      await load();
    } catch (exc) {
      toast?.(`Revoke failed: ${String(exc?.message || exc)}`, 'error');
    }
  }, [api, toast, load]);

  const copyToClipboard = useCallback(async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      toast?.('Copied to clipboard.', 'success');
    } catch {
      toast?.('Copy failed — select the value manually.', 'error');
    }
  }, [toast]);

  return html`
    <div class="panel" ref=${panelRef}>
      <div class="panel-head">
        <strong>API keys</strong>
        <span class="muted">For your own integrations.</span>
      </div>

      <form class="cl-settings-row" onSubmit=${onCreate}>
        <label style="flex:1">
          <span class="muted" style="display:block;font-size:11px;text-transform:uppercase">Label</span>
          <input
            type="text"
            placeholder="e.g. ci-deploy or finance-team-script"
            value=${label}
            onInput=${(e) => setLabel(e.target.value)}
            disabled=${creating}
            style="width:100%"
          />
        </label>
        <button type="submit" class="btn btn-primary" disabled=${creating || selectedScopes.length === 0}>
          ${creating ? 'Creating…' : 'Create key'}
        </button>
      </form>

      ${scopeCatalog.length > 0 ? html`
        <div class="cl-api-key-scopes" role="group" aria-label="Scopes">
          <span class="cl-api-key-scopes-label">Scopes</span>
          <div class="cl-api-key-scopes-grid">
            ${scopeCatalog.map((scope) => html`
              <label key=${scope} class="cl-api-key-scope-cell">
                <input
                  type="checkbox"
                  checked=${selectedScopes.includes(scope)}
                  onChange=${() => toggleScope(scope)}
                  disabled=${creating} />
                <code>${scope}</code>
              </label>
            `)}
          </div>
          ${selectedScopes.length === 0 ? html`
            <p class="cl-api-key-scopes-empty">Pick at least one scope. A key with no scopes is rejected by every guarded route.</p>
          ` : null}
        </div>
      ` : null}

      ${revealedKey ? html`
        <div class="cl-settings-reveal" role="alert">
          <strong>Copy this key now</strong>
          <p class="muted" style="margin:4px 0 8px">
            Solden stores only a hash. After you close this banner the key cannot be retrieved.
          </p>
          <div class="cl-settings-reveal-row">
            <code style="font-family:monospace;flex:1;word-break:break-all">${revealedKey.raw_key}</code>
            <button class="btn btn-secondary" onClick=${() => copyToClipboard(revealedKey.raw_key)}>Copy</button>
            <button class="btn btn-tertiary" onClick=${() => setRevealedKey(null)}>Close</button>
          </div>
        </div>
      ` : null}

      ${loading ? html`<p class="muted">Loading…</p>` : null}
      ${!loading && keys.length === 0 ? html`
        <p class="muted" style="padding:16px 0">No API keys yet. Create one above.</p>
      ` : null}

      ${keys.length > 0 ? html`
        <table class="cl-settings-table">
          <thead>
            <tr>
              <th>Label</th>
              <th>Prefix</th>
              <th>Scopes</th>
              <th>Created</th>
              <th>Last used</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${keys.map((k) => {
              const scopes = Array.isArray(k.scopes) ? k.scopes
                : (typeof k.scopes === 'string' ? (() => { try { return JSON.parse(k.scopes); } catch { return []; } })() : []);
              return html`
                <tr key=${k.id}>
                  <td>${k.label || html`<span class="muted">(none)</span>`}</td>
                  <td><code>${k.key_prefix}</code></td>
                  <td>
                    ${scopes.length === 0
                      ? html`<span class="muted">unscoped (legacy — full access)</span>`
                      : html`<div class="cl-api-key-scope-chips">
                          ${scopes.map((s) => html`<span key=${s} class="cl-api-key-scope-chip"><code>${s}</code></span>`)}
                        </div>`}
                  </td>
                  <td class="muted">${k.created_at ? formatDisplayDate(k.created_at) : '—'}</td>
                  <td class="muted">${k.last_used_at ? formatDisplayDate(k.last_used_at) : 'never'}</td>
                  <td style="text-align:right">
                    <button class="btn btn-tertiary btn-sm" onClick=${() => onRotate(k)}>Rotate</button>
                    <button class="btn btn-tertiary btn-sm" onClick=${() => onRevoke(k)}>Revoke</button>
                  </td>
                </tr>
              `;
            })}
          </tbody>
        </table>
      ` : null}
    </div>
  `;
}


// ─── Module 4 — Customer-configurable fraud rules ──────────────────
//
// Spec line 158: "new IBAN doesn't match prior payments; unusually
// large invoice from low-frequency vendor; vendor created within
// last 30 days with first invoice over $X. Configurable per
// customer."
//
// Six knobs map to the three fraud rule types. Defaults preserve
// the historical hardcoded behaviour; overrides land in
// settings_json["fraud_thresholds"] and feed into compute_vendor_risk_score.

function FraudThresholdsPanel({ api, orgId, toast, canManage }) {
  const [cfg, setCfg] = useState(null);
  const [defaults, setDefaults] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api(`/api/workspace/fraud-thresholds?organization_id=${encodeURIComponent(orgId)}`)
      .then((r) => {
        if (cancelled) return;
        setDefaults(r?.defaults || {});
        setCfg(r?.configured || {});
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [api, orgId]);

  const merged = useMemo(() => {
    const out = { ...(defaults || {}) };
    for (const [k, v] of Object.entries(cfg || {})) {
      if (v !== null && v !== undefined && v !== '') out[k] = v;
    }
    return out;
  }, [cfg, defaults]);

  const setField = (key) => (e) => {
    const raw = e.target.value;
    const value = raw === '' ? null : (key.includes('multiplier') || key.includes('max') ? parseFloat(raw) : parseInt(raw, 10));
    setCfg((prev) => ({ ...(prev || {}), [key]: value }));
  };

  const onSave = async () => {
    setSaving(true);
    try {
      const payload = { organization_id: orgId };
      for (const k of Object.keys(merged || {})) {
        if (merged[k] !== null && merged[k] !== undefined && merged[k] !== '') payload[k] = merged[k];
      }
      const resp = await api('/api/workspace/fraud-thresholds', { method: 'PATCH', body: payload });
      setCfg(resp?.configured || {});
      toast?.('Fraud thresholds saved.', 'success');
    } catch (exc) {
      toast?.(`Save failed: ${exc?.message || exc}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  if (!cfg || !defaults) return null;

  const fields = [
    { key: 'bank_change_alert_days', label: 'Bank change alert window (days)', help: 'High-severity flag when IBAN changed within this many days.' },
    { key: 'bank_change_warn_days', label: 'Bank change warn window (days)', help: 'Medium flag for changes between alert and warn windows.' },
    { key: 'low_frequency_invoice_count_threshold', label: 'Low-frequency vendor threshold', help: 'Vendors with fewer invoices than this are flagged "low_history".' },
    { key: 'low_frequency_invoice_multiplier', label: 'Low-frequency amount multiplier', step: '0.1', help: 'Flag if invoice amount > N × vendor avg.' },
    { key: 'new_vendor_days', label: 'New vendor window (days)', help: 'Vendors created within this many days are "new".' },
    { key: 'new_vendor_first_invoice_max', label: 'New vendor first-invoice max ($)', help: 'Flag invoices above this amount from new vendors.' },
  ];

  return html`
    <div class="panel">
      <div class="panel-head compact">
        <div>
          <h3 style="margin-top:0">Fraud rules</h3>
          <p class="muted" style="margin:0">
            Three configurable signals: new IBAN mismatch, large invoice from low-frequency vendor,
            and new vendor with first invoice over a threshold. Triggers feed into the agent's
            vendor risk score; high risk forces human review.
          </p>
        </div>
      </div>
      <div class="cl-fraud-grid">
        ${fields.map((f) => html`
          <label class="cl-fraud-field" key=${f.key}>
            <span>${f.label}</span>
            <input
              type="number"
              step=${f.step || '1'}
              value=${merged[f.key] ?? ''}
              placeholder=${String(defaults[f.key])}
              onInput=${setField(f.key)}
              disabled=${!canManage || saving} />
            <small class="muted">${f.help}</small>
          </label>
        `)}
      </div>
      <div style="display:flex;justify-content:flex-end;padding-top:8px">
        <button class="btn-primary" onClick=${onSave} disabled=${!canManage || saving}>
          ${saving ? 'Saving…' : 'Save thresholds'}
        </button>
      </div>
    </div>
  `;
}


// ─── Module 11 — Full-account data export ───────────────────────────
//
// Spec line 348 + 352: portable JSON dump of the org's data. Pulls
// from /api/workspace/account/export which streams the JSON with a
// Content-Disposition header so the browser handles save-as.

function DataExportPanel({ orgId, toast, canManage }) {
  const [busy, setBusy] = useState(false);
  const onExport = useCallback(() => {
    if (!canManage) return;
    setBusy(true);
    try {
      const url = `/api/workspace/account/export?organization_id=${encodeURIComponent(orgId)}`;
      window.location.href = url;
      toast?.('Preparing data export…', 'info');
    } finally {
      // The browser navigation handles the download; reset the
      // button state quickly so a second export is always reachable.
      setTimeout(() => setBusy(false), 1200);
    }
  }, [orgId, toast, canManage]);

  return html`
    <div class="panel">
      <div class="panel-head compact">
        <div>
          <h3 style="margin-top:0">Account data export</h3>
          <p class="muted" style="margin:0">
            Download a JSON dump of this workspace — organization settings, AP items (up to 50K),
            vendors, approval rules, custom roles, team list, integration metadata, and API key
            metadata (key hashes never included). Audit log lives behind its own export with retention
            controls.
          </p>
        </div>
        <button class="btn-secondary" onClick=${onExport} disabled=${!canManage || busy}>
          ${busy ? 'Preparing…' : 'Download JSON'}
        </button>
      </div>
    </div>
  `;
}


// ─── Module 11 — Escalation policies panel ──────────────────────────
//
// Org-level "if exception X sits longer than N hours, email recipients."
// Per spec line 354 the worker fires within 1 minute of breach.

function EscalationPoliciesPanel({ api, toast, panelRef }) {
  const [policies, setPolicies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [thresholdHours, setThresholdHours] = useState(24);
  const [recipients, setRecipients] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api(
        '/api/workspace/escalation-policies?include_inactive=true',
      );
      setPolicies(resp?.policies || []);
    } catch (exc) {
      toast?.(`Failed to load policies: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [api, toast]);

  useEffect(() => { load(); }, [load]);

  const onCreate = useCallback(async (e) => {
    e?.preventDefault?.();
    const trimmed = recipients.split(',').map((r) => r.trim()).filter(Boolean);
    if (!name.trim()) {
      toast?.('Name is required.', 'error');
      return;
    }
    if (trimmed.length === 0) {
      toast?.('Add at least one recipient email.', 'error');
      return;
    }
    setCreating(true);
    try {
      await api('/api/workspace/escalation-policies', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          threshold_hours: Number(thresholdHours) || 24,
          recipients: trimmed,
          action: 'notify_email',
        }),
      });
      toast?.('Escalation policy created.', 'success');
      setName('');
      setRecipients('');
      setThresholdHours(24);
      await load();
    } catch (exc) {
      toast?.(`Create failed: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setCreating(false);
    }
  }, [api, name, thresholdHours, recipients, toast, load]);

  const onTogglePause = useCallback(async (policy) => {
    try {
      await api(`/api/workspace/escalation-policies/${policy.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: !policy.is_active }),
      });
      await load();
    } catch (exc) {
      toast?.(`Update failed: ${String(exc?.message || exc)}`, 'error');
    }
  }, [api, toast, load]);

  const onDelete = useCallback(async (policy) => {
    if (!window.confirm(`Delete policy '${policy.name}'?`)) return;
    try {
      await api(`/api/workspace/escalation-policies/${policy.id}`, {
        method: 'DELETE',
      });
      toast?.('Policy deleted.', 'success');
      await load();
    } catch (exc) {
      toast?.(`Delete failed: ${String(exc?.message || exc)}`, 'error');
    }
  }, [api, toast, load]);

  return html`
    <div class="panel" ref=${panelRef}>
      <div class="panel-head">
        <strong>Escalation policies</strong>
        <span class="muted">If an exception sits too long, email a human.</span>
      </div>

      <form class="cl-settings-row" onSubmit=${onCreate}>
        <label style="flex:2">
          <span class="muted" style="display:block;font-size:11px;text-transform:uppercase">Name</span>
          <input
            type="text"
            placeholder="e.g. needs_info > 24h"
            value=${name}
            onInput=${(e) => setName(e.target.value)}
            disabled=${creating}
            style="width:100%"
          />
        </label>
        <label style="width:120px">
          <span class="muted" style="display:block;font-size:11px;text-transform:uppercase">Hours</span>
          <input
            type="number"
            min="1"
            max="720"
            value=${thresholdHours}
            onInput=${(e) => setThresholdHours(e.target.value)}
            disabled=${creating}
            style="width:100%"
          />
        </label>
        <label style="flex:3">
          <span class="muted" style="display:block;font-size:11px;text-transform:uppercase">Recipients (comma-separated)</span>
          <input
            type="text"
            placeholder="oncall@your-co.com, ops-lead@your-co.com"
            value=${recipients}
            onInput=${(e) => setRecipients(e.target.value)}
            disabled=${creating}
            style="width:100%"
          />
        </label>
        <button type="submit" class="btn btn-primary" disabled=${creating}>
          ${creating ? 'Creating…' : 'Create policy'}
        </button>
      </form>

      ${loading ? html`<p class="muted">Loading…</p>` : null}
      ${!loading && policies.length === 0 ? html`
        <p class="muted" style="padding:16px 0">
          No escalation policies yet. Add one above so stuck exceptions don’t go unnoticed.
        </p>
      ` : null}

      ${policies.length > 0 ? html`
        <table class="cl-settings-table">
          <thead>
            <tr>
              <th>Policy</th>
              <th>Threshold</th>
              <th>Recipients</th>
              <th>State</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${policies.map((p) => html`
              <tr key=${p.id} class=${p.is_active ? '' : 'cl-settings-row-inactive'}>
                <td><strong>${p.name}</strong></td>
                <td>${p.threshold_hours}h</td>
                <td class="muted">${(p.recipients || []).join(', ') || '—'}</td>
                <td>
                  <span class=${`cl-record-chip cl-record-chip-${p.is_active ? 'success' : 'warning'}`}>
                    ${p.is_active ? 'active' : 'paused'}
                  </span>
                </td>
                <td style="text-align:right">
                  <button class="btn btn-tertiary btn-sm" onClick=${() => onTogglePause(p)}>
                    ${p.is_active ? 'Pause' : 'Resume'}
                  </button>
                  <button class="btn btn-tertiary btn-sm" onClick=${() => onDelete(p)}>Delete</button>
                </td>
              </tr>
            `)}
          </tbody>
        </table>
      ` : null}
    </div>
  `;
}


// ─── Module 11 — Notification preferences panel ─────────────────────
//
// Per-user toggles for the three notification channels. Each toggle
// has a typed slot in the schema; the GET /schema endpoint returns the
// canonical list so this UI renders every available one without
// hard-coding a copy.

function NotificationPreferencesPanel({ api, toast, panelRef }) {
  const [prefs, setPrefs] = useState(null);
  const [defaults, setDefaults] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const [prefsResp, schemaResp] = await Promise.all([
        api('/api/workspace/notification-preferences'),
        api('/api/workspace/notification-preferences/schema'),
      ]);
      setPrefs(prefsResp?.preferences || null);
      setDefaults(schemaResp?.defaults || null);
    } catch (exc) {
      toast?.(`Failed to load preferences: ${String(exc?.message || exc)}`, 'error');
    }
  }, [api, toast]);

  useEffect(() => { load(); }, [load]);

  const onToggle = useCallback(async (channel, event, next) => {
    if (saving) return;
    setSaving(true);
    try {
      const patch = { [channel]: { [event]: next } };
      const resp = await api('/api/workspace/notification-preferences', {
        method: 'PATCH',
        body: JSON.stringify(patch),
      });
      setPrefs(resp?.preferences || prefs);
    } catch (exc) {
      toast?.(`Save failed: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setSaving(false);
    }
  }, [api, prefs, saving, toast]);

  if (!prefs || !defaults) {
    return html`
      <div class="panel" ref=${panelRef}>
        <div class="panel-head">
          <strong>Notifications</strong>
        </div>
        <p class="muted">Loading…</p>
      </div>
    `;
  }

  // Collect every (channel, event) pair from the canonical schema so
  // we render every available toggle, not just the ones the user has
  // saved. Order channels stably for layout.
  const channelOrder = ['email', 'slack', 'in_app'];
  const channelLabels = { email: 'Email', slack: 'Slack', in_app: 'In-app' };

  return html`
    <div class="panel" ref=${panelRef}>
      <div class="panel-head">
        <strong>Notifications</strong>
        <span class="muted">
          Per-user. Affects what reaches you on email, Slack, and the in-app inbox.
        </span>
      </div>

      <table class="cl-settings-table cl-notif-table">
        <thead>
          <tr>
            <th>Event</th>
            ${channelOrder.map((c) => html`<th key=${c} style="text-align:center">${channelLabels[c]}</th>`)}
          </tr>
        </thead>
        <tbody>
          ${eventList(defaults).map((event) => html`
            <tr key=${event}>
              <td><strong>${humanizeEvent(event)}</strong></td>
              ${channelOrder.map((channel) => html`
                <td key=${channel} style="text-align:center">
                  ${event in (defaults[channel] || {}) ? html`
                    <input
                      type="checkbox"
                      checked=${prefs[channel]?.[event] ?? defaults[channel][event]}
                      disabled=${saving}
                      onChange=${(e) => onToggle(channel, event, e.target.checked)}
                      aria-label=${`${channelLabels[channel]} — ${humanizeEvent(event)}`}
                    />
                  ` : html`<span class="muted">—</span>`}
                </td>
              `)}
            </tr>
          `)}
        </tbody>
      </table>
    </div>
  `;
}

function eventList(defaults) {
  const seen = new Set();
  const order = [];
  for (const channel of ['email', 'slack', 'in_app']) {
    for (const event of Object.keys(defaults[channel] || {})) {
      if (!seen.has(event)) {
        seen.add(event);
        order.push(event);
      }
    }
  }
  return order;
}

function humanizeEvent(event) {
  const labels = {
    exception_raised: 'Exception raised',
    approval_requested: 'Approval requested',
    approval_decided: 'Approval decided',
    vendor_response: 'Vendor responded',
    weekly_digest: 'Weekly digest',
    report_subscriptions: 'Scheduled reports',
    comment_mentions: 'Comment mentions',
  };
  return labels[event] || event.replace(/_/g, ' ');
}


// ─── Module 6 — Active members panel (deactivate / reactivate) ──────
//
// Lives below the invite list in the Team panel. Shows every user
// in the org, their role, and an Active/Deactivated chip. Admins
// get inline Deactivate / Reactivate buttons that POST to the
// Module 6 offboarding endpoints. The UI guards self-deactivation
// client-side too (the backend enforces it as well — defense in depth).

function TeamMembersPanel({ api, toast, orgId, actorEmail, canManage }) {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api(
        `/api/workspace/team/users?organization_id=${encodeURIComponent(orgId)}&include_inactive=true`,
      );
      setUsers(resp?.users || []);
    } catch (exc) {
      toast?.(`Failed to load team: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [api, orgId, toast]);

  useEffect(() => { load(); }, [load]);

  const onDeactivate = useCallback(async (u) => {
    const confirmation = `Deactivate ${u.email}? They lose access immediately across all surfaces. Active sessions will fail on the next request, and any of their API keys will be revoked.`;
    if (!window.confirm(confirmation)) return;
    setBusyId(u.id);
    try {
      const resp = await api(`/api/workspace/team/users/${u.id}/deactivate`, {
        method: 'POST',
      });
      const revoked = (resp && resp.api_keys_revoked) || 0;
      const noun = revoked === 1 ? 'API key' : 'API keys';
      toast?.(
        revoked > 0
          ? `${u.email} deactivated. ${revoked} ${noun} revoked.`
          : `${u.email} deactivated.`,
        'success',
      );
      await load();
    } catch (exc) {
      const detail = exc?.response?.detail || exc?.detail;
      const code = detail?.code;
      const message = detail?.message || String(exc?.message || exc);
      toast?.(
        code === 'last_owner_protected'
          ? message
          : code === 'cannot_deactivate_self'
          ? message
          : `Deactivate failed: ${message}`,
        'error',
      );
    } finally {
      setBusyId(null);
    }
  }, [api, toast, load]);

  const onReactivate = useCallback(async (u) => {
    setBusyId(u.id);
    try {
      await api(`/api/workspace/team/users/${u.id}/reactivate`, {
        method: 'POST',
      });
      toast?.(`${u.email} reactivated.`, 'success');
      await load();
    } catch (exc) {
      toast?.(`Reactivate failed: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setBusyId(null);
    }
  }, [api, toast, load]);

  if (loading && users.length === 0) {
    return html`
      <div class="panel" style="margin-top:18px">
        <div class="panel-head compact">
          <h3>Active members</h3>
        </div>
        <p class="muted" style="padding:12px 0">Loading…</p>
      </div>
    `;
  }
  if (users.length === 0) return null;

  return html`
    <div class="panel" style="margin-top:18px">
      <div class="panel-head compact">
        <div>
          <h3>Active members</h3>
          <p class="muted">
            Deactivation removes access immediately across the dashboard,
            Gmail extension, Slack/Teams, and any of the user's API keys.
          </p>
        </div>
      </div>
      <table class="cl-settings-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Role</th>
            <th>Last active</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${users.map((u) => {
            const isSelf = (u.email || '').toLowerCase() === (actorEmail || '');
            const isActive = u.is_active !== false;
            const tone = isActive ? 'success' : 'warning';
            return html`
              <tr key=${u.id} class=${isActive ? '' : 'cl-settings-row-inactive'}>
                <td><strong>${u.name || u.email}</strong>${isSelf ? html` <span class="muted">(you)</span>` : null}</td>
                <td><code>${u.email}</code></td>
                <td>${(u.role || '').replace(/_/g, ' ')}</td>
                <td><span class="muted">${formatLastActive(u.last_active_at)}</span></td>
                <td>
                  <span class=${`cl-record-chip cl-record-chip-${tone}`}>
                    ${isActive ? 'active' : 'deactivated'}
                  </span>
                </td>
                <td style="text-align:right">
                  ${canManage && isActive && !isSelf ? html`
                    <button class="btn btn-tertiary btn-sm"
                      onClick=${() => onDeactivate(u)}
                      disabled=${busyId === u.id}>
                      ${busyId === u.id ? 'Working…' : 'Deactivate'}
                    </button>
                  ` : null}
                  ${canManage && !isActive ? html`
                    <button class="btn btn-tertiary btn-sm"
                      onClick=${() => onReactivate(u)}
                      disabled=${busyId === u.id}>
                      ${busyId === u.id ? 'Working…' : 'Reactivate'}
                    </button>
                  ` : null}
                </td>
              </tr>
            `;
          })}
        </tbody>
      </table>
    </div>
  `;
}


// ─── Module 9 — FX rates panel ──────────────────────────────────────
//
// Operator-managed currency conversion rates. The Volume report uses
// these to roll up cross-currency invoices into the org's functional
// currency. Per spec §304, rates come from the customer's ERP (or
// manual operator entry), never a third-party API.

function FxRatesPanel({ api, toast, panelRef }) {
  const [rates, setRates] = useState([]);
  // Empty until the API responds — don't flash 'USD' at EU/UK orgs.
  const [functionalCcy, setFunctionalCcy] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [rateValue, setRateValue] = useState('');
  const [asOfDate, setAsOfDate] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, fcResp] = await Promise.all([
        api('/api/workspace/fx-rates?limit=200'),
        api('/api/workspace/fx-rates/functional-currency'),
      ]);
      setRates(list?.rates || []);
      if (fcResp?.functional_currency) {
        setFunctionalCcy(fcResp.functional_currency);
      }
    } catch (exc) {
      toast?.(`Failed to load FX rates: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [api, toast]);

  useEffect(() => { load(); }, [load]);

  const onSave = useCallback(async (e) => {
    e?.preventDefault?.();
    const fromCcy = from.trim().toUpperCase();
    const toCcy = to.trim().toUpperCase();
    const rate = Number(rateValue);
    if (fromCcy.length !== 3 || toCcy.length !== 3) {
      toast?.('Currency codes must be 3 letters (e.g. USD, EUR, GBP).', 'error');
      return;
    }
    if (fromCcy === toCcy) {
      toast?.('From and To currencies cannot be the same.', 'error');
      return;
    }
    if (!Number.isFinite(rate) || rate <= 0) {
      toast?.('Rate must be a positive number.', 'error');
      return;
    }
    setSaving(true);
    try {
      await api('/api/workspace/fx-rates', {
        method: 'POST',
        body: JSON.stringify({
          from_currency: fromCcy,
          to_currency: toCcy,
          rate,
          as_of_date: asOfDate || null,
          source: 'manual',
        }),
      });
      toast?.(`Saved ${fromCcy}→${toCcy} @ ${rate}`, 'success');
      setFrom('');
      setTo('');
      setRateValue('');
      setAsOfDate('');
      await load();
    } catch (exc) {
      toast?.(`Save failed: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setSaving(false);
    }
  }, [api, from, to, rateValue, asOfDate, toast, load]);

  const onDelete = useCallback(async (rate) => {
    if (!window.confirm(
      `Delete the ${rate.from_currency}→${rate.to_currency} rate ` +
      `(${rate.rate}) effective ${rate.as_of_date}?`,
    )) return;
    try {
      await api(`/api/workspace/fx-rates/${rate.id}`, { method: 'DELETE' });
      toast?.('Rate deleted.', 'success');
      await load();
    } catch (exc) {
      toast?.(`Delete failed: ${String(exc?.message || exc)}`, 'error');
    }
  }, [api, toast, load]);

  // Module 9 spec line 298: rates pulled from ERP. The "Sync from
  // ERP" button hits /api/workspace/fx-rates/sync-from-erp which
  // dispatches per ERP type (QuickBooks shipped today; Xero /
  // NetSuite / SAP return not_supported with helpful copy).
  const [syncing, setSyncing] = useState(false);
  const onSyncFromErp = useCallback(async () => {
    setSyncing(true);
    try {
      const resp = await api('/api/workspace/fx-rates/sync-from-erp', { method: 'POST' });
      const status = resp?.status || 'unknown';
      if (status === 'ok' || status === 'partial') {
        toast?.(`Synced ${resp.rates_synced || 0} rates from ${resp.erp_type || 'ERP'}.`, 'success');
        await load();
      } else if (status === 'not_supported') {
        toast?.(resp?.message || 'FX sync not yet supported for this ERP — use manual entry.', 'info');
      } else if (status === 'no_currencies_needed') {
        toast?.('No non-functional-currency invoices need a rate right now.', 'info');
      } else if (status === 'no_erp_connected') {
        toast?.('Connect an ERP first; FX sync needs a source.', 'info');
      } else {
        toast?.(resp?.message || `Sync returned: ${status}`, 'info');
      }
    } catch (exc) {
      toast?.(`Sync failed: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setSyncing(false);
    }
  }, [api, load, toast]);

  return html`
    <div class="panel" ref=${panelRef}>
      <div class="panel-head">
        <div>
          <strong>FX rates</strong>
          <span class="muted">
            Functional currency: <code>${functionalCcy}</code>. Reports convert every
            invoice to this currency before aggregating across entities.
          </span>
        </div>
        <button class="btn-secondary btn-sm" onClick=${onSyncFromErp} disabled=${syncing}>
          ${syncing ? 'Syncing…' : 'Sync from ERP'}
        </button>
      </div>

      <form class="cl-settings-row" onSubmit=${onSave}>
        <label style="width:100px">
          <span class="muted" style="display:block;font-size:11px;text-transform:uppercase">From</span>
          <input
            type="text"
            placeholder="EUR"
            value=${from}
            onInput=${(e) => setFrom(e.target.value)}
            disabled=${saving}
            maxlength="3"
            style="width:100%;text-transform:uppercase"
          />
        </label>
        <label style="width:100px">
          <span class="muted" style="display:block;font-size:11px;text-transform:uppercase">To</span>
          <input
            type="text"
            placeholder=${functionalCcy || 'GBP'}
            value=${to}
            onInput=${(e) => setTo(e.target.value)}
            disabled=${saving}
            maxlength="3"
            style="width:100%;text-transform:uppercase"
          />
        </label>
        <label style="width:140px">
          <span class="muted" style="display:block;font-size:11px;text-transform:uppercase">Rate</span>
          <input
            type="number"
            step="0.0001"
            min="0"
            placeholder="1.10"
            value=${rateValue}
            onInput=${(e) => setRateValue(e.target.value)}
            disabled=${saving}
            style="width:100%"
          />
        </label>
        <label style="width:160px">
          <span class="muted" style="display:block;font-size:11px;text-transform:uppercase">Effective from</span>
          <input
            type="date"
            value=${asOfDate}
            onInput=${(e) => setAsOfDate(e.target.value)}
            disabled=${saving}
            style="width:100%"
          />
        </label>
        <button type="submit" class="btn btn-primary" disabled=${saving}>
          ${saving ? 'Saving…' : 'Save rate'}
        </button>
      </form>

      ${loading ? html`<p class="muted">Loading…</p>` : null}
      ${!loading && rates.length === 0 ? html`
        <p class="muted" style="padding:16px 0">
          No FX rates yet. Add one above for every currency pair you need.
          ${functionalCcy} → ${functionalCcy} doesn't need a rate.
        </p>
      ` : null}

      ${rates.length > 0 ? html`
        <table class="cl-settings-table">
          <thead>
            <tr>
              <th>From</th>
              <th>To</th>
              <th class="cl-record-num">Rate</th>
              <th>Effective from</th>
              <th>Source</th>
              <th>Note</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${rates.map((r) => html`
              <tr key=${r.id}>
                <td><code>${r.from_currency}</code></td>
                <td><code>${r.to_currency}</code></td>
                <td class="cl-record-num">${r.rate?.toFixed?.(4) ?? r.rate}</td>
                <td class="muted">${r.as_of_date}</td>
                <td>
                  <span class=${`cl-record-chip cl-record-chip-${r.source === 'manual' ? 'info' : 'success'}`}>
                    ${r.source}
                  </span>
                </td>
                <td class="muted">${r.note || '—'}</td>
                <td style="text-align:right">
                  <button class="btn btn-tertiary btn-sm" onClick=${() => onDelete(r)}>Delete</button>
                </td>
              </tr>
            `)}
          </tbody>
        </table>
      ` : null}
    </div>
  `;
}
