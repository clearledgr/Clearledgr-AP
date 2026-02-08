# Clearledgr Email Integration (Autonomous)

Clearledgr **automatically** processes finance emails in Gmail and Outlook. When invoices, payment gateway settlements, or payment confirmations arrive, Clearledgr processes them autonomously.

## How It Works

```
Email arrives (invoice, settlement, payment)
         ↓
Clearledgr auto-detects & parses
         ↓
Auto-categorizes to GL account
         ↓
Auto-matches to bank/internal records
         ↓
High confidence? → Auto-approved, written to Sheets
Low confidence?  → Exception surfaced for review
         ↓
User reviews ONLY exceptions (not every email)
```

**You don't click anything.** Clearledgr processes emails on a schedule (every 15 minutes) or when you open an email.

## Overview

### How It Appears

- **Side Panel**: Opens alongside any email thread (shows auto-processing status)
- **Inline Actions**: Quick action buttons for exception review only
- **Notifications**: Slack/Teams alerts when exceptions need attention

### Key Capabilities

1. **Autonomous Processing**
   - Runs automatically every 15 minutes (Gmail) or on email open (Outlook)
   - No manual triggering needed
   - Processes invoices, settlements, payment confirmations

2. **Auto-Categorization**
   - Classifies transactions to GL accounts automatically
   - Uses vendor patterns, keywords, historical learning
   - 70%+ auto-categorization rate for established patterns

3. **Auto-Matching**
   - 3-way/2-way matching to bank and internal transactions
   - 90-95% auto-match target
   - High-confidence matches auto-approved

4. **Exception-Only Review**
   - Only low-confidence items surfaced
   - Unmatched invoices per vendor
   - One-click resolution from inbox

5. **Autonomous Follow-ups**
   - Auto-creates tasks for unresolved items
   - Sends reminders via Slack/Teams
   - Escalates overdue items

5. **Audit Trail**
   - Who, what, when for every action
   - Complete email-to-ledger traceability
   - Written to `CL_AUDIT_LOG` tab in your spreadsheet
   - Also stored in backend for API queries
   - Compliance-ready documentation

## API Endpoints

### Email Parsing

```
POST /email/parse
```

Parse an email and extract financial data.

**Request:**
```json
{
  "subject": "Invoice #INV-2025-001 from Acme Corp",
  "body": "Please find attached invoice for €5,250.00...",
  "sender": "billing@acme.com",
  "attachments": [
    {
      "name": "invoice.pdf",
      "content_type": "application/pdf"
    }
  ]
}
```

**Response:**
```json
{
  "email_type": "invoice",
  "vendor": "Acme",
  "sender": "billing@acme.com",
  "amounts": [{"value": 5250.00, "currency": "EUR"}],
  "primary_amount": {"value": 5250.00, "currency": "EUR"},
  "invoice_numbers": ["INV-2025-001"],
  "primary_invoice": "INV-2025-001",
  "dates": ["2025-01-15"],
  "confidence": 0.85
}
```

### Transaction Matching

```
POST /email/match-invoice
```

Match a parsed invoice to transactions.

**Request:**
```json
{
  "invoice": {
    "amount": 5250.00,
    "vendor": "Acme",
    "invoice_number": "INV-2025-001",
    "date": "2025-01-15"
  },
  "bank_transactions": [
    {"id": "TXN001", "amount": 5250.00, "date": "2025-01-16", "counterparty": "Acme Corp"}
  ],
  "internal_transactions": [
    {"id": "GL001", "amount": 5250.00, "account": "Accounts Payable"}
  ]
}
```

**Response:**
```json
{
  "matched": true,
  "match_type": "3-way-match",
  "confidence": 0.92,
  "auto_approve": true,
  "matches": {
    "bank": {"transaction": {...}, "score": 0.95},
    "internal": {"transaction": {...}, "score": 0.90}
  }
}
```

### Payment Matching

```
POST /email/match-payment
```

Match a payment confirmation to open invoices.

### Vendor Exceptions

```
POST /email/vendor-exceptions
```

Get unmatched items for a vendor.

**Response:**
```json
{
  "vendor": "Acme",
  "unmatched_invoices": [...],
  "unmatched_transactions": [...],
  "unmatched_invoice_count": 3,
  "unmatched_transaction_count": 1,
  "total_unmatched_amount": 15750.00,
  "has_exceptions": true
}
```

### Full Email Processing

```
POST /email/process
```

End-to-end processing: parse, match, audit.

## Autonomous Follow-ups

**Clearledgr automatically creates follow-up tasks** when it can't match an item. No manual task creation required.

### How It Works

1. Email comes in with invoice/payment
2. Clearledgr parses and attempts to match
3. If no match found → Clearledgr auto-creates a follow-up task
4. User notified via Slack/Teams
5. User resolves with one click

Tasks are surfaced in:
- **Gmail/Outlook sidebars** - See tasks related to current email
- **Slack/Teams notifications** - Get notified automatically

### Create Task (Manual Override)

For edge cases where you need to manually create a task:

```
POST /email/tasks
```

**Request:**
```json
{
  "email_id": "msg_abc123",
  "email_subject": "Invoice #INV-2025-001",
  "email_sender": "billing@acme.com",
  "thread_id": "thread_xyz",
  "created_by": "user@company.com",
  "task_type": "collect_docs",
  "title": "Collect supporting docs for Acme invoice",
  "assignee_email": "finance@company.com",
  "priority": "high"
}
```

### Task Types

- `collect_docs` - Collect supporting documents
- `chase_approver` - Chase approval
- `reconcile_item` - Reconcile item
- `verify_payment` - Verify payment
- `follow_up` - General follow-up
- `close_task` - Close-related task

