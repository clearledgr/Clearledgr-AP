# Clearledgr MVP Scope: "Streak for AP"

> **Single source of truth for V1. Reference this before building anything.**

---

## The Hair-on-Fire Problem

### The Numbers (Source: IFOL 2025 Survey)

| Metric | Stat | Trend |
|--------|------|-------|
| Manual keying invoices into ERP | 66% | Getting WORSE |
| Time on invoice processing | 63% spend 10+ hrs/week | Getting WORSE |
| Staff stress from AP processes | 78% | Getting WORSE |
| Invoices arriving as email attachments | 62% | - |

### The Cost

| Method | Cost per Invoice |
|--------|------------------|
| Manual | $16 |
| Automated | $3 |

- Average team wastes **11.2 hours/week** on manual entry
- Approval cycles: **8-10 days** via email

### The Painful Flow (Today)

```
Invoice lands in inbox
        ↓
Someone manually types it into QuickBooks/NetSuite
        ↓
Approval request sent via email → gets lost/ignored
        ↓
Vendor calls: "Where's my payment?" → scramble to find status
        ↓
Late payment → damaged vendor relationship
```

---

## The Competition Gap

| Tool | Approach | Problem |
|------|----------|---------|
| Bill.com, Ramp | Separate app | Finance has to leave Gmail |
| GetMyInvoices | Gmail extension | Just extracts to external system |
| Invoice Reader AI | Gmail add-on | Just dumps to Sheets |

**Nobody is doing the full workflow inside Gmail.**

---

## Clearledgr V1: The Solution

### Core Value Proposition

> "Streak for AP" - Invoice lands → Auto-extract → One-click approve → Posted → Status visible
> 
> **All without leaving Gmail.**

### The Flow

```
Invoice Email → Auto-Detect → Extract → Route to Slack → Approve → Post to ERP
     ↓              ↓           ↓            ↓              ↓           ↓
   Gmail        Smart Label   AI Parse   Notification    One-Click   QuickBooks
                                         + Exception                 Xero
                                                                     NetSuite
```

---

## V1 Feature Scope

### Gmail Extension

| Feature | Description | Status |
|---------|-------------|--------|
| Smart Labeling | Auto-categorize: Invoice, Receipt, Statement, Payment Confirmation | ✅ |
| Status Tracking | Visual: New → Pending Approval → Approved → Posted | ✅ |
| Data Extraction | Vendor, Amount, Due Date, Invoice # from email/attachment | ✅ |
| Quick Actions | Approve, Flag, Reject right from inbox | ✅ |
| Search/Filter | "Show unpaid invoices over $1000" | ✅ |
| Invoice Pipeline | Streak-style sidebar showing all invoices by status | ✅ |
| PDF Extraction | Claude Vision for invoice PDFs/images | ✅ Wired |
| 24/7 Processing | Gmail API + Pub/Sub for autonomous detection | ⚠️ Code exists, not deployed |

### Slack Integration

| Feature | Description | Status |
|---------|-------------|--------|
| Approval Requests | Invoice drops in #finance-approvals with Approve/Reject buttons | ✅ |
| Exception Alerts | "Duplicate detected" / "Missing PO" / "Amount mismatch" | ✅ |
| Quick Approve | One-click from Slack, no context switching | ✅ |
| Thread History | Discussion stays with the invoice | ✅ |
| Expense Requests | "I spent $30 on lunch" → Extract → Route for approval | ✅ |

### ERP Connection

| ERP | Bills | Vendors | OAuth | Status |
|-----|-------|---------|-------|--------|
| QuickBooks Online | ✅ | ✅ | ✅ | Full |
| Xero | ✅ | ✅ | ✅ | Full |
| NetSuite | ✅ | ✅ | ✅ | Full |

### Recurring Subscriptions

| Scenario | Action |
|----------|--------|
| Same amount as last month | ✅ Auto-approve & post |
| Amount changed <5% | ✅ Auto-approve |
| Amount changed 5-20% | ⚠️ Send for review with alert |
| Amount changed >20% | 🚨 Send for review, flag significant change |

**Pre-configured vendors:** AWS, GCP, Azure, GitHub, Stripe, Slack, Notion, Salesforce, HubSpot, Zendesk, Datadog, etc. (50+)

### Learning / Feedback Loop

