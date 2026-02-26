# Clearledgr AP v1 Embedded Operator Experience

This document describes the **embedded AP v1 operator experience** across Gmail, Slack, and Teams.

It aligns to the canonical doctrine in:

- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`

Related implementation roadmap (post-hardening product-expression work):

- `/Users/mombalam/Desktop/Clearledgr.v1/docs/AGENTIC_UX_V1_5_IMPLEMENTATION_PLAN.md`

## Purpose

Define the user-facing behavior of the AP v1 embedded experience so the product feels:
1. inbox-native
2. agentic (not a generic automation tool)
3. trustworthy
4. low-clutter

Note: The current codebase already contains agent runtime, browser-agent tooling, and Gmail agent session rendering primitives. The v1.5 roadmap focuses on making those capabilities more visible and operator-centric without changing AP v1 doctrine.

## Product Shape (AP v1)

- **Gmail** = context + status + exceptions + next action (primary operator surface)
- **Slack / Teams** = approvals and escalation decisions
- **ERP** = system of record
- **Admin Console** = setup/ops only (not daily AP processing)

## Core UX Principles

1. **Decision-first**
   - Show what the operator needs to decide now.
   - Hide technical details and raw payloads by default.

2. **Agentic but transparent**
   - The agent can gather context and propose/execute steps.
   - The UI must always show what happened, what is blocked, and what is next.

3. **Progressive disclosure**
   - Summary first.
   - Context tabs and audit details only when needed.

4. **No mini-dashboard in Gmail**
   - Gmail thread panel is a focused workspace, not a full AP console.

5. **Consistent semantics across Slack and Teams**
   - Approval actions and outcomes should feel equivalent even if channel UI differs.

## Gmail Experience (Primary Operator Surface)

### AP Workspace (Thread-Level)

When Clearledgr identifies an AP item in a thread, Gmail should show a focused workspace with:

1. **Status**
   - Current AP state (for example `validating`, `needs_info`, `needs_approval`, `ready_to_post`, `posted`)

2. **Key extracted fields**
   - vendor
   - invoice number
   - amount
   - due date

3. **Exceptions**
   - reason-coded blockers (duplicate risk, low confidence, PO mismatch, budget issue, posting failure)

4. **Next action**
   - one clear action (review, request info, route approval, retry post, etc.)

5. **Audit breadcrumbs**
   - compact human-readable history (approved by X, rejected by Y, posted to ERP at Z)

### Gmail Sections (progressive disclosure)

Default-visible:
1. primary invoice summary
2. status and exception summary
3. next action

Collapsed by default:
1. sources (linked emails/threads)
2. full context tabs
3. technical details (IDs, payload fragments, traces)
4. full audit event list

### Gmail Empty/Idle States

Avoid dashboard-style filler panels.

Preferred behavior:
1. single compact status line
2. actionable message only when needed
3. no large empty KPI/audit sections

### Gmail Operator Actions (examples)

Allowed actions depend on state/policy:
1. request info
2. submit for approval / re-route approval
3. approve with override (with required justification)
4. retry posting (legal states only)
5. open source email / open linked source

No action may bypass server-side state validation or policy enforcement.

## Slack and Teams Experience (Approval/Decision Surfaces)

### Role in the AP workflow

Slack and Teams are where approvers make decisions. They are not full workflow dashboards.

### Approval card requirements

Each card should include:
1. invoice summary
2. validation summary and exception highlights
3. requested action
4. action buttons:
   - approve
   - reject
   - request info
5. clear result feedback
6. link back to Gmail/AP context

### Channel action behavior requirements

1. Duplicate clicks/callbacks are safe and idempotent.
2. Invalid/stale actions return clear feedback.
3. All action outcomes are audited.
4. Slack and Teams support equivalent AP decision semantics.

## Agentic Transparency Patterns (Required)

To preserve trust, the UI should reveal agent behavior without overwhelming users.

### Show in primary flow

1. what state the agent is in
2. what blocked progress (if blocked)
3. what the next step is
4. whether human confirmation is required

### Show on demand

1. validation details
2. source evidence
3. execution history
4. posting preview details (for high-risk/fallback paths)

## Experience Anti-Patterns (Do Not Ship)

1. Gmail panel as global navigation hub
2. Gmail panel as dense multi-section dashboard
3. Technical text in the primary decision area
4. Inconsistent Slack vs Teams decision semantics
5. Hidden failure states ("looks successful" but actually blocked)

## Admin Console Boundary (Explicit)

Admin Console responsibilities:
1. integration setup
2. policy configuration
3. org/team settings
4. health and diagnostics
5. subscription/plan management

Admin Console is **not** the daily AP operator workflow UI for AP v1.

## UX Acceptance Checklist (AP v1)

1. Operator can process the current invoice from Gmail without reading raw technical data.
2. Gmail panel defaults to one active item and low-clutter summary.
3. Slack and Teams approval cards support equivalent actions and outcomes.
4. Exceptions are clearly reason-coded and actionable.
5. Audit breadcrumbs are visible in-context with deeper detail on demand.
6. The product feels like an embedded execution layer, not a generic automation panel.
