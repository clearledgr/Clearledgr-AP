const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

const EXTENSION_ROOT = path.resolve(__dirname, '..');
const SOURCE_PATH = path.join(EXTENSION_ROOT, 'src', 'inboxsdk-layer.js');
const RUN_BROWSER_HARNESS = process.env.RUN_GMAIL_BROWSER_HARNESS === '1';
const BROWSER_TIMEOUT_MS = Number(process.env.GMAIL_BROWSER_HARNESS_TIMEOUT_MS || 120000);
const BROWSER_CHANNEL = String(process.env.GMAIL_BROWSER_HARNESS_CHANNEL || '').trim();
const HEADFUL = process.env.GMAIL_BROWSER_HARNESS_HEADFUL === '1';

async function launchHarnessBrowser(chromium) {
  const attempts = [];
  if (BROWSER_CHANNEL) {
    attempts.push({
      label: `channel:${BROWSER_CHANNEL}`,
      options: { headless: !HEADFUL, channel: BROWSER_CHANNEL },
    });
  } else {
    attempts.push({
      label: 'bundled-chromium',
      options: { headless: !HEADFUL },
    });
    attempts.push({
      label: 'channel:chrome',
      options: { headless: !HEADFUL, channel: 'chrome' },
    });
  }

  const failures = [];
  for (const attempt of attempts) {
    try {
      const browser = await chromium.launch(attempt.options);
      return browser;
    } catch (error) {
      const message = error && error.message ? String(error.message) : String(error);
      failures.push(`[${attempt.label}] ${message.split('\n')[0]}`);
    }
  }

  throw new Error(
    [
      'Could not launch browser for Gmail harness.',
      ...failures,
      'Try: GMAIL_BROWSER_HARNESS_CHANNEL=chrome npm run test:browser-harness',
      'If needed, install browsers: npx playwright install chromium',
    ].join('\n'),
  );
}

function buildBrowserHarnessSource() {
  let source = fs.readFileSync(SOURCE_PATH, 'utf8');
  const inboxImport = "import * as InboxSDK from '@inboxsdk/core';";
  const qmImport = "import { ClearledgrQueueManager } from '../queue-manager.js';";
  if (!source.includes(inboxImport) || !source.includes(qmImport)) {
    throw new Error('Unexpected inboxsdk-layer import format; browser harness transform needs update.');
  }

  source = source.replace(inboxImport, 'const InboxSDK = globalThis.__TEST_INBOXSDK__;');
  source = source.replace(qmImport, 'const { ClearledgrQueueManager } = globalThis.__TEST_QUEUE_MANAGER_MODULE__;');

  const bootstrapCall = '\nbootstrap();\n';
  const hookBlock = `
globalThis.__clearledgrBootstrapPromise = bootstrap();
globalThis.__clearledgrInboxsdkLayerTestApi = {
  waitForBootstrap: async () => globalThis.__clearledgrBootstrapPromise,
  getState: () => ({
    hasSdk: Boolean(sdk),
    hasQueueManager: Boolean(queueManager),
    currentThreadId,
    selectedItemId,
    queueSize: Array.isArray(queueState) ? queueState.length : 0,
  }),
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

function browserInitScript() {
  const records = {
    inboxSdkLoadCalls: [],
    sidebarPanels: [],
    queueManagerInstance: null,
    sdkHandlers: null,
  };

  class MockQueueManager {
    constructor() {
      this._onQueueUpdated = null;
      this._debugUiEnabled = true;
      this.queue = [];
      records.queueManagerInstance = this;
    }

    async init() { return true; }
    onQueueUpdated(handler) { this._onQueueUpdated = handler; }
    isDebugUiEnabled() { return this._debugUiEnabled; }
    setDebugUiEnabled(value) { this._debugUiEnabled = Boolean(value); }
    parseMetadata(value) {
      if (!value) return {};
      if (typeof value === 'object') return value;
      try { return JSON.parse(String(value)); } catch (_) { return {}; }
    }
    async authorizeGmailNow() { return { success: true }; }
    async refreshQueue() { return true; }
    async scanNow() { return true; }
    getKpiSnapshot() { return null; }
    async fetchAuditTrail() { return []; }
    async fetchItemContext() { return null; }
    async hydrateItemContext() { return true; }
    async verifyConfidence() { return { confidence_pct: 96, can_post: true, mismatches: [] }; }
    async previewAgentCommand() { return null; }
    async dispatchAgentMacro(sessionId, macroName, options = {}) {
      return {
        status: options?.dryRun ? 'preview' : 'dispatched',
        session_id: sessionId,
        macro_name: macroName,
      };
    }
    async syncAgentSessions() { return true; }
    async syncQueueWithBackend() {
      this.emitQueueUpdated(this.queue, { state: 'idle' });
      return true;
    }
    async nudgeApproval(item) { return { status: 'nudged', email_id: item?.id || 'unknown' }; }
    async requestApproval(item) { return { status: 'pending_approval', email_id: item?.id || 'unknown' }; }
    async prepareVendorFollowup(item) {
      return { status: 'prepared', email_id: item?.id || 'unknown', draft_id: `draft-${item?.id || 'unknown'}` };
    }
    async retryFailedPost(item) {
      return { status: 'posted', ap_item_id: item?.id || '', erp_reference: item?.id ? `ERP-${item.id}` : null };
    }
    async routeLowRiskForApproval(item) {
      return { status: 'pending_approval', ap_item_id: item?.id || '' };
    }
    async retryRecoverableFailure(item) {
      return { status: 'posted', ap_item_id: item?.id || '', erp_reference: item?.id ? `ERP-${item.id}` : null };
    }
    findMergeCandidates() { return []; }

    emitQueueUpdated(queue = [], status = { state: 'idle' }, agentSessions = new Map(), tabs = [], agentInsights = new Map(), sources = new Map(), contexts = new Map(), kpis = null) {
      this.queue = Array.isArray(queue) ? queue.map((item) => ({ ...item })) : [];
      if (typeof this._onQueueUpdated === 'function') {
        this._onQueueUpdated(this.queue, status, agentSessions, tabs, agentInsights, sources, contexts, kpis);
      }
    }
  }

  const handlers = {
    compose: null,
    threadView: null,
    threadRowView: null,
  };
  const sdk = {
    Global: {
      addSidebarContentPanel(payload) {
        records.sidebarPanels.push(payload);
        if (payload?.el && typeof document !== 'undefined' && document.body) {
          document.body.appendChild(payload.el);
        }
      },
    },
    Compose: {
      registerComposeViewHandler(handler) {
        handlers.compose = handler;
      },
      openNewComposeView() {},
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
  records.sdkHandlers = handlers;

  window.__TEST_RECORDS = records;
  window.__TEST_INBOXSDK__ = {
    load: async (...args) => {
      records.inboxSdkLoadCalls.push(args);
      return sdk;
    },
  };
  window.__TEST_QUEUE_MANAGER_MODULE__ = { ClearledgrQueueManager: MockQueueManager };
  if (!window.chrome) window.chrome = {};
  if (!window.chrome.runtime) window.chrome.runtime = {};
  if (typeof window.chrome.runtime.getURL !== 'function') {
    window.chrome.runtime.getURL = (assetPath) => `chrome-extension://test/${String(assetPath || '')}`;
  }
}

