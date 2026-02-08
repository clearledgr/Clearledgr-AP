# How Clearledgr Works

## Overview

Clearledgr is an AI-powered accounts payable assistant that lives inside Gmail. Instead of forcing finance teams into another dashboard, we embed directly where invoices arrive - your inbox.

---

## Step-by-Step

### 1. Install the Gmail Extension

Add Clearledgr to Chrome. It embeds directly into Gmail - no new tabs, no new apps to learn. You'll see a Clearledgr icon in your Gmail sidebar.

### 2. Connect Your Accounting Software

One-click OAuth to link your ERP:
- QuickBooks Online
- Xero
- NetSuite
- SAP

Your chart of accounts syncs automatically, so GL code suggestions are always accurate.

### 3. Clearledgr Monitors Your Inbox 24/7

Our AI agent watches for invoices, receipts, and payment requests. No action required from you. The agent:
- Scans incoming emails in real-time
- Identifies financial documents using pattern matching and AI
- Extracts data from PDFs and images using Claude Vision

### 4. Invoices Appear in Your Pipeline

When an invoice arrives, Clearledgr:
- Extracts vendor name, amount, currency, due date
- Suggests the correct GL code based on learned patterns
- Calculates a confidence score
- Shows it in a pipeline view right inside Gmail

### 5. Review & Approve (or Let AI Handle It)

Clearledgr uses confidence-based automation:

| Confidence | Action |
|------------|--------|
| **95%+** | Auto-approved and posted to ERP (no human needed) |
| **85-94%** | Auto-approved, you get a notification |
| **70-84%** | Asks for confirmation before posting |
| **Below 70%** | Requires manual review |

Low-confidence invoices appear in Slack or the Gmail sidebar for one-click approval.

### 6. Posted to Your ERP

Approved invoices become Bills in your accounting system automatically:
- Creates the vendor if they don't exist
- Maps to the correct GL account
- Attaches the original invoice PDF

### 7. Payment Scheduled

After posting, Clearledgr:
- Queues the payment based on due date
- Detects early payment discounts (e.g., "2/10 Net 30")
- Sends reminders before due dates
- Supports batch payment processing

---

## The Result

What used to take hours of:
- Downloading PDF attachments
- Manual data entry into your ERP
- Switching between Gmail, spreadsheets, and accounting software

Becomes a **20-minute batch review session** - all without leaving Gmail.

---

## Key Features

### For Finance Teams
- **No context switching** - work stays in Gmail
- **AI-powered extraction** - handles PDFs, images, and email text
- **Confidence-based automation** - routine invoices process automatically
- **Full audit trail** - every action is logged

### For Leadership
- **Real-time visibility** - dashboard shows AP status at a glance
- **Policy compliance** - automatic checks against spending rules
- **Reduced errors** - AI catches duplicates and anomalies
- **Faster close** - less manual work at month-end

---

## Integrations

### ERPs
- QuickBooks Online
- Xero
- NetSuite
- SAP Business One

### Bank Feeds
- Nordigen (EU Open Banking)
- TrueLayer (UK)
- Okra (Africa)

### Notifications
- Slack (approval workflows)
- Email digests

### Payments
- Wise Business API
- Stripe
- ACH/NACHA batch files

---

## Security & Compliance

- **OAuth 2.0** - secure connection to ERPs (no passwords stored)
- **GDPR compliant** - data residency options for EU
- **EU VAT validation** - automatic VIES checks
- **Audit logs** - full history of all actions
- **Role-based access** - control who can approve

---

## Getting Started

1. Install the Chrome extension
2. Authorize Gmail access
3. Connect your ERP
4. Start processing invoices

The AI learns from your approvals, getting smarter over time.
