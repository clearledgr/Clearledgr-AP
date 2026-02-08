# Google Sheets Add-on Deployment Guide

## Prerequisites

1. Google Cloud Project with Apps Script API enabled
2. Node.js installed
3. clasp CLI installed: `npm install -g @google/clasp`

## Setup Steps

### 1. Login to clasp

```bash
clasp login
```

### 2. Create a new Apps Script project

```bash
cd ui/sheets
clasp create --type sheets --title "Clearledgr"
```

This will update `.clasp.json` with your script ID.

### 3. Push code to Apps Script

```bash
clasp push
```

### 4. Open in Apps Script editor

```bash
clasp open
```

### 5. Test the add-on

1. In Apps Script editor, click "Run" > "Test as add-on"
2. Select a test spreadsheet
3. Click "Test"

### 6. Deploy as add-on

#### For internal/unlisted distribution:

1. In Apps Script editor, click "Deploy" > "Test deployments"
2. Click "Install"
3. Share the script with your team

#### For Google Workspace Marketplace:

1. Go to Google Cloud Console
2. Enable Google Workspace Marketplace SDK
3. Configure OAuth consent screen
4. Create marketplace listing
5. Submit for review

## Configuration

### API URL

Update `API_BASE_URL` in `Code.gs` for production:

```javascript
const API_BASE_URL = 'https://api.clearledgr.com';
```

### OAuth Scopes

The add-on requests these permissions:
- `spreadsheets.currentonly` - Read/write current spreadsheet only
- `script.container.ui` - Show sidebars and dialogs
- `script.external_request` - Call Clearledgr API
- `userinfo.email` - Identify user for agent memory

## Files

| File | Purpose |
|------|---------|
| Code.gs | Main add-on logic |
| sidebar.html | Reconciliation sidebar UI |
| settings.html | Settings dialog |
| schedules.html | Schedule management |
| reconciliation_*.html | Reconciliation feature sidebars |
| appsscript.json | Add-on manifest |

## Troubleshooting

### "Authorization required"
User needs to grant permissions on first use.

### "API request failed"
Check that API_BASE_URL is correct and API is running.

### "Sheet not found"
Ensure sheet names match what user selected.

