// Clearledgr Configuration
const CONFIG = {
  BACKEND_URL: 'https://api.clearledgr.com',
  // Hub-and-spoke: the Gmail extension is the contextual companion;
  // the workspace SPA at WORKSPACE_URL is the system of record
  // (admin surfaces — Pipeline, Exceptions, Vendors, Reconciliation).
  // "Open in Console" deep-links from the sidebar/banners point here.
  // Switch to https://workspace.clearledgr.com once that domain's
  // Let's Encrypt cert clears.
  WORKSPACE_URL: 'https://web-app-production-a046.up.railway.app',
  APP_ID: 'sdk_Clearledgr2026_dc12c60472',
  VERSION: '1.2026.002 Phoenix'
};

if (typeof self !== 'undefined') {
  self.CONFIG = CONFIG;
  self.CLEARLEDGR_CONFIG = CONFIG;
}

if (typeof globalThis !== 'undefined') {
  globalThis.CONFIG = CONFIG;
  globalThis.CLEARLEDGR_CONFIG = CONFIG;
}
