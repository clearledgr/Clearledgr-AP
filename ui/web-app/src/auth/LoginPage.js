import { useEffect, useState } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { html } from '../utils/htm.js';
import { useSession, refreshSession } from './useSession.js';
import { api, ApiError } from '../api/client.js';

const GOOGLE_START_PATH = '/auth/google/start';
const MICROSOFT_START_PATH = '/auth/microsoft/start';

export function LoginPage() {
  const { isAuthenticated, isLoading } = useSession();
  const [, navigate] = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (isAuthenticated) {
      const params = new URLSearchParams(window.location.search);
      const next = params.get('next') || '/';
      navigate(next, { replace: true });
    }
  }, [isAuthenticated, navigate]);

  // After Google's callback returns the user to /login?post_oauth=1,
  // re-fetch /auth/me so the session cache picks up the freshly issued
  // cookies before AuthGate redirects.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has('post_oauth')) refreshSession();
  }, []);

  const startGoogle = () => {
    setError('');
    const params = new URLSearchParams({
      organization_id: 'default',
      redirect_path: '/?post_oauth=1',
    });
    window.location.href = `${GOOGLE_START_PATH}?${params.toString()}`;
  };

  const startMicrosoft = () => {
    setError('');
    const params = new URLSearchParams({
      organization_id: 'default',
      redirect_path: '/?post_oauth=1',
    });
    // The api returns 503 microsoft_oauth_not_configured if the
    // env vars aren't set on the server. The browser sees that as
    // a normal page navigation; we surface the error in the URL
    // bar via ?auth_error= and handle below.
    window.location.href = `${MICROSOFT_START_PATH}?${params.toString()}`;
  };

  const submitPassword = async (e) => {
    e.preventDefault();
    if (submitting) return;
    setError('');
    setSubmitting(true);
    try {
      await api('/auth/login', {
        method: 'POST',
        body: { email: email.trim(), password },
        retry: false,
      });
      await refreshSession();
      // The useEffect above redirects on isAuthenticated flip; this
      // handles the rare case where the session listener hasn't fired
      // by the time we get here.
      const params = new URLSearchParams(window.location.search);
      navigate(params.get('next') || '/', { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Email or password didn't match.");
      } else {
        setError(err?.message || 'Sign-in failed. Try again.');
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
        <h1 class="cl-auth-title">Sign in</h1>
        <p class="cl-auth-sub">Coordination layer for finance teams.</p>

        ${error ? html`<div class="cl-auth-error">${error}</div>` : null}

        <button class="cl-auth-btn cl-auth-btn-primary" onClick=${startGoogle} disabled=${submitting}>
          Continue with Google
        </button>

        <button class="cl-auth-btn cl-auth-btn-secondary" onClick=${startMicrosoft} disabled=${submitting}>
          Continue with Microsoft
        </button>

        <div class="cl-auth-divider"><span>or</span></div>

        <form class="cl-auth-form" onSubmit=${submitPassword} autoComplete="on">
          <label class="cl-auth-field">
            <span>Work email</span>
            <input
              type="email"
              autoComplete="email"
              required
              value=${email}
              onInput=${(e) => setEmail(e.currentTarget.value)}
              placeholder="you@company.com"
            />
          </label>
          <label class="cl-auth-field">
            <span>Password</span>
            <input
              type="password"
              autoComplete="current-password"
              required
              value=${password}
              onInput=${(e) => setPassword(e.currentTarget.value)}
            />
          </label>
          <button
            type="submit"
            class="cl-auth-btn cl-auth-btn-secondary"
            disabled=${submitting || !email || !password}>
            ${submitting ? 'Signing in…' : 'Sign in with email'}
          </button>
        </form>

        <p class="cl-auth-fineprint">
          Don't have an account yet? <a href="/request-demo">Request a demo</a>.
          If your team admin sent you an invite link, open it directly.
        </p>
        <p class="cl-auth-fineprint">
          By continuing you agree to our <a href="/terms">Terms</a>
          and <a href="/privacy">Privacy Policy</a>.
        </p>
      </div>
    </main>
  `;
}
