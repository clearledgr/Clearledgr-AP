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

// Route imports (Gmail-native admin pages — Streak pattern)
import {
  ROUTES,
  DEFAULT_ROUTE,
  getNavEligibleRoutes,
  getVisibleNavRoutes,
  readRoutePreferences,
  writeRoutePreferences,
} from './routes/route-registry.js';
import { createAdminApi, setToastFn } from './routes/admin-api.js';
import { createOAuthBridge } from './routes/oauth-bridge.js';
import { ROUTE_CSS } from './routes/route-styles.js';
import HomePage from './routes/pages/HomePage.js';
import ActivityPage from './routes/pages/ActivityPage.js';
import ConnectionsPage from './routes/pages/ConnectionsPage.js';
import RulesPage from './routes/pages/RulesPage.js';
import TeamPage from './routes/pages/TeamPage.js';
import CompanyPage from './routes/pages/CompanyPage.js';
import PlanPage from './routes/pages/PlanPage.js';
import ReconciliationPage from './routes/pages/ReconciliationPage.js';
import HealthPage from './routes/pages/HealthPage.js';
import PipelinePage from './routes/pages/PipelinePage.js';
import InvoiceDetailPage from './routes/pages/InvoiceDetailPage.js';
import VendorsPage from './routes/pages/VendorsPage.js';
import { hasOpsAccess } from './routes/route-helpers.js';
import { watchForSettingsPage } from './settings-tab.js';

const html = htm.bind(h);
const APP_ID = 'sdk_Clearledgr2026_dc12c60472';
const INIT_KEY = '__clearledgr_ap_v1_inboxsdk_initialized';
const LOGO_PATH = 'icons/icon48.png';
const STORAGE_ACTIVE_AP_ITEM_ID = 'clearledgr_active_ap_item_id';

let sdk = null;
let queueManager = null;
let _pendingComposePrefill = null;
let sidebarContainer = null;
let appMenuItemView = null;
let appMenuPanelView = null;
let appMenuNavItemViews = [];
let fallbackNavItemViews = [];

// ==================== FONT LOADING ====================

