# Clearledgr Demo Script (3 Minutes)

## Overview
This demo shows Clearledgr's AI agents working inside Gmail to automate finance workflows end-to-end.

---

## Pre-Demo Setup

1. Open Chrome with the Clearledgr extension installed
2. Navigate to Gmail (any Gmail account works)
3. Open `sidebar-demo.html` directly in browser OR inject it via the extension
4. Have the sidebar visible on the right side

**To test locally:**
```bash
cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension
open sidebar-demo.html
```

---

## Demo Flow (3 minutes)

### Scene 1: The Problem (0:00 - 0:20)
**Voiceover:**
> "Finance teams spend 40% of their time on manual data entry. Copying invoice data from emails, categorizing transactions, reconciling bank statements. It's tedious, error-prone, and expensive."

**On screen:** Show Gmail inbox with finance emails visible

---

### Scene 2: Clearledgr Introduction (0:20 - 0:40)
**Voiceover:**
> "Clearledgr embeds AI agents directly in the tools finance teams already use. No new platform to learn. No context switching."

**Action:** Click the Clearledgr toggle button to open sidebar

**On screen:** Sidebar opens showing:
- 6 finance emails detected
- 4 pending, 2 processed
- $62K total value

---

### Scene 3: Invoice Processing (0:40 - 1:30)
**Voiceover:**
> "When an invoice email arrives, Clearledgr automatically extracts all the data."

**Action:** Click on the AWS invoice card to expand it

**On screen:** Show the extracted data:
- Vendor: Amazon Web Services
- Invoice #: INV-2024-0892
- Amount: $12,847.32
- Line items breakdown
- AI suggested GL code: 6200 - Technology Expenses

**Voiceover:**
> "Our AI extracts vendor details, line items, and suggests the correct GL category with 97% confidence. One click to approve and post directly to QuickBooks."

**Action:** Click "Approve & Post" button

**On screen:** 
- Button shows spinner "Processing..."
- Changes to green checkmark "Posted to QuickBooks"
- Toast notification appears

---

### Scene 4: Bank Reconciliation (1:30 - 2:15)
**Voiceover:**
> "Bank reconciliation is even more powerful. When a statement arrives, Clearledgr imports and matches transactions automatically."

**Action:** Click on the Chase Bank Statement card to expand

**On screen:** Show statement details:
- Account ending 4892
- 47 transactions
- Opening/closing balances

**Action:** Click "Import & Reconcile" button

**On screen:**
- Button shows spinner "Reconciling..."
- Results appear: 44 matched, 3 exceptions, 93.6% match rate
- Button changes to green "Reconciled"

**Voiceover:**
> "47 transactions matched in seconds. 93.6% auto-matched. Only 3 exceptions need human review."

---

### Scene 5: Vita AI Assistant (2:15 - 2:45)
**Voiceover:**
> "And when you need help, Vita is your AI finance expert."

**Action:** Click on "Vita AI" tab

**On screen:** Chat interface with Vita's greeting

**Action:** Click "Post to QuickBooks" suggestion

**On screen:** Vita responds with:
- Posting 4 invoices
- Success confirmations
- Total: $18,051.71 added to AP

**Voiceover:**
> "Vita doesn't just answer questions. She takes action. Post invoices, run reconciliations, generate reports. All through natural conversation."

---

### Scene 6: Closing (2:45 - 3:00)
**Voiceover:**
> "Clearledgr. AI agents that work where your finance team works. Slash your close time from weeks to hours."

**On screen:** 
- Show the updated stats (more processed, fewer pending)
- Clearledgr logo

---

## Key Talking Points

1. **Embedded, not another platform** - Works inside Gmail, Sheets, Slack
2. **AI extraction with high confidence** - 96-99% accuracy on invoice data
3. **One-click approval** - Human in the loop, but minimal friction
4. **Automatic reconciliation** - 90%+ match rate on bank statements
5. **Conversational AI** - Vita takes action, not just answers

---

## Demo Tips

- **Pace yourself** - Don't rush through the UI interactions
- **Pause on results** - Let viewers see the extracted data and match rates
- **Show the transformation** - Before (pending) → After (processed)
- **Emphasize the "no new platform"** - This is a key differentiator

---

## Backup Flows

If something doesn't work as expected:

1. **Sidebar won't open:** Open `sidebar-demo.html` directly in browser
2. **Animations stuck:** Refresh the page, demo data resets
3. **Chat not responding:** The demo uses hardcoded responses, just click suggestions

---

## Recording Tips

1. Use a clean Gmail account or blur sensitive emails
2. Record at 1080p or higher
3. Use a quality microphone for voiceover
4. Consider adding subtle background music
5. Add captions for accessibility

---

## Files

- `sidebar-demo.html` - The demo sidebar UI
- `demo-mode.js` - Demo data and mock responses
- `icons/` - Clearledgr logo assets

Good luck!
