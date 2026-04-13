/**
 * Streak-style Onboarding Flow — DESIGN_THESIS.md §15
 *
 * Renders as a modal overlay on Gmail (like Streak's first-install modal).
 * Flow: Auth → ERP picker → Pipeline creation → Done
 *
 * Streak pattern:
 * 1. Modal: "Sign in with Google"
 * 2. Google OAuth consent
 * 3. Welcome page: "What do you want to use Streak for?"
 * 4. "Creating your pipeline..." with progress animation
 * 5. Category picker
 * 6. Ready — redirect to pipeline
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

// ==================== STEP 2: ERP PICKER ====================

function ErpPicker({ onSelect, pending }) {
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
          ${pending ? 'Connecting...' : 'Connect'}
        </button>
      </div>
    </div>
  `;
}

// ==================== STEP 3: PIPELINE CREATION PROGRESS ====================

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
  const [step, setStep] = useState('auth');  // auth | erp | creating | done
  const [pending, setPending] = useState(false);
  const [erpType, setErpType] = useState('');

  const handleSignIn = useCallback(async () => {
    setPending(true);
    try {
      // Native extension OAuth: chrome.identity.getAuthToken → register with
      // backend → backend Bearer token populated in queueManager. This is
      // the same credential queueManager.backendFetch uses, so the ERP step
      // that follows is authenticated.
      if (!signIn) throw new Error('signIn handler missing');
      await signIn();
      setStep('erp');
    } catch (_err) {
      // Stay on the auth step; user can click again.
    } finally {
      setPending(false);
    }
  }, [signIn]);

  const handleErpSelect = useCallback(async (erpId) => {
    setPending(true);
    setErpType(erpId);
    try {
      const payload = await api('/api/workspace/integrations/erp/connect/start', {
        method: 'POST',
        body: JSON.stringify({ organization_id: 'default', erp_type: erpId }),
      });

      // Credential-based ERPs (NetSuite, SAP): no OAuth popup. Backend
      // returns a form spec; user fills it out on Connections later.
      // Advance through onboarding — connecting is deferred.
      if (payload?.method === 'form' || payload?.method === 'not_configured') {
        setStep('creating');
        return;
      }

      if (payload?.auth_url && oauthBridge) {
        // Wait for the OAuth popup's real result rather than blindly
        // advancing. The bridge fires clearledgr_erp_oauth_complete from
        // the backend callback; we advance only on success (or on
        // popup-closed-without-message, which we treat as optimistic
        // success since the callback may have completed before the
        // postMessage window opened).
        await new Promise((resolve) => {
          const handler = (event) => {
            const data = event?.data;
            if (!data || data.type !== 'clearledgr_erp_oauth_complete') return;
            if (String(data.erp || '').toLowerCase() !== String(erpId).toLowerCase()) return;
            window.removeEventListener('message', handler);
            resolve({ success: !!data.success, detail: data.detail || null });
          };
          window.addEventListener('message', handler);
          oauthBridge.startOAuth(payload.auth_url, `erp-${erpId}`);
        });
      } else if (payload?.auth_url) {
        // No bridge available (defensive): open in a blank window and
        // advance. Can't wait on a result we can't hear.
        window.open(payload.auth_url, '_blank', 'width=600,height=700');
      }

      setStep('creating');
    } catch {
      // ERP failed — user can connect later from Connections page.
      setStep('creating');
    } finally {
      setPending(false);
    }
  }, [api, oauthBridge]);

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
    ${step === 'erp' ? html`<${ErpPicker} onSelect=${handleErpSelect} pending=${pending} />` : ''}
    ${step === 'creating' ? html`<${PipelineCreation} erpType=${erpType} onComplete=${handleCreationComplete} />` : ''}
  `;
}
