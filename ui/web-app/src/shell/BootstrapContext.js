import { createContext, h } from 'preact';
import { useCallback, useContext, useEffect, useMemo, useState } from 'preact/hooks';
import { html } from '../utils/htm.js';
import { api } from '../api/client.js';
import { setFaviconBadge } from '../lib/faviconBadge.js';

/**
 * Bootstrap = the per-session payload extension pages used to receive
 * via InboxSDK injection. Includes current_user, organization,
 * integrations, and capability flags. Pages read it through context
 * instead of the prop drilling the extension did.
 *
 * The provider also exposes a `refresh` callback that re-fetches
 * /api/workspace/bootstrap; pages that take an `onRefresh` prop
 * (ConnectionsPage, SettingsPage, etc.) get this
 * via `usePageProps()` so admin actions which change integration
 * state can invalidate the cached bootstrap.
 */
const BootstrapContext = createContext({ data: null, refresh: () => Promise.resolve() });

const BOOTSTRAP_ENDPOINT = '/api/workspace/bootstrap';

function resolveBootstrapOrgId(data) {
  return (
    data?.organization?.id ||
    data?.organization_id ||
    data?.current_user?.organization_id ||
    null
  );
}

export function BootstrapProvider({ children }) {
  const [state, setState] = useState({ status: 'loading', data: null, error: null });

  const load = useCallback(async () => {
    setState((current) => (
      current.status === 'ready'
        ? current
        : { status: 'loading', data: null, error: null }
    ));
    try {
      const data = await api(BOOTSTRAP_ENDPOINT, { retry: false });
      if (!resolveBootstrapOrgId(data)) {
        setState({ status: 'error', data: null, error: 'missing_organization' });
        return null;
      }
      setState({ status: 'ready', data, error: null });
      return data;
    } catch (err) {
      setState({ status: 'error', data: null, error: err?.message || String(err) });
      return null;
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // Status-aware favicon: whenever the bootstrap payload changes,
  // re-paint the workspace favicon so the browser tab reflects how
  // many AP items are waiting on operator approval. ``pending_approval``
  // is the count derived in workspace_shell._safe_dashboard_stats —
  // see solden/api/workspace_shell.py L1113. Setting count=0
  // restores the unbadged Solden mark.
  useEffect(() => {
    const count = Number(state.data?.dashboard_stats?.pending_approval || 0);
    void setFaviconBadge(count);
  }, [state.data]);

  const value = useMemo(() => ({ data: state.data, refresh: load }), [state.data, load]);

  if (state.status === 'loading') {
    return html`<div class="cl-app-loading">Loading workspace…</div>`;
  }
  if (state.status === 'error') {
    return html`
      <div class="cl-app-loading cl-app-loading-error" role="alert">
        <div class="cl-app-loading-card">
          <h1>We couldn't load your workspace.</h1>
          <p>Refresh your session, then try again. If this keeps happening, contact your workspace admin.</p>
          <div class="cl-app-loading-actions">
            <button type="button" onClick=${load}>Retry</button>
            <button type="button" onClick=${() => { window.location.href = '/login'; }}>
              Sign in again
            </button>
          </div>
        </div>
      </div>
    `;
  }
  return html`<${BootstrapContext.Provider} value=${value}>${children}<//>`;
}

export function useBootstrap() {
  return useContext(BootstrapContext).data;
}

export function useBootstrapRefresh() {
  return useContext(BootstrapContext).refresh;
}

// The bootstrap payload exposes the caller's organization in three
// places depending on whether the API serialised the org as a nested
// object, a top-level field, or only on the user record. We accept
// any of them.
//
// Returning ``null`` when none is present is intentional: the
// pre-fix shape coerced to the literal string ``'default'``, which
// any downstream caller would then send to the API as
// ``?organization_id=default``. That mirrors the M4 backend
// landmine — a degraded bootstrap (network error, partial response,
// session without an org) would silently scope every fetch to a
// shared bucket. Returning null forces backends and tests to
// reject the request explicitly. The api/client.js wrapper renders
// the failure as a session error rather than papering over it.
export function useOrgId() {
  const bootstrap = useBootstrap();
  const candidate = resolveBootstrapOrgId(bootstrap);
  if (!candidate && bootstrap) {
    // Bootstrap fetched, but no org — that's a hard error condition
    // (session without org). Log so it surfaces in the browser
    // console rather than coercing silently.
    // eslint-disable-next-line no-console
    console.error('[bootstrap] session has no organization_id', bootstrap);
  }
  return candidate;
}

export function useUserEmail() {
  const bootstrap = useBootstrap();
  return bootstrap?.current_user?.email || '';
}
