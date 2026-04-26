import { useEffect, useState } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { html } from '../utils/htm.js';
import { useSession, refreshSession } from './useSession.js';

const GOOGLE_START_PATH = '/auth/google/start';

export function LoginPage() {
  const { isAuthenticated, isLoading } = useSession();
  const [, navigate] = useLocation();
  const [error, setError] = useState('');

  useEffect(() => {
    if (isAuthenticated) {
      const params = new URLSearchParams(window.location.search);
      const next = params.get('next') || '/';
      navigate(next, { replace: true });
    }
  }, [isAuthenticated, navigate]);

  // After Google callback redirects back here we re-fetch /auth/me
  // so the session cache picks up the freshly issued cookies.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has('post_oauth')) {
      refreshSession();
    }
  }, []);

  const startGoogle = () => {
    setError('');
    const params = new URLSearchParams({
      organization_id: 'default',
      redirect_path: '/?post_oauth=1',
    });
    window.location.href = `${GOOGLE_START_PATH}?${params.toString()}`;
  };

  if (isLoading) return html`<div class="cl-auth-loading">Loading…</div>`;

  return html`
    <main class="cl-auth-shell">
      <div class="cl-auth-card">
        <div class="cl-auth-brand">Clearledgr</div>
        <h1 class="cl-auth-title">Sign in</h1>
        <p class="cl-auth-sub">Coordination layer for finance teams.</p>
        ${error ? html`<div class="cl-auth-error">${error}</div>` : null}
        <button class="cl-auth-btn cl-auth-btn-primary" onClick=${startGoogle}>
          Continue with Google
        </button>
        <div class="cl-auth-divider"><span>or</span></div>
        <a class="cl-auth-btn cl-auth-btn-secondary" href="mailto:hello@clearledgr.com?subject=Email%20sign-in%20access">
          Email sign-in
        </a>
        <p class="cl-auth-fineprint">
          By continuing you agree to our <a href="https://clearledgr.com/terms">Terms</a>
          and <a href="https://clearledgr.com/privacy">Privacy Policy</a>.
        </p>
      </div>
    </main>
  `;
}