function injectFonts() {
  // Inject Google Fonts link tags into page <head> (CSP-safe, not @import)
  if (document.getElementById('cl-fonts-loaded')) return;
  const marker = document.createElement('meta');
  marker.id = 'cl-fonts-loaded';
  document.head.appendChild(marker);

  const preconnect1 = document.createElement('link');
  preconnect1.rel = 'preconnect';
  preconnect1.href = 'https://fonts.googleapis.com';
  document.head.appendChild(preconnect1);

  const preconnect2 = document.createElement('link');
  preconnect2.rel = 'preconnect';
  preconnect2.href = 'https://fonts.gstatic.com';
  preconnect2.crossOrigin = 'anonymous';
  document.head.appendChild(preconnect2);

  const fontLink = document.createElement('link');
  fontLink.rel = 'stylesheet';
  fontLink.href = 'https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700&family=Instrument+Sans:wght@400;500;600;700&display=swap';
  document.head.appendChild(fontLink);

  // Geist Mono via stylesheet injection (CDN doesn't have a Google Fonts URL)
  const monoStyle = document.createElement('style');
  monoStyle.textContent = `
    @font-face { font-family: 'Geist Mono'; src: url('https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-mono/GeistMono-Regular.woff2') format('woff2'); font-weight: 400; font-display: swap; }
    @font-face { font-family: 'Geist Mono'; src: url('https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-mono/GeistMono-Medium.woff2') format('woff2'); font-weight: 500; font-display: swap; }
    @font-face { font-family: 'Geist Mono'; src: url('https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-mono/GeistMono-SemiBold.woff2') format('woff2'); font-weight: 600; font-display: swap; }
  `;
  document.head.appendChild(monoStyle);
}

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
      .then(async (threadId) => {
        store.update({ currentThreadId: threadId });
        let item = store.findItemByThreadId(threadId);
        if (item?.id) {
          store.update({ selectedItemId: item.id });
          writeLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID, item.id);
        } else if (threadId && queueManager) {
          // Streak pattern: fetch from backend if not in local queue
          try {
            const result = await queueManager.backendFetch(
              `/extension/by-thread/${encodeURIComponent(threadId)}`
            );
            if (result?.ok) {
              const data = await result.json();
              if (data?.found && data?.item) {
                item = data.item;
                const currentQueue = store.queueState || [];
                if (!currentQueue.find(i => i.id === item.id)) {
                  store.update({ queueState: [...currentQueue, item] });
                }
                store.update({ selectedItemId: item.id });
                writeLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID, item.id);
              }
            }
          } catch (_) { /* no invoice for this thread — that's fine */ }
        }

        // Inject thread-top banner for invoice threads (Mixmax-style)
        if (item && typeof threadView.addNoticeBar === 'function') {
          injectInvoiceBanner(threadView, item);
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

function injectInvoiceBanner(threadView, item) {
  const state = String(item.state || '').toLowerCase();
  const vendor = item.vendor_name || item.vendor || 'Unknown vendor';
  const amount = Number(item.amount);
  const amountStr = Number.isFinite(amount)
    ? '$' + amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '';
  const currency = item.currency || 'USD';

  // Banner color based on state
  const stateConfig = {
    needs_approval:   { bg: '#fef9ee', border: '#d97706', text: '#92400e', label: 'Needs approval' },
    pending_approval: { bg: '#fef9ee', border: '#d97706', text: '#92400e', label: 'Pending approval' },
    approved:         { bg: '#ECFDF5', border: '#10B981', text: '#059669', label: 'Approved' },
    ready_to_post:    { bg: '#ECFDF5', border: '#10B981', text: '#059669', label: 'Ready to post' },
    posted_to_erp:    { bg: '#ECFDF5', border: '#10B981', text: '#059669', label: 'Posted to ERP' },
    rejected:         { bg: '#fef2f2', border: '#dc2626', text: '#991b1b', label: 'Rejected' },
    failed_post:      { bg: '#fef2f2', border: '#dc2626', text: '#991b1b', label: 'ERP post failed' },
    needs_info:       { bg: '#fef9ee', border: '#d97706', text: '#92400e', label: 'Info requested' },
  };
  const cfg = stateConfig[state] || { bg: '#f0f0ed', border: '#8c8c8c', text: '#525252', label: state.replace(/_/g, ' ') };

  const el = document.createElement('div');
  el.style.cssText = `
    display:flex; align-items:center; gap:12px; padding:10px 16px;
    background:${cfg.bg}; border-left:3px solid ${cfg.border};
    font-family:Inter,-apple-system,system-ui,sans-serif; font-size:13px; color:${cfg.text};
  `;

  // Invoice summary
  const summary = document.createElement('span');
  summary.style.cssText = 'flex:1; font-weight:500;';
  summary.textContent = `${vendor} \u2014 ${amountStr} ${currency}`;
  el.appendChild(summary);

  // State pill
  const pill = document.createElement('span');
  pill.style.cssText = `
    font-size:11px; font-weight:600; padding:2px 10px; border-radius:999px;
    background:${cfg.border}20; color:${cfg.text}; text-transform:uppercase; letter-spacing:0.02em;
  `;
  pill.textContent = cfg.label;
  el.appendChild(pill);

  // Actions — approve directly (if user has authority) or route to Slack/Teams
  if (['needs_approval', 'pending_approval', 'needs_info'].includes(state)) {
    const btnStyle = (bg, color, border) => `
      border:${border || 'none'}; border-radius:6px; padding:5px 14px; font-size:12px; font-weight:600;
      cursor:pointer; background:${bg}; color:${color}; font-family:inherit;
    `;

    // Direct approve — the person in Gmail may be the approver
    const approveBtn = document.createElement('button');
    approveBtn.textContent = 'Approve';
    approveBtn.style.cssText = btnStyle('#10B981', '#fff');
    approveBtn.addEventListener('click', () => {
      approveBtn.textContent = 'Approving\u2026';
      approveBtn.disabled = true;
      if (queueManager?.submitForApproval) {
        queueManager.submitForApproval(item).then(() => {
          pill.textContent = 'Approved';
          approveBtn.textContent = 'Approved';
          sendBtn.remove();
        }).catch(() => { approveBtn.textContent = 'Approve'; approveBtn.disabled = false; });
      }
    });

    // Route to Slack/Teams for someone else to approve
    const sendBtn = document.createElement('button');
    sendBtn.textContent = 'Send for approval';
    sendBtn.style.cssText = btnStyle('transparent', cfg.text, `1px solid ${cfg.border}`);
    sendBtn.addEventListener('click', () => {
      sendBtn.textContent = 'Sending\u2026';
      sendBtn.disabled = true;
      if (queueManager?.submitForApproval) {
        queueManager.submitForApproval(item).then(() => {
          pill.textContent = 'Sent to approver';
          sendBtn.textContent = 'Sent';
          approveBtn.remove();
        }).catch(() => { sendBtn.textContent = 'Send for approval'; sendBtn.disabled = false; });
      }
    });

    el.appendChild(approveBtn);
    el.appendChild(sendBtn);
  }

  threadView.addNoticeBar({ el });
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

        // State label (colored pill)
        const label = getStateLabel(item.state || 'received');
        const color = STATE_COLORS[item.state] || '#2563eb';
        try {
          threadRowView.addLabel({
            title: label,
            foregroundColor: '#ffffff',
            backgroundColor: color,
          });
        } catch (_) { /* ignore */ }

        // Vendor + amount label (secondary info)
        const vendor = item.vendor_name || item.vendor || '';
        const amount = Number(item.amount);
        if (vendor || amount) {
          try {
            const amountStr = Number.isFinite(amount) ? `$${amount.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '';
            threadRowView.addLabel({
              title: vendor ? `${vendor}${amountStr ? ' \u00B7 ' + amountStr : ''}` : amountStr,
              foregroundColor: '#525252',
              backgroundColor: '#f0f0ed',
            });
          } catch (_) { /* ignore */ }
        }

        // "Process" action button on hover (like Streak's "+" button)
        if (['needs_approval', 'pending_approval'].includes(item.state)) {
          try {
            if (typeof threadRowView.addActionButton === 'function') {
              threadRowView.addActionButton({
                type: 'ICON_ONLY',
                title: 'Send for approval',
                iconUrl: getAssetUrl(LOGO_PATH) || undefined,
                onClick: () => {
                  if (queueManager?.submitForApproval) {
                    queueManager.submitForApproval(item);
                  }
                },
              });
            }
          } catch (_) { /* ignore */ }
        }
      })
      .catch(() => { /* ignore */ });
  });
}

function registerBulkActions() {
  // Bulk action toolbar button — appears when multiple emails are selected
  if (!sdk?.Toolbars) return;
  try {
    sdk.Toolbars.registerToolbarButtonForList({
      title: 'Process with Clearledgr',
      iconUrl: getAssetUrl(LOGO_PATH) || undefined,
      section: 'METADATA_STATE',
      hasDropdown: false,
      onClick: (event) => {
        const selectedThreads = event.selectedThreadRowViews || [];
        if (!selectedThreads.length) return;

        // Collect thread IDs and trigger bulk processing
        Promise.all(selectedThreads.map(async (trv) => {
          try {
            return typeof trv.getThreadIDAsync === 'function' ? await trv.getThreadIDAsync() : null;
          } catch { return null; }
        })).then(threadIds => {
          const ids = threadIds.filter(Boolean);
          if (!ids.length) return;
          // Send to backend for bulk scan/triage
          const backendUrl = String(queueManager?.runtimeConfig?.backendUrl || '').replace(/\/+$/, '');
          const orgId = queueManager?.runtimeConfig?.organizationId || 'default';
          queueManager.backendFetch(`${backendUrl}/extension/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ organization_id: orgId, email_ids: ids }),
          }).then(() => {
            showToast(`Processing ${ids.length} email${ids.length > 1 ? 's' : ''} with Clearledgr`, 'success');
          }).catch(() => {
            showToast('Bulk processing failed', 'error');
          });
        });
      },
    });
  } catch (err) {
    console.warn('[Clearledgr] Bulk action registration failed:', err);
  }
}

