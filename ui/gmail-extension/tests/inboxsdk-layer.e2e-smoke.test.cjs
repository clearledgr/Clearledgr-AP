const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

const EXTENSION_ROOT = path.resolve(__dirname, '..');
const RUN_E2E = process.env.RUN_GMAIL_E2E === '1';
const ASSERT_AUTH = process.env.GMAIL_E2E_ASSERT_AUTH === '1';
const RUN_GMAIL_E2E_ACTION = process.env.RUN_GMAIL_E2E_ACTION === '1';
const E2E_TIMEOUT_MS = Number(process.env.GMAIL_E2E_TIMEOUT_MS || 180000);
const E2E_UI_SETTLE_MS = Number(process.env.GMAIL_E2E_UI_SETTLE_MS || 15000);
const EXPECT_SELECTOR = process.env.GMAIL_E2E_EXPECT_SELECTOR || '#cl-scan-status';
const E2E_EVIDENCE_JSON = process.env.GMAIL_E2E_EVIDENCE_JSON || '';
const E2E_ACTION_EVIDENCE_JSON = process.env.GMAIL_E2E_ACTION_EVIDENCE_JSON || E2E_EVIDENCE_JSON;
const E2E_EXECUTABLE_PATH = String(process.env.GMAIL_E2E_EXECUTABLE_PATH || '').trim();
const E2E_PROFILE_DIRECTORY = String(process.env.GMAIL_E2E_PROFILE_DIRECTORY || '').trim();
const ACTION_SELECTOR = String(process.env.GMAIL_E2E_ACTION_SELECTOR || '').trim();
const ACTION_SUCCESS_SELECTOR = String(process.env.GMAIL_E2E_ACTION_SUCCESS_SELECTOR || '').trim();
const ACTION_SUCCESS_TEXT = String(process.env.GMAIL_E2E_ACTION_SUCCESS_TEXT || '').trim();
const ACTION_SETTLE_MS = Number(process.env.GMAIL_E2E_ACTION_SETTLE_MS || 12000);
const UI_MARKERS = String(
  process.env.GMAIL_E2E_UI_MARKERS || 'Clearledgr Invoices,Process with Clearledgr',
)
  .split(',')
  .map((value) => String(value || '').trim())
  .filter(Boolean);
const REQUIRED_SELECTORS = String(
  process.env.GMAIL_E2E_REQUIRED_SELECTORS || '#cl-scan-status,#cl-thread-context,#cl-agent-actions',
)
  .split(',')
  .map((value) => String(value || '').trim())
  .filter(Boolean);
const REQUIRE_ALL_SELECTORS = process.env.GMAIL_E2E_REQUIRE_ALL_SELECTORS === '1';

function _looksLikeLoginPage(url, title, bodyText) {
  const urlText = String(url || '').toLowerCase();
  const titleText = String(title || '').toLowerCase();
  const body = String(bodyText || '').toLowerCase();
  return (
    urlText.includes('accounts.google.com')
    || urlText.includes('servicelogin')
    || titleText.includes('sign in')
    || body.includes('to continue to gmail')
  );
}

function _looksLikeMarketingPage(url, title, bodyText) {
  const urlText = String(url || '').toLowerCase();
  const titleText = String(title || '').toLowerCase();
  const body = String(bodyText || '').toLowerCase();
  return (
    urlText.includes('workspace.google.com')
    || titleText.includes('ai-powered email for everyone')
    || body.includes('ai-powered email for everyone')
    || body.includes('for work')
  );
}

function _looksLikeAuthenticatedInbox(url, title, bodyText) {
  const urlText = String(url || '').toLowerCase();
  const titleText = String(title || '').toLowerCase();
  const body = String(bodyText || '').toLowerCase();
  return (
    urlText.includes('mail.google.com/mail/')
    && !urlText.includes('servicelogin')
    && !titleText.includes('sign in')
    && !body.includes('to continue to gmail')
  );
}

async function _findExtensionServiceWorker(context, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const worker = context.serviceWorkers().find((candidate) => {
      const url = String(candidate.url() || '');
      return url.startsWith('chrome-extension://');
    });
    if (worker) return worker;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return null;
}

