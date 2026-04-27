import { useEffect, useState } from 'preact/hooks';
import { html } from '../utils/htm.js';
import { api } from '../api/client.js';

/**
 * App footer — privacy / terms / status / help links across every
 * authenticated page. Status indicator is a small live dot that
 * reflects the api's /health response so customers can see at a
 * glance whether the workspace is operating cleanly.
 */
export function AppFooter() {
  const [status, setStatus] = useState({ state: 'checking', label: 'Checking…' });

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const check = async () => {
      try {
        const data = await api('/health', { retry: false });
        if (cancelled) return;
        const ok = data && data.status === 'healthy';
        setStatus(ok
          ? { state: 'ok', label: 'All systems operational' }
          : { state: 'degraded', label: 'Partially degraded' });
      } catch {
        if (cancelled) return;
        setStatus({ state: 'down', label: 'Unable to reach api' });
      }
      if (!cancelled) timer = setTimeout(check, 60_000);
    };

    check();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return html`
    <footer class="cl-app-footer">
      <a class=${`cl-footer-status cl-footer-status-${status.state}`} href="/status">
        <span class="cl-footer-status-dot" aria-hidden="true"></span>
        <span class="cl-footer-status-label">${status.label}</span>
      </a>
      <div class="cl-footer-links">
        <a href="/privacy">Privacy</a>
        <a href="/terms">Terms</a>
        <a href="/status">Status</a>
        <a href="mailto:hello@clearledgr.com">Help</a>
      </div>
      <div class="cl-footer-meta">
        © ${new Date().getFullYear()} Clearledgr
      </div>
    </footer>
  `;
}
