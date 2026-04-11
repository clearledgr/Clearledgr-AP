/**
 * Clearledgr AP v1 Queue Manager
 * AP intake, queue sync, and action dispatch.
 */
function normalizeBackendUrl(raw) {
  let url = String(raw || '').trim();
  if (!url) return '';
  if (!/^https?:\/\//i.test(url)) url = `http://${url}`;
  if (url.endsWith('/v1')) url = url.slice(0, -3);
  try {
    const parsed = new URL(url);
    if (parsed.hostname === '0.0.0.0' || parsed.hostname === 'localhost') {
      parsed.hostname = '127.0.0.1';
    }
    return parsed.toString().replace(/\/+$/, '');
  } catch (_) {
    return url.replace(/\/+$/, '');
  }
}

function selectBackendUrl(storedUrl, configuredUrl) {
  const configured = normalizeBackendUrl(configuredUrl);
  const stored = normalizeBackendUrl(storedUrl);
  if (!configuredUrl) return stored;
  if (!storedUrl) return configured;
  try {
    const configuredParsed = new URL(configured);
    const storedParsed = new URL(stored);
    const configuredHost = configuredParsed.hostname.toLowerCase();
    const storedHost = storedParsed.hostname.toLowerCase();
    const configuredSecure = configuredParsed.protocol === 'https:';
    const looksEphemeralStoredHost =
      storedHost === '127.0.0.1' ||
      storedHost === 'localhost' ||
      storedHost.endsWith('.trycloudflare.com') ||
      storedHost.endsWith('.up.railway.app');
    if (configuredSecure && configuredHost === 'api.clearledgr.com' && looksEphemeralStoredHost) {
      return configured;
    }
  } catch (_) {
    return configured || stored;
  }
  return stored;
}

function shouldClearStoredBackendOverride(storedUrl, configuredUrl) {
  const configured = normalizeBackendUrl(configuredUrl);
  const stored = normalizeBackendUrl(storedUrl);
  if (!configuredUrl || !storedUrl) return false;
  try {
    const configuredParsed = new URL(configured);
    const storedParsed = new URL(stored);
    const configuredHost = configuredParsed.hostname.toLowerCase();
    const storedHost = storedParsed.hostname.toLowerCase();
    return configuredParsed.protocol === 'https:' && configuredHost === 'api.clearledgr.com' && (
      storedHost === '127.0.0.1' ||
      storedHost === 'localhost' ||
      storedHost.endsWith('.trycloudflare.com') ||
      storedHost.endsWith('.up.railway.app')
    );
  } catch (_) {
    return false;
  }
}

async function clearStoredBackendOverride(data, nested, configuredBackendUrl) {
  const storedBackendUrl = data.backendUrl || nested.backendUrl || nested.apiEndpoint || null;
  if (!shouldClearStoredBackendOverride(storedBackendUrl, configuredBackendUrl)) return;
  const nextNested = { ...nested };
  delete nextNested.backendUrl;
  delete nextNested.apiEndpoint;
  try {
    await chrome.storage.sync.set({ settings: nextNested });
    await chrome.storage.sync.remove(['backendUrl']);
  } catch (_) {
    /* best effort */
  }
}

class ClearledgrQueueManager {
  constructor() {
    this.queue = [];
    this.listeners = [];
    this.currentUserRole = null;
    this.scanStatus = {
      state: 'initializing',
      mode: 'dom',
      lastScanAt: null,
      candidates: 0,
      added: 0,
      error: null
    };
    this.processedIds = new Set();
    this.runtimeConfig = null;
    this.scanTimer = null;
    this.backendSyncTimer = null;
    this.scanInFlight = false;
    this.apScan = {
      nextPageToken: null
    };
    this.authPrompted = false;
    this.authInFlight = false;
    this.backendAuthToken = null;
    this.backendAuthTokenExpiry = 0;
    this.backendAuthOrgId = null;
    this.backendAuthRequired = false;
    this.lastBackendAuthFailureAt = 0;
    this.backendAuthRetryCooldownMs = 30000;
    this.interactiveAuthCooldownMs = 60000;
    this.lastInteractiveAuthAttemptAt = 0;
    this.debugManualScan = false;
    this.autopilotStatus = null;
    this.auditCache = new Map();
    this.auditRequests = new Map();
    this.sourcesByItem = new Map();
    this.contextByItem = new Map();
    this.sourceRequests = new Map();
    this.contextRequests = new Map();
    this.tasksByItem = new Map();
    this.taskRequests = new Map();
    this.notesByItem = new Map();
    this.noteRequests = new Map();
    this.commentsByItem = new Map();
    this.commentRequests = new Map();
    this.filesByItem = new Map();
    this.fileRequests = new Map();
    this.kpiSnapshot = null;
    this.kpiUpdatedAt = null;
    this.kpiRequest = null;
  }

  static STATES = {
    RECEIVED: 'received',
    VALIDATED: 'validated',
    NEEDS_INFO: 'needs_info',
    NEEDS_APPROVAL: 'needs_approval',
    APPROVED: 'approved',
    READY_TO_POST: 'ready_to_post',
    POSTED_TO_ERP: 'posted_to_erp',
    CLOSED: 'closed',
    REJECTED: 'rejected',
    FAILED_POST: 'failed_post'
  };

  static ACTION_STATES = {
    request_approval: ['validated']
  };

  onQueueUpdated(callback) {
    if (typeof callback === 'function') this.listeners.push(callback);
  }

  emitQueueUpdated() {
    this.listeners.forEach((callback) => {
      try {
        callback(
          this.queue,
          this.scanStatus,
          new Map(),
          [],
          new Map(),
          this.sourcesByItem,
          this.contextByItem,
          this.tasksByItem,
          this.notesByItem,
          this.commentsByItem,
          this.filesByItem,
          this.kpiSnapshot
        );
      } catch (_) {
        // ignore
      }
    });
  }

  getQueue() {
    return Array.isArray(this.queue) ? [...this.queue] : [];
  }

  isDebugUiEnabled() {
    return Boolean(this.debugManualScan);
  }

  getItemByThreadId(threadId) {
    if (!threadId) return null;
    return this.queue.find((item) => (
      item.thread_id === threadId
      || item.threadId === threadId
      || item.message_id === threadId
      || item.messageId === threadId
    )) || null;
  }

  buildItemLocator(item) {
    const primary = item?.primary_source || {};
    const apItemId = String(item?.id || item?.ap_item_id || '').trim();
    const emailId = String(
      item?.thread_id
      || item?.threadId
      || item?.message_id
      || item?.messageId
      || primary?.thread_id
      || primary?.message_id
      || apItemId
    ).trim();
    return {
      ap_item_id: apItemId || undefined,
      email_id: emailId || undefined,
    };
  }

  getSourcesForItem(itemId) {
    if (!itemId) return [];
    return this.sourcesByItem.get(itemId) || [];
  }

  getContextForItem(itemId) {
    if (!itemId) return null;
    return this.contextByItem.get(itemId) || null;
  }

  getKpiSnapshot() {
    return this.kpiSnapshot || null;
  }

  getUiActionDisabledReason(action, state) {
    const allowed = ClearledgrQueueManager.ACTION_STATES[action] || [];
    if (!state) return 'Action unavailable';
    if (!allowed.includes(state)) return 'Action unavailable';
    return '';
  }

  getSeverityRank(severity) {
    const normalized = String(severity || '').trim().toLowerCase();
    if (normalized === 'critical') return 4;
    if (normalized === 'high') return 3;
    if (normalized === 'medium') return 2;
    if (normalized === 'low') return 1;
    return 0;
  }

  getPriorityScore(item) {
    const explicit = Number(item?.priority_score);
    if (Number.isFinite(explicit)) return explicit;
    const severityRank = this.getSeverityRank(item?.exception_severity);
    const state = String(item?.state || '').toLowerCase();
    let score = severityRank * 100;
    if (state === 'failed_post') score += 45;
    else if (state === 'needs_info') score += 40;
    else if (state === 'needs_approval') score += 30;
    else if (state === 'approved') score += 20;
    if (item?.navigator?.sla_breached) score += 30;
    const urgency = String(item?.navigator?.urgency || '').toLowerCase();
    if (urgency === 'urgent') score += 25;
    else if (urgency === 'elevated') score += 12;
    return score;
  }

  sortQueueItems(items) {
    const list = Array.isArray(items) ? [...items] : [];
    list.sort((left, right) => {
      const rightScore = this.getPriorityScore(right);
      const leftScore = this.getPriorityScore(left);
      if (rightScore !== leftScore) return rightScore - leftScore;
      const rightCreated = Date.parse(String(right?.created_at || right?.updated_at || '')) || 0;
      const leftCreated = Date.parse(String(left?.created_at || left?.updated_at || '')) || 0;
      return rightCreated - leftCreated;
    });
    return list;
  }

  parseMetadata(raw) {
    if (!raw) return {};
    if (typeof raw === 'object') return raw;
    if (typeof raw === 'string') {
      try {
        return JSON.parse(raw);
      } catch (_) {
        return {};
      }
    }
    return {};
  }

  async init() {
    await this.loadProcessedIds();
    this.runtimeConfig = await this.getSyncConfig();
    await this.loadBackendAuthFromStorage();
    this.debugManualScan = Boolean(this.runtimeConfig?.debugManualScan);

    if (!this.runtimeConfig.valid) {
      this.setScanStatus({
        state: 'blocked',
        mode: 'setup_required',
        error: this.runtimeConfig.errors?.[0] || 'setup_invalid'
      });
      return;
    }

    await this.ensureBackendAuthIfNeeded();
    const synced = await this.syncQueueWithBackend({ updateStatus: false });
    this.applyRuntimeStatus({ synced, extra: { lastScanAt: Date.now() } });
    if (this.scanStatus.state === 'auth_required') {
      void this.ensureBackendAuthIfNeeded();
    }
    this.startBackendSync();
    this.startPeriodicScan();
    if (this.debugManualScan) {
      await this.scanNow('debug');
    }
  }

  async safeSendMessage(message, { timeoutMs = 6000 } = {}) {
    if (!chrome.runtime?.id || typeof chrome.runtime.sendMessage !== 'function') {
      return { success: false, error: 'runtime_unavailable' };
    }

    return new Promise((resolve) => {
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        resolve(value);
      };

      const timeoutId = setTimeout(() => {
        finish({ success: false, error: 'runtime_message_timeout' });
      }, Math.max(1000, Number(timeoutMs) || 6000));

      try {
        chrome.runtime.sendMessage(message, (response) => {
          clearTimeout(timeoutId);
          const runtimeError = chrome.runtime?.lastError?.message;
          if (runtimeError) {
            finish({ success: false, error: `runtime_message_failed:${runtimeError}` });
            return;
          }
          finish(response ?? null);
        });
      } catch (error) {
        clearTimeout(timeoutId);
        finish({ success: false, error: error?.message || 'runtime_message_failed' });
      }
    });
  }

  async ensureGmailAuth(interactive = true, attempt = 0) {
    const authTimeoutMs = interactive ? 180000 : 30000;
    const result = await this.safeSendMessage({
      action: 'ensureGmailAuth',
      interactive: !!interactive
    }, {
      timeoutMs: authTimeoutMs
    });
    const retryableRuntimeFailure = String(result?.error || '').startsWith('runtime_message_');
    if ((!result || retryableRuntimeFailure) && attempt < 2) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      return this.ensureGmailAuth(interactive, attempt + 1);
    }
    if (!result || result.success === false) {
      const retryAfterSeconds = Number(result?.retryAfterSeconds || result?.retry_after_seconds || 0) || 0;
      this.setScanStatus({
        state: 'auth_required',
        mode: 'gmail_oauth',
        error: result?.error || 'auth_required'
      });
      return {
        success: false,
        error: result?.error || 'auth_required',
        retry_after_seconds: retryAfterSeconds,
      };
    }
    this.setScanStatus({
      state: 'idle',
      mode: 'gmail_api',
      error: null
    });
    return result;
  }

  async getSyncConfig() {
    const data = await new Promise((resolve) => {
      chrome.storage.sync.get([
        'settings',
        'backendUrl',
        'organizationId',
        'userEmail',
        'slackChannel',
        'financeLeadEmail',
        'backendApiKey'
      ], resolve);
    });
    const nested = data.settings || {};
    const globalDebugDefault =
      Boolean((typeof window !== 'undefined' && window.CLEARLEDGR_CONFIG?.AP_DEBUG_UI)) ||
      Boolean((typeof globalThis !== 'undefined' && globalThis.CLEARLEDGR_CONFIG?.AP_DEBUG_UI));

    const raw = {
      ...nested,
      backendUrl: data.backendUrl || nested.backendUrl || nested.apiEndpoint || null,
      organizationId: data.organizationId || nested.organizationId || null,
      userEmail: data.userEmail || nested.userEmail || null,
      slackChannel: data.slackChannel || nested.slackChannel || null,
      financeLeadEmail: data.financeLeadEmail || nested.financeLeadEmail || null,
      backendApiKey: data.backendApiKey || nested.backendApiKey || nested.apiKey || null,
      debugManualScan: nested.debugManualScan ?? globalDebugDefault,
      authEntryMode: nested.authEntryMode || null
    };

    const validator =
      (typeof window !== 'undefined' && window.validateRuntimeConfig) ||
      (typeof globalThis !== 'undefined' && globalThis.validateRuntimeConfig);

    if (typeof validator === 'function') {
      const validation = validator(raw);
      return {
        ...validation.settings,
        backendApiKey: String(raw.backendApiKey || '').trim() || null,
        valid: Boolean(validation.valid),
        errors: Array.isArray(validation.errors) ? validation.errors : [],
        warnings: Array.isArray(validation.warnings) ? validation.warnings : [],
        debugManualScan: Boolean(raw.debugManualScan),
        authEntryMode: String(raw.authEntryMode || 'routed').trim().toLowerCase() === 'inline'
          ? 'inline'
          : 'routed'
      };
    }

    const extensionConfig =
      (typeof window !== 'undefined' && (window.CLEARLEDGR_CONFIG || window.CONFIG))
      || (typeof globalThis !== 'undefined' && (globalThis.CLEARLEDGR_CONFIG || globalThis.CONFIG))
      || {};
    const configuredBackendUrl = String(
      extensionConfig.API_URL || extensionConfig.BACKEND_URL || ''
    ).trim();
    const configuredApiKey = String(
      extensionConfig.BACKEND_API_KEY || extensionConfig.API_KEY || ''
    ).trim();
    const configuredAuthEntryMode = String(
      extensionConfig.AUTH_ENTRY_MODE || extensionConfig.SIDEBAR_AUTH_ENTRY_MODE || ''
    ).trim().toLowerCase();
    await clearStoredBackendOverride(data, nested, configuredBackendUrl);
    const backendUrl = selectBackendUrl(
      raw.backendUrl,
      configuredBackendUrl || 'http://127.0.0.1:8010'
    ) || 'http://127.0.0.1:8010';
    const authEntryMode = String(raw.authEntryMode || configuredAuthEntryMode || 'inline')
      .trim()
      .toLowerCase() === 'inline'
      ? 'inline'
      : 'routed';
    return {
      backendUrl,
      organizationId: String(raw.organizationId || 'default').trim(),
      userEmail: raw.userEmail || null,
      slackChannel: String(raw.slackChannel || '#finance-approvals').trim(),
      financeLeadEmail: raw.financeLeadEmail || null,
      backendApiKey: String(raw.backendApiKey || configuredApiKey || '').trim() || null,
      authEntryMode,
      confidenceThreshold: 0.85,
      amountAnomalyThreshold: 0.35,
      erpWritebackEnabled: false,
      debugManualScan: Boolean(raw.debugManualScan),
      valid: Boolean(backendUrl),
      errors: backendUrl ? [] : ['Backend URL is required.'],
      warnings: []
    };
  }

  async loadBackendAuthFromStorage() {
    const stored = await chrome.storage.local.get([
      'clearledgr_backend_access_token',
      'clearledgr_backend_token_expiry',
      'clearledgr_backend_org_id'
    ]);
    this.backendAuthToken = String(stored.clearledgr_backend_access_token || '').trim() || null;
    this.backendAuthTokenExpiry = Number(stored.clearledgr_backend_token_expiry || 0) || 0;
    this.backendAuthOrgId = String(stored.clearledgr_backend_org_id || '').trim() || null;
    if (!this.isBackendAuthTokenValid()) {
      this.clearBackendAuthToken();
    }
  }

  async persistBackendAuthToken() {
    if (!this.backendAuthToken) {
      await chrome.storage.local.remove([
        'clearledgr_backend_access_token',
        'clearledgr_backend_token_expiry',
        'clearledgr_backend_org_id'
      ]);
      return;
    }
    await chrome.storage.local.set({
      clearledgr_backend_access_token: this.backendAuthToken,
      clearledgr_backend_token_expiry: this.backendAuthTokenExpiry || 0,
      clearledgr_backend_org_id: this.backendAuthOrgId || (this.runtimeConfig?.organizationId || 'default'),
    });
  }

  clearBackendAuthToken() {
    this.backendAuthToken = null;
    this.backendAuthTokenExpiry = 0;
    this.backendAuthOrgId = null;
    void chrome.storage.local.remove([
      'clearledgr_backend_access_token',
      'clearledgr_backend_token_expiry',
      'clearledgr_backend_org_id'
    ]);
  }

  isBackendAuthTokenValid() {
    if (!this.backendAuthToken) return false;
    if (!this.backendAuthTokenExpiry) return true;
    return Date.now() < Math.max(0, this.backendAuthTokenExpiry - 15_000);
  }

  hasBackendCredential() {
    return this.isBackendAuthTokenValid() || Boolean(this.runtimeConfig?.backendApiKey);
  }

  getBackendAuthHeaders(existingHeaders = {}) {
    const headers = {};
    if (existingHeaders && typeof existingHeaders === 'object') {
      Object.entries(existingHeaders).forEach(([key, value]) => {
        if (value !== undefined && value !== null) headers[key] = value;
      });
    }
    if (!headers['Authorization'] && this.isBackendAuthTokenValid()) {
      headers['Authorization'] = `Bearer ${this.backendAuthToken}`;
    }
    if (!headers['X-API-Key'] && this.runtimeConfig?.backendApiKey) {
      headers['X-API-Key'] = this.runtimeConfig.backendApiKey;
    }
    return headers;
  }

  async backendFetch(url, init = {}, options = {}) {
    const retryOnAuth = options?.retryOnAuth !== false;
    const suppressRefresh = options?.suppressRefresh === true;
    if (
      !suppressRefresh
      && !this.hasBackendCredential()
      && !this.authInFlight
      && !this.isBackendAuthCoolingDown()
    ) {
      await this.ensureBackendAuth({ force: false, interactive: false });
    }
    const rawHeaders = (init && typeof init === 'object' && init.headers) ? init.headers : {};
    const headers = this.getBackendAuthHeaders(rawHeaders);
    const requestInit = {
      ...(init || {}),
      headers
    };
    const response = await fetch(url, requestInit);
    if (response.status !== 401) {
      this.backendAuthRequired = false;
      return response;
    }

    this.backendAuthRequired = true;
    this.clearBackendAuthToken();

    if (!retryOnAuth || suppressRefresh || this.authInFlight) {
      return response;
    }

    if (this.isBackendAuthCoolingDown()) {
      return response;
    }

    const authResult = await this.ensureBackendAuth({ force: true, interactive: false });
    if (!authResult?.success || !this.hasBackendCredential()) return response;

    const retryHeaders = this.getBackendAuthHeaders(rawHeaders);
    return fetch(url, {
      ...(init || {}),
      headers: retryHeaders
    });
  }

  setScanStatus(update) {
    this.scanStatus = {
      ...this.scanStatus,
      ...(update || {}),
      lastScanAt: update?.lastScanAt || this.scanStatus.lastScanAt
    };
    this.emitQueueUpdated();
  }

  async loadProcessedIds() {
    const stored = await chrome.storage.local.get(['clearledgr_processed_ids']);
    const ids = stored.clearledgr_processed_ids || [];
    ids.forEach((id) => this.processedIds.add(id));
  }

  async saveProcessedIds() {
    await chrome.storage.local.set({ clearledgr_processed_ids: Array.from(this.processedIds).slice(-2000) });
  }

  startPeriodicScan() {
    if (this.scanTimer) clearInterval(this.scanTimer);
    this.scanTimer = setInterval(() => {
      this.scanNow('auto');
    }, 60000);
  }

  startBackendSync() {
    if (this.backendSyncTimer) clearInterval(this.backendSyncTimer);
    this.backendSyncTimer = setInterval(async () => {
      const synced = await this.syncQueueWithBackend({ updateStatus: false });
      this.applyRuntimeStatus({ synced, extra: { lastScanAt: Date.now() } });
    }, 30000);
  }

  async scanNow(source = 'auto') {
    if (this.scanInFlight || !this.runtimeConfig?.valid) return;
    this.scanInFlight = true;
    try {
      this.setScanStatus({ state: 'scanning', mode: 'backend_api', error: null });
      const backendSynced = await this.syncQueueWithBackend({ updateStatus: false });
      this.applyRuntimeStatus({
        synced: backendSynced,
        extra: {
          candidates: Array.isArray(this.queue) ? this.queue.length : 0,
          added: 0,
          lastScanAt: Date.now()
        }
      });
    } finally {
      this.scanInFlight = false;
    }
  }

  upsertQueueItem(item, gmailMeta = null) {
    if (!item) return;
    const normalizedItem = this.normalizeWorklistItem(item);
    const existingIndex = this.queue.findIndex((entry) => entry.id === normalizedItem.id || entry.invoice_key === normalizedItem.invoice_key);
    const merged = { ...normalizedItem };
    if (gmailMeta) {
      merged.subject = merged.subject || gmailMeta.subject || null;
      merged.sender = merged.sender || gmailMeta.sender || null;
      merged.received_at = merged.received_at || gmailMeta.date || null;
    }
    if (existingIndex >= 0) {
      this.queue[existingIndex] = { ...this.queue[existingIndex], ...merged };
    } else {
      this.queue.push(merged);
    }
    this.queue = this.sortQueueItems(this.queue);
  }

  normalizeWorklistItem(item) {
    const normalized = { ...(item || {}) };
    const primary = normalized.primary_source || {};
    const metadata = this.parseMetadata(normalized.metadata);
    if (!normalized.thread_id && primary.thread_id) normalized.thread_id = primary.thread_id;
    if (!normalized.message_id && primary.message_id) normalized.message_id = primary.message_id;
    if (!normalized.currency && metadata.currency) normalized.currency = metadata.currency;
    normalized.currency = String(normalized.currency || '').trim().toUpperCase() || null;
    if (normalized.source_count === undefined || normalized.source_count === null) {
      normalized.source_count = 0;
    }
    normalized.has_context_conflict = Boolean(normalized.has_context_conflict);
    normalized.has_attachment = Boolean(normalized.has_attachment || Number(normalized.attachment_count || 0) > 0);
    normalized.attachment_count = Math.max(0, Number(normalized.attachment_count || 0) || 0);
    normalized.exception_code = normalized.exception_code || null;
    normalized.exception_severity = normalized.exception_severity || null;
    normalized.budget_status = normalized.budget_status || null;
    normalized.budget_requires_decision = Boolean(normalized.budget_requires_decision);
    normalized.entity_routing = normalized.entity_routing && typeof normalized.entity_routing === 'object'
      ? normalized.entity_routing
      : {};
    normalized.entity_routing_status = normalized.entity_routing_status || normalized.entity_routing?.status || 'not_needed';
    normalized.entity_candidates = Array.isArray(normalized.entity_candidates)
      ? normalized.entity_candidates
      : (Array.isArray(normalized.entity_routing?.candidates) ? normalized.entity_routing.candidates : []);
    normalized.approval_followup = normalized.approval_followup && typeof normalized.approval_followup === 'object'
      ? normalized.approval_followup
      : {};
    normalized.approval_pending_assignees = Array.isArray(normalized.approval_pending_assignees)
      ? normalized.approval_pending_assignees
      : (Array.isArray(normalized.approval_followup?.pending_assignees) ? normalized.approval_followup.pending_assignees : []);
    normalized.risk_signals = normalized.risk_signals || {};
    normalized.source_ranking = normalized.source_ranking || {};
    normalized.navigator = normalized.navigator || {};
    normalized.conflict_actions = Array.isArray(normalized.conflict_actions) ? normalized.conflict_actions : [];
    const priorityScore = Number(normalized.priority_score);
    normalized.priority_score = Number.isFinite(priorityScore)
      ? priorityScore
      : this.getPriorityScore(normalized);
    return normalized;
  }

  async fetchItemSources(apItemId, { force = false } = {}) {
    if (!apItemId || !this.runtimeConfig?.backendUrl) return [];
    if (!force && this.sourcesByItem.has(apItemId)) {
      return this.sourcesByItem.get(apItemId) || [];
    }
    if (this.sourceRequests.has(apItemId)) {
      return this.sourceRequests.get(apItemId);
    }

    const request = (async () => {
      try {
        const response = await this.backendFetch(
          `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(apItemId)}/sources`,
          { method: 'GET' }
        );
        if (!response.ok) return [];
        const payload = await response.json();
        const sources = Array.isArray(payload?.sources) ? payload.sources : [];
        this.sourcesByItem.set(apItemId, sources);
        this.emitQueueUpdated();
        return sources;
      } catch (_) {
        return [];
      } finally {
        this.sourceRequests.delete(apItemId);
      }
    })();

    this.sourceRequests.set(apItemId, request);
    return request;
  }

  async fetchItemContext(apItemId, { refresh = false } = {}) {
    if (!apItemId || !this.runtimeConfig?.backendUrl) return null;
    if (!refresh && this.contextByItem.has(apItemId)) {
      return this.contextByItem.get(apItemId);
    }
    if (this.contextRequests.has(apItemId)) {
      return this.contextRequests.get(apItemId);
    }

    const request = (async () => {
      try {
        const url = new URL(`${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(apItemId)}/context`);
        if (refresh) url.searchParams.set('refresh', 'true');
        const response = await this.backendFetch(url.toString(), { method: 'GET' });
        if (!response.ok) return null;
        const payload = await response.json();
        this.contextByItem.set(apItemId, payload || null);
        this.emitQueueUpdated();
        return payload || null;
      } catch (_) {
        return null;
      } finally {
        this.contextRequests.delete(apItemId);
      }
    })();

    this.contextRequests.set(apItemId, request);
    return request;
  }

  async hydrateItemContext(apItemId, { refresh = false } = {}) {
    if (!apItemId) return { sources: [], context: null };
    const [sources, context] = await Promise.all([
      this.fetchItemSources(apItemId, { force: refresh }),
      this.fetchItemContext(apItemId, { refresh })
    ]);
    return { sources, context };
  }

  async fetchApKpis({ force = false } = {}) {
    // Gmail Work UI no longer polls ops metrics.
    // Keep a stable no-op for compatibility with existing listeners.
    if (force) this.emitQueueUpdated();
    return this.kpiSnapshot;
  }

  async syncQueueWithBackend({ updateStatus = false } = {}) {
    if (!this.runtimeConfig?.backendUrl) return false;
    try {
      const org = encodeURIComponent(this.runtimeConfig.organizationId || 'default');
      const worklistUrl = `${this.runtimeConfig.backendUrl}/extension/worklist?organization_id=${org}`;
      const worklistResponse = await this.backendFetch(worklistUrl, { method: 'GET' });

      let items = [];
      if (worklistResponse.ok) {
        const payload = await worklistResponse.json();
        items = Array.isArray(payload?.items) ? payload.items.map((item) => this.normalizeWorklistItem(item)) : [];
      } else {
        throw new Error(`worklist_${worklistResponse.status}`);
      }

      this.queue = this.sortQueueItems(items);
      if (updateStatus) {
        this.setScanStatus({
          state: 'idle',
          mode: 'backend_api',
          error: null,
          lastScanAt: Date.now()
        });
      }
      this.emitQueueUpdated();
      return true;
    } catch (error) {
      if (updateStatus) {
        this.setScanStatus({ state: 'error', mode: 'backend_api', error: error.message || 'worklist_unavailable' });
      }
      return false;
    }
  }

  async fetchAutopilotStatus() {
    // Gmail Work UI no longer depends on ops autopilot polling.
    return this.autopilotStatus || null;
  }

  applyRuntimeStatus({ synced, autopilot, extra = {} } = {}) {
    const _unused = autopilot; // compatibility: callers may still pass this key
    void _unused;
    const mergedExtra = {
      ...extra
    };
    if (!mergedExtra.lastScanAt) mergedExtra.lastScanAt = Date.now();

    if (this.backendAuthRequired) {
      this.setScanStatus({
        state: 'auth_required',
        mode: 'backend_auth',
        error: 'auth_required',
        ...mergedExtra
      });
      void this.ensureBackendAuthIfNeeded();
      return;
    }

    if (!synced) {
      this.setScanStatus({
        state: 'error',
        mode: 'backend_api',
        error: 'backend_unreachable',
        ...mergedExtra
      });
      return;
    }

    this.setScanStatus({
      state: 'idle',
      mode: 'backend_api',
      error: null,
      ...mergedExtra
    });
  }

  async ensureBackendAuthIfNeeded() {
    return this.ensureBackendAuth({ force: false, interactive: false });
  }

  async authorizeGmailNow() {
    return this.ensureBackendAuth({ force: true, interactive: true });
  }

  getInteractiveAuthRetryAfterSeconds(now = Date.now()) {
    const nextAllowedAt = this.lastInteractiveAuthAttemptAt + this.interactiveAuthCooldownMs;
    const retryAfterMs = Math.max(0, nextAllowedAt - now);
    return Math.ceil(retryAfterMs / 1000);
  }

  getBackendAuthRetryAfterSeconds(now = Date.now()) {
    const nextAllowedAt = this.lastBackendAuthFailureAt + this.backendAuthRetryCooldownMs;
    const retryAfterMs = Math.max(0, nextAllowedAt - now);
    return Math.ceil(retryAfterMs / 1000);
  }

  isBackendAuthCoolingDown(now = Date.now()) {
    if (!this.lastBackendAuthFailureAt) return false;
    return (now - this.lastBackendAuthFailureAt) < this.backendAuthRetryCooldownMs;
  }

  isInteractiveAuthCoolingDown(now = Date.now()) {
    if (!this.lastInteractiveAuthAttemptAt) return false;
    return (now - this.lastInteractiveAuthAttemptAt) < this.interactiveAuthCooldownMs;
  }

  describeAuthResult(result = {}) {
    if (result?.success) {
      return { toast: 'Gmail authorized. Autopilot is resuming.', severity: 'success' };
    }
    const code = String(result?.error || 'authorization_failed').trim().toLowerCase();
    if (code === 'interactive_auth_cooldown') {
      const retryAfter = Number(result?.retry_after_seconds || this.getInteractiveAuthRetryAfterSeconds());
      return {
        toast: `Authorization already started. Try again in ${Math.max(1, retryAfter)}s.`,
        severity: 'warning',
      };
    }
    if (code === 'backend_auth_cooldown') {
      const retryAfter = Number(result?.retry_after_seconds || this.getBackendAuthRetryAfterSeconds());
      return {
        toast: `Clearledgr sign-in is cooling down after repeated failures. Try again in ${Math.max(1, retryAfter)}s.`,
        severity: 'warning',
      };
    }
    if (code === 'auth_unavailable' || code === 'auth_in_progress') {
      return { toast: 'Authorization is already in progress.', severity: 'warning' };
    }
    if (code.includes('redirect_uri_mismatch')) {
      return { toast: 'OAuth redirect URI mismatch. Fix OAuth settings in Workspace Shell Integrations.', severity: 'error' };
    }
    if (code.includes('invalid_client')) {
      return { toast: 'OAuth client configuration is invalid. Verify Gmail integration settings.', severity: 'error' };
    }
    if (code === 'backend_auth_token_missing') {
      return { toast: 'Gmail token was received, but backend sign-in failed. Open Integrations to reconnect.', severity: 'error' };
    }
    if (code.includes('network') || code.includes('fetch') || code.includes('backend_unreachable')) {
      return { toast: 'Authorization failed because backend is unreachable. Check backend and retry.', severity: 'error' };
    }
    if (code === 'auth_required' || code === 'authorization_failed') {
      return { toast: 'Authorization failed. Try again or reconnect from Workspace Shell Integrations.', severity: 'error' };
    }
    return { toast: `Authorization failed: ${code}`, severity: 'error' };
  }

  async ensureBackendAuth({ force = false, interactive = false } = {}) {
    if (!this.runtimeConfig?.valid) return { success: false, error: 'auth_unavailable' };
    if (this.authInFlight) return { success: false, error: 'auth_in_progress' };
    const now = Date.now();
    if (!interactive && this.isBackendAuthCoolingDown(now)) {
      return {
        success: false,
        error: 'backend_auth_cooldown',
        retry_after_seconds: this.getBackendAuthRetryAfterSeconds(now),
      };
    }
    if (interactive && this.isInteractiveAuthCoolingDown(now)) {
      return {
        success: false,
        error: 'interactive_auth_cooldown',
        retry_after_seconds: this.getInteractiveAuthRetryAfterSeconds(now),
      };
    }
    if (!force && this.authPrompted) return { success: false, error: 'auth_already_prompted' };
    if (!force) this.authPrompted = true;
    if (interactive) this.lastInteractiveAuthAttemptAt = now;
    this.authInFlight = true;
    let authCompleted = false;

    try {
      // Only explicit user actions should open interactive OAuth windows.
      // Automatic retries (e.g. 401 recovery) must stay non-interactive.
      const result = await this.ensureGmailAuth(Boolean(interactive));
      if (!result?.success) return result || { success: false, error: 'auth_required' };

      const backendAccessToken = String(result?.backendAccessToken || '').trim();
      if (backendAccessToken) {
        const expiresInSeconds = Number(result?.backendExpiresIn || 0) || 3600;
        this.backendAuthToken = backendAccessToken;
        this.backendAuthTokenExpiry = Date.now() + Math.max(60, expiresInSeconds) * 1000;
        this.backendAuthOrgId = String(result?.organizationId || this.runtimeConfig?.organizationId || 'default').trim();
        await this.persistBackendAuthToken();
      }

      if (!this.hasBackendCredential()) {
        this.backendAuthRequired = true;
        this.lastBackendAuthFailureAt = Date.now();
        return { success: false, error: 'backend_auth_token_missing' };
      }

      this.backendAuthRequired = false;

      this.authPrompted = false;
      const synced = await this.syncQueueWithBackend({ updateStatus: false });
      this.applyRuntimeStatus({
        synced,
        extra: { lastScanAt: Date.now() }
      });
      authCompleted = true;
      return {
        success: true,
        backendAccessToken: this.backendAuthToken || null,
        organizationId: this.backendAuthOrgId || (this.runtimeConfig?.organizationId || 'default')
      };
    } finally {
      if (!authCompleted) {
        this.lastBackendAuthFailureAt = Date.now();
        this.backendAuthRequired = true;
        this.authPrompted = false;
      }
      this.authInFlight = false;
    }
  }

  async refreshQueue() {
    const synced = await this.syncQueueWithBackend({ updateStatus: false });
    this.applyRuntimeStatus({
      synced,
      extra: { lastScanAt: Date.now() }
    });
  }

  invalidateItemCaches(apItemId) {
    if (!apItemId) return;
    this.auditCache.delete(apItemId);
    this.auditRequests.delete(apItemId);
    this.contextByItem.delete(apItemId);
    this.contextRequests.delete(apItemId);
    this.sourcesByItem.delete(apItemId);
    this.sourceRequests.delete(apItemId);
    this.tasksByItem.delete(apItemId);
    this.taskRequests.delete(apItemId);
    this.notesByItem.delete(apItemId);
    this.noteRequests.delete(apItemId);
    this.commentsByItem.delete(apItemId);
    this.commentRequests.delete(apItemId);
    this.filesByItem.delete(apItemId);
    this.fileRequests.delete(apItemId);
  }

  async fetchAuditTrail(apItemId, { force = false } = {}) {
    if (!apItemId || !this.runtimeConfig?.backendUrl) return [];
    if (!force && this.auditCache.has(apItemId)) {
      return this.auditCache.get(apItemId) || [];
    }

    if (this.auditRequests.has(apItemId)) {
      return this.auditRequests.get(apItemId);
    }

    const request = (async () => {
      try {
        const url = `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(apItemId)}/audit`;
        const response = await this.backendFetch(url, { method: 'GET' });
        if (!response.ok) return [];
        const payload = await response.json();
        const events = Array.isArray(payload?.events) ? payload.events : [];
        this.auditCache.set(apItemId, events);
        return events;
      } catch (_) {
        return [];
      } finally {
        this.auditRequests.delete(apItemId);
      }
    })();

    this.auditRequests.set(apItemId, request);
    return request;
  }

  async fetchItemTasks(apItemId, { force = false, includeCompleted = true } = {}) {
    if (!apItemId || !this.runtimeConfig?.backendUrl) return [];
    if (!force && this.tasksByItem.has(apItemId)) {
      return this.tasksByItem.get(apItemId) || [];
    }
    if (this.taskRequests.has(apItemId)) {
      return this.taskRequests.get(apItemId);
    }

    const request = (async () => {
      try {
        const url = new URL(`${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(apItemId)}/tasks`);
        if (!includeCompleted) url.searchParams.set('include_completed', 'false');
        const response = await this.backendFetch(url.toString(), { method: 'GET' });
        if (!response.ok) return [];
        const payload = await response.json();
        const tasks = Array.isArray(payload?.tasks) ? payload.tasks : [];
        this.tasksByItem.set(apItemId, tasks);
        this.emitQueueUpdated();
        return tasks;
      } catch (_) {
        return [];
      } finally {
        this.taskRequests.delete(apItemId);
      }
    })();

    this.taskRequests.set(apItemId, request);
    return request;
  }

  async fetchItemNotes(apItemId, { force = false } = {}) {
    if (!apItemId || !this.runtimeConfig?.backendUrl) return [];
    if (!force && this.notesByItem.has(apItemId)) {
      return this.notesByItem.get(apItemId) || [];
    }
    if (this.noteRequests.has(apItemId)) {
      return this.noteRequests.get(apItemId);
    }

    const request = (async () => {
      try {
        const response = await this.backendFetch(
          `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(apItemId)}/notes`,
          { method: 'GET' }
        );
        if (!response.ok) return [];
        const payload = await response.json();
        const notes = Array.isArray(payload?.notes) ? payload.notes : [];
        this.notesByItem.set(apItemId, notes);
        this.emitQueueUpdated();
        return notes;
      } catch (_) {
        return [];
      } finally {
        this.noteRequests.delete(apItemId);
      }
    })();

    this.noteRequests.set(apItemId, request);
    return request;
  }

  async fetchItemComments(apItemId, { force = false } = {}) {
    if (!apItemId || !this.runtimeConfig?.backendUrl) return [];
    if (!force && this.commentsByItem.has(apItemId)) {
      return this.commentsByItem.get(apItemId) || [];
    }
    if (this.commentRequests.has(apItemId)) {
      return this.commentRequests.get(apItemId);
    }

    const request = (async () => {
      try {
        const response = await this.backendFetch(
          `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(apItemId)}/comments`,
          { method: 'GET' }
        );
        if (!response.ok) return [];
        const payload = await response.json();
        const comments = Array.isArray(payload?.comments) ? payload.comments : [];
        this.commentsByItem.set(apItemId, comments);
        this.emitQueueUpdated();
        return comments;
      } catch (_) {
        return [];
      } finally {
        this.commentRequests.delete(apItemId);
      }
    })();

    this.commentRequests.set(apItemId, request);
    return request;
  }

  async fetchItemFiles(apItemId, { force = false } = {}) {
    if (!apItemId || !this.runtimeConfig?.backendUrl) return [];
    if (!force && this.filesByItem.has(apItemId)) {
      return this.filesByItem.get(apItemId) || [];
    }
    if (this.fileRequests.has(apItemId)) {
      return this.fileRequests.get(apItemId);
    }

    const request = (async () => {
      try {
        const response = await this.backendFetch(
          `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(apItemId)}/files`,
          { method: 'GET' }
        );
        if (!response.ok) return [];
        const payload = await response.json();
        const files = Array.isArray(payload?.files) ? payload.files : [];
        this.filesByItem.set(apItemId, files);
        this.emitQueueUpdated();
        return files;
      } catch (_) {
        return [];
      } finally {
        this.fileRequests.delete(apItemId);
      }
    })();

    this.fileRequests.set(apItemId, request);
    return request;
  }

  async createTask(item, payload = {}) {
    if (!item?.id || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(item.id)}/tasks`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }
    );
    if (!response.ok) {
      return { status: 'error', reason: `task_create_${response.status}` };
    }
    const result = await response.json();
    await this.fetchItemTasks(item.id, { force: true });
    await this.fetchAuditTrail(item.id, { force: true });
    return result;
  }

  async updateTaskStatus(taskId, payload = {}, itemId = '') {
    if (!taskId || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/api/ap/items/tasks/${encodeURIComponent(taskId)}/status`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }
    );
    if (!response.ok) return { status: 'error', reason: `task_status_${response.status}` };
    const result = await response.json();
    if (itemId) await this.fetchItemTasks(itemId, { force: true });
    return result;
  }

  async assignTask(taskId, payload = {}, itemId = '') {
    if (!taskId || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/api/ap/items/tasks/${encodeURIComponent(taskId)}/assign`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }
    );
    if (!response.ok) return { status: 'error', reason: `task_assign_${response.status}` };
    const result = await response.json();
    if (itemId) await this.fetchItemTasks(itemId, { force: true });
    return result;
  }

  async addTaskComment(taskId, payload = {}, itemId = '') {
    if (!taskId || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/api/ap/items/tasks/${encodeURIComponent(taskId)}/comments`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }
    );
    if (!response.ok) return { status: 'error', reason: `task_comment_${response.status}` };
    const result = await response.json();
    if (itemId) await this.fetchItemTasks(itemId, { force: true });
    return result;
  }

  async addItemNote(item, payload = {}) {
    if (!item?.id || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(item.id)}/notes`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }
    );
    if (!response.ok) return { status: 'error', reason: `note_create_${response.status}` };
    const result = await response.json();
    await this.fetchItemNotes(item.id, { force: true });
    await this.fetchAuditTrail(item.id, { force: true });
    return result;
  }

  async addItemComment(item, payload = {}) {
    if (!item?.id || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(item.id)}/comments`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }
    );
    if (!response.ok) return { status: 'error', reason: `comment_create_${response.status}` };
    const result = await response.json();
    await this.fetchItemComments(item.id, { force: true });
    await this.fetchAuditTrail(item.id, { force: true });
    return result;
  }

  async addItemFileLink(item, payload = {}) {
    if (!item?.id || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(item.id)}/files`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }
    );
    if (!response.ok) return { status: 'error', reason: `file_create_${response.status}` };
    const result = await response.json();
    await this.fetchItemFiles(item.id, { force: true });
    await this.fetchAuditTrail(item.id, { force: true });
    return result;
  }

  async updateRecordFields(item, payload = {}) {
    if (!item?.id || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(item.id)}/fields`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }
    );
    if (!response.ok) return { status: 'error', reason: `field_update_${response.status}` };
    const result = await response.json();
    this.invalidateItemCaches(item.id);
    await this.refreshQueue();
    return result;
  }

  async searchRecordCandidates(query, { limit = 12 } = {}) {
    if (!this.runtimeConfig?.backendUrl) return [];
    const url = new URL(`${this.runtimeConfig.backendUrl}/api/ap/items/search`);
    url.searchParams.set('organization_id', this.runtimeConfig.organizationId || 'default');
    url.searchParams.set('q', String(query || ''));
    url.searchParams.set('limit', String(limit));
    const response = await this.backendFetch(url.toString(), { method: 'GET' });
    if (!response.ok) return [];
    const payload = await response.json();
    return Array.isArray(payload?.items) ? payload.items : [];
  }

  async lookupComposeRecord(payload = {}) {
    if (!this.runtimeConfig?.backendUrl) return { status: 'missing', ap_item: null };
    const url = new URL(`${this.runtimeConfig.backendUrl}/api/ap/items/compose/lookup`);
    url.searchParams.set('organization_id', this.runtimeConfig.organizationId || 'default');
    if (payload?.draft_id) url.searchParams.set('draft_id', String(payload.draft_id));
    if (payload?.thread_id) url.searchParams.set('thread_id', String(payload.thread_id));
    const response = await this.backendFetch(url.toString(), { method: 'GET' });
    if (!response.ok) return { status: 'missing', ap_item: null };
    return response.json();
  }

  async createRecordFromComposeDraft(payload = {}) {
    if (!this.runtimeConfig?.backendUrl) return { status: 'invalid', ap_item: null };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/api/ap/items/compose/create`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }
    );
    if (!response.ok) return { status: 'error', reason: `compose_create_${response.status}`, ap_item: null };
    const result = await response.json();
    this.invalidateItemCaches(result?.ap_item?.id || '');
    await this.refreshQueue();
    return result;
  }

  async recoverCurrentThread(threadId) {
    if (!threadId || !this.runtimeConfig?.backendUrl) return { found: false, recovered: false, item: null };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/extension/by-thread/${encodeURIComponent(threadId)}/recover`,
      { method: 'POST' }
    );
    if (!response.ok) return { found: false, recovered: false, item: null };
    const payload = await response.json();
    if (payload?.item) {
      this.upsertQueueItem(payload.item);
      this.emitQueueUpdated();
    }
    return payload || { found: false, recovered: false, item: null };
  }

  async linkCurrentThreadToItem(item, payload = {}) {
    if (!item?.id || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(item.id)}/gmail-link`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }
    );
    if (!response.ok) return { status: 'error', reason: `gmail_link_${response.status}` };
    const result = await response.json();
    this.invalidateItemCaches(item.id);
    await this.refreshQueue();
    return result;
  }

  async linkComposeDraftToItem(item, payload = {}) {
    if (!item?.id || !this.runtimeConfig?.backendUrl) return { status: 'invalid', ap_item: null };
    const response = await this.backendFetch(
      `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(item.id)}/compose-link`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      }
    );
    if (!response.ok) return { status: 'error', reason: `compose_link_${response.status}`, ap_item: null };
    const result = await response.json();
    this.invalidateItemCaches(item.id);
    await this.refreshQueue();
    return result;
  }

  async verifyConfidence(item) {
    if (!item || !this.runtimeConfig?.backendUrl) return null;
    const locator = this.buildItemLocator(item);
    const metadata = this.parseMetadata(item?.metadata);
    const extraction = {
      vendor: item.vendor_name || item.vendor || '',
      amount: item.amount,
      currency: item.currency || metadata.currency || null,
      invoice_number: item.invoice_number || '',
    };
    try {
      const response = await this.backendFetch(`${this.runtimeConfig.backendUrl}/extension/verify-confidence`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...locator,
          extraction,
          organization_id: this.runtimeConfig.organizationId || 'default',
        })
      });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async approveAndPost(item, { override = false, overrideJustification = '', idempotencyKey = '' } = {}) {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'error', reason: 'invalid' };
    const locator = this.buildItemLocator(item);
    try {
      const result = await this.executeAgentIntent(
        'post_to_erp',
        {
          ...locator,
          override: Boolean(override),
          override_justification: overrideJustification || undefined,
          field_confidences: item.field_confidences || undefined,
        },
        {
          idempotencyKey,
          defaultStatus: 'error',
        }
      );
      await this.syncQueueWithBackend({ updateStatus: false });
      this.emitQueueUpdated();
      return result;
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async getGlSuggestions(item) {
    if (!item || !this.runtimeConfig?.backendUrl) return null;
    try {
      const response = await this.backendFetch(`${this.runtimeConfig.backendUrl}/extension/suggestions/gl-code`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vendor_name: item.vendor_name || item.vendor || '',
          organization_id: this.runtimeConfig.organizationId || 'default',
        })
      });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async getVendorSuggestions(item) {
    if (!item || !this.runtimeConfig?.backendUrl) return null;
    try {
      const response = await this.backendFetch(`${this.runtimeConfig.backendUrl}/extension/suggestions/vendor`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          extracted_vendor: item.vendor_name || item.vendor || '',
          sender_email: item.sender || '',
          organization_id: this.runtimeConfig.organizationId || 'default',
        })
      });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async requestApproval(item, {
    forceHumanReview = false,
    idempotencyKey = '',
    reason = '',
  } = {}) {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const locator = this.buildItemLocator(item);
    const result = await this.executeAgentIntent(
      'request_approval',
      {
        ...locator,
        force_human_review: Boolean(forceHumanReview),
        reason: reason || undefined,
      },
      {
        idempotencyKey,
        defaultStatus: 'pending_approval',
      }
    );
    this.emitQueueUpdated();
    await this.syncQueueWithBackend({ updateStatus: false });
    return result;
  }

  async nudgeApproval(item, { message = '', idempotencyKey = '' } = {}) {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const locator = this.buildItemLocator(item);
    try {
      const result = await this.executeAgentIntent(
        'nudge_approval',
        {
          ...locator,
          message: message || undefined,
        },
        {
          idempotencyKey,
          defaultStatus: 'error',
        }
      );
      await this.syncQueueWithBackend({ updateStatus: false });
      this.emitQueueUpdated();
      const normalized = result && typeof result === 'object' ? { ...result } : {};
      const delivered = ['slack', 'teams', 'fallback'].some((key) => (
        String(normalized?.[key]?.status || '').toLowerCase() === 'sent'
      ));
      if (delivered && String(normalized.status || '').toLowerCase() !== 'nudged') {
        normalized.status = 'nudged';
      }
      return normalized.status ? normalized : { status: 'nudged' };
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async escalateApproval(item, { message = '', idempotencyKey = '' } = {}) {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const locator = this.buildItemLocator(item);
    try {
      const result = await this.executeAgentIntent(
        'escalate_approval',
        {
          ...locator,
          message: message || undefined,
        },
        {
          idempotencyKey,
          defaultStatus: 'error',
        }
      );
      await this.syncQueueWithBackend({ updateStatus: false });
      this.emitQueueUpdated();
      return result || { status: 'escalated' };
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async reassignApproval(item, { assignee = '', note = '', idempotencyKey = '' } = {}) {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const locator = this.buildItemLocator(item);
    try {
      const result = await this.executeAgentIntent(
        'reassign_approval',
        {
          ...locator,
          assignee: assignee || undefined,
          note: note || undefined,
        },
        {
          idempotencyKey,
          defaultStatus: 'error',
        }
      );
      await this.syncQueueWithBackend({ updateStatus: false });
      this.emitQueueUpdated();
      return result || { status: 'reassigned' };
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async rejectInvoice(item, { reason = '', idempotencyKey = '' } = {}) {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid', reason: 'invalid' };
    const locator = this.buildItemLocator(item);
    try {
      const result = await this.executeAgentIntent(
        'reject_invoice',
        {
          ...locator,
          reason: reason || 'Rejected from Gmail',
        },
        {
          idempotencyKey,
          defaultStatus: 'error',
        }
      );
      await this.syncQueueWithBackend({ updateStatus: false });
      this.emitQueueUpdated();
      return result || { status: 'rejected' };
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async readErrorDetail(response) {
    if (!response) return '';
    try {
      const payload = await response.json();
      return String(payload?.detail || '').trim();
    } catch (_) {
      return '';
    }
  }

  async executeAgentIntent(intent, input, { idempotencyKey = '', defaultStatus = 'error' } = {}) {
    if (!this.runtimeConfig?.backendUrl) return { status: 'invalid' };

    const normalizedIntent = String(intent || '').trim();
    if (!normalizedIntent) return { status: 'invalid', reason: 'missing_intent' };

    const key = String(idempotencyKey || '').trim();
    const executePayload = {
      intent: normalizedIntent,
      input: input && typeof input === 'object' ? input : {},
      idempotency_key: key || undefined,
      organization_id: this.runtimeConfig.organizationId || 'default',
    };

    try {
      const response = await this.backendFetch(`${this.runtimeConfig.backendUrl}/api/agent/intents/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(executePayload),
      });
      if (response.ok) {
        const result = await response.json();
        return result || { status: defaultStatus };
      }
      const detail = await this.readErrorDetail(response);
      return { status: 'error', reason: detail || `http_${response.status}` };
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async prepareVendorFollowup(item, { reason = '', force = false, idempotencyKey = '' } = {}) {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    try {
      const locator = this.buildItemLocator(item);
      const result = await this.executeAgentIntent(
        'prepare_vendor_followups',
        {
          ...locator,
          reason: reason || undefined,
          force: Boolean(force),
        },
        {
          idempotencyKey,
          defaultStatus: 'prepared',
        }
      );
      await this.syncQueueWithBackend({ updateStatus: false });
      this.emitQueueUpdated();
      return result || { status: 'prepared' };
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async retryFailedPost(item) {
    if (!item?.id || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    try {
      const orgId = this.runtimeConfig.organizationId || 'default';
      const url = new URL(
        `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(item.id)}/retry-post`
      );
      url.searchParams.set('organization_id', orgId);
      const response = await this.backendFetch(url.toString(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!response.ok) {
        let detail = '';
        try {
          const errPayload = await response.json();
          detail = errPayload?.detail || '';
        } catch (_) {}
        return { status: 'error', reason: detail || `http_${response.status}` };
      }
      const result = await response.json();
      await this.syncQueueWithBackend({ updateStatus: false });
      this.emitQueueUpdated();
      return result || { status: 'ready_to_post' };
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async routeLowRiskForApproval(item, { idempotencyKey = '', reason = '' } = {}) {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    try {
      const locator = this.buildItemLocator(item);
      const result = await this.executeAgentIntent(
        'route_low_risk_for_approval',
        {
          ...locator,
          reason: reason || undefined,
        },
        {
          idempotencyKey,
          defaultStatus: 'pending_approval',
        }
      );
      await this.syncQueueWithBackend({ updateStatus: false });
      this.emitQueueUpdated();
      return result || { status: 'pending_approval' };
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async retryRecoverableFailure(item, { idempotencyKey = '', reason = '' } = {}) {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    try {
      const locator = this.buildItemLocator(item);
      const result = await this.executeAgentIntent(
        'retry_recoverable_failures',
        {
          ...locator,
          reason: reason || undefined,
        },
        {
          idempotencyKey,
          defaultStatus: 'error',
        }
      );
      await this.syncQueueWithBackend({ updateStatus: false });
      this.emitQueueUpdated();
      return result || { status: 'error', reason: 'unknown_response' };
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async resolveEntityRoute(item, {
    selection = '',
    entityId = '',
    entityCode = '',
    entityName = '',
    note = '',
  } = {}) {
    if (!item?.id || !this.runtimeConfig?.backendUrl) return { status: 'invalid', reason: 'invalid' };

    try {
      const url = new URL(
        `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(item.id)}/entity-route/resolve`
      );
      url.searchParams.set('organization_id', this.runtimeConfig.organizationId || 'default');
      const response = await this.backendFetch(url.toString(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          selection: selection || undefined,
          entity_id: entityId || undefined,
          entity_code: entityCode || undefined,
          entity_name: entityName || undefined,
          note: note || undefined,
        }),
      });
      if (!response.ok) {
        const detail = await this.readErrorDetail(response);
        return { status: 'error', reason: detail || `http_${response.status}` };
      }
      const result = await response.json();
      if (result?.ap_item) {
        this.upsertQueueItem(result.ap_item);
      }
      await this.syncQueueWithBackend({ updateStatus: false });
      this.emitQueueUpdated();
      return result || { status: 'resolved' };
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async resolveFieldReview(item, {
    field = '',
    source = '',
    manualValue = undefined,
    note = '',
    autoResume = true,
  } = {}) {
    if (!item?.id || !this.runtimeConfig?.backendUrl) return { status: 'invalid', reason: 'invalid' };
    const normalizedField = String(field || '').trim().toLowerCase();
    const normalizedSource = String(source || '').trim().toLowerCase();
    if (!normalizedField || !normalizedSource) return { status: 'invalid', reason: 'missing_resolution_input' };

    try {
      const url = new URL(
        `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(item.id)}/field-review/resolve`
      );
      url.searchParams.set('organization_id', this.runtimeConfig.organizationId || 'default');
      const response = await this.backendFetch(url.toString(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          field: normalizedField,
          source: normalizedSource,
          manual_value: manualValue,
          note: note || undefined,
          auto_resume: Boolean(autoResume),
        }),
      });
      if (!response.ok) {
        const detail = await this.readErrorDetail(response);
        return { status: 'error', reason: detail || `http_${response.status}` };
      }
      const result = await response.json();
      if (result?.ap_item) {
        this.upsertQueueItem(result.ap_item);
      }
      this.invalidateItemCaches(item.id);
      await this.syncQueueWithBackend({ updateStatus: false });
      this.emitQueueUpdated();
      return result || { status: 'resolved' };
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async shareFinanceSummary(item, {
    target = 'email_draft',
    recipientEmail = '',
    note = '',
    previewOnly = false
  } = {}) {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const locator = this.buildItemLocator(item);
    try {
      const response = await this.backendFetch(`${this.runtimeConfig.backendUrl}/extension/finance-summary-share`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...locator,
          target,
          preview_only: Boolean(previewOnly),
          recipient_email: recipientEmail || this.runtimeConfig.financeLeadEmail || undefined,
          note: note || undefined,
          organization_id: this.runtimeConfig.organizationId || 'default',
          user_email: this.runtimeConfig.userEmail || 'gmail_extension',
        })
      });
      if (!response.ok) {
        let detail = '';
        try {
          const errPayload = await response.json();
          detail = errPayload?.detail || '';
        } catch (_) {}
        return { status: 'error', reason: detail || `http_${response.status}` };
      }
      return await response.json();
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  async previewFinanceSummaryShare(item, options = {}) {
    return this.shareFinanceSummary(item, { ...options, previewOnly: true });
  }

  async submitBudgetDecision(item, decision, justification = '') {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const locator = this.buildItemLocator(item);
    const payload = {
      ...locator,
      decision,
      justification,
      organization_id: this.runtimeConfig.organizationId,
      user_email: this.runtimeConfig.userEmail
    };

    try {
      const response = await this.backendFetch(`${this.runtimeConfig.backendUrl}/extension/budget-decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        let detail = '';
        try {
          const errPayload = await response.json();
          detail = errPayload?.detail || '';
        } catch (_) {
          detail = '';
        }
        return { status: 'error', reason: detail || `http_${response.status}` };
      }

      const result = await response.json();
      await this.syncQueueWithBackend({ updateStatus: false });
      if (item.id) {
        await this.fetchItemContext(item.id, { refresh: true });
      }
      this.emitQueueUpdated();
      return result;
    } catch (_) {
      return { status: 'error', reason: 'network_error' };
    }
  }

  findMergeCandidates(item) {
    if (!item) return [];
    const invoiceNumber = String(item.invoice_number || '').trim().toLowerCase();
    const vendorName = String(item.vendor_name || item.vendor || '').trim().toLowerCase();
    return (Array.isArray(this.queue) ? this.queue : [])
      .filter((entry) => entry.id && entry.id !== item.id)
      .filter((entry) => {
        const sameInvoice = invoiceNumber && String(entry.invoice_number || '').trim().toLowerCase() === invoiceNumber;
        const sameVendor = vendorName && String(entry.vendor_name || entry.vendor || '').trim().toLowerCase() === vendorName;
        return sameInvoice || (sameVendor && !invoiceNumber);
      })
      .sort((left, right) => {
        const leftSources = Number(left.source_count || 0);
        const rightSources = Number(right.source_count || 0);
        if (rightSources !== leftSources) return rightSources - leftSources;
        return this.getPriorityScore(right) - this.getPriorityScore(left);
      });
  }

  async mergeItems(targetItemId, sourceItemId, actorId = 'gmail_user', reason = 'manual_merge') {
    if (!targetItemId || !sourceItemId || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    try {
      const response = await this.backendFetch(
        `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(targetItemId)}/merge`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source_ap_item_id: sourceItemId,
            actor_id: actorId,
            reason
          })
        }
      );
      if (!response.ok) return { status: 'error' };
      const payload = await response.json();
      await this.refreshQueue();
      return payload;
    } catch (_) {
      return { status: 'error' };
    }
  }

  async splitItem(apItemId, sources, actorId = 'gmail_user', reason = 'manual_split') {
    if (!apItemId || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    try {
      const response = await this.backendFetch(
        `${this.runtimeConfig.backendUrl}/api/ap/items/${encodeURIComponent(apItemId)}/split`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            actor_id: actorId,
            reason,
            sources: Array.isArray(sources) ? sources : []
          })
        }
      );
      if (!response.ok) return { status: 'error' };
      const payload = await response.json();
      await this.refreshQueue();
      return payload;
    } catch (_) {
      return { status: 'error' };
    }
  }

}

export { ClearledgrQueueManager };
