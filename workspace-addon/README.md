# Clearledgr AP — Google Workspace Add-on

DESIGN_THESIS.md §6.9: Mobile Approvals

## What it does

Lightweight approval panel inside the native Gmail app (iOS/Android).
When a CFO or Controller opens an email thread linked to a Clearledgr
invoice Box, the Add-on shows:

- Invoice amount (large)
- Match status (PO ✓ / GRN ✓ / Invoice ✓)
- Exception reason (if any)
- **Approve** or **Reject** button

One tap. No navigation. No separate app.

## What it is NOT

- Not the full Clearledgr experience (that's the Chrome extension)
- Not a standalone mobile app
- Not a replacement for the desktop pipeline view

## Deployment

1. Create a Google Cloud project with Gmail Add-on API enabled
2. Create an Apps Script project linked to the Cloud project
3. Copy `appsscript.json` and `Code.gs` into the Apps Script editor
4. Set script property `CLEARLEDGR_API_URL` to your API endpoint
5. Deploy as a Gmail Add-on (test deployment first)
6. Publish to Google Workspace Marketplace for enterprise distribution

## Authentication

Uses Google's built-in OAuth via `ScriptApp.getOAuthToken()`.
The Clearledgr backend validates this token in `core/auth.py`
(same path as the Chrome extension's Google OAuth).
No separate Clearledgr login required.

## API Endpoints Used

- `GET /extension/worklist` — find AP item for current email
- `POST /api/ap/items/{id}/approve` — approve action
- `POST /api/ap/items/{id}/reject` — reject action
