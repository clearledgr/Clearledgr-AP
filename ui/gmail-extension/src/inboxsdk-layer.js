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
import { STATE_LABELS, STATE_COLORS, getStateLabel, readLocalStorage, writeLocalStorage, getAssetUrl, formatAmount } from './utils/formatters.js';
import { resolveRecordRouteId } from './utils/record-route.js';
import { resolveVendorRouteName } from './utils/vendor-route.js';
import { navigateInboxRoute } from './utils/inbox-route.js';

// Route imports (Gmail-native support pages — Streak pattern)
import {
  ROUTES,
  DEFAULT_ROUTE,
  canViewRoute,
  getVisibleNavRoutes,
  getNavEligibleRoutes,
  getMenuNavRoutes,
  readRoutePreferences,
  writeRoutePreferences,
} from './routes/route-registry.js';
import { createWorkspaceShellApi, setToastFn } from './routes/workspace-shell-api.js';
import { createOAuthBridge } from './routes/oauth-bridge.js';
import { ROUTE_CSS } from './routes/route-styles.js';
import { getPipelineViewIconUrl, getRouteIconUrl } from './routes/route-icons.js';
import HomePage from './routes/pages/HomePage.js';
import ReviewPage from './routes/pages/ReviewPage.js';
import UpcomingPage from './routes/pages/UpcomingPage.js';
import ActivityPage from './routes/pages/ActivityPage.js';
import ConnectionsPage from './routes/pages/ConnectionsPage.js';
import RulesPage from './routes/pages/RulesPage.js';
import SettingsPage from './routes/pages/SettingsPage.js';
import PlanPage from './routes/pages/PlanPage.js';
import ReconciliationPage from './routes/pages/ReconciliationPage.js';
import HealthPage from './routes/pages/HealthPage.js';
import PipelinePage from './routes/pages/PipelinePage.js';
import InvoiceDetailPage from './routes/pages/InvoiceDetailPage.js';
import VendorsPage from './routes/pages/VendorsPage.js';
import VendorDetailPage from './routes/pages/VendorDetailPage.js';
import TemplatesPage from './routes/pages/TemplatesPage.js';
import ReportsPage from './routes/pages/ReportsPage.js';
import { getCapabilities } from './routes/route-helpers.js';
import {
  clearPipelineNavigation,
  createSavedPipelineView,
  focusPipelineItem,
  getBootstrappedPipelinePreferences,
  getPinnedPipelineViews,
  getPipelineViewRef,
  normalizePipelinePreferences,
  pipelinePreferencesEqual,
  readPipelinePreferences,
  resolvePipelineViewByRef,
  writePipelinePreferences,
} from './routes/pipeline-views.js';
import { watchForSettingsPage } from './settings-tab.js';

const html = htm.bind(h);
const APP_ID = 'sdk_Clearledgr2026_dc12c60472';
const INIT_KEY = '__clearledgr_ap_v1_inboxsdk_initialized';
const LOGO_PATH = 'icons/icon48.png';
const STORAGE_ACTIVE_AP_ITEM_ID = 'clearledgr_active_ap_item_id';
const STORAGE_PENDING_DIRECT_ROUTE = '__clearledgr_pending_direct_route_v1';
const STORAGE_RELOAD_ROUTE = '__clearledgr_reload_route_v1';
const ATTR_PENDING_DIRECT_ROUTE = 'data-clearledgr-pending-direct-route';

let sdk = null;
let queueManager = null;
let _pendingComposePrefill = null;
let sidebarContainer = null;
let sidebarPanelView = null;
let sidebarPanelViewPromise = null;
let appMenuItemView = null;
let appMenuPanelView = null;
let appMenuPanelReady = null; // Promise that resolves when panel is available
let appMenuNavItemViews = [];
let fallbackNavItemViews = [];
const APPMENU_WORKSPACE_ROUTE_IDS = new Set([
  'clearledgr/invoices',
  'clearledgr/home',
  'clearledgr/review',
  'clearledgr/upcoming',
  'clearledgr/activity',
  'clearledgr/vendors',
  'clearledgr/reports',
  'clearledgr/reconciliation',
]);
const APPMENU_CONFIGURATION_ROUTE_IDS = new Set([
  'clearledgr/connections',
  'clearledgr/rules',
  'clearledgr/settings',
]);
const APPMENU_LIBRARY_ROUTE_IDS = new Set([
  'clearledgr/templates',
]);

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

async function ensureSidebarPanelView() {
  if (sidebarPanelView && !sidebarPanelView.destroyed) return sidebarPanelView;
  if (sidebarPanelViewPromise) return sidebarPanelViewPromise;
  if (!sdk?.Global || !sidebarContainer) return null;

  const logoUrl = getAssetUrl(LOGO_PATH);
  sidebarPanelViewPromise = sdk.Global.addSidebarContentPanel({
    title: 'Clearledgr AP',
    iconUrl: logoUrl || null,
    el: sidebarContainer,
    hideTitleBar: false,
  }).then((panelView) => {
    sidebarPanelView = panelView || null;
    sidebarPanelViewPromise = null;
    return sidebarPanelView;
  }).catch(() => {
    sidebarPanelViewPromise = null;
    return null;
  });

  return sidebarPanelViewPromise;
}

async function setSidebarPanelOpen(shouldOpen) {
  const panelView = await ensureSidebarPanelView();
  if (!panelView || panelView.destroyed) return;
  if (shouldOpen) {
    if (!panelView.isActive()) panelView.open();
    return;
  }
  if (panelView.isActive()) panelView.close();
}

async function openComposeWithPrefill(prefill = {}) {
  if (!sdk?.Compose || typeof sdk.Compose.openNewComposeView !== 'function') {
    throw new Error('compose_unavailable');
  }
  _pendingComposePrefill = {
    to: prefill?.to || '',
    subject: prefill?.subject || '',
    body: prefill?.body || '',
    recordContext: prefill?.recordContext || null,
  };
  try {
    await sdk.Compose.openNewComposeView();
  } catch (error) {
    _pendingComposePrefill = null;
    throw error;
  }
}

function buildComposeRecordContext(item = null) {
  if (!item?.id) return null;
  return {
    apItemId: String(item.id),
    vendorName: String(item.vendor_name || item.vendor || item.sender || 'Unknown vendor'),
    invoiceNumber: String(item.invoice_number || '').trim(),
    amountLabel: formatAmount(item.amount, item.currency),
  };
}

function normalizeComposeRecipients(recipients = []) {
  const source = Array.isArray(recipients)
    ? recipients
    : recipients == null
      ? []
      : [recipients];
  const normalized = [];
  for (const recipient of source) {
    const value = String(
      recipient?.emailAddress
      || recipient?.address
      || recipient?.email
      || recipient
      || ''
    ).trim();
    if (!value || normalized.includes(value)) continue;
    normalized.push(value);
  }
  return normalized.slice(0, 12);
}

async function collectComposeDraftPayload(composeView) {
  let draftId = '';
  let threadId = '';
  let subject = '';
  let bodyPreview = '';
  let recipients = [];

  try {
    if (typeof composeView?.getCurrentDraftID === 'function') {
      draftId = await Promise.resolve(composeView.getCurrentDraftID());
    }
  } catch (_) { /* ignore */ }
  if (!draftId) {
    try {
      if (typeof composeView?.getDraftID === 'function') {
        draftId = await Promise.resolve(composeView.getDraftID());
      }
    } catch (_) { /* ignore */ }
  }
  try {
    threadId = String(composeView?.getThreadID?.() || '').trim();
  } catch (_) { /* ignore */ }
  try {
    subject = String(composeView?.getSubject?.() || '').trim();
  } catch (_) { /* ignore */ }
  try {
    bodyPreview = String(composeView?.getTextContent?.() || '').trim();
  } catch (_) { /* ignore */ }
  try {
    recipients = normalizeComposeRecipients(composeView?.getToRecipients?.() || []);
  } catch (_) { /* ignore */ }

  return {
    draft_id: draftId || undefined,
    thread_id: threadId || undefined,
    subject: subject || undefined,
    recipients,
    body_preview: bodyPreview ? bodyPreview.slice(0, 600) : undefined,
  };
}

function buildComposeSearchSeed(payload = {}) {
  const recipients = Array.isArray(payload?.recipients) ? payload.recipients : [];
  return String(
    payload?.subject
    || recipients[0]
    || ''
  ).trim();
}

