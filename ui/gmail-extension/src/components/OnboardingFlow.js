/**
 * Streak-style Onboarding Flow — DESIGN_THESIS.md §15
 *
 * Renders as a modal overlay on Gmail (like Streak's first-install modal).
 *
 * DESIGN_THESIS.md §15: "five steps, all happening inside Gmail,
 * completed in one sitting." Step 0 (install) happens before the
 * modal appears; steps 1-4 run through this flow:
 *
 *   Auth → Create Workspace → ERP → Policy → Slack → Pipeline creation → Done
 *     step 0       step 1      step 2   step 3   step 4
 *
 *   - auth: Google signin (part of step 0 handoff)
 *   - workspace: §15 step 1 (name, AP inbox, timezone — workspace
 *     identity before integrations)
 *   - erp: §15 step 2 (OAuth handoff; structured errors on failure
 *     name the missing permission + link to remediation)
 *   - policy: §15 step 3 (auto-approve threshold, match tolerance,
 *     default approver — the three values the thesis calls out)
 *   - slack: §15 step 4 (OAuth handoff; skippable — channel
 *     selection happens in Settings after connect)
 *   - creating / done: post-step-4 pipeline materialisation +
 *     "your agent is live" handoff
 */
import { h, Component } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

const LOGO_URL = typeof chrome !== 'undefined' && chrome.runtime
  ? chrome.runtime.getURL('icons/icon48.png')
  : '';

// ==================== STEP 1: AUTH MODAL ====================

function AuthModal({ onSignIn, pending, onDismiss }) {
  return html`
    <div class="cl-onboard-overlay">
      <div class="cl-onboard-modal">
        <div style="text-align:center;margin-bottom:20px;">
          ${LOGO_URL ? html`<img src=${LOGO_URL} alt="" style="width:48px;height:48px;margin-bottom:12px;" />` : ''}
          <h2 style="font:700 20px/1.3 'Instrument Sans','DM Sans',sans-serif;color:#0A1628;margin:0 0 8px;">Clearledgr AP</h2>
          <p style="font:400 14px/1.5 'DM Sans',sans-serif;color:#475569;margin:0;max-width:320px;">
            Clearledgr helps your finance team process invoices inside Gmail.
            Use your Google account to start.
          </p>
        </div>
        <button
          class="cl-onboard-google-btn"
          onClick=${onSignIn}
          disabled=${pending}
        >
          <svg width="18" height="18" viewBox="0 0 18 18" style="margin-right:10px;flex-shrink:0;">
            <path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615Z"/>
            <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.26c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18Z"/>
            <path fill="#FBBC05" d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.997 8.997 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332Z"/>
            <path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58Z"/>
          </svg>
          ${pending ? 'Connecting...' : 'Sign in with Google'}
        </button>
        <button
          type="button"
          onClick=${onDismiss}
          style="display:block;margin:16px auto 0;padding:0;border:0;background:transparent;cursor:pointer;font:400 12px/1.4 'DM Sans',sans-serif;color:#94A3B8;text-align:center;text-decoration:underline;"
        >
          Don't use Clearledgr on this account
        </button>
      </div>
    </div>
  `;
}

// ==================== STEP 2: CREATE WORKSPACE ====================

