# Clearledgr v1 Embedded Worker Experience

Clearledgr v1 is an automation-first finance worker embedded in Gmail, Sheets, and Slack. The conversational sidebar is the control plane for intent, explanations, and approvals. The worker runs end-to-end workflows and surfaces exceptions only.

## Core Principles
- **Automation first**: Clearledgr runs workflows automatically; humans only handle exceptions and approvals.
- **Embedded**: The worker lives in the tools teams already use (Gmail, Sheets, Slack).
- **Full-context processing**: Email content and transaction details can be used to improve extraction and explanations.
- **Auditability**: Every exception and approval is tracked as a task and/or audit event.

## Gmail Experience (Embedded Worker Sidebar)

### States and User Messages
- **Idle**: Finance queue visible.
  - Empty state: "All caught up!" / "No finance items to process"
  - Tip: "Open an email to process it"
- **Processing** (Worker Activity panel):
  - "Scanning email locally..."
  - "Classified: invoice/payment/statement/non-finance"
  - "Extracting finance signals..."
  - "Assigning GL category..."
  - "Checking matching engine..."
  - "Match found: 92% confidence"
  - "No matching transaction found"
  - "Backend offline - local mode"
  - "Error: <message>"
- **Result card**:
  - Badge: "Matched" | "Pending" | "Needs review"
  - Labels: "Matched to", "Match confidence"

### Actions
- **Approve**: Sends feedback to `/agent/feedback`.
- **Edit**: Adjusts vendor/amount/category before approval (local).
- **Flag**: Creates an exception task in `/email/tasks` and triggers Slack notification.
- **Log to Sheets**: Sends a log entry to the Sheets webhook to append into `CL_EMAIL_INBOX`.
 - **Auto-route** (default): Missing matches automatically create a task for Slack approval.

### Data Handling
- Email subject/body and attachment text can be sent to Clearledgr for extraction.
- Transaction details are shared with Clearledgr to power matching and explanations.

## Sheets Experience (Reconciliation Worker)

### States and User Messages
- **Ready**: "Ready to reconcile"
- **Running**: "Clearledgr is running in your sheet"
- **Activity feed**:
  - "Scanning your sheet"
  - "Matched (78%)"
- **Exceptions**:
  - Section title: "Needs Review"
  - Shows exception list with amounts and reasons

### Outputs
- `CL_SUMMARY` - reconciliation summary
  - Match rate, totals, exceptions
- `CL_RECONCILED` - matched groups
- `CL_EXCEPTIONS` - exceptions requiring review
- `CL_EMAIL_INBOX` - Gmail log (when webhook is used)

### Slack Notification
When reconciliation completes, Slack receives:
- Summary (match rate, matched volume, exception count)
- Top exceptions (if any)
- "View in Sheets" button with the sheet URL

## Slack Experience (Exception and Approval Hub)

### Commands
- `/clearledgr status` - summary stats from `/runs/stats`
- `/clearledgr run` - run reconciliation for the default sheet
- `/clearledgr exceptions` - list open exception tasks
- `/clearledgr tasks` - list open tasks
- `/reconcile` - shortcut for `/clearledgr run`

### Exceptions
- New exception tasks are posted with actions: **Complete**, **Approve**, **Reject**, **Add Note**.
- Completing or approving closes the task via `/email/tasks/status`.
- Reconciliation exceptions are converted into tasks when runs are triggered from Slack.

### Approval Outcomes
- **Approve**: task status -> `completed`
- **Reject**: task status -> `cancelled`
- **Add Note**: posts to `/email/tasks/comments`

## Exception Lifecycle (Shared)
1) **Detected** -> task created with status `open`
2) **Routed** -> Slack notification posted in `#finance`
3) **Reviewed** -> approved/rejected in Slack
4) **Closed** -> task status updates and audit events recorded

## Viral Loops (v1)

### Loop A: Gmail -> Slack approvals -> Teammate pull-in
1) Finance email flagged in Gmail creates a task.
2) Task posts to Slack with "Invite Approver" CTA.
3) Approver completes task in Slack and sees Clearledgr working in context.
4) Approver invites another teammate for the next approval.

### Loop B: Sheets -> Slack digest -> Team adoption
1) Reconciliation run posts summary and exceptions to Slack.
2) Exceptions appear as tasks with action buttons.
3) Approvers complete tasks in Slack; results link back to Sheets.
4) Team subscribes to the channel digest for ongoing visibility.

### Entry Points to Emphasize
- Gmail sidebar: "Exceptions route to Slack for approval."
- Slack tasks: "Invite Approver" button.
- Slack reconciliation summary: "Invite Approver" button.
