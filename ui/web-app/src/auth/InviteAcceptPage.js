import { useEffect, useState } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { html } from '../utils/htm.js';
import { api, ApiError } from '../api/client.js';
import { logout, refreshSession, useSession } from './useSession.js';
import { BrandMark } from '../shell/BrandMark.js';

/**
 * /signup/accept?token=<invite-token>
 *
 * Lands a teammate who clicked an admin's invite link. Three states:
 *
 *   1. Not signed in. Show OAuth (Google / Microsoft) + email+password
 *      form. All three honour the invite_token and bind the new
 *      account to the invited email.
 *   2. Signed in AS THE INVITED EMAIL. Auto-accept (call /auth/invites/
 *      accept with just the token — the existing-user branch doesn't
 *      need a password) and continue to the workspace.
 *   3. Signed in AS A DIFFERENT EMAIL. Block silently bouncing them
 *      to their own home (which silently lost the invitee's
 *      attribution). Show "this invite is for X, you're Y" with a
 *      sign-out button.
 *
 * Pre-fix the page ALWAYS redirected authenticated users to /, which
 * meant an admin clicking their own invite-test link (or any user who
 * already had a Solden session) never saw the invite at all and the
 * invite stayed pending forever.
 */
export function InviteAcceptPage() {
  const { session, isAuthenticated, isLoading } = useSession();
  const [, navigate] = useLocation();

  const [token] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return (params.get('token') || '').trim();
  });

  // Preview state: undefined = not yet fetched, null = fetch errored,
  // object = present.
  const [preview, setPreview] = useState(undefined);
  const [previewError, setPreviewError] = useState('');

  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // Fetch invite metadata on mount. Without this we can't tell
  // which email the invite is intended for and can't make a smart
  // decision about already-authenticated visitors.
  useEffect(() => {
    if (!token) return undefined;
    let cancelled = false;
    api(`/auth/invites/preview?token=${encodeURIComponent(token)}`, { retry: false })
      .then((res) => { if (!cancelled) setPreview(res); })
      .catch((err) => {
        if (cancelled) return;
        const code = err instanceof ApiError ? err.status : 0;
        const detail = err?.payload?.detail;
        if (code === 404 || detail === 'invite_not_found') {
          setPreviewError('This invite link is no longer valid. Ask your admin to resend.');
        } else {
          setPreviewError(err?.message || 'Could not load the invite.');
        }
        setPreview(null);
      });
    return () => { cancelled = true; };
  }, [token]);

  if (!token) {
    return html`
      <main class="cl-auth-shell">
        <div class="cl-auth-card">
          <div class="cl-auth-brand"><${BrandMark} height=${36} /></div>
          <h1 class="cl-auth-title">Invite link incomplete</h1>
          <p class="cl-auth-sub">
            The invite token is missing from this URL. Open the link
            from your invite email exactly as it was sent, or ask
            your admin to send a new one.
          </p>
        </div>
      </main>
    `;
  }

  if (isLoading || preview === undefined) {
    return html`<div class="cl-auth-loading">Loading…</div>`;
  }

  if (preview === null) {
    return html`
      <main class="cl-auth-shell">
        <div class="cl-auth-card">
          <div class="cl-auth-brand"><${BrandMark} height=${36} /></div>
          <h1 class="cl-auth-title">Invite unavailable</h1>
          <p class="cl-auth-sub">${previewError}</p>
        </div>
      </main>
    `;
  }

  if (preview.status && preview.status !== 'pending') {
    const message = preview.status === 'accepted'
      ? 'This invite was already accepted. Sign in normally to reach your workspace.'
      : 'This invite is no longer active. Ask your admin to send a fresh one.';
    return html`
      <main class="cl-auth-shell">
        <div class="cl-auth-card">
          <div class="cl-auth-brand"><${BrandMark} height=${36} /></div>
          <h1 class="cl-auth-title">Invite ${preview.status}</h1>
          <p class="cl-auth-sub">${message}</p>
        </div>
      </main>
    `;
  }

  const inviteEmail = (preview.email || '').toLowerCase().trim();
  const sessionEmail = (session?.email || '').toLowerCase().trim();
  const sameUser = isAuthenticated && sessionEmail && sessionEmail === inviteEmail;
  const wrongUser = isAuthenticated && sessionEmail && sessionEmail !== inviteEmail;
  const orgLabel = preview.organization_name || 'your team';

  // ── Signed in as the invited email. Accept + continue. ─────────
  const acceptAsCurrentUser = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError('');
    try {
      await api('/auth/invites/accept', {
        method: 'POST',
        body: { token },
        retry: false,
      });
      await refreshSession();
      navigate('/', { replace: true });
    } catch (err) {
      const detail = err?.payload?.detail;
      setError(detail || err?.message || 'Could not accept invite.');
      setSubmitting(false);
    }
  };

  if (sameUser) {
    return html`
      <main class="cl-auth-shell">
        <div class="cl-auth-card">
          <div class="cl-auth-brand"><${BrandMark} height=${36} /></div>
          <h1 class="cl-auth-title">Welcome to ${orgLabel}</h1>
          <p class="cl-auth-sub">
            You're already signed in as <strong>${inviteEmail}</strong>.
            Accept the invite to bind this account to the workspace.
          </p>
          ${error ? html`<div class="cl-auth-error">${error}</div>` : null}
          <button
            class="cl-auth-btn cl-auth-btn-primary"
            onClick=${acceptAsCurrentUser}
            disabled=${submitting}>
            ${submitting ? 'Accepting…' : 'Accept invite'}
          </button>
        </div>
      </main>
    `;
  }

  // ── Signed in as a different email. Don't silently bounce. ─────
  if (wrongUser) {
    const signOutAndStay = async () => {
      try {
        await logout();
      } catch { /* swallow — logout best-effort */ }
      // Refresh the page so useSession reloads as unauthenticated and
      // the invite token / preview both rehydrate from the URL.
      try {
        window.location.reload();
      } catch { /* old browsers — no-op */ }
    };
    return html`
      <main class="cl-auth-shell">
        <div class="cl-auth-card">
          <div class="cl-auth-brand"><${BrandMark} height=${36} /></div>
          <h1 class="cl-auth-title">This invite is for someone else</h1>
          <p class="cl-auth-sub">
            The invite was sent to <strong>${inviteEmail}</strong>, but
            you're signed in as <strong>${sessionEmail}</strong>. Sign
            out first, then re-open the invite link and sign in with
            ${inviteEmail}.
          </p>
          <button
            class="cl-auth-btn cl-auth-btn-primary"
            onClick=${signOutAndStay}>
            Sign out
          </button>
        </div>
      </main>
    `;
  }

  // ── Not signed in. OAuth + email+password. ─────────────────────
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

  // OAuth-start URLs thread the invite token through the signed state
  // payload (Google: auth.py L636; Microsoft: auth.py L884). The SPA
  // proxies /auth/* to the api service so relative paths work in dev +
  // production identically.
  const googleStart = `/auth/google/start?invite_token=${encodeURIComponent(token)}`;
  const microsoftStart = `/auth/microsoft/start?invite_token=${encodeURIComponent(token)}`;

  return html`
    <main class="cl-auth-shell">
      <div class="cl-auth-card">
        <div class="cl-auth-brand"><${BrandMark} height=${36} /></div>
        <h1 class="cl-auth-title">Join ${orgLabel}</h1>
        <p class="cl-auth-sub">
          You've been invited as <strong>${inviteEmail}</strong>. Pick
          how you want to sign in — Google, Microsoft, or set a password.
        </p>

        ${error ? html`<div class="cl-auth-error">${error}</div>` : null}

        <div class="cl-auth-providers" style="display:flex;flex-direction:column;gap:10px;margin-bottom:18px">
          <a
            class="cl-auth-btn cl-auth-btn-secondary"
            href=${googleStart}
            style="display:flex;align-items:center;justify-content:center;gap:10px;text-decoration:none">
            <span>Continue with Google</span>
          </a>
          <a
            class="cl-auth-btn cl-auth-btn-secondary"
            href=${microsoftStart}
            style="display:flex;align-items:center;justify-content:center;gap:10px;text-decoration:none">
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
