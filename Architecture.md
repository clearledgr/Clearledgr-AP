# Architecture

## Principles
- Execution-first, not UI-first
- Event-driven, not page-driven
- Deterministic core with LLM assistance
- Idempotent workflow runs
- Append-only audit logs

## Components
- Email ingestion via Gmail API and InboxSDK
- Workflow engine with durable state transitions
- Deterministic validation rules
- LLM used for AP reasoning and typed browser action planning
- Slack and Teams approval interfaces
- NetSuite-first ERP connector with mock mode for local
- Immutable audit store
- Browser-native agent control plane:
  - Agent sessions
  - Policy-gated tool registry (`read_page`, `extract_table`, `find_element`, `click`, `type`, `select`, `open_tab`, `switch_tab`, `capture_evidence`)
  - Extension action runner for DOM execution
  - Per-command audit evidence and idempotency

## Browser Agent Safety
- Read-only actions can run automatically on allowed domains.
- Mutating actions are policy-gated.
- Sensitive actions require explicit human confirmation in the Gmail sidebar.
- Every enqueue and execution result appends immutable audit entries.

## Non-negotiable rule

If an invoice does not result in an approved ERP entry,
the workflow is incomplete and invalid.

No dashboards.
No long-lived UI state.
No silent actions.
