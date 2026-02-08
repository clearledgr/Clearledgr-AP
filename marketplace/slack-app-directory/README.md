# Slack App Directory Submission

## Pre-requisites

1. Slack workspace for development
2. Backend API deployed (for slash commands and interactivity)
3. Logo and screenshots

## Architecture

Clearledgr Slack app uses:
- **Slash commands**: `/clearledgr`, `/reconcile`, `/categorize`
- **Interactive buttons**: Approve/Reject in exception cards
- **App Home**: Dashboard view in Slack

## Submission Steps

### 1. Create Slack App

Go to: https://api.slack.com/apps

1. Click "Create New App"
2. Choose "From a manifest"
3. Select your workspace
4. Paste the manifest from `ui/slack/manifest.json`

### 2. Deploy Backend

The Slack app requires your backend to be publicly accessible.

**Deploy to your server:**
```bash
# Example: Deploy to a VPS or cloud instance
cd /path/to/Clearledgr.v1

# Install dependencies
pip install -r requirements.txt

# Run with gunicorn
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

**Or use a tunnel for testing:**
```bash
# Using ngrok
ngrok http 8000
```

### 3. Update App URLs

In Slack App settings, update these URLs:

**Slash Commands:**
- `/clearledgr`: https://api.clearledgr.com/slack/commands
- `/reconcile`: https://api.clearledgr.com/slack/commands
- `/categorize`: https://api.clearledgr.com/slack/commands

**Interactivity & Shortcuts:**
- Request URL: https://api.clearledgr.com/slack/interactive

**Event Subscriptions:**
- Request URL: https://api.clearledgr.com/slack/events
- Subscribe to: `app_home_opened`, `message.channels`

### 4. Set Environment Variables

```bash
export SLACK_BOT_TOKEN="xoxb-your-bot-token"
export SLACK_SIGNING_SECRET="your-signing-secret"
export SLACK_APP_TOKEN="xapp-your-app-token"  # For socket mode (optional)
```

### 5. Install to Workspace

1. Go to "Install App" in Slack App settings
2. Click "Install to Workspace"
3. Authorize the permissions
4. Test the commands

### 6. Submit to App Directory

Go to: https://api.slack.com/apps > Your App > Submit to App Directory

**Required information:**

**App name:** Clearledgr

**Short description (10 words max):**
Embedded reconciliation and categorization for finance teams

**Long description:**
```
Clearledgr brings reconciliation and transaction categorization directly into Slack.

FEATURES:
- Get notified when reconciliation completes
- Review exceptions without leaving Slack
- Approve or reject matches with one click
- View categorization suggestions
- Track task status and reminders

HOW IT WORKS:
Clearledgr runs inside your Google Sheets and Excel, processing transactions automatically. When it finds exceptions or completes a run, it notifies your Slack channel with actionable cards.

COMMANDS:
/clearledgr status - Check system status
/reconcile run - Trigger reconciliation
/categorize review - Review pending categorizations

PRIVACY:
Your financial data stays in your spreadsheets. Slack only receives summaries and exception metadata, never raw transaction data.
```

**Category:** Productivity > Finance

**Screenshots:** 
- Command in action (800x600)
- Exception card with buttons (800x600)
- App Home view (800x600)

**Support URL:** https://clearledgr.com/support

**Privacy policy:** https://clearledgr.com/privacy

### 7. Security Review

Slack reviews:
- Token handling
- Data encryption
- Scope justification
- HTTPS enforcement

Timeline: 1-2 weeks

## Post-Submission

Once approved:
- Users find app at: https://slack.com/apps
- One-click "Add to Slack" button
- Admins can approve for entire workspace

