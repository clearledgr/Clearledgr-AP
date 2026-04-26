import { createContext, h } from 'preact';
import { useContext, useEffect, useState } from 'preact/hooks';
import { html } from '../utils/htm.js';
import { api } from '../api/client.js';

/**
 * Bootstrap = the per-session payload extension pages used to receive
 * via InboxSDK injection. Includes current_user, organization,
 * integrations, and capability flags. Pages read it through context
 * instead of the prop drilling the extension did.
 */
const BootstrapContext = createContext(null);

const BOOTSTRAP_ENDPOINT = '/api/workspace/bootstrap';

export function BootstrapProvider({ children }) {
  const [state, setState] = useState({ status: 'loading', data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    api(BOOTSTRAP_ENDPOINT, { retry: false })
      .then((data) => { if (!cancelled) setState({ status: 'ready', data, error: null }); })
      .catch((err) => {
        if (cancelled) return;
        // Non-fatal: pages can render without a bootstrap by treating
        // the user as `operator` with no integrations. Capabilities
        // hook returns the fallback in that case.
        setState({ status: 'ready', data: null, error: err.message || String(err) });
      });
    return () => { cancelled = true; };
  }, []);

  if (state.status === 'loading') {
    return html`<div class="cl-app-loading">Loading workspace…</div>`;
  }
  return html`<${BootstrapContext.Provider} value=${state.data}>${children}<//>`;
}

export function useBootstrap() {
  return useContext(BootstrapContext);
}

export function useOrgId() {
  const bootstrap = useBootstrap();
  return (
    bootstrap?.organization?.id ||
    bootstrap?.organization_id ||
    bootstrap?.current_user?.organization_id ||
    'default'
  );
}

export function useUserEmail() {
  const bootstrap = useBootstrap();
  return bootstrap?.current_user?.email || '';
}