function renderComposeRecordStatus(recordContext) {
  if (!recordContext) return null;
  const bar = document.createElement('div');
  bar.style.cssText = [
    'display:flex',
    'align-items:center',
    'justify-content:space-between',
    'gap:12px',
    'padding:7px 14px',
    'font-size:12px',
    'background:#ecfdf5',
    'color:#166534',
    'border-bottom:1px solid #d1fae5',
    'font-family:inherit',
  ].join(';');

  const copy = document.createElement('div');
  const summary = [
    recordContext.vendorName || 'Finance record',
    recordContext.invoiceNumber ? `Invoice ${recordContext.invoiceNumber}` : '',
    recordContext.amountLabel || '',
  ].filter(Boolean).join(' · ');
  copy.textContent = `Clearledgr: linked finance record${summary ? ` — ${summary}` : ''}`;
  bar.appendChild(copy);

  if (recordContext.apItemId) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Open record';
    button.style.cssText = [
      'border:1px solid #86efac',
      'background:#ffffff',
      'color:#166534',
      'border-radius:999px',
      'padding:4px 10px',
      'font:inherit',
      'font-weight:600',
      'cursor:pointer',
      'flex-shrink:0',
    ].join(';');
    button.addEventListener('click', () => {
      navigateInboxRoute('clearledgr/invoice/:id', sdk, { id: recordContext.apItemId });
    });
    bar.appendChild(button);
  }

  return bar;
}

function renderComposeRecordChooser({ composeView, queueManager, onLinked }) {
  const bar = document.createElement('div');
  bar.style.cssText = [
    'display:flex',
    'flex-direction:column',
    'gap:8px',
    'padding:8px 14px',
    'font-size:12px',
    'background:#f8fafc',
    'color:#334155',
    'border-bottom:1px solid #e2e8f0',
    'font-family:inherit',
  ].join(';');

  const topRow = document.createElement('div');
  topRow.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap';
  const copy = document.createElement('div');
  copy.textContent = 'Clearledgr: no finance record linked to this draft yet.';
  topRow.appendChild(copy);

  const actions = document.createElement('div');
  actions.style.cssText = 'display:flex;align-items:center;gap:8px;flex-wrap:wrap';
  const createButton = document.createElement('button');
  createButton.type = 'button';
  createButton.textContent = 'Create finance record';
  createButton.style.cssText = [
    'border:1px solid #cbd5e1',
    'background:#ffffff',
    'color:#0f172a',
    'border-radius:999px',
    'padding:4px 10px',
    'font:inherit',
    'font-weight:600',
    'cursor:pointer',
  ].join(';');
  const openButton = document.createElement('button');
  openButton.type = 'button';
  openButton.textContent = 'Open invoices';
  openButton.style.cssText = createButton.style.cssText;
  openButton.addEventListener('click', () => {
    navigateInboxRoute('clearledgr/invoices', sdk);
  });
  actions.appendChild(createButton);
  actions.appendChild(openButton);
  topRow.appendChild(actions);
  bar.appendChild(topRow);

  const searchRow = document.createElement('div');
  searchRow.style.cssText = 'display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px';
  const searchInput = document.createElement('input');
  searchInput.type = 'text';
  searchInput.placeholder = 'Search vendor, invoice, or email';
  searchInput.style.cssText = [
    'width:100%',
    'padding:6px 10px',
    'border:1px solid #cbd5e1',
    'border-radius:8px',
    'font:inherit',
    'background:#ffffff',
    'color:#0f172a',
  ].join(';');
  const searchButton = document.createElement('button');
  searchButton.type = 'button';
  searchButton.textContent = 'Find record';
  searchButton.style.cssText = createButton.style.cssText;
  searchRow.appendChild(searchInput);
  searchRow.appendChild(searchButton);
  bar.appendChild(searchRow);

  const results = document.createElement('div');
  results.style.cssText = 'display:none;flex-direction:column;gap:6px';
  bar.appendChild(results);

  const setBusy = (busy, searchBusy = false) => {
    createButton.disabled = busy;
    searchButton.disabled = busy || searchBusy;
    searchInput.disabled = busy || searchBusy;
    createButton.style.opacity = busy ? '0.6' : '1';
    searchButton.style.opacity = (busy || searchBusy) ? '0.6' : '1';
  };

  const renderResults = (items = []) => {
    results.innerHTML = '';
    if (!Array.isArray(items) || items.length === 0) {
      results.style.display = 'none';
      return;
    }
    results.style.display = 'flex';
    items.slice(0, 4).forEach((item) => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 10px;border:1px solid #e2e8f0;border-radius:8px;background:#ffffff';
      const text = document.createElement('div');
      text.style.cssText = 'min-width:0;flex:1';
      const title = document.createElement('strong');
      title.style.cssText = 'display:block;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis';
      title.textContent = item.vendor_name || 'Unknown vendor';
      const detail = document.createElement('span');
      detail.style.cssText = 'color:#64748b';
      detail.textContent = `${item.invoice_number || 'No invoice #'} · ${formatAmount(item.amount, item.currency)}`;
      text.appendChild(title);
      text.appendChild(detail);
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = 'Link';
      button.style.cssText = createButton.style.cssText;
      button.addEventListener('click', async () => {
        if (!queueManager?.linkComposeDraftToItem) {
          showToast('Compose record linking is still loading. Try again in a moment.', 'warning');
          return;
        }
        setBusy(true, false);
        try {
          const payload = await collectComposeDraftPayload(composeView);
          const result = await queueManager.linkComposeDraftToItem(item, payload);
          if (result?.ap_item?.id) {
            showToast(`Linked draft to ${result.ap_item.vendor_name || result.ap_item.invoice_number || 'finance record'}.`, 'success');
            onLinked(buildComposeRecordContext(result.ap_item));
            return;
          }
          showToast(result?.reason || 'Could not link this draft to the selected record.', 'error');
        } catch (error) {
          showToast(error?.message || 'Could not link this draft right now.', 'error');
        } finally {
          setBusy(false, false);
        }
      });
      row.appendChild(text);
      row.appendChild(button);
      results.appendChild(row);
    });
  };

  createButton.addEventListener('click', async () => {
    if (!queueManager?.createRecordFromComposeDraft) {
      showToast('Compose record creation is still loading. Try again in a moment.', 'warning');
      return;
    }
    setBusy(true, false);
    try {
      const payload = await collectComposeDraftPayload(composeView);
      if (!payload.subject && (!Array.isArray(payload.recipients) || payload.recipients.length === 0)) {
        showToast('Add a recipient or subject before creating a finance record.', 'warning');
        return;
      }
      const result = await queueManager.createRecordFromComposeDraft(payload);
      if (result?.ap_item?.id) {
        showToast(
          String(result?.status || '').toLowerCase() === 'already_linked'
            ? 'This draft is already linked to a finance record.'
            : 'Finance record created from this draft.',
          'success',
        );
        onLinked(buildComposeRecordContext(result.ap_item));
        return;
      }
      showToast(result?.reason || 'Could not create a finance record from this draft.', 'error');
    } catch (error) {
      showToast(error?.message || 'Could not create a finance record from this draft.', 'error');
    } finally {
      setBusy(false, false);
    }
  });

  searchButton.addEventListener('click', async () => {
    if (!queueManager?.searchRecordCandidates) {
      showToast('Compose record search is still loading. Try again in a moment.', 'warning');
      return;
    }
    setBusy(false, true);
    try {
      const payload = await collectComposeDraftPayload(composeView);
      const query = String(searchInput.value || buildComposeSearchSeed(payload)).trim();
      searchInput.value = query;
      if (!query) {
        renderResults([]);
        showToast('Add a subject or recipient before searching for a finance record.', 'warning');
        return;
      }
      const items = await queueManager.searchRecordCandidates(query, { limit: 4 });
      renderResults(items);
      if (!items.length) {
        showToast('No matching finance records found for this draft.', 'info');
      }
    } catch (error) {
      renderResults([]);
      showToast(error?.message || 'Could not search finance records right now.', 'error');
    } finally {
      setBusy(false, false);
    }
  });

  searchInput.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    searchButton.click();
  });

  return bar;
}

// ==================== SIDEBAR INIT ====================

function initializeSidebar() {
  const container = document.createElement('div');
  container.className = 'cl-sidebar-host';
  sidebarContainer = container;

  // Mount Preact into the container
  mountSidebar();

  // Register with InboxSDK
  void ensureSidebarPanelView();

  // Restore last active item
  const restoredId = readLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID);
  if (restoredId) {
    store.update({ selectedItemId: restoredId });
  }
}

