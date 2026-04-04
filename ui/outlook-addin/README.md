# Clearledgr Outlook Add-in

Finance execution agents embedded in Outlook — AP workflows, vendor intelligence, and approvals right inside your inbox.

## Architecture

This add-in mirrors the Gmail extension sidebar. The backend API endpoints (`/extension/*`) are shared between both platforms — only the surface integration layer differs:

| Layer | Gmail Extension | Outlook Add-in |
|-------|----------------|----------------|
| Email context | InboxSDK + Chrome APIs | Office.js mailbox API |
| Auth | Chrome identity → backend token | Office SSO → backend token |
| UI framework | Preact + HTM | Preact + HTM (same) |
| Sidebar rendering | InboxSDK thread sidebar | Office taskpane |
| Backend API | `/extension/*` | `/extension/*` (same) |

## Development

```bash
# Install dev dependencies
npm install

# Validate manifest
npm run validate

# Start local dev server
npm run dev

# Sideload into Outlook (opens browser-based Outlook)
npm run sideload
```

## Deployment

1. Host `taskpane.html`, `taskpane.css`, `functions.html`, and `src/` on HTTPS (e.g., `https://app.clearledgr.com/outlook/`)
2. Update URLs in `manifest.xml` to point to the hosted location
3. Submit `manifest.xml` to Microsoft AppSource or deploy via admin center

## Files

| File | Purpose |
|------|---------|
| `manifest.xml` | Office Add-in manifest (defines taskpane, commands, permissions) |
| `taskpane.html` | Main entry point (loads Office.js + Preact app) |
| `taskpane.css` | Styles matching Gmail extension design system |
| `functions.html` | Required by manifest (stub) |
| `src/outlook-entry.js` | Office.js bootstrap, email context reading, sidebar UI |
| `package.json` | Dev tooling (validation, sideloading) |
