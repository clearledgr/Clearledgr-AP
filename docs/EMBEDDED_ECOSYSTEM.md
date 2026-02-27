# Clearledgr Embedded Ecosystem (AP-First)

## Status

- **Role:** Product-surface and ecosystem reference
- **Canonical doctrine:** `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
- **Scope here:** AP v1 embedded ecosystem and runtime expansion model

## Ecosystem Summary

Clearledgr is an embedded finance execution agent:

1. Gmail is the primary AP operator surface.
2. Slack/Teams are approval decision surfaces.
3. ERP is the system of record.
4. Clearledgr runtime executes policy-governed AP actions with auditability.

The product is designed to execute work in-place, not move operators into a new daily dashboard.

## Surface Map

### 1. Gmail (operator execution context)

Primary responsibilities:

1. show AP status, exceptions, and next action
2. support in-thread operator decisions
3. display progressive disclosure for context/audit details

### 2. Slack / Teams (decision context)

Primary responsibilities:

1. present approval/rejection/request-info actions
2. provide concise validation and risk context
3. capture idempotent, auditable callback decisions

### 3. ERP systems (record context)

Primary responsibilities:

1. receive approved AP postings
2. return ERP references and posting outcomes
3. remain source of truth for posted financial records

### 4. Admin/Ops surfaces (configuration context)

Primary responsibilities:

1. integration setup and health diagnostics
2. policy and routing configuration
3. rollout controls and evidence operations

## Runtime Ecosystem Model

```text
Embedded surfaces (Gmail + Slack/Teams)
      |
      v
Finance Agent Runtime (single execution core)
      |
      +-> AP skill (production)
      +-> health/read-only skill(s)
      +-> future finance skills (post-AP)
      |
      v
ERP and external system adapters
```

## AP v1 Lifecycle Across Surfaces

1. AP email arrives in Gmail.
2. Runtime classifies/extracts and validates.
3. Gmail shows operator summary and next action.
4. If required, approval is routed to Slack/Teams.
5. On approval + eligibility, runtime posts to ERP.
6. All transitions and outcomes are audited.

## Expansion Doctrine (Post AP)

Future workflow expansion should happen by adding new skills to the same runtime, not by introducing disconnected execution stacks.

Examples:

1. disputes/vendor issue handling
2. collections/cash-application support
3. close-task orchestration support

All new skills must inherit:

1. policy gates
2. HITL controls where required
3. idempotent mutation contracts
4. end-to-end auditability
5. truthful durability semantics

## Non-Goals for AP v1 Ecosystem

1. Generic no-code automation-builder UX as core product model.
2. Dashboard-first AP workflow model.
3. New consumer messaging surfaces as AP execution channels.

## Related Docs

1. `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
2. `/Users/mombalam/Desktop/Clearledgr.v1/docs/HOW_IT_WORKS.md`
3. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_EMBEDDED_WORKER_EXPERIENCE.md`
4. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_BACKEND_CONTRACTS.md`
5. `/Users/mombalam/Desktop/Clearledgr.v1/docs/API_REFERENCE.md`
6. `/Users/mombalam/Desktop/Clearledgr.v1/docs/AGENT_ARCHITECTURE.md`
