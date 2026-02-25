# Clearledgr Demo Plan - Multi-Surface Demo

## Overview
A comprehensive 3-minute demo showing Clearledgr's AI agents working across Gmail, Slack, and Google Sheets.

---

## Demo Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     CLEARLEDGR BACKEND                          │
│  (Python/FastAPI + Temporal + PostgreSQL)                       │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │  Vita    │  │  Recon   │  │  Invoice │  │  ERP     │        │
│  │  Agent   │  │  Engine  │  │  Extract │  │  Router  │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
└─────────────────────────────────────────────────────────────────┘
         ▲              ▲              ▲
         │              │              │
    ┌────┴────┐    ┌────┴────┐    ┌────┴────┐
    │  Gmail  │    │  Slack  │    │ Sheets  │
    │Extension│    │   App   │    │ Add-on  │
    └─────────┘    └─────────┘    └─────────┘
```

---

## Pre-Demo Setup Checklist

### 1. Backend Server
```bash
cd /Users/mombalam/Desktop/Clearledgr.v1
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Gmail Extension (Demo Mode)
- Load extension in Chrome (developer mode)
- Open Gmail with test account
- Pre-seed test emails (see below)

### 3. Slack Workspace
- Create demo Slack workspace or use existing
- Install Clearledgr app
- Pre-configure #finance channel

### 4. Google Sheets
- Create demo spreadsheet with test data
- Install Clearledgr add-on
- Pre-populate Gateway and Bank transaction sheets

---

## Test Data to Pre-Seed

### Gmail Test Emails (send to demo account)

**Email 1: AWS Invoice**
- From: billing@amazon.com
- Subject: Invoice #INV-2024-0892 from AWS
- Body: Your January 2026 AWS invoice is ready. Total: $12,847.32
- Attachment: aws_invoice.pdf

**Email 2: Stripe Settlement**
- From: receipts@stripe.com
- Subject: Stripe Invoice - January 2026
- Body: Payment processing fees for January. Amount: $3,421.89

**Email 3: Bank Statement**
- From: alerts@chase.com
- Subject: Your January Statement is Ready - Chase Business
- Body: Your business checking statement is now available.
- Attachment: chase_statement_jan2026.csv

**Email 4: Customer Payment**
- From: ar@acmecorp.com
- Subject: Payment Received - Invoice #4521
- Body: We have received your payment of $45,000.00

### Google Sheets Test Data

**Sheet: Gateway_Transactions**
| Amount | Date | Description | Reference |
|--------|------|-------------|-----------|
| 45000.00 | 2026-01-10 | Acme Corp Payment | INV-4521 |
| 28456.78 | 2026-01-11 | Stripe Payout | STR-89234 |
| 15234.00 | 2026-01-12 | TechStart Inc | INV-4522 |
| 8750.00 | 2026-01-13 | GlobalCo Ltd | INV-4523 |

**Sheet: Bank_Transactions**
| Amount | Date | Description | Reference |
|--------|------|-------------|-----------|
| 45000.00 | 2026-01-11 | Wire from ACME CORP | CHK-89234 |
| 28456.78 | 2026-01-12 | STRIPE PAYOUT | STR-89234 |
| 15234.00 | 2026-01-13 | TECHSTART INC PAYMENT | |
| 8750.00 | 2026-01-14 | GLOBALCO LTD | |
| -12847.32 | 2026-01-15 | AWS SERVICES | |
| 5234.00 | 2026-01-16 | UNKNOWN TRANSFER | |

---

## Demo Script (3 Minutes)

### Scene 1: Problem Statement (0:00 - 0:15)
**Screen:** Show typical finance team workflow - multiple tabs, manual copying

**Voiceover:**
> "Finance teams spend 40% of their time on manual data entry. Copying invoice data, matching transactions, reconciling statements. It's tedious and error-prone."

---

### Scene 2: Gmail - Invoice Detection (0:15 - 0:50)

**Screen:** Gmail inbox with Clearledgr sidebar

**Action:** 
1. Show inbox with finance emails detected (badges visible)
2. Click Clearledgr toggle to open sidebar
3. Show "4 finance emails detected" with stats

**Voiceover:**
> "Clearledgr embeds AI agents directly in Gmail. When an invoice arrives, our agent automatically detects and extracts the data."

**Action:**
4. Click on AWS invoice email
5. Show sidebar with extracted data:
   - Vendor: Amazon Web Services
   - Amount: $12,847.32
   - Line items breakdown
   - Suggested GL code: 6200 - Technology Expenses

**Voiceover:**
> "AI extracts vendor, amount, line items, and suggests the correct GL category. 97% confidence."