function CreateWorkspace({ onContinue, pending, errorMessage, defaultName }) {
  const [name, setName] = useState(defaultName || '');
  const trimmed = name.trim();

  return html`
    <div class="cl-onboard-overlay">
      <div class="cl-onboard-modal" style="max-width:440px;">
        <div style="text-align:center;margin-bottom:20px;">
          ${LOGO_URL ? html`<img src=${LOGO_URL} alt="" style="width:36px;height:36px;margin-bottom:8px;" />` : ''}
          <h2 style="font:700 18px/1.3 'Instrument Sans','DM Sans',sans-serif;color:#0A1628;margin:0 0 6px;">Name your workspace</h2>
          <p style="font:400 13px/1.4 'DM Sans',sans-serif;color:#94A3B8;margin:0;">
            One workspace per finance team. Use your company name — you can change it later.
          </p>
        </div>
        ${errorMessage ? html`
          <div style="
            background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;
            padding:10px 12px;margin-bottom:16px;
            font:400 12px/1.4 'DM Sans',sans-serif;color:#991B1B;
          ">${errorMessage}</div>
        ` : ''}
        <label style="display:block;font:500 12px/1.4 'DM Sans',sans-serif;color:#475569;margin-bottom:6px;">
          Workspace name
        </label>
        <input
          type="text"
          value=${name}
          onInput=${(e) => setName(e.target.value)}
          placeholder="Acme Finance"
          autofocus
          onKeyDown=${(e) => {
            if (e.key === 'Enter' && trimmed && !pending) {
              onContinue(trimmed);
            }
          }}
          style="
            display:block;width:100%;padding:10px 12px;border:1px solid #E2E8F0;border-radius:8px;
            font:400 14px/1.4 'DM Sans',sans-serif;color:#0A1628;box-sizing:border-box;margin-bottom:20px;
          "
        />
        <button
          class="cl-onboard-primary-btn"
          onClick=${() => trimmed && onContinue(trimmed)}
          disabled=${!trimmed || pending}
        >
          ${pending ? 'Creating...' : 'Continue'}
        </button>
      </div>
    </div>
  `;
}

// ==================== STEP 3: ERP PICKER ====================

function ErpPicker({ onSelect, pending, errorMessage }) {
  const [selected, setSelected] = useState('');

  const erps = [
    { id: 'quickbooks', name: 'QuickBooks', icon: 'QB' },
    { id: 'xero', name: 'Xero', icon: 'XR' },
    { id: 'netsuite', name: 'NetSuite', icon: 'NS' },
    { id: 'sap', name: 'SAP', icon: 'SP' },
  ];

  return html`
    <div class="cl-onboard-overlay">
      <div class="cl-onboard-modal" style="max-width:440px;">
        <div style="text-align:center;margin-bottom:20px;">
          ${LOGO_URL ? html`<img src=${LOGO_URL} alt="" style="width:36px;height:36px;margin-bottom:8px;" />` : ''}
          <h2 style="font:700 18px/1.3 'Instrument Sans','DM Sans',sans-serif;color:#0A1628;margin:0 0 6px;">Which accounting system do you use?</h2>
          <p style="font:400 13px/1.4 'DM Sans',sans-serif;color:#94A3B8;margin:0;">
            Clearledgr connects to your ERP to read POs, GRNs, and vendor master data.
          </p>
        </div>
        ${errorMessage ? html`
          <div style="
            background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;
            padding:10px 12px;margin-bottom:16px;
            font:400 12px/1.4 'DM Sans',sans-serif;color:#991B1B;
          ">${errorMessage}</div>
        ` : ''}
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px;">
          ${erps.map((erp) => html`
            <button
              key=${erp.id}
              onClick=${() => setSelected(erp.id)}
              style="
                padding:16px 8px;border-radius:8px;border:2px solid ${selected === erp.id ? '#00D67E' : '#E2E8F0'};
                background:${selected === erp.id ? '#ECFDF5' : '#fff'};cursor:pointer;text-align:center;
              "
            >
              <div style="font:700 16px/1 'Geist Mono',monospace;color:#0A1628;margin-bottom:4px;">${erp.icon}</div>
              <div style="font:500 12px/1 'DM Sans',sans-serif;color:#475569;">${erp.name}</div>
            </button>
          `)}
        </div>
        <button
          class="cl-onboard-primary-btn"
          onClick=${() => selected && onSelect(selected)}
          disabled=${!selected || pending}
        >
          ${pending ? 'Connecting...' : (errorMessage ? 'Try again' : 'Connect')}
        </button>
      </div>
    </div>
  `;
}

// ==================== STEP 4: AP POLICY CONFIGURATION ====================

