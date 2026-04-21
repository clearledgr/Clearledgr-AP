# Agent runtime

How the core loop actually works. If `architecture.md` is the map, this is the flight manual.

**One-sentence version:** inbound events enter a queue, `DeterministicPlanningEngine` turns each event + the current Box state into a `Plan`, and `CoordinationEngine` runs the plan action-by-action with an audit row written before every side effect.

*Last verified: 2026-04-21 against commit `94c98eb`.*

---

## The three objects you'll touch every week

### `AgentEvent`

[`clearledgr/core/events.py`](../clearledgr/core/events.py)

```python
@dataclass
class AgentEvent:
    type: AgentEventType   # enum — 26 members, 19 wired, 7 reserved for V1.2
    source: str            # "gmail_webhook" | "slack_callback" | "timer" | ...
    payload: dict          # producer-specific; for a Box event includes box_id + box_type
    organization_id: str
```

The enum is the closed set of things the agent can react to. Adding a new event type means adding an enum member AND a `_plan_<type>` handler in `planning_engine.py`. Both are required — the planner raises `RuntimeError` (and records a `box_exceptions` row if the event names a Box) on unknown types.

### `Plan`

[`clearledgr/core/plan.py`](../clearledgr/core/plan.py)

```python
@dataclass
class Plan:
    event_type: str
    actions: List[Action]   # ordered
    organization_id: str
    box_id: Optional[str]   # set by coordinator once Box exists
```

```python
@dataclass
class Action:
    name: str               # must match a key in CoordinationEngine._handlers
    kind: Literal["DET", "LLM", "EXT"]
    params: dict
    description: str        # human-readable, shown in audit
```

A `Plan` is inert data. The planner returns it, the coordinator executes it. If you want to know what the agent will do for an event, call `DeterministicPlanningEngine.plan(event, box_state)` and read the list — no side effects fire until you hand the plan to `CoordinationEngine.execute`.

### `BoxLifecycleStore` records

[`clearledgr/core/stores/box_lifecycle_store.py`](../clearledgr/core/stores/box_lifecycle_store.py)

```python
box_exceptions:   id, box_id, box_type, organization_id,
                  exception_type, severity, reason, metadata_json,
                  raised_at, raised_by, raised_actor_type,
                  resolved_at, resolved_by, resolved_actor_type, resolution_note,
                  idempotency_key UNIQUE

box_outcomes:     id, box_id, box_type, organization_id,
                  outcome_type, data_json,
                  recorded_at, recorded_by, recorded_actor_type
                  -- UNIQUE (box_type, box_id): one outcome per Box
```

Don't write to these directly. Use the funnels:

- `db.raise_box_exception(...)` / `db.resolve_box_exception(...)` / `db.record_box_outcome(...)` — each one narrates to `audit_events` AND emits a `box.*` webhook to subscribed customers.
- `update_ap_item(ap_item_id, exception_code=...)` — sets the control-plane column AND mirrors into `box_exceptions`. Don't skip the funnel.
- `update_ap_item(ap_item_id, state="posted_to_erp")` — transitions state AND records the outcome. Same story.

---

## The loop, end to end

```
  inbound surface
     │
     ▼
  event producer (gmail_webhook | slack_callback | timer | ...)
     │
     ▼
  event queue (Redis Streams, in-memory fallback in dev)
     │
     ▼
  celery_tasks.py:_dispatch_event(event)
     │
     ├──► _load_box_state(event, db)            # look up ap_items row if event names a Box
     │
     ├──► DeterministicPlanningEngine.plan(event, box_state) → Plan
     │         │
     │         ├── dispatcher routes by event.type to _plan_email_received / etc.
     │         └── returns Plan with actions[] — may be 0 actions if no-op
     │
     ├──► CoordinationEngine.execute(plan) → CoordinationResult
     │         │
     │         │ for each Action in plan.actions:
     │         │   1. _pre_write (Rule 1) — audit row BEFORE the side effect
     │         │   2. _handlers[action.name] — dispatch with params
     │         │   3. _post_write — outcome audit row (success or failure)
     │         │   4. if waiting_condition set → pause the plan
     │         │   5. if failure is retriable → retry with backoff
     │         │   6. if failure is terminal → abort with box_exceptions row
     │
     └──► returns {status, event_id, result}
```

Three files to know:

- [`clearledgr/services/celery_tasks.py`](../clearledgr/services/celery_tasks.py) — the outermost dispatcher. Loads box state, calls the planner, runs the coordinator, catches any exception and reports `status=failed`.
- [`clearledgr/core/planning_engine.py`](../clearledgr/core/planning_engine.py) — `DeterministicPlanningEngine.plan(...)`. 19 handlers covering §4.1–4.3 and §5 (vendor onboarding).
- [`clearledgr/core/coordination_engine.py`](../clearledgr/core/coordination_engine.py) — `CoordinationEngine.execute(plan)`. 60+ handlers in `_handlers`. Rule 1 enforcement in `_pre_write`. Retry + classification in `_execute_with_retry`.

