# Clearledgr Agent Architecture (AP-First Runtime)

## Status

- **Role:** Architecture reference for the production agent runtime model
- **Scope:** AP v1 runtime and skill architecture
- **Canonical doctrine:** `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`

This document describes how Clearledgr is implemented as one finance execution agent runtime, with AP as the first production skill domain.

## Core Model

Clearledgr runs a single agent runtime and exposes canonical intent APIs:

- `POST /api/agent/intents/preview`
- `POST /api/agent/intents/execute`

The runtime dispatches intents to skills that implement shared execution contracts:

1. `policy_precheck`
2. `preview`
3. `execute`
4. `audit_contract`

## Runtime Topology

```text
Gmail surface (operator context)
   + Slack/Teams (approval decisions)
   + ERP connectors (system-of-record write-back)
                 |
                 v
      Finance Agent Runtime (single execution core)
                 |
          Skill Registry + Dispatch
                 |
      ---------------------------------
      | AP Skill | Workflow Health Skill |
      ---------------------------------
                 |
        AP workflow/state/audit services
```

## Current Runtime Components

### 1. Intent API layer

- File: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_intents.py`
- Responsibility:
  - validate request shape
  - enforce auth boundaries
  - call runtime preview/execute
  - return normalized operator-safe responses

### 2. Runtime dispatcher

- File: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_agent_runtime.py`
- Responsibility:
  - maintain skill registry
  - map intent to skill
  - enforce idempotency replay semantics
  - apply audit correlation metadata

### 3. Skill modules

- Files:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/base.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/ap_skill.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/workflow_health_skill.py`
- Responsibility:
  - AP domain behavior (routing, retries, follow-up preparation)
  - read-only health/status skill behavior
  - deterministic preview/execution contracts

### 4. AP workflow orchestration and state machine

- Primary file: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py`
- Supporting state and storage:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/ap_states.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/ap_store.py`
- Responsibility:
  - legal state transitions
  - deterministic policy checks
  - approval and posting orchestration
  - retry/fallback and audit coverage

## AP-First Skill Set (Current)

### AP skill intents

1. `prepare_vendor_followups`
2. `route_low_risk_for_approval`
3. `retry_recoverable_failures`

### Read-only ops/health intent

1. `read_ap_workflow_health`

These intents are intentionally AP-scoped for v1. Expansion to additional finance workflows should add skills to the same runtime, not new parallel runtimes.

## Execution Guarantees (Required)

1. **Policy before mutation:** no risky mutating action without server-side policy/state checks.
2. **HITL at risk boundaries:** approvals/overrides remain human-confirmed where policy requires.
3. **Idempotency:** duplicate requests/callbacks cannot create duplicate approvals/posts.
4. **Auditability:** all state transitions and mutating operations emit traceable audit events.
5. **Truthful durability claims:** runtime/ops surfaces must accurately reflect durability mode and retry guarantees.

## Surfaces and Responsibilities

1. **Gmail:** primary AP operator workspace (status, exceptions, next action).
2. **Slack/Teams:** approval and escalation decision surfaces.
3. **ERP:** record system for posted AP transactions.
4. **Admin/Ops APIs:** configuration, health, diagnostics, and evidence support.

No surface should duplicate core execution logic outside the runtime.

## Expansion Pattern (Post-AP)

Future domains should be added as new skills on the same runtime, for example:

1. disputes/vendor issue resolution
2. collections/cash-application support
3. close-task orchestration support

Each new skill must inherit the same policy, HITL, audit, and idempotency controls as AP.

## Non-Goals (for this architecture)

1. Introducing a generic no-code automation builder as the execution model.
2. Splitting workflows into separate agent runtimes with inconsistent controls.
3. Bypassing policy/HITL/audit for speed in production paths.

## References

1. `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
2. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_BACKEND_CONTRACTS.md`
3. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_EMBEDDED_WORKER_EXPERIENCE.md`
4. `/Users/mombalam/Desktop/Clearledgr.v1/docs/API_REFERENCE.md`