function PolicyForm({ onContinue, pending, errorMessage }) {
  const [threshold, setThreshold] = useState('1000');
  const [tolerance, setTolerance] = useState('2');
  const [approverEmail, setApproverEmail] = useState('');

  // §15 "The AP Manager sets three values inside Gmail: auto-approve
  // threshold, match tolerance, and approval routing." These three
  // inputs are the minimum required to mark step 3 complete. More
  // advanced policy edits (per-tier routing, escalation rules) are
  // reachable later in Settings; onboarding keeps it to the three
  // the thesis names.
  const canContinue = Boolean(
    threshold && !Number.isNaN(Number(threshold))
    && tolerance && !Number.isNaN(Number(tolerance))
    && approverEmail.trim()
  );

  return html`
    <div class="cl-onboard-overlay">
      <div class="cl-onboard-modal" style="max-width:480px;">
        <div style="text-align:center;margin-bottom:20px;">
          ${LOGO_URL ? html`<img src=${LOGO_URL} alt="" style="width:36px;height:36px;margin-bottom:8px;" />` : ''}
          <h2 style="font:700 18px/1.3 'Instrument Sans','DM Sans',sans-serif;color:#0A1628;margin:0 0 6px;">Set your AP policy</h2>
          <p style="font:400 13px/1.4 'DM Sans',sans-serif;color:#94A3B8;margin:0;">
            Three defaults you can fine-tune later from Settings > Policy.
          </p>
        </div>
        ${errorMessage ? html`
          <div style="
            background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;
            padding:10px 12px;margin-bottom:16px;
            font:400 12px/1.4 'DM Sans',sans-serif;color:#991B1B;
          ">${errorMessage}</div>
        ` : ''}

        <label style="display:block;font:500 12px/1.4 'DM Sans',sans-serif;color:#475569;margin-bottom:6px;">
          Auto-approve threshold (matched invoices under this go through without a human)
        </label>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">
          <span style="font:600 14px/1 'Geist Mono',monospace;color:#0A1628;">£</span>
          <input
            type="number"
            value=${threshold}
            onInput=${(e) => setThreshold(e.target.value)}
            min="0"
            style="flex:1;padding:10px 12px;border:1px solid #E2E8F0;border-radius:8px;font:500 14px/1.2 'Geist Mono',monospace;color:#0A1628;box-sizing:border-box;"
          />
        </div>

        <label style="display:block;font:500 12px/1.4 'DM Sans',sans-serif;color:#475569;margin-bottom:6px;">
          Match tolerance (% delta between invoice and GRN before flagging)
        </label>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">
          <input
            type="number"
            value=${tolerance}
            onInput=${(e) => setTolerance(e.target.value)}
            min="0"
            step="0.1"
            style="flex:1;padding:10px 12px;border:1px solid #E2E8F0;border-radius:8px;font:500 14px/1.2 'Geist Mono',monospace;color:#0A1628;box-sizing:border-box;"
          />
          <span style="font:600 14px/1 'Geist Mono',monospace;color:#0A1628;">%</span>
        </div>

        <label style="display:block;font:500 12px/1.4 'DM Sans',sans-serif;color:#475569;margin-bottom:6px;">
          Default approver (receives Slack notification for everything above the threshold)
        </label>
        <input
          type="email"
          value=${approverEmail}
          onInput=${(e) => setApproverEmail(e.target.value)}
          placeholder="sarah@acme.com"
          style="
            display:block;width:100%;padding:10px 12px;border:1px solid #E2E8F0;border-radius:8px;
            font:400 14px/1.4 'DM Sans',sans-serif;color:#0A1628;box-sizing:border-box;margin-bottom:20px;
          "
        />

        <button
          class="cl-onboard-primary-btn"
          onClick=${() => canContinue && onContinue({
            auto_approve_threshold: Number(threshold),
            match_tolerance: Number(tolerance) / 100,
            approval_routing: { default: approverEmail.trim() },
          })}
          disabled=${!canContinue || pending}
        >
          ${pending ? 'Saving...' : 'Continue'}
        </button>
      </div>
    </div>
  `;
}