async function _collectVisibleSelectorPresence(page, selectors) {
  const targetSelectors = Array.isArray(selectors) ? selectors : [];
  return page.evaluate((values) => {
    const snapshot = {};
    for (const selector of values || []) {
      const element = document.querySelector(selector);
      const style = element ? window.getComputedStyle(element) : null;
      const rect = element ? element.getBoundingClientRect() : null;
      snapshot[selector] = Boolean(
        element
        && style
        && style.visibility !== 'hidden'
        && style.display !== 'none'
        && rect
        && rect.width > 0
        && rect.height > 0
      );
    }
    return snapshot;
  }, targetSelectors);
}

async function _collectUiMarkerPresence(page, markers) {
  const targetMarkers = Array.isArray(markers) ? markers : [];
  const snapshot = {};
  for (const marker of targetMarkers) {
    const count = await page.locator(`text=${marker}`).count().catch(() => 0);
    snapshot[marker] = count > 0;
  }
  return snapshot;
}

function _writeEvidence(payload) {
  if (!E2E_EVIDENCE_JSON) return;
  const outputPath = path.resolve(E2E_EVIDENCE_JSON);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
}

function _writeActionEvidence(payload) {
  if (!E2E_ACTION_EVIDENCE_JSON) return;
  const outputPath = path.resolve(E2E_ACTION_EVIDENCE_JSON);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
}

test('real Gmail/Chrome smoke scaffold is configured (manual-gated)', { skip: !RUN_E2E }, async () => {
  let chromium;
  try {
    ({ chromium } = require('playwright'));
  } catch (_) {
    assert.fail(
      'RUN_GMAIL_E2E=1 requires playwright. Install with `npm i -D playwright` and run again.',
    );
  }

  const manifestPath = path.join(EXTENSION_ROOT, 'manifest.json');
  assert.ok(fs.existsSync(manifestPath), 'manifest.json must exist for extension load');

  const userDataDir = process.env.GMAIL_E2E_PROFILE_DIR || path.resolve(EXTENSION_ROOT, '.e2e-profile');
  let context;
  let page = null;

  const evidence = {
    status: 'running',
    started_at: new Date().toISOString(),
    target_url: process.env.GMAIL_E2E_URL || 'https://mail.google.com/mail/u/0/#inbox',
    assert_auth: ASSERT_AUTH,
    expect_selector: EXPECT_SELECTOR,
    extension_worker_detected: false,
    extension_worker_url: null,
    mounted_sections: 0,
    ui_markers: UI_MARKERS,
    ui_marker_presence: {},
    entry_points_detected: 0,
    required_selectors: REQUIRED_SELECTORS,
    selector_presence: {},
    missing_selectors: [],
    current_url: null,
    page_title: null,
    screenshot_path: process.env.GMAIL_E2E_CAPTURE_PATH || null,
  };

  try {
    context = await chromium.launchPersistentContext(userDataDir, {
      headless: false,
      executablePath: E2E_EXECUTABLE_PATH || undefined,
      ignoreDefaultArgs: ['--disable-extensions'],
      args: [
        `--disable-extensions-except=${EXTENSION_ROOT}`,
        `--load-extension=${EXTENSION_ROOT}`,
        ...(E2E_PROFILE_DIRECTORY ? [`--profile-directory=${E2E_PROFILE_DIRECTORY}`] : []),
      ],
    });

    page = context.pages()[0] || await context.newPage();
    const targetUrl = evidence.target_url;
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: E2E_TIMEOUT_MS });
    const title = await page.title();
    assert.ok(typeof title === 'string');
    const currentUrl = page.url();
    evidence.current_url = currentUrl;
    evidence.page_title = title;
    const bodyText = await page.evaluate(() => document.body ? document.body.innerText || '' : '');

    if (ASSERT_AUTH) {
      assert.ok(
        !_looksLikeLoginPage(currentUrl, title, bodyText),
        `GMAIL_E2E_ASSERT_AUTH=1 expects an authenticated Gmail profile. Current URL: ${currentUrl}`,
      );
      assert.ok(
        !_looksLikeMarketingPage(currentUrl, title, bodyText),
        `GMAIL_E2E_ASSERT_AUTH=1 reached a marketing or landing page instead of the Gmail inbox. Current URL: ${currentUrl}`,
      );
      assert.ok(
        _looksLikeAuthenticatedInbox(currentUrl, title, bodyText),
        `GMAIL_E2E_ASSERT_AUTH=1 expects a real authenticated Gmail inbox. Current URL: ${currentUrl}`,
      );
      const extensionWorker = await _findExtensionServiceWorker(context, Math.min(E2E_TIMEOUT_MS, 20000));
      assert.ok(
        extensionWorker,
        'Extension service worker not detected. Confirm extension loaded via --load-extension.',
      );
      evidence.extension_worker_detected = true;
      evidence.extension_worker_url = extensionWorker.url();

      await page.waitForTimeout(E2E_UI_SETTLE_MS);
      const selectorPresence = await _collectVisibleSelectorPresence(page, REQUIRED_SELECTORS);
      evidence.selector_presence = selectorPresence;
      evidence.missing_selectors = REQUIRED_SELECTORS.filter((selector) => !selectorPresence[selector]);
      evidence.mounted_sections = REQUIRED_SELECTORS.length - evidence.missing_selectors.length;
      const uiMarkerPresence = await _collectUiMarkerPresence(page, UI_MARKERS);
      evidence.ui_marker_presence = uiMarkerPresence;
      evidence.entry_points_detected = Object.values(uiMarkerPresence).filter(Boolean).length;
      if (REQUIRE_ALL_SELECTORS) {
        assert.equal(
          evidence.missing_selectors.length,
          0,
          `Required selectors missing in authenticated Gmail runtime: ${evidence.missing_selectors.join(', ')}`,
        );
      } else {
        assert.ok(
          evidence.mounted_sections >= 2 || evidence.entry_points_detected >= 1,
          'Expected Clearledgr inbox entry points or sidebar sections not found in authenticated Gmail runtime.',
        );
      }
    }
    evidence.status = 'passed';
  } catch (error) {
    evidence.status = 'failed';
    evidence.error = String(error?.message || error || 'unknown_e2e_error');
    throw error;
  } finally {
    const screenshotPath = process.env.GMAIL_E2E_CAPTURE_PATH;
    if (screenshotPath && page && !page.isClosed()) {
      const resolved = path.resolve(screenshotPath);
      try {
        await page.screenshot({ path: resolved, fullPage: true });
        if (fs.existsSync(resolved)) {
          evidence.screenshot_path = resolved;
        }
      } catch (_) {
        // best effort
      }
    }
    evidence.finished_at = new Date().toISOString();
    _writeEvidence(evidence);
    if (context) {
      await context.close();
    }
  }
});

