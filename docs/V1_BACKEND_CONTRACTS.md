# Clearledgr v1 Backend Contracts (Gmail, Sheets, Slack)

This doc captures the v1 contracts used by embedded surfaces. Payloads can include full email bodies and transaction rows when needed for extraction, matching, and explanations.

## Shared Conventions
- **Auth**: optional `x-api-key` header for all backend calls.
- **Task status**: `open`, `in_progress`, `pending_approval`, `completed`, `cancelled`.
- **Context**: include full subject/body or transaction details when needed.

## Gmail Extension -> Backend

### Health Check
`GET /health`

Response:
```json
{ "status": "ok" }
```

### Match Invoice
`POST /email/match-invoice`
```json
{
  "invoice": {
    "vendor": "Acme",
    "amount": 1200.5,
    "currency": "USD",
    "invoice_number": "INV-123",
    "invoice_date": "2024-09-01",
    "email_type": "invoice",
    "email_subject": "Acme Invoice INV-123",
    "email_sender": "billing@acme.com"
  },
  "bank_transactions": [],
  "internal_transactions": [],
  "config": {
    "amount_tolerance_pct": 0.5,
    "date_window_days": 3
  }
}
```

### Vendor Exceptions
`POST /email/vendor-exceptions`
```json
{
  "vendor": "Acme",
  "all_invoices": [],
  "all_transactions": [],
  "config": {}
}
```

### Feedback (approvals/corrections)
`POST /agent/feedback`
```json
{
  "run_id": "feedback_1726000000",
  "feedback_type": "approval",
  "original_result": {
    "vendor": "Acme",
    "amount": 1200.5,
    "gl_code": "6100",
    "email_type": "invoice",
    "confidence": 0.92
  },
  "corrected_result": null,
  "user_notes": null,
  "organization_id": "org_abc123"
}
```

### Create Exception Task
`POST /email/tasks`
```json
{
  "email_id": "email_1726000000",
  "email_subject": "Acme Invoice INV-123",
  "email_sender": "vendor@acme.com",
  "thread_id": "thread_1726000000",
  "created_by": "user@company.com",
  "task_type": "review_item",
  "title": "Review: Acme - $1,200.50",
  "description": "Flagged for manual review by user.",
  "priority": "high",
  "related_entity_type": "invoice",
  "related_entity_id": "INV-123",
  "related_amount": 1200.5,
  "related_vendor": "Acme",
  "organization_id": "org_abc123"
}
```

Notes:
- Creating a task triggers a Slack notification when the Slack app is configured.
- For reconciliation exceptions created by the Slack app, use `task_type: "reconciliation_exception"` to avoid duplicate notifications.

### Check Status by Email
`GET /email/tasks/by-email/{email_id}`

Response:
```json
{ "tasks": [ { "task_id": "task_20240901T120000Z", "status": "open" } ], "count": 1 }
```

### Audit (ERP sync request)
`POST /audit/record`
```json
{
  "user_email": "user@company.com",
  "action": "sync_to_erp_requested",
  "entity_type": "email",
  "entity_id": "gmail_message_id",
  "source_type": "gmail",
  "source_id": "gmail_message_id",
  "source_name": "Acme Invoice INV-123",
  "after_state": { "vendor": "Acme", "amount": 1200.5, "target_erp": "netsuite" },
  "organization_id": "org_abc123"
}
```

## Gmail Extension -> Sheets Webhook

Apps Script Web App `doPost` endpoint.

Request:
```json
{
  "sheetId": "1abc...sheetId",
  "gmailMessageId": "1788c8d...",
  "vendor": "Acme",
  "amount": 1200.5,
  "currency": "USD",
  "invoiceNumber": "INV-123",
  "invoiceDate": "2024-09-01",
  "emailType": "invoice",
  "senderDomain": "acme.com",
  "senderEmail": "billing@acme.com",
  "receivedAt": "Sep 12, 2024, 10:15 AM",
  "token": "optional_shared_secret"
}
```

Response:
```json
{ "success": true, "sheetId": "1abc...sheetId" }
```

## Slack App -> Backend

### Run Reconciliation (Sheets)
`POST /run-reconciliation-sheets`
```json
{
  "sheet_id": "1abc...sheetId",
  "period_start": "2024-09-01",
  "period_end": "2024-09-30",
  "gateway_tab": "GATEWAY",
  "bank_tab": "BANK",
  "internal_tab": "INTERNAL"
}
```

Response:
```json
{
  "sheet_url": "https://docs.google.com/spreadsheets/...",
  "summary": [ { "matched_pct": 94.2, "matched_volume": 120000 } ],
  "exceptions": [ { "tx_ids": ["GW-123"], "amounts": 1500, "reason": "no_match" } ]
}
```

After reconciliation, the Slack app may create exception tasks via `POST /email/tasks` so approvals can be handled in Slack.
Those tasks should include `related_entity_id` (the source transaction id) for display and use `task_type: "reconciliation_exception"`.

### List Tasks
`GET /email/tasks?status=open&limit=5`

### Update Task Status
`PATCH /email/tasks/status`
```json
{
  "task_id": "task_20240901T120000Z",
  "new_status": "completed",
  "changed_by": "U12345",
  "notes": "Approved in Slack"
}
```

### Add Task Comment
`POST /email/tasks/comments`
```json
{
  "task_id": "task_20240901T120000Z",
  "user_email": "U12345",
  "comment": "Investigated and approved."
}
```

### Run Statistics
`GET /runs/stats`
```json
{
  "total_runs": 12,
  "succeeded": 11,
  "failed": 1,
  "avg_match_rate": 93.4,
  "total_exceptions_found": 22
}
```

## Slack App Configuration
Set these environment variables for the Slack app:
- `API_BASE_URL` (default `http://localhost:8000`)
- `API_KEY` (optional)
- `DEFAULT_SHEET_ID`
- `DEFAULT_GATEWAY_TAB`, `DEFAULT_BANK_TAB`, `DEFAULT_INTERNAL_TAB`
- `DEFAULT_PERIOD_START`, `DEFAULT_PERIOD_END`, or `DEFAULT_PERIOD_DAYS`
- `DEFAULT_ORGANIZATION_ID` (optional)