function injectAppMenuPanelStyles() {
  if (document.getElementById('cl-appmenu-panel-styles')) return;
  const style = document.createElement('style');
  style.id = 'cl-appmenu-panel-styles';
  style.textContent = `
    .cl-appmenu-panel {
      --cl-panel-accent: #cfe8ff;
      --cl-panel-border: #dbe7f3;
      --cl-panel-text: #17324d;
      --cl-panel-muted: #73859b;
    }
    .cl-appmenu-panel .aic {
      display: none;
    }
    .cl-appmenu-panel .aBO {
      padding-top: 0;
    }
    .cl-appmenu-panel .Ls77Lb {
      margin-top: 0;
    }
    .cl-appmenu-panel .nM.inboxsdk__collapsiblePanel_navItems {
      display: none;
      padding-top: 0;
    }
    .cl-appmenu-panel .inboxsdk__collapsiblePanel_navItems {
      display: none;
    }
    .cl-appmenu-panel-shell {
      padding: 12px 10px 14px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .cl-appmenu-panel-cta {
      display: flex;
      align-items: center;
      gap: 10px;
      width: 100%;
      border: 1px solid #b7dafd;
      border-radius: 16px;
      background: #cfe8ff;
      color: #17324d;
      font: 600 14px/1.2 "DM Sans", sans-serif;
      padding: 14px 16px;
      cursor: pointer;
      box-sizing: border-box;
      text-align: left;
    }
    .cl-appmenu-panel-cta:hover {
      background: #c2e0ff;
    }
    .cl-appmenu-panel-cta-icon {
      font-size: 22px;
      line-height: 1;
      flex-shrink: 0;
    }
    .cl-appmenu-panel-cta-copy {
      display: block;
      min-width: 0;
    }
    .cl-appmenu-panel-label {
      margin: 0 8px 2px;
      color: var(--cl-panel-muted);
      font: 700 11px/1 "DM Sans", sans-serif;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .cl-appmenu-panel-section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin: 0 4px 6px;
    }
    .cl-appmenu-panel-section-title {
      color: #1b1b1b;
      font: 700 14px/1.2 "DM Sans", sans-serif;
    }
    .cl-appmenu-panel-section-action {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      border: 0;
      border-radius: 999px;
      background: transparent;
      color: #455a72;
      font: 500 20px/1 "DM Sans", sans-serif;
      cursor: pointer;
    }
    .cl-appmenu-panel-section-action:hover {
      background: #eef4fa;
    }
    .cl-appmenu-panel-view-list {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .cl-appmenu-panel-view-item {
      display: flex;
      align-items: center;
      gap: 8px;
      width: 100%;
      border: 0;
      border-radius: 10px;
      background: transparent;
      color: #283746;
      padding: 8px 10px;
      cursor: pointer;
      text-align: left;
      font: 500 13px/1.25 "DM Sans", sans-serif;
    }
    .cl-appmenu-panel-view-item:hover {
      background: #eef4fa;
    }
    .cl-appmenu-panel-view-item.is-active {
      background: #d9eaff;
      color: #17324d;
    }
    .cl-appmenu-panel-view-icon {
      color: var(--cl-panel-muted);
      font: 600 11px/1 "Geist Mono", monospace;
      flex-shrink: 0;
      width: 16px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    .cl-appmenu-panel-view-icon-image {
      width: 16px;
      height: 16px;
      display: block;
      object-fit: contain;
      opacity: 0.88;
    }
    .cl-appmenu-panel-view-meta {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    .cl-appmenu-panel-view-name {
      color: #25384a;
      font: 600 13px/1.2 "DM Sans", sans-serif;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .cl-appmenu-panel-view-description {
      color: var(--cl-panel-muted);
      font: 500 11px/1.3 "DM Sans", sans-serif;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .cl-appmenu-panel-section {
      display: flex;
      flex-direction: column;
    }
  `;
  document.head.appendChild(style);
}

function prepareRouteHost(customRouteView) {
  const routeEl = customRouteView?.getElement?.();
  if (!routeEl) return null;
  routeEl.style.width = '100%';
  routeEl.style.maxWidth = 'none';
  routeEl.style.padding = '0';
  routeEl.style.boxSizing = 'border-box';
  return routeEl;
}

function resolveAppMenuPanelRoot() {
  const panelRoot = appMenuPanelView?.getElement?.();
  if (panelRoot instanceof HTMLElement) return panelRoot;
  const fallbackRoot = document.querySelector('.cl-appmenu-panel');
  return fallbackRoot instanceof HTMLElement ? fallbackRoot : null;
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
        void setSidebarPanelOpen(true);
        store.update({ currentThreadId: threadId });
        let item = store.findItemByThreadId(threadId);
        if (item?.id) {
          store.update({ selectedItemId: item.id });
          writeLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID, item.id);
        }

        if (threadId && queueManager) {
          // Always refresh the canonical item for the open thread so new
          // backend-derived fields (for example attachment evidence) replace
          // stale queue rows already in memory. Lookup stays read-only; thread
          // repair is an explicit fallback when the backend reports a miss.
          try {
            const result = await queueManager.backendFetch(
              `/extension/by-thread/${encodeURIComponent(threadId)}`
            );
            if (result?.ok) {
              const data = await result.json();
              if (data?.found && data?.item) {
                item = data.item;
              } else {
                const recovered = await queueManager.backendFetch(
                  `/extension/by-thread/${encodeURIComponent(threadId)}/recover`,
                  { method: 'POST' }
                );
                if (recovered?.ok) {
                  const recoveredData = await recovered.json();
                  if (recoveredData?.found && recoveredData?.item) {
                    item = recoveredData.item;
                  }
                }
              }
              if (item?.id) {
                queueManager.upsertQueueItem(item);
                queueManager.emitQueueUpdated();
                store.update({ selectedItemId: item.id });
                writeLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID, item.id);
              }
            }
          } catch (_) { /* no finance record for this thread — that's fine */ }
        }

        // Inject thread-top banner for finance-record threads (Mixmax-style)
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

function bindRouteSidebarBehavior(customRouteView) {
  void setSidebarPanelOpen(false);
  customRouteView?.on?.('destroy', () => {
    window.setTimeout(() => {
      const hash = String(window.location.hash || '');
      if (!hash.includes('clearledgr/')) {
        void setSidebarPanelOpen(true);
      }
    }, 0);
  });
}

function openItemInPipeline(item, source = 'thread') {
  if (!item?.id) return;
  const pipelineScope = {
    orgId: queueManager?.runtimeConfig?.organizationId || 'default',
    userEmail: queueManager?.runtimeConfig?.userEmail || '',
  };
  store.setSelectedItem(String(item.id));
  focusPipelineItem(pipelineScope, item, source);
  sdk?.Router?.goto?.('clearledgr/invoices');
}

function injectInvoiceBanner(threadView, item) {
  const state = String(item.state || '').toLowerCase();
  const vendor = item.vendor_name || item.vendor || 'Unknown vendor';
  const amountLabel = formatAmount(item.amount, item.currency);

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
  summary.textContent = amountLabel === 'Amount unavailable' ? vendor : `${vendor} \u2014 ${amountLabel}`;
  el.appendChild(summary);

  // State pill
  const pill = document.createElement('span');
  pill.style.cssText = `
    font-size:11px; font-weight:600; padding:2px 10px; border-radius:999px;
    background:${cfg.border}20; color:${cfg.text}; text-transform:uppercase; letter-spacing:0.02em;
  `;
  pill.textContent = cfg.label;
  el.appendChild(pill);

  if (item?.id) {
    const btnStyle = (bg, color, border) => `
      border:${border || 'none'}; border-radius:6px; padding:5px 14px; font-size:12px; font-weight:600;
      cursor:pointer; background:${bg}; color:${color}; font-family:inherit;
    `;

    const openBtn = document.createElement('button');
    openBtn.textContent = 'Open in invoices';
    openBtn.style.cssText = btnStyle('transparent', cfg.text, `1px solid ${cfg.border}`);
    openBtn.addEventListener('click', () => {
      openItemInPipeline(item, 'thread_banner');
    });
    el.appendChild(openBtn);
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
                title: 'Open in invoices',
                iconUrl: getAssetUrl(LOGO_PATH) || undefined,
                onClick: () => {
                  openItemInPipeline(item, 'thread_row');
                },
              });
            }
          } catch (_) { /* ignore */ }
        }
      })
      .catch(() => { /* ignore */ });
  });
}

