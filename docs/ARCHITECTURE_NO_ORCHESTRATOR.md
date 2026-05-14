# How Solden honors "no master orchestrator"

> "We believe in coordination through shared state, not through
> orchestration. No master orchestrator. No giant prompt. No
> workflow engine routing everything through a chokepoint.
> Persistent state, reactive agents, structured transitions,
> deterministic outcomes."
> — *The Back Office Runtime Manifesto*

This document is for anyone reading the manifesto and looking at the
codebase wondering: *"…but there's a class called `CoordinationEngine`.
Isn't that exactly the chokepoint the manifesto rejects?"*

Short answer: **no**. The name is the artifact, not the architecture.
The long answer follows.

## What the manifesto rejects

The manifesto's "master orchestrator" critique has a specific
target: workflow engines that put an LLM in the routing loop. The
pattern looks like this:

```
[event] → [giant LLM prompt: "what should we do next?"]
       → [free-form response, parsed]
       → [side effect]
```

That pattern:
* makes the LLM the source of decisions (nondeterministic),
* makes the prompt the chokepoint (every workflow funnels through one model call),
* makes the outputs un-auditable (prose, not typed transitions),
* makes failures irrecoverable (replaying a nondeterministic prompt produces a different plan).

The manifesto says: don't do that. Use shared, typed, persistent
state as the substrate; let small deterministic modules read and
write it; let humans intervene on structured exceptions.

## What Solden actually does

Solden has zero LLM calls in its routing loop. The decision modules
are all deterministic:

| Concern | Where it lives | What kind of code |
|---|---|---|
| What state should the Box move to? | `clearledgr/services/ap_decision.py` | 10-step rule cascade. The docstring says it explicitly: *"rules decide, LLM describes. Claude is **not** called here."* |
| What actions should run on this Box? | `clearledgr/core/planning_engine.py` | `DeterministicPlanningEngine`. The module header says: *"No Claude calls. Claude is only called WITHIN specific Actions during execution."* |
| Should this autonomous write run? | `clearledgr/services/finance_agent_governance.py` | Rule-based autonomy-tier check. No model. |
| Is this transition allowed? | `clearledgr/core/ap_states.py` + `clearledgr/core/bank_match_states.py` | Static `VALID_TRANSITIONS` dict. Hard state machine. |
| Does this match the policy? | `clearledgr/services/override_window.py`, `clearledgr/services/approval_revert.py` | Time-bounded rule checks. No model. |

The LLM, when it runs at all, runs **inside** specific actions —
extracting fields from an invoice, writing a Slack card narrative,
classifying a vendor email. Those are bounded leaf-level uses with
typed input and typed output. The LLM never decides *what should
happen next* — it transforms an input into a structured output that
deterministic code then routes.

## Where `CoordinationEngine` fits

`CoordinationEngine` is what executes the typed `Plan` that
`DeterministicPlanningEngine` produced. It's a dispatcher: given a
Plan with N actions, it walks them in order, runs each, writes the
audit trail, handles retries on transient failures, and respects
the override windows that the reversibility primitives opened.

Critically:

* The Plan was already decided before the engine sees it. The
  engine doesn't choose actions; it executes them.
* The audit funnel (`append_audit_event` in
  `clearledgr/core/stores/ap_store.py`) is the SAME funnel that
  human-driven endpoints write to. Agent writes, operator writes,
  webhook writes — all go through the same Rule-1 pre-write +
  typed-row pattern. There is no "agent path" that bypasses what
  humans see.
* Every action is reversible at some scope: ERP posts via the
  override-window service (15-minute default), approvals via the
  approval-revert service (15-minute default), state transitions
  via the audit-trail replay path.

The CoordinationEngine is a chokepoint **for execution mechanics**
(retry, audit, sequencing) — the same way every program has a chokepoint
called "the interpreter." It is NOT a chokepoint for decisions.

## What still has the "orchestrator" smell

Honest list of what looks orchestrator-shaped if you squint:

1. **The handler registry inside `CoordinationEngine`.** It maps
   action names to executor functions (100+ entries). Critique: this
   is a centralized routing table. Counter: it's a typed dispatch
   table, not an LLM choosing branches. The same shape exists in
   every event handler.
2. **Single-process plan execution.** A Plan today runs in one
   process, one event loop. A truly distributed-coordination
   manifesto might split actions across multiple workers reading
   from shared state. Current scope: deferred. The audit + typed
   transitions are in place to make that refactor mechanical when
   the load demands it.
3. **The name.** `CoordinationEngine` does sound like an
   orchestrator. A rename to `ReactiveDispatcher` or `PlanExecutor`
   would describe the role more accurately. The blast radius —
   ~100 callsites, several tests, integration surfaces — is the
   only reason it hasn't happened yet. This file exists to
   discharge the naming debt with documentation rather than churn.

## Reading order if you want to verify

1. `clearledgr/core/ap_states.py` — the state machine.
2. `clearledgr/core/bank_match_states.py` — the second state
   machine. Same shape, different domain — manifesto generalization
   proof.
3. `clearledgr/services/ap_decision.py` — "rules decide, LLM describes."
4. `clearledgr/core/planning_engine.py` — "No Claude calls."
5. `clearledgr/core/coordination_engine.py` — dispatcher; read the
   module docstring + `_pre_write` to see Rule 1 in action.
6. `clearledgr/services/override_window.py`,
   `clearledgr/services/approval_revert.py` — reversibility primitives.
7. `clearledgr/api/box_export.py` + `docs/BOX_SCHEMA.md` —
   sovereignty / removability primitive.

If you read those in order and still think Solden has a master
orchestrator, file an issue — the architecture review is open.