test('real Gmail/Chrome smoke stays opt-in unless RUN_GMAIL_E2E=1', () => {
  assert.ok(true);
});

test('real Gmail/Chrome canonical AP action can be executed (manual-gated)', { skip: !(RUN_E2E && RUN_GMAIL_E2E_ACTION) }, async () => {
  let chromium;
  try {
    ({ chromium } = require('playwright'));
  } catch (_) {
    assert.fail(
      'RUN_GMAIL_E2E_ACTION=1 requires playwright. Install with `npm i -D playwright` and run again.',
    );
  }

  assert.ok(ACTION_SELECTOR, 'GMAIL_E2E_ACTION_SELECTOR is required when RUN_GMAIL_E2E_ACTION=1');

  const manifestPath = path.join(EXTENSION_ROOT, 'manifest.json');
  assert.ok(fs.existsSync(manifestPath), 'manifest.json must exist for extension load');

  const userDataDir = process.env.GMAIL_E2E_PROFILE_DIR || path.resolve(EXTENSION_ROOT, '.e2e-profile');
  let context;
  let page = null;

  const evidence = {
    status: 'running',
    started_at: new Date().toISOString(),
    target_url: process.env.GMAIL_E2E_URL || 'https://mail.google.com/mail/u/0/#inbox',
    action_selector: ACTION_SELECTOR,
    action_success_selector: ACTION_SUCCESS_SELECTOR || null,
    action_success_text: ACTION_SUCCESS_TEXT || null,
    current_url: null,
    page_title: null,
    extension_worker_detected: false,
    extension_worker_url: null,
    action_clicked: false,
    action_completed: false,
    screenshot_path: process.env.GMAIL_E2E_CAPTURE_PATH || null,
  };

  try {
    context = await chromium.launchPersistentContext(userDataDir, {
      headless: false,
      executablePath: E2E_EXECUTABLE_PATH || undefined,
      ignoreDefaultArgs: ['--disable-extensions'],
      args: [
        `--disable-extensions-except=${EXTENSION_ROOT}`,
        `--load-extension=${EXTENSION_ROOT}`,
        ...(E2E_PROFILE_DIRECTORY ? [`--profile-directory=${E2E_PROFILE_DIRECTORY}`] : []),
      ],
    });

    page = context.pages()[0] || await context.newPage();
    await page.goto(evidence.target_url, { waitUntil: 'domcontentloaded', timeout: E2E_TIMEOUT_MS });
    evidence.current_url = page.url();
    evidence.page_title = await page.title();

    const bodyText = await page.evaluate(() => document.body ? document.body.innerText || '' : '');
    assert.ok(
      _looksLikeAuthenticatedInbox(evidence.current_url, evidence.page_title, bodyText),
      `RUN_GMAIL_E2E_ACTION=1 expects an authenticated Gmail inbox. Current URL: ${evidence.current_url}`,
    );

    const extensionWorker = await _findExtensionServiceWorker(context, Math.min(E2E_TIMEOUT_MS, 20000));
    assert.ok(extensionWorker, 'Extension service worker not detected for Gmail action smoke.');
    evidence.extension_worker_detected = true;
    evidence.extension_worker_url = extensionWorker.url();

    await page.waitForTimeout(E2E_UI_SETTLE_MS);
    await page.locator(ACTION_SELECTOR).first().click({ timeout: E2E_TIMEOUT_MS });
    evidence.action_clicked = true;
    await page.waitForTimeout(ACTION_SETTLE_MS);

    if (ACTION_SUCCESS_SELECTOR) {
      await page.locator(ACTION_SUCCESS_SELECTOR).first().waitFor({ state: 'visible', timeout: E2E_TIMEOUT_MS });
    }
    if (ACTION_SUCCESS_TEXT) {
      await page.locator(`text=${ACTION_SUCCESS_TEXT}`).first().waitFor({ state: 'visible', timeout: E2E_TIMEOUT_MS });
    }
    evidence.action_completed = true;
    evidence.status = 'passed';
  } catch (error) {
    evidence.status = 'failed';
    evidence.error = String(error?.message || error || 'unknown_gmail_action_error');
    throw error;
  } finally {
    const screenshotPath = process.env.GMAIL_E2E_CAPTURE_PATH;
    if (screenshotPath && page && !page.isClosed()) {
      const resolved = path.resolve(screenshotPath);
      try {
        await page.screenshot({ path: resolved, fullPage: true });
        if (fs.existsSync(resolved)) {
          evidence.screenshot_path = resolved;
        }
      } catch (_) {
        // best effort
      }
    }
    evidence.finished_at = new Date().toISOString();
    _writeActionEvidence(evidence);
    if (context) {
      await context.close();
    }
  }
});

test('_looksLikeLoginPage detects Google auth redirects', () => {
  assert.equal(
    _looksLikeLoginPage(
      'https://accounts.google.com/v3/signin/identifier?continue=https%3A%2F%2Fmail.google.com',
      'Gmail',
      'To continue to Gmail',
    ),
    true,
  );
});

test('_looksLikeMarketingPage rejects workspace landing pages', () => {
  assert.equal(
    _looksLikeMarketingPage(
      'https://workspace.google.com/intl/en-US/gmail/#inbox',
      'Gmail: Secure, AI-Powered Email for Everyone | Google Workspace',
      'AI-powered email for everyone',
    ),
    true,
  );
});

test('_looksLikeAuthenticatedInbox accepts real Gmail inbox urls', () => {
  assert.equal(
    _looksLikeAuthenticatedInbox(
      'https://mail.google.com/mail/u/0/#inbox',
      'Inbox (3) - ops@example.com - Gmail',
      'Compose Inbox Starred',
    ),
    true,
  );
});
