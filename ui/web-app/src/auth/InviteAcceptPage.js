import { useEffect, useState } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { html } from '../utils/htm.js';
import { api, ApiError } from '../api/client.js';
import { refreshSession, useSession } from './useSession.js';
import { BrandMark } from '../shell/BrandMark.js';

/**
 * /signup/accept?token=<invite-token>
 *
 * Lands a teammate who clicked an admin's invite link into the org.
 * Posts to /auth/invites/accept which:
 *   - looks up the invite row by token
 *   - if the user already exists: updates org/role and signs them in
 *   - if not: creates the user with the password they set here and
 *     signs them in
 *
 * Cookies are set by the backend in the same response — useSession
 * picks up the new session immediately via refreshSession() and the
 * AuthGate redirects to the post-accept destination.
 */
export function InviteAcceptPage() {
  const { isAuthenticated, isLoading } = useSession();
  const [, navigate] = useLocation();

  const [token] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return (params.get('token') || '').trim();
  });
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (isAuthenticated && !submitting) {
      navigate('/', { replace: true });
    }
  }, [isAuthenticated, submitting, navigate]);

  if (!token) {
    return html`
      <main class="cl-auth-shell">
        <div class="cl-auth-card">
          <div class="cl-auth-brand"><${BrandMark} height=${36} /></div>
          <h1 class="cl-auth-title">Invite link incomplete</h1>
          <p class="cl-auth-sub">
            The invite token is missing from this URL. Open the link from
            your invite email exactly as it was sent, or ask your admin
            to send a new one.
          </p>
        </div>
      </main>
    `;
  }

  const submit = async (e) => {
    e.preventDefault();
    if (submitting) return;
    setError('');

    if (password.length < 12) {
      setError('Password must be at least 12 characters.');
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords don't match.");
      return;
    }

    setSubmitting(true);
    try {
      await api('/auth/invites/accept', {
        method: 'POST',
        body: { token, password, name: name.trim() || undefined },
        retry: false,
      });
      await refreshSession();
      navigate('/', { replace: true });
    } catch (err) {
      const code = err instanceof ApiError ? err.status : 0;
      const detail = err?.payload?.detail;
      if (code === 404 || detail === 'invite_not_found') {
        setError("We couldn't find this invite. It may have been revoked.");
      } else if (detail === 'invite_not_pending') {
        setError('This invite has already been used. Sign in normally.');
      } else if (detail === 'invite_expired') {
        setError('This invite has expired. Ask your admin to resend it.');
      } else if (detail === 'password_required_for_new_user') {
        setError('Set a password to finish creating your account.');
      } else {
        setError(err?.message || 'Could not accept invite. Try again.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (isLoading) return html`<div class="cl-auth-loading">Loading…</div>`;

  // Pre-built OAuth start URLs that thread the invite token through.
  // The backend Google + Microsoft callbacks both honour
  // invite_token in the signed state payload (see
  // clearledgr/api/auth.py — google/start at L636, microsoft/start
  // at L884). The SPA proxies /auth/* to the api service so a
  // relative path works in dev + production identically.
  const googleStart = `/auth/google/start?invite_token=${encodeURIComponent(token)}`;
  const microsoftStart = `/auth/microsoft/start?invite_token=${encodeURIComponent(token)}`;

  return html`
    <main class="cl-auth-shell">
      <div class="cl-auth-card">
        <div class="cl-auth-brand"><${BrandMark} height=${36} /></div>
        <h1 class="cl-auth-title">Join your team</h1>
        <p class="cl-auth-sub">
          Pick how you want to sign in. Whichever option you choose,
          you'll land in the same workspace.
        </p>

        ${error ? html`<div class="cl-auth-error">${error}</div>` : null}

        <div class="cl-auth-providers" style="display:flex;flex-direction:column;gap:10px;margin-bottom:18px">
          <a
            class="cl-auth-btn cl-auth-btn-secondary"
            href=${googleStart}
            style="display:flex;align-items:center;justify-content:center;gap:10px;text-decoration:none">
            <span aria-hidden="true">🟦</span>
            <span>Continue with Google</span>
          </a>
          <a
            class="cl-auth-btn cl-auth-btn-secondary"
            href=${microsoftStart}
            style="display:flex;align-items:center;justify-content:center;gap:10px;text-decoration:none">
            <span aria-hidden="true">⬛</span>
            <span>Continue with Microsoft</span>
          </a>
        </div>

        <div
          aria-hidden="true"
          style="display:flex;align-items:center;gap:10px;margin:6px 0 16px;color:var(--muted, #5a6b80);font-size:12px">
          <span style="flex:1;height:1px;background:currentColor;opacity:0.2"></span>
          <span>or set a password</span>
          <span style="flex:1;height:1px;background:currentColor;opacity:0.2"></span>
        </div>

        <form class="cl-auth-form" onSubmit=${submit} autoComplete="on">
          <label class="cl-auth-field">
            <span>Display name <em>(optional)</em></span>
            <input
              type="text"
              autoComplete="name"
              value=${name}
              onInput=${(e) => setName(e.currentTarget.value)}
              placeholder="Mo Mbalam"
            />
          </label>
          <label class="cl-auth-field">
            <span>Set a password</span>
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength=${12}
              value=${password}
              onInput=${(e) => setPassword(e.currentTarget.value)}
            />
          </label>
          <label class="cl-auth-field">
            <span>Confirm password</span>
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength=${12}
              value=${confirmPassword}
              onInput=${(e) => setConfirmPassword(e.currentTarget.value)}
            />
          </label>
          <button
            type="submit"
            class="cl-auth-btn cl-auth-btn-primary"
            disabled=${submitting || !password || !confirmPassword}>
            ${submitting ? 'Accepting…' : 'Accept invite'}
          </button>
        </form>

        <p class="cl-auth-fineprint">
          Use 12+ characters. We hash with bcrypt; we never see your
          password in the clear.
        </p>
      </div>
    </main>
  `;
}