---

## Rule 1 — audit before action

The invariant: every external write (ERP post, Gmail send, Slack send, state transition, label change) appends an `audit_events` row *before* the side effect fires.

Mechanism:

1. `CoordinationEngine._pre_write(action, box_id)` inserts the audit row.
2. If the audit insert fails (after 3 retries), it raises `_Rule1PreWriteFailed` and the action is aborted with no side effect. Fail-closed.
3. If the audit insert succeeds, dispatch to the handler. If the handler's side effect fails, `_post_write` records the failure — the box timeline reflects "we tried."

All 60+ action handlers go through the execute loop, so Rule 1 wraps every one. No handler should bypass the loop by calling external APIs directly in a different context.

**Don't:** call `erp_router.post_bill(...)` from a service method and return success.
**Do:** return an `Action("post_bill", "EXT", ...)` from a planner, let the coordinator dispatch it, get the audit trail for free.

Drift fence lives in [`tests/test_state_audit_atomicity.py`](../tests/test_state_audit_atomicity.py): simulates an audit INSERT failure and verifies the `ap_items` UPDATE rolls back.

---

## Rule 2 — confirmation before state advance

The invariant: no optimistic state progression. ERP posts land in `posted_to_erp` **after** the ERP confirms, not before. Slack sends don't advance state at all — state advances on the inbound callback.

How it's enforced:

- [`invoice_posting.py:520-540`](../clearledgr/services/invoice_posting.py) — `approve_invoice` calls `_post_to_erp`, AWAITS the result, THEN calls `_transition_invoice_state(target_state="posted_to_erp")` only on `result.get("status") == "success"`.
- [`coordination_engine.py:1025`](../clearledgr/core/coordination_engine.py) — `_handle_send_approval` returns `waiting_condition = {"type": "approval_response", ...}`. No state advance. The plan is paused until `approval_received` event arrives.
- Slack inbound callback enqueues an `approval_received` event, which the coordinator runs, which dispatches `approve_invoice`, which does the ERP post + state advance only on confirmation.

If you're adding a new "make external call and advance state" flow, follow the same pattern: return the external call's `Action`, WAIT for the result, check for success, THEN produce the next `Action` that advances state.

---

## The LLM boundary (spec §7.1)

Exactly five actions call Claude at runtime:

| Action | File | What it does |
|---|---|---|
| `classify_email` | `coordination_engine.py:_handle_classify_email` (~line 640) | Invoice / credit note / query / irrelevant |
| `extract_invoice_fields` | `coordination_engine.py:_handle_extract` (~line 666) | vendor, amount, invoice_number, due_date, field_confidences |
| `generate_exception_reason` | `coordination_engine.py:_handle_generate_exception` (~line 1397) | Human-readable exception narrative for Slack card |
| `classify_vendor_response` | `coordination_engine.py:_handle_classify_vendor` (~line 1241) | Does this reply address the missing info? |
| `draft_vendor_response` | `coordination_engine.py:_handle_draft_vendor_response` (~line 1932) | Outbound vendor email body |

Everything else is deterministic. Nothing outside these five calls `gateway.call` or `gateway.call_sync`.

Drift fence lives in [`tests/test_execution_engine.py::TestLLMBoundaryFence`](../tests/test_execution_engine.py). It greps the source of every `_handle_*` method and asserts only those five invoke the gateway. Adding a sixth without updating the spec + the fence's `SPEC_LLM_ACTIONS` set breaks the test.

---

## Writing a planner

A planner is a method on `DeterministicPlanningEngine` that takes an event + Box state and returns a `Plan`. Rules only — no LLM calls in here.

```python
def _plan_my_new_event(self, event: AgentEvent, box_state: dict) -> Plan:
    actions = [
        Action("some_det_action", "DET", {"foo": event.payload["foo"]},
               "What this step does in English"),
        Action("some_llm_action", "LLM", {"message_id": event.payload["msg_id"]},
               "Claude does the specific thing"),
    ]
    # Branch by state if needed — do NOT branch on free-form LLM output here
    if box_state.get("state") == "some_condition":
        actions.append(Action("conditional_action", "DET", {}, "Handles the branch"))
    return Plan(
        event_type=event.type.value,
        actions=actions,
        organization_id=event.organization_id,
    )
```

Then wire it in the dispatcher at `planning_engine.py:50`:

```python
AgentEventType.MY_NEW_EVENT: self._plan_my_new_event,
```

Adding the enum member in `events.py` is mandatory. Forgetting the dispatcher entry is safe — the engine raises `RuntimeError` and records a `box_exceptions` row, which is loud.

---

## Writing an action handler

An action handler lives on `CoordinationEngine` and is registered in `_register_handlers` at [`coordination_engine.py:120`](../clearledgr/core/coordination_engine.py).

