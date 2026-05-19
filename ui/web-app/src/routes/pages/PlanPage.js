import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { hasCapability, useAction } from '../route-helpers.js';

const html = htm.bind(h);

const PLAN_ORDER = ['free', 'starter', 'professional', 'enterprise'];
const PLAN_META = {
  free: {
    label: 'Free',
    monthly: 0,
    annual: 0,
    summary: 'Run the AP lane in one inbox and validate the workflow.',
    highlights: ['Gmail sidebar', 'Invoice extraction', 'One user'],
  },
  starter: {
    label: 'Starter',
    monthly: 79,
    annual: 65,
    summary: 'Handle day-to-day approvals and posting for a lean finance team.',
    highlights: ['Approval routing', 'ERP posting', 'Slack and Teams'],
  },
  professional: {
    label: 'Professional',
    monthly: 149,
    annual: 125,
    summary: 'Add stronger AI controls, analytics, and custom policy coverage.',
    highlights: ['Custom policies', 'Advanced analytics', 'Priority support'],
  },
  enterprise: {
    label: 'Enterprise',
    monthly: 299,
    annual: 249,
    summary: 'Unlock procurement-grade controls and enterprise admin features.',
    highlights: ['SSO', 'Data residency', 'Unlimited workspace scale'],
  },
};

const USAGE_FIELDS = [
  { key: 'invoices_this_month', label: 'Invoices this month', limitKey: 'invoices_per_month', type: 'count' },
  { key: 'users_count', label: 'Users', limitKey: 'users', type: 'count' },
  { key: 'vendors_count', label: 'Vendors', limitKey: 'vendors', type: 'count' },
  { key: 'erp_connections', label: 'ERP connections', limitKey: 'erp_connections', type: 'count' },
  { key: 'ai_credits_this_month', label: 'AI credits', limitKey: 'ai_credits_per_month', type: 'count' },
  { key: 'api_calls_today', label: 'API calls today', limitKey: 'api_calls_per_day', type: 'count' },
  { key: 'storage_used_gb', label: 'Storage used', limitKey: 'storage_gb', type: 'storage' },
];

const FEATURE_GROUPS = [
  {
    title: 'Workflow',
    keys: ['gmail_sidebar', 'invoice_extraction', 'approval_routing', 'erp_posting', 'approval_chains', 'custom_workflows'],
  },
  {
    title: 'Intelligence',
    keys: ['ai_categorization', 'gl_auto_coding', 'vendor_intelligence', 'recurring_detection', 'three_way_matching', 'advanced_analytics'],
  },
  {
    title: 'Admin and integrations',
    keys: ['slack_integration', 'teams_integration', 'api_access', 'audit_logs', 'priority_support', 'sso', 'data_residency'],
  },
];

function toTitleCase(value) {
  return String(value || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function formatPlanLabel(plan) {
  return PLAN_META[plan]?.label || toTitleCase(plan || 'free');
}

function formatStatus(status) {
  const normalized = String(status || 'active').trim().toLowerCase();
  if (normalized === 'trialing') return 'Trialing';
  if (normalized === 'past_due') return 'Past due';
  if (normalized === 'cancelled') return 'Cancelled';
  return 'Active';
}

function statusTone(status) {
  const normalized = String(status || 'active').trim().toLowerCase();
  if (normalized === 'trialing') return 'warning';
  if (normalized === 'past_due' || normalized === 'cancelled') return 'danger';
  return 'connected';
}

function formatPrice(plan, cycle = 'monthly') {
  const meta = PLAN_META[plan] || PLAN_META.free;
  const normalizedCycle = String(cycle || 'monthly').trim().toLowerCase();
  const amount = normalizedCycle === 'yearly' ? meta.annual : meta.monthly;
  if (!amount) return 'Free';
  if (normalizedCycle === 'yearly') return `$${amount}/seat/mo billed annually`;
  return `$${amount}/seat/mo`;
}

function formatDateLabel(value) {
  if (!value) return 'Not set';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Not set';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatUsageValue(value, type = 'count') {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric)) return '0';
  if (type === 'storage') {
    return `${numeric.toLocaleString(undefined, {
      minimumFractionDigits: numeric < 10 ? 1 : 0,
      maximumFractionDigits: 1,
    })} GB`;
  }
  return numeric.toLocaleString();
}

function formatLimitValue(value, type = 'count') {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < 0) return 'Unlimited';
  if (type === 'storage') {
    return `${numeric.toLocaleString(undefined, {
      minimumFractionDigits: numeric < 10 ? 1 : 0,
      maximumFractionDigits: 1,
    })} GB`;
  }
  return numeric.toLocaleString();
}

function calculateUsagePercent(usageValue, limitValue) {
  const usage = Number(usageValue || 0);
  const limit = Number(limitValue);
  if (!Number.isFinite(usage) || !Number.isFinite(limit) || limit <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((usage / limit) * 100)));
}

