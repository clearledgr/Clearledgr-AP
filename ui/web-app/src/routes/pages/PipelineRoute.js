import { useCallback } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { html } from '../../utils/htm.js';
import PipelinePage from './PipelinePage.js';
import { api } from '../../api/client.js';
import { useBootstrap, useOrgId, useUserEmail } from '../../shell/BootstrapContext.js';
import { useToast } from '../../shell/Toast.js';

/**
 * SPA wrapper around the lifted PipelinePage. Preserves the page's
 * existing prop contract (`{ api, bootstrap, toast, orgId, userEmail,
 * navigate }`) by sourcing each value from the SPA's hooks instead of
 * the InboxSDK injection it used inside the Gmail extension.
 *
 * This shape lets every other page lift in the same way: copy the
 * page file from the extension, wrap it with a route adapter that
 * binds the same six props, register it in App.js. No changes to the
 * page body required.
 */
export function PipelineRoute() {
  const bootstrap = useBootstrap();
  const orgId = useOrgId();
  const userEmail = useUserEmail();
  const [, locationSet] = useLocation();
  const rawToast = useToast();

  // The extension toast signature is `toast(msg, variant)`. The SPA's
  // is `toast(msg, opts)`. Adapt without touching the page body.
  const toast = useCallback((message, variant) => {
    if (variant && typeof variant === 'object') return rawToast(message, variant);
    return rawToast(message, { variant: variant || 'info' });
  }, [rawToast]);

  // Adapt the SPA api() to the extension's `silent` opt convention so
  // call sites that pass `{ silent: true }` continue to work — the SPA
  // version doesn't surface error toasts itself, so silent is a no-op
  // on this side, but accepting it prevents confusing strict checks.
  const apiAdapter = useCallback((path, opts = {}) => {
    const { silent: _silent, ...rest } = opts;
    return api(path, rest);
  }, []);

  return html`
    <${PipelinePage}
      api=${apiAdapter}
      bootstrap=${bootstrap}
      toast=${toast}
      orgId=${orgId}
      userEmail=${userEmail}
      navigate=${locationSet}
    />
  `;
}