function registerToolbarIcon() {
  // Clearledgr icon in Gmail's top toolbar (like Streak's orange icon)
  if (!sdk?.Toolbars) return;
  try {
    const logoUrl = getAssetUrl(LOGO_PATH);
    sdk.Toolbars.registerToolbarButtonForList({
      title: 'Clearledgr Home',
      iconUrl: logoUrl || undefined,
      section: 'METADATA_STATE',
      onClick: () => {
        sdk.Router.goto(DEFAULT_ROUTE);
      },
    });
  } catch (err) {
    console.warn('[Clearledgr] Toolbar icon registration failed:', err);
  }
}

function registerSearchSuggestions() {
  // Search integration — type in Gmail search to find Clearledgr invoices
  if (!sdk?.Search || typeof sdk.Search.registerSearchSuggestionsProvider !== 'function') return;
  try {
    sdk.Search.registerSearchSuggestionsProvider((query) => {
      const q = (query || '').toLowerCase().trim();
      if (!q) return [];

      const suggestions = [];
      const queue = store.queueState || [];

      // Match against vendor names
      const vendorMatches = queue.filter(item => {
        const vendor = (item.vendor_name || item.vendor || '').toLowerCase();
        return vendor.includes(q);
      });
      for (const item of vendorMatches.slice(0, 3)) {
        const vendor = item.vendor_name || item.vendor || 'Unknown';
        const amount = Number(item.amount);
        const amountStr = Number.isFinite(amount) ? ` \u00B7 $${amount.toLocaleString(undefined, {maximumFractionDigits: 0})}` : '';
        suggestions.push({
          name: `${vendor}${amountStr}`,
          description: `Invoice \u2014 ${getStateLabel(item.state || 'received')}`,
          routeID: 'clearledgr/activity',
          iconUrl: getAssetUrl(LOGO_PATH) || undefined,
        });
      }

      // Suggest Clearledgr pages
      if ('clearledgr'.includes(q) || 'invoice'.includes(q) || 'ap'.includes(q)) {
        suggestions.push({
          name: 'Clearledgr Home',
          description: 'Open Clearledgr dashboard',
          routeID: DEFAULT_ROUTE,
          iconUrl: getAssetUrl(LOGO_PATH) || undefined,
        });
      }
      if ('reconcil'.includes(q) || 'recon'.includes(q) || 'bank'.includes(q)) {
        suggestions.push({
          name: 'Reconciliation',
          description: 'Match bank transactions to invoices',
          routeID: 'clearledgr/reconciliation',
          iconUrl: getAssetUrl(LOGO_PATH) || undefined,
        });
      }

      return suggestions.slice(0, 5);
    });
  } catch (err) {
    console.warn('[Clearledgr] Search suggestions failed:', err);
  }
}

