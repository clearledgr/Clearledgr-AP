import { useCallback, useEffect, useMemo, useState } from 'preact/hooks';
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
    // Two completion signals: (a) the structural one — actual ERP /
    // Slack / Teams / Gmail connection is live, or AP policy exists;
    // (b) the operator pressed "Mark done manually", which advances
    // onboarding.step server-side. Either signal flips the step to
    // 'done' so the badge clears immediately on manual ack.
    if (id === 1) {
      const ok = isConnected('erp', 'netsuite', 'sap', 'xero', 'quickbooks');
      if (ok || onboarding.step >= 1) return 'done';
      return 'next';
    }
    if (id === 2) {
      const settings = bootstrap?.organization?.settings || {};
      const has = !!(settings.ap_policy || settings.workflow_controls);
      if (has || onboarding.step >= 2) return 'done';
      return onboarding.step >= 1 ? 'next' : 'pending';
    }
    if (id === 3) {
      const ok = isConnected('slack', 'teams');
      if (ok || onboarding.step >= 3) return 'done';
      return onboarding.step >= 2 ? 'next' : 'pending';
    }
    if (id === 4) {
      if (isConnected('gmail') || onboarding.step >= 4) return 'done';
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

      <${SampleDataSection} />

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


// ─── Module 10 — Sample Data section ──────────────────────────────
//
// Lets the leader load a curated set of sample invoices and practice
// the workflow before going live with real data. Sample rows are
// tagged is_sample=true on the backend; production reads filter them
// out so they never contaminate live aggregates (spec §329).

function SampleDataSection() {
  const [count, setCount] = useState(0);
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [showItems, setShowItems] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const status = await api(
        '/api/workspace/onboarding/sample-data/status',
      );
      setCount((status && status.sample_count) || 0);
    } catch {
      // Non-fatal — leave count at the previous value.
    }
  }, []);

  const loadPreview = useCallback(async () => {
    try {
      const resp = await api(
        '/api/workspace/onboarding/sample-data/preview',
      );
      setItems((resp && resp.items) || []);
    } catch {
      setItems([]);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onLoad = useCallback(async () => {
    setBusy(true);
    try {
      const resp = await api(
        '/api/workspace/onboarding/sample-data/load',
        { method: 'POST' },
      );
      const loaded = (resp && resp.loaded) || 0;
      const already = (resp && resp.already_present) || 0;
      if (loaded > 0) {
        toast(`Loaded ${loaded} sample invoices.`, 'success');
      } else if (already > 0) {
        toast(`${already} sample invoices already loaded.`, 'info');
      }
      await load();
      if (showItems) await loadPreview();
    } catch (exc) {
      toast(`Load failed: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setBusy(false);
    }
  }, [load, loadPreview, showItems, toast]);

  const onClear = useCallback(async () => {
    if (!window.confirm(
      'Clear all sample invoices? Production data is unaffected. ' +
      'You can reload the sample set any time.',
    )) return;
    setBusy(true);
    try {
      const resp = await api(
        '/api/workspace/onboarding/sample-data/clear',
        { method: 'POST' },
      );
      const deleted = (resp && resp.deleted) || 0;
      toast(`Cleared ${deleted} sample invoices.`, 'success');
      await load();
      setItems([]);
    } catch (exc) {
      toast(`Clear failed: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setBusy(false);
    }
  }, [load, toast]);

  const togglePreview = useCallback(async () => {
    if (!showItems) {
      await loadPreview();
    }
    setShowItems((v) => !v);
  }, [loadPreview, showItems]);

  return html`
    <section class="cl-sample-section">
      <header class="cl-sample-head">
        <div>
          <h2>Practice with sample invoices</h2>
          <p class="cl-sample-sub">
            Run a set of realistic invoices through the system so you can
            see exceptions, approvals, and reports light up before going
            live with real data. Sample data is kept separate from production
            and never appears in your live reports.
          </p>
        </div>
        <div class="cl-sample-actions">
          ${count > 0
            ? html`
                <button class="cl-onb-btn cl-onb-btn-ghost"
                  onClick=${togglePreview} disabled=${busy}>
                  ${showItems ? 'Hide' : 'View'} (${count})
                </button>
                <button class="cl-onb-btn cl-onb-btn-ghost"
                  onClick=${onClear} disabled=${busy}>
                  Clear
                </button>
              `
            : html`
                <button class="cl-onb-btn cl-onb-btn-primary"
                  onClick=${onLoad} disabled=${busy}>
                  ${busy ? 'Loading…' : 'Load sample invoices'}
                </button>
              `}
        </div>
      </header>

      ${showItems && items.length > 0 ? html`
        <table class="cl-sample-table">
          <thead>
            <tr>
              <th>Vendor</th>
              <th>Invoice no.</th>
              <th class="cl-sample-num">Amount</th>
              <th>State</th>
              <th>Exception</th>
            </tr>
          </thead>
          <tbody>
            ${items.map((it) => html`
              <tr key=${it.id}>
                <td><strong>${it.vendor_name}</strong></td>
                <td><code>${it.invoice_number}</code></td>
                <td class="cl-sample-num">
                  ${it.currency} ${Number(it.amount).toLocaleString(undefined, {
                    minimumFractionDigits: 2, maximumFractionDigits: 2,
                  })}
                </td>
                <td>${(it.state || '').replace(/_/g, ' ')}</td>
                <td class="cl-sample-muted">${it.exception_code || '—'}</td>
              </tr>
            `)}
          </tbody>
        </table>
      ` : null}
    </section>
  `;
}
