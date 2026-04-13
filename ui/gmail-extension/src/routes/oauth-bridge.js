/**
 * OAuth bridge — opens OAuth flows in a popup and resolves when the
 * popup's callback page reports a result via postMessage. Falls back
 * to popup-close polling if postMessage is blocked (e.g., strict COOP).
 *
 * Messages the bridge listens for:
 *   - { type: 'clearledgr_oauth_complete', success, organizationId }
 *     — /auth/popup-complete (Google sign-in, Gmail scope upgrades)
 *   - { type: 'clearledgr_erp_oauth_complete', erp, success,
 *       organizationId, detail }
 *     — /erp/{quickbooks,xero}/callback
 *
 * onComplete receives a real payload: { success, integration, detail }.
 * Integration names come from the postMessage (`erp`) when present, or
 * from the `integrationName` passed to startOAuth().
 */

const OAUTH_MESSAGE_TYPES = new Set([
  'clearledgr_oauth_complete',
  'clearledgr_erp_oauth_complete',
]);

function integrationFromMessage(data, fallback) {
  if (!data) return fallback;
  if (data.type === 'clearledgr_erp_oauth_complete' && data.erp) {
    return String(data.erp);
  }
  return fallback;
}

export function createOAuthBridge(onComplete) {
  let activePopup = null;
  let pollInterval = null;
  let currentIntegration = null;
  let messageHandler = null;
  let resolved = false;

  function finish(payload) {
    if (resolved) return;
    resolved = true;
    clearInterval(pollInterval);
    pollInterval = null;
    if (messageHandler) {
      window.removeEventListener('message', messageHandler);
      messageHandler = null;
    }
    if (activePopup && !activePopup.closed) {
      try { activePopup.close(); } catch (_) { /* popup may be cross-origin */ }
    }
    activePopup = null;
    currentIntegration = null;
    try { onComplete?.(payload); } catch (_) { /* handler errors are theirs */ }
  }

  function startOAuth(authUrl, integrationName = 'integration') {
    if (activePopup && !activePopup.closed) {
      activePopup.focus();
      return;
    }

    currentIntegration = integrationName;
    resolved = false;

    activePopup = window.open(
      authUrl,
      'clearledgr_oauth',
      'width=600,height=700,left=200,top=100,toolbar=no,menubar=no'
    );

    if (!activePopup) {
      finish({ success: false, error: 'popup_blocked', integration: integrationName });
      return;
    }

    // Primary signal: the callback page postMessages its result.
    messageHandler = (event) => {
      const data = event && event.data;
      if (!data || typeof data !== 'object') return;
      if (!OAUTH_MESSAGE_TYPES.has(data.type)) return;
      finish({
        success: Boolean(data.success),
        integration: integrationFromMessage(data, currentIntegration),
        organizationId: data.organizationId || null,
        detail: data.detail || null,
      });
    };
    window.addEventListener('message', messageHandler);

    // Fallback: if postMessage is blocked (strict COOP / third-party
    // cookie rules), the popup still closes. Treat close-without-message
    // as "unknown" so the caller can decide whether to optimistically
    // refresh or keep waiting. integration-specific callers can
    // disambiguate by re-fetching state.
    clearInterval(pollInterval);
    pollInterval = setInterval(() => {
      if (!activePopup || activePopup.closed) {
        finish({
          success: true,
          integration: currentIntegration,
          detail: 'popup_closed_without_message',
        });
      }
    }, 1000);
  }

  function cleanup() {
    clearInterval(pollInterval);
    pollInterval = null;
    if (messageHandler) {
      window.removeEventListener('message', messageHandler);
      messageHandler = null;
    }
    if (activePopup && !activePopup.closed) {
      try { activePopup.close(); } catch (_) { /* cross-origin, ignore */ }
    }
    activePopup = null;
    currentIntegration = null;
  }

  return { startOAuth, cleanup };
}
