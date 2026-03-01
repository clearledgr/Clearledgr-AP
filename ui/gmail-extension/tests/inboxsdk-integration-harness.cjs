const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE_PATH = path.resolve(__dirname, '../src/inboxsdk-layer.js');
const VOID_TAGS = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function parseAttributes(raw = '') {
  const attrs = {};
  const regex = /([A-Za-z_:][-A-Za-z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
  let match;
  while ((match = regex.exec(raw))) {
    const name = String(match[1] || '').trim();
    if (!name) continue;
    const value = match[2] ?? match[3] ?? match[4] ?? '';
    attrs[name] = value;
  }
  return attrs;
}

function findTagEnd(html, startIndex) {
  let i = startIndex;
  let quote = null;
  while (i < html.length) {
    const ch = html[i];
    if (quote) {
      if (ch === quote) quote = null;
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      i += 1;
      continue;
    }
    if (ch === '>') return i;
    i += 1;
  }
  return -1;
}

function parseHtmlTree(html) {
  const root = { tagName: '#root', attrs: {}, children: [], innerHTML: String(html || ''), outerStart: 0, outerEnd: String(html || '').length };
  const stack = [root];
  let i = 0;
  while (i < html.length) {
    const lt = html.indexOf('<', i);
    if (lt === -1) break;
    if (html.startsWith('<!--', lt)) {
      const endComment = html.indexOf('-->', lt + 4);
      i = endComment === -1 ? html.length : endComment + 3;
      continue;
    }
    const gt = findTagEnd(html, lt + 1);
    if (gt === -1) break;
    const rawTag = html.slice(lt + 1, gt).trim();
    if (!rawTag) {
      i = gt + 1;
      continue;
    }
    if (rawTag.startsWith('!') || rawTag.startsWith('?')) {
      i = gt + 1;
      continue;
    }
    if (rawTag.startsWith('/')) {
      const closingTag = rawTag.slice(1).trim().toLowerCase();
      for (let s = stack.length - 1; s > 0; s -= 1) {
        const candidate = stack[s];
        if (candidate.tagName === closingTag) {
          candidate.outerEnd = gt + 1;
          candidate.innerHTML = html.slice(candidate.innerStart, lt);
          stack.length = s;
          break;
        }
      }
      i = gt + 1;
      continue;
    }

    const selfClosingBySyntax = /\/\s*$/.test(rawTag);
    const noSlashTag = rawTag.replace(/\/\s*$/, '').trim();
    const spaceIdx = noSlashTag.search(/\s/);
    const tagName = (spaceIdx === -1 ? noSlashTag : noSlashTag.slice(0, spaceIdx)).toLowerCase();
    const attrsRaw = spaceIdx === -1 ? '' : noSlashTag.slice(spaceIdx + 1);
    const attrs = parseAttributes(attrsRaw);
    const node = {
      tagName,
      attrs,
      children: [],
      outerStart: lt,
      outerEnd: null,
      innerStart: gt + 1,
      innerHTML: '',
    };
    const parent = stack[stack.length - 1];
    parent.children.push(node);

    if (selfClosingBySyntax || VOID_TAGS.has(tagName)) {
      node.outerEnd = gt + 1;
      node.innerHTML = '';
    } else {
      stack.push(node);
    }
    i = gt + 1;
  }
  return root;
}

function collectMatches(rootNode, selector) {
  const results = [];
  if (!selector) return results;
  const normalized = String(selector).trim();
  const mode = normalized.startsWith('#')
    ? 'id'
    : normalized.startsWith('.')
      ? 'class'
      : 'tag';
  const needle = mode === 'tag' ? normalized.toLowerCase() : normalized.slice(1);

  function visit(node) {
    if (!node || !Array.isArray(node.children)) return;
    for (const child of node.children) {
      const attrs = child.attrs || {};
      let matched = false;
      if (mode === 'id') {
        matched = String(attrs.id || '') === needle;
      } else if (mode === 'class') {
        const classes = String(attrs.class || '').split(/\s+/).filter(Boolean);
        matched = classes.includes(needle);
      } else {
        matched = String(child.tagName || '').toLowerCase() === needle;
      }
      if (matched) results.push(child);
      visit(child);
    }
  }

  visit(rootNode);
  return results;
}

class FakeEventTarget {
  constructor() {
    this._listeners = new Map();
  }

  addEventListener(type, handler) {
    if (!type || typeof handler !== 'function') return;
    const key = String(type);
    const list = this._listeners.get(key) || [];
    list.push(handler);
    this._listeners.set(key, list);
  }

  removeEventListener(type, handler) {
    const key = String(type);
    const list = this._listeners.get(key) || [];
    this._listeners.set(key, list.filter((fn) => fn !== handler));
  }

  async dispatchEvent(event) {
    const evt = event || { type: 'event' };
    const type = String(evt.type || '');
    const list = [...(this._listeners.get(type) || [])];
    evt.target = evt.target || this;
    evt.currentTarget = this;
    evt.defaultPrevented = false;
    if (typeof evt.preventDefault !== 'function') {
      evt.preventDefault = function preventDefault() { evt.defaultPrevented = true; };
    }
    for (const handler of list) {
      try {
        const result = handler(evt);
        if (result && typeof result.then === 'function') {
          result.catch(() => {});
        }
      } catch (_) {
        // Keep harness dispatch resilient; caller assertions inspect rendered side effects.
      }
    }
    return !evt.defaultPrevented;
  }
}

class FakeElement extends FakeEventTarget {
  constructor(tagName = 'div', ownerDocument = null) {
    super();
    this.tagName = String(tagName || 'div').toUpperCase();
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.className = '';
    this.id = '';
    this.disabled = false;
    this.value = '';
    this._innerHTML = '';
    this._textContent = '';
    this._parsedTree = null;
    this._queryCache = new Map();
    this.removed = false;
    this.src = '';
    this._selectionStart = 0;
    this._selectionEnd = 0;
  }

  _resetParseCache() {
    this._parsedTree = null;
    this._queryCache.clear();
  }

  _applyParsedAttributes(attrs = {}) {
    this.attributes = { ...attrs };
    this.id = String(attrs.id || '');
    this.className = String(attrs.class || '');
    this.disabled = Object.prototype.hasOwnProperty.call(attrs, 'disabled');
    if (Object.prototype.hasOwnProperty.call(attrs, 'value')) {
      this.value = String(attrs.value ?? '');
    }
    if (Object.prototype.hasOwnProperty.call(attrs, 'src')) {
      this.src = String(attrs.src ?? '');
    }
    const dataset = {};
    for (const [name, value] of Object.entries(attrs)) {
      if (!name.startsWith('data-')) continue;
      const key = name
        .slice(5)
        .split('-')
        .filter(Boolean)
        .map((segment, index) => (index === 0 ? segment : segment.charAt(0).toUpperCase() + segment.slice(1)))
        .join('');
      dataset[key] = String(value ?? '');
    }
    this.dataset = dataset;
  }

  _initFromNode(node) {
    this.tagName = String(node.tagName || 'div').toUpperCase();
    this._applyParsedAttributes(node.attrs || {});
    this.innerHTML = String(node.innerHTML || '');
    if (this.tagName === 'SELECT' && !this.value) {
      const optionMatch = this._innerHTML.match(/<option[^>]*value="([^"]*)"[^>]*>/i);
      this.value = optionMatch ? String(optionMatch[1] || '') : '';
    }
  }

  set innerHTML(value) {
    this._innerHTML = String(value ?? '');
    this._textContent = '';
    this._resetParseCache();
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set textContent(value) {
    this._textContent = String(value ?? '');
    this._innerHTML = '';
    this._resetParseCache();
  }

  get textContent() {
    return this._textContent;
  }

  appendChild(child) {
    if (!child) return child;
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  remove() {
    this.removed = true;
    if (this.parentNode && Array.isArray(this.parentNode.children)) {
      this.parentNode.children = this.parentNode.children.filter((node) => node !== this);
    }
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  setAttribute(name, value) {
    const key = String(name);
    const stringValue = String(value ?? '');
    this.attributes[key] = stringValue;
    if (key === 'id') this.id = stringValue;
    if (key === 'class') this.className = stringValue;
    if (key === 'value') this.value = stringValue;
    if (key === 'src') this.src = stringValue;
    if (key.startsWith('data-')) {
      const datasetKey = key
        .slice(5)
        .split('-')
        .filter(Boolean)
        .map((segment, index) => (index === 0 ? segment : segment.charAt(0).toUpperCase() + segment.slice(1)))
        .join('');
      this.dataset[datasetKey] = stringValue;
    }
  }

  _getParsedTree() {
    if (!this._parsedTree) {
      this._parsedTree = parseHtmlTree(this._innerHTML || '');
    }
    return this._parsedTree;
  }

  _nodeCacheKey(node, selector, index) {
    const attrs = node.attrs || {};
    return [
      selector,
      index,
      node.tagName,
      attrs.id || '',
      attrs.class || '',
      attrs['data-intent'] || '',
      attrs['data-tab'] || '',
      attrs['data-macro'] || '',
      attrs['data-source-index'] || '',
      node.outerStart,
      node.outerEnd,
    ].join('|');
  }

  _fakeElementFromNode(node, selector, index) {
    const key = this._nodeCacheKey(node, selector, index);
    if (this._queryCache.has(key)) {
      return this._queryCache.get(key);
    }
    const el = new FakeElement(node.tagName || 'div', this.ownerDocument);
    el.parentNode = this;
    el._initFromNode(node);
    this._queryCache.set(key, el);
    return el;
  }

  querySelectorAll(selector) {
    const normalized = String(selector || '').trim();
    if (!normalized) return [];
    const tree = this._getParsedTree();
    const matches = collectMatches(tree, normalized);
    return matches.map((node, index) => this._fakeElementFromNode(node, normalized, index));
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  async click() {
    if (this.disabled) return false;
    return this.dispatchEvent({ type: 'click' });
  }

  focus() {
    return true;
  }

  blur() {
    return true;
  }

  setSelectionRange(start, end) {
    this._selectionStart = Number(start) || 0;
    this._selectionEnd = Number(end) || this._selectionStart;
  }
}

class FakeDocument extends FakeEventTarget {
  constructor() {
    super();
    this.body = new FakeElement('body', this);
  }

  createElement(tagName) {
    return new FakeElement(tagName, this);
  }

  querySelector(selector) {
    return this.body.querySelector(selector);
  }

  querySelectorAll(selector) {
    return this.body.querySelectorAll(selector);
  }
}

class FakeCustomEvent {
  constructor(type, init = {}) {
    this.type = String(type || '');
    this.detail = init.detail;
    this.bubbles = Boolean(init.bubbles);
    this.cancelable = Boolean(init.cancelable);
    this.defaultPrevented = false;
  }

  preventDefault() {
    this.defaultPrevented = true;
  }
}

function createWindowLike(document) {
  const listeners = new Map();
  const trackedSetTimeout = (...args) => {
    const handle = setTimeout(...args);
    if (typeof handle?.unref === 'function') handle.unref();
    return handle;
  };
  const win = {
    document,
    location: { hash: '' },
    navigator: { userAgent: 'node-test' },
    prompt: () => '',
    confirm: () => true,
    open: () => null,
    setTimeout: trackedSetTimeout,
    clearTimeout,
    addEventListener(type, handler) {
      const key = String(type);
      const list = listeners.get(key) || [];
      list.push(handler);
      listeners.set(key, list);
    },
    removeEventListener(type, handler) {
      const key = String(type);
      const list = listeners.get(key) || [];
      listeners.set(key, list.filter((fn) => fn !== handler));
    },
    async dispatchEvent(event) {
      const evt = event || { type: 'event' };
      const key = String(evt.type || '');
      const list = [...(listeners.get(key) || [])];
      for (const handler of list) {
        try {
          const result = handler(evt);
          if (result && typeof result.then === 'function') {
            result.catch(() => {});
          }
        } catch (_) {
          // ignore in harness dispatch
        }
      }
      return true;
    },
  };
  return win;
}

function buildTransformedSource() {
  let source = fs.readFileSync(SOURCE_PATH, 'utf8');
  const inboxImport = "import * as InboxSDK from '@inboxsdk/core';";
  const qmImport = "import { ClearledgrQueueManager } from '../queue-manager.js';";
  if (!source.includes(inboxImport) || !source.includes(qmImport)) {
    throw new Error('Unexpected inboxsdk-layer import format; integration harness transform needs update.');
  }
  source = source.replace(inboxImport, 'const InboxSDK = globalThis.__TEST_INBOXSDK__;');
  source = source.replace(qmImport, 'const { ClearledgrQueueManager } = globalThis.__TEST_QUEUE_MANAGER_MODULE__;');
  const bootstrapCall = '\nbootstrap();\n';
  const hookBlock = `
globalThis.__clearledgrBootstrapPromise = bootstrap();
globalThis.__clearledgrInboxsdkLayerTestApi = {
  waitForBootstrap: async () => globalThis.__clearledgrBootstrapPromise,
  getState: () => ({
    sdk,
    queueManager,
    globalSidebarEl,
    workSidebarEl,
    opsSidebarEl,
    currentThreadId,
    selectedItemId,
    queueState: Array.isArray(queueState) ? [...queueState] : [],
    scanStatus: scanStatus ? { ...scanStatus } : {},
  }),
  renderSidebar,
  renderAllSidebars,
  renderThreadContext,
  registerThreadHandler,
  registerThreadRowLabels,
  initializeSidebar,
};
`;
  if (source.includes(bootstrapCall)) {
    source = source.replace(bootstrapCall, `\n${hookBlock}\n`);
  } else if (source.trimEnd().endsWith('bootstrap();')) {
    source = source.replace(/bootstrap\(\);\s*$/, `${hookBlock}\n`);
  } else {
    throw new Error('Could not locate bootstrap() call in inboxsdk-layer.js');
  }
  return source;
}

function createMockQueueManagerClass(records, options = {}) {
  return class MockQueueManager {
    constructor() {
      this.runtimeConfig = {
        backendUrl: 'http://localhost:8000',
        organizationId: 'default',
        userEmail: 'extension@example.com',
        financeLeadEmail: 'finance@example.com',
      };
      this._debugUiEnabled = options.debugUiEnabled !== false;
      this._onQueueUpdated = null;
      this.queue = [];
      this.calls = {
        init: 0,
        refreshQueue: 0,
        scanNow: 0,
        authorizeGmailNow: 0,
        fetchAuditTrail: 0,
        fetchItemContext: 0,
        verifyConfidence: 0,
        hydrateItemContext: 0,
        dispatchAgentMacro: 0,
        syncAgentSessions: 0,
        syncQueueWithBackend: 0,
        requestApproval: 0,
        approveAndPost: 0,
        submitBudgetDecision: 0,
        prepareVendorFollowup: 0,
        nudgeApproval: 0,
        retryFailedPost: 0,
        routeLowRiskForApproval: 0,
        retryRecoverableFailure: 0,
      };
      records.queueManagerInstance = this;
    }

    async init() {
      this.calls.init += 1;
      return true;
    }

    onQueueUpdated(handler) {
      this._onQueueUpdated = handler;
    }

    emitQueueUpdated(queue = [], status = { state: 'idle' }, agentSessions = new Map(), tabs = [], agentInsights = new Map(), sources = new Map(), contexts = new Map(), kpis = null) {
      const queueItems = Array.isArray(queue) ? queue : [];
      this.queue = queueItems.map((item) => ({ ...item }));
      const normalizedSources = sources instanceof Map ? new Map(sources) : new Map();
      const normalizedContexts = contexts instanceof Map ? new Map(contexts) : new Map();
      for (const item of queueItems) {
        if (!item?.id) continue;
        if (!normalizedSources.has(item.id)) {
          normalizedSources.set(item.id, [
            {
              source_type: 'gmail_thread',
              source_ref: item.thread_id || item.threadId || `thread-${item.id}`,
              subject: item.subject || '',
              sender: item.sender || '',
              detected_at: item.updated_at || item.created_at || null,
            },
          ]);
        }
        if (!normalizedContexts.has(item.id)) {
          normalizedContexts.set(item.id, {
            erp: {
              connector_available: true,
              state: item.state || 'received',
              erp_reference: item.erp_reference || null,
            },
            po_match: {},
            budget: {},
          });
        }
      }
      if (typeof this._onQueueUpdated === 'function') {
        this._onQueueUpdated(queueItems, status, agentSessions, tabs, agentInsights, normalizedSources, normalizedContexts, kpis);
      }
    }

    isDebugUiEnabled() {
      return this._debugUiEnabled;
    }

    setDebugUiEnabled(value) {
      this._debugUiEnabled = Boolean(value);
    }

    async authorizeGmailNow() {
      this.calls.authorizeGmailNow += 1;
      return { success: true };
    }

    async refreshQueue() {
      this.calls.refreshQueue += 1;
      return true;
    }

    async scanNow() {
      this.calls.scanNow += 1;
      return true;
    }

    getKpiSnapshot() {
      return null;
    }

    parseMetadata(value) {
      if (!value) return {};
      if (typeof value === 'object') return value;
      try {
        return JSON.parse(String(value));
      } catch (_) {
        return {};
      }
    }

    async fetchAuditTrail() {
      this.calls.fetchAuditTrail += 1;
      if (typeof options.fetchAuditTrail === 'function') {
        return await options.fetchAuditTrail(...arguments, this);
      }
      return [];
    }

    async fetchItemContext() {
      this.calls.fetchItemContext += 1;
      if (typeof options.fetchItemContext === 'function') {
        return await options.fetchItemContext(...arguments, this);
      }
      return null;
    }

    async hydrateItemContext() {
      this.calls.hydrateItemContext += 1;
      if (typeof options.hydrateItemContext === 'function') {
        return await options.hydrateItemContext(...arguments, this);
      }
      return true;
    }

    async verifyConfidence() {
      this.calls.verifyConfidence += 1;
      if (typeof options.verifyConfidence === 'function') {
        return await options.verifyConfidence(...arguments, this);
      }
      return { confidence_pct: 96, can_post: true, mismatches: [] };
    }

    async previewAgentCommand() {
      return null;
    }

    async dispatchAgentMacro(sessionId, macroName, options = {}) {
      this.calls.dispatchAgentMacro += 1;
      records.dispatchedMacros = records.dispatchedMacros || [];
      records.dispatchedMacros.push({ sessionId, macroName, options });
      return {
        status: options?.dryRun ? 'preview' : 'dispatched',
        session_id: sessionId,
        macro_name: macroName,
      };
    }

    async syncAgentSessions() {
      this.calls.syncAgentSessions += 1;
      return true;
    }

    async syncQueueWithBackend() {
      this.calls.syncQueueWithBackend += 1;
      this.emitQueueUpdated(this.queue, { state: 'idle' });
      return true;
    }

    async nudgeApproval(item) {
      this.calls.nudgeApproval += 1;
      return { status: 'nudged', email_id: item?.id || item?.thread_id || 'unknown' };
    }

    async requestApproval(item, _options = {}) {
      this.calls.requestApproval += 1;
      return { status: 'pending_approval', email_id: item?.id || item?.thread_id || 'unknown' };
    }

    async approveAndPost(item, requestOptions = {}) {
      this.calls.approveAndPost += 1;
      records.approveAndPostCalls = records.approveAndPostCalls || [];
      records.approveAndPostCalls.push({ item, options: requestOptions });
      if (typeof options.approveAndPost === 'function') {
        return await options.approveAndPost(item, requestOptions, this);
      }
      return {
        status: 'posted',
        ap_item_id: item?.id || '',
        erp_reference: item?.id ? `ERP-${item.id}` : null,
      };
    }

    async submitBudgetDecision(item, decision, reason) {
      this.calls.submitBudgetDecision += 1;
      records.submitBudgetDecisionCalls = records.submitBudgetDecisionCalls || [];
      records.submitBudgetDecisionCalls.push({ item, decision, reason });
      if (typeof options.submitBudgetDecision === 'function') {
        return await options.submitBudgetDecision(item, decision, reason, this);
      }
      if (decision === 'approve_override') return { status: 'approved' };
      if (decision === 'request_budget_adjustment') return { status: 'needs_info' };
      if (decision === 'reject') return { status: 'rejected' };
      return { status: 'ok' };
    }

    async prepareVendorFollowup(item, _options = {}) {
      this.calls.prepareVendorFollowup += 1;
      return {
        status: 'prepared',
        email_id: item?.id || item?.thread_id || 'unknown',
        draft_id: `draft-${item?.id || 'unknown'}`,
      };
    }

    async retryFailedPost(item) {
      this.calls.retryFailedPost += 1;
      if (typeof options.retryFailedPost === 'function') {
        return await options.retryFailedPost(item, this);
      }
      this.queue = (Array.isArray(this.queue) ? this.queue : []).map((entry) =>
        entry?.id === item?.id
          ? { ...entry, state: 'posted_to_erp', erp_reference: entry.erp_reference || `ERP-${entry.id || 'ref'}` }
          : entry
      );
      return {
        status: 'posted',
        ap_item_id: item?.id || '',
        erp_reference: item?.id ? `ERP-${item.id}` : null,
      };
    }

    async routeLowRiskForApproval(item, _options = {}) {
      this.calls.routeLowRiskForApproval += 1;
      this.queue = (Array.isArray(this.queue) ? this.queue : []).map((entry) =>
        entry?.id === item?.id
          ? { ...entry, state: 'needs_approval', next_action: 'approve_or_reject' }
          : entry
      );
      return {
        status: 'pending_approval',
        ap_item_id: item?.id || '',
      };
    }

    async retryRecoverableFailure(item, _options = {}) {
      this.calls.retryRecoverableFailure += 1;
      this.queue = (Array.isArray(this.queue) ? this.queue : []).map((entry) =>
        entry?.id === item?.id
          ? { ...entry, state: 'posted_to_erp', erp_reference: entry.erp_reference || `ERP-${entry.id || 'ref'}` }
          : entry
      );
      return {
        status: 'posted',
        ap_item_id: item?.id || '',
        erp_reference: item?.id ? `ERP-${item.id}` : null,
      };
    }

    findMergeCandidates() {
      return [];
    }
  };
}

function createMockInboxSdk(records) {
  const handlers = {
    compose: null,
    threadView: null,
    threadRowView: null,
  };
  const sdk = {
    Global: {
      addSidebarContentPanel(payload) {
        records.sidebarPanels.push(payload);
      },
    },
    Compose: {
      registerComposeViewHandler(handler) {
        handlers.compose = handler;
      },
      openNewComposeView() {
        records.composeOpenCalls += 1;
      },
    },
    Conversations: {
      registerThreadViewHandler(handler) {
        handlers.threadView = handler;
      },
    },
    Lists: {
      registerThreadRowViewHandler(handler) {
        handlers.threadRowView = handler;
      },
    },
  };
  records.sdk = sdk;
  records.sdkHandlers = handlers;

  return {
    load: async (...args) => {
      records.inboxSdkLoadCalls.push(args);
      return sdk;
    },
  };
}

async function createInboxSdkIntegrationRuntime(options = {}) {
  const source = buildTransformedSource();
  const records = {
    inboxSdkLoadCalls: [],
    sidebarPanels: [],
    composeOpenCalls: 0,
    sdkHandlers: null,
    sdk: null,
    queueManagerInstance: null,
  };

  const document = new FakeDocument();
  const window = createWindowLike(document);
  const InboxSDK = createMockInboxSdk(records);
  const MockQueueManager = createMockQueueManagerClass(records, options.queueManager || {});

  const trackedSetTimeout = (...args) => {
    const handle = setTimeout(...args);
    if (typeof handle?.unref === 'function') handle.unref();
    return handle;
  };
  const context = {
    console,
    setTimeout: trackedSetTimeout,
    clearTimeout,
    Promise,
    Date,
    Math,
    JSON,
    Intl,
    String,
    Number,
    Boolean,
    Array,
    Object,
    Set,
    Map,
    URL,
    encodeURIComponent,
    decodeURIComponent,
    window,
    document,
    navigator: window.navigator,
    CustomEvent: FakeCustomEvent,
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    globalThis: null,
    __TEST_INBOXSDK__: InboxSDK,
    __TEST_QUEUE_MANAGER_MODULE__: { ClearledgrQueueManager: MockQueueManager },
  };
  context.globalThis = context;
  window.window = window;
  window.globalThis = context;

  vm.runInNewContext(source, context, { filename: 'inboxsdk-layer.integration.vm.js' });
  if (context.__clearledgrBootstrapPromise) {
    await context.__clearledgrBootstrapPromise;
  }

  const api = context.__clearledgrInboxsdkLayerTestApi;
  if (!api) {
    throw new Error('Test API not exposed by transformed inboxsdk-layer module');
  }

  const flush = async () => {
    await Promise.resolve();
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await Promise.resolve();
  };

  return {
    context,
    window,
    document,
    records,
    api,
    getState: () => api.getState(),
    getQueueManager: () => records.queueManagerInstance,
    flush,
    createThreadView(threadId = 'thread-1') {
      const listeners = {};
      return {
        async getThreadIDAsync() { return threadId; },
        on(event, handler) { listeners[String(event)] = handler; },
        async destroy() {
          if (typeof listeners.destroy === 'function') {
            await listeners.destroy();
          }
        },
      };
    },
    createThreadRowView(threadId = 'thread-1') {
      const labels = [];
      return {
        labels,
        async getThreadIDAsync() { return threadId; },
        addLabel(payload) { labels.push(payload); },
      };
    },
  };
}

module.exports = {
  SOURCE_PATH,
  FakeElement,
  FakeDocument,
  createInboxSdkIntegrationRuntime,
};
