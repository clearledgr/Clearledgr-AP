/**
 * OAuth bridge — opens OAuth flows in a popup window instead of navigating
 * Gmail away. Polls for popup close, then triggers data refresh.
 *
 * Used by SetupPage and ConnectionsPage for Slack, ERP, and Gmail OAuth.
 */

export function createOAuthBridge(onComplete) {
  let activePopup = null;
  let pollInterval = null;

  function startOAuth(authUrl, integrationName = 'integration') {
    if (activePopup && !activePopup.closed) {
      activePopup.focus();
      return;
    }

    activePopup = window.open(
      authUrl,
      'clearledgr_oauth',
      'width=600,height=700,left=200,top=100,toolbar=no,menubar=no'
    );

    if (!activePopup) {
      onComplete?.({ success: false, error: 'popup_blocked', integration: integrationName });
      return;
    }

    // Poll for popup close
    clearInterval(pollInterval);
    pollInterval = setInterval(() => {
      if (!activePopup || activePopup.closed) {
        clearInterval(pollInterval);
        pollInterval = null;
        activePopup = null;
        onComplete?.({ success: true, integration: integrationName });
      }
    }, 1000);
  }

  function cleanup() {
    clearInterval(pollInterval);
    if (activePopup && !activePopup.closed) activePopup.close();
    activePopup = null;
  }

  return { startOAuth, cleanup };
}
