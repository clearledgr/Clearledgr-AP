# Clearledgr v1 Marketplace Deployment

## Overview

v1 focuses on three marketplaces:

| Marketplace | Product | Timeline | Status |
|-------------|---------|----------|--------|
| Google Workspace | Sheets Add-on | 1-3 weeks | Ready to submit |
| Chrome Web Store | Gmail Extension | 1-2 weeks | Ready to submit |
| Slack App Directory | Slack Bot | 1-2 weeks | Ready to submit |

**v2 (Later):** Microsoft AppSource (Excel, Outlook), Teams App Store

## Quick Start

### Step 1: Deploy Backend

```bash
# Option A: Docker
docker-compose up -d

# Option B: Direct
pip install -r requirements.txt
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

Backend must be accessible at a public HTTPS URL (e.g., https://api.clearledgr.com)

### Step 2: Prepare Assets

All marketplaces require:

1. **Logo files** (in `assets/`)
   - 32x32 PNG
   - 96x96 PNG  
   - 128x128 PNG
   - 192x192 PNG
   - 256x256 PNG

2. **Screenshots** (1280x800 or 1366x768 PNG)
   - Sheets showing reconciliation
   - Gmail sidebar with invoice
   - Slack notification

3. **Legal pages** (hosted on clearledgr.com)
   - Privacy Policy
   - Terms of Service
   - Support/Help page

### Step 3: Submit to Marketplaces

```bash
# Google Workspace (Sheets Add-on)
cd google-workspace && ./deploy.sh

# Chrome Web Store (Gmail Extension)
# Package ui/gmail-extension/ as .zip
# Upload at https://chrome.google.com/webstore/developer/dashboard

# Slack - Manual via api.slack.com
# See slack-app-directory/README.md
```

## Timeline Estimate

| Week | Milestone |
|------|-----------|
| Week 1 | Deploy backend, submit all apps |
| Week 2 | Chrome extension approved, Slack in review |
| Week 3 | All marketplaces approved |

## Checklist

### Before Submission

- [ ] Backend deployed and accessible via HTTPS
- [ ] Logo files in all required sizes
- [ ] Screenshots captured
- [ ] Privacy policy published
- [ ] Terms of service published
- [ ] Support email configured

### Google Workspace (Sheets)

- [ ] clasp installed and logged in
- [ ] Apps Script project created
- [ ] OAuth consent screen configured
- [ ] Marketplace SDK enabled
- [ ] Listing submitted

### Chrome Web Store (Gmail Extension)

- [ ] Developer account created ($5 one-time fee)
- [ ] Extension packaged as .zip
- [ ] Store listing created
- [ ] Screenshots uploaded
- [ ] Submitted for review

### Slack App Directory

- [ ] Slack app created
- [ ] Bot token obtained
- [ ] Signing secret configured
- [ ] App Directory listing submitted

## Post-Launch

Once approved:

1. Add "Available on" badges to website
2. Create installation guides
3. Add marketplace links to documentation
4. Set up app analytics

## Support

For submission issues:
- Google Workspace: https://support.google.com/a/topic/6310992
- Chrome Web Store: https://support.google.com/chrome_webstore/
- Slack: https://api.slack.com/support
