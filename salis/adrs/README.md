# Architecture Decision Records

One short doc per decision that would otherwise get re-litigated. Each ADR is a **paragraph of context + the decision + the consequences** so a future engineer can undo the decision if they want — but only after they see the original reasoning.

Format for every ADR:

```
# ADR-NNN: <Title>
Status: Accepted | Superseded-by-ADR-X | Proposed
Date: YYYY-MM-DD
Author: <who>

## Context
What was true when the decision was made.

## Decision
What we chose.

## Consequences
What that buys us and what it costs us.

## Alternatives considered
What we explicitly rejected.
```

---

## Index

| # | Title | Status |
|---|---|---|
| [001](./001-box-abstraction.md) | Why the Box is the central abstraction | Accepted |
| [002](./002-coordination-engine-and-feature-flags.md) | Why one coordination engine + feature-flagged surfaces | Accepted |
| [003](./003-gmail-slack-erp-surfaces.md) | Why Gmail is work surface, Slack is decision surface, ERP is record | Accepted |
| [004](./004-rule-1-audit-before-action.md) | Why audit rows are written BEFORE actions execute (Rule 1) | Accepted |
| [005](./005-four-erps-at-v1.md) | Why four ERPs shipped at V1 (not one) | Accepted |
| [006](./006-no-payment-execution.md) | Why Clearledgr does not move money | Accepted |
| [007](./007-idempotency-piggybacks-on-audit-events.md) | Why idempotency piggybacks on audit_events instead of a new table | Accepted |
| [008](./008-commission-clawback-frozen.md) | Why commission clawback is frozen (not cancelled) | Accepted |
| [009](./009-kyc-and-open-banking-stubbed.md) | Why KYC and open-banking providers are stubbed | Accepted |
| [010](./010-portal-input-validation.md) | Why NFKC + RTL-override rejection on vendor portal input | Accepted |
| [011](./011-retire-agent-planning-loop.md) | Why we retired the Claude tool-use planning loop (rules decide, LLM describes) | Accepted |

---

## When to write a new ADR

Write one when:
- You made a decision that took more than an hour to think through
- The decision could be reasonably reversed (someone might want to reverse it in 6 months)
- The decision has non-obvious tradeoffs

Don't write one for:
- Obvious choices (we use Python; we use Git)
- Taste-level naming
- One-off implementation details with no reusability

Numbering is monotonic. Don't renumber. If an ADR is superseded, write a new one and mark the old one `Superseded-by-ADR-N`.