| Feature | Description | Status |
|---------|-------------|--------|
| Vendor → GL Mapping | Learn "Stripe always goes to GL 6150" | ✅ Wired |
| Confidence from History | Higher confidence for known vendors | ✅ Wired |
| Correction Learning | If user changes GL, remember for next time | ⚠️ UI not done |

---

## NOT in V1 (V2 Scope)

| Feature | Why Not V1 |
|---------|------------|
| Bank Reconciliation | Different problem, different workflow |
| Google Sheets Integration | Nice-to-have, not hair-on-fire |
| Three-way PO Matching | Enterprise feature |
| Multi-currency | Complexity, limited initial market |
| Bank Feed APIs (Okra, TrueLayer) | Needed for reconciliation (V2) |

---

## Success Metrics

### For Users

| Metric | Target |
|--------|--------|
| Invoice processing time | < 30 seconds (from 10+ minutes) |
| Approval cycle | < 1 day (from 8-10 days) |
| Manual data entry | 0% (from 66%) |

### For Business

| Metric | Target |
|--------|--------|
| Cost per invoice | $3 (from $16) |
| Finance team time saved | 10+ hours/week |

---

## Technical Requirements

### Must Have for V1 Launch

1. **Gmail API + Pub/Sub** - 24/7 autonomous processing (not just when browser open)
2. **Claude Vision** - Real PDF/image extraction (not just email text)
3. **Learning Service** - Wire into approval flow (call `record_approval()`)
4. **ERP Posting** - Bills, not journal entries

### Environment Variables Required

```bash
# Gmail API
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
PUBSUB_TOPIC=projects/clearledgr/topics/gmail-push

# AI
ANTHROPIC_API_KEY=  # Required for PDF extraction

# Slack
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=

# ERPs (user provides during onboarding)
QUICKBOOKS_CLIENT_ID=
QUICKBOOKS_CLIENT_SECRET=
XERO_CLIENT_ID=
XERO_CLIENT_SECRET=
NETSUITE_ACCOUNT_ID=
NETSUITE_CONSUMER_KEY=
NETSUITE_CONSUMER_SECRET=
NETSUITE_TOKEN_ID=
NETSUITE_TOKEN_SECRET=
```

---

## Key Differentiators

| Differentiator | Implementation |
|----------------|----------------|
| **Audit-Link Generation** | Unique `Clearledgr_Audit_ID` in ERP memo, traces back to email |
| **Human-in-the-Loop** | If confidence < 95%, show "Review" not "Post" |
| **Multi-System Routing** | Approve in Gmail → Post to ERP + Update Slack thread |

---

## User Personas

### Primary: Finance Manager at Growing Company

- 50-500 employees
- Processing 50-500 invoices/month
- Using QuickBooks/Xero/NetSuite
- Team uses Gmail + Slack
- Pain: Manual entry, lost approvals, vendor complaints

### Secondary: CFO

- Wants visibility into AP status
- Needs audit trail
- Cares about: cost savings, team productivity, compliance

---

## V1 Launch Checklist

- [ ] Gmail API + Pub/Sub deployed and working
- [x] Claude Vision wired into extraction flow
- [x] Learning service called on every approval
- [x] ERP posting creates Bills (not journal entries)
- [ ] Slack approval flow end-to-end tested
- [x] Recurring subscription detection working
- [x] Expense request flow tested
- [x] Audit trail complete (email → ERP linkable)

---

## Reference Files

| File | Purpose |
|------|---------|
| `clearledgr/services/invoice_workflow.py` | Main workflow orchestrator |
| `clearledgr/services/recurring_detection.py` | Subscription handling |
| `clearledgr/services/expense_workflow.py` | Slack expense requests |
| `clearledgr/services/learning.py` | Vendor→GL learning |
| `clearledgr/services/llm_multimodal.py` | Claude Vision extraction |
| `clearledgr/api/gmail_extension.py` | Extension API endpoints |
| `clearledgr/integrations/erp_router.py` | ERP posting (Bills + Vendors) |
| `ui/gmail-extension/` | Chrome extension |
| `ui/slack/app.py` | Slack bot |

---

## What We're NOT Building

1. **NOT a dashboard** - Everything happens in Gmail/Slack
2. **NOT batch processing** - Real-time, event-driven
3. **NOT a replacement** - Enhances existing tools
4. **NOT reconciliation (V1)** - That's V2

---

*Last updated: January 2026*
*Version: 1.0*