### Other Task Endpoints

```
GET /email/tasks                    # List tasks
GET /email/tasks/{task_id}          # Get task
GET /email/tasks/by-email/{email_id} # Tasks for email
GET /email/tasks/overdue            # Overdue tasks
POST /email/tasks/notify-overdue    # Send overdue notification to Slack/Teams
PATCH /email/tasks/status           # Update status
PATCH /email/tasks/assign           # Assign task
POST /email/tasks/comments          # Add comment
```

### Task Notifications (Slack/Teams)

Tasks automatically trigger Slack/Teams notifications:

- **Task Created**: When a new task is created from an email
- **Task Assigned**: When a task is assigned to someone
- **Task Completed**: When a task is marked complete
- **Comment Added**: When someone comments on a task

### Autonomous Follow-ups & Reminders

Clearledgr autonomously follows up on tasks:

| Check | Trigger | Action |
|-------|---------|--------|
| **Overdue** | Task past due date | Send reminder |
| **Urgent Reminder** | 3+ days overdue | More urgent notification |
| **Escalation** | 7+ days overdue | Escalation notification |
| **Approaching Deadline** | Due tomorrow/day after | Proactive reminder |
| **Stale Task** | No activity for 5+ days | Nudge notification |

**Endpoint:**
```
POST /email/tasks/run-scheduler
```

This runs all checks and sends appropriate reminders. Call it daily via cron or scheduler.

**Example cron (daily at 9am):**
```bash
0 9 * * * curl -X POST https://api.clearledgr.com/email/tasks/run-scheduler
```

Notification format includes:
- Task title and type
- Priority (with color coding)
- Assignee
- Due date
- Related vendor/amount
- Source email subject

## Audit Trail

### Record Event

```
POST /audit/record
```

**Request:**
```json
{
  "user_email": "user@company.com",
  "action": "document_matched",
  "entity_type": "invoice",
  "entity_id": "INV-2025-001",
  "source_type": "email",
  "source_id": "msg_abc123",
  "after_state": {"matched_to": "TXN001"}
}
```

### Query Audit Trail

```
GET /audit/trail?entity_type=invoice&entity_id=INV-2025-001
GET /audit/entity/{entity_type}/{entity_id}
GET /audit/user/{user_email}
```

### Action Types

- `email_processed` - Email parsed and analyzed
- `document_added` - Added to AP/ledger
- `document_matched` - Matched to transaction
- `document_flagged` - Flagged for review
- `task_created` - Task created from email
- `task_completed` - Task completed
- `variance_flagged` - Variance flagged
- `transaction_approved` - Match approved
- `transaction_rejected` - Match rejected

## Gmail Add-on

Located in `ui/gmail/`

### Features

- Contextual card when viewing emails
- Quick action buttons
- **Writes to linked Google Sheet** (CL_EMAIL_MATCHES, CL_EMAIL_EXCEPTIONS, CL_TASKS)
- Settings configuration

### End-to-End Flow

```
Gmail Email → Parse → Match → Write to Google Sheet → Notify Slack/Teams
                         ↓
              Auto-create task if exception
```

### Output Tabs Created

| Tab | Content |
|-----|---------|
| `CL_EMAIL_MATCHES` | Successfully matched items |
| `CL_EMAIL_EXCEPTIONS` | Unmatched items with reasons |
| `CL_TASKS` | Auto-created follow-up tasks |
| `CL_AUDIT_LOG` | Complete audit trail (who, what, when) |

### Deployment

See `ui/gmail/DEPLOYMENT.md`

## Outlook Add-in

Located in `ui/outlook/`

### Features

- Task pane with full UI
- Toolbar commands
- Mobile support
- Command functions
- API calls for matching and task creation

### End-to-End Flow

```
Outlook Email → Parse → Match via API → Notify Slack/Teams
                              ↓
                   Auto-create task if exception
```

For Excel write-back (planned), configure Microsoft Graph API access.

### Deployment

See `ui/outlook/DEPLOYMENT.md`

## Integration with Finance Stack

### ERP/Accounting Push

Clearledgr pushes cleaned, structured data to:
- NetSuite
- Xero
- QuickBooks
- Other ERPs via API

### Data Flow

```
Email → Clearledgr Parse → Match → Approve → Push to ERP
                ↓
        Audit Trail Records
                ↓
        Task Creation (if needed)
```

### Clearledgr as Source of Truth

- Internal reconciliation engine is source of truth
- Email is capture and review surface
- ERP receives approved, validated data
- Complete audit trail from email to ledger

## Configuration

### API URL

Set in add-on settings:
- Development: `http://localhost:8000`
- Production: `https://api.clearledgr.com`

### Matching Tolerances

Configure via API:
```json
{
  "amount_tolerance_pct": 1.0,
  "date_window_days": 7
}
```

### Currency Support

Supported currencies (Europe/Africa focus):
- EUR, USD, GBP
- NGN (Nigerian Naira)
- ZAR (South African Rand)
- KES (Kenyan Shilling)

## Best Practices

1. **Connect Data Sources**
   - Link bank transactions sheet
   - Connect ERP for GL entries
   - Maintain open invoices list

2. **Review Exceptions**
   - Check vendor exceptions regularly
   - Resolve before period close
   - Use tasks for follow-ups

3. **Maintain Audit Trail**
   - All actions are logged
   - Review for compliance
   - Export for auditors

4. **Use Tasks**
   - Create tasks for complex items
   - Assign to team members
   - Track through completion
