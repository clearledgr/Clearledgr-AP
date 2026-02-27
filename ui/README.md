# Clearledgr UI Surfaces (AP-first)

Clearledgr is an embedded finance execution agent.

Current product priority is AP v1 with Gmail as the primary operator surface, Slack/Teams as approval surfaces, and ERP write-back via backend runtime contracts.

## Active AP v1 Surfaces

| Surface | Type | Directory | Role |
|---|---|---|---|
| Gmail | Chrome Extension | `ui/gmail-extension/` | Primary AP operator workflow |
| Slack | App/Bot | `ui/slack/` | Approval and exception decisions |

## Legacy / Non-canonical Surfaces

| Surface | Type | Directory | Status |
|---|---|---|---|
| Google Sheets | Workspace Add-on | `ui/sheets/` | Legacy/experimental (not AP v1 canonical) |

## Gmail Extension (`ui/gmail-extension/`)

### What it does
- Detects invoice/AP-related emails
- Shows AP status, exceptions, and next actions in-thread
- Calls canonical runtime intent APIs for batch/agent actions
- Keeps execution auditable and policy-checked through backend contracts

### Setup
1. Open `chrome://extensions/`
2. Enable Developer Mode
3. Click `Load unpacked`
4. Select `ui/gmail-extension/`

## Slack Surface (`ui/slack/`)

### What it does
- Receives AP approvals/exception decisions
- Surfaces AP execution notifications
- Supports operational slash commands and shortcuts

### Setup
1. Create Slack app at <https://api.slack.com/apps>
2. Load `ui/slack/manifest.json`
3. Configure backend URLs and secrets
4. Run `python ui/slack/app.py`

## Architecture Note

UI surfaces are thin clients. Core execution happens in backend runtime/skills (`/api/agent/intents/*`, AP skill modules, policy checks, audit, orchestration).
