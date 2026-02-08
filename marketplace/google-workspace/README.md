# Google Workspace Marketplace Submission

## Pre-requisites

1. Google Cloud Project with billing enabled
2. OAuth consent screen configured
3. Domain verification (clearledgr.com)

## Submission Steps

### 1. Create Apps Script Project

```bash
# Install clasp (Google Apps Script CLI)
npm install -g @google/clasp

# Login to Google
clasp login

# Create project for Sheets add-on
cd ui/sheets
clasp create --type sheets --title "Clearledgr for Sheets"

# Push code
clasp push

# Create project for Gmail add-on
cd ../gmail
clasp create --type gmail --title "Clearledgr for Gmail"

# Push code
clasp push
```

### 2. Configure OAuth Consent Screen

Go to: https://console.cloud.google.com/apis/credentials/consent

Required fields:
- App name: Clearledgr
- User support email: support@clearledgr.com
- App logo: Upload 128x128 PNG
- Application home page: https://clearledgr.com
- Application privacy policy: https://clearledgr.com/privacy
- Application terms of service: https://clearledgr.com/terms
- Authorized domains: clearledgr.com

### 3. Submit to Marketplace

Go to: https://console.cloud.google.com/apis/dashboard

1. Select your project
2. Go to "Google Workspace Marketplace SDK"
3. Enable the API
4. Configure the SDK:

**App Configuration:**
- Application name: Clearledgr
- Short description: Embedded reconciliation and transaction categorization for finance teams
- Detailed description: (see below)
- Category: Business Tools > Accounting & Finance
- Pricing: Free (or configure pricing)

**Listing Info:**
- Icons: 32x32, 96x96, 128x128 PNG
- Screenshots: 1280x800 PNG (3-5 required)
- Video: YouTube link (optional but recommended)

### 4. Verification

Google will review:
- OAuth scopes justification
- Privacy policy compliance
- Functionality testing

Timeline: 1-3 weeks for initial review

## Detailed Description (for submission)

```
Clearledgr is an intelligent reconciliation and transaction categorization engine that works inside Google Sheets and Gmail.

KEY FEATURES:

Reconciliation:
- 3-way matching across payment gateways, banks, and internal systems
- Configurable tolerances for amounts and dates
- AI-powered exception explanations
- Audit trail written directly to your spreadsheet

Transaction Categorization:
- Autonomous categorization on spreadsheet open
- Learns from your corrections
- Pattern-based matching for consistent results
- Only surfaces exceptions for review

Email Integration (Gmail):
- Parses invoices and payment confirmations automatically
- Matches to existing transactions
- Creates tasks for unmatched items
- Syncs to your connected spreadsheet

DATA HANDLING:
- Full email and sheet context can be processed for AI explanations
- Clearledgr services power extraction, matching, and approvals
```

## Post-Submission

Once approved:
- Users install from: https://workspace.google.com/marketplace
- One-click install, no manual setup
- Automatic updates when you push new code

## Support Files Needed

Create these before submission:
1. Logo files (32x32, 96x96, 128x128 PNG)
2. Screenshots (1280x800)
3. Privacy policy page
4. Terms of service page
