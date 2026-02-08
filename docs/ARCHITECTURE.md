# Clearledgr Architecture

## Overview

Clearledgr is an **autonomous finance agent** that embeds directly into tools finance teams already use. It combines the best of two proven approaches:

1. **Server-side Model**: Server-side Gmail API access for 24/7 autonomous processing
2. **Embedded Model**: Browser extension for in-Gmail UI with zero context switching

The result: **True autopilot** that processes finance data from all sources 24/7, with results surfaced in the tools finance teams already use.

---

## Complete Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           ALL INBOUND DATA SOURCES                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │
│   │   GMAIL     │  │  PAYMENT    │  │    ERP      │  │   BANK      │           │
│   │  Pub/Sub    │  │  GATEWAYS   │  │  SYSTEMS    │  │   FEEDS     │           │
│   │             │  │             │  │             │  │             │           │
│   │ Statements  │  │ Stripe      │  │ QuickBooks  │  │ Plaid (US)  │           │
│   │ Invoices    │  │ Paystack    │  │ Xero        │  │ TrueLayer   │           │
│   │ Receipts    │  │ Flutterwave │  │ NetSuite    │  │  (UK)       │           │
│   │             │  │             │  │ SAP         │  │ Tink (EU)   │           │
│   │ Africa: no  │  │             │  │             │  │             │           │
│   │ open banking│  │             │  │             │  │ Africa: via │           │
│   │ → use email │  │             │  │             │  │ Gmail/Email │           │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘           │
│          │                │                │                │                   │
│          │   Webhooks     │   Webhooks     │   Webhooks     │   Webhooks       │
│          │                │                │                │                   │
│          └────────────────┴────────────────┴────────────────┘                   │
│                                    │                                            │
│                                    ▼                                            │
│   ┌─────────────────────────────────────────────────────────────────────────┐  │
│   │                         EVENT BUS                                       │  │
│   │                                                                         │  │
│   │   gmail.email.received  │  gateway.settled  │  erp.gl.updated  │  ...   │  │
│   └─────────────────────────────────────────────────────────────────────────┘  │
│                                    │                                            │
│                                    ▼                                            │
│   ┌─────────────────────────────────────────────────────────────────────────┐  │
│   │                    CLEARLEDGR ENGINE (24/7)                             │  │
│   │                                                                         │  │
│   │   Classify → Parse → Match → Categorize → Generate JE → Store          │  │
│   │                                                                         │  │
│   │   ┌─────────────────────────────────────────────────────────────────┐  │  │
│   │   │                    REAL-TIME SYNC                               │  │  │
│   │   │   Push updates to all surfaces when data changes                │  │  │
│   │   └─────────────────────────────────────────────────────────────────┘  │  │
│   └─────────────────────────────────────────────────────────────────────────┘  │
│                                    │                                            │
│                                    ▼                                            │
│   ┌─────────────────────────────────────────────────────────────────────────┐  │
│   │                      THIN CLIENT SURFACES                               │  │
│   │                                                                         │  │
│   │   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │  │
│   │   │  Gmail   │  │  Sheets  │  │  Slack   │  │ Outlook  │  │  Teams   │ │  │
│   │   │Extension │  │  Add-on  │  │   App    │  │Extension │  │   App    │ │  │
│   │   │          │  │          │  │          │  │ (Future) │  │ (Future) │ │  │
│   │   └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘ │  │
│   │                                                                         │  │
│   │   All surfaces display results and send actions - no local processing  │  │
│   └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLEARLEDGR SYSTEM                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                         USER'S GMAIL                                │  │
│   │                                                                     │  │
│   │   Bank sends statement → arrives in inbox → triggers push          │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│                                    │                                        │
│                                    │ Gmail API Watch                        │
│                                    ▼                                        │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                    GOOGLE CLOUD PUB/SUB                             │  │
│   │                                                                     │  │
│   │   Receives notification for every new email in watched inbox       │  │
│   │   Pushes to Clearledgr webhook endpoint                            │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│                                    │                                        │
│                                    │ HTTPS webhook                          │
│                                    ▼                                        │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                      CLEARLEDGR BACKEND                             │  │
│   │                                                                     │  │
│   │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                │  │
│   │   │ Gmail Push  │  │ Reconcile   │  │ ERP Post    │                │  │
│   │   │ Handler     │→ │ Engine      │→ │ Service     │                │  │
│   │   └─────────────┘  └─────────────┘  └─────────────┘                │  │
│   │          │                │                │                        │  │
│   │          ▼                ▼                ▼                        │  │
│   │   ┌─────────────────────────────────────────────────────────────┐  │  │
│   │   │                    UNIFIED DATA STORE                       │  │  │
│   │   │   • Finance emails detected                                 │  │  │
│   │   │   • Transactions parsed                                     │  │  │
│   │   │   • Matches found                                           │  │  │
│   │   │   • Exceptions flagged                                      │  │  │
│   │   │   • Draft journal entries                                   │  │  │
│   │   │   • Audit trail                                             │  │  │
│   │   └─────────────────────────────────────────────────────────────┘  │  │
│   │                                                                     │  │
│   │   Works 24/7 - processes emails even when user is offline          │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│                                    │                                        │
│                                    │ REST API                               │
│                                    ▼                                        │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                    EMBEDDED UI SURFACES                             │  │
│   │                                                                     │  │
│   │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐    │  │
│   │   │ Gmail Extension │  │ Google Sheets   │  │ Slack App       │    │  │
│   │   │                 │  │                 │  │                 │    │  │
│   │   │ • Shows summary │  │ • Reconciliation│  │ • Notifications │    │  │
│   │   │ • Review/approve│  │ • Dashboards    │  │ • Approvals     │    │  │
│   │   │ • Vita AI chat  │  │ • Vita AI chat  │  │ • Vita AI chat  │    │  │
│   │   └─────────────────┘  └─────────────────┘  └─────────────────┘    │  │
│   │                                                                     │  │
│   │   All surfaces are thin clients - display results, send actions    │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Gmail API + Pub/Sub Integration

