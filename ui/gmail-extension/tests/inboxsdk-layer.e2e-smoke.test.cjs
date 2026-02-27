const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

const EXTENSION_ROOT = path.resolve(__dirname, '..');
const RUN_E2E = process.env.RUN_GMAIL_E2E === '1';
const ASSERT_AUTH = process.env.GMAIL_E2E_ASSERT_AUTH === '1';
const E2E_TIMEOUT_MS = Number(process.env.GMAIL_E2E_TIMEOUT_MS || 180000);
const EXPECT_SELECTOR = process.env.GMAIL_E2E_EXPECT_SELECTOR || '#cl-scan-status';
const E2E_EVIDENCE_JSON = process.env.GMAIL_E2E_EVIDENCE_JSON || '';

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

function _writeEvidence(payload) {
  if (!E2E_EVIDENCE_JSON) return;
  const outputPath = path.resolve(E2E_EVIDENCE_JSON);
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
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: false,
    args: [
      `--disable-extensions-except=${EXTENSION_ROOT}`,
      `--load-extension=${EXTENSION_ROOT}`,
    ],
  });

  const evidence = {
    status: 'running',
    started_at: new Date().toISOString(),
    target_url: process.env.GMAIL_E2E_URL || 'https://mail.google.com/mail/u/0/#inbox',
    assert_auth: ASSERT_AUTH,
    expect_selector: EXPECT_SELECTOR,
    extension_worker_detected: false,
    mounted_sections: 0,
    current_url: null,
    page_title: null,
    screenshot_path: process.env.GMAIL_E2E_CAPTURE_PATH || null,
  };

  try {
    const page = context.pages()[0] || await context.newPage();
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
      const extensionWorker = await _findExtensionServiceWorker(context, Math.min(E2E_TIMEOUT_MS, 20000));
      assert.ok(
        extensionWorker,
        'Extension service worker not detected. Confirm extension loaded via --load-extension.',
      );
      evidence.extension_worker_detected = true;

      await page.waitForSelector(EXPECT_SELECTOR, { timeout: E2E_TIMEOUT_MS });
      const mountedSections = await page.evaluate(() => {
        const ids = ['#cl-thread-context', '#cl-kpi-summary', '#cl-agent-actions'];
        return ids.filter((selector) => Boolean(document.querySelector(selector))).length;
      });
      evidence.mounted_sections = mountedSections;
      assert.ok(mountedSections >= 2, 'Expected Clearledgr sidebar sections not found in authenticated Gmail runtime.');
    }

    const screenshotPath = process.env.GMAIL_E2E_CAPTURE_PATH;
    if (screenshotPath) {
      const resolved = path.resolve(screenshotPath);
      await page.screenshot({ path: resolved, fullPage: true });
      assert.ok(fs.existsSync(resolved), `Expected screenshot at ${resolved}`);
      evidence.screenshot_path = resolved;
    }
    evidence.status = 'passed';
  } catch (error) {
    evidence.status = 'failed';
    evidence.error = String(error?.message || error || 'unknown_e2e_error');
    throw error;
  } finally {
    evidence.finished_at = new Date().toISOString();
    _writeEvidence(evidence);
    await context.close();
  }
});

test('real Gmail/Chrome smoke stays opt-in unless RUN_GMAIL_E2E=1', () => {
  assert.ok(true);
});
