# Clearledgr AP Wedge Quality Scorecard

## Purpose

This document defines the standard Clearledgr must meet before it can honestly claim to be better than existing AP tools in its current wedge.

That wedge is narrow and specific:

1. Gmail-native AP triage
2. multi-entity routing
3. approval chasing
4. Slack/Teams approval execution
5. ERP writeback without manual re-entry

This is not a broad AP-suite scorecard. It is the quality bar for the current business wedge.

---

## Wedge Claim

Clearledgr is only winning the wedge if the following statement is true in live customer use:

`A finance manager can run AP from Gmail with less context switching, less approval chasing, and less ERP re-entry than with incumbent tools.`

If this is not true, the wedge is not yet won.

---

## Three Non-Negotiable Standards

### 1. Reliability beats alternatives in live use

Clearledgr must be safer and more trustworthy than the manual workflow it replaces.

Minimum standard:

1. No silent failures in approval, routing, or posting paths
2. No illegal AP state transitions
3. No duplicate approvals or duplicate ERP posts
4. Retry only happens for genuinely recoverable failures
5. Permanent failures surface the real blocker clearly
6. Every mutating step is auditable and idempotent

### 2. Context switching is materially reduced

Clearledgr must remove tab and tool thrash, not just add another layer.

Minimum standard:

1. Finance manager starts in Gmail
2. Most invoices do not require opening a second work surface
3. Approvers can act from Slack or Teams
4. Pipeline is the queue/control plane, not a mandatory second inbox
5. ERP is the system of record, not the place where operators re-enter routine invoice data

### 3. Approval chasing and ERP re-entry are actually removed

The product only works if it eliminates labor, not if it partially automates around it.

Minimum standard:

1. Reminders are automatic or one-click
2. Escalations are automatic or one-click
3. Reassignment is explicit and fast
4. Approved invoices write back to ERP without manual re-keying
5. When writeback fails, operators get a precise blocker and a clean recovery path

---

## Agent Excellence Standard

Each AP agent step must be judged on four dimensions:

### Correctness

Did the agent choose the right action for the current invoice state and evidence?

### Restraint

Did the agent stop when confidence, policy, entity routing, or finance-effect review made automation unsafe?

### Recovery

When an external system failed, did the agent preserve the right state, the right failure code, and the right next action?

### Operator clarity

Did the operator get a clear explanation of what happened and what to do next?

If an agent is clever but creates cleanup work, it is not excellent.

---

## Scorecard

Use this scorecard every week during pilot hardening.

| Area | Standard | Pass threshold | Evidence |
|---|---|---:|---|
| AP triage | AP emails are correctly classified into invoice vs non-invoice finance docs | `>= 95%` | replay set + real pilot traffic sample |
| Entity routing | Correct entity selected automatically for known vendors | `>= 90%` | routing corrections, manual overrides |
| Approval routing | Approval requests go to the correct approver/channel | `>= 95%` | audit events + approval chain |
| Approval chasing | Invoices complete without human reminder chasing | `>= 80%` | reminder/escalation metrics |
| ERP writeback | Approved invoices post without manual ERP entry | `>= 85%` | posted-to-ERP rate on approved invoices |
| Operator clarity | Failed or blocked items surface a precise next action | `>= 95%` | operator review of blocked items |
| Silent failure rate | Mutating actions fail without explicit operator signal | `0%` | audit + incident review |
| Duplicate action safety | Duplicate approvals/posts create duplicate side effects | `0%` | idempotency tests + audit review |
| Context switching | Completed invoices require no manual bounce across Gmail, Slack, and ERP | `>= 70%` | workflow observation + operator logging |
| Manual touches | Human interventions per invoice trend downward | weekly decline | pilot ops review |

These are wedge thresholds, not GA-for-everything thresholds.

---

## Product Boxes That Must Be Checked

### Reliability box

This box is checked only if all of these are true:

1. AP runtime preserves canonical state transitions
2. Approval and ERP actions are idempotent
3. Non-recoverable ERP failures do not enter noisy retry loops
4. Connector/config failures surface as specific blockers like `erp_not_connected` or `erp_not_configured`
5. Resume/retry paths preserve state and failure class
6. Audit trail is complete enough to explain every material action