// ==================== STEP 5: SLACK CONNECTION ====================

function SlackConnect({ onConnect, onSkip, pending, errorMessage, connected }) {
  // §15 "OAuth connection to the team's Slack workspace. AP Manager
  // selects which channel receives agent notifications." The onboard
  // modal only initiates the OAuth handoff — channel selection
  // happens in Settings after connection completes because the
  // channel list isn't available until the install callback fires.
  return html`
    <div class="cl-onboard-overlay">
      <div class="cl-onboard-modal" style="max-width:440px;">
        <div style="text-align:center;margin-bottom:20px;">
          ${LOGO_URL ? html`<img src=${LOGO_URL} alt="" style="width:36px;height:36px;margin-bottom:8px;" />` : ''}
          <h2 style="font:700 18px/1.3 'Instrument Sans','DM Sans',sans-serif;color:#0A1628;margin:0 0 6px;">Connect Slack</h2>
          <p style="font:400 13px/1.4 'DM Sans',sans-serif;color:#94A3B8;margin:0;">
            Approvals and escalations happen in Slack. The AP pipeline stays in Gmail.
          </p>
        </div>
        ${errorMessage ? html`
          <div style="
            background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;
            padding:10px 12px;margin-bottom:16px;
            font:400 12px/1.4 'DM Sans',sans-serif;color:#991B1B;
          ">${errorMessage}</div>
        ` : ''}
        ${connected ? html`
          <div style="
            background:#ECFDF5;border:1px solid #A7F3D0;border-radius:8px;
            padding:10px 12px;margin-bottom:16px;
            font:500 13px/1.4 'DM Sans',sans-serif;color:#065F46;
          ">✓ Slack connected. You can change the channel anytime from Settings > Connections.</div>
        ` : ''}
        ${!connected ? html`
          <button
            class="cl-onboard-primary-btn"
            onClick=${onConnect}
            disabled=${pending}
            style="margin-bottom:12px;"
          >
            ${pending ? 'Opening Slack...' : 'Connect Slack workspace'}
          </button>
          <button
            type="button"
            onClick=${onSkip}
            style="display:block;width:100%;padding:10px 12px;border:1px solid #E2E8F0;border-radius:8px;background:#fff;color:#475569;font:500 13px/1 'DM Sans',sans-serif;cursor:pointer;"
          >
            Skip for now
          </button>
          <p style="font:400 11px/1.4 'DM Sans',sans-serif;color:#94A3B8;text-align:center;margin:12px 0 0;">
            If you skip, approvals route to email until Slack is connected. You can set it up anytime from Settings.
          </p>
        ` : html`
          <button
            class="cl-onboard-primary-btn"
            onClick=${onSkip}
          >
            Continue
          </button>
        `}
      </div>
    </div>
  `;
}

// ==================== STEP 6: PIPELINE CREATION PROGRESS ====================

