# Clearledgr Embedded Ecosystem

## Summary

Clearledgr embeds autonomous finance agents inside the tools finance teams already use. The system combines **server-side Gmail API access** for 24/7 autonomous processing with **in-app UI surfaces** for zero context switching.

**Key Innovation**: Work happens in the background. Users see results, not processes.

## Architecture Overview

```
AUTONOMOUS BACKEND (Always Running - 24/7)
├── Gmail API + Pub/Sub → Real-time email notifications
├── Classification Engine → LLM-powered finance email detection
├── Bank Statement Parser → CSV/PDF extraction
├── Reconciliation Engine → 3-way matching (Bank ↔ Gateway ↔ Internal)
├── Pattern Learning → Improves from corrections
└── ERP Integration → NetSuite, SAP, Xero, QuickBooks
        │
        │ Results stored, ready for user
        ▼
EMBEDDED UI SURFACES (Thin Clients)
├── Gmail Extension → "While you were away" summary + approvals
├── Google Sheets → Reconciliation workspace + Vita AI
├── Slack App → Notifications + approvals
└── (Future) Outlook, Teams, Excel
```

## How It Works

### 1. Autonomous Email Processing

```
3:47 AM   Bank sends statement to user's Gmail
          ↓
          Google Pub/Sub notifies Clearledgr backend
          ↓
          Backend fetches email via Gmail API
          ↓
          LLM classifies as "bank_statement" (97% confidence)
          ↓
          Parser extracts 47 transactions from PDF
          ↓
          Reconciliation matches 44, flags 3 exceptions
          ↓
          Results stored in database

9:00 AM   User opens Gmail
          ↓
          Extension fetches summary from backend
          ↓
          Sidebar: "While you were away: 44 matched, 3 need review"
          ↓
          User reviews exceptions, clicks "Approve"
          ↓
          Backend posts to SAP
          ↓
          Done. Total time: 2 minutes.
```

### 2. Event-Driven Architecture

All processing is triggered by events, not schedules:

| Event | Trigger | Action |
|-------|---------|--------|
| `gmail.message.received` | Pub/Sub webhook | Fetch and classify email |
| `bank_statement.detected` | Classification result | Parse attachment |
| `gateway.webhook.received` | Paystack/Stripe | Store settlement data |
| `reconciliation.triggered` | Statement parsed | Run 3-way matching |
| `exception.created` | Match failed | Notify via Slack |
| `draft.approved` | User action | Post to ERP |

### 3. Thin Client Surfaces

All UI surfaces are **display layers only**. They:
- Fetch results from backend
- Display summaries and details
- Send user actions (approve, reject, resolve)
- Never process data locally

## Inbound Data Sources

| Source | Integration Method | Data Type |
|--------|-------------------|-----------|
| Gmail | API + Pub/Sub (24/7) | Bank statements, invoices, receipts |
| Paystack | Webhooks | Settlement notifications |
| Stripe | Webhooks | Payout notifications |
| Flutterwave | Webhooks | Transaction notifications |
| NetSuite/SAP | REST API | GL entries, vendors |
| Google Sheets | Apps Script | Manual transaction data |

## Embedded Outputs

| Surface | Purpose | Key Features |
|---------|---------|--------------|
| Gmail Extension | Review and approve | Summary view, exception review, Vita AI |
| Google Sheets | Reconciliation workspace | Live results, dashboards, formulas |
| Slack | Team notifications | Alerts, approvals, slash commands |

## Authentication Model

### User Authentication (Google Identity)
- User installs Chrome extension
- Extension uses `chrome.identity.getAuthToken()`
- Backend verifies Google ID token, issues Clearledgr JWT
- No separate Clearledgr login required

### Gmail API Authorization (Separate)
- User clicks "Enable Autopilot" in extension
- OAuth flow with Gmail read scopes
- Backend stores refresh token securely
- Enables 24/7 autonomous processing

## Phased Roadmap

### Phase 1 (Current - V1)
- Gmail Extension with autonomous processing
- Google Sheets reconciliation workspace
- Slack notifications and approvals
- Paystack, Stripe, Flutterwave integrations
- NetSuite, SAP, Xero, QuickBooks ERP posting

### Phase 2
- Outlook extension (same architecture)
- Microsoft Teams app
- Excel add-in

### Phase 3
- Multi-currency support
- Advanced ML models
- Custom ERP adapters

### Phase 4
- Expense system integrations
- Payment platform expansions
- White-label capabilities

## Codebase Mapping

```
clearledgr/
├── core/
│   ├── engine.py              # Central orchestrator
│   ├── database.py            # Unified data store
│   └── event_bus.py           # Event-driven architecture
│
├── services/
│   ├── gmail_api.py           # Gmail API client
│   ├── gmail_watch.py         # Pub/Sub subscription
│   ├── bank_statement_parser.py
│   ├── multi_factor_scoring.py
│   └── ai_enhanced.py         # LLM classification
│
├── api/
│   ├── gmail_webhooks.py      # Pub/Sub handler
│   ├── webhooks.py            # Payment gateway webhooks
│   └── engine.py              # Core API
│
└── integrations/
    └── erp_router.py          # ERP connections

ui/
├── gmail-extension/           # Chrome extension (thin client)
├── sheets/                    # Google Sheets add-on
└── slack/                     # Slack app
```

## Design Principles

1. **Autonomous First**: Backend processes without user intervention
2. **Embed-First UX**: No new dashboard, no context switching
3. **Event-Driven**: Immediate processing, no batch delays
4. **Thin Clients**: UI surfaces display results, don't process data
5. **Human in the Loop**: User approves exceptions, maintains control
6. **Auditability**: Every action logged with context

## Key Metrics

| Metric | Target |
|--------|--------|
| Email detection latency | < 30 seconds |
| Statement parsing time | < 5 seconds |
| Reconciliation accuracy | > 95% |
| User review time | < 2 minutes |
| End-to-end (email → ERP) | < 10 minutes |

## Security

- OAuth tokens encrypted at rest
- Minimal Gmail scopes (read-only)
- JWT with short expiry
- Complete audit trail
- Rate limiting per user
- Input validation on all endpoints