**Action:**
6. Click "Approve & Post"
7. Show success: "Posted to QuickBooks"

---

### Scene 3: Gmail - Bank Reconciliation (0:50 - 1:20)

**Action:**
1. Click on Chase bank statement email
2. Show "Bank Statement Detected" card
3. Click "Import & Reconcile"

**Screen:** Show reconciliation results appearing:
- 47 transactions imported
- 44 auto-matched (93.6%)
- 3 exceptions

**Voiceover:**
> "Bank statements are even more powerful. 47 transactions matched in seconds. 93.6% auto-match rate. Only 3 need human review."

---

### Scene 4: Slack - Real-time Notifications (1:20 - 1:50)

**Screen:** Switch to Slack #finance channel

**Action:**
1. Show notification from Clearledgr:
   ```
   [Clearledgr] Reconciliation Complete
   Matches: 44
   Exceptions: 3
   Match Rate: 93.6%
   ```

2. Show exception notification with buttons:
   ```
   [HIGH] Exception Requires Review
   Vendor: Unknown Transfer
   Amount: $5,234.00
   Type: No matching invoice
   [Resolve] [View in Sheets]
   ```

3. Type `/clearledgr status` to show slash command

**Voiceover:**
> "Your team gets real-time notifications in Slack. Approve entries, resolve exceptions, all without leaving the conversation."

**Action:**
4. Click "Resolve" button
5. Show message update: "[RESOLVED] by @user"

---

### Scene 5: Google Sheets - Full Visibility (1:50 - 2:30)

**Screen:** Switch to Google Sheets

**Action:**
1. Show Clearledgr menu in Sheets
2. Open sidebar showing:
   - Activity feed with agent reasoning
   - Match progress
   - Exceptions list

**Voiceover:**
> "Google Sheets gives your team full visibility. See exactly what the AI did, why it made each decision, and approve or override."

**Action:**
3. Show Exceptions sheet with flagged items
4. Select exception row
5. Click "Resolve" in sidebar
6. Show Vita AI tab

**Action:**
7. Type "What's my reconciliation status?"
8. Show Vita response with stats

**Voiceover:**
> "And Vita, your AI finance assistant, is available everywhere. Ask questions, run reports, take action through natural conversation."

---

### Scene 6: Closing (2:30 - 3:00)

**Screen:** Split view showing all three surfaces

**Voiceover:**
> "Clearledgr. AI agents that work where your finance team works. Gmail, Slack, Sheets. No new platform to learn. No context switching. Just faster, more accurate financial operations."

**Screen:** Clearledgr logo + "clearledgr.com"

---

## Recording Tips

1. **Use a clean demo environment** - Fresh Gmail, Slack workspace, Sheets
2. **Pre-seed all data** before recording
3. **Practice the flow** 2-3 times before recording
4. **Record in 1080p or higher**
5. **Use a quality microphone** for voiceover
6. **Add subtle transitions** between scenes
7. **Keep pace steady** - don't rush

---

## Backup Plans

### If Gmail extension doesn't work:
- Use the standalone `sidebar-demo.html` 
- Open directly in browser at `localhost:8081/sidebar-demo.html`

### If Slack isn't configured:
- Show mock Slack messages in a static image
- Or skip to Sheets demo

### If Sheets add-on fails:
- Show the sidebar HTML directly
- Use pre-recorded footage

---

## Key Messages to Emphasize

1. **Embedded, not another platform** - Works in tools teams already use
2. **AI with human oversight** - High confidence automation, human approval
3. **Multi-surface consistency** - Same data, same AI, everywhere
4. **Real integrations** - QuickBooks, Xero, NetSuite, SAP
5. **Autonomous operation** - Works 24/7, even when you're not there

---

## Files Reference

### Gmail Extension
- `ui/gmail-extension/sidebar.html` - Production sidebar
- `ui/gmail-extension/sidebar-demo.html` - Demo version with mock data
- `ui/gmail-extension/content.js` - Gmail integration
- `ui/gmail-extension/demo-mode.js` - Demo data

### Slack App
- `ui/slack/app.py` - Slack bot handlers
- `ui/slack/manifest.json` - App configuration

### Google Sheets
- `ui/sheets/Code.gs` - Main add-on code
- `ui/sheets/sidebar.html` - Sidebar UI
- `ui/sheets/vita-chat.html` - Vita AI chat

### Backend
- `main.py` - FastAPI server
- `clearledgr/agents/vita.py` - Vita AI agent
- `clearledgr/integrations/erp_router.py` - ERP integrations

---

Good luck!
