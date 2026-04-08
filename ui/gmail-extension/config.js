// Clearledgr Configuration
const CONFIG = {
  BACKEND_URL: 'https://api.clearledgr.com',
  APP_ID: 'sdk_Clearledgr2026_dc12c60472',
  VERSION: '1.2026.001 Phoenix'
};

if (typeof self !== 'undefined') {
  self.CONFIG = CONFIG;
  self.CLEARLEDGR_CONFIG = CONFIG;
}

if (typeof globalThis !== 'undefined') {
  globalThis.CONFIG = CONFIG;
  globalThis.CLEARLEDGR_CONFIG = CONFIG;
}