function registerKeyboardShortcuts() {
  if (!sdk?.Keyboard) return;
  try {
    // G then C → Go to Clearledgr Home
    const goHome = sdk.Keyboard.createShortcutHandle({
      chord: 'g c',
      description: 'Go to Clearledgr Home',
    });
    goHome.on('activate', () => sdk.Router.goto(DEFAULT_ROUTE));

    // G then A → Go to Activity
    const goActivity = sdk.Keyboard.createShortcutHandle({
      chord: 'g a',
      description: 'Go to Clearledgr Activity',
    });
    goActivity.on('activate', () => sdk.Router.goto('clearledgr/activity'));

    // G then R → Go to Reconciliation
    const goRecon = sdk.Keyboard.createShortcutHandle({
      chord: 'g r',
      description: 'Go to Clearledgr Reconciliation',
    });
    goRecon.on('activate', () => sdk.Router.goto('clearledgr/reconciliation'));

  } catch (err) {
    console.warn('[Clearledgr] Keyboard shortcuts failed:', err);
  }
}

// ==================== BOOTSTRAP ====================

async function bootstrap() {
  if (window[INIT_KEY]) return;
  window[INIT_KEY] = true;

  // Load fonts before anything renders
  injectFonts();

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
    // Prefill from "Draft vendor reply" action
    if (_pendingComposePrefill) {
      const prefill = _pendingComposePrefill;
      _pendingComposePrefill = null;
      try {
        if (prefill.to) composeView.setToRecipients([{ emailAddress: prefill.to }]);
        if (prefill.subject) composeView.setSubject(prefill.subject);
        if (prefill.body) composeView.setBodyHTML(prefill.body.replace(/\n/g, '<br>'));
      } catch (_) { /* ignore */ }
    }

    // Vendor duplicate detection — warn if composing to a known vendor
    try {
      composeView.on('recipientsChanged', (event) => {
        const recipients = event?.to?.map(r => r.emailAddress?.toLowerCase()) || [];
        const queue = store.queueState || [];
        for (const email of recipients) {
          if (!email) continue;
          const vendorItems = queue.filter(i => (i.sender || '').toLowerCase().includes(email));
          if (vendorItems.length > 0) {
            const vendor = vendorItems[0].vendor_name || vendorItems[0].vendor || email;
            const count = vendorItems.length;
            try {
              composeView.addStatusBar({
                height: 30,
                addAboveStandardStatusBar: true,
                el: (() => {
                  const bar = document.createElement('div');
                  bar.style.cssText = 'padding:6px 14px;font-size:12px;color:#92400e;background:#fef9ee;border-bottom:1px solid #f3e8d0;font-family:inherit;';
                  bar.textContent = `Clearledgr: ${vendor} has ${count} invoice${count > 1 ? 's' : ''} in your AP queue.`;
                  return bar;
                })(),
              });
            } catch (_) { /* ignore */ }
            break;
          }
        }
      });
    } catch (_) { /* ignore */ }
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
  registerToolbarIcon();
  registerBulkActions();
  registerKeyboardShortcuts();
  registerSearchSuggestions();
  watchForSettingsPage(queueManager);

  // Register full-page routes inside Gmail (Streak pattern)
  registerAppMenuAndRoutes();
}

