import { useState, useMemo } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { html } from '../../utils/htm.js';
import { api } from '../../api/client.js';
import { useBootstrap, useBootstrapRefresh, useOrgId } from '../../shell/BootstrapContext.js';
import { useToast } from '../../shell/Toast.js';

/**
 * Onboarding wizard for new orgs (workstream C).
 *
 * Industry-standard ERP-first flow modelled on BILL.com / Ramp /
 * Stampli onboarding sequences:
 *   1. Connect ERP            (anchor — without this, no AP coordination)
 *   2. Set AP policy          (auto-approve threshold, match tolerances)
 *   3. Connect Slack/Teams    (approval surface)
 *   4. Install Gmail extension (optional intake — companion only)
 *
 * The wizard does NOT embed the OAuth flows itself — each step links
 * to the existing settings/connections page where the integration is
 * configured. After the user completes that flow elsewhere, they
 * return to /onboarding and the bootstrap refresh detects the
 * connected integration and advances `onboarding.step`. Decoupling
 * keeps each step's deep flow (e.g. NetSuite TBA token entry) in
 * one place rather than duplicating it inside the wizard.
 *
 * Steps surface:
 *   - Status pill: ✓ done / → next / ○ pending / ↶ skipped
 *   - "Set up" button → routes to /connections, /settings, etc.
 *   - "Mark done" button → POST /api/workspace/onboarding/step (admin
 *     can self-attest if integration state isn't auto-detected)
 *   - "Skip" on optional steps
 *
 * Exit conditions:
 *   - All required steps (1, 2, 3) complete → onboarding.completed=true
 *     → AuthGate stops redirecting here, user lands on / (PlanPage).
 *   - Admin clicks "Finish later" → marks current step persisted and
 *     drops the user at / for free exploration. Wizard remains in
 *     primary nav until completed.
 */
