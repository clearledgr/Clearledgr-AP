# Clearledgr Architecture (AP v1 Aligned)

## Governance Note

- **Document role:** System architecture reference
- **Canonical doctrine source:** `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
- **Scope in this document:** AP v1 production architecture and runtime expansion pattern

If this document conflicts with `PLAN.md`, `PLAN.md` wins.

## Architecture Summary

Clearledgr is a finance execution agent platform implemented as:

1. one core agent runtime
2. AP as the first production skill domain
3. embedded surfaces (Gmail operator workflow + Slack/Teams approvals)
4. ERP write-back as system-of-record mutation path
5. deterministic policy/state/audit controls around all risky actions

## System Context

```text
Operator surfaces
  Gmail extension (primary AP workspace)
  Slack/Teams (approval decisions)
        |
        v
Clearledgr backend
  - Auth + org scoping
  - AP state machine and policy checks
  - Finance agent runtime (intent preview/execute)
  - Audit/event persistence
  - ERP integration router
        |
        v
ERP systems (QuickBooks, Xero, NetSuite, SAP)
```

## Runtime and Skill Layers

### Intent layer

- Canonical endpoints:
  - `POST /api/agent/intents/preview`
  - `POST /api/agent/intents/execute`
- Responsibility:
  - auth + tenant scope
  - request validation
  - runtime dispatch and result normalization

### Runtime core

- Primary module:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_agent_runtime.py`
- Responsibility:
  - map intents to skills
  - enforce idempotency replay behavior
  - standardize audit/correlation metadata

### Skill modules

- Primary modules:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/base.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/ap_skill.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/workflow_health_skill.py`
- Responsibility:
  - implement preview/execute contracts for domain intents
  - keep workflow logic out of surface adapters

## AP Workflow Architecture

### Intake and AP item formation

1. AP email arrives in Gmail.
2. Clearledgr classifies and extracts AP-relevant data.
3. AP item is created/updated with source linkage metadata.

### Deterministic control points

1. policy checks
2. confidence gates
3. legal state transitions
4. approval requirements

No direct client-forced state mutation is allowed.

### Approval path

1. clear decision payload generated
2. action dispatched to Slack/Teams
3. callback verified and normalized
4. transition applied idempotently
5. audit event persisted

### Posting path

1. posting preconditions enforced server-side
2. ERP API-first posting attempted
3. normalized success/failure contract returned
4. retries/fallbacks governed by policy + runtime durability controls
5. all outcomes audited

## Trust and Safety Controls

### Required controls

1. Auth boundaries for mutating/sensitive endpoints.
2. Signature/token verification for Slack/Teams callbacks.
3. Idempotency keys for approvals and ERP posting.
4. Immutable/append-only audit semantics.
5. Explicit operator-visible failure states.

### HITL stance

HITL is intentional in AP:

1. approvals
2. overrides
3. high-risk fallback paths

Autonomy is increased on low-risk paths only when policy allows it.

## Durability and Orchestration

### Principles

1. Retry behavior must be truthful and auditable.
2. Non-durable retry semantics must be gated in production.
3. Runtime status surfaces must reflect actual backend mode.

### Current implementation direction

1. local durable runtime path is acceptable with explicit labeling
2. durable retry scheduling must persist attempt lifecycle metadata
3. restart safety is required for GA-level claims

## Surface Boundaries

### Gmail

- primary AP operator surface
- thread-level decision workspace
- progressive disclosure for context and audit

### Slack / Teams

- approval and escalation decision surfaces
- parity in decision semantics
- callback idempotency and audit propagation

### Admin/Ops

- setup, policy, diagnostics, launch controls
- not the daily AP operator surface
- production/staging should run strict AP-v1 surface mode (`AP_V1_STRICT_SURFACES=true`) so legacy/non-canonical routes are blocked unless explicitly re-enabled

## Expansion Path (Post AP v1)

Future workflows should be implemented as skills on the same runtime, reusing:

1. policy gating
2. HITL controls
3. audit contracts
4. idempotent mutating semantics
5. durability and observability contracts

This avoids creating separate workflow silos with inconsistent controls.

## Non-Goals

1. Generic no-code automation builder as the core execution model.
2. Dashboard-first AP operations as the default product experience.
3. Separate per-workflow runtimes with divergent trust models.

## Related Docs

1. `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
2. `/Users/mombalam/Desktop/Clearledgr.v1/docs/AGENT_ARCHITECTURE.md`
3. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_BACKEND_CONTRACTS.md`
4. `/Users/mombalam/Desktop/Clearledgr.v1/docs/API_REFERENCE.md`
5. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_EMBEDDED_WORKER_EXPERIENCE.md`