function usageTone(percent) {
  if (percent >= 90) return 'danger';
  if (percent >= 70) return 'warning';
  return 'success';
}

function featureLabel(key) {
  const value = toTitleCase(key)
    .replace(/\bAi\b/g, 'AI')
    .replace(/\bApi\b/g, 'API')
    .replace(/\bErp\b/g, 'ERP')
    .replace(/\bSso\b/g, 'SSO')
    .replace(/\bGl\b/g, 'GL');
  return value;
}

function getPlanAction(planId, sub) {
  const currentPlan = String(sub?.plan || 'free').trim().toLowerCase();
  const status = String(sub?.status || 'active').trim().toLowerCase();
  const hasTrialHistory = Boolean(sub?.trial_started_at);

  if (currentPlan === planId && status !== 'trialing') {
    return { label: 'Current plan', value: planId, disabled: true };
  }
  if (planId === 'professional' && !hasTrialHistory && status !== 'trialing') {
    return { label: 'Start 14-day trial', value: 'trial', disabled: false };
  }
  return { label: `Switch to ${formatPlanLabel(planId)}`, value: planId, disabled: false };
}

function PlanFeatureGroup({ title, keys, features }) {
  const enabledKeys = keys.filter((key) => Boolean(features?.[key]));
  return html`<div class="billing-feature-card">
    <div class="billing-feature-title">${title}</div>
    ${enabledKeys.length
      ? html`<div class="billing-chip-row">
          ${enabledKeys.map((key) => html`<span key=${key} class="secondary-chip">${featureLabel(key)}</span>`)}
        </div>`
      : html`<div class="secondary-empty">No additional entitlements in this group on the current plan.</div>`}
  </div>`;
}

