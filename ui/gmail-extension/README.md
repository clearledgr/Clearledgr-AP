# Clearledgr Gmail Extension (AP v1)

Embedded InboxSDK sidebar for Clearledgr AP execution in Gmail.

## Features

- InboxSDK embedded sidebar only
- Autonomous AP inbox scanning
- AP queue and current thread status
- Approval and rejection actions
- Backend-sourced immutable audit trail

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
   - Go to Gmail (`mail.google.com`)
   - Open any invoice thread
   - Clearledgr sidebar runs autonomously and updates AP state

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
├── content-script.js  # Data bridge (NO UI)
├── queue-manager.js   # AP queue + autonomous scanning orchestration
├── src/inboxsdk-layer.js    # InboxSDK implementation (single AP sidebar)
├── dist/inboxsdk-layer.js   # Built bundle (generated)
├── icons/             # Extension icons
│   ├── icon16.png
│   ├── icon48.png
│   └── icon128.png
└── README.md          # This file
```

## Configuration

Settings are stored in Chrome sync storage and include:
- Backend URL
- Organization ID
- User email
- Slack channel

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

## Support

For issues or feature requests, contact the Clearledgr team.
