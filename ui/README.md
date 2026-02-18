# Clearledgr AP v1 UI

The UI surface is Gmail only. Clearledgr embeds a single InboxSDK sidebar that shows a minimal AP queue and thread level actions.

## Principles
- No navigation menus
- No dashboard pages
- AP workflow only

## Files
- `gmail-extension/src/inboxsdk-layer.js` Single InboxSDK render path
- `gmail-extension/queue-manager.js` AP scanning and action dispatch
- `gmail-extension/background.js` Gmail API intake and backend calls