function registerInboxHeadsUp() {
  // Inbox heads-up: priority summary bar at top of inbox (Streak-style)
  // Uses a global banner that updates as queue state changes.
  if (!sdk?.Global) return;

  const headsUpEl = document.createElement('div');
  headsUpEl.id = 'cl-inbox-headsup';
  headsUpEl.style.cssText = 'display:none;padding:8px 16px;background:#0A1628;color:#fff;font-size:12px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;align-items:center;gap:12px;cursor:pointer;';

  const updateHeadsUp = () => {
    const items = store.queue || [];
    const needsApproval = items.filter((i) => i.state === 'needs_approval').length;
    const failedPost = items.filter((i) => i.state === 'failed_post').length;
    const needsInfo = items.filter((i) => i.state === 'needs_info').length;
    const overdue = items.filter((i) => {
      if (!i.due_date) return false;
      try { return new Date(i.due_date) < new Date(); } catch { return false; }
    }).length;

    const parts = [];
    if (needsApproval) parts.push(`${needsApproval} awaiting approval`);
    if (failedPost) parts.push(`${failedPost} failed post`);
    if (needsInfo) parts.push(`${needsInfo} needs info`);
    if (overdue) parts.push(`${overdue} overdue`);

    if (parts.length === 0) {
      headsUpEl.style.display = 'none';
      return;
    }

    headsUpEl.style.display = 'flex';
    headsUpEl.innerHTML = `
      <span style="width:8px;height:8px;border-radius:50%;background:#00D67E;flex-shrink:0"></span>
      <span><strong>Clearledgr</strong> \u00B7 ${parts.join(' \u00B7 ')}</span>
      <span style="margin-left:auto;opacity:0.6;font-size:11px">Open invoices \u203A</span>
    `;
  };

  headsUpEl.addEventListener('click', () => {
    if (sdk?.Router) sdk.Router.goto('clearledgr/invoices');
  });

  // Insert at top of Gmail main area
  try {
    const target = document.querySelector('[role="main"]') || document.body;
    target.insertBefore(headsUpEl, target.firstChild);
  } catch (_) {
    document.body.appendChild(headsUpEl);
  }

  // Update on store changes
  store.subscribe(updateHeadsUp);
  updateHeadsUp();
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
      title: 'Clearledgr Invoices',
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
          name: 'Clearledgr Pipeline',
          description: 'Open the AP control plane',
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
    // G then C → Go to Clearledgr Pipeline
    const goHome = sdk.Keyboard.createShortcutHandle({
      chord: 'g c',
      description: 'Go to Clearledgr Pipeline',
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
    let composeRecordContext = null;
    let composeStatusHandle = null;

    // Prefill from "Draft vendor reply" action
    if (_pendingComposePrefill) {
      const prefill = _pendingComposePrefill;
      _pendingComposePrefill = null;
      composeRecordContext = prefill.recordContext || null;
      try {
        if (prefill.to) composeView.setToRecipients([{ emailAddress: prefill.to }]);
        if (prefill.subject) composeView.setSubject(prefill.subject);
        if (prefill.body) composeView.setBodyHTML(prefill.body.replace(/\n/g, '<br>'));
      } catch (_) { /* ignore */ }
    }

    const mountComposeRecordStatus = (recordContext) => {
      if (typeof composeView?.addStatusBar !== 'function') return;
      try {
        composeStatusHandle?.destroy?.();
        composeStatusHandle?.remove?.();
      } catch (_) { /* ignore */ }
      try {
        composeStatusHandle = composeView.addStatusBar({
          height: recordContext ? 34 : 92,
          addAboveStandardStatusBar: true,
          el: recordContext
            ? renderComposeRecordStatus(recordContext)
            : renderComposeRecordChooser({
                composeView,
                queueManager,
                onLinked(nextRecordContext) {
                  composeRecordContext = nextRecordContext || null;
                  mountComposeRecordStatus(composeRecordContext);
                },
              }),
        });
      } catch (_) { /* ignore */ }
    };

    const resolveComposeRecordContext = async () => {
      if (!composeRecordContext) {
        composeRecordContext = buildComposeRecordContext(store.findItemByThreadId(store.currentThreadId));
      }
      if (!composeRecordContext && queueManager?.lookupComposeRecord) {
        try {
          const payload = await collectComposeDraftPayload(composeView);
          if (payload.draft_id || payload.thread_id) {
            const lookup = await queueManager.lookupComposeRecord(payload);
            if (lookup?.ap_item?.id) {
              composeRecordContext = buildComposeRecordContext(lookup.ap_item);
            }
          }
        } catch (_) { /* ignore */ }
      }
      mountComposeRecordStatus(composeRecordContext);
    };

    void resolveComposeRecordContext();

    // Vendor duplicate detection — warn if composing to a known vendor
    try {
      composeView.on('recipientsChanged', (event) => {
        const recipients = normalizeComposeRecipients(
          event?.to ?? composeView?.getToRecipients?.() ?? []
        ).map((recipient) => String(recipient || '').toLowerCase());
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
                  bar.textContent = `Clearledgr: ${vendor} has ${count} record${count > 1 ? 's' : ''} in your AP queue.`;
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
  queueManager.onQueueUpdated((queue, status, agentSessions, tabs, agentInsights, sources, contexts, tasks, notes, comments, files) => {
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
      tasksState: tasks instanceof Map ? tasks : new Map(),
      notesState: notes instanceof Map ? notes : new Map(),
      commentsState: comments instanceof Map ? comments : new Map(),
      filesState: files instanceof Map ? files : new Map(),
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
  registerInboxHeadsUp();
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
    'clearledgr/review': ReviewPage,
    'clearledgr/upcoming': UpcomingPage,
    'clearledgr/invoices': PipelinePage,
    'clearledgr/activity': ActivityPage,
    'clearledgr/vendors': VendorsPage,
    'clearledgr/templates': TemplatesPage,
    'clearledgr/reports': ReportsPage,
    'clearledgr/connections': ConnectionsPage,
    'clearledgr/rules': RulesPage,
    'clearledgr/settings': SettingsPage,
    'clearledgr/reconciliation': ReconciliationPage,
    'clearledgr/health': HealthPage,
  };
  const settingsRoute = ROUTES.find((route) => route.id === 'clearledgr/settings') || null;
  const LEGACY_PAGE_MAP = {
    'clearledgr/team': PAGE_MAP['clearledgr/settings'],
    'clearledgr/company': PAGE_MAP['clearledgr/settings'],
    'clearledgr/plan': PlanPage,
  };
  let directHashSyncInFlight = false;
  let lastDirectHashRoute = '';
  let reloadRouteRestoreInFlight = false;
  let hasRenderedClearledgrRouteThisBoot = false;
  let lastActiveClearledgrRoute = '';
  let lastKnownMailboxDocumentTitle = String(document.title || '').trim();
  const routeBootStartedAt = Date.now();
  const ROUTE_RESTORE_WINDOW_MS = 5000;

  function normalizeClearledgrHash(hash = '') {
    const normalized = String(hash || '').trim().replace(/^#/, '').split('?')[0];
    return normalized.startsWith('clearledgr/') ? normalized : '';
  }

  function buildRouteDocumentTitle(pageTitle = '') {
    const normalizedTitle = String(pageTitle || '').trim();
    if (!normalizedTitle) return '';
    const mailboxEmail = String(
      sdk?.User?.getEmailAddress?.()
      || queueManager?.runtimeConfig?.userEmail
      || ''
    ).trim();
    return mailboxEmail
      ? `${normalizedTitle} - ${mailboxEmail} - Clearledgr Mail`
      : `${normalizedTitle} - Clearledgr Mail`;
  }

  function claimRouteDocumentTitle(pageTitle = '') {
    const nextTitle = buildRouteDocumentTitle(pageTitle);
    if (!nextTitle) return () => {};
    document.title = nextTitle;
    return () => {
      window.setTimeout(() => {
        if (!normalizeClearledgrHash(window.location.hash) && lastKnownMailboxDocumentTitle) {
          document.title = lastKnownMailboxDocumentTitle;
        }
      }, 0);
    };
  }

  function buildClearledgrRouteHash(routeId, params = null) {
    if (!routeId) return '';
    if (routeId === 'clearledgr/invoice/:id') {
      const id = encodeURIComponent(String(params?.id || ''));
      return id ? `clearledgr/invoice/${id}` : '';
    }
    if (routeId === 'clearledgr/vendor/:name') {
      const name = encodeURIComponent(String(params?.name || ''));
      return name ? `clearledgr/vendor/${name}` : '';
    }
    if (routeId === 'clearledgr/invoices-view/:ref') {
      const ref = encodeURIComponent(String(params?.ref || ''));
      return ref ? `clearledgr/invoices-view/${ref}` : '';
    }
    if (routeId === 'clearledgr/pipeline-view/:ref') {
      const ref = encodeURIComponent(String(params?.ref || ''));
      return ref ? `clearledgr/pipeline-view/${ref}` : '';
    }
    return normalizeClearledgrHash(routeId);
  }

  function rememberActiveClearledgrRoute(routeIdOrHash, params = null) {
    const normalized = params
      ? buildClearledgrRouteHash(routeIdOrHash, params)
      : normalizeClearledgrHash(routeIdOrHash);
    if (!normalized) return;
    lastActiveClearledgrRoute = normalized;
    hasRenderedClearledgrRouteThisBoot = true;
  }

  function navigationWasReload() {
    try {
      const [navigationEntry] = globalThis.performance?.getEntriesByType?.('navigation') || [];
      if (navigationEntry?.type) return navigationEntry.type === 'reload';
    } catch (_) {
      /* best effort */
    }
    try {
      return globalThis.performance?.navigation?.type === 1;
    } catch (_) {
      /* best effort */
    }
    return false;
  }

  function persistReloadedClearledgrRoute() {
    const activeHash = normalizeClearledgrHash(window.location.hash) || lastActiveClearledgrRoute;
    if (!activeHash) return;
    try {
      window.sessionStorage?.setItem?.(STORAGE_RELOAD_ROUTE, JSON.stringify({
        hash: activeHash,
        ts: Date.now(),
        pathname: String(window.location.pathname || ''),
      }));
    } catch (_) {
      /* best effort */
    }
  }

  function readReloadedClearledgrRoute() {
    try {
      const raw = window.sessionStorage?.getItem?.(STORAGE_RELOAD_ROUTE);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      const hash = normalizeClearledgrHash(parsed?.hash || '');
      const ts = Number(parsed?.ts || 0);
      const pathname = String(parsed?.pathname || '').trim();
      if (!hash) return null;
      if (!Number.isFinite(ts) || (Date.now() - ts) > ROUTE_RESTORE_WINDOW_MS) return null;
      if (pathname && pathname !== String(window.location.pathname || '')) return null;
      return hash;
    } catch (_) {
      return null;
    }
  }

  function clearReloadedClearledgrRoute() {
    try {
      window.sessionStorage?.removeItem?.(STORAGE_RELOAD_ROUTE);
    } catch (_) {
      /* best effort */
    }
  }

  function parseDirectHashRoute(hash = '') {
    const normalized = String(hash || '').trim().replace(/^#/, '').split('?')[0];
    if (!normalized.startsWith('clearledgr/')) return null;

    if (PAGE_MAP[normalized] || LEGACY_PAGE_MAP[normalized] || normalized === 'clearledgr/pipeline') {
      return { routeId: normalized, params: null };
    }

    if (normalized.startsWith('clearledgr/invoice/')) {
      return {
        routeId: 'clearledgr/invoice/:id',
        params: { id: decodeURIComponent(normalized.slice('clearledgr/invoice/'.length)) },
      };
    }
    if (normalized.startsWith('clearledgr/vendor/')) {
      return {
        routeId: 'clearledgr/vendor/:name',
        params: { name: decodeURIComponent(normalized.slice('clearledgr/vendor/'.length)) },
      };
    }
    if (normalized.startsWith('clearledgr/invoices-view/')) {
      return {
        routeId: 'clearledgr/invoices-view/:ref',
        params: { ref: decodeURIComponent(normalized.slice('clearledgr/invoices-view/'.length)) },
      };
    }
    if (normalized.startsWith('clearledgr/pipeline-view/')) {
      return {
        routeId: 'clearledgr/pipeline-view/:ref',
        params: { ref: decodeURIComponent(normalized.slice('clearledgr/pipeline-view/'.length)) },
      };
    }

    return null;
  }

  async function readPendingDirectHashRoute() {
    try {
      if (globalThis.chrome?.runtime?.sendMessage) {
        const response = await globalThis.chrome.runtime.sendMessage({ action: 'getPendingDirectRouteForTab' });
        const pending = response?.pending || null;
        const hash = String(pending?.hash || '').trim();
        const ts = Number(pending?.ts || 0);
        const pathname = String(pending?.pathname || '').trim();
        if (hash.startsWith('clearledgr/') && Number.isFinite(ts) && (Date.now() - ts) <= 30000) {
          if (!pathname || pathname === String(window.location.pathname || '')) {
            return hash;
          }
        }
      }
    } catch (_) {
      /* best effort */
    }
    try {
      const attrValue = document?.documentElement?.getAttribute?.(ATTR_PENDING_DIRECT_ROUTE);
      const normalizedAttrValue = String(attrValue || '').trim();
      if (normalizedAttrValue.startsWith('clearledgr/')) return normalizedAttrValue;
    } catch (_) {
      /* best effort */
    }
    try {
      const raw = window.sessionStorage?.getItem?.(STORAGE_PENDING_DIRECT_ROUTE);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      const hash = String(parsed?.hash || '').trim();
      const ts = Number(parsed?.ts || 0);
      if (!hash.startsWith('clearledgr/')) return null;
      if (!Number.isFinite(ts) || (Date.now() - ts) > 20000) return null;
      return hash;
    } catch (_) {
      /* best effort */
    }
    try {
      if (globalThis.chrome?.storage?.session?.get) {
        const payload = await globalThis.chrome.storage.session.get([STORAGE_PENDING_DIRECT_ROUTE]);
        const pending = payload?.[STORAGE_PENDING_DIRECT_ROUTE];
        const hash = String(pending?.hash || '').trim();
        const ts = Number(pending?.ts || 0);
        const pathname = String(pending?.pathname || '').trim();
        if (!hash.startsWith('clearledgr/')) return null;
        if (!Number.isFinite(ts) || (Date.now() - ts) > 20000) return null;
        if (pathname && pathname !== String(window.location.pathname || '')) return null;
        return hash;
      }
    } catch (_) {
      /* best effort */
    }
    return null;
  }

  async function clearPendingDirectHashRoute() {
    try {
      if (globalThis.chrome?.runtime?.sendMessage) {
        await globalThis.chrome.runtime.sendMessage({ action: 'clearPendingDirectRouteForTab' });
      }
    } catch (_) {
      /* best effort */
    }
    try {
      document?.documentElement?.removeAttribute?.(ATTR_PENDING_DIRECT_ROUTE);
    } catch (_) {
      /* best effort */
    }
    try {
      window.sessionStorage?.removeItem?.(STORAGE_PENDING_DIRECT_ROUTE);
    } catch (_) {
      /* best effort */
    }
    try {
      if (globalThis.chrome?.storage?.session?.remove) {
        await globalThis.chrome.storage.session.remove(STORAGE_PENDING_DIRECT_ROUTE);
      }
    } catch (_) {
      /* best effort */
    }
  }

  async function maybeRestoreReloadedClearledgrRoute({ force = false } = {}) {
    if (!sdk?.Router || reloadRouteRestoreInFlight || hasRenderedClearledgrRouteThisBoot) return false;
    if (!navigationWasReload()) return false;
    if (normalizeClearledgrHash(window.location.hash)) return false;
    if (!force && (Date.now() - routeBootStartedAt) > ROUTE_RESTORE_WINDOW_MS) return false;

    const reloadHash = readReloadedClearledgrRoute();
    const target = parseDirectHashRoute(reloadHash || '');
    if (!target) return false;

    reloadRouteRestoreInFlight = true;
    try {
      sdk.Router.goto(target.routeId, target.params || undefined);
      clearReloadedClearledgrRoute();
      return true;
    } catch (_) {
      return false;
    } finally {
      window.setTimeout(() => {
        reloadRouteRestoreInFlight = false;
      }, 0);
    }
  }

  async function syncDirectHashRoute({ force = false } = {}) {
    if (!sdk?.Router || directHashSyncInFlight) return;

    const hash = String(window.location.hash || '').trim();
    const pendingHash = !hash.startsWith('#clearledgr/')
      ? await readPendingDirectHashRoute()
      : null;
    const target = parseDirectHashRoute(hash) || parseDirectHashRoute(pendingHash || '');
    if (!target) {
      lastDirectHashRoute = '';
      if (pendingHash && !parseDirectHashRoute(pendingHash)) {
        await clearPendingDirectHashRoute();
      }
      return;
    }

    const expectedHash = buildClearledgrRouteHash(target.routeId, target.params || undefined)
      || normalizeClearledgrHash(pendingHash || target.routeId);
    const routeSignature = `#${expectedHash || pendingHash || target.routeId}`;
    if (
      !force
      && routeSignature === lastDirectHashRoute
      && normalizeClearledgrHash(window.location.hash) === expectedHash
    ) return;
    directHashSyncInFlight = true;

    try {
      sdk.Router.goto(target.routeId, target.params || undefined);
      const confirmRouteActivation = async () => {
        const activeHash = normalizeClearledgrHash(window.location.hash);
        if (activeHash === expectedHash) {
          lastDirectHashRoute = routeSignature;
          rememberActiveClearledgrRoute(activeHash);
          await clearPendingDirectHashRoute();
          return;
        }
        lastDirectHashRoute = '';
      };

      if (normalizeClearledgrHash(window.location.hash) === expectedHash) {
        await confirmRouteActivation();
      } else {
        window.setTimeout(() => {
          void confirmRouteActivation();
        }, 120);
      }
    } catch (_) {
      lastDirectHashRoute = '';
      /* best effort */
    } finally {
      window.setTimeout(() => {
        directHashSyncInFlight = false;
      }, 0);
    }
  }

  function clearNavItemViews(handles) {
    handles.forEach((handle) => {
      try { handle?.remove?.(); } catch (_) { /* best-effort */ }
    });
    handles.length = 0;
  }

  function saveCurrentPipelineView() {
    const pipelineScope = {
      orgId: queueManager?.runtimeConfig?.organizationId || 'default',
      userEmail: sdk?.User?.getEmailAddress?.() || queueManager?.runtimeConfig?.userEmail || '',
    };
    const currentPreferences = readPipelinePreferences(pipelineScope);
    const suggestedName = String(
      (currentPreferences?.activeSliceId || 'view')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, (match) => match.toUpperCase())
    ).trim() || 'My view';
    const name = String(window.prompt('Name this view', suggestedName) || '').trim();
    if (!name) return;
    createSavedPipelineView(pipelineScope, {
      name,
      description: `Saved from ${currentPreferences?.activeSliceId ? currentPreferences.activeSliceId.replace(/_/g, ' ') : 'the current queue'}.`,
      pinned: true,
      snapshot: currentPreferences,
    });
    rebuildMenuNavigation();
    showToast(`Saved "${name}" to Views.`, 'success');
  }

  async function handlePrimaryAppMenuAction() {
    const currentItem = store.getCurrentItem?.() || null;
    const threadId = String(store.currentThreadId || '').trim();

    if (currentItem?.id) {
      navigateInboxRoute('clearledgr/invoice/:id', sdk, { id: currentItem.id });
      return;
    }

    if (threadId && queueManager?.recoverCurrentThread) {
      store.setSelectedItem?.(null);
      await setSidebarPanelOpen(true);
      try {
        const result = await queueManager.recoverCurrentThread(threadId);
        const recoveredItem = result?.item || null;
        if (recoveredItem?.id) {
          queueManager.upsertQueueItem?.(recoveredItem);
          queueManager.emitQueueUpdated?.();
          store.update({ selectedItemId: recoveredItem.id });
          writeLocalStorage(STORAGE_ACTIVE_AP_ITEM_ID, recoveredItem.id);
          navigateInboxRoute('clearledgr/invoice/:id', sdk, { id: recoveredItem.id });
          showToast('Finance record ready from this email.', 'success');
          return;
        }
      } catch (_) {
        /* fall through to info guidance */
      }
      showToast('Use the right-hand Clearledgr panel to create a record from this email or link it to an existing record.', 'info');
      return;
    }

    navigateInboxRoute(DEFAULT_ROUTE, sdk);
    showToast('Open an invoice email, then use New record again to create or link a finance record from that email.', 'info');
  }

  function renderAppMenuPanelChrome({ workspaceRoutes = [], pinnedViews = [], configurationRoutes = [], libraryRoutes = [] } = {}) {
    const panelRoot = resolveAppMenuPanelRoot();
    if (!panelRoot) return;
    const navContainer = panelRoot.querySelector('.nM.inboxsdk__collapsiblePanel_navItems, .inboxsdk__collapsiblePanel_navItems');
    const currentHash = normalizeClearledgrHash(window.location.hash) || lastActiveClearledgrRoute;

    let shell = panelRoot.querySelector('.cl-appmenu-panel-shell');
    if (!(shell instanceof HTMLElement)) {
      shell = document.createElement('div');
      shell.className = 'cl-appmenu-panel-shell';
      if (navContainer?.parentNode) {
        navContainer.parentNode.insertBefore(shell, navContainer);
      } else {
        panelRoot.appendChild(shell);
      }
    }
    shell.innerHTML = '';

    const cta = document.createElement('button');
    cta.type = 'button';
    cta.className = 'cl-appmenu-panel-cta';
    cta.innerHTML = `
      <span class="cl-appmenu-panel-cta-icon">+</span>
      <span class="cl-appmenu-panel-cta-copy">New record</span>
    `;
    cta.addEventListener('click', () => {
      void handlePrimaryAppMenuAction();
    });
    shell.appendChild(cta);

    const renderSection = (title, rows = [], options = {}) => {
      if (!Array.isArray(rows) || rows.length === 0) return;
      const section = document.createElement('div');
      section.className = 'cl-appmenu-panel-section';

      if (title) {
        if (options.trailingActionLabel) {
          const header = document.createElement('div');
          header.className = 'cl-appmenu-panel-section-header';
          header.innerHTML = `
            <span class="cl-appmenu-panel-section-title">${title}</span>
            <button type="button" class="cl-appmenu-panel-section-action" aria-label="${options.trailingActionAriaLabel || options.trailingActionLabel}">${options.trailingActionLabel}</button>
          `;
          header.querySelector('.cl-appmenu-panel-section-action')?.addEventListener('click', () => {
            options.onTrailingAction?.();
          });
          section.appendChild(header);
        } else {
          const label = document.createElement('div');
          label.className = 'cl-appmenu-panel-label';
          label.textContent = title;
          section.appendChild(label);
        }
      }

      const list = document.createElement('div');
      list.className = 'cl-appmenu-panel-view-list';
      section.appendChild(list);

      rows.forEach((row) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'cl-appmenu-panel-view-item';
        if (row.active) button.classList.add('is-active');

        const icon = document.createElement('span');
        icon.className = 'cl-appmenu-panel-view-icon';
        if (row.iconUrl) {
          const iconImage = document.createElement('img');
          iconImage.className = 'cl-appmenu-panel-view-icon-image';
          iconImage.alt = '';
          iconImage.src = row.iconUrl;
          icon.appendChild(iconImage);
        } else {
          icon.textContent = row.iconText || '•';
        }

        const meta = document.createElement('span');
        meta.className = 'cl-appmenu-panel-view-meta';

        const name = document.createElement('span');
        name.className = 'cl-appmenu-panel-view-name';
        name.textContent = String(row.name || 'Route');
        meta.appendChild(name);

        if (row.description) {
          const description = document.createElement('span');
          description.className = 'cl-appmenu-panel-view-description';
          description.textContent = String(row.description || '');
          meta.appendChild(description);
        }

        button.appendChild(icon);
        button.appendChild(meta);
        button.addEventListener('click', () => {
          row.onClick?.();
        });
        list.appendChild(button);
      });

      shell.appendChild(section);
    };

    renderSection('Workspace', workspaceRoutes.map((route) => ({
      name: route.title,
      iconUrl: getRouteIconUrl(route),
      active: currentHash === normalizeClearledgrHash(route.id),
      onClick: () => navigateInboxRoute(route.id, sdk),
    })));

    const viewsToRender = Array.isArray(pinnedViews) ? pinnedViews.slice(0, 6) : [];
    const viewRows = viewsToRender.length > 0
      ? viewsToRender.map((view) => {
          const viewHash = buildClearledgrRouteHash(view.id, view.routeParams || undefined);
          return {
            name: String(view?.name || view?.title || 'Saved view'),
            description: String(view?.description || 'Open this AP queue view in Gmail.'),
            iconText: '▸',
            active: Boolean(viewHash && currentHash === viewHash),
            onClick: () => navigateInboxRoute(view.id, sdk, view.routeParams || undefined),
          };
        })
      : [{
          name: 'Save your first view',
          description: 'Pin the queues your finance team comes back to every day.',
          iconText: '+',
          active: false,
          onClick: saveCurrentPipelineView,
        }];

    renderSection('Views', viewRows, {
      trailingActionLabel: '+',
      trailingActionAriaLabel: 'Save current view',
      onTrailingAction: saveCurrentPipelineView,
    });

    renderSection('Configurations', configurationRoutes.map((route) => ({
      name: route.title,
      iconUrl: getRouteIconUrl(route),
      active: currentHash === normalizeClearledgrHash(route.id),
      onClick: () => navigateInboxRoute(route.id, sdk),
    })));

    renderSection('Templates', libraryRoutes.map((route) => ({
      name: route.title,
      iconUrl: getRouteIconUrl(route),
      active: currentHash === normalizeClearledgrHash(route.id),
      onClick: () => navigateInboxRoute(route.id, sdk),
    })));
  }

  async function rebuildMenuNavigation() {
    if (!routeAccessResolved) return;

    // Wait for the AppMenu panel to be ready before populating
    if (appMenuPanelReady) {
      try { await appMenuPanelReady; } catch (_) { /* panel failed, will use fallback */ }
    }

    const routeOptions = currentRouteAccess;
    const routePreferences = readRoutePreferences(routeOptions);
    const menuRoutes = getMenuNavRoutes(routePreferences, routeOptions);
    const workspaceRoutes = menuRoutes.filter((route) => APPMENU_WORKSPACE_ROUTE_IDS.has(route.id));
    const pipelineScope = {
      orgId: queueManager?.runtimeConfig?.organizationId || 'default',
      userEmail: sdk?.User?.getEmailAddress?.() || queueManager?.runtimeConfig?.userEmail || '',
    };
    const pinnedViewRoutes = getPinnedPipelineViews(readPipelinePreferences(pipelineScope))
      .slice(0, 3)
      .map((view) => ({
        title: view.name,
        name: view.name,
        description: view.description || 'Pinned AP queue view.',
        id: 'clearledgr/invoices-view/:ref',
        routeParams: { ref: getPipelineViewRef(view) },
        iconUrl: getPipelineViewIconUrl(),
      }));
    clearNavItemViews(appMenuNavItemViews);
    clearNavItemViews(fallbackNavItemViews);

    if (appMenuPanelView && typeof appMenuPanelView.addNavItem === 'function') {
      renderAppMenuPanelChrome({
        workspaceRoutes,
        pinnedViews: pinnedViewRoutes,
        configurationRoutes: menuRoutes.filter((route) => APPMENU_CONFIGURATION_ROUTE_IDS.has(route.id)),
        libraryRoutes: menuRoutes.filter((route) => APPMENU_LIBRARY_ROUTE_IDS.has(route.id)),
      });
      return;
    }

    // Fallback only if AppMenu panel genuinely failed (not just slow)
    if (sdk.NavMenu && typeof sdk.NavMenu.addNavItem === 'function') {
      menuRoutes.forEach((route) => {
        const navHandle = sdk.NavMenu.addNavItem({
          name: route.title,
          routeID: route.id,
          type: 'NAVIGATION',
          iconUrl: getRouteIconUrl(route),
        });
        fallbackNavItemViews.push(navHandle);
      });
      pinnedViewRoutes.forEach((route) => {
        const navHandle = sdk.NavMenu.addNavItem({
          name: route.title,
          routeID: route.id,
          routeParams: route.routeParams,
          type: 'NAVIGATION',
          iconUrl: route.iconUrl,
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

  const workspaceShellApi = createWorkspaceShellApi(queueManager);
  const oauthBridge = createOAuthBridge(() => {
    bootstrapCache = null;
    queueManager?.scanNow?.();
    void getBootstrap();
  });

  store.sdk = sdk;
  store.openComposeWithPrefill = openComposeWithPrefill;

  let bootstrapCache = null;
  let bootstrapPromise = null;
  // Start with full view access — never gate navigation on bootstrap.
  // Manage capabilities get refined when bootstrap resolves.
  let currentRouteAccess = { capabilities: getCapabilities({}) };
  let routeAccessResolved = true;

  async function getBootstrap() {
    if (bootstrapCache) return bootstrapCache;
    if (bootstrapPromise) return bootstrapPromise;
    bootstrapPromise = workspaceShellApi.bootstrapWorkspaceShellData().then((data) => {
      bootstrapCache = data;
      queueManager.currentUserRole = data?.current_user?.role || null;
      const gmailIntegration = Array.isArray(data?.integrations)
        ? data.integrations.find((integration) => integration?.name === 'gmail') || null
        : null;
      store.update({
        currentUserRole: queueManager.currentUserRole,
        gmailIntegration,
      });
      const pipelineScope = {
        orgId: queueManager?.runtimeConfig?.organizationId || 'default',
        userEmail: sdk?.User?.getEmailAddress?.() || queueManager?.runtimeConfig?.userEmail || '',
      };
      const remotePipelinePrefs = getBootstrappedPipelinePreferences(data);
      if (remotePipelinePrefs) {
        const localPipelinePrefs = readPipelinePreferences(pipelineScope);
        const normalizedRemotePipelinePrefs = normalizePipelinePreferences(remotePipelinePrefs);
        if (!pipelinePreferencesEqual(localPipelinePrefs, normalizedRemotePipelinePrefs)) {
          writePipelinePreferences(pipelineScope, normalizedRemotePipelinePrefs);
        }
      }
      const nextRouteAccess = {
        capabilities: getCapabilities(data),
      };
      const hadResolvedRouteAccess = routeAccessResolved;
      routeAccessResolved = true;
      if (
        !hadResolvedRouteAccess
        || appMenuNavItemViews.length === 0
        || JSON.stringify(nextRouteAccess.capabilities) !== JSON.stringify(currentRouteAccess.capabilities)
      ) {
        currentRouteAccess = nextRouteAccess;
        rebuildMenuNavigation();
      }
      bootstrapPromise = null;
      return data;
    }).catch(() => {
      bootstrapPromise = null;
      routeAccessResolved = true;
      currentRouteAccess = { capabilities: getCapabilities({}) };
      if (appMenuNavItemViews.length === 0 && fallbackNavItemViews.length === 0) {
        rebuildMenuNavigation();
      }
      return {};
    });
    return bootstrapPromise;
  }

  function onRefresh() {
    bootstrapCache = null;
  }

  void getBootstrap();

  sdk.Router.handleCustomRoute('clearledgr/invoices-view/:ref', async (customRouteView) => {
    bindRouteSidebarBehavior(customRouteView);
    const releaseDocumentTitle = claimRouteDocumentTitle('Saved View');
    customRouteView?.on?.('destroy', releaseDocumentTitle);
    const params = customRouteView.getParams?.() || {};
    const rawRef = params.ref || window.location.hash.split('clearledgr/invoices-view/')[1]?.split('?')[0] || '';
    const pipelineScope = {
      orgId: queueManager?.runtimeConfig?.organizationId || 'default',
      userEmail: sdk?.User?.getEmailAddress?.() || queueManager?.runtimeConfig?.userEmail || '',
    };
    const bootstrap = await getBootstrap();
    const remotePipelinePrefs = getBootstrappedPipelinePreferences(bootstrap);
    let prefs = readPipelinePreferences(pipelineScope);
    if (remotePipelinePrefs) {
      const normalizedRemotePrefs = normalizePipelinePreferences(remotePipelinePrefs);
      if (!pipelinePreferencesEqual(prefs, normalizedRemotePrefs)) {
        prefs = writePipelinePreferences(pipelineScope, normalizedRemotePrefs);
      } else {
        prefs = normalizedRemotePrefs;
      }
    }
    const targetView = resolvePipelineViewByRef(prefs, decodeURIComponent(rawRef));
    if (targetView?.snapshot) {
      clearPipelineNavigation(pipelineScope);
      writePipelinePreferences(pipelineScope, targetView.snapshot);
    }
    sdk.Router.goto('clearledgr/invoices');
    try {
      customRouteView.destroy?.();
    } catch (_) { /* best effort */ }
  });

  // Legacy redirect: old pipeline URL → new invoices URL
  sdk.Router.handleCustomRoute('clearledgr/pipeline', () => {
    sdk.Router.goto('clearledgr/invoices');
  });
  sdk.Router.handleCustomRoute('clearledgr/pipeline-view/:ref', (routeView) => {
    const ref = routeView.getParams?.()?.ref || '';
    sdk.Router.goto('clearledgr/invoices-view/' + ref);
  });

  // Dynamic route: invoice detail (clearledgr/invoice/:id)
  sdk.Router.handleCustomRoute('clearledgr/invoice/:id', async (customRouteView) => {
    bindRouteSidebarBehavior(customRouteView);
    const releaseDocumentTitle = claimRouteDocumentTitle('Record Detail');
    customRouteView?.on?.('destroy', releaseDocumentTitle);
    const container = document.createElement('div');
    container.className = 'cl-route cl-route-record-detail';
    const style = document.createElement('style');
    style.textContent = ROUTE_CSS;
    container.appendChild(style);
    const topbar = document.createElement('div');
    topbar.className = 'topbar';
    topbar.innerHTML = '<h2>Record Detail</h2>';
    container.appendChild(topbar);
    const pageMount = document.createElement('div');
    container.appendChild(pageMount);
    const routeEl = prepareRouteHost(customRouteView);
    routeEl.appendChild(container);

    const params = customRouteView.getParams?.() || {};
    const rawId = resolveRecordRouteId(params, window.location.hash);
    rememberActiveClearledgrRoute('clearledgr/invoice/:id', { id: rawId });
    rebuildMenuNavigation();
    const orgId = workspaceShellApi.orgId();
    const navigate = (routeId, params) => sdk.Router.goto(routeId, params);
    const userEmail = sdk.User?.getEmailAddress?.() || queueManager?.runtimeConfig?.userEmail || '';
    const bootstrap = await getBootstrap();

    render(html`<${InvoiceDetailPage}
      api=${workspaceShellApi.api}
      bootstrap=${bootstrap}
      toast=${workspaceShellApi.toast}
      orgId=${orgId}
      userEmail=${userEmail}
      navigate=${navigate}
      routeParams=${{ id: rawId }}
    />`, pageMount);
  });

  sdk.Router.handleCustomRoute('clearledgr/vendor/:name', async (customRouteView) => {
    bindRouteSidebarBehavior(customRouteView);
    const releaseDocumentTitle = claimRouteDocumentTitle('Vendor Detail');
    customRouteView?.on?.('destroy', releaseDocumentTitle);
    const container = document.createElement('div');
    container.className = 'cl-route';
    const style = document.createElement('style');
    style.textContent = ROUTE_CSS;
    container.appendChild(style);
    const topbar = document.createElement('div');
    topbar.className = 'topbar';
    topbar.innerHTML = '<h2>Vendor Detail</h2>';
    container.appendChild(topbar);
    const pageMount = document.createElement('div');
    container.appendChild(pageMount);
    const routeEl = prepareRouteHost(customRouteView);
    routeEl.appendChild(container);

    const params = customRouteView.getParams?.() || {};
    const rawName = resolveVendorRouteName(params, window.location.hash);
    rememberActiveClearledgrRoute('clearledgr/vendor/:name', { name: rawName });
    rebuildMenuNavigation();
    const orgId = workspaceShellApi.orgId();
    const navigate = (routeId, params) => sdk.Router.goto(routeId, params);
    const userEmail = sdk.User?.getEmailAddress?.() || queueManager?.runtimeConfig?.userEmail || '';
    const bootstrap = await getBootstrap();

    render(html`<${VendorDetailPage}
      api=${workspaceShellApi.api}
      bootstrap=${bootstrap}
      toast=${workspaceShellApi.toast}
      orgId=${orgId}
      userEmail=${userEmail}
      navigate=${navigate}
      routeParams=${{ name: rawName }}
    />`, pageMount);
  });

  for (const route of ROUTES) {
    const PageComponent = PAGE_MAP[route.id];
    if (!PageComponent) continue;

    sdk.Router.handleCustomRoute(route.id, async (customRouteView) => {
      bindRouteSidebarBehavior(customRouteView);
      rememberActiveClearledgrRoute(route.id);
      rebuildMenuNavigation();
      const releaseDocumentTitle = claimRouteDocumentTitle(route.title);
      customRouteView?.on?.('destroy', releaseDocumentTitle);
      const container = document.createElement('div');
      container.className = 'cl-route';

      const style = document.createElement('style');
      style.textContent = ROUTE_CSS;
      container.appendChild(style);

      const topbar = document.createElement('div');
      topbar.className = 'topbar';
      topbar.innerHTML = `<h2>${route.title}</h2><p>${route.subtitle}</p>`;
      if (route.hideTopbar !== true) {
        container.appendChild(topbar);
      }

      const pageMount = document.createElement('div');
      container.appendChild(pageMount);
      const routeEl = prepareRouteHost(customRouteView);
      routeEl.appendChild(container);

      const orgId = workspaceShellApi.orgId();
      const navigate = (routeId, params) => sdk.Router.goto(routeId, params);
      const userEmail = sdk.User?.getEmailAddress?.() || queueManager?.runtimeConfig?.userEmail || '';

      let renderCurrentPage = async () => {};
      const updateRoutePreferences = async (nextPreferences) => {
        const bootstrap = await getBootstrap();
        const routeOptions = { capabilities: getCapabilities(bootstrap) };
        const normalized = writeRoutePreferences(nextPreferences, routeOptions);
        rebuildMenuNavigation();
        await renderCurrentPage();
        return normalized;
      };

      renderCurrentPage = async () => {
        const bootstrap = await getBootstrap();
        const routeOptions = { capabilities: getCapabilities(bootstrap) };
        if (!canViewRoute(route, routeOptions)) {
          render(html`
            <div class="panel">
              <h3 style="margin:0 0 8px">Access restricted</h3>
              <p class="muted" style="margin:0 0 12px">This page is not enabled for your workspace access.</p>
              <button onClick=${() => navigate(DEFAULT_ROUTE)}>Back to Invoices</button>
            </div>
          `, pageMount);
          return;
        }
        const routePreferences = readRoutePreferences(routeOptions);
        render(html`<${PageComponent}
          bootstrap=${bootstrap}
          api=${workspaceShellApi.api}
          toast=${workspaceShellApi.toast}
          orgId=${orgId}
          userEmail=${userEmail}
          onRefresh=${async () => { onRefresh(); await renderCurrentPage(); }}
          oauthBridge=${oauthBridge}
          navigate=${navigate}
          routePreferences=${routePreferences}
          availableRoutes=${getNavEligibleRoutes(routeOptions)}
          updateRoutePreferences=${updateRoutePreferences}
          routeId=${route.id}
        />`, pageMount);
      };

      await renderCurrentPage();
    });
  }

  for (const [routeId, PageComponent] of Object.entries(LEGACY_PAGE_MAP)) {
    sdk.Router.handleCustomRoute(routeId, async (customRouteView) => {
      bindRouteSidebarBehavior(customRouteView);
      rememberActiveClearledgrRoute(routeId);
      rebuildMenuNavigation();
      const releaseDocumentTitle = claimRouteDocumentTitle(routeId === 'clearledgr/plan' ? 'Billing' : 'Settings');
      customRouteView?.on?.('destroy', releaseDocumentTitle);
      const container = document.createElement('div');
      container.className = 'cl-route';

      const style = document.createElement('style');
      style.textContent = ROUTE_CSS;
      container.appendChild(style);

      const topbar = document.createElement('div');
      topbar.className = 'topbar';
      if (routeId === 'clearledgr/plan') {
        topbar.innerHTML = '<h2>Billing</h2><p>Plan, usage, and workspace limits.</p>';
      } else {
        topbar.innerHTML = '<h2>Settings</h2><p>Team, workspace, and billing.</p>';
      }
      container.appendChild(topbar);

      const pageMount = document.createElement('div');
      container.appendChild(pageMount);
      prepareRouteHost(customRouteView)?.appendChild(container);

      const orgId = workspaceShellApi.orgId();
      const navigate = (nextRouteId, params) => sdk.Router.goto(nextRouteId, params);
      const userEmail = sdk.User?.getEmailAddress?.() || queueManager?.runtimeConfig?.userEmail || '';

      const renderCurrentPage = async () => {
        const bootstrap = await getBootstrap();
        const routeOptions = { capabilities: getCapabilities(bootstrap) };
        if (settingsRoute && !canViewRoute(settingsRoute, routeOptions)) {
          render(html`
            <div class="panel">
              <h3 style="margin:0 0 8px">Access restricted</h3>
              <p class="muted" style="margin:0 0 12px">This page is not enabled for your workspace access.</p>
              <button onClick=${() => navigate(DEFAULT_ROUTE)}>Back to Invoices</button>
            </div>
          `, pageMount);
          return;
        }
        const routePreferences = readRoutePreferences(routeOptions);
        render(html`<${PageComponent}
          bootstrap=${bootstrap}
          api=${workspaceShellApi.api}
          toast=${workspaceShellApi.toast}
          orgId=${orgId}
          userEmail=${userEmail}
          onRefresh=${async () => { onRefresh(); await renderCurrentPage(); }}
          oauthBridge=${oauthBridge}
          navigate=${navigate}
          routePreferences=${routePreferences}
          availableRoutes=${getNavEligibleRoutes(routeOptions)}
          updateRoutePreferences=${async (nextPreferences) => {
            const normalized = writeRoutePreferences(nextPreferences, routeOptions);
            rebuildMenuNavigation();
            await renderCurrentPage();
            return normalized;
          }}
          routeId=${routeId}
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
        injectAppMenuPanelStyles();
        appMenuPanelReady = appMenuItemView.addCollapsiblePanel({
          className: 'cl-appmenu-panel',
        })
          .then((panel) => {
            if (!panel || typeof panel.addNavItem !== 'function') return;
            appMenuPanelView = panel;
            try { panel.on?.('destroy', () => { appMenuPanelView = null; }); } catch (_) { /* best effort */ }
          })
          .catch((err) => {
            console.warn('[Clearledgr] CollapsiblePanel failed:', err);
            appMenuPanelReady = null;
          });
      }
    } catch (err) {
      console.warn('[Clearledgr] AppMenu not available, falling back to NavMenu', err);
      rebuildMenuNavigation();
    }
  }

  window.addEventListener('pagehide', persistReloadedClearledgrRoute, true);
  window.addEventListener('beforeunload', persistReloadedClearledgrRoute, true);
  window.addEventListener('hashchange', () => {
    const currentClearledgrHash = normalizeClearledgrHash(window.location.hash);
    if (currentClearledgrHash) {
      lastActiveClearledgrRoute = currentClearledgrHash;
    } else {
      lastKnownMailboxDocumentTitle = String(document.title || '').trim() || lastKnownMailboxDocumentTitle;
    }
    rebuildMenuNavigation();
    window.setTimeout(async () => {
      const restored = await maybeRestoreReloadedClearledgrRoute();
      if (!restored) {
        await syncDirectHashRoute();
      }
    }, 0);
  });
  window.setTimeout(async () => {
    const restored = await maybeRestoreReloadedClearledgrRoute({ force: true });
    if (!restored) {
      await syncDirectHashRoute({ force: true });
    }
  }, 0);
  window.setTimeout(() => clearReloadedClearledgrRoute(), ROUTE_RESTORE_WINDOW_MS);
}

bootstrap();

console.log(
  '\n%cClearledgr\n%cThe Gmail AP Workspace\nfor Finance Teams\n\n%cYou found us in the console.\nThat means you care how things work.\nSo do we.\n\n%chttps://clearledgr.com\n',
  'font-size:28px;font-weight:800;color:#00D67E;line-height:1.2;',
  'font-size:18px;font-weight:600;color:#0A1628;line-height:1.3;',
  'font-size:14px;color:#6B7280;line-height:1.5;',
  'font-size:13px;color:#00D67E;font-weight:600;',
);
