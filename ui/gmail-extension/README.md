# Clearledgr Gmail Chrome Extension (Work Surface)

Decision-first Gmail execution surface for AP Skill v1. Setup, account management, Ops, batch controls, and debug tooling live in the Admin Console.

## Features

- **Work-only Gmail sidebar** - one current invoice, clear blockers, one primary action
- **Inline reason sheet** - reject/override reason capture without native prompt dialogs
- **Embedded action execution** - request approval, nudge, preview/retry ERP posting
- **Evidence and audit** - compact checklist + plain-language audit timeline
- **Admin Console link-outs** - integrations and Ops open outside Gmail

## Installation

### Development Mode

1. **Add Icon Files**
   Place your Clearledgr logo icons in the `icons/` folder:
   - `icon16.png` (16x16 pixels)
   - `icon48.png` (48x48 pixels)
   - `icon128.png` (128x128 pixels)

2. **Load the Extension**
   - Open Chrome and go to `chrome://extensions/`
   - Enable "Developer mode" (toggle in top right)
   - Click "Load unpacked"
   - Select this `gmail-extension` folder

3. **Grant Permissions**
   - The extension will request access to Gmail
   - Click "Allow" when prompted

4. **Use the Extension**
   - Go to Gmail (mail.google.com)
   - Open the Clearledgr item in Gmail's left AppMenu (Streak-style)
   - Open an invoice email to see the Clearledgr email sidebar

### Publishing to Chrome Web Store

1. Create a ZIP of this folder
2. Go to [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole)
3. Pay the one-time $5 developer fee
4. Upload your extension
5. Fill in store listing details
6. Submit for review (usually 1-3 business days)

## File Structure

```
gmail-extension/
├── manifest.json            # Extension configuration
├── background.js            # Service worker (OAuth + runtime command bridge)
├── content-script.js        # Data bridge (no operator UI)
├── queue-manager.js         # AP queue/runtime orchestration
├── src/inboxsdk-layer.js    # Gmail Work UI source
├── dist/inboxsdk-layer.js   # Shipped Gmail Work bundle
├── scripts/verify-bundle-parity.cjs
├── tests/*.test.cjs
└── icons/
```

## Configuration

- Runtime endpoint and org config are resolved from extension storage + backend bootstrap.
- Integrations and account management are controlled from `/console` (Admin Console).
- Legacy popup/options/demo assets were moved to `/Users/mombalam/Desktop/Clearledgr.v1/docs/legacy/gmail-extension-ui/` and are not part of the shipped extension UX.

## Data Handling

This extension can use Clearledgr backend services:
- Full email context may be sent for extraction and matching
- Attachment text can be processed for better accuracy
- Settings and API credentials are stored in your Chrome profile

## Development

To modify the extension:
1. Make changes to the source files
2. Rebuild the shipped Gmail bundle with Bun-backed tooling:
   - `npm run build`
   - `npm run build:prod`
   - Bun must be installed locally and available on `PATH`, or pointed to via `CLEARLEDGR_BUN_BIN`.
3. Go to `chrome://extensions/`
4. Click the refresh icon on the Clearledgr extension
5. Reload Gmail to see changes

Build toolchain note:

- The extension no longer uses webpack as the active local build path.
- `scripts/build-extension.cjs` drives Bun bundling for `dist/inboxsdk-layer.js` and `dist/pageWorld.js`, then applies the audited fingerprint and parity checks.
- `npm run start` uses Bun watch mode for the Gmail Work bundle.

## Testing

Run deterministic local coverage:

- `npm test`
- `npm run test:integration`
- `npm run test:browser-harness` (real-browser DOM lifecycle harness; Playwright required)
- `npm run test:browser-harness:ci` (same harness with required browser mode; fails if prerequisites are missing)

Browser runtime prerequisites (one-time):

- `npm i -D playwright`
- `npx playwright install chromium`

Run manual-gated real Chrome/Gmail smoke:

- `npm run test:e2e-smoke`
- `npm run test:e2e-runner:preflight` (checks runner profile + browser prerequisites for nightly workflow)

Run authenticated Gmail runtime assertions (requires logged-in Gmail profile):

- `npm run test:e2e-auth`

Generate deterministic UI hardening screenshot evidence (Work + Admin Ops surfaces):

- `npm run evidence:ui-hardening -- --release-id ap-v1-2026-02-25-pilot-rc1 --backend-url http://127.0.0.1:8010`

Optional environment variables for E2E:

- `GMAIL_E2E_PROFILE_DIR`: persistent Chrome profile path
- `GMAIL_E2E_URL`: Gmail URL to open (default inbox)
- `GMAIL_E2E_EXPECT_SELECTOR`: selector to assert in authenticated mode (default `#cl-scan-status`)
- `GMAIL_E2E_TIMEOUT_MS`: wait timeout (default `180000`)
- `GMAIL_E2E_CAPTURE_PATH`: screenshot output path for evidence capture
- `GMAIL_E2E_EVIDENCE_JSON`: write JSON evidence payload for smoke/auth run (status, URL, mounted sections, errors)
- `GMAIL_BROWSER_HARNESS_TIMEOUT_MS`: timeout for browser harness test (default `120000`)
- `GMAIL_BROWSER_HARNESS_CHANNEL`: browser channel override for harness launch (for example `chrome`)
- `GMAIL_BROWSER_HARNESS_HEADFUL`: set `1` to run harness in headed mode

Browser harness troubleshooting:

- If the default Playwright Chromium launch fails on macOS, run:
  - `GMAIL_BROWSER_HARNESS_CHANNEL=chrome npm run test:browser-harness`
- If needed, install browser binaries:
  - `npx playwright install chromium`

CI and nightly runtime verification:

- `/.github/workflows/gmail-extension-browser-harness.yml`: deterministic browser harness on PR/push for extension changes.
- `/.github/workflows/gmail-runtime-smoke-nightly.yml`: nightly authenticated Gmail runtime smoke + evidence artifact upload.
- Nightly job requires a controlled self-hosted runner with a pre-authenticated Gmail profile path provided via secret `GMAIL_E2E_PROFILE_DIR`.
- Runner setup guide: `/Users/mombalam/Desktop/Clearledgr.v1/docs/GMAIL_RUNTIME_RUNNER_SETUP.md`.

## Support

For issues or feature requests, contact the Clearledgr team.