**Purpose**: Enable 24/7 autonomous email processing without requiring the browser to be open.

**How it works**:
1. User authorizes Clearledgr via OAuth (one-time)
2. Backend calls `gmail.users.watch()` to subscribe to inbox changes
3. Google Cloud Pub/Sub sends webhook to our backend on every new email
4. Backend fetches email via Gmail API, processes autonomously
5. Results stored in database, ready when user opens Gmail

**Key endpoints**:
- `POST /webhooks/gmail-push` - Receives Pub/Sub notifications
- `POST /auth/gmail/authorize` - Initiates OAuth flow
- `GET /auth/gmail/callback` - Handles OAuth callback

**Files**:
- `clearledgr/services/gmail_api.py` - Gmail API client
- `clearledgr/api/gmail_webhooks.py` - Push notification handler
- `clearledgr/services/gmail_watch.py` - Watch subscription manager

### 2. Clearledgr Backend (Core Engine)

**Purpose**: Central processing engine that handles all business logic.

**Responsibilities**:
- Email classification (LLM-powered)
- Bank statement parsing (CSV/PDF)
- Transaction matching (multi-factor scoring)
- Exception detection and routing
- Journal entry generation
- ERP posting (NetSuite, SAP, Xero, QuickBooks)
- Audit trail maintenance

**Key modules**:
```
clearledgr/
├── core/
│   ├── engine.py          # Central orchestrator
│   ├── database.py        # Unified data store
│   ├── models.py          # Transaction, Match, Exception models
│   ├── auth.py            # JWT + Google Identity auth
│   └── event_bus.py       # Event-driven architecture
├── services/
│   ├── multi_factor_scoring.py    # 100-point matching algorithm
│   ├── bank_statement_parser.py   # CSV/PDF parsing
│   ├── ai_enhanced.py             # LLM categorization
│   └── pattern_learning.py        # Learns from corrections
├── integrations/
│   ├── erp_router.py      # NetSuite, SAP, Xero, QuickBooks
│   └── oauth.py           # ERP OAuth flows
└── api/
    ├── engine.py          # Core API endpoints
    ├── webhooks.py        # Payment gateway webhooks
    └── gmail_webhooks.py  # Gmail push notifications
```