```python
async def _handle_my_new_action(
    self,
    params: dict,
    box_id: Optional[str],
    ctx: dict,
) -> dict:
    """One-line docstring. Explain the side effect."""
    # Do the thing. If you need to advance state, DO IT HERE via
    # self.db.update_ap_item(box_id, state=<new_state>, _actor_type='agent',
    # _actor_id='coordination_engine'). The store funnel will atomically
    # write the audit row AND mirror into box_outcomes if terminal.
    return {"ok": True, "some_result": "value"}
```

Register in `_register_handlers`:

```python
"my_new_action": self._handle_my_new_action,
```

Things to remember:
- Return `{"ok": True, ...}` on success; the coordinator post-writes the outcome audit row from your result.
- If you make an external API call that might be slow, set a per-action timeout in `_ACTION_TIMEOUTS` at the top of the file.
- If you want to PAUSE the plan and wait for an inbound event (approval, vendor response), return `{"ok": True, "waiting_condition": {...}}`. See `_handle_send_approval` for the pattern.
- If your action calls Claude, update `SPEC_LLM_ACTIONS` in `test_execution_engine.py` AND update the spec §7.1 list. The drift fence will fail the build otherwise.

Planner-handler coverage fence lives in [`tests/test_execution_engine.py::TestPlannerActionCoverage`](../tests/test_execution_engine.py) — it greps the planner source for `Action("x", ...)` and asserts "x" is in `_handlers`. You can't ship a planner that emits an unregistered action.

---

## Retry + failure classification

[`coordination_engine.py:_execute_with_retry`](../clearledgr/core/coordination_engine.py) classifies every handler exception into one of:

- **TRANSIENT** (network blip, 503 from ERP, Redis timeout) — retry with exponential backoff, max 3 attempts per action.
- **RATE_LIMIT** (429 from ERP) — retry with a longer delay + wait for the rate-limit reset header.
- **AUTH** (401 from ERP) — refresh OAuth token once, retry once. If still 401, abort and raise `auth_failed` exception on the Box.
- **PERMANENT** (400 validation error, 404 resource gone, schema mismatch) — no retry, abort, raise a `box_exceptions` row.

Classification is in `_classify_failure`. If you're adding a handler that calls an external API and the failure mode doesn't fit existing categories, extend the classifier. Silent `except Exception: pass` is never the right answer — it leaves the Box in an unknown state with no audit trail.

---

## Crash resume

If the process dies mid-plan, the next worker picks up the event from the queue (Redis persists) and re-runs the plan. Idempotency gates prevent duplicate side effects:

- `audit_events.idempotency_key` is UNIQUE. A retried action attempts to insert the same audit row; the UNIQUE constraint catches it and the coordinator knows "this action already ran, move to the next."
- `box_outcomes` is UNIQUE per Box, so the terminal outcome is written exactly once.
- `box_exceptions` uses explicit `idempotency_key` arguments where deterministic (e.g. `ap_excp:{ap_item_id}:{exception_code}:{timestamp}`).

See ADR 007 for the full idempotency story.

`task_runs` table + `FinanceAgentRuntime.resume_pending_agent_tasks` runs at startup to reclaim any task interrupted mid-way. `app_startup.py` calls it once per process start.

---

## When to reach for Claude

You probably shouldn't add a new LLM call. But when you genuinely need one:

1. Check whether it fits one of the five spec §7.1 actions. Can you frame the work as "generate a description"? (Yes → probably fits `generate_exception_reason` or `draft_vendor_response`.)
2. If it's truly new, it means the spec needs an update. Get that approved first, then update `SPEC_LLM_ACTIONS` in the drift fence, then add the handler.
3. Use `gateway.call_sync(LLMAction.YOUR_ACTION, messages=..., tools=...)` — never `httpx` directly. The gateway logs to `llm_call_log`, enforces rate limits, and tracks cost.
4. Never let Claude's output decide routing. Claude writes prose. Rules decide the routing.

The most recent violation we cleaned up was `APDecisionService` calling Claude to pick `approve | needs_info | escalate | reject`. That's now a 10-step rule cascade. If you find yourself typing `if claude_recommendation == "approve"` somewhere, stop and ask "what rule would produce this answer deterministically?" and write that rule instead.

---

## Related reading

- [`architecture.md`](./architecture.md) — where these objects live in the repo.
- [`adrs/001-box-abstraction.md`](./adrs/001-box-abstraction.md) — why Box-first.
- [`adrs/002-coordination-engine-and-feature-flags.md`](./adrs/002-coordination-engine-and-feature-flags.md) — why rename from ExecutionEngine.
- [`adrs/004-rule-1-audit-before-action.md`](./adrs/004-rule-1-audit-before-action.md) — the audit-first invariant in full.
- [`adrs/007-idempotency-piggybacks-on-audit-events.md`](./adrs/007-idempotency-piggybacks-on-audit-events.md) — the idempotency story.
- [`../AGENT_DESIGN_SPECIFICATION.md`](../AGENT_DESIGN_SPECIFICATION.md) §3, §4, §5, §7 — the canonical spec.
