// Clearledgr Configuration
const CONFIG = {
  BACKEND_URL: 'http://127.0.0.1:8000',
  APP_ID: 'sdk_Clearledgr2026_dc12c60472',
  VERSION: '1.2026.001 Phoenix',
  DEFAULT_ORG_ID: 'default',
  DEFAULT_SLACK_CHANNEL: '#finance-approvals',
  AP_CONFIDENCE_THRESHOLD: 0.85,
  AP_AMOUNT_ANOMALY_THRESHOLD: 0.35,
  AP_DEBUG_UI: false
};

function normalizeBackendUrl(raw) {
  let backendUrl = String(raw || '').trim();
  if (!backendUrl) backendUrl = CONFIG.BACKEND_URL;
  if (!/^https?:\/\//i.test(backendUrl)) {
    backendUrl = `http://${backendUrl}`;
  }
  if (backendUrl.endsWith('/v1')) {
    backendUrl = backendUrl.slice(0, -3);
  }
  try {
    const parsed = new URL(backendUrl);
    if (parsed.hostname === 'localhost' || parsed.hostname === '0.0.0.0') {
      parsed.hostname = '127.0.0.1';
    }
    if (!parsed.port) parsed.port = '8000';
    return parsed.toString().replace(/\/+$/, '');
  } catch (_) {
    return CONFIG.BACKEND_URL;
  }
}

function buildRuntimeSettings(raw = {}) {
  return {
    backendUrl: normalizeBackendUrl(raw.backendUrl || raw.apiEndpoint || CONFIG.BACKEND_URL),
    organizationId: String(raw.organizationId || CONFIG.DEFAULT_ORG_ID).trim(),
    userEmail: raw.userEmail ? String(raw.userEmail).trim() : null,
    slackChannel: String(raw.slackChannel || CONFIG.DEFAULT_SLACK_CHANNEL).trim(),
    confidenceThreshold: Number(raw.confidenceThreshold ?? CONFIG.AP_CONFIDENCE_THRESHOLD),
    amountAnomalyThreshold: Number(raw.amountAnomalyThreshold ?? CONFIG.AP_AMOUNT_ANOMALY_THRESHOLD),
    erpWritebackEnabled: false
  };
}

function validateRuntimeConfig(raw = {}) {
  const settings = buildRuntimeSettings(raw);
  const errors = [];
  const warnings = [];

  if (!settings.backendUrl) errors.push('Backend URL is required.');
  if (!settings.organizationId) errors.push('Organization ID is required.');
  if (!settings.slackChannel) errors.push('Slack channel is required.');

  if (!Number.isFinite(settings.confidenceThreshold) || settings.confidenceThreshold <= 0 || settings.confidenceThreshold > 1) {
    errors.push('Confidence threshold must be between 0 and 1.');
  }
  if (!Number.isFinite(settings.amountAnomalyThreshold) || settings.amountAnomalyThreshold < 0 || settings.amountAnomalyThreshold > 5) {
    errors.push('Amount anomaly threshold must be between 0 and 5.');
  }

  if (!settings.userEmail) {
    warnings.push('User email is not configured. Actions will be attributed to extension user.');
  }

  return {
    valid: errors.length === 0,
    errors,
    warnings,
    settings
  };
}

if (typeof window !== 'undefined') {
  window.CLEARLEDGR_CONFIG = CONFIG;
  window.normalizeBackendUrl = normalizeBackendUrl;
  window.buildRuntimeSettings = buildRuntimeSettings;
  window.validateRuntimeConfig = validateRuntimeConfig;
}

if (typeof self !== 'undefined') {
  self.CLEARLEDGR_CONFIG = CONFIG;
  self.normalizeBackendUrl = normalizeBackendUrl;
  self.buildRuntimeSettings = buildRuntimeSettings;
  self.validateRuntimeConfig = validateRuntimeConfig;
}
