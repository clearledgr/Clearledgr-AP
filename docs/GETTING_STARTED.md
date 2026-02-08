# Getting Started with Clearledgr

Welcome to Clearledgr! This guide will help you set up and run your first reconciliation and Reconciliation analysis.

## What is Clearledgr?

Clearledgr is an intelligent layer that embeds directly into the tools you already use. Google Sheets, Gmail, and Slack. It automates financial reconciliation workflows without requiring you to learn a new platform. Excel/Teams support is planned.

## Quick Start (5 minutes)

### Step 1: Prepare Your Data

You need three data sources for reconciliation:

1. **Payment Gateway Export** (from Stripe, Flutterwave, PayStack, etc.)
   - Transaction ID
   - Date
   - Amount
   - Status

2. **Bank Statement** (from your bank)
   - Transaction ID
   - Date
   - Amount

3. **Internal Records** (from your accounting system)
   - Invoice/Transaction ID
   - Date
   - Amount

### Step 2: Open Your Spreadsheet

#### Google Sheets
1. Create a new Google Sheet
2. Import each data source as a separate tab
3. Name the tabs: `GATEWAY`, `BANK`, `INTERNAL`

Excel support is planned after v1.

### Step 3: Run Clearledgr

#### In Google Sheets
1. Click **Clearledgr** in the menu bar
2. Select **Reconciliation > Run Reconciliation**
3. In the sidebar:
   - Select your three data tabs
   - Set the period dates
   - Click **Run Reconciliation**

### Step 4: Review Results

Results appear in new sheets in your workbook:

| Sheet | Contents |
|-------|----------|
| CL_SUMMARY | Overall statistics for the period |
| CL_RECONCILED | All matched transaction groups |
| CL_EXCEPTIONS | Unmatched items with AI explanations |

---

## Notifications

### Slack App

Install the Clearledgr Slack app from your workspace's App Directory:

1. Search for "Clearledgr" in Slack's app directory
2. Click "Add to Slack"
3. Authorize the app

You'll receive rich interactive notifications:
- Reconciliation results with match rates
- Exception notifications with resolve buttons
- Task reminders with complete buttons

Use slash commands directly in Slack:
- `/clearledgr status` - View current status
- `/clearledgr run` - Run reconciliation
- `/reconcile` - Quick reconciliation
---

## Tips for Best Results

### Reconciliation

1. **Use consistent date formats** - Dates should be in a recognized format (YYYY-MM-DD, DD/MM/YYYY, etc.)

2. **Include reference numbers** - If available, include transaction references to improve matching

3. **Review exceptions** - The AI explains why items didn't match, use this to investigate

---

## Troubleshooting

### "Sheet not found"
Verify the sheet names match exactly what you entered in the sidebar.

### "No matches found"
Check that:
- Dates are within the selected period
- Amounts are in the same currency
- Date formats are consistent

### "API request failed"
The Clearledgr service may be temporarily unavailable. Try again in a few minutes.

---

## Support

- Documentation: https://docs.clearledgr.com
- Email: support@clearledgr.com
