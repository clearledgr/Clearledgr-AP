import { createContext, h } from 'preact';
import { useCallback, useContext, useEffect, useMemo, useState } from 'preact/hooks';
import { html } from '../utils/htm.js';
import { api } from '../api/client.js';

/**
 * Bootstrap = the per-session payload extension pages used to receive
 * via InboxSDK injection. Includes current_user, organization,
 * integrations, and capability flags. Pages read it through context
 * instead of the prop drilling the extension did.
 *
 * The provider also exposes a `refresh` callback that re-fetches
 * /api/workspace/bootstrap; pages that take an `onRefresh` prop
 * (ConnectionsPage, SettingsPage, ReconciliationPage, etc.) get this
 * via `usePageProps()` so admin actions which change integration
 * state can invalidate the cached bootstrap.
 */
const BootstrapContext = createContext({ data: null, refresh: () => Promise.resolve() });

const BOOTSTRAP_ENDPOINT = '/api/workspace/bootstrap';

export function BootstrapProvider({ children }) {
  const [state, setState] = useState({ status: 'loading', data: null, error: null });

  const load = useCallback(async () => {
    try {
      const data = await api(BOOTSTRAP_ENDPOINT, { retry: false });
      setState({ status: 'ready', data, error: null });
      return data;
    } catch (err) {
      // Non-fatal: pages can render without a bootstrap by treating
      // the user as `operator` with no integrations. Capabilities
      // hook returns the fallback in that case.
      setState({ status: 'ready', data: null, error: err?.message || String(err) });
      return null;
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const value = useMemo(() => ({ data: state.data, refresh: load }), [state.data, load]);

  if (state.status === 'loading') {
    return html`<div class="cl-app-loading">Loading workspace…</div>`;
  }
  return html`<${BootstrapContext.Provider} value=${value}>${children}<//>`;
}

export function useBootstrap() {
  return useContext(BootstrapContext).data;
}

export function useBootstrapRefresh() {
  return useContext(BootstrapContext).refresh;
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