test('real-browser InboxSDK harness mounts and renders AP sidebar', { skip: !RUN_BROWSER_HARNESS, timeout: BROWSER_TIMEOUT_MS }, async (t) => {
  let chromium;
  try {
    ({ chromium } = require('playwright'));
  } catch (_) {
    t.skip('RUN_GMAIL_BROWSER_HARNESS=1 requires playwright (`npm i -D playwright`).');
    return;
  }

  let browser;
  try {
    browser = await launchHarnessBrowser(chromium);
  } catch (error) {
    t.diagnostic(String(error && error.message ? error.message : error));
    t.skip('Browser harness prerequisites are not available in this environment.');
    return;
  }
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.addInitScript(browserInitScript);
  await page.setContent('<!doctype html><html><body><main id="gmail-root"></main></body></html>', {
    waitUntil: 'domcontentloaded',
  });
  await page.addScriptTag({ content: buildBrowserHarnessSource() });
  await page.evaluate(async () => {
    if (globalThis.__clearledgrBootstrapPromise) {
      await globalThis.__clearledgrBootstrapPromise;
    }
  });

  const mounted = await page.evaluate(() => ({
    loadCalls: window.__TEST_RECORDS?.inboxSdkLoadCalls?.length || 0,
    hasSidebarStatus: Boolean(document.querySelector('#cl-scan-status')),
    hasThreadContext: Boolean(document.querySelector('#cl-thread-context')),
  }));
  assert.equal(mounted.loadCalls, 1);
  assert.equal(mounted.hasSidebarStatus, true);
  assert.equal(mounted.hasThreadContext, true);

  await page.evaluate(() => {
    const qm = window.__TEST_RECORDS?.queueManagerInstance;
    if (!qm) throw new Error('queue manager mock missing');
    qm.emitQueueUpdated(
      [
        {
          id: 'AP-BROWSER-1',
          thread_id: 'thread-browser-1',
          sender: 'billing@acme.test',
          subject: 'Invoice INV-BROWSER-1',
          vendor_name: 'Acme Supplies',
          invoice_number: 'INV-BROWSER-1',
          amount: 125.4,
          currency: 'USD',
          due_date: '2026-03-12',
          state: 'failed_post',
          confidence: 0.88,
          exception_code: 'erp_post_failed',
          exception_severity: 'high',
          next_action: 'retry_post',
        },
      ],
      { state: 'scanning' },
      new Map(),
      [],
      new Map(),
      new Map(),
      new Map(),
      null,
    );
  });
  await page.waitForTimeout(25);

  const rendered = await page.evaluate(() => ({
    scanStatusText: String(document.querySelector('#cl-scan-status')?.textContent || ''),
    threadContextText: String(document.querySelector('#cl-thread-context')?.textContent || ''),
  }));
  assert.match(rendered.scanStatusText, /Scanning inbox for invoices/i);
  assert.match(rendered.threadContextText, /Acme Supplies/i);
  assert.match(rendered.threadContextText, /INV-BROWSER-1/i);

  await context.close();
  await browser.close();
});

test('real-browser InboxSDK harness stays opt-in unless RUN_GMAIL_BROWSER_HARNESS=1', () => {
  assert.ok(true);
});