### Context-switching box

This box is checked only if all of these are true:

1. Gmail is the starting point for daily operator work
2. Slack/Teams approvals are actionable, not just notification-only
3. Pipeline is used for backlog control, exceptions, and admin review, not as a mandatory second primary workspace
4. Operators do not manually copy invoice data into ERP for normal cases

### Chasing and re-entry box

This box is checked only if all of these are true:

1. Approval reminders can happen without operator babysitting
2. Escalation and reassignment are operationally usable
3. Approved invoices reliably move to ERP writeback
4. Recovery paths for failed posts are explicit and low-friction

---

## Metrics We Should Track Weekly

### Reliability metrics

1. `% AP emails correctly triaged`
2. `% invoices routed to correct entity without correction`
3. `% approval requests routed to correct approver/channel`
4. `% approved invoices successfully posted to ERP`
5. `% failed posts caused by product/runtime bugs`
6. `% failed posts caused by external connector/config issues`
7. `silent failure count`
8. `duplicate side-effect count`

### Workflow-efficiency metrics

1. `median approval turnaround time`
2. `% approvals completed without manual chasing`
3. `manual touches per invoice`
4. `% invoices completed without finance re-entry into ERP`
5. `% invoices completed without leaving Gmail + Slack/Teams`

### Operator-experience metrics

1. `% blocked items with a precise next action`
2. `% blocked items resolved without engineering help`
3. `top repeated blocker categories`

---

## Claim Gate

Do not claim Clearledgr is better in the wedge until these conditions are met on live traffic:

1. `>= 95%` AP triage correctness
2. `>= 90%` correct entity routing for known vendors
3. `>= 80%` approvals completed without manual chasing
4. `>= 85%` approved invoices posted without manual ERP entry
5. near-zero silent failures
6. near-zero duplicate side effects

If the team cannot show these numbers or defensible pilot evidence close to them, the claim is still aspirational.

---

## Test and Evidence Map

These areas should back the scorecard with code-level evidence:

### Runtime and workflow correctness

1. [`tests/test_invoice_workflow_runtime_state_transitions.py`](/Users/mombalam/Desktop/Clearledgr.v1/tests/test_invoice_workflow_runtime_state_transitions.py)
2. [`tests/test_finance_agent_runtime.py`](/Users/mombalam/Desktop/Clearledgr.v1/tests/test_finance_agent_runtime.py)
3. [`tests/test_e2e_ap_flow.py`](/Users/mombalam/Desktop/Clearledgr.v1/tests/test_e2e_ap_flow.py)

### Failed-post and operator surface quality

1. [`tests/test_ap_record_surfaces.py`](/Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_record_surfaces.py)
2. [`clearledgr/api/ap_items.py`](/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py)
3. [`clearledgr/services/ap_operator_audit.py`](/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/ap_operator_audit.py)

### Approval execution quality

1. [`tests/test_channel_approval_contract.py`](/Users/mombalam/Desktop/Clearledgr.v1/tests/test_channel_approval_contract.py)
2. [`clearledgr/api/slack_invoices.py`](/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py)
3. [`clearledgr/api/teams_invoices.py`](/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py)

### Live/pilot evidence

1. [`docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/PILOT_E2E_EVIDENCE.md`](/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/PILOT_E2E_EVIDENCE.md)
2. [`docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/FAILURE_MODE_MATRIX.md`](/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/FAILURE_MODE_MATRIX.md)
3. real pilot metrics from `Reports`, `Pipeline`, and audit exports

---

## What To Build Next

If this scorecard is the truth standard, product work should be prioritized in this order:

1. live AP workflow hardening
2. operator-facing blocker clarity
3. replay/eval harnesses for agent decisions and failed posts
4. approval chasing automation quality
5. ERP writeback reliability and recovery
6. only after that, broader workflow expansion

Do not widen the product before the wedge scorecard is green enough to defend.

---

## Weekly Review Questions

Every week, ask:

1. What failed silently?
2. What caused manual chasing?
3. What caused manual ERP re-entry?
4. Which blockers confused the operator?
5. Which failure modes looked transient but were really configuration or policy issues?
6. Which agent action created cleanup work instead of removing it?

If the answers are repetitive, that is where the next hardening sprint goes.