### 3. Chrome Extension (Gmail UI)

**Purpose**: In-Gmail interface following the "stay in your inbox" philosophy.

**Key features**:
- Sidebar showing processed results
- "While you were away" summary
- One-click approve/reject
- Vita AI conversational agent
- Exception review interface

**Architecture**:
```
ui/gmail-extension/
├── manifest.json          # Chrome extension manifest (v3)
├── background.js          # Service worker (API calls)
├── content.js             # Gmail DOM interaction
├── sidebar.html/js        # Main UI
└── icons/                 # Clearledgr branding
```

**Key principle**: Extension is a **display layer only**. All processing happens on backend.

### 4. Google Sheets Add-on

**Purpose**: Reconciliation workspace embedded in Sheets.

**Features**:
- Live reconciliation results
- Exception review
- Draft journal entry approval
- Vita AI assistant
- Custom formulas (CLEARLEDGR_MATCH, etc.)

### 5. Slack App

**Purpose**: Notifications and approvals in team communication.

**Features**:
- Exception alerts
- Approval workflows
- Slash commands (/clearledgr status)
- Vita AI in Slack

### 6. Payment Gateway Webhooks

**Supported Gateways**:
| Gateway | Webhook Endpoint | Events |
|---------|-----------------|--------|
| Stripe | `POST /webhooks/stripe` | payout.paid, charge.succeeded |
| Paystack | `POST /webhooks/paystack` | transfer.success, charge.success |
| Flutterwave | `POST /webhooks/flutterwave` | transfer.completed, charge.completed |

**Flow**: Gateway settles payout → Webhook fires → Clearledgr matches to bank statement

### 7. ERP Webhooks

**Supported ERPs**:
| ERP | Webhook Endpoint | Events | Notes |
|-----|-----------------|--------|-------|
| QuickBooks | `POST /webhooks/quickbooks` | Account, JournalEntry, BankTransaction | Native webhooks |
| Xero | `POST /webhooks/xero` | INVOICE, BANKSTATEMENT, ACCOUNT | Native webhooks |
| NetSuite | `POST /webhooks/netsuite` | account, journalentry, vendorbill, customerpayment | Via SuiteScript RESTlet |
| SAP | `POST /webhooks/sap` | GLAccount, JournalEntry, SupplierInvoice, Payment | Via Event Mesh or ABAP |

**Flow**: GL account changes in ERP → Webhook fires → Clearledgr syncs mappings

**NetSuite Setup**: Create a SuiteScript RESTlet that calls Clearledgr webhook on record changes
**SAP Setup**: Configure SAP Event Mesh or create custom ABAP program to call webhook

### 8. Bank Data Sources

**By Region**:

| Region | Provider | Webhook Endpoint |
|--------|----------|------------------|
| US, Canada | Plaid | `POST /webhooks/plaid` |
| UK | TrueLayer, Yapily | `POST /webhooks/truelayer` |
| EU | Tink, Nordigen | `POST /webhooks/tink` |
| Africa | Email statements | Gmail Pub/Sub (no open banking) |

**Notes**:
- **UK/EU**: PSD2 regulation enables open banking. Multiple providers compete.
- **Africa**: No open banking infrastructure. Banks email PDF/CSV statements to finance teams. Gmail integration parses these automatically.

**Flow**: 
- **Open banking regions**: Bank transaction → Provider webhook → Clearledgr matches
- **Africa**: Bank sends email → Gmail Pub/Sub → Parse statement → Clearledgr matches

---

## Data Flow: Autonomous Email Processing

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        AUTONOMOUS PROCESSING FLOW                        │
└──────────────────────────────────────────────────────────────────────────┘

