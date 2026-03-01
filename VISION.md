# Clearledgr Vision

## Core Thesis

Clearledgr is a Finance AI Agent.

Like coding agents execute software tasks, Clearledgr executes finance workflows with policy gates, human controls, durable execution, and auditable outcomes.

## Product Direction

### Now (AP Skill v1)
- One finance agent runtime
- One production skill domain: Accounts Payable
- Embedded work surface: Gmail (`Clearledgr AP`, action-first)
- Embedded approval surfaces: Slack and Teams
- ERP system-of-record execution with connector readiness gates:
  - NetSuite
  - QuickBooks
  - Xero
  - SAP

### Next (Skill Expansion on the same runtime)
- Vendor onboarding/compliance operations
- AP exceptions and dispute resolution
- Collections/cash-application support
- Close-task and accrual support workflows

## Non-Negotiables

1. One runtime, many skills (no second execution runtime per workflow)
2. Embedded-first execution (work in inbox/chat surfaces already used by finance teams)
3. Deterministic policy/state gates before risky or mutating actions
4. Human-in-the-loop for high-risk actions unless policy explicitly allows autopilot
5. Idempotent mutating actions with explicit outcomes
6. Append-only, evidence-linked audit trail for material actions
7. Truthful runtime claims (no fake durability, no hidden fallback claims)

## Runtime Model

Clearledgr runs one core agent runtime with reusable skill contracts:

- Canonical intent APIs:
  - `/api/agent/intents/preview`
  - `/api/agent/intents/execute`
  - `/api/agent/intents/preview-request`
  - `/api/agent/intents/execute-request`
  - `/api/agent/intents/skills`
- Shared contracts:
  - `SkillRequest`
  - `SkillResponse`
  - `ActionExecution`
  - `AuditEvent`
- Skill modules implement state machine + action catalog + policy pack + evidence schema + adapter bindings + KPI contract
- Surface adapters (Gmail, Slack/Teams, ERP) remain thin; execution logic stays in runtime/skills

## Surface Doctrine

1. Gmail is Work-only: decision-first operator experience for AP items.
2. Slack/Teams are approval and escalation decision surfaces.
3. Admin Console is for ops/monitoring/batch/debug and setup controls.
4. Ops/telemetry/batch/raw debug content does not live in Gmail work UI.

## Positioning

- Not a generic automation builder
- Not a standalone AP dashboard replacement
- Not "AI insights only"

Clearledgr is execution software: it performs finance work, requests decisions when needed, and records what happened.

## AP Skill v1 Outcome Target

When invoices/AP requests arrive:
1. Clearledgr classifies and extracts.
2. Applies deterministic policy/confidence checks.
3. Routes approvals when required.
4. Posts to ERP when approved and eligible.
5. Surfaces exceptions with explicit next action.
6. Records complete audit breadcrumbs and evidence references.

## Current Runtime Skills

1. `ap_v1` (production AP execution skill)
2. `workflow_health_v1` (workflow health diagnostics skill)
3. `vendor_compliance_v1` (vendor compliance posture skill)

## Canonical Sources

For doctrine, contracts, launch gates, and implementation details:
1. `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
2. `/Users/mombalam/Desktop/Clearledgr.v1/README.md`
