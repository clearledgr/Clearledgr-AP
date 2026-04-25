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
import { perfMarkStart } from './utils/perf-budget.js';
import OnboardingFlow from './components/OnboardingFlow.js';
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
import ExceptionsPage from './routes/pages/ExceptionsPage.js';
import PipelinePage from './routes/pages/PipelinePage.js';
import InvoiceDetailPage from './routes/pages/InvoiceDetailPage.js';
import VendorsPage from './routes/pages/VendorsPage.js';
import VendorDetailPage from './routes/pages/VendorDetailPage.js';
import TemplatesPage from './routes/pages/TemplatesPage.js';
import ReportsPage from './routes/pages/ReportsPage.js';
import VendorOnboardingPage from './routes/pages/VendorOnboardingPage.js';
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
let _cachedExceptionCount = 0;
// §6.2 — thesis-defined nav structure:
// Primary: Home, AP Invoices (badge), Vendor Onboarding, Agent Activity.
// Saved Views (nested): Exceptions, Awaiting Approval, Due This Week.
// Settings: single entry per §16.
const APPMENU_PRIMARY_ROUTE_IDS = new Set([
  'clearledgr/home',
  'clearledgr/invoices',
  'clearledgr/vendor-onboarding',
  'clearledgr/activity',
]);
const APPMENU_SETTINGS_ROUTE_IDS = new Set([
  'clearledgr/settings',
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
    .cl-appmenu-panel-view-badge {
      margin-left: auto;
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 18px;
      height: 18px;
      padding: 0 5px;
      border-radius: 9px;
      background: #dc2626;
      color: #fff;
      font: 600 10px/1 "DM Sans", sans-serif;
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

    // §4.07 sidebar-load budget — clock starts the moment a thread opens,
    // ends when SidebarApp paints a ThreadSidebar with a resolved Box.
    perfMarkStart('sidebar');

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
            const backendUrl = String(queueManager?.runtimeConfig?.backendUrl || '').replace(/\/+$/, '');
            const result = await queueManager.backendFetch(
              `${backendUrl}/extension/by-thread/${encodeURIComponent(threadId)}`
            );
            if (result?.ok) {
              const data = await result.json();
              if (data?.found && data?.item) {
                item = data.item;
              } else {
                const recovered = await queueManager.backendFetch(
                  `${backendUrl}/extension/by-thread/${encodeURIComponent(threadId)}/recover`,
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

        // Inject thread-top banners for finance-record threads (Mixmax-style).
        // InboxSDK's addNoticeBar prepends; the call order is reversed
        // from the visual order so the most actionable signal sits
        // nearest the message body:
        //
        //   [exception banner]  ← if active exception
        //   [approval banner]   ← if state in needs_approval / pending_approval
        //   [state banner]      ← always
        //   [Gmail message body]
        //
        // The state banner is the always-on identity row (vendor +
        // amount + state pill); the contextual banners add the "what's
        // happening right now" expansion that previously required
        // leaving Gmail to discover.
        if (item && typeof threadView.addNoticeBar === 'function') {
          injectExceptionBanner(threadView, item);
          injectApprovalBanner(threadView, item);
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
    approved:         { bg: '#ECFDF5', border: '#10B981', text: '#16A34A', label: 'Approved' },
    ready_to_post:    { bg: '#ECFDF5', border: '#10B981', text: '#16A34A', label: 'Ready to post' },
    posted_to_erp:    { bg: '#ECFDF5', border: '#10B981', text: '#16A34A', label: 'Posted to ERP' },
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

// Phase 3.1: contextual exception banner. Renders above the state banner
// when the AP item has an active exception (exception_code set, or
// requires_field_review, or non-empty field_review_blockers). The intent
// is to surface "what's blocking this thread, and why" inline in the
// Gmail message view so the user doesn't have to leave Gmail to find
// out what an Exception means. Streak/Fyxer-style: invisible AI, native
// Gmail primitives.
function _itemHasActiveException(item) {
  if (!item) return false;
  if (item.exception_code) return true;
  if (item.requires_field_review) return true;
  const blockers = Array.isArray(item.field_review_blockers)
    ? item.field_review_blockers
    : [];
  if (blockers.length > 0) return true;
  const pipelineBlockers = Array.isArray(item.pipeline_blockers)
    ? item.pipeline_blockers
    : [];
  if (pipelineBlockers.length > 0) return true;
  return false;
}

function _humanizeExceptionCode(code) {
  if (!code) return 'Exception raised';
  // Match the snake_case → Title-Case pattern used by ThreadSidebar's
  // humanizeEventType so banner copy stays consistent with the timeline.
  return String(code)
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function _exceptionSeverityConfig(severity) {
  const sev = String(severity || '').toLowerCase();
  if (sev === 'critical') return { bg: '#fef2f2', border: '#dc2626', text: '#991b1b', label: 'Critical' };
  if (sev === 'high')     return { bg: '#fef9ee', border: '#ea580c', text: '#9a3412', label: 'High' };
  if (sev === 'medium')   return { bg: '#fefce8', border: '#ca8a04', text: '#854d0e', label: 'Medium' };
  if (sev === 'low')      return { bg: '#f0fdf4', border: '#16a34a', text: '#166534', label: 'Low' };
  // No declared severity but we know there's an exception — render in
  // the same warning palette as the state banner uses for needs_info.
  return { bg: '#fef9ee', border: '#d97706', text: '#92400e', label: 'Exception' };
}

function injectExceptionBanner(threadView, item) {
  if (!_itemHasActiveException(item)) return;
  if (typeof threadView.addNoticeBar !== 'function') return;

  const cfg = _exceptionSeverityConfig(item.exception_severity);
  const headline = _humanizeExceptionCode(item.exception_code)
    || (item.requires_field_review ? 'Field review required' : 'Exception raised');

  // Up to 3 most relevant blocker bullets — anything more belongs in
  // the sidebar's full Exceptions section so the banner stays scannable.
  const fieldBlockers = (Array.isArray(item.field_review_blockers) ? item.field_review_blockers : [])
    .slice(0, 3)
    .map((b) => {
      if (!b) return '';
      const field = String(b.field_name || b.field || '').replace(/_/g, ' ');
      const reason = String(b.reason || b.message || '').replace(/_/g, ' ');
      if (field && reason) return `${field}: ${reason}`;
      return field || reason;
    })
    .filter(Boolean);

  const el = document.createElement('div');
  el.style.cssText = `
    display:flex; align-items:flex-start; gap:12px; padding:10px 16px;
    background:${cfg.bg}; border-left:3px solid ${cfg.border};
    font-family:Inter,-apple-system,system-ui,sans-serif; font-size:13px; color:${cfg.text};
  `;

  const left = document.createElement('div');
  left.style.cssText = 'flex:1; display:flex; flex-direction:column; gap:4px;';

  const titleRow = document.createElement('div');
  titleRow.style.cssText = 'display:flex; align-items:center; gap:8px;';
  const sevPill = document.createElement('span');
  sevPill.style.cssText = `
    font-size:11px; font-weight:600; padding:2px 10px; border-radius:999px;
    background:${cfg.border}20; color:${cfg.text}; text-transform:uppercase; letter-spacing:0.02em;
  `;
  sevPill.textContent = cfg.label;
  titleRow.appendChild(sevPill);
  const title = document.createElement('span');
  title.style.cssText = 'font-weight:600;';
  title.textContent = headline;
  titleRow.appendChild(title);
  left.appendChild(titleRow);

  if (fieldBlockers.length > 0) {
    const list = document.createElement('div');
    list.style.cssText = 'font-size:12px; opacity:0.9;';
    list.textContent = fieldBlockers.join(' • ');
    left.appendChild(list);
  }

  el.appendChild(left);

  if (item?.id) {
    const btnGroup = document.createElement('div');
    btnGroup.style.cssText = 'display:flex; align-items:center; gap:8px;';

    // Phase 3.3: "Suggest reply" — pre-fills a Gmail Compose with a
    // template matched to the AP item's exception state. Lives on the
    // exception banner specifically because exceptions are the most
    // common reason to chase the vendor for missing info; approval
    // banners go to internal approvers (Slack/Teams), not vendors.
    const replyBtn = document.createElement('button');
    replyBtn.textContent = 'Suggest reply';
    replyBtn.style.cssText = `
      border:none; border-radius:6px;
      padding:5px 14px; font-size:12px; font-weight:600; cursor:pointer;
      background:${cfg.border}; color:#fff; font-family:inherit;
    `;
    replyBtn.addEventListener('click', () => {
      // Disable the button while the request is in flight so the user
      // doesn't open multiple composes from a double-click.
      replyBtn.disabled = true;
      const originalText = replyBtn.textContent;
      replyBtn.textContent = 'Drafting…';
      Promise.resolve()
        .then(() => suggestReplyForItem(item))
        .catch((err) => {
          // Best-effort surface; queue-manager already logs richly.
          console.warn('[Clearledgr] Suggest reply failed:', err);
          if (typeof showToast === 'function') {
            showToast('Could not generate draft. Try again from the sidebar.', 'error');
          }
        })
        .finally(() => {
          replyBtn.disabled = false;
          replyBtn.textContent = originalText;
        });
    });
    btnGroup.appendChild(replyBtn);

    const detailsBtn = document.createElement('button');
    detailsBtn.textContent = 'View details';
    detailsBtn.style.cssText = `
      border:1px solid ${cfg.border}; border-radius:6px;
      padding:5px 14px; font-size:12px; font-weight:600; cursor:pointer;
      background:transparent; color:${cfg.text}; font-family:inherit;
    `;
    // The sidebar is already open and bound to this thread; the click
    // routes through the same intent the Exceptions tab uses, so
    // clicking from the banner lands at the same context.
    detailsBtn.addEventListener('click', () => {
      openItemInPipeline(item, 'thread_exception_banner');
    });
    btnGroup.appendChild(detailsBtn);

    el.appendChild(btnGroup);
  }

  threadView.addNoticeBar({ el });
}

async function suggestReplyForItem(item) {
  if (!item?.id) return;
  if (!queueManager) throw new Error('queue_manager_unavailable');
  const backendUrl = String(queueManager?.runtimeConfig?.backendUrl || '').replace(/\/+$/, '');
  if (!backendUrl) throw new Error('backend_url_unavailable');

  const response = await queueManager.backendFetch(`${backendUrl}/extension/draft-reply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ap_item_id: String(item.id),
      thread_id: String(item.thread_id || store.currentThreadId || ''),
      organization_id: queueManager?.runtimeConfig?.organizationId || 'default',
    }),
  });
  if (!response?.ok) {
    throw new Error(`draft_reply_http_${response?.status || 'unknown'}`);
  }
  const draft = await response.json();
  if (!draft || (!draft.subject && !draft.body)) {
    throw new Error('draft_reply_empty');
  }

  // Hand off to the existing compose pre-fill plumbing — same path
  // every other "Draft vendor reply" CTA in the sidebar uses, so we
  // get the compose-record status bar and audit linkage for free.
  await openComposeWithPrefill({
    to: draft.to || '',
    subject: draft.subject || '',
    body: draft.body || '',
    recordContext: buildComposeRecordContext(item),
  });
}

// Phase 3.2: contextual approval banner. Stacks above the state banner
// when state is needs_approval / pending_approval, surfacing the
// information a user previously had to leave Gmail to find: who the
// approver is, how long they've been sitting on it, and whether the
// SLA window has tipped into nudge or escalation territory. The state
// banner still renders ("NEEDS APPROVAL" pill + amount); this banner
// is the context expansion. Same Streak/Fyxer pattern as 3.1's
// exception banner.
function _itemAwaitsApproval(item) {
  if (!item) return false;
  const state = String(item.state || '').toLowerCase();
  return state === 'needs_approval' || state === 'pending_approval';
}

function _humanizeWaitMinutes(minutes) {
  const m = Math.max(0, Math.round(Number(minutes) || 0));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const remM = m - h * 60;
  if (h < 24) return remM ? `${h}h ${remM}m` : `${h}h`;
  const d = Math.floor(h / 24);
  const remH = h - d * 24;
  return remH ? `${d}d ${remH}h` : `${d}d`;
}

function _formatApprovers(assignees) {
  const list = (Array.isArray(assignees) ? assignees : [])
    .map((a) => String(a || '').trim())
    .filter(Boolean);
  if (list.length === 0) return '';
  // Strip the domain so the banner reads "Awaiting Mo, Sarah" instead
  // of "mo@x.com, sarah@y.com" — the full email lives in the sidebar's
  // approval section if the user wants disambiguation.
  const display = list.slice(0, 2).map((a) => a.split('@')[0]);
  if (list.length > 2) display.push(`+${list.length - 2} more`);
  return display.join(', ');
}

function _approvalUrgencyConfig(followup) {
  const f = followup && typeof followup === 'object' ? followup : {};
  if (f.escalation_due) {
    return { bg: '#fef2f2', border: '#dc2626', text: '#991b1b', label: 'Escalate' };
  }
  if (f.sla_breached) {
    return { bg: '#fef2f2', border: '#dc2626', text: '#991b1b', label: 'SLA breached' };
  }
  // Within SLA — soft yellow, same palette as the state banner's
  // needs_approval theme so the two visually belong together.
  return { bg: '#fef9ee', border: '#d97706', text: '#92400e', label: 'Waiting' };
}

function injectApprovalBanner(threadView, item) {
  if (!_itemAwaitsApproval(item)) return;
  if (typeof threadView.addNoticeBar !== 'function') return;

  const followup = (item && typeof item.approval_followup === 'object' && item.approval_followup) || {};
  const cfg = _approvalUrgencyConfig(followup);

  const waitMinutes = Number(
    item.approval_wait_minutes != null ? item.approval_wait_minutes : followup.wait_minutes,
  ) || 0;
  const approvers = _formatApprovers(
    item.approval_pending_assignees || followup.pending_assignees,
  );

  // Headline reads as a single sentence: "[Waiting] 2h 15m — Awaiting Mo, Sarah".
  // If we have neither a wait time nor an approver, the banner adds no
  // information beyond the state banner, so suppress it.
  if (waitMinutes <= 0 && !approvers) return;

  const headlineParts = [];
  if (waitMinutes > 0) headlineParts.push(_humanizeWaitMinutes(waitMinutes));
  if (approvers) headlineParts.push(`Awaiting ${approvers}`);
  const headline = headlineParts.join(' — ');

  const el = document.createElement('div');
  el.style.cssText = `
    display:flex; align-items:center; gap:12px; padding:10px 16px;
    background:${cfg.bg}; border-left:3px solid ${cfg.border};
    font-family:Inter,-apple-system,system-ui,sans-serif; font-size:13px; color:${cfg.text};
  `;

  const left = document.createElement('div');
  left.style.cssText = 'flex:1; display:flex; align-items:center; gap:8px;';
  const pill = document.createElement('span');
  pill.style.cssText = `
    font-size:11px; font-weight:600; padding:2px 10px; border-radius:999px;
    background:${cfg.border}20; color:${cfg.text}; text-transform:uppercase; letter-spacing:0.02em;
  `;
  pill.textContent = cfg.label;
  left.appendChild(pill);
  const text = document.createElement('span');
  text.style.cssText = 'font-weight:500;';
  text.textContent = headline;
  left.appendChild(text);
  el.appendChild(left);

  if (item?.id) {
    const detailsBtn = document.createElement('button');
    detailsBtn.textContent = 'View details';
    detailsBtn.style.cssText = `
      align-self:center; border:1px solid ${cfg.border}; border-radius:6px;
      padding:5px 14px; font-size:12px; font-weight:600; cursor:pointer;
      background:transparent; color:${cfg.text}; font-family:inherit;
    `;
    detailsBtn.addEventListener('click', () => {
      openItemInPipeline(item, 'thread_approval_banner');
    });
    el.appendChild(detailsBtn);
  }

  threadView.addNoticeBar({ el });
}

function registerThreadRowLabels() {
  if (!sdk?.Lists || typeof sdk.Lists.registerThreadRowViewHandler !== 'function') return;

  // §4.07 inbox-labels budget: measure from the moment the inbox list
  // hands us a row to the moment we commit a label on it. The budget
  // applies to the happy case (user opens inbox, labels appear before
  // they finish reading subjects) so we only measure the very first
  // decorated row per session — per-row marks would flood telemetry
  // and dilute the signal.
  let firstRowPerfFired = false;

  sdk.Lists.registerThreadRowViewHandler((threadRowView) => {
    const getId = async () => {
      if (typeof threadRowView.getThreadIDAsync === 'function') {
        return await threadRowView.getThreadIDAsync();
      }
      return null;
    };

    if (!firstRowPerfFired) perfMarkStart('inbox_labels');

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
          if (!firstRowPerfFired) {
            firstRowPerfFired = true;
            perfMarkDone('inbox_labels', { context: { thread_id: threadId } });
          }
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

    // Update cached exception count for nav badge (§6.2)
    const newExceptionCount = failedPost + needsInfo;
    if (newExceptionCount !== _cachedExceptionCount) {
      _cachedExceptionCount = newExceptionCount;
      rebuildMenuNavigation().catch(() => {});
    }

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

// ==================== THREAD TOOLBAR BUTTONS (Phase 3.3 — §6.5) ====================

async function _hydrateErpRuntimeConfig(qm) {
  // Reads the workspace bootstrap once and stamps erpType +
  // erpDeepLinkId onto queueManager.runtimeConfig so the thread
  // toolbar ERP button can build deep links without per-invoice
  // refetches. Failure is non-fatal — the button simply falls back
  // to showing the raw ERP reference as a toast.
  const rc = qm?.runtimeConfig;
  if (!rc || !rc.backendUrl) return;
  try {
    const orgId = rc.organizationId || 'default';
    const url = `${String(rc.backendUrl).replace(/\/+$/, '')}/api/workspace/bootstrap?organization_id=${encodeURIComponent(orgId)}`;
    const payload = await qm.backendFetch(url);
    if (!payload || typeof payload !== 'object') return;

    // bootstrap.integrations may be an array (admin console shape) or
    // a keyed object depending on endpoint version. Handle both.
    const integrations = payload.integrations;
    let erpEntry = null;
    if (Array.isArray(integrations)) {
      erpEntry = integrations.find((i) => i?.name === 'erp') || null;
    } else if (integrations && typeof integrations === 'object') {
      erpEntry = integrations.erp || null;
    }
    if (!erpEntry || !erpEntry.connected) return;
    const connections = Array.isArray(erpEntry.connections) ? erpEntry.connections : [];
    const active = connections.find((c) => c?.is_active) || connections[0] || null;
    if (!active) return;
    rc.erpType = String(active.erp_type || '').toLowerCase() || rc.erpType || '';
    rc.erpDeepLinkId = String(active.deep_link_id || '').trim() || null;
  } catch (_) { /* non-fatal */ }
}


function registerThreadToolbarButtons() {
  if (!sdk?.Toolbars || typeof sdk.Toolbars.registerThreadButton !== 'function') {
    console.warn('[Clearledgr] sdk.Toolbars.registerThreadButton not available — skipping thread toolbar');
    return;
  }

  // Review Exception button — visible when the thread has an AP item
  // with an exception (needs_info, failed_post, etc.). Opens the item
  // in the full pipeline view where the user can resolve it.
  //
  // An Approve button previously lived here for needs_approval items
  // but was removed: Gmail is the work surface, Slack is the decision
  // surface (DESIGN_THESIS.md §6.3). Approvals route to Slack.
  sdk.Toolbars.registerThreadButton({
    title: 'Review exception',
    positions: ['THREAD'],
    threadSection: 'METADATA_STATE',
    orderHint: 2,
    onClick: async (event) => {
      const threadViews = event.selectedThreadViews || [];
      if (!threadViews.length) return;
      const threadView = threadViews[0];
      let threadId = null;
      try {
        threadId = typeof threadView.getThreadIDAsync === 'function'
          ? await threadView.getThreadIDAsync()
          : null;
      } catch (_) { return; }
      if (!threadId) return;

      const item = store.findItemByThreadId(threadId);
      if (!item?.id) {
        showToast('No invoice found for this thread', 'error');
        return;
      }
      openItemInPipeline(item, 'thread_toolbar');
    },
  });

  // 3. ERP link button — opens the invoice in the connected ERP system.
  //    Thesis §6.5: "NetSuite ↗" — reflects actual connected ERP name.
  const erpDisplayNames = {
    quickbooks: 'QuickBooks',
    xero: 'Xero',
    netsuite: 'NetSuite',
    sap: 'SAP',
  };
  const connectedErpType = String(queueManager?.runtimeConfig?.erpType || '').toLowerCase();
  const erpButtonTitle = erpDisplayNames[connectedErpType]
    ? `${erpDisplayNames[connectedErpType]} ↗`
    : 'Open in ERP ↗';

  sdk.Toolbars.registerThreadButton({
    title: erpButtonTitle,
    positions: ['THREAD'],
    threadSection: 'OTHER',
    orderHint: 3,
    onClick: async (event) => {
      const threadViews = event.selectedThreadViews || [];
      if (!threadViews.length) return;
      const threadView = threadViews[0];
      let threadId = null;
      try {
        threadId = typeof threadView.getThreadIDAsync === 'function'
          ? await threadView.getThreadIDAsync()
          : null;
      } catch (_) { return; }
      if (!threadId) return;

      const item = store.findItemByThreadId(threadId);
      if (!item?.id) {
        showToast('No invoice found for this thread', 'error');
        return;
      }

      const erpRef = item.erp_reference || item.erp_reference_id || '';
      const erpType = String(item.erp_type || '').toLowerCase();
      if (!erpRef) {
        showToast('No ERP reference — invoice has not been posted yet', 'error');
        return;
      }

      // The deep-link id (QB realm_id / Xero tenant_id / NetSuite
      // account_id / SAP base_url) comes from the bootstrap
      // integrations payload. Fall back to per-item erp_realm_id
      // when present for older item records.
      const deepLinkId = String(
        queueManager?.runtimeConfig?.erpDeepLinkId
        || item.erp_realm_id
        || item.erp_account_id
        || ''
      ).trim();

      // Build ERP-specific deep link.
      let erpUrl = null;
      if (erpType === 'quickbooks') {
        // Intuit's bill-detail URL doesn't require the realm_id in the
        // path (the signed-in QB session resolves tenancy), but
        // requiring deep_link_id means we only link when we're confident
        // the user is on the expected company.
        if (deepLinkId) {
          erpUrl = `https://app.qbo.intuit.com/app/bill?txnId=${encodeURIComponent(erpRef)}`;
        }
      } else if (erpType === 'xero') {
        erpUrl = `https://go.xero.com/AccountsPayable/View.aspx?InvoiceID=${encodeURIComponent(erpRef)}`;
      } else if (erpType === 'netsuite' && deepLinkId) {
        // NetSuite account_id becomes the subdomain. Underscores in
        // sandbox ids (e.g. 1234567_SB1) must become hyphens in the
        // URL host ("1234567-sb1.app.netsuite.com").
        const host = deepLinkId.toLowerCase().replace(/_/g, '-');
        erpUrl = `https://${host}.app.netsuite.com/app/accounting/transactions/vendbill.nl?id=${encodeURIComponent(erpRef)}`;
      } else if (erpType === 'sap' && deepLinkId) {
        // SAP S/4HANA Cloud public API uses SupplierInvoice as the
        // entity path; on-prem deployments expose the same Fiori app
        // tile. deepLinkId is the customer-configured base URL.
        const base = deepLinkId.replace(/\/+$/, '');
        erpUrl = `${base}/ui#SupplierInvoice-displayFactSheet?SupplierInvoice=${encodeURIComponent(erpRef)}`;
      }

      if (erpUrl) {
        window.open(erpUrl, '_blank', 'noopener');
      } else {
        // Deep link unavailable (no deep_link_id, or ERP shape we
        // don't support yet) — surface the reference so the user can
        // copy it into their ERP manually.
        showToast(`ERP reference: ${erpRef}`, 'success');
      }
    },
  });
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

    // G then H → Go to Clearledgr Home
    const goHomeView = sdk.Keyboard.createShortcutHandle({
      chord: 'g h',
      description: 'Go to Clearledgr Home',
    });
    goHomeView.on('activate', () => sdk.Router.goto('clearledgr/home'));

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

  // §6.5 — fetch ERP connection info once so the thread-toolbar ERP
  // button has the deep-link identifier needed to build a working URL
  // (QuickBooks realm_id / Xero tenant_id / NetSuite account_id / SAP
  // base_url). Fire-and-forget is acceptable here because the button's
  // onClick re-reads runtimeConfig at click time — if the fetch hasn't
  // landed by first click, the click falls back to the toast path and
  // the next click works.
  _hydrateErpRuntimeConfig(queueManager).catch(() => {});

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
  registerThreadToolbarButtons();
  registerInboxHeadsUp();
  registerKeyboardShortcuts();
  registerSearchSuggestions();
  watchForSettingsPage(queueManager);

  // §6.2 "Show in Inbox" — add saved view sections to the Gmail inbox
  registerInboxSavedViewSections();

  // Register full-page routes inside Gmail (Streak pattern)
  registerAppMenuAndRoutes();
}

// ==================== §15 STREAK-STYLE ONBOARDING ====================

function _showOnboardingFlow(bootstrapData, oauthBridgeRef) {
  // Mount the onboarding modal as a Preact component into a container on the page
  const existing = document.getElementById('cl-onboarding-root');
  if (existing) return; // Already showing

  const container = document.createElement('div');
  container.id = 'cl-onboarding-root';
  document.body.appendChild(container);

  const backendUrl = String(
    queueManager?.runtimeConfig?.backendUrl || 'https://api.clearledgr.com'
  ).replace(/\/+$/, '');

  const api = async (path, options = {}) => {
    const fullUrl = `${backendUrl}${path}`;
    const result = await queueManager.backendFetch(fullUrl, {
      method: options.method || 'GET',
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      body: options.body || undefined,
    });
    if (!result || !result.ok) throw new Error(`HTTP ${result?.status || 'unknown'}`);
    if (result.status === 204) return {};
    return result.json();
  };

  const onComplete = () => {
    container.remove();
    try { sdk.Router.goto('clearledgr/home'); } catch (_) {}
    try { queueManager.refreshQueue(); } catch (_) {}
  };

  // User dismissed the modal ("Don't use Clearledgr on this account"). Close
  // it and remember the choice for this Gmail account so we don't re-prompt
  // on every page load. User can reopen from the sidebar "Connect Gmail" CTA.
  const onDismiss = () => {
    try {
      const email = String(
        queueManager?.runtimeConfig?.userEmail
        || sdk?.User?.getEmailAddress?.()
        || ''
      ).trim().toLowerCase();
      if (email && typeof chrome !== 'undefined' && chrome.storage?.local) {
        chrome.storage.local.set({
          [`clearledgr_onboarding_dismissed_${email}`]: Date.now(),
        });
      }
    } catch (_) { /* dismissal is best-effort */ }
    container.remove();
  };

  // Native extension auth: getAuthToken → register with backend → Bearer token.
  // This is the same path queueManager.backendFetch expects, so the ERP picker
  // call that follows will have a valid credential.
  const signIn = async () => {
    const result = await queueManager.authorizeGmailNow();
    if (!result?.success) {
      throw new Error(result?.error || 'sign_in_failed');
    }
    return result;
  };

  render(
    html`<${OnboardingFlow}
      api=${api}
      onComplete=${onComplete}
      onDismiss=${onDismiss}
      oauthBridge=${oauthBridgeRef}
      backendUrl=${backendUrl}
      signIn=${signIn}
    />`,
    container,
  );
}

// ==================== §6.2 INBOX SAVED VIEW SECTIONS ====================

function registerInboxSavedViewSections() {
  // "Saved Views can be set to 'Show in Inbox' — this surfaces the
  // filtered Box list as a labelled section at the top of the Gmail inbox."
  if (!sdk?.Router || typeof sdk.Router.handleListRoute !== 'function') return;

  sdk.Router.handleListRoute(sdk.Router.NativeRouteIDs.INBOX, (listRouteView) => {
    const items = store.queue || [];

    // Exceptions section — Match Status = Exception or Failed
    const exceptionItems = items.filter((i) => {
      const state = String(i.state || '').toLowerCase();
      return ['needs_info', 'failed_post', 'reversed'].includes(state);
    });
    if (exceptionItems.length > 0) {
      listRouteView.addSection({
        title: `Exceptions (${exceptionItems.length})`,
        subtitle: 'Invoices requiring human resolution',
        tableRows: exceptionItems.slice(0, 5).map((item) => ({
          title: item.vendor_name || item.vendor || 'Unknown',
          body: `${item.currency || ''} ${Number(item.amount || 0).toLocaleString()} — ${(item.exception_reason || item.exception_code || item.state || '').replace(/_/g, ' ')}`,
          shortDetailText: item.invoice_number || '',
          isRead: false,
          routeID: 'clearledgr/invoices',
        })),
      });
    }

    // Awaiting Approval section
    const approvalItems = items.filter((i) => {
      const state = String(i.state || '').toLowerCase();
      return ['needs_approval', 'pending_approval'].includes(state);
    });
    if (approvalItems.length > 0) {
      listRouteView.addSection({
        title: `Awaiting Approval (${approvalItems.length})`,
        subtitle: 'Invoices routed for human approval',
        tableRows: approvalItems.slice(0, 5).map((item) => ({
          title: item.vendor_name || item.vendor || 'Unknown',
          body: `${item.currency || ''} ${Number(item.amount || 0).toLocaleString()}`,
          shortDetailText: item.invoice_number || '',
          isRead: false,
          routeID: 'clearledgr/invoices',
        })),
      });
    }

    // Due This Week section
    const now = new Date();
    const fiveDays = new Date(now.getTime() + 5 * 86400000);
    const dueItems = items.filter((i) => {
      if (!i.due_date) return false;
      const state = String(i.state || '').toLowerCase();
      if (['closed', 'rejected'].includes(state)) return false;
      try {
        const due = new Date(i.due_date);
        return due <= fiveDays;
      } catch { return false; }
    });
    if (dueItems.length > 0) {
      listRouteView.addSection({
        title: `Due This Week (${dueItems.length})`,
        subtitle: 'Invoices due within 5 days',
        tableRows: dueItems.slice(0, 5).map((item) => ({
          title: item.vendor_name || item.vendor || 'Unknown',
          body: `${item.currency || ''} ${Number(item.amount || 0).toLocaleString()} — due ${item.due_date?.slice(0, 10) || ''}`,
          shortDetailText: item.invoice_number || '',
          isRead: true,
          routeID: 'clearledgr/invoices',
        })),
      });
    }
  });
}

// ==================== GMAIL-NATIVE ROUTES (Streak pattern) ====================

function registerAppMenuAndRoutes() {
  const PAGE_MAP = {
    'clearledgr/home': HomePage,
    'clearledgr/review': ReviewPage,
    'clearledgr/upcoming': UpcomingPage,
    'clearledgr/invoices': PipelinePage,
    'clearledgr/activity': ActivityPage,
    'clearledgr/exceptions': ExceptionsPage,
    'clearledgr/vendor-onboarding': VendorOnboardingPage,
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

    if (PAGE_MAP[normalized] || LEGACY_PAGE_MAP[normalized]) {
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

  function renderAppMenuPanelChrome({ primaryRoutes = [], savedViews = [], settingsRoutes = [] } = {}) {
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

        if (row.badge) {
          const badge = document.createElement('span');
          badge.className = 'cl-appmenu-panel-view-badge';
          badge.textContent = row.badge;
          button.appendChild(badge);
        }

        button.addEventListener('click', () => {
          row.onClick?.();
        });
        list.appendChild(button);
      });

      shell.appendChild(section);
    };

    // §6.2 — primary nav: Home, AP Invoices (with badge), Vendor Onboarding, Agent Activity
    // §15 — leading "Finish setup (N)" checklist entry when
    // onboarding is incomplete. The sentinel id 'clearledgr/setup'
    // routes the click to _showOnboardingFlow instead of a real
    // route so the modal walks the admin through remaining steps.
    const exceptionCount = _cachedExceptionCount ?? 0;
    renderSection(null, primaryRoutes.map((route) => {
      if (route.isOnboardingChecklist) {
        return {
          name: route.title,
          description: route.description || 'Finish installing Clearledgr.',
          iconText: '⚙',
          active: false,
          onClick: () => {
            if (bootstrapCache) _showOnboardingFlow(bootstrapCache, oauthBridge);
          },
        };
      }
      const row = {
        name: route.title,
        iconUrl: getRouteIconUrl(route),
        active: currentHash === normalizeClearledgrHash(route.id),
        onClick: () => navigateInboxRoute(route.id, sdk),
      };
      if (route.id === 'clearledgr/invoices' && exceptionCount > 0) {
        row.badge = String(exceptionCount);
      }
      return row;
    }));

    // §6.2 — saved views: thesis defaults + user-pinned
    const viewRows = (Array.isArray(savedViews) ? savedViews : []).map((view) => {
      const viewHash = buildClearledgrRouteHash(view.id, view.routeParams || undefined);
      return {
        name: String(view?.name || view?.title || 'Saved view'),
        description: String(view?.description || ''),
        iconText: '▸',
        active: Boolean(viewHash && currentHash === viewHash),
        onClick: () => navigateInboxRoute(view.id, sdk, view.routeParams || undefined),
      };
    });
    renderSection('Saved Views', viewRows, {
      trailingActionLabel: '+',
      trailingActionAriaLabel: 'Save current view',
      onTrailingAction: saveCurrentPipelineView,
    });

    renderSection('Settings', settingsRoutes.map((route) => ({
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
    const primaryRoutes = menuRoutes.filter((route) => APPMENU_PRIMARY_ROUTE_IDS.has(route.id));

    // §15 Onboarding Design Rule: "The onboarding checklist lives
    // in the Clearledgr nav section until all four steps are
    // complete." When bootstrap reports incomplete steps, prepend a
    // "Finish setup (N)" entry to primaryRoutes — the number is the
    // count of outstanding step-relevant required_actions. Clicking
    // it opens the OnboardingFlow modal so the admin can pick up
    // where they left off. Disappears automatically once every step
    // is done (count drops to zero).
    const ONBOARDING_REQUIRED_ACTION_CODES = new Set([
      'connect_gmail',
      'reconnect_gmail',
      'connect_erp',
      'configure_ap_policy',
      'connect_slack',
      'set_slack_channel',
    ]);
    const bootstrapRequiredActions = Array.isArray(bootstrapCache?.required_actions)
      ? bootstrapCache.required_actions
      : [];
    const outstandingOnboardingActions = bootstrapRequiredActions
      .filter((a) => ONBOARDING_REQUIRED_ACTION_CODES.has(String(a?.code || '')));
    const onboardingCompleted = Boolean(bootstrapCache?.onboarding?.completed);
    if (!onboardingCompleted && outstandingOnboardingActions.length > 0) {
      primaryRoutes.unshift({
        id: 'clearledgr/setup',
        title: `Finish setup (${outstandingOnboardingActions.length})`,
        description: outstandingOnboardingActions
          .map((a) => a.message)
          .filter(Boolean)
          .join(' · '),
        // Not a real route — onClick opens the OnboardingFlow modal.
        // Handled in renderAppMenuPanelChrome + the fallback nav
        // below, both of which check for this sentinel id.
        isOnboardingChecklist: true,
      });
    }

    // §6.2 — three thesis-required saved views (hardcoded fallback)
    const thesisSavedViewsFallback = [
      {
        name: 'Exceptions',
        description: 'Invoices with Match Status = Exception or Failed.',
        id: 'clearledgr/invoices-view/:ref',
        routeParams: { ref: 'thesis:exceptions' },
      },
      {
        name: 'Awaiting Approval',
        description: 'Invoices routed for approval but not yet actioned.',
        id: 'clearledgr/invoices-view/:ref',
        routeParams: { ref: 'thesis:awaiting_approval' },
      },
      {
        name: 'Due This Week',
        description: 'Invoices due within 5 days.',
        id: 'clearledgr/invoices-view/:ref',
        routeParams: { ref: 'thesis:due_this_week' },
      },
    ];

    // §5.1 — fetch saved views from API, fall back to hardcoded thesis defaults
    let apiSavedViews = [];
    try {
      const orgId = queueManager?.runtimeConfig?.organizationId || 'default';
      const backendUrl = queueManager?.runtimeConfig?.backendUrl || '';
      if (backendUrl && queueManager?.backendFetch) {
        const resp = await queueManager.backendFetch(
          `${backendUrl}/api/saved-views?organization_id=${encodeURIComponent(orgId)}&pipeline=ap-invoices`,
        );
        const views = resp?.saved_views || [];
        apiSavedViews = views.map((v) => ({
          name: v.name,
          description: v.filter_json ? `Filter: ${JSON.stringify(v.filter_json)}` : '',
          id: 'clearledgr/invoices-view/:ref',
          routeParams: { ref: `saved:${v.id}` },
        }));
      }
    } catch (_) { /* non-fatal — use fallback */ }

    const thesisSavedViews = apiSavedViews.length > 0 ? apiSavedViews : thesisSavedViewsFallback;

    // Append any user-pinned views after the thesis/API views
    const pipelineScope = {
      orgId: queueManager?.runtimeConfig?.organizationId || 'default',
      userEmail: sdk?.User?.getEmailAddress?.() || queueManager?.runtimeConfig?.userEmail || '',
    };
    const userPinnedViews = getPinnedPipelineViews(readPipelinePreferences(pipelineScope))
      .slice(0, 3)
      .filter((view) => !thesisSavedViews.some((sv) => sv.name === view.name))
      .map((view) => ({
        name: view.name,
        description: view.description || 'Pinned AP queue view.',
        id: 'clearledgr/invoices-view/:ref',
        routeParams: { ref: getPipelineViewRef(view) },
      }));
    const allSavedViews = [...thesisSavedViews, ...userPinnedViews];

    clearNavItemViews(appMenuNavItemViews);
    clearNavItemViews(fallbackNavItemViews);

    if (appMenuPanelView && typeof appMenuPanelView.addNavItem === 'function') {
      renderAppMenuPanelChrome({
        primaryRoutes,
        savedViews: allSavedViews,
        settingsRoutes: menuRoutes.filter((route) => APPMENU_SETTINGS_ROUTE_IDS.has(route.id)),
      });
      return;
    }

    // Fallback only if AppMenu panel genuinely failed (not just slow)
    if (sdk.NavMenu && typeof sdk.NavMenu.addNavItem === 'function') {
      // §15 — prepend the setup-checklist entry if there are
      // outstanding onboarding actions. Same sentinel as the panel
      // path; click opens the OnboardingFlow modal.
      if (!onboardingCompleted && outstandingOnboardingActions.length > 0) {
        try {
          const setupNav = sdk.NavMenu.addNavItem({
            name: `Finish setup (${outstandingOnboardingActions.length})`,
            type: 'NAVIGATION',
          });
          if (setupNav && typeof setupNav.on === 'function') {
            setupNav.on('click', (event) => {
              if (event && typeof event.preventDefault === 'function') event.preventDefault();
              if (bootstrapCache) _showOnboardingFlow(bootstrapCache, oauthBridge);
            });
          }
          fallbackNavItemViews.push(setupNav);
        } catch (_) { /* fall through — NavMenu version doesn't support on-click */ }
      }
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
  const oauthBridge = createOAuthBridge((result) => {
    // Any OAuth completion (success, failure, or popup-closed-early)
    // invalidates our cached state — the server-side connection table
    // changed, or the user might have picked a different identity.
    bootstrapCache = null;
    queueManager?.scanNow?.();
    const refreshed = getBootstrap();

    if (!result) return;
    const integration = String(result.integration || '').trim().toLowerCase();
    if (result.success === false) {
      const label = integration
        ? integration.charAt(0).toUpperCase() + integration.slice(1)
        : 'Integration';
      const reason = result.detail ? ` (${result.detail})` : '';
      showToast(`${label} connection failed${reason}`, 'error');
      return;
    }

    // ERP connect → refresh ERP connection state + toolbar label so the
    // "Connected as X" chip picks up the new realm/tenant.
    if (integration === 'quickbooks' || integration === 'xero'
        || integration === 'netsuite' || integration === 'sap'
        || integration.startsWith('erp-')) {
      // Bootstrap refresh picks up the new connection. The toolbar's
      // "Connected as X" chip reads from bootstrap state, so it updates
      // on the next render cycle.
      void refreshed;
      const label = integration.replace(/^erp-/, '');
      const pretty = label ? label.charAt(0).toUpperCase() + label.slice(1) : 'ERP';
      showToast(`${pretty} connected`, 'success');
      return;
    }

    if (integration === 'gmail' || integration === 'google') {
      showToast('Google connected', 'success');
      return;
    }

    if (integration === 'slack') {
      showToast('Slack connected', 'success');
    }
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
      // §15 — also rebuild when the onboarding-checklist signature
      // changes. Without this the "Finish setup (N)" badge would
      // stick to its pre-refresh count after the admin finishes a
      // step, giving the impression nothing happened.
      const ONBOARDING_REQUIRED_ACTION_CODES = new Set([
        'connect_gmail', 'reconnect_gmail', 'connect_erp',
        'configure_ap_policy', 'connect_slack', 'set_slack_channel',
      ]);
      const outstandingOnboarding = (Array.isArray(data?.required_actions) ? data.required_actions : [])
        .filter((a) => ONBOARDING_REQUIRED_ACTION_CODES.has(String(a?.code || '')))
        .length;
      const onboardingSignature = `${Boolean(data?.onboarding?.completed)}:${outstandingOnboarding}`;
      const prevOnboardingSignature = store.__onboardingNavSig || null;
      store.__onboardingNavSig = onboardingSignature;

      if (
        !hadResolvedRouteAccess
        || appMenuNavItemViews.length === 0
        || JSON.stringify(nextRouteAccess.capabilities) !== JSON.stringify(currentRouteAccess.capabilities)
        || onboardingSignature !== prevOnboardingSignature
      ) {
        currentRouteAccess = nextRouteAccess;
        rebuildMenuNavigation();
      }
      // §15: First install — show Streak-style onboarding modal.
      // The flow needs the same oauthBridge instance the rest of the
      // sidebar uses so its postMessage listener and popup-close
      // poller are coordinated.
      // Only pop the OnboardingFlow modal on a true cold start
      // (step === 0). If the user has Gmail + extension installed —
      // which by definition is true here, since this code is running
      // inside the extension — the bootstrap derives step >= 1 and
      // the user can finish remaining steps from the /home page
      // instead of having a modal slammed in their face every refresh.
      const onboardingStep = Number(data?.onboarding?.step || 0);
      if (data?.onboarding && !data.onboarding.completed && onboardingStep === 0) {
        // Respect the user's "Don't use Clearledgr on this account"
        // dismissal — don't re-prompt on every page load.
        const emailForDismiss = String(
          queueManager?.runtimeConfig?.userEmail
          || sdk?.User?.getEmailAddress?.()
          || ''
        ).trim().toLowerCase();
        const dismissKey = emailForDismiss
          ? `clearledgr_onboarding_dismissed_${emailForDismiss}`
          : null;
        const checkAndMount = () => {
          if (!dismissKey || typeof chrome === 'undefined' || !chrome.storage?.local) {
            _showOnboardingFlow(data, oauthBridge);
            return;
          }
          chrome.storage.local.get([dismissKey], (stored) => {
            if (!stored?.[dismissKey]) {
              _showOnboardingFlow(data, oauthBridge);
            }
          });
        };
        checkAndMount();
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
    const decodedRef = decodeURIComponent(rawRef);

    // §6.2 thesis-defined saved views — resolve to pipeline slice directly
    const THESIS_VIEW_SNAPSHOTS = {
      'thesis:exceptions': { activeSliceId: 'blocked_exception', viewMode: 'table', sortCol: 'due_date', sortDir: 'asc' },
      'thesis:awaiting_approval': { activeSliceId: 'waiting_on_approval', viewMode: 'table', sortCol: 'due_date', sortDir: 'asc' },
      'thesis:due_this_week': { activeSliceId: 'due_soon', viewMode: 'table', sortCol: 'due_date', sortDir: 'asc' },
    };
    const thesisSnapshot = THESIS_VIEW_SNAPSHOTS[decodedRef];
    if (thesisSnapshot) {
      clearPipelineNavigation(pipelineScope);
      writePipelinePreferences(pipelineScope, thesisSnapshot);
    } else {
      const targetView = resolvePipelineViewByRef(prefs, decodedRef);
      if (targetView?.snapshot) {
        clearPipelineNavigation(pipelineScope);
        writePipelinePreferences(pipelineScope, targetView.snapshot);
      }
    }
    sdk.Router.goto('clearledgr/invoices');
    try {
      customRouteView.destroy?.();
    } catch (_) { /* best effort */ }
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
  window.addEventListener('hashchange', (event) => {
    const currentClearledgrHash = normalizeClearledgrHash(window.location.hash);
    // If the user navigated FROM a clearledgr route TO a non-clearledgr
    // route (e.g., back to #inbox), they're explicitly leaving our
    // surface. Clear any pending-direct-route marker so we don't bounce
    // them back on the next tick. The pending marker exists for
    // first-install deep-linking; it should not override live nav.
    const prevHash = normalizeClearledgrHash(String(event?.oldURL || '').split('#')[1] || '');
    if (!currentClearledgrHash && prevHash) {
      void clearPendingDirectHashRoute();
      lastDirectHashRoute = '';
    }
    if (currentClearledgrHash) {
      lastActiveClearledgrRoute = currentClearledgrHash;
    } else {
      lastKnownMailboxDocumentTitle = String(document.title || '').trim() || lastKnownMailboxDocumentTitle;
    }
    rebuildMenuNavigation();
    window.setTimeout(async () => {
      const restored = await maybeRestoreReloadedClearledgrRoute();
      if (!restored) {
        // Only try to sync a pending direct route if the user is NOT
        // already on a native Gmail route. If they just clicked #inbox,
        // respect it — don't second-guess them.
        const nowHash = String(window.location.hash || '').trim();
        if (nowHash.startsWith('#clearledgr/') || !nowHash || nowHash === '#') {
          await syncDirectHashRoute();
        }
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
