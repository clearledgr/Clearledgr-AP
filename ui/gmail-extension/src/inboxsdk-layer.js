/**
 * Clearledgr AP v1 InboxSDK Layer — Preact + HTM
 *
 * Entry point for the Gmail extension sidebar. Handles InboxSDK integration
 * and mounts the Preact component tree into the sidebar container.
 *
 * Architecture:
 *   InboxSDK bootstrap → QueueManager init → Preact mount → reactive re-renders
 *   State flows through a reactive store (utils/store.js).
 *   Components live in components/SidebarApp.js.
 *   Business logic utilities live in utils/formatters.js.
 *   CSS is extracted to styles.js.
 */
import * as InboxSDK from '@inboxsdk/core';
import { h, render } from 'preact';
import htm from 'htm';
import { ClearledgrQueueManager } from '../queue-manager.js';
import store from './utils/store.js';
import SidebarApp, { showToast } from './components/SidebarApp.js';
import { STATE_LABELS, STATE_COLORS, getStateLabel, readLocalStorage, writeLocalStorage, getAssetUrl } from './utils/formatters.js';

const html = htm.bind(h);
const APP_ID = 'sdk_Clearledgr2026_dc12c60472';
const INIT_KEY = '__clearledgr_ap_v1_inboxsdk_initialized';
const LOGO_PATH = 'icons/icon48.png';
const STORAGE_ACTIVE_AP_ITEM_ID = 'clearledgr_active_ap_item_id';

let sdk = null;
let queueManager = null;
let _pendingComposePrefill = null;
let sidebarContainer = null;

// ==================== PREACT MOUNT ====================

function mountSidebar() {
  if (!sidebarContainer) return;
  render(html`<${SidebarApp} queueManager=${queueManager} />`, sidebarContainer);
}

// ==================== SIDEBAR INIT ====================

function initializeSidebar() {
  const container = document.createElement('div');
  container.className = 'cl-sidebar';
  sidebarContainer = container;

  // Mount Preact into the container
  mountSidebar();

  // Register with InboxSDK
  const logoUrl = getAssetUrl(LOGO_PATH);
  sdk.Global.addSidebarContentPanel({
    title: 'Clearledgr AP',
    iconUrl: logoUrl || null,
    el: container,
    hideTitleBar: false,
  });

  // Restore last active item
  const restoredId = readLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID);
  if (restoredId) {
    store.update({ selectedItemId: restoredId });
  }
}

// ==================== THREAD HANDLERS ====================

function registerThreadHandler() {
  sdk.Conversations.registerThreadViewHandler((threadView) => {
    const getId = async () => {
      if (typeof threadView.getThreadIDAsync === 'function') {
        return await threadView.getThreadIDAsync();
      }
      return null;
    };

    getId()
      .then((threadId) => {
        store.update({ currentThreadId: threadId });
        const threadItem = store.findItemByThreadId(threadId);
        if (threadItem?.id) {
          store.update({ selectedItemId: threadItem.id });
          writeLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID, threadItem.id);
        }

        threadView.on('destroy', () => {
          if (store.currentThreadId === threadId) {
            store.update({ currentThreadId: null });
          }
        });
      })
      .catch(() => { /* ignore */ });
  });
}

function registerThreadRowLabels() {
  if (!sdk?.Lists || typeof sdk.Lists.registerThreadRowViewHandler !== 'function') return;

  sdk.Lists.registerThreadRowViewHandler((threadRowView) => {
    const getId = async () => {
      if (typeof threadRowView.getThreadIDAsync === 'function') {
        return await threadRowView.getThreadIDAsync();
      }
      return null;
    };

    getId()
      .then((threadId) => {
        if (!threadId || store.rowDecorated.has(threadId)) return;
        const item = store.findItemByThreadId(threadId);
        if (!item) return;
        store.rowDecorated.add(threadId);
        const label = getStateLabel(item.state || 'received');
        const color = STATE_COLORS[item.state] || '#2563eb';
        try {
          threadRowView.addLabel({
            title: label,
            foregroundColor: '#ffffff',
            backgroundColor: color,
          });
        } catch (_) { /* ignore */ }
      })
      .catch(() => { /* ignore */ });
  });
}

// ==================== BOOTSTRAP ====================

async function bootstrap() {
  if (window[INIT_KEY]) return;
  window[INIT_KEY] = true;

  try {
    sdk = await InboxSDK.load(2, APP_ID, {
      eventTracking: false,
      globalErrorLogging: false,
    });
  } catch (error) {
    console.error('[Clearledgr] InboxSDK failed to load', error);
    return;
  }

  // Pre-fill compose views opened by "Draft vendor reply"
  sdk.Compose.registerComposeViewHandler((composeView) => {
    if (_pendingComposePrefill) {
      const prefill = _pendingComposePrefill;
      _pendingComposePrefill = null;
      try {
        if (prefill.to) composeView.setToRecipients([{ emailAddress: prefill.to }]);
        if (prefill.subject) composeView.setSubject(prefill.subject);
        if (prefill.body) composeView.setBodyHTML(prefill.body.replace(/\n/g, '<br>'));
      } catch (_) { /* ignore */ }
    }
  });

  // Initialize queue manager
  queueManager = new ClearledgrQueueManager();
  await queueManager.init();

  // Subscribe to queue updates → update reactive store → Preact re-renders
  queueManager.onQueueUpdated((queue, status, agentSessions, tabs, agentInsights, sources, contexts) => {
    const queueState = Array.isArray(queue) ? queue : [];

    // Clean up selected item if no longer in queue
    let selectedItemId = store.selectedItemId;
    if (selectedItemId && !queueState.find(i => i.id === selectedItemId || i.invoice_key === selectedItemId)) {
      selectedItemId = null;
      writeLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID, '');
    }

    // Restore from localStorage if nothing selected
    if (!selectedItemId) {
      const restored = readLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID);
      if (restored && queueState.find(i => i.id === restored || i.invoice_key === restored)) {
        selectedItemId = restored;
      }
    }

    store.update({
      queueState,
      scanStatus: status || {},
      agentSessionsState: agentSessions instanceof Map ? agentSessions : new Map(),
      browserTabContext: Array.isArray(tabs) ? tabs : [],
      agentInsightsState: agentInsights instanceof Map ? agentInsights : new Map(),
      sourcesState: sources instanceof Map ? sources : new Map(),
      contextState: contexts instanceof Map ? contexts : new Map(),
      selectedItemId,
    });

    // Decorate thread rows with state labels
    registerThreadRowLabels();
  });

  // Mount sidebar and register handlers
  initializeSidebar();
  registerThreadHandler();
  registerThreadRowLabels();
}

bootstrap();
