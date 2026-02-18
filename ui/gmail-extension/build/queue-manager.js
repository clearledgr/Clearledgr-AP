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
    this.sourcesByItem = new Map();
    this.contextByItem = new Map();
    this.sourceRequests = new Map();
    this.contextRequests = new Map();
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
          this.agentSessionsByItem,
          this.browserTabContext,
          this.agentInsightsByItem,
          this.sourcesByItem,
          this.contextByItem,
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

  buildAgentScope(item, fallback = {}) {
    const metadata = this.parseMetadata(item?.metadata);
    const sessionMeta = this.parseMetadata(fallback?.session?.metadata);
    const actorRole = String(
      fallback?.actorRole
      || metadata.actor_role
      || metadata.agent_actor_role
      || item?.actor_role
      || item?.assignee_role
      || sessionMeta.actor_role
      || ''
    ).trim();
    const workflowId = String(
      fallback?.workflowId
      || item?.workflow_id
      || metadata.workflow_id
      || sessionMeta.workflow_id
      || ''
    ).trim();
    return {
      actorRole: actorRole || null,
      workflowId: workflowId || null
    };
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
    await this.fetchApKpis({ force: true });
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
      await this.fetchApKpis({ force: true });
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
      await this.fetchApKpis({ force: true });
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
    if (!normalized.thread_id && primary.thread_id) normalized.thread_id = primary.thread_id;
    if (!normalized.message_id && primary.message_id) normalized.message_id = primary.message_id;
    if (normalized.source_count === undefined || normalized.source_count === null) {
      normalized.source_count = 0;
    }
    normalized.has_context_conflict = Boolean(normalized.has_context_conflict);
    normalized.exception_code = normalized.exception_code || null;
    normalized.exception_severity = normalized.exception_severity || null;
    normalized.budget_status = normalized.budget_status || null;
    normalized.budget_requires_decision = Boolean(normalized.budget_requires_decision);
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
        const response = await fetch(
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
        const response = await fetch(url.toString(), { method: 'GET' });
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
    if (!this.runtimeConfig?.backendUrl) return null;
    if (!force && this.kpiSnapshot) return this.kpiSnapshot;
    if (this.kpiRequest) return this.kpiRequest;

    const request = (async () => {
      try {
        const org = encodeURIComponent(this.runtimeConfig.organizationId || 'default');
        const response = await fetch(`${this.runtimeConfig.backendUrl}/api/ops/ap-kpis?organization_id=${org}`, {
          method: 'GET'
        });
        if (!response.ok) return this.kpiSnapshot;
        const payload = await response.json();
        this.kpiSnapshot = payload?.kpis || null;
        this.kpiUpdatedAt = Date.now();
        this.emitQueueUpdated();
        return this.kpiSnapshot;
      } catch (_) {
        return this.kpiSnapshot;
      } finally {
        this.kpiRequest = null;
      }
    })();

    this.kpiRequest = request;
    return request;
  }

  async syncQueueWithBackend({ updateStatus = false } = {}) {
    if (!this.runtimeConfig?.backendUrl) return false;
    try {
      const org = encodeURIComponent(this.runtimeConfig.organizationId || 'default');
      const worklistUrl = `${this.runtimeConfig.backendUrl}/extension/worklist?organization_id=${org}`;
      const worklistResponse = await fetch(worklistUrl, { method: 'GET' });

      let items = [];
      if (worklistResponse.ok) {
        const payload = await worklistResponse.json();
        items = Array.isArray(payload?.items) ? payload.items.map((item) => this.normalizeWorklistItem(item)) : [];
      } else {
        const pipelineUrl = `${this.runtimeConfig.backendUrl}/extension/pipeline?organization_id=${org}`;
        const pipelineResponse = await fetch(pipelineUrl, { method: 'GET' });
        if (!pipelineResponse.ok) throw new Error(`pipeline_${pipelineResponse.status}`);
        const pipeline = await pipelineResponse.json();
        Object.values(pipeline || {}).forEach((group) => {
          if (Array.isArray(group)) items.push(...group.map((item) => this.normalizeWorklistItem(item)));
        });
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
      await this.fetchApKpis({ force: true });
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
    await this.fetchApKpis({ force: true });
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

    const scope = this.buildAgentScope(item);
    try {
      const response = await fetch(`${this.runtimeConfig.backendUrl}/api/agent/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          org_id: this.runtimeConfig.organizationId || 'default',
          ap_item_id: item.id,
          actor_id: 'gmail_extension',
          metadata: {
            source: 'gmail_sidebar',
            actor_role: scope.actorRole,
            workflow_id: scope.workflowId
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

  async confirmAgentCommand(sessionId, command, actorId = 'gmail_user', scopeContext = {}) {
    if (!sessionId || !command || !this.runtimeConfig?.backendUrl) return null;
    const payload = command.request_payload || {};
    const scope = this.buildAgentScope(null, {
      actorRole: payload.actor_role || scopeContext.actorRole,
      workflowId: payload.workflow_id || scopeContext.workflowId
    });
    try {
      const response = await fetch(`${this.runtimeConfig.backendUrl}/api/agent/sessions/${encodeURIComponent(sessionId)}/commands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          actor_id: actorId,
          actor_role: scope.actorRole,
          workflow_id: scope.workflowId,
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

  async enqueueAgentCommand(sessionId, command, actorId = 'gmail_extension', scopeContext = {}) {
    if (!sessionId || !command || !this.runtimeConfig?.backendUrl) return null;
    const scope = this.buildAgentScope(null, {
      actorRole: command.actor_role || scopeContext.actorRole,
      workflowId: command.workflow_id || scopeContext.workflowId
    });
    try {
      const response = await fetch(`${this.runtimeConfig.backendUrl}/api/agent/sessions/${encodeURIComponent(sessionId)}/commands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          actor_id: actorId,
          actor_role: scope.actorRole,
          workflow_id: scope.workflowId,
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

  async previewAgentCommand(sessionId, command, actorId = 'gmail_user', scopeContext = {}) {
    if (!sessionId || !command || !this.runtimeConfig?.backendUrl) return null;
    const payload = command.request_payload || command;
    const scope = this.buildAgentScope(null, {
      actorRole: payload.actor_role || scopeContext.actorRole,
      workflowId: payload.workflow_id || scopeContext.workflowId
    });
    try {
      const response = await fetch(`${this.runtimeConfig.backendUrl}/api/agent/sessions/${encodeURIComponent(sessionId)}/commands/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          actor_id: actorId,
          actor_role: scope.actorRole,
          workflow_id: scope.workflowId,
          tool_name: payload.tool_name,
          command_id: payload.command_id || command.command_id,
          correlation_id: payload.correlation_id,
          target: payload.target || {},
          params: payload.params || {}
        })
      });
      if (!response.ok) return null;
      const body = await response.json();
      return body?.preview || null;
    } catch (_) {
      return null;
    }
  }

  async dispatchAgentMacro(sessionId, macroName, {
    actorId = 'gmail_user',
    actorRole = null,
    workflowId = null,
    params = {},
    dryRun = false
  } = {}) {
    if (!sessionId || !macroName || !this.runtimeConfig?.backendUrl) return null;
    const scope = this.buildAgentScope(null, {
      actorRole,
      workflowId
    });
    try {
      const response = await fetch(
        `${this.runtimeConfig.backendUrl}/api/agent/sessions/${encodeURIComponent(sessionId)}/macros/${encodeURIComponent(macroName)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            actor_id: actorId,
            actor_role: scope.actorRole,
            workflow_id: scope.workflowId,
            params: params || {},
            dry_run: Boolean(dryRun)
          })
        }
      );
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
    const enqueued = await this.enqueueAgentCommand(
      sessionId,
      retryCommand,
      'gmail_extension_recovery',
      {
        actorRole: priorRequest.actor_role || this.parseMetadata(session?.metadata).actor_role || null,
        workflowId: priorRequest.workflow_id || this.parseMetadata(session?.metadata).workflow_id || null
      }
    );
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

  async submitBudgetDecision(item, decision, justification = '') {
    if (!item || !this.runtimeConfig?.backendUrl) return { status: 'invalid' };
    const payload = {
      email_id: item.thread_id || item.message_id || item.id,
      decision,
      justification,
      organization_id: this.runtimeConfig.organizationId,
      user_email: this.runtimeConfig.userEmail
    };

    try {
      const response = await fetch(`${this.runtimeConfig.backendUrl}/extension/budget-decision`, {
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
      const response = await fetch(
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
      const response = await fetch(
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