function PipelineCreation({ erpType, onComplete }) {
  const [steps, setSteps] = useState([
    { id: 'connect', label: 'Connecting to ' + (erpType || 'ERP'), detail: 'Establishing OAuth connection', done: false },
    { id: 'vendors', label: 'Importing vendor master', detail: 'Reading vendor records from your ERP', done: false },
    { id: 'pipeline', label: 'Creating AP pipeline', detail: 'Setting up invoice stages and columns', done: false },
    { id: 'policies', label: 'Configuring default policies', detail: 'Auto-approve threshold and match tolerance', done: false },
  ]);

  useEffect(() => {
    // Simulate progress with real-ish timing
    const timers = [
      setTimeout(() => setSteps((s) => s.map((st, i) => i === 0 ? { ...st, done: true } : st)), 2000),
      setTimeout(() => setSteps((s) => s.map((st, i) => i <= 1 ? { ...st, done: true } : st)), 4000),
      setTimeout(() => setSteps((s) => s.map((st, i) => i <= 2 ? { ...st, done: true } : st)), 5500),
      setTimeout(() => setSteps((s) => s.map((st, i) => ({ ...st, done: true }))), 7000),
      setTimeout(() => onComplete && onComplete(), 8000),
    ];
    return () => timers.forEach(clearTimeout);
  }, []);

  return html`
    <div class="cl-onboard-overlay">
      <div class="cl-onboard-modal" style="max-width:480px;">
        <div style="display:flex;gap:24px;">
          <div style="flex:1;">
            <h2 style="font:700 18px/1.3 'Instrument Sans','DM Sans',sans-serif;color:#0A1628;margin:0 0 16px;">
              Setting up your AP workspace...
            </h2>
            <div style="display:flex;flex-direction:column;gap:14px;">
              ${steps.map((step) => html`
                <div key=${step.id} style="display:flex;gap:10px;align-items:flex-start;">
                  <div style="
                    width:20px;height:20px;border-radius:50%;flex-shrink:0;margin-top:1px;
                    display:flex;align-items:center;justify-content:center;font-size:11px;
                    ${step.done
                      ? 'background:#00D67E;color:#fff;'
                      : 'background:#F1F5F9;color:#94A3B8;border:1px solid #E2E8F0;'}
                  ">
                    ${step.done ? '✓' : ''}
                  </div>
                  <div>
                    <div style="font:600 13px/1.3 'DM Sans',sans-serif;color:${step.done ? '#0A1628' : '#94A3B8'};">${step.label}</div>
                    <div style="font:400 11px/1.3 'DM Sans',sans-serif;color:#94A3B8;">${step.detail}</div>
                  </div>
                </div>
              `)}
            </div>
          </div>
          <div style="width:180px;flex-shrink:0;background:#F7F9FB;border-radius:8px;padding:14px;">
            <div style="font:600 11px/1 'DM Sans',sans-serif;color:#94A3B8;margin-bottom:8px;">Pipeline view</div>
            ${['Received', 'Matching', 'Exception', 'Approved', 'Paid'].map((stage) => html`
              <div key=${stage} style="font:500 11px/2 'DM Sans',sans-serif;color:#0A1628;border-bottom:1px solid #E2E8F0;">${stage}</div>
            `)}
            <div style="font:400 10px/1 'DM Sans',sans-serif;color:#94A3B8;margin-top:8px;">← Stages</div>
          </div>
        </div>
      </div>
    </div>
  `;
}

// ==================== MAIN FLOW ====================