export default function PlanPage({ bootstrap, api, toast, orgId, onRefresh, navigate }) {
  const sub = bootstrap?.subscription || {};
  const limits = sub.limits || {};
  const usage = sub.usage || {};
  const features = sub.features || {};
  const canManagePlan = hasCapability(bootstrap, 'manage_plan');
  const plan = String(sub.plan || 'free').trim().toLowerCase();
  const status = String(sub.status || 'active').trim().toLowerCase();
  const planLabel = formatPlanLabel(plan);
  const periodLabel = sub.current_period_end
    ? `Renews ${formatDateLabel(sub.current_period_end)}`
    : (status === 'trialing' && sub.trial_ends_at ? `Trial ends ${formatDateLabel(sub.trial_ends_at)}` : 'Billing period not started');

  const [changePlan, changingPlan] = useAction(async (nextPlan) => {
    if (!canManagePlan) return;
    await api('/api/workspace/subscription/plan', {
      method: 'PATCH',
      body: { organization_id: orgId, plan: nextPlan },
    });
    toast?.(
      nextPlan === 'trial'
        ? 'Professional trial started.'
        : `Plan updated to ${formatPlanLabel(nextPlan)}.`,
      'success',
    );
    onRefresh?.();
  });

  // Module 11 — Paddle billing surface. Pull state on mount + when
  // plan changes; render Subscribe / Manage billing / Switch to
  // invoicing CTAs based on what's actually wired Paddle-side.
  const [billingState, setBillingState] = useState(null);
  useEffect(() => {
    let cancelled = false;
    api('/api/workspace/billing/invoices', { silent: true })
      .then((r) => { if (!cancelled) setBillingState(r); })
      .catch(() => { if (!cancelled) setBillingState({ configured: false, invoices: [] }); });
    return () => { cancelled = true; };
  }, [api, plan]);

  const [startCheckout, checkingOut] = useAction(async (chosenPlan, mode) => {
    if (!canManagePlan) return;
    const resp = await api('/api/workspace/billing/checkout', {
      method: 'POST',
      body: {
        plan: chosenPlan,
        collection_mode: mode || 'card',
        return_url: `${window.location.origin}/plan?paddle_return=1`,
      },
    });
    if (resp?.checkout_url) {
      window.location.href = resp.checkout_url;
    }
  });

  const [openPortal, openingPortal] = useAction(async () => {
    if (!canManagePlan) return;
    try {
      const resp = await api('/api/workspace/billing/portal');
      if (resp?.portal_url) window.location.href = resp.portal_url;
    } catch (exc) {
      toast?.(`Portal unavailable: ${exc?.message || exc}`, 'error');
    }
  });

  const [flipMode, flippingMode] = useAction(async (mode) => {
    if (!canManagePlan) return;
    await api('/api/workspace/billing/collection-mode', {
      method: 'PATCH',
      body: { mode },
    });
    toast?.(
      mode === 'invoice'
        ? 'Switched to invoicing. Next renewal will be issued as a paid invoice with bank wire details.'
        : 'Switched to card billing. Card on file will auto-charge on next renewal.',
      'success',
    );
    setBillingState((prev) => prev ? { ...prev, billing_collection_mode: mode } : prev);
  });

  return html`
    <div class=${`secondary-banner billing-hero ${canManagePlan ? '' : 'warning'}`}>
      <div class="secondary-banner-copy">
        <div class="billing-eyebrow">Subscription and billing</div>
        <h3>${planLabel} plan</h3>
        <p class="muted">
          ${canManagePlan
            ? 'Manage workspace entitlement, track usage against limits, and change the plan from the workspace.'
            : 'Review workspace entitlement and usage here. Only admins can change the plan.'}
        </p>
        <div class="billing-hero-meta">
          <span class=${`status-badge ${statusTone(status)}`}>${formatStatus(status)}</span>
          <span class="secondary-chip">${formatPrice(plan, sub.billing_cycle)}</span>
          <span class="secondary-chip">${String(sub.billing_cycle || 'monthly').toLowerCase() === 'yearly' ? 'Annual billing' : 'Monthly billing'}</span>
          ${status === 'trialing'
            ? html`<span class="secondary-chip">${sub.trial_days_remaining || 0} trial day${Number(sub.trial_days_remaining || 0) === 1 ? '' : 's'} left</span>`
            : null}
        </div>
      </div>
      <div class="secondary-banner-actions">
        ${navigate
          ? html`<button class="btn-secondary" onClick=${() => navigate('/settings')}>Open settings</button>`
          : null}
        ${canManagePlan && !sub.trial_started_at && status !== 'trialing'
          ? html`<button class="btn-primary" onClick=${() => changePlan('trial')} disabled=${changingPlan}>
              ${changingPlan ? 'Working…' : 'Start Pro trial'}
            </button>`
          : null}
      </div>
    </div>

    <div class="billing-shell">
      <div class="billing-main-stack">
        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin-top:0">Billing snapshot</h3>
              <p class="muted" style="margin:0">The current plan state, renewal window, and workspace limits in one place.</p>
            </div>
          </div>
          <div class="billing-summary-grid">
            <div class="billing-summary-card">
              <strong>Current plan</strong>
              <span>${planLabel}</span>
              <small>${formatPrice(plan, sub.billing_cycle)}</small>
            </div>
            <div class="billing-summary-card">
              <strong>Status</strong>
              <span>${formatStatus(status)}</span>
              <small>${periodLabel}</small>
            </div>
            <div class="billing-summary-card">
              <strong>Billing cycle</strong>
              <span>${String(sub.billing_cycle || 'monthly').toLowerCase() === 'yearly' ? 'Annual' : 'Monthly'}</span>
              <small>${String(sub.billing_cycle || 'monthly').toLowerCase() === 'yearly' ? 'Billed on a yearly term' : 'Billed monthly'}</small>
            </div>
            <div class="billing-summary-card">
              <strong>AI credits</strong>
              <span>${formatUsageValue(usage.ai_credits_this_month)}</span>
              <small>${formatLimitValue(limits.ai_credits_per_month)} available this month</small>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin-top:0">Usage against plan limits</h3>
              <p class="muted" style="margin:0">How the workspace is tracking against the current month’s entitlement.</p>
            </div>
          </div>
          <div class="billing-usage-list">
            ${USAGE_FIELDS.map((field) => {
              const usageValue = usage[field.key] ?? 0;
              const limitValue = limits[field.limitKey];
              const percent = calculateUsagePercent(usageValue, limitValue);
              const unlimited = !Number.isFinite(Number(limitValue)) || Number(limitValue) < 0;
              return html`<div key=${field.key} class="billing-usage-row">
                <div class="billing-usage-copy">
                  <div class="billing-usage-header">
                    <strong>${field.label}</strong>
                    <span>${formatUsageValue(usageValue, field.type)} / ${formatLimitValue(limitValue, field.type)}</span>
                  </div>
                  <div class="billing-usage-bar">
                    <div
                      class=${`billing-usage-fill ${usageTone(percent)}`}
                      style=${`width:${unlimited ? '24' : Math.max(percent, percent ? 8 : 0)}%`}
                    ></div>
                  </div>
                  <div class="muted billing-usage-note">
                    ${unlimited ? 'This metric is unlimited on the current plan.' : `${percent}% of this plan limit used.`}
                  </div>
                </div>
              </div>`;
            })}
          </div>
        </div>

        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin-top:0">Included right now</h3>
              <p class="muted" style="margin:0">The finance capabilities turned on for this workspace under the current plan.</p>
            </div>
          </div>
          <div class="billing-feature-grid">
            ${FEATURE_GROUPS.map((group) => html`
              <${PlanFeatureGroup}
                key=${group.title}
                title=${group.title}
                keys=${group.keys}
                features=${features}
              />
            `)}
          </div>
        </div>
      </div>

      <div class="billing-side-stack">
        <div class="panel">
          <div class="panel-head compact">
            <div>
              <h3 style="margin-top:0">Choose a plan</h3>
              <p class="muted" style="margin:0">Pick the workspace tier that matches invoice volume, controls, and support needs.</p>
            </div>
          </div>
          <div class="billing-plan-list">
            ${PLAN_ORDER.map((planId) => {
              const meta = PLAN_META[planId];
              const action = getPlanAction(planId, sub);
              const isCurrent = planId === plan;
              return html`<div key=${planId} class=${`billing-plan-option ${isCurrent ? 'is-current' : ''}`}>
                <div class="billing-plan-row">
                  <div class="billing-plan-copy">
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
                      <strong>${meta.label}</strong>
                      ${isCurrent ? html`<span class="status-badge connected">Current</span>` : null}
                    </div>
                    <div class="billing-plan-price">${formatPrice(planId, sub.billing_cycle)}</div>
                    <p>${meta.summary}</p>
                    <div class="billing-chip-row">
                      ${meta.highlights.map((highlight) => html`<span key=${highlight} class="secondary-chip">${highlight}</span>`)}
                    </div>
                  </div>
                  ${canManagePlan
                    ? html`<div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">
                        <button
                          class=${isCurrent ? 'btn-secondary btn-sm' : 'btn-primary btn-sm'}
                          onClick=${() => changePlan(action.value)}
                          disabled=${action.disabled || changingPlan}>
                          ${changingPlan ? 'Working…' : action.label}
                        </button>
                        ${(billingState?.configured && !isCurrent && action.value !== 'free') ? html`
                          <button
                            class="btn-tertiary btn-sm"
                            onClick=${() => startCheckout(planId, 'card')}
                            disabled=${checkingOut}
                            title="Pay with card via Paddle">
                            ${checkingOut ? '…' : 'Subscribe with card'}
                          </button>
                          <button
                            class="btn-tertiary btn-sm"
                            onClick=${() => startCheckout(planId, 'invoice')}
                            disabled=${checkingOut}
                            title="Issue an invoice instead — net-30 wire payment">
                            ${checkingOut ? '…' : 'Subscribe with invoice'}
                          </button>
                        ` : null}
                      </div>`
                    : null}
                </div>
              </div>`;
            })}
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">What changes when you switch</h3>
          <div class="secondary-note">
            Plan changes update workspace entitlement immediately for Solden features, limits, and approval tooling. Billing cadence stays ${String(sub.billing_cycle || 'monthly').toLowerCase() === 'yearly' ? 'annual' : 'monthly'} unless your team changes it from the billing portal.
          </div>
        </div>

        ${billingState?.configured ? html`
          <div class="panel">
            <div class="panel-head compact">
              <div>
                <h3 style="margin-top:0">Billing & invoices</h3>
                <p class="muted" style="margin:0">
                  ${billingState.billing_collection_mode === 'invoice'
                    ? 'Currently invoiced — Paddle issues an invoice with bank wire details on each renewal.'
                    : 'Currently auto-charged on a card on file via Paddle.'}
                  ${billingState.next_billed_at ? html` · Next renewal ${formatDateLabel(billingState.next_billed_at)}.` : null}
                </p>
              </div>
              ${canManagePlan ? html`<div style="display:flex;gap:8px;flex-wrap:wrap">
                <button class="btn-secondary btn-sm" onClick=${openPortal} disabled=${openingPortal}>
                  ${openingPortal ? '…' : 'Manage in Paddle portal'}
                </button>
                ${billingState.billing_collection_mode === 'card' ? html`
                  <button class="btn-tertiary btn-sm"
                    onClick=${() => flipMode('invoice')}
                    disabled=${flippingMode}
                    title="Switch to issued invoices with net-30 wire terms (good for AP departments that don't authorise cards).">
                    ${flippingMode ? '…' : 'Switch to invoicing'}
                  </button>
                ` : html`
                  <button class="btn-tertiary btn-sm"
                    onClick=${() => flipMode('card')}
                    disabled=${flippingMode}>
                    ${flippingMode ? '…' : 'Switch to card'}
                  </button>
                `}
              </div>` : null}
            </div>
            ${(billingState.invoices || []).length > 0 ? html`
              <table class="cl-settings-table" style="margin-top:8px">
                <thead>
                  <tr>
                    <th>Invoice</th>
                    <th>Date</th>
                    <th>Amount</th>
                    <th>Status</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  ${billingState.invoices.map((inv) => html`
                    <tr key=${inv.id}>
                      <td>${inv.invoice_number || inv.id}</td>
                      <td class="muted">${inv.billed_at ? formatDateLabel(inv.billed_at) : '—'}</td>
                      <td>${inv.currency || ''} ${inv.amount || '—'}</td>
                      <td><span class="status-badge ${String(inv.status || '').toLowerCase() === 'paid' ? 'connected' : ''}">${inv.status || '—'}</span></td>
                      <td style="text-align:right">
                        ${inv.invoice_pdf_url ? html`<a class="btn-tertiary btn-sm" href=${inv.invoice_pdf_url} target="_blank" rel="noreferrer">PDF</a>` : null}
                      </td>
                    </tr>`)}
                </tbody>
              </table>
            ` : html`<p class="muted" style="margin:8px 0 0">No invoices yet. The first one is generated on your next renewal.</p>`}
          </div>
        ` : null}
      </div>
    </div>
  `;
}