// ==================== GMAIL-NATIVE ROUTES (Streak pattern) ====================

function registerAppMenuAndRoutes() {
  const PAGE_MAP = {
    'clearledgr/home': HomePage,
    'clearledgr/pipeline': PipelinePage,
    'clearledgr/activity': ActivityPage,
    'clearledgr/vendors': VendorsPage,
    'clearledgr/connections': ConnectionsPage,
    'clearledgr/rules': RulesPage,
    'clearledgr/team': TeamPage,
    'clearledgr/company': CompanyPage,
    'clearledgr/plan': PlanPage,
    'clearledgr/reconciliation': ReconciliationPage,
    'clearledgr/health': HealthPage,
  };

  function clearNavItemViews(handles) {
    handles.forEach((handle) => {
      try { handle?.remove?.(); } catch (_) { /* best-effort */ }
    });
    handles.length = 0;
  }

  function rebuildMenuNavigation() {
    const routeOptions = { includeAdmin: includeAdminRoutes };
    const visibleRoutes = getVisibleNavRoutes(readRoutePreferences(routeOptions), routeOptions);
    clearNavItemViews(appMenuNavItemViews);
    clearNavItemViews(fallbackNavItemViews);

    if (appMenuPanelView && typeof appMenuPanelView.addNavItem === 'function') {
      visibleRoutes.forEach((route) => {
        const navHandle = appMenuPanelView.addNavItem({
          name: route.title,
          routeID: route.id,
        });
        appMenuNavItemViews.push(navHandle);
      });
      return;
    }

    if (sdk.NavMenu && typeof sdk.NavMenu.addNavItem === 'function') {
      visibleRoutes.forEach((route) => {
        const navHandle = sdk.NavMenu.addNavItem({
          name: route.title,
          routeID: route.id,
          type: 'NAVIGATION',
        });
        fallbackNavItemViews.push(navHandle);
      });
    }
  }

  // Wire toast — route pages dispatch events, sidebar showToast renders them
  window.addEventListener('clearledgr:toast', (e) => {
    showToast(e.detail?.message || '', e.detail?.type || 'info');
  });
  setToastFn((msg, type) => {
    showToast(msg, type);
  });

  const adminApi = createAdminApi(queueManager);
  const oauthBridge = createOAuthBridge(() => {
    bootstrapCache = null;
    queueManager?.scanNow?.();
  });

  store.sdk = sdk;

  let bootstrapCache = null;
  let bootstrapPromise = null;
  let includeAdminRoutes = false;

  async function getBootstrap() {
    if (bootstrapCache) return bootstrapCache;
    if (bootstrapPromise) return bootstrapPromise;
    bootstrapPromise = adminApi.bootstrapAdminData().then((data) => {
      bootstrapCache = data;
      const nextIncludeAdmin = hasOpsAccess(data);
      if (nextIncludeAdmin !== includeAdminRoutes) {
        includeAdminRoutes = nextIncludeAdmin;
        rebuildMenuNavigation();
      }
      bootstrapPromise = null;
      return data;
    }).catch(() => {
      bootstrapPromise = null;
      return {};
    });
    return bootstrapPromise;
  }

  function onRefresh() {
    bootstrapCache = null;
  }

  // Dynamic route: invoice detail (clearledgr/invoice/:id)
  sdk.Router.handleCustomRoute('clearledgr/invoice', async (customRouteView) => {
    const container = document.createElement('div');
    container.className = 'cl-route';
    const style = document.createElement('style');
    style.textContent = ROUTE_CSS;
    container.appendChild(style);
    const topbar = document.createElement('div');
    topbar.className = 'topbar';
    topbar.innerHTML = '<h2>Invoice Detail</h2>';
    container.appendChild(topbar);
    const pageMount = document.createElement('div');
    container.appendChild(pageMount);
    const routeEl = customRouteView.getElement();
    routeEl.appendChild(container);

    const params = customRouteView.getParams?.() || {};
    const rawId = params.id || window.location.hash.split('clearledgr/invoice/')[1]?.split('?')[0] || '';
    const orgId = adminApi.orgId();
    const navigate = (routeId) => sdk.Router.goto(routeId);

    render(html`<${InvoiceDetailPage}
      api=${adminApi.api}
      toast=${adminApi.toast}
      orgId=${orgId}
      navigate=${navigate}
      routeParams=${{ id: decodeURIComponent(rawId) }}
    />`, pageMount);
  });

  for (const route of ROUTES) {
    const PageComponent = PAGE_MAP[route.id];
    if (!PageComponent) continue;

    sdk.Router.handleCustomRoute(route.id, async (customRouteView) => {
      const container = document.createElement('div');
      container.className = 'cl-route';

      const style = document.createElement('style');
      style.textContent = ROUTE_CSS;
      container.appendChild(style);

      const topbar = document.createElement('div');
      topbar.className = 'topbar';
      topbar.innerHTML = `<h2>${route.title}</h2><p>${route.subtitle}</p>`;
      container.appendChild(topbar);

      const pageMount = document.createElement('div');
      container.appendChild(pageMount);
      const routeEl = customRouteView.getElement();
      routeEl.appendChild(container);

      const orgId = adminApi.orgId();
      const navigate = (routeId) => sdk.Router.goto(routeId);
      const userEmail = sdk.User?.getEmailAddress?.() || queueManager?.runtimeConfig?.userEmail || '';

      let renderCurrentPage = async () => {};
      const updateRoutePreferences = async (nextPreferences) => {
        const bootstrap = await getBootstrap();
        const routeOptions = { includeAdmin: hasOpsAccess(bootstrap) };
        const normalized = writeRoutePreferences(nextPreferences, routeOptions);
        rebuildMenuNavigation();
        await renderCurrentPage();
        return normalized;
      };

      renderCurrentPage = async () => {
        const bootstrap = await getBootstrap();
        const routeOptions = { includeAdmin: hasOpsAccess(bootstrap) };
        if (route.adminOnly && !routeOptions.includeAdmin) {
          render(html`
            <div class="panel">
              <h3 style="margin:0 0 8px">Access restricted</h3>
              <p class="muted" style="margin:0 0 12px">This page is only available to operators with admin access.</p>
              <button onClick=${() => navigate(DEFAULT_ROUTE)}>Back to Home</button>
            </div>
          `, pageMount);
          return;
        }
        const routePreferences = readRoutePreferences(routeOptions);
        render(html`<${PageComponent}
          bootstrap=${bootstrap}
          api=${adminApi.api}
          toast=${adminApi.toast}
          orgId=${orgId}
          userEmail=${userEmail}
          onRefresh=${async () => { onRefresh(); await renderCurrentPage(); }}
          oauthBridge=${oauthBridge}
          navigate=${navigate}
          routePreferences=${routePreferences}
          availableRoutes=${getNavEligibleRoutes(routeOptions)}
          updateRoutePreferences=${updateRoutePreferences}
        />`, pageMount);
      };

      await renderCurrentPage();
    });
  }

  if (sdk.AppMenu && typeof sdk.AppMenu.addMenuItem === 'function') {
    try {
      const logoUrl = getAssetUrl(LOGO_PATH);
      const iconConfig = logoUrl
        ? { lightTheme: { active: logoUrl, default: logoUrl }, darkTheme: { active: logoUrl, default: logoUrl } }
        : undefined;

      appMenuItemView = sdk.AppMenu.addMenuItem({
        name: 'Clearledgr',
        iconUrl: iconConfig,
        insertIndex: 3,
        routeID: DEFAULT_ROUTE,
        isRouteActive: (routeView) => {
          const id = routeView?.getRouteID?.() || '';
          return id.startsWith('clearledgr/');
        },
      });

      if (appMenuItemView && typeof appMenuItemView.addCollapsiblePanel === 'function') {
        appMenuItemView.addCollapsiblePanel({})
          .then((panel) => {
            if (!panel || typeof panel.addNavItem !== 'function') return;
            appMenuPanelView = panel;
            rebuildMenuNavigation();
          })
          .catch((err) => console.warn('[Clearledgr] CollapsiblePanel failed:', err));
      }
    } catch (err) {
      console.warn('[Clearledgr] AppMenu not available, falling back to NavMenu', err);
      rebuildMenuNavigation();
    }
  }
}

bootstrap();
