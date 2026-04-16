/**
 * InviteVendorModal — shared modal for opening a vendor onboarding session.
 *
 * Shared by VendorOnboardingPage (admin board) and ThreadSidebar (Gmail
 * thread prompt). Handles the POST to
 *   /api/vendors/{vendor_name}/onboarding/invite
 * and surfaces the magic-link / email-dispatch result to the operator.
 *
 * Props:
 *   api           — bound fetch helper (url, opts) → JSON
 *   orgId         — organization id for the invite
 *   defaultVendor — prefilled vendor name (locked if truthy)
 *   defaultEmail  — prefilled contact email (editable)
 *   onClose       — fires when operator dismisses the modal
 *   onSuccess     — fires with the invite response on successful invite
 *   toast         — optional toast(message, kind) helper
 */
import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

const OVERLAY_CSS = `
.cl-invm-overlay {
  position: fixed; inset: 0; background: rgba(10, 22, 40, 0.45);
  display: flex; align-items: center; justify-content: center;
  z-index: 9999; padding: 24px;
}
.cl-invm-card {
  background: #fff; border-radius: 12px; max-width: 460px; width: 100%;
  box-shadow: 0 20px 48px rgba(10, 22, 40, 0.28);
  padding: 24px 24px 20px; font-family: inherit;
}
.cl-invm-title { font-size: 17px; font-weight: 700; color: #0A1628; margin: 0 0 4px; }
.cl-invm-subtitle { font-size: 12px; color: #5C6B7A; margin: 0 0 16px; line-height: 1.4; }
.cl-invm-field { margin-bottom: 12px; }
.cl-invm-label {
  display: block; font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.04em; color: #5C6B7A; margin-bottom: 4px;
}
.cl-invm-input {
  width: 100%; padding: 8px 10px; border: 1px solid #E2E8F0; border-radius: 6px;
  font-size: 13px; color: #0A1628; background: #fff; box-sizing: border-box;
}
.cl-invm-input:focus { outline: none; border-color: #00D67E; }
.cl-invm-input[disabled] { background: #F7F9FB; color: #5C6B7A; }
.cl-invm-hint { font-size: 11px; color: #94A3B8; margin-top: 4px; }
.cl-invm-error {
  font-size: 12px; color: #991B1B; background: #FEF2F2;
  padding: 8px 10px; border-radius: 6px; margin: 4px 0 12px;
}
.cl-invm-success {
  font-size: 12px; color: #065F46; background: #ECFDF5;
  padding: 10px 12px; border-radius: 6px; margin: 4px 0 12px; line-height: 1.5;
}
.cl-invm-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 4px; }
.cl-invm-btn {
  padding: 8px 14px; border-radius: 6px; font-size: 13px; font-weight: 600;
  cursor: pointer; border: 1px solid transparent;
}
.cl-invm-btn.primary { background: #00D67E; color: #0A1628; border-color: #00D67E; }
.cl-invm-btn.primary:disabled { background: #9FE6C1; cursor: not-allowed; }
.cl-invm-btn.ghost { background: #fff; color: #5C6B7A; border-color: #E2E8F0; }
.cl-invm-link {
  font-family: 'SF Mono', 'Fira Code', monospace; font-size: 11px;
  word-break: break-all; color: #0A1628; display: block;
  background: #F7F9FB; padding: 6px 8px; border-radius: 4px; margin-top: 4px;
}
`;

function isValidEmail(s) {
  const v = String(s || '').trim();
  return v.length >= 3 && v.length <= 320 && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v);
}

