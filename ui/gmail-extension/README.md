# Clearledgr Gmail Chrome Extension (Embedded Worker)

An embedded Gmail worker that automates AP workflows (invoice intake -> review -> approvals -> ERP posting) without leaving your inbox.

## Features

- **Streak-style UI (InboxSDK)** - AppMenu + routes inside Gmail (no custom DOM sidebar)
- **Email sidebar** - Contextual AP panel when you open an invoice email
- **Autonomous inbox scanning** - Finds AP candidates and queues them
- **Approvals** - Review/approve/reject from the Clearledgr routes
- **Non-finance detection** - Aggressively filters noise/marketing

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
├── manifest.json      # Extension configuration
├── background.js      # Service worker for settings/storage
├── content-script.js  # Data bridge (NO UI): queue/events <-> InboxSDK layer
├── queue-manager.js   # AP queue + autonomous scanning orchestration
├── src/inboxsdk-layer.js    # InboxSDK implementation (routes + email sidebar)
├── dist/inboxsdk-layer.js   # Built bundle (generated)
├── icons/             # Extension icons
│   ├── icon16.png
│   ├── icon48.png
│   └── icon128.png
└── README.md          # This file
```

## Configuration

Settings are stored in Chrome sync storage and include:
- Auto-process emails toggle
- Confidence threshold for matches
- Ignored sender domains
- GL account mappings
- ERP connection settings

## Data Handling

This extension can use Clearledgr backend services:
- Full email context may be sent for extraction and matching
- Attachment text can be processed for better accuracy
- Settings and API credentials are stored in your Chrome profile

## Development

To modify the extension:
1. Make changes to the source files
2. Go to `chrome://extensions/`
3. Click the refresh icon on the Clearledgr extension
4. Reload Gmail to see changes

## Testing

Run deterministic local coverage:

- `npm test`
- `npm run test:integration`
- `npm run test:browser-harness` (real-browser DOM lifecycle harness; Playwright required)

Browser runtime prerequisites (one-time):

- `npm i -D playwright`
- `npx playwright install chromium`

Run manual-gated real Chrome/Gmail smoke:

- `npm run test:e2e-smoke`

Run authenticated Gmail runtime assertions (requires logged-in Gmail profile):

- `npm run test:e2e-auth`

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

## Support

For issues or feature requests, contact the Clearledgr team.
