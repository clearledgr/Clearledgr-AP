import { useEffect, useState } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { html } from '../utils/htm.js';
import { api, ApiError } from '../api/client.js';
import { refreshSession, useSession } from './useSession.js';

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
          <div class="cl-auth-brand">Clearledgr</div>
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

  return html`
    <main class="cl-auth-shell">
      <div class="cl-auth-card">
        <div class="cl-auth-brand">Clearledgr</div>
        <h1 class="cl-auth-title">Join your team</h1>
        <p class="cl-auth-sub">
          Set a password to finish accepting your invite. You'll sign in
          with the same email going forward.
        </p>

        ${error ? html`<div class="cl-auth-error">${error}</div>` : null}

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