3:47 AM - Bank sends statement email
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ GMAIL INBOX                                                             │
│ From: statements@bank.com                                               │
│ Subject: Your January 2026 Statement                                    │
│ Attachment: statement_jan_2026.pdf                                      │
└─────────────────────────────────────────────────────────────────────────┘
          │
          │ Gmail API Watch triggers
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ GOOGLE CLOUD PUB/SUB                                                    │
│ Message: { historyId: 12345, emailAddress: "finance@company.com" }      │
└─────────────────────────────────────────────────────────────────────────┘
          │
          │ Webhook POST to /webhooks/gmail-push
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ CLEARLEDGR BACKEND                                                      │
│                                                                         │
│ 1. Fetch email via Gmail API                                            │
│ 2. Classify with LLM → "bank_statement" (confidence: 0.97)              │
│ 3. Download PDF attachment                                              │
│ 4. Parse transactions (47 found)                                        │
│ 5. Fetch Paystack settlements via API                                   │
│ 6. Run 3-way matching:                                                  │
│    - Bank ↔ Paystack: 45 matched                                        │
│    - Paystack ↔ Internal: 44 matched                                    │
│    - Exceptions: 3 flagged                                              │
│ 7. Generate draft journal entries                                       │
│ 8. Store results in database                                            │
│ 9. Send Slack notification (optional)                                   │
└─────────────────────────────────────────────────────────────────────────┘
          │
          │ Results stored, ready for user
          ▼
9:00 AM - User opens Gmail
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ CHROME EXTENSION                                                        │
│                                                                         │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │  Good morning! While you were away:                                 │ │
│ │                                                                     │ │
│ │  ✓ 1 bank statement processed                                       │ │
│ │  ✓ 47 transactions parsed                                           │ │
│ │  ✓ 44 transactions matched (€127,500)                               │ │
│ │  Warning 3 exceptions need review                                         │ │
│ │                                                                     │ │
│ │  [Review Exceptions]  [Approve All]  [Post to SAP]                  │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
          │
          │ User clicks "Approve All"
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ BACKEND → ERP                                                           │
│                                                                         │
│ POST journal entries to SAP/NetSuite                                    │
│ Response: { posted: 44, reference: "JE-2026-0147" }                     │
└─────────────────────────────────────────────────────────────────────────┘
          │
          ▼
Done. Total user time: 30 seconds.
```

---

## Authentication Architecture

### User Authentication Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      GOOGLE IDENTITY AUTHENTICATION                     │
└─────────────────────────────────────────────────────────────────────────┘

User installs Chrome extension
          │
          ▼
Extension calls chrome.identity.getAuthToken()
          │
          │ Google OAuth popup (if needed)
          ▼
Google returns ID token
          │
          │ POST /auth/google-login { id_token }
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ CLEARLEDGR BACKEND                                                      │
│                                                                         │
│ 1. Verify Google ID token                                               │
│ 2. Extract email, name, google_id                                       │
│ 3. Create or find user in database                                      │
│ 4. Generate Clearledgr JWT                                              │
│ 5. Return { access_token, refresh_token, user }                         │
└─────────────────────────────────────────────────────────────────────────┘
          │
          ▼
Extension stores JWT, uses for all API calls
```

### Gmail API Authorization (Separate from User Auth)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      GMAIL API AUTHORIZATION                            │
└─────────────────────────────────────────────────────────────────────────┘

User clicks "Enable Autopilot" in extension
          │
          ▼
Extension opens /auth/gmail/authorize
          │
          │ OAuth 2.0 flow with Gmail scopes
          ▼
Google consent screen:
  "Clearledgr wants to:
   - Read your emails
   - Manage labels"
          │
          │ User clicks "Allow"
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ CLEARLEDGR BACKEND                                                      │
│                                                                         │
│ 1. Exchange code for tokens                                             │
│ 2. Store refresh_token securely (encrypted)                             │
│ 3. Call gmail.users.watch() to subscribe                                │
│ 4. Store historyId for delta sync                                       │
│ 5. Return success                                                       │
└─────────────────────────────────────────────────────────────────────────┘
          │
          ▼
Autopilot enabled - backend now receives push notifications
```

---

## Event-Driven Architecture

All processing is event-driven, not scheduled:

```python
# Event types
EVENTS = {
    "gmail.message.received": "New email in inbox",
    "bank_statement.detected": "Finance email classified",
    "bank_statement.parsed": "Transactions extracted",
    "gateway.webhook.received": "Paystack/Stripe webhook",
    "match.found": "Transaction matched",
    "exception.created": "Exception needs review",
    "draft.approved": "Journal entry approved",
    "erp.posted": "Posted to ERP"
}

