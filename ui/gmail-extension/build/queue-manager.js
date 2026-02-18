/**
 * Clearledgr AP v1 Queue Manager
 * AP intake, queue sync, and action dispatch.
 */
class ClearledgrQueueManager {
  constructor() {
    this.queue = [];
    this.listeners = [];
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
    this.debugManualScan = false;
    this.autopilotStatus = null;
    this.auditCache = new Map();
    this.auditRequests = new Map();
    this.agentSessionsByItem = new Map();
    this.agentCommandInFlight = new Set();
    this.agentSyncInFlight = false;
    this.browserTabContext = [];
    this.agentInsightsByItem = new Map();
    this.agentCommandRetryCount = new Map();
    this.agentReadPageRecovery = new Set();
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
          this.agentSessionsByItem,
          this.browserTabContext,
          this.agentInsightsByItem
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
    return this.queue.find((item) => item.thread_id === threadId || item.threadId === threadId) || null;
  }

  getAgentSessionForItem(itemId) {
    if (!itemId) return null;
    return this.agentSessionsByItem.get(itemId) || null;
  }

  getBrowserTabContext() {
    return Array.isArray(this.browserTabContext) ? [...this.browserTabContext] : [];
  }

  getAgentInsightsForItem(itemId) {
    if (!itemId) return null;
    return this.agentInsightsByItem.get(itemId) || null;
  }

