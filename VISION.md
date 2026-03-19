# Clearledgr Vision

## Core Thesis

Clearledgr is the execution layer for finance operations — AI agents embedded in the tools finance teams already use.

Like coding agents execute software tasks, Clearledgr's agents execute finance workflows end-to-end across email, spreadsheets, ERPs, and communication tools — with policy gates, human controls, durable execution, and auditable outcomes.

## Product Direction

### Now (AP Skill v1)
- One finance agent runtime
- One production skill domain: Accounts Payable
- Embedded work surface: Gmail (`Clearledgr AP`, action-first)
- Embedded approval surfaces: Slack and Teams
- Outlook inbox intake is de-scoped for AP v1 GA (Gmail-only inbox scope)
- Initial rollout sequence is Europe and Africa first
- Shared operator time standard is `Europe/London` (system storage remains UTC)
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

1. Gmail is the primary Clearledgr product shell.
2. Gmail thread sidebar is the daily AP execution surface: action-first, decision-first, compact.
3. Gmail-native routed pages handle onboarding, integrations, monitoring, policy management, team access, plan, and health.
4. Default pinned Gmail nav stays intentionally sparse: `Home`, `Pipeline`, `Connections`.
5. `Pipeline` is the AP queue/process surface, with finance-native slices, saved starter views, pinned personal views, and direct thread <-> queue reopening.
6. `Activity` and other secondary pages remain available through Home or user pinning, not as default clutter.
7. Slack/Teams are approval and escalation decision surfaces.
8. Ops/telemetry/batch/raw debug content does not live in the Gmail thread work card; it is role-gated in Gmail-native routed pages.
9. Onboarding/account management is admin-first, but it remains inside the Gmail product shell rather than forcing a separate operating console.
10. Gmail authorization is explicit from inline product CTAs, not automatic startup popups.
11. Gmail extension release integrity is CI-enforced: shipped `dist` must match source doctrine and cannot include legacy popup/options/demo surfaces.

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
7. Preserves the same AP record context across Gmail, approvals, ERP results, and pipeline views.

## Current Runtime Skills

1. `ap_v1` (production AP execution skill)
2. `workflow_health_v1` (workflow health diagnostics skill)
3. `vendor_compliance_v1` (vendor compliance posture skill)

## Canonical Sources

For doctrine, contracts, launch gates, and implementation details:
1. `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
2. `/Users/mombalam/Desktop/Clearledgr.v1/README.md`
