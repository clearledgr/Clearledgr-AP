# Clearledgr v1 - UI Components

Clearledgr v1 focuses on three platforms where finance teams live:

| Platform | Type | Directory |
|----------|------|-----------|
| **Google Sheets** | Workspace Add-on | `ui/sheets/` |
| **Gmail** | Chrome Extension | `ui/gmail-extension/` |
| **Slack** | Bot | `ui/slack/` |

---

## Google Sheets Add-on

**Location:** `ui/sheets/`

### What It Does
- **Intelligent Reconciliation** - Auto-detects columns (date, amount, reference) from any sheet structure
- **Visible AI Reasoning** - Shows step-by-step what the AI is doing
- **Spreadsheet Interaction** - Highlights matched rows (green) and exceptions (yellow)
- **Local-first Processing** - Reconciliation runs locally with optional backend augmentation

### Files
- `appsscript.json` - Manifest with OAuth scopes
- `Code.gs` - Main logic, sidebar, menu
- `ReconciliationEngine.gs` - Matching algorithms
- `CategorizationEngine.gs` - GL categorization
- `sidebar.html` - Main UI (Ramp-like activity feed)
- `settings.html` - Configuration UI
- `schedules.html` - Scheduled runs UI

### Setup
1. Create a Google Apps Script project
2. Copy all `.gs` and `.html` files
3. Deploy as Workspace Add-on (or test as Editor Add-on)

---

## Gmail Chrome Extension

**Location:** `ui/gmail-extension/`

### What It Does
- **Invoice Detection** - Identifies finance emails (invoices, payments)
- **Data Extraction** - Parses vendor, amount, dates from email body
- **Side Panel UI** - Premium sidebar using Chrome's native Side Panel API
- **Backend Augmentation** - Full context can be sent to Clearledgr for extraction and matching

### Files
- `manifest.json` - Chrome extension manifest
- `background.js` - Service worker
- `content.js` - Gmail DOM injection, email detection
- `content.css` - Styles for injected elements
- `sidebar.html` - Side panel UI
- `sidebar.js` - Side panel logic
- `icons/` - Extension icons

### Setup
1. Open `chrome://extensions/`
2. Enable "Developer mode"
3. Click "Load unpacked"
4. Select `ui/gmail-extension/` directory

---

## Slack Bot

**Location:** `ui/slack/`

### What It Does
- **Slash Commands** - Run reconciliation from Slack
- **Notifications** - Get alerts for exceptions needing review
- **Approvals** - Approve/reject exceptions directly in Slack

### Files
- `app.py` - Slack Bolt app
- `manifest.json` - Slack app manifest
- `DEPLOYMENT.md` - Setup instructions

### Setup
1. Create Slack App at https://api.slack.com/apps
2. Enable Socket Mode
3. Set environment variables:
   ```bash
   export SLACK_BOT_TOKEN="xoxb-..."
   export SLACK_APP_TOKEN="xapp-..."
   export API_BASE_URL="http://localhost:8000"
   ```
4. Run: `python ui/slack/app.py`

---

## v2 Roadmap (Not in v1)

- Microsoft Excel Add-in
- Microsoft Outlook Add-in  
- Microsoft Teams Bot

---

## Architecture

UI components are embedded where finance teams work, with local-first processing and
backend services available for extraction, matching, and approvals.

Backend API is used for:
- Learning from user feedback
- Cross-platform task management
- LLM explanations and extraction