  getUiActionDisabledReason(action, state) {
    const allowed = ClearledgrQueueManager.ACTION_STATES[action] || [];
    if (!state) return 'Action unavailable';
    if (!allowed.includes(state)) return 'Action unavailable';
    return '';
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

  extractHostname(url) {
    try {
      const parsed = new URL(String(url || ''));
      return String(parsed.hostname || '').toLowerCase();
    } catch (_) {
      return '';
    }
  }

  extractSenderDomain(sender) {
    const value = String(sender || '').trim().toLowerCase();
    if (!value) return '';
    const match = value.match(/@([a-z0-9.-]+\.[a-z]{2,})/);
    return match ? match[1] : '';
  }

  tokenizeSearchText(value) {
    const text = String(value || '').toLowerCase();
    if (!text) return [];
    const tokens = text
      .split(/[^a-z0-9.-]+/)
      .map((token) => token.trim())
      .filter((token) => token.length >= 3);
    return Array.from(new Set(tokens)).slice(0, 25);
  }

  buildTabSearchTokens(item) {
    const tokens = new Set();
    this.tokenizeSearchText(item?.vendor_name || item?.vendor).forEach((token) => tokens.add(token));
    this.tokenizeSearchText(item?.subject).forEach((token) => tokens.add(token));
    this.tokenizeSearchText(item?.invoice_number).forEach((token) => tokens.add(token));

    const senderDomain = this.extractSenderDomain(item?.sender);
    if (senderDomain) {
      tokens.add(senderDomain);
      senderDomain.split('.').forEach((part) => {
        if (part.length >= 3) tokens.add(part);
      });
    }

    return Array.from(tokens).slice(0, 30);
  }

  buildCrossTabInsights(item) {
    const tabs = Array.isArray(this.browserTabContext) ? this.browserTabContext : [];
    if (!item) {
      return {
        totalTabs: tabs.length,
        relatedCount: 0,
        relatedTabs: [],
        senderDomain: ''
      };
    }

    const senderDomain = this.extractSenderDomain(item.sender);
    const tokens = this.buildTabSearchTokens(item);
    const scoredTabs = tabs
      .map((tab) => {
        const host = this.extractHostname(tab.url);
        const haystack = `${String(tab.title || '').toLowerCase()} ${String(tab.url || '').toLowerCase()}`;
        let score = 0;

        for (const token of tokens) {
          if (token && haystack.includes(token)) score += 1;
        }

        if (senderDomain && host) {
          if (host === senderDomain || host.endsWith(`.${senderDomain}`) || senderDomain.endsWith(`.${host}`)) {
            score += 2;
          }
        }

        if (String(tab.url || '').toLowerCase().includes('mail.google.com')) score += 1;
        if (tab.active) score += 1;

        return {
          ...tab,
          host,
          score
        };
      })
      .filter((tab) => tab.score > 0)
      .sort((a, b) => (b.score - a.score) || Number(b.active) - Number(a.active));

    return {
      totalTabs: tabs.length,
      relatedCount: scoredTabs.length,
      relatedTabs: scoredTabs.slice(0, 5),
      senderDomain
    };
  }

  extractKeyFacts(text) {
    const normalized = String(text || '').replace(/\s+/g, ' ');
    if (!normalized) return [];
    const facts = new Set();
    const invoiceMatch = normalized.match(/\b(?:invoice|inv)[\s#:.-]*([a-z0-9-]{4,})\b/i);
    if (invoiceMatch?.[1]) facts.add(`Invoice ${invoiceMatch[1]}`);
    const amountMatches = normalized.match(/\b(?:USD|EUR|GBP|SEK|NOK|DKK|CAD|AUD)\s?\d[\d,]*(?:\.\d{2})?\b/g) || [];
    amountMatches.slice(0, 2).forEach((match) => facts.add(match));
    return Array.from(facts).slice(0, 3);
  }

  summarizeReadPageResult(result) {
    if (!result || result.ok !== true) {
      return {
        ok: false,
        error: result?.error || 'read_page_failed'
      };
    }
    const headings = Array.isArray(result.headings)
      ? result.headings.map((heading) => String(heading || '').trim()).filter(Boolean).slice(0, 3)
      : [];
    const body = String(result.body_text || '').replace(/\s+/g, ' ').trim();
    let snippet = body.slice(0, 280);
    if (snippet.length === 280) {
      const sentenceIx = snippet.lastIndexOf('. ');
      if (sentenceIx > 120) snippet = snippet.slice(0, sentenceIx + 1);
    }
    return {
      ok: true,
      title: String(result.title || '').trim(),
      url: String(result.url || '').trim(),
      headings,
      snippet,
      facts: this.extractKeyFacts(body)
    };
  }

  async summarizeCurrentPage() {
    const response = await this.safeSendMessage({
      action: 'executeBrowserToolCommand',
      command: {
        tool_name: 'read_page',
        params: { include_headings: true }
      }
    });
    if (!response?.success) {
      return { ok: false, error: response?.error || 'runtime_unavailable' };
    }
    return this.summarizeReadPageResult(response.result || {});
  }

  async summarizeRelatedTabs(item, maxTabs = 3) {
    const insights = this.buildCrossTabInsights(item);
    const tabs = (insights.relatedTabs || []).slice(0, Math.max(1, Math.min(5, Number(maxTabs) || 3)));
    const summaries = [];
    for (const tab of tabs) {
      const response = await this.safeSendMessage({
        action: 'executeBrowserToolCommand',
        command: {
          tool_name: 'read_page',
          target: { tab_id: tab.tabId },
          params: { include_headings: true }
        }
      });
      if (!response?.success) continue;
      const summary = this.summarizeReadPageResult(response.result || {});
      if (!summary.ok) continue;
      summaries.push({
        tabId: tab.tabId,
        host: tab.host || this.extractHostname(tab.url),
        title: tab.title || summary.title,
        summary
      });
    }

    return {
      ok: true,
      totalRelated: insights.relatedCount || 0,
      analyzedCount: summaries.length,
      summaries
    };
  }

  isTransientAgentError(errorCode) {
    const code = String(errorCode || '').toLowerCase();
    if (!code) return true;
    return (
      code === 'execution_failed' ||
      code === 'execution_exception' ||
      code.includes('runtime_message_failed') ||
      code.includes('runtime_message_timeout') ||
      code.includes('runtime_unavailable') ||
      code.includes('receiving end does not exist') ||
      code.includes('target_tab_not_found') ||
      code.includes('no_result') ||
      code.includes('disconnected')
    );
  }

  getAgentEventTimestamp(event) {
    const raw = event?.updated_at || event?.updatedAt || event?.created_at || event?.createdAt || null;
    const parsed = raw ? Date.parse(raw) : NaN;
    return Number.isFinite(parsed) ? parsed : 0;
  }

  getLatestAgentCommandStatuses(events) {
    const latest = new Map();
    const rows = Array.isArray(events) ? events : [];
    for (const event of rows) {
      const commandId = String(event?.command_id || '');
      if (!commandId) continue;
      const ts = this.getAgentEventTimestamp(event);
      const prev = latest.get(commandId);
      if (!prev || ts >= prev.ts) {
        latest.set(commandId, { status: String(event?.status || ''), ts });
      }
    }
    return latest;
  }

  getCommandDependencies(commandPayload) {
    if (!commandPayload) return [];
    const raw = commandPayload.depends_on ?? commandPayload.dependsOn ?? [];
    if (Array.isArray(raw)) {
      return raw.map((value) => String(value || '').trim()).filter(Boolean);
    }
    if (typeof raw === 'string') {
      const value = raw.trim();
      return value ? [value] : [];
    }
    return [];
  }

  async init() {
    await this.loadProcessedIds();
    this.runtimeConfig = await this.getSyncConfig();
    this.debugManualScan = Boolean(this.runtimeConfig?.debugManualScan);

    if (!this.runtimeConfig.valid) {
      this.setScanStatus({
        state: 'blocked',
        mode: 'setup_required',
        error: this.runtimeConfig.errors?.[0] || 'setup_invalid'
      });
      return;
    }

    const synced = await this.syncQueueWithBackend({ updateStatus: false });
    const autopilot = await this.fetchAutopilotStatus();
    this.applyRuntimeStatus({ synced, autopilot });
    if (this.scanStatus.state === 'auth_required') {
      void this.ensureBackendAuthIfNeeded();
    }
    await this.syncAgentSessions();
    this.startBackendSync();
    this.startPeriodicScan();
    if (this.debugManualScan) {
      await this.scanNow('debug');
    }
  }

  async safeSendMessage(message) {
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
      }, 6000);

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
    const result = await this.safeSendMessage({
      action: 'ensureGmailAuth',
      interactive: !!interactive
    });
    if (!result && attempt < 2) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      return this.ensureGmailAuth(interactive, attempt + 1);
    }
    if (!result || result.success === false) {
      this.setScanStatus({
        state: 'auth_required',
        mode: 'gmail_oauth',
        error: result?.error || 'auth_required'
      });
      return { success: false, error: result?.error || 'auth_required' };
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
      chrome.storage.sync.get(['settings', 'backendUrl', 'organizationId', 'userEmail', 'slackChannel'], resolve);
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
      debugManualScan: nested.debugManualScan ?? globalDebugDefault
    };

    const validator =
      (typeof window !== 'undefined' && window.validateRuntimeConfig) ||
      (typeof globalThis !== 'undefined' && globalThis.validateRuntimeConfig);

    if (typeof validator === 'function') {
      const validation = validator(raw);
      return {
        ...validation.settings,
        valid: Boolean(validation.valid),
        errors: Array.isArray(validation.errors) ? validation.errors : [],
        warnings: Array.isArray(validation.warnings) ? validation.warnings : [],
        debugManualScan: Boolean(raw.debugManualScan)
      };
    }

    const backendUrl = String(raw.backendUrl || 'http://127.0.0.1:8000').trim().replace(/\/+$/, '');
    return {
      backendUrl,
      organizationId: String(raw.organizationId || 'default').trim(),
      userEmail: raw.userEmail || null,
      slackChannel: String(raw.slackChannel || '#finance-approvals').trim(),
      confidenceThreshold: 0.85,
      amountAnomalyThreshold: 0.35,
      erpWritebackEnabled: false,
      debugManualScan: Boolean(raw.debugManualScan),
      valid: Boolean(backendUrl),
      errors: backendUrl ? [] : ['Backend URL is required.'],
      warnings: []
    };
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
      const autopilot = await this.fetchAutopilotStatus();
      this.applyRuntimeStatus({ synced, autopilot });
      await this.syncAgentSessions();
    }, 30000);
  }

  async scanNow(source = 'auto') {
    if (this.scanInFlight || !this.runtimeConfig?.valid) return;
    this.scanInFlight = true;
    try {
      this.setScanStatus({ state: 'scanning', mode: 'backend_api', error: null });
      const backendSynced = await this.syncQueueWithBackend({ updateStatus: false });
      const autopilot = await this.fetchAutopilotStatus();
      this.applyRuntimeStatus({
        synced: backendSynced,
        autopilot,
        extra: {
          candidates: Array.isArray(this.queue) ? this.queue.length : 0,
          added: 0,
          lastScanAt: Date.now()
        }
      });
      await this.syncAgentSessions();
    } finally {
      this.scanInFlight = false;
    }
  }

  upsertQueueItem(item, gmailMeta = null) {
    if (!item) return;
    const existingIndex = this.queue.findIndex((entry) => entry.id === item.id || entry.invoice_key === item.invoice_key);
    const merged = { ...item };
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
  }

  async syncQueueWithBackend({ updateStatus = false } = {}) {
    if (!this.runtimeConfig?.backendUrl) return false;
    try {
      const url = `${this.runtimeConfig.backendUrl}/extension/pipeline?organization_id=${encodeURIComponent(this.runtimeConfig.organizationId || 'default')}`;
      const response = await fetch(url, { method: 'GET' });
      if (!response.ok) throw new Error(`pipeline_${response.status}`);
      const pipeline = await response.json();
      const items = [];
      Object.values(pipeline || {}).forEach((group) => {
        if (Array.isArray(group)) items.push(...group);
      });
      this.queue = items;
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
        this.setScanStatus({ state: 'error', mode: 'backend_api', error: error.message || 'pipeline_unavailable' });
      }
      return false;
    }
  }

  async fetchAutopilotStatus() {
    if (!this.runtimeConfig?.backendUrl) return null;
    try {
      const response = await fetch(`${this.runtimeConfig.backendUrl}/api/ops/autopilot-status`, {
        method: 'GET'
      });
      if (!response.ok) return null;
      const payload = await response.json();
      const autopilot = payload?.autopilot || null;
      this.autopilotStatus = autopilot;
      return autopilot;
    } catch (_) {
      return null;
    }
  }

  applyRuntimeStatus({ synced, autopilot, extra = {} } = {}) {
    const status = autopilot || this.autopilotStatus || {};
    const backendError = !synced;
    const mergedExtra = {
      ...extra
    };
    if (typeof status?.failed_count === 'number' && !Number.isNaN(status.failed_count)) {
      mergedExtra.failedCount = status.failed_count;
    }
    if (typeof status?.processed_count === 'number' && !Number.isNaN(status.processed_count)) {
      mergedExtra.processedCount = status.processed_count;
    }
    if (!mergedExtra.lastScanAt && status?.last_run) {
      const ts = Date.parse(status.last_run);
      if (!Number.isNaN(ts)) {
        mergedExtra.lastScanAt = ts;
      }
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

    if (status?.enabled === false) {
      this.setScanStatus({
        state: 'blocked',
        mode: 'backend_autopilot',
        error: 'autopilot_disabled',
        ...mergedExtra
      });
      return;
    }

    if ((status?.state || '') === 'blocked') {
      this.setScanStatus({
        state: 'blocked',
        mode: 'backend_autopilot',
        error: status?.error || 'autopilot_blocked',
        ...mergedExtra
      });
      return;
    }

    if ((status?.state || '') === 'auth_required' || status?.has_tokens === false) {
      this.setScanStatus({
        state: 'auth_required',
        mode: 'backend_autopilot',
        error: 'auth_required',
        ...mergedExtra
      });
      void this.ensureBackendAuthIfNeeded();
      return;
    }

    if ((status?.state || '') === 'error') {
      this.setScanStatus({
        state: 'error',
        mode: 'backend_autopilot',
        error: status?.error || (backendError ? 'backend_unreachable' : 'autopilot_error'),
        ...mergedExtra
      });
      return;
    }

    if ((status?.state || '') === 'degraded') {
      this.setScanStatus({
        state: 'error',
        mode: 'backend_autopilot',
        error: status?.error || 'autopilot_processing_failures',
        ...mergedExtra
      });
      return;
    }

    this.setScanStatus({
      state: 'idle',
      mode: 'backend_autopilot',
      error: null,
      ...mergedExtra
    });
  }

  async ensureBackendAuthIfNeeded() {
    return this.ensureBackendAuth({ force: false });
  }

  async authorizeGmailNow() {
    return this.ensureBackendAuth({ force: true });
  }

  async ensureBackendAuth({ force = false } = {}) {
    if (!this.runtimeConfig?.valid || this.authInFlight) return { success: false, error: 'auth_unavailable' };
    if (!force && this.authPrompted) return { success: false, error: 'auth_already_prompted' };
    if (!force) this.authPrompted = true;
    this.authInFlight = true;

    try {
      const result = await this.ensureGmailAuth(true);
      if (!result?.success) return result || { success: false, error: 'auth_required' };

      this.authPrompted = false;
      const synced = await this.syncQueueWithBackend({ updateStatus: false });
      const autopilot = await this.fetchAutopilotStatus();
      this.applyRuntimeStatus({
        synced,
        autopilot,
        extra: { lastScanAt: Date.now() }
      });
      return { success: true };
    } finally {
      this.authInFlight = false;
    }
  }

  async refreshQueue() {
    const synced = await this.syncQueueWithBackend({ updateStatus: false });
    const autopilot = await this.fetchAutopilotStatus();
    this.applyRuntimeStatus({
      synced,
      autopilot,
      extra: { lastScanAt: Date.now() }
    });
    await this.syncAgentSessions();
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
        const response = await fetch(url, { method: 'GET' });
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

  async ensureAgentSession(item) {
    if (!item?.id || !this.runtimeConfig?.backendUrl) return null;
    const existing = this.agentSessionsByItem.get(item.id);
    if (existing?.session?.id) return existing.session.id;

    const metadata = this.parseMetadata(item.metadata);
    if (metadata?.agent_session_id) return metadata.agent_session_id;

    try {
      const response = await fetch(`${this.runtimeConfig.backendUrl}/api/agent/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          org_id: this.runtimeConfig.organizationId || 'default',
          ap_item_id: item.id,
          actor_id: 'gmail_extension',
          metadata: {
            source: 'gmail_sidebar'
          }
        })
      });
      if (!response.ok) return null;
      const payload = await response.json();
      return payload?.session?.id || null;
    } catch (_) {
      return null;
    }
  }

  async fetchAgentSession(sessionId) {
    if (!sessionId || !this.runtimeConfig?.backendUrl) return null;
    try {
      const response = await fetch(`${this.runtimeConfig.backendUrl}/api/agent/sessions/${encodeURIComponent(sessionId)}`, {
        method: 'GET'
      });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async submitAgentResult(sessionId, commandId, status, resultPayload) {
    if (!sessionId || !commandId || !this.runtimeConfig?.backendUrl) return null;
    try {
      const response = await fetch(`${this.runtimeConfig.backendUrl}/api/agent/sessions/${encodeURIComponent(sessionId)}/results`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          actor_id: 'gmail_extension',
          command_id: commandId,
          status,
          result_payload: resultPayload || {}
        })
      });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async confirmAgentCommand(sessionId, command, actorId = 'gmail_user') {
    if (!sessionId || !command || !this.runtimeConfig?.backendUrl) return null;
    const payload = command.request_payload || {};
    try {
      const response = await fetch(`${this.runtimeConfig.backendUrl}/api/agent/sessions/${encodeURIComponent(sessionId)}/commands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          actor_id: actorId,
          tool_name: payload.tool_name || command.tool_name,
          command_id: command.command_id,
          correlation_id: payload.correlation_id,
          target: payload.target || {},
          params: payload.params || {},
          idempotency_key: payload.idempotency_key,
          confirm: true,
          confirmed_by: actorId
        })
      });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async enqueueAgentCommand(sessionId, command, actorId = 'gmail_extension') {
    if (!sessionId || !command || !this.runtimeConfig?.backendUrl) return null;
    try {
      const response = await fetch(`${this.runtimeConfig.backendUrl}/api/agent/sessions/${encodeURIComponent(sessionId)}/commands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          actor_id: actorId,
          tool_name: command.tool_name,
          command_id: command.command_id,
          correlation_id: command.correlation_id,
          target: command.target || {},
          params: command.params || {},
          idempotency_key: command.idempotency_key
        })
      });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async recoverFailedReadPage(sessionPayload) {
    const session = sessionPayload?.session;
    const sessionId = session?.id;
    if (!sessionId || this.agentReadPageRecovery.has(sessionId)) return;
    const queued = Array.isArray(sessionPayload?.queued_commands) ? sessionPayload.queued_commands : [];
    if (queued.length > 0) return;

    const events = Array.isArray(sessionPayload?.events) ? sessionPayload.events : [];
    const hasCompletedRead = events.some((event) => event.tool_name === 'read_page' && event.status === 'completed');
    if (hasCompletedRead) return;

    const latestReadFailure = [...events].reverse().find((event) => event.tool_name === 'read_page' && event.status === 'failed');
    if (!latestReadFailure) return;
    const failureError = latestReadFailure?.result_payload?.error || latestReadFailure?.resultPayload?.error || 'execution_failed';
    if (!this.isTransientAgentError(failureError)) return;

    const priorRequest = latestReadFailure.request_payload || latestReadFailure.requestPayload || {};
    const retryCommand = {
      tool_name: 'read_page',
      command_id: `read_invoice_page_retry_${Date.now()}`,
      correlation_id: priorRequest.correlation_id,
      target: priorRequest.target || { url: 'https://mail.google.com/' },
      params: priorRequest.params || { include_tables: true }
    };
    const enqueued = await this.enqueueAgentCommand(sessionId, retryCommand, 'gmail_extension_recovery');
    if (enqueued?.event?.command_id) {
      this.agentReadPageRecovery.add(sessionId);
    }
  }

  async processPendingAgentCommands(sessionPayload) {
    if (!sessionPayload?.session?.id) return;
    const sessionId = sessionPayload.session.id;
    const allEvents = Array.isArray(sessionPayload.events) ? sessionPayload.events : [];
    const latestStatuses = this.getLatestAgentCommandStatuses(allEvents);
    const queuedCommands = Array.isArray(sessionPayload.queued_commands) ? sessionPayload.queued_commands : [];
    for (const command of queuedCommands.slice(0, 5)) {
      const commandId = command.command_id;
      if (!commandId) continue;
      const commandPayload = command.request_payload || {};
      const dependencies = this.getCommandDependencies(commandPayload);
      if (dependencies.length > 0) {
        const failedDependency = dependencies.find((depId) => {
          const depStatus = latestStatuses.get(depId)?.status || '';
          return depStatus === 'failed' || depStatus === 'denied_policy';
        });
        if (failedDependency) {
          await this.submitAgentResult(sessionId, commandId, 'failed', {
            error: 'dependency_failed',
            dependency: failedDependency
          });
          latestStatuses.set(commandId, { status: 'failed', ts: Date.now() });
          continue;
        }

        const pendingDependency = dependencies.find((depId) => {
          const depStatus = latestStatuses.get(depId)?.status || '';
          return depStatus !== 'completed';
        });
        if (pendingDependency) {
          continue;
        }
      }

      const inFlightKey = `${sessionId}:${commandId}`;
      if (this.agentCommandInFlight.has(inFlightKey)) continue;
      this.agentCommandInFlight.add(inFlightKey);
      try {
        const response = await this.safeSendMessage({
          action: 'executeBrowserToolCommand',
          command: {
            tool_name: command.tool_name || commandPayload.tool_name,
            target: commandPayload.target || {},
            params: commandPayload.params || {},
            url: commandPayload.url
          }
        });

        const transientTransportError = !response || (response?.success === false && this.isTransientAgentError(response?.error));
        if (transientTransportError) {
          const nextAttempts = (this.agentCommandRetryCount.get(inFlightKey) || 0) + 1;
          this.agentCommandRetryCount.set(inFlightKey, nextAttempts);
          if (nextAttempts < 3) {
            continue;
          }
        }

        let status = 'failed';
        let resultPayload = { error: response?.error || 'execution_failed' };
        if (response?.success && response?.result?.ok) {
          status = 'completed';
          resultPayload = response.result;
        } else if (response?.success) {
          status = 'failed';
          resultPayload = response.result || resultPayload;
        }

        await this.submitAgentResult(sessionId, commandId, status, resultPayload);
        latestStatuses.set(commandId, { status, ts: Date.now() });
        this.agentCommandRetryCount.delete(inFlightKey);
      } catch (_) {
        await this.submitAgentResult(sessionId, commandId, 'failed', { error: 'execution_exception' });
        latestStatuses.set(commandId, { status: 'failed', ts: Date.now() });
        this.agentCommandRetryCount.delete(inFlightKey);
      } finally {
        this.agentCommandInFlight.delete(inFlightKey);
      }
    }
  }

  async syncAgentSessions() {
    if (this.agentSyncInFlight || !this.runtimeConfig?.backendUrl) return;
    this.agentSyncInFlight = true;
    try {
      const tabsResp = await this.safeSendMessage({ action: 'listBrowserTabs' });
      if (tabsResp?.success && Array.isArray(tabsResp.tabs)) {
        this.browserTabContext = tabsResp.tabs;
      }
      const map = new Map();
      const insightsMap = new Map();
      const items = (Array.isArray(this.queue) ? this.queue : []).slice(0, 30);
      for (const item of items) {
        if (!item?.id) continue;
        insightsMap.set(item.id, this.buildCrossTabInsights(item));
        const sessionId = await this.ensureAgentSession(item);
        if (!sessionId) continue;
        const payload = await this.fetchAgentSession(sessionId);
        if (!payload) continue;
        await this.recoverFailedReadPage(payload);
        map.set(item.id, payload);
        await this.processPendingAgentCommands(payload);
      }
      this.agentSessionsByItem = map;
      this.agentInsightsByItem = insightsMap;
      this.emitQueueUpdated();
    } finally {
      this.agentSyncInFlight = false;
    }
  }

  async requestApproval(item) {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const payload = {
      email_id: item.thread_id || item.message_id || item.id,
      invoice_key: item.invoice_key,
      subject: item.subject,
      sender: item.sender,
      vendor: item.vendor_name || item.vendor,
      amount: item.amount,
      currency: item.currency,
      invoice_number: item.invoice_number,
      due_date: item.due_date,
      confidence: item.confidence || 0,
      organization_id: this.runtimeConfig.organizationId,
      user_email: this.runtimeConfig.userEmail,
      slack_channel: this.runtimeConfig.slackChannel
    };

    const response = await fetch(`${this.runtimeConfig.backendUrl}/extension/submit-for-approval`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      return { status: 'error' };
    }
    const result = await response.json();
    if (result.ap_item) this.upsertQueueItem(result.ap_item);
    this.emitQueueUpdated();
    return result;
  }

}

export { ClearledgrQueueManager };
