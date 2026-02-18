const DEFAULT_SETTINGS = {
  backendUrl: 'http://127.0.0.1:8000',
  organizationId: 'default',
  slackChannel: '#finance-approvals',
  confidenceThreshold: 0.85,
  amountAnomalyThreshold: 0.35,
  erpWritebackEnabled: false,
  notifications: true
};

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('settings-form');
  if (!form) return;
  form.addEventListener('submit', saveSettings);
  loadSettings();
});

function normalizeBackendUrl(raw) {
  let value = String(raw || '').trim();
  if (!value) value = DEFAULT_SETTINGS.backendUrl;
  if (!/^https?:\/\//i.test(value)) {
    value = `http://${value}`;
  }
  if (value.endsWith('/v1')) {
    value = value.slice(0, -3);
  }
  try {
    const parsed = new URL(value);
    if (parsed.hostname === 'localhost' || parsed.hostname === '0.0.0.0') {
      parsed.hostname = '127.0.0.1';
    }
    if (!parsed.port) {
      parsed.port = '8000';
    }
    return parsed.toString().replace(/\/+$/, '');
  } catch (_) {
    return DEFAULT_SETTINGS.backendUrl;
  }
}

function validateSettings(candidate) {
  const settings = {
    ...DEFAULT_SETTINGS,
    ...candidate
  };
  settings.backendUrl = normalizeBackendUrl(settings.backendUrl || settings.apiEndpoint);
  settings.organizationId = String(settings.organizationId || DEFAULT_SETTINGS.organizationId).trim();
  settings.slackChannel = String(settings.slackChannel || DEFAULT_SETTINGS.slackChannel).trim();
  settings.confidenceThreshold = Number(settings.confidenceThreshold);
  settings.amountAnomalyThreshold = Number(settings.amountAnomalyThreshold);
  settings.erpWritebackEnabled = false;

  if (!settings.organizationId) {
    return { valid: false, error: 'Organization ID is required.' };
  }
  if (!Number.isFinite(settings.confidenceThreshold) || settings.confidenceThreshold <= 0 || settings.confidenceThreshold > 1) {
    return { valid: false, error: 'Confidence threshold must be between 0 and 1.' };
  }
  if (!Number.isFinite(settings.amountAnomalyThreshold) || settings.amountAnomalyThreshold < 0 || settings.amountAnomalyThreshold > 5) {
    return { valid: false, error: 'Amount anomaly threshold must be between 0 and 5.' };
  }
  return { valid: true, settings };
}

function loadSettings() {
  chrome.storage.sync.get(['apiKey', 'settings', 'backendUrl'], (result) => {
    const settings = {
      ...DEFAULT_SETTINGS,
      ...(result.settings || {})
    };
    if (result.backendUrl) settings.backendUrl = result.backendUrl;

    const apiKeyInput = document.getElementById('apiKey');
    const endpointInput = document.getElementById('apiEndpoint');
    const notificationsInput = document.getElementById('notifications');

    if (apiKeyInput) apiKeyInput.value = result.apiKey || '';
    if (endpointInput) endpointInput.value = normalizeBackendUrl(settings.backendUrl);
    if (notificationsInput) notificationsInput.checked = settings.notifications !== false;
  });
}

function saveSettings(event) {
  event.preventDefault();

  const apiKey = String(document.getElementById('apiKey')?.value || '').trim();
  const apiEndpoint = String(document.getElementById('apiEndpoint')?.value || '').trim();
  const notifications = document.getElementById('notifications')?.checked ?? true;

  const validation = validateSettings({
    apiEndpoint,
    backendUrl: apiEndpoint,
    notifications
  });
  if (!validation.valid) {
    showStatus(validation.error, 'error');
    return;
  }

  const settings = {
    ...validation.settings,
    apiEndpoint: validation.settings.backendUrl
  };

  chrome.storage.sync.set({
    apiKey: apiKey || null,
    backendUrl: settings.backendUrl,
    settings
  }, () => {
    if (chrome.runtime.lastError) {
      showStatus(chrome.runtime.lastError.message || 'Unable to save settings.', 'error');
      return;
    }
    showStatus('Settings saved.', 'success');
    chrome.runtime.sendMessage({ action: 'SETTINGS_UPDATED', settings }).catch(() => {});
  });
}

function showStatus(message, type) {
  const statusEl = document.getElementById('status');
  if (!statusEl) return;
  statusEl.textContent = message;
  statusEl.className = `status ${type}`;
  statusEl.style.display = 'block';
  window.setTimeout(() => {
    statusEl.style.display = 'none';
  }, 3000);
}