export default function OnboardingFlow({ api, onComplete, onDismiss, oauthBridge, backendUrl, signIn }) {
  const [step, setStep] = useState('auth');  // auth | workspace | erp | policy | slack | creating | done
  const [pending, setPending] = useState(false);
  const [erpType, setErpType] = useState('');
  const [erpError, setErpError] = useState('');
  const [workspaceError, setWorkspaceError] = useState('');
  const [workspaceDefaultName, setWorkspaceDefaultName] = useState('');
  const [policyError, setPolicyError] = useState('');
  const [slackError, setSlackError] = useState('');
  const [slackConnected, setSlackConnected] = useState(false);

  const handleSignIn = useCallback(async () => {
    setPending(true);
    try {
      // Native extension OAuth: chrome.identity.getAuthToken → register with
      // backend → backend Bearer token populated in queueManager. This is
      // the same credential queueManager.backendFetch uses, so the ERP step
      // that follows is authenticated.
      if (!signIn) throw new Error('signIn handler missing');
      await signIn();
      // Seed the workspace name with the email domain as a reasonable
      // default ("acme.com" → "Acme"). User can overwrite before continuing.
      try {
        const boot = await api('/api/workspace/bootstrap?organization_id=default', { silent: true });
        const orgName = boot?.organization?.name;
        const email = boot?.user?.email || '';
        const domainGuess = email.split('@')[1]?.split('.')[0] || '';
        const seed = (orgName && orgName !== 'default') ? orgName
          : domainGuess ? domainGuess.charAt(0).toUpperCase() + domainGuess.slice(1)
          : '';
        setWorkspaceDefaultName(seed);
      } catch { /* non-fatal — seed stays empty */ }
      setStep('workspace');
    } catch (_err) {
      // Stay on the auth step; user can click again.
    } finally {
      setPending(false);
    }
  }, [signIn, api]);

  const handleWorkspaceContinue = useCallback(async (workspaceName) => {
    setPending(true);
    setWorkspaceError('');
    try {
      await api('/api/workspace/org/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          organization_id: 'default',
          patch: { organization_name: workspaceName },
        }),
      });
      setStep('erp');
    } catch {
      setWorkspaceError('Could not save workspace name. Please try again.');
    } finally {
      setPending(false);
    }
  }, [api]);

  // Verify ERP was actually connected by querying the server-side
  // integration status. The OAuth popup can close for reasons that
  // are NOT authorization success: the user clicked the X, hit ESC,
  // cancelled on Google/Intuit's consent screen, or the callback
  // failed on the backend. Trusting "popup closed" as success
  // advanced the user into onboarding completion without an ERP
  // actually being connected. Source of truth is the DB, not the
  // popup lifecycle.
  const verifyErpConnected = useCallback(async () => {
    try {
      const data = await api('/api/workspace/integrations?organization_id=default', { silent: true });
      const erp = (data?.integrations || []).find((i) => i?.name === 'erp');
      return Boolean(erp?.connected);
    } catch {
      return false;
    }
  }, [api]);

  const handleErpSelect = useCallback(async (erpId) => {
    setPending(true);
    setErpType(erpId);
    setErpError('');
    try {
      const payload = await api('/api/workspace/integrations/erp/connect/start', {
        method: 'POST',
        body: JSON.stringify({ organization_id: 'default', erp_type: erpId }),
      });

      // Credential-based ERPs (NetSuite, SAP): no OAuth popup. Backend
      // returns a form spec; user fills it out on Connections later.
      // Advance to the policy step — ERP connection will complete
      // after onboarding via the Connections page.
      if (payload?.method === 'form' || payload?.method === 'not_configured') {
        setStep('policy');
        return;
      }

      if (payload?.auth_url && oauthBridge) {
        await new Promise((resolve) => {
          oauthBridge.startOAuth(payload.auth_url, `erp-${erpId}`, (result) => {
            resolve(result || null);
          });
        });
      } else if (payload?.auth_url) {
        window.open(payload.auth_url, '_blank', 'width=600,height=700');
      }

      const connected = await verifyErpConnected();
      if (!connected) {
        setErpError('We didn\'t see the connection complete. If the sign-in window closed before authorization, please try again.');
        return;
      }

      setStep('policy');
    } catch (err) {
      // §15 — surface the structured ERP error from the backend.
      // The backend returns {code, missing_permission, remediation_
      // link, message, detail} rather than raw exception text.
      const detail = err?.body?.detail;
      if (detail && typeof detail === 'object' && detail.message) {
        setErpError(detail.remediation_link
          ? `${detail.message} → ${detail.remediation_link}`
          : detail.message);
      } else {
        setErpError('Something went wrong connecting to your ERP. Please try again.');
      }
    } finally {
      setPending(false);
    }
  }, [api, oauthBridge, verifyErpConnected]);

  const handlePolicyContinue = useCallback(async (policyConfig) => {
    setPending(true);
    setPolicyError('');
    try {
      await api('/api/workspace/policies/ap', {
        method: 'PUT',
        body: JSON.stringify({
          organization_id: 'default',
          config: policyConfig,
        }),
      });
      setStep('slack');
    } catch {
      setPolicyError('Could not save your policy. Please try again.');
    } finally {
      setPending(false);
    }
  }, [api]);

  const handleSlackConnect = useCallback(async () => {
    setPending(true);
    setSlackError('');
    try {
      const payload = await api('/api/workspace/integrations/slack/install/start', {
        method: 'POST',
        body: JSON.stringify({ organization_id: 'default' }),
      });
      if (payload?.auth_url && oauthBridge) {
        await new Promise((resolve) => {
          oauthBridge.startOAuth(payload.auth_url, 'slack', (result) => resolve(result || null));
        });
      } else if (payload?.auth_url) {
        window.open(payload.auth_url, '_blank', 'width=600,height=700');
      }
      // Verify connection landed server-side.
      const data = await api('/api/workspace/integrations?organization_id=default', { silent: true });
      const slackIntegration = (data?.integrations || []).find((i) => i?.name === 'slack');
      if (slackIntegration?.connected) {
        setSlackConnected(true);
      } else {
        setSlackError('We didn\'t see the Slack connection complete. You can try again or skip and connect later from Settings.');
      }
    } catch {
      setSlackError('Could not start the Slack connect flow. You can skip and connect later from Settings.');
    } finally {
      setPending(false);
    }
  }, [api, oauthBridge]);

  const handleSlackFinish = useCallback(() => {
    setStep('creating');
  }, []);

  const handleCreationComplete = useCallback(() => {
    setStep('done');
    api('/api/workspace/onboarding/step', {
      method: 'POST',
      body: JSON.stringify({ organization_id: 'default', step: 4 }),
    }).catch(() => {});
    if (onComplete) onComplete();
  }, [api, onComplete]);

  if (step === 'done') return null;

  return html`
    <style>
      .cl-onboard-overlay {
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(10, 22, 40, 0.5); z-index: 99999;
        display: flex; align-items: center; justify-content: center;
        font-family: 'DM Sans', -apple-system, sans-serif;
      }
      .cl-onboard-modal {
        background: #fff; border-radius: 12px; padding: 32px;
        max-width: 380px; width: 90%; box-shadow: 0 20px 60px rgba(0,0,0,0.2);
      }
      .cl-onboard-google-btn {
        display: flex; align-items: center; justify-content: center;
        width: 100%; padding: 12px 16px; border: 1px solid #E2E8F0;
        border-radius: 8px; background: #fff; color: #0A1628;
        font: 500 14px/1 'DM Sans', sans-serif; cursor: pointer;
      }
      .cl-onboard-google-btn:hover { background: #F7F9FB; }
      .cl-onboard-google-btn:disabled { opacity: 0.6; cursor: not-allowed; }
      .cl-onboard-primary-btn {
        display: block; width: 100%; padding: 12px 16px;
        border: none; border-radius: 8px; background: #00D67E; color: #0A1628;
        font: 600 14px/1 'DM Sans', sans-serif; cursor: pointer;
      }
      .cl-onboard-primary-btn:hover { background: #00C271; }
      .cl-onboard-primary-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    </style>
    ${step === 'auth' ? html`<${AuthModal} onSignIn=${handleSignIn} pending=${pending} onDismiss=${onDismiss} />` : ''}
    ${step === 'workspace' ? html`<${CreateWorkspace} onContinue=${handleWorkspaceContinue} pending=${pending} errorMessage=${workspaceError} defaultName=${workspaceDefaultName} />` : ''}
    ${step === 'erp' ? html`<${ErpPicker} onSelect=${handleErpSelect} pending=${pending} errorMessage=${erpError} />` : ''}
    ${step === 'policy' ? html`<${PolicyForm} onContinue=${handlePolicyContinue} pending=${pending} errorMessage=${policyError} />` : ''}
    ${step === 'slack' ? html`<${SlackConnect} onConnect=${handleSlackConnect} onSkip=${handleSlackFinish} pending=${pending} errorMessage=${slackError} connected=${slackConnected} />` : ''}
    ${step === 'creating' ? html`<${PipelineCreation} erpType=${erpType} onComplete=${handleCreationComplete} />` : ''}
  `;
}
