# Clearledgr Vision

## Core Thesis

Clearledgr is the finance execution agent platform.

Like coding agents execute software tasks, Clearledgr executes finance workflows with policy gates, HITL controls, auditability, and durable orchestration.

## Product Direction

### Now (AP v1)
- One runtime
- One primary skill domain: Accounts Payable
- Embedded operator surface: Gmail
- Embedded decision surfaces: Slack and Teams
- System-of-record write-back: ERP

### Next (Skill Expansion on the same runtime)
- Disputes and vendor follow-up operations
- Collections and cash-application workflows
- Close-task orchestration support
- Accrual and exception workflows

## Non-Negotiables

1. Embedded-first execution, not dashboard-first workflow management
2. Policy checks and deterministic hard gates before risky actions
3. Human-in-the-loop for approvals/overrides where required
4. Idempotent mutating actions and explicit execution outcomes
5. End-to-end audit trail and traceability
6. Truthful runtime claims (no fake durability or hidden shortcuts)

## Runtime Model

Clearledgr should operate as one core agent runtime with workflow skills:

- Canonical intent APIs (`/api/agent/intents/preview`, `/api/agent/intents/execute`)
- Skill modules implementing preview/execute/policy/audit contracts
- Orchestrated execution with recoverability, retries, and observability
- Surface adapters (Gmail, Slack/Teams, ERP connectors) that do not duplicate execution logic

## Positioning

- Not a generic automation builder
- Not a standalone AP dashboard replacement
- Not “AI insights only”

Clearledgr is execution software: it performs finance work, requests decisions when needed, and records what happened.

## AP v1 Outcome Target

When invoices/AP requests arrive:
1. Clearledgr classifies and extracts
2. Applies policy/confidence checks
3. Routes approvals when needed
4. Posts to ERP when approved and eligible
5. Surfaces exceptions with explicit next action
6. Records complete audit breadcrumbs

## Canonical Source

For doctrine, contracts, and launch gates, use:
- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