# Event flow
gmail.message.received
    → classify_email()
    → bank_statement.detected
        → parse_attachment()
        → bank_statement.parsed
            → run_reconciliation()
            → match.found (x44)
            → exception.created (x3)
                → notify_slack()
```

---

## Security Considerations

1. **OAuth tokens**: Encrypted at rest, never exposed to frontend
2. **Gmail access**: Minimal scopes (read-only where possible)
3. **JWT authentication**: Short-lived access tokens, refresh rotation
4. **Audit trail**: Every action logged with user, timestamp, context
5. **Rate limiting**: Token bucket per user/endpoint
6. **Input validation**: Pydantic models, sanitization

---

## Deployment Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PRODUCTION DEPLOYMENT                           │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Chrome Extension│     │ Google Sheets   │     │ Slack App       │
│ (Chrome Store)  │     │ (Workspace Mkt) │     │ (Slack Dir)     │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │      LOAD BALANCER      │
                    │   (Railway/Render/AWS)  │
                    └────────────┬────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
           ┌────────▼────────┐     ┌─────────▼────────┐
           │ API Server 1    │     │ API Server 2     │
           │ (FastAPI)       │     │ (FastAPI)        │
           └────────┬────────┘     └─────────┬────────┘
                    │                         │
                    └────────────┬────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
     ┌────────▼────────┐ ┌──────▼──────┐ ┌────────▼────────┐
     │ PostgreSQL      │ │ Redis       │ │ Google Cloud    │
     │ (TimescaleDB)   │ │ (Cache)     │ │ Pub/Sub         │
     └─────────────────┘ └─────────────┘ └─────────────────┘
```

---

## File Structure

```
clearledgr/
├── core/                      # Core engine
│   ├── engine.py              # Central orchestrator
│   ├── database.py            # Data persistence
│   ├── models.py              # Pydantic models
│   ├── auth.py                # Authentication
│   ├── event_bus.py           # Event system
│   └── org_config.py          # Per-org settings
│
├── services/                  # Business logic
│   ├── gmail_api.py           # Gmail API client [NEW]
│   ├── gmail_watch.py         # Watch subscription [NEW]
│   ├── bank_statement_parser.py
│   ├── multi_factor_scoring.py
│   ├── ai_enhanced.py
│   ├── pattern_learning.py
│   ├── paystack_client.py
│   ├── stripe_client.py
│   └── flutterwave_client.py
│
├── api/                       # REST endpoints
│   ├── engine.py              # Core API
│   ├── gmail_webhooks.py      # Gmail push [NEW]
│   ├── webhooks.py            # Payment webhooks
│   ├── auth.py                # Auth endpoints
│   └── onboarding.py          # Setup flow
│
├── integrations/              # External systems
│   ├── erp_router.py          # ERP connections
│   └── oauth.py               # OAuth flows
│
└── agents/                    # AI agents
    ├── vita.py                # Conversational agent
    └── finance_expert.py      # Domain expertise

ui/
├── gmail-extension/           # Chrome extension
│   ├── manifest.json
│   ├── background.js
│   ├── content.js
│   ├── sidebar.html/js
│   └── icons/
│
├── sheets/                    # Google Sheets add-on
│   ├── Code.gs
│   └── sidebar.html
│
└── slack/                     # Slack app
    └── app.py
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Gmail API + Pub/Sub | True 24/7 autonomy (server-side model) |
| Browser extension for UI | Stay in inbox, no context switch (embedded model) |
| Backend does all processing | Extension is thin client, works offline |
| Event-driven, not scheduled | Immediate processing, no batch delays |
| Google Identity for auth | One-click, no separate Clearledgr login |
| Separate Gmail OAuth | Explicit consent for email access |

---

## References

- Extension-based inbox enhancement model
- [Gmail API Push Notifications](https://developers.google.com/gmail/api/guides/push)
- [Google Cloud Pub/Sub](https://cloud.google.com/pubsub/docs)
