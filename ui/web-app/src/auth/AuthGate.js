import { useLocation } from 'wouter-preact';
import { useEffect } from 'preact/hooks';
import { useSession } from './useSession.js';
import { html } from '../utils/htm.js';

export function AuthGate({ children }) {
  const { isLoading, isAuthenticated } = useSession();
  const [, navigate] = useLocation();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      navigate(`/login?next=${next}`, { replace: true });
    }
  }, [isLoading, isAuthenticated, navigate]);

  if (isLoading) {
    return html`<div class="cl-app-loading">Loading…</div>`;
  }
  if (!isAuthenticated) {
    return null;
  }
  return children;
}
