import { useCallback, useMemo } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { api } from '../api/client.js';
import { useBootstrap, useBootstrapRefresh, useOrgId, useUserEmail } from './BootstrapContext.js';
import { useToast } from './Toast.js';

/**
 * Single source of truth for the prop bundle every lifted page expects.
 *
 * The Gmail extension prop-drilled `{ api, bootstrap, toast, orgId,
 * userEmail, navigate, onRefresh, oauthBridge, routeId }` from the
 * InboxSDK shell into each route page. The SPA shell sources each of
 * those values from hooks instead, so every route adapter becomes a
 * one-liner:
 *
 *   export function VendorsRoute() {
 *     return html`<${VendorsPage} ...${usePageProps()} />`;
 *   }
 *
 * Per-page contracts vary (some need `onRefresh`, some don't), but
 * forwarding extras is harmless — Preact ignores unknown props.
 */
export function usePageProps() {
  const bootstrap = useBootstrap();
  const refreshBootstrap = useBootstrapRefresh();
  const orgId = useOrgId();
  const userEmail = useUserEmail();
  const [, navigate] = useLocation();
  const rawToast = useToast();

  const toast = useCallback((message, variant) => {
    if (variant && typeof variant === 'object') return rawToast(message, variant);
    return rawToast(message, { variant: variant || 'info' });
  }, [rawToast]);

  const apiAdapter = useCallback((path, opts = {}) => {
    const { silent: _silent, ...rest } = opts;
    return api(path, rest);
  }, []);

  // ConnectionsPage uses oauthBridge to coordinate OAuth popup auth
  // flows across the InboxSDK parent window. The SPA does full-page
  // OAuth redirects, so the bridge is a thin no-op.
  const oauthBridge = useMemo(() => ({
    open: (url) => { window.location.href = url; },
    close: () => {},
    on: () => () => {},
  }), []);

  return {
    api: apiAdapter,
    bootstrap,
    toast,
    orgId,
    userEmail,
    navigate,
    onRefresh: refreshBootstrap,
    oauthBridge,
    routeId: '',  // Page-specific route ids are unused in the SPA
  };
}
