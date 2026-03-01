#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const { createInboxSdkIntegrationRuntime } = require('../tests/inboxsdk-integration-harness.cjs');

const DEFAULT_RELEASE_ID = 'ap-v1-2026-02-25-pilot-rc1';
const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8000';

function parseArgs(argv = process.argv.slice(2)) {
  const options = {
    releaseId: DEFAULT_RELEASE_ID,
    backendUrl: DEFAULT_BACKEND_URL,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const token = String(argv[index] || '').trim();
    const next = argv[index + 1];
    if (token === '--release-id' && next) {
      options.releaseId = String(next).trim();
      index += 1;
      continue;
    }
    if (token === '--backend-url' && next) {
      options.backendUrl = String(next).trim().replace(/\/+$/, '');
      index += 1;
    }
  }
  return options;
}

function repoRoot() {
  return path.resolve(__dirname, '..', '..', '..');
}

function artifactDirForRelease(releaseId) {
  return path.join(repoRoot(), 'docs', 'ga-evidence', 'releases', releaseId, 'artifacts');
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

async function flushUntil(runtime, predicate, attempts = 8) {
  for (let index = 0; index < attempts; index += 1) {
    await runtime.flush();
    if (predicate()) return true;
  }
  return false;
}

async function renderThread(runtime, threadId) {
  const threadHandler = runtime.records.sdkHandlers.threadView;
  if (typeof threadHandler !== 'function') {
    throw new Error('thread_view_handler_missing');
  }
  threadHandler(runtime.createThreadView(threadId));
  runtime.api.renderAllSidebars();
  await runtime.flush();
  await runtime.flush();
}

function emitSingleQueueItem(runtime, item, context = null) {
  const queueManager = runtime.getQueueManager();
  const contexts = new Map();
  if (item?.id && context) {
    contexts.set(item.id, context);
  }
  queueManager.emitQueueUpdated([item], { state: 'idle' }, new Map(), [], new Map(), new Map(), contexts);
}

function wrapSidebarHtml(innerHtml) {
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Clearledgr UI Evidence</title>
    <style>
      body {
        margin: 0;
        background: #f3f4f6;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        display: flex;
        justify-content: center;
        padding: 24px;
      }
      #frame {
        width: 420px;
      }
    </style>
  </head>
  <body>
    <div id="frame">${innerHtml}</div>
  </body>
</html>`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function composeSidebarSnapshotHtml(sidebar, { forceDialogOpen = false } = {}) {
  let html = String(sidebar?.innerHTML || '');
  const threadContext = String(sidebar?.querySelector('#cl-thread-context')?.innerHTML || '');
  html = html.replace(
    /<div id="cl-thread-context"><\/div>/,
    `<div id="cl-thread-context">${threadContext}</div>`,
  );

  const scanStatusText = String(sidebar?.querySelector('#cl-scan-status')?.textContent || '').trim();
  if (scanStatusText) {
    html = html.replace(
      /(<div id="cl-scan-status"[^>]*>)[\s\S]*?(<\/div>)/,
      `$1${escapeHtml(scanStatusText)}$2`,
    );
  }

  const dialog = sidebar?.querySelector('#cl-action-dialog');
  if (dialog) {
    const titleText = String(dialog.querySelector('.cl-action-dialog-title')?.textContent || '').trim();
    const labelText = String(dialog.querySelector('.cl-action-dialog-label')?.textContent || '').trim();
    const hintText = String(dialog.querySelector('.cl-action-dialog-hint')?.textContent || '').trim();
    const chipsHtml = String(dialog.querySelector('.cl-action-dialog-chips')?.innerHTML || '');
    const inputValue = String(dialog.querySelector('.cl-action-dialog-input')?.value || '');
    const shouldOpen = forceDialogOpen || String(dialog.style?.display || '').toLowerCase() === 'flex';

    html = html.replace(
      /(<div id="cl-action-dialog" class="cl-action-dialog")([^>]*?)>/,
      shouldOpen
        ? '$1$2 style="display:flex" aria-hidden="false">'
        : '$1$2 aria-hidden="true">',
    );
    if (titleText) {
      html = html.replace(
        /(<div class="cl-action-dialog-title" id="cl-action-dialog-title">)[\s\S]*?(<\/div>)/,
        `$1${escapeHtml(titleText)}$2`,
      );
    }
    if (labelText) {
      html = html.replace(
        /(<label class="cl-action-dialog-label" id="cl-action-dialog-label" for="cl-action-dialog-input">)[\s\S]*?(<\/label>)/,
        `$1${escapeHtml(labelText)}$2`,
      );
    }
    html = html.replace(
      /(<div class="cl-action-dialog-chips">)[\s\S]*?(<\/div>)/,
      `$1${chipsHtml}$2`,
    );
    if (hintText) {
      html = html.replace(
        /(<div class="cl-action-dialog-hint" id="cl-action-dialog-hint">)[\s\S]*?(<\/div>)/,
        `$1${escapeHtml(hintText)}$2`,
      );
    }
    html = html.replace(
      /(<input id="cl-action-dialog-input" class="cl-action-dialog-input" type="text" aria-labelledby="cl-action-dialog-label")(.*?)\/>/,
      `$1 value="${escapeHtml(inputValue)}"$2/>`,
    );
  }

  return html;
}

function openAuditSection(html) {
  return String(html || '').replace(
    /<details class="cl-details" aria-label="Audit timeline">/,
    '<details class="cl-details" aria-label="Audit timeline" open>',
  );
}

async function captureSidebarAuditExpanded(browser, outputPath) {
  const runtime = await createInboxSdkIntegrationRuntime({
    queueManager: {
      debugUiEnabled: false,
      async fetchAuditTrail() {
        return [
          {
            id: 'audit-1',
            event_type: 'deterministic_validation_failed',
            operator_title: 'Validation checks failed',
            operator_message: 'Policy requires approval for invoices above $500. PO/GR check failed because goods receipt is missing.',
            ts: '2026-02-28T21:36:00Z',
          },
          {
            id: 'audit-2',
            event_type: 'approval_routed_from_extension',
            operator_title: 'Approval request sent',
            operator_message: 'Sent to approver in Slack or Teams.',
            ts: '2026-02-28T21:36:30Z',
          },
          {
            id: 'audit-3',
            event_type: 'approval_nudge_failed',
            operator_title: 'Approval reminder failed',
            operator_message: 'Could not send reminder to approver. Try "Send reminder" again.',
            ts: '2026-03-01T03:51:00Z',
          },
        ];
      },
    },
  });
  const sidebar = runtime.getState().workSidebarEl || runtime.getState().globalSidebarEl;
  const item = {
    id: 'audit-evidence-1',
    thread_id: 'thread-audit-evidence-1',
    state: 'needs_approval',
    vendor_name: 'Acme Supplies',
    invoice_number: 'INV-1001',
    amount: 842.19,
    currency: 'USD',
    subject: 'Invoice INV-1001',
    sender: 'billing@acme.example',
    confidence: 0.94,
    metadata: {},
  };
  emitSingleQueueItem(runtime, item);
  await renderThread(runtime, item.thread_id);
  await flushUntil(runtime, () => /View audit/i.test(sidebar?.innerHTML || ''));

  const html = openAuditSection(composeSidebarSnapshotHtml(sidebar));
  const page = await browser.newPage({ viewport: { width: 980, height: 1800 } });
  await page.setContent(wrapSidebarHtml(html), { waitUntil: 'domcontentloaded' });
  await page.locator('#frame').screenshot({ path: outputPath });
  await page.close();
}

async function captureSidebarAuthRequired(browser, outputPath) {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const sidebar = runtime.getState().workSidebarEl || runtime.getState().globalSidebarEl;
  const queueManager = runtime.getQueueManager();
  queueManager.emitQueueUpdated([], { state: 'auth_required', message: 'Authorize Gmail to start monitoring.' }, new Map(), [], new Map(), new Map(), new Map());
  await runtime.flush();

  const page = await browser.newPage({ viewport: { width: 980, height: 980 } });
  await page.setContent(wrapSidebarHtml(composeSidebarSnapshotHtml(sidebar)), { waitUntil: 'domcontentloaded' });
  await page.locator('#frame').screenshot({ path: outputPath });
  await page.close();
}

async function captureReasonSheetDialog(browser, outputPath) {
  const runtime = await createInboxSdkIntegrationRuntime({ queueManager: { debugUiEnabled: false } });
  const sidebar = runtime.getState().workSidebarEl || runtime.getState().globalSidebarEl;
  const item = {
    id: 'reason-evidence-1',
    thread_id: 'thread-reason-evidence-1',
    state: 'needs_approval',
    vendor_name: 'Dialog Vendor',
    invoice_number: 'INV-DIALOG-1',
    amount: 315.0,
    currency: 'USD',
    subject: 'Invoice INV-DIALOG-1',
    sender: 'billing@dialog.example',
    confidence: 0.97,
    metadata: {},
  };
  emitSingleQueueItem(runtime, item);
  await renderThread(runtime, item.thread_id);
  const threadContext = sidebar.querySelector('#cl-thread-context');
  const rejectBtn = threadContext?.querySelector('#cl-secondary-reject');
  if (!rejectBtn) throw new Error('reject_button_missing');
  await rejectBtn.click();
  await runtime.flush();
  await runtime.flush();

  const page = await browser.newPage({ viewport: { width: 980, height: 1150 } });
  await page.setContent(
    wrapSidebarHtml(composeSidebarSnapshotHtml(sidebar, { forceDialogOpen: true })),
    { waitUntil: 'domcontentloaded' },
  );
  await page.locator('#frame').screenshot({ path: outputPath });
  await page.close();
}

function json(route, payload, status = 200) {
  return route.fulfill({
    status,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

async function installConsoleMocks(page) {
  const bootstrapPayload = {
    organization: { id: 'default', name: 'Clearledgr' },
    current_user: { id: 'usr-admin-1', email: 'admin@clearledgr.com', role: 'admin' },
    integrations: [
      { name: 'gmail', connected: true, email: 'mo@clearledgr.com', watch_status: 'active', last_sync_at: '2026-03-01T03:16:00Z', invoices_processed: 4 },
      { name: 'slack', connected: true, team_name: 'Clearledgr', approval_channel: '#finance-approvals' },
      { name: 'erp', connected: true, erp_type: 'netsuite', account_id: '1234567_SB1' },
    ],
    dashboard: { total_invoices: 4, failed_posts: 1, pending_approvals: 1 },
    health: { integrations: { gmail: 'ok', slack: 'ok', erp: 'ok' }, required_actions: [] },
  };
  const policyPayload = {
    policy: {
      config_json: {
        amount_threshold: 500,
        require_po: true,
      },
    },
  };
  const recentAudit = {
    events: [
      {
        event_type: 'deterministic_validation_failed',
        operator_title: 'Validation checks failed',
        operator_message: 'Policy requires approval for invoices above $500.',
        created_at: '2026-03-01T02:30:00Z',
      },
    ],
  };
  const tenantHealth = {
    health: {
      queue_lag_seconds: 14,
      scan_lag_seconds: 42,
      posting_success_rate_24h: 0.97,
      approval_latency_minutes_p50: 18,
      top_blockers: ['po_match_no_gr', 'confidence_field_review_required'],
    },
  };
  const kpis = {
    kpis: {
      total_invoices: 4,
      pending_approvals: 1,
      failed_posts: 1,
      posted_to_erp: 2,
    },
  };
  const retryQueue = {
    jobs: [
      { id: 'job-1', ap_item_id: 'inv-1001', status: 'failed_post', attempts: 2, last_error: 'connector_timeout' },
      { id: 'job-2', ap_item_id: 'inv-1002', status: 'failed_post', attempts: 1, last_error: 'temporary_api_error' },
    ],
  };
  const worklist = {
    items: [
      { id: 'inv-1001', thread_id: 'thread-1001', state: 'needs_approval', amount: 842.19, vendor_name: 'Acme Supplies', confidence: 0.94 },
      { id: 'inv-1002', thread_id: 'thread-1002', state: 'failed_post', amount: 420.0, vendor_name: 'Beta Industrial', confidence: 0.98 },
    ],
  };
  const connectorReadiness = {
    connector_readiness: {
      netsuite: { ready: true, reason: 'connected' },
      sap: { ready: false, reason: 'not_connected' },
      quickbooks: { ready: false, reason: 'not_connected' },
      xero: { ready: false, reason: 'not_connected' },
    },
  };
  const learningCalibration = {
    snapshot: {
      org_id: 'default',
      generated_at: '2026-03-01T03:12:00Z',
      total_feedback: 18,
      accepted_rate: 0.83,
      override_rate: 0.11,
    },
  };

  await page.route('**/api/admin/bootstrap**', (route) => json(route, bootstrapPayload));
  await page.route('**/api/admin/policies/ap**', (route) => json(route, policyPayload));
  await page.route('**/api/admin/team/invites**', (route) => json(route, { invites: [] }));
  await page.route('**/api/ap/audit/recent**', (route) => json(route, recentAudit));
  await page.route('**/api/ops/tenant-health**', (route) => json(route, tenantHealth));
  await page.route('**/api/ops/ap-kpis**', (route) => json(route, kpis));
  await page.route('**/api/ops/retry-queue**', (route) => json(route, retryQueue));
  await page.route('**/extension/worklist**', (route) => json(route, worklist));
  await page.route('**/api/admin/ops/connector-readiness**', (route) => json(route, connectorReadiness));
  await page.route('**/api/admin/ops/learning-calibration**', (route) => json(route, learningCalibration));
}

async function captureAdminConsolePages(browser, backendUrl, setupPath, opsPath) {
  const page = await browser.newPage({ viewport: { width: 1680, height: 1080 } });
  await page.addInitScript(() => {
    localStorage.setItem('cl_admin_token', 'evidence-token');
    localStorage.setItem('cl_admin_org', 'default');
  });
  await installConsoleMocks(page);

  await page.goto(`${backendUrl}/console?page=setup&org=default`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#page-content .panel');
  await page.waitForTimeout(200);
  await page.screenshot({ path: setupPath, fullPage: true });

  await page.goto(`${backendUrl}/console?page=ops&org=default`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#page-content .panel');
  await page.waitForTimeout(200);
  await page.screenshot({ path: opsPath, fullPage: true });
  await page.close();
}

async function main() {
  const args = parseArgs();
  const artifactDir = artifactDirForRelease(args.releaseId);
  ensureDir(artifactDir);

  const targets = {
    sidebarAuditExpanded: path.join(artifactDir, 'sidebar-work-audit-expanded.png'),
    sidebarAuthRequired: path.join(artifactDir, 'sidebar-auth-required.png'),
    sidebarReasonSheet: path.join(artifactDir, 'sidebar-reason-sheet.png'),
    adminSetup: path.join(artifactDir, 'admin-console-setup.png'),
    adminOps: path.join(artifactDir, 'admin-console-ops.png'),
  };

  const browser = await chromium.launch({ headless: true });
  try {
    await captureSidebarAuditExpanded(browser, targets.sidebarAuditExpanded);
    await captureSidebarAuthRequired(browser, targets.sidebarAuthRequired);
    await captureReasonSheetDialog(browser, targets.sidebarReasonSheet);
    await captureAdminConsolePages(browser, args.backendUrl, targets.adminSetup, targets.adminOps);
  } finally {
    await browser.close();
  }

  process.stdout.write(`UI hardening evidence captured in ${artifactDir}\n`);
  Object.entries(targets).forEach(([key, value]) => {
    process.stdout.write(`- ${key}: ${value}\n`);
  });
}

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`capture-ui-hardening-evidence failed: ${String(error?.stack || error)}\n`);
    process.exit(1);
  });
}
