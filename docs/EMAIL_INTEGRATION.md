# Clearledgr AP Email Integration

## Status

- **Role:** AP v1 email integration reference
- **Primary intake surface:** Gmail
- **Canonical doctrine:** `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`

This document describes how AP-related email intake is handled in the current AP-first product direction.

## Scope (AP v1)

In scope:

1. Detect AP-relevant emails in Gmail.
2. Extract invoice/AP fields from email body + attachments.
3. Create/update AP items for workflow orchestration.
4. Route approvals and posting through AP state machine and policy controls.

Out of scope:

1. Non-AP workflow ownership as primary product scope.
2. Consumer messaging channels.
3. Reconciliation-focused workflow ownership in AP v1.

## AP Email Intake Flow

```text
Gmail message received
  -> classify AP relevance
  -> extract fields and evidence
  -> run deterministic validation + confidence checks
  -> create/update AP item
  -> route approval (if required)
  -> post to ERP (when approved and eligible)
  -> persist audit events
```

## Message Types (AP v1)

The intake layer prioritizes AP workflow intent, for example:

1. supplier invoice
2. payment request
3. remittance/supporting AP context
4. AP follow-up / missing-information response

Non-AP messages are ignored or handled as non-workflow items.

## Extraction Expectations

Critical AP fields targeted by default:

1. `vendor_name`
2. `invoice_number`
3. `amount`
4. `due_date` (where required)

When confidence is low on critical fields:

1. posting is blocked
2. `requires_field_review` style flags are raised
3. operator action is required
4. all gating decisions are audited

## Data and State Integration

Email integration feeds the AP state machine and does not bypass workflow controls.

Key properties:

1. AP item IDs are canonical workflow anchors.
2. Source linkage is preserved across related messages/threads.
3. Approval/posting state transitions are server-enforced.
4. Audit records connect intake -> decision -> posting outcomes.

## Security Model

1. Gmail integration credentials are stored securely.
2. Tenant/org scoping is enforced for AP item creation and retrieval.
3. Mutating downstream actions still require policy/state validation.
4. Channel callbacks (Slack/Teams) remain verifier-protected.

## Operational Checks

For runtime validation and incident handling, use:

1. `/Users/mombalam/Desktop/Clearledgr.v1/docs/GETTING_STARTED.md`
2. `/Users/mombalam/Desktop/Clearledgr.v1/docs/RUNBOOKS.md`
3. `/Users/mombalam/Desktop/Clearledgr.v1/docs/STAGING_DRILL_RUNBOOK.md`

## Related Contracts

1. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_BACKEND_CONTRACTS.md`
2. `/Users/mombalam/Desktop/Clearledgr.v1/docs/API_REFERENCE.md`
3. `/Users/mombalam/Desktop/Clearledgr.v1/docs/HOW_IT_WORKS.md`