export function OnboardingPage() {
  const bootstrap = useBootstrap();
  const refreshBootstrap = useBootstrapRefresh();
  const orgId = useOrgId();
  const toast = useToast();
  const [, navigate] = useLocation();
  const [busy, setBusy] = useState(false);

  const onboarding = bootstrap?.onboarding || {};
  const integrations = bootstrap?.integrations || [];
  const integrationsByName = useMemo(() => {
    const map = {};
    for (const i of integrations) {
      const name = String(i?.name || '').toLowerCase();
      if (name) map[name] = i;
    }
    return map;
  }, [integrations]);

  const isConnected = (...names) => {
    for (const n of names) {
      const info = integrationsByName[n.toLowerCase()];
      if (!info) continue;
      if (info.connected) return true;
      const status = String(info.status || '').toLowerCase();
      if (['connected', 'active', 'ready'].includes(status)) return true;
    }
    return false;
  };

  const stepStatus = (id) => {
    if (id === 1) {
      const ok = isConnected('erp', 'netsuite', 'sap', 'xero', 'quickbooks');
      return ok ? 'done' : (onboarding.step >= 1 ? 'done' : 'next');
    }
    if (id === 2) {
      const settings = bootstrap?.organization?.settings || {};
      const has = !!(settings.ap_policy || settings.workflow_controls);
      if (has) return 'done';
      return onboarding.step >= 1 ? 'next' : 'pending';
    }
    if (id === 3) {
      const ok = isConnected('slack', 'teams');
      if (ok) return 'done';
      return onboarding.step >= 2 ? 'next' : 'pending';
    }
    if (id === 4) {
      if (isConnected('gmail')) return 'done';
      return onboarding.step >= 3 ? 'optional' : 'pending';
    }
    return 'pending';
  };

  const markStepDone = async (stepId) => {
    if (busy) return;
    setBusy(true);
    try {
      await api('/api/workspace/onboarding/step', {
        method: 'POST',
        body: { organization_id: orgId, step: stepId },
        retry: false,
      });
      await refreshBootstrap();
      toast(`Step ${stepId} marked complete.`, 'success');
    } catch (err) {
      toast(err?.message || 'Could not mark step complete.', 'error');
    } finally {
      setBusy(false);
    }
  };

  const finishLater = () => navigate('/');

  const completed = onboarding.completed === true;
  const steps = onboarding.steps || [];

  const STEP_DESTINATIONS = {
    1: '/connections',
    2: '/settings',
    3: '/connections',
    // Step 4 (Gmail extension) opens an external link instead — Chrome
    // Web Store. Handled inline below.
  };

  return html`
    <div class="cl-onb-shell">
      <header class="cl-onb-header">
        <div class="cl-onb-eyebrow">Workspace setup</div>
        <h1 class="cl-onb-title">${completed ? 'Setup complete.' : "Let's get Clearledgr ready."}</h1>
        <p class="cl-onb-sub">
          ${completed
            ? 'Every required integration is connected. You can revisit any step from this page; nothing here is destructive.'
            : 'Four steps; the last one is optional. Each step links to the page where you actually configure the integration — come back here when you\'re done.'}
        </p>
      </header>

      <ol class="cl-onb-steps">
        ${steps.map((step) => {
          const status = stepStatus(step.id);
          const destination = STEP_DESTINATIONS[step.id];
          return html`
            <li class=${`cl-onb-step cl-onb-step-${status}`} key=${step.id}>
              <div class="cl-onb-step-rail">
                <span class=${`cl-onb-step-pip cl-onb-step-pip-${status}`} aria-hidden="true">
                  ${status === 'done' ? '✓' : (status === 'next' ? '→' : (status === 'optional' ? '·' : '○'))}
                </span>
              </div>
              <div class="cl-onb-step-body">
                <div class="cl-onb-step-head">
                  <h2 class="cl-onb-step-name">
                    ${step.id}. ${step.name}
                    ${step.required === false ? html`<span class="cl-onb-step-tag">optional</span>` : null}
                  </h2>
                  <span class=${`cl-onb-step-status cl-onb-step-status-${status}`}>
                    ${status === 'done' ? 'Connected'
                      : status === 'next' ? 'Up next'
                      : status === 'optional' ? 'Optional'
                      : 'Pending'}
                  </span>
                </div>
                <p class="cl-onb-step-desc">${step.description}</p>
                <div class="cl-onb-step-actions">
                  ${step.id === 4
                    ? html`
                        <a
                          class="cl-onb-btn cl-onb-btn-primary"
                          href="https://chrome.google.com/webstore/category/extensions"
                          target="_blank"
                          rel="noopener noreferrer"
                        >Open Chrome Web Store ↗</a>
                      `
                    : html`
                        <button
                          class="cl-onb-btn cl-onb-btn-primary"
                          disabled=${busy}
                          onClick=${() => navigate(destination)}>
                          ${status === 'done' ? 'Re-configure' : 'Set up'}
                        </button>
                      `}
                  ${status !== 'done' && step.id !== 4
                    ? html`
                        <button
                          class="cl-onb-btn cl-onb-btn-ghost"
                          disabled=${busy}
                          onClick=${() => markStepDone(step.id)}>
                          Mark done manually
                        </button>
                      `
                    : null}
                </div>
                ${step.time_estimate
                  ? html`<div class="cl-onb-step-time">≈ ${step.time_estimate}</div>`
                  : null}
              </div>
            </li>
          `;
        })}
      </ol>

      <footer class="cl-onb-footer">
        ${completed
          ? html`
              <button class="cl-onb-btn cl-onb-btn-primary" onClick=${() => navigate('/')}>
                Open workspace
              </button>
            `
          : html`
              <button class="cl-onb-btn cl-onb-btn-ghost" onClick=${finishLater}>
                Finish later
              </button>
              <span class="cl-onb-footer-hint">
                Required steps (1–3) must be complete before bills auto-route.
              </span>
            `}
      </footer>
    </div>
  `;
}