export default function InviteVendorModal({
  api,
  orgId,
  defaultVendor = '',
  defaultEmail = '',
  onClose,
  onSuccess,
  toast,
}) {
  const [vendorName, setVendorName] = useState(defaultVendor || '');
  const [contactEmail, setContactEmail] = useState(defaultEmail || '');
  const [contactName, setContactName] = useState('');
  const [ttlDays, setTtlDays] = useState(14);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  const vendorLocked = Boolean(defaultVendor);

  const handleSubmit = async (e) => {
    if (e && e.preventDefault) e.preventDefault();
    setError('');
    const v = String(vendorName || '').trim();
    const c = String(contactEmail || '').trim();
    if (!v) { setError('Vendor name is required.'); return; }
    if (!isValidEmail(c)) { setError('Enter a valid contact email.'); return; }
    const ttl = Math.max(1, Math.min(30, parseInt(ttlDays, 10) || 14));

    setSubmitting(true);
    try {
      const data = await api(
        `/api/vendors/${encodeURIComponent(v)}/onboarding/invite?organization_id=${encodeURIComponent(orgId)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            contact_email: c,
            contact_name: contactName.trim() || null,
            ttl_days: ttl,
          }),
        },
      );
      setResult(data);
      toast?.('Onboarding invite sent.', 'success');
      onSuccess?.(data);
    } catch (err) {
      const msg = err?.message || String(err);
      // Common server error payloads include "onboarding_session_already_active"
      if (/already_active/i.test(msg)) {
        setError('An onboarding session is already active for this vendor.');
      } else if (/403|cross_tenant/i.test(msg)) {
        setError('You don\'t have permission to invite vendors.');
      } else {
        setError(msg || 'Invite failed. Please try again.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const close = () => { if (!submitting) onClose?.(); };

  return html`
    <div class="cl-invm-overlay" onClick=${close}>
      <style>${OVERLAY_CSS}</style>
      <div class="cl-invm-card" onClick=${(e) => e.stopPropagation()}>
        ${result ? html`
          <h3 class="cl-invm-title">Invite sent</h3>
          <p class="cl-invm-subtitle">
            ${result.email_dispatch?.success
              ? `We emailed the onboarding link to ${result.contact_email}.`
              : 'Email dispatch didn\'t go through. Copy the magic link and send it manually.'}
          </p>
          <div class="cl-invm-success">
            <div><strong>Magic link</strong></div>
            <span class="cl-invm-link">${result.magic_link}</span>
          </div>
          <div class="cl-invm-actions">
            <button class="cl-invm-btn primary" onClick=${close}>Done</button>
          </div>
        ` : html`
          <h3 class="cl-invm-title">Invite vendor to onboarding</h3>
          <p class="cl-invm-subtitle">
            Opens a fresh onboarding session and emails a one-time link. The vendor completes
            KYC, bank details, and micro-deposit verification through the portal.
          </p>
          <form onSubmit=${handleSubmit}>
            <div class="cl-invm-field">
              <label class="cl-invm-label" for="cl-invm-vendor">Vendor name</label>
              <input
                id="cl-invm-vendor"
                class="cl-invm-input"
                type="text"
                value=${vendorName}
                disabled=${vendorLocked}
                maxLength=${128}
                onInput=${(e) => setVendorName(e.currentTarget.value)}
                placeholder="e.g. Acme Ltd"
              />
            </div>
            <div class="cl-invm-field">
              <label class="cl-invm-label" for="cl-invm-email">Contact email</label>
              <input
                id="cl-invm-email"
                class="cl-invm-input"
                type="email"
                value=${contactEmail}
                maxLength=${320}
                onInput=${(e) => setContactEmail(e.currentTarget.value)}
                placeholder="billing@acme.com"
              />
            </div>
            <div class="cl-invm-field">
              <label class="cl-invm-label" for="cl-invm-name">Contact name (optional)</label>
              <input
                id="cl-invm-name"
                class="cl-invm-input"
                type="text"
                value=${contactName}
                maxLength=${128}
                onInput=${(e) => setContactName(e.currentTarget.value)}
                placeholder="Jane Doe"
              />
            </div>
            <div class="cl-invm-field">
              <label class="cl-invm-label" for="cl-invm-ttl">Link expires after</label>
              <input
                id="cl-invm-ttl"
                class="cl-invm-input"
                type="number"
                value=${ttlDays}
                min=${1}
                max=${30}
                onInput=${(e) => setTtlDays(e.currentTarget.value)}
              />
              <div class="cl-invm-hint">Days. Chase loop re-issues on expiry.</div>
            </div>
            ${error ? html`<div class="cl-invm-error">${error}</div>` : ''}
            <div class="cl-invm-actions">
              <button
                type="button"
                class="cl-invm-btn ghost"
                onClick=${close}
                disabled=${submitting}
              >Cancel</button>
              <button
                type="submit"
                class="cl-invm-btn primary"
                disabled=${submitting}
              >${submitting ? 'Sending…' : 'Send invite'}</button>
            </div>
          </form>
        `}
      </div>
    </div>
  `;
}
