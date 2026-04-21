# Contributing

How to work here. The "where things live" answers to the questions you'll ask your first month.

*Last verified: 2026-04-21 against commit `94c98eb`.*

---

## The two rules

1. **Code wins over docs.** If the code contradicts a doc, write a PR to fix the doc. Don't trust a doc you haven't verified against the code.
2. **The drift fences are not optional.** They exist because we already broke these invariants once. If a drift fence fails your PR, don't silence it — the test is telling you your change violates an invariant the deck makes to customers.

The drift fences that will catch you:

- [`tests/test_execution_engine.py::TestLLMBoundaryFence`](../tests/test_execution_engine.py) — you added a sixth LLM action without updating the spec.
- [`tests/test_execution_engine.py::TestPlannerActionCoverage`](../tests/test_execution_engine.py) — you added an `Action("foo", ...)` in the planner without a `_handle_foo` in `_handlers`.
- [`tests/test_state_audit_atomicity.py`](../tests/test_state_audit_atomicity.py) — your `update_ap_item` refactor split the audit INSERT from the state UPDATE. Torn state is the specific thing this catches.
- [`tests/test_runtime_surface_scope.py`](../tests/test_runtime_surface_scope.py) — you added a new top-level route prefix that's now exposed in prod without review. Either add it to the strict allowlist (and this fence's expected set) or gate it.
- [`tests/test_tenant_isolation.py`](../tests/test_tenant_isolation.py) — your change leaks a row across organization_id boundaries.

---

## Local dev loop

```bash
# 1. Branch
git checkout -b your-name/short-descriptive-name

# 2. Code
# ... edit ...

# 3. Tests first, narrow
python -m pytest tests/test_the_one_you_touched.py -q

# 4. Then broader
python -m pytest tests/ -q

# 5. Lint / type-check (if configured — check pyproject.toml)
# we currently don't gate on mypy but PRs that add types are welcome.

# 6. Extension build if you touched ui/gmail-extension/
cd ui/gmail-extension && npm test && npm run build

# 7. Commit — conventional commit prefix, short subject
git commit -m "feat(area): what this does"

# 8. Push, open PR, link it here in Slack
```

Expect ~8–10 minutes for a full `python -m pytest tests/ -q` run on a clean macOS laptop. If you're iterating on one module, stay narrow and save the full run for the PR.

---

## Recipes

Copy-paste scaffolds for the work you'll do most.

### Add a new action

Example: you want to add `notify_accounting` — a new DET action that posts to an accounting Slack channel when an invoice crosses a threshold.

1. Add the handler in [`clearledgr/core/coordination_engine.py`](../clearledgr/core/coordination_engine.py):

    ```python
    async def _handle_notify_accounting(self, params, box_id, ctx):
        """Post a high-amount notice to the accounting Slack channel."""
        slack_client = self._get_slack_client()
        await slack_client.post_message(
            channel=params["channel"],
            text=params["text"],
        )
        return {"ok": True, "posted_to": params["channel"]}
    ```

2. Register it in `_register_handlers` (same file):

    ```python
    "notify_accounting": self._handle_notify_accounting,
    ```

3. Emit it from wherever needs to trigger it. If it's from a planner:

    ```python
    if invoice_amount > threshold:
        actions.append(Action(
            "notify_accounting", "EXT",
            {"channel": "#accounting-alerts", "text": f"Large invoice: ${invoice_amount}"},
            "Alert accounting of high-amount invoice",
        ))
    ```

4. If it touches external systems, add a timeout in `_ACTION_TIMEOUTS` at the top of the file.

5. `TestPlannerActionCoverage` will pass automatically — the action is in `_handlers`. If you also want a handler-level test, add one to `test_execution_engine.py`.

### Add a new Box type

Example: you want `expense_claim` alongside `ap_item` and `vendor_onboarding_session`.

1. Create a source table + state machine:

    ```python
    # clearledgr/core/expense_claim_states.py
    class ExpenseClaimState(str, Enum):
        SUBMITTED = "submitted"
        APPROVED = "approved"
        REIMBURSED = "reimbursed"
        REJECTED = "rejected"
    VALID_TRANSITIONS = {...}
    ```

2. Add a schema migration to [`clearledgr/core/migrations.py`](../clearledgr/core/migrations.py):

    ```python
    @migration(44, "Expense claim source table")
    def _v44_expense_claims(cur, db):
        cur.execute("""
            CREATE TABLE expense_claims (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                organization_id TEXT NOT NULL,
                ...
            )
        """)
    ```

3. Register with `BoxRegistry` in [`clearledgr/core/box_registry.py`](../clearledgr/core/box_registry.py):

    ```python
    register(BoxType(
        name="expense_claim",
        source_table="expense_claims",
        state_field="state",
        open_states=frozenset({"submitted", "approved"}),
        terminal_states=frozenset({"reimbursed", "rejected"}),
        ...
    ))
    ```

4. Write a store mixin in `clearledgr/core/stores/expense_claim_store.py` — cover `create_<type>`, `get_<type>`, `update_<type>` with the same atomicity pattern `ap_store.py:update_ap_item` uses. Compose it into `ClearledgrDB` in `clearledgr/core/database.py`.

5. The Box lifecycle machinery works for free: exceptions and outcomes are keyed on `(box_type, box_id)`, so `db.raise_box_exception(box_type="expense_claim", box_id=...)` just works. Same for outcomes and the `/api/ap_items/{id}/box` pattern — copy the read route for the new type.

6. Add event types the new Box needs (`EXPENSE_CLAIM_SUBMITTED`, etc.) to `events.py`, and a `_plan_<event>` to `planning_engine.py`.

7. Add end-to-end tests in `tests/test_expense_claim_<something>.py`. The existing `test_box_lifecycle_store.py` pattern (seeding via `_seed_ap_box`) translates directly.

ADR 001 has the philosophy; this section is the mechanics.

### Add a new ERP

Example: you want to support Sage Intacct.

1. Add a connector file: `clearledgr/integrations/erp_intacct.py` with functions `post_bill_to_intacct(connection, payload)`, `get_payment_status_from_intacct(...)`, etc. Mirror `erp_quickbooks.py` for the shape.

2. Add fields to `ERPConnection` dataclass in [`clearledgr/integrations/erp_router.py`](../clearledgr/integrations/erp_router.py) if Intacct needs auth data not already covered.

3. Extend `erp_router.post_bill` dispatch:

    ```python
    elif connection.erp_type == "intacct":
        return await post_bill_to_intacct(connection, payload)
    ```

4. Add HMAC verification in `clearledgr/core/erp_webhook_verify.py` and a webhook route in `clearledgr/api/erp_webhooks.py`.

5. Add the OAuth callback in `clearledgr/api/erp_oauth.py` if OAuth; otherwise credentials-only setup lives in `clearledgr/api/erp_connections.py`.

6. Add integration tests: mock the Intacct API, assert correct payload shape, auth refresh on 401, rate-limit handling, the whole failure matrix.

7. Add a feature flag if it's V1.2: `FEATURE_INTACCT_ENABLED=false` default, gate the route registration. That way you can ship the code without exposing it.

8. Write an ADR: `adrs/0NN-sage-intacct.md`. Explain why this ERP, what's different, what failure modes you've covered.

### Add a new event type

1. Add to the enum in [`clearledgr/core/events.py`](../clearledgr/core/events.py):

    ```python
    MY_NEW_EVENT = "my_new_event"
    ```

2. Add a planner in [`clearledgr/core/planning_engine.py`](../clearledgr/core/planning_engine.py):

    ```python
    def _plan_my_new_event(self, event, box_state):
        return Plan(...)
    ```

3. Wire it in the dispatcher dict around line 50. Forgetting this is survivable — the engine raises + records a `box_exceptions` row if the event arrives unwired.

4. Add a producer somewhere — an API route, a timer, a webhook. The producer calls `event_queue.enqueue(AgentEvent(type=MY_NEW_EVENT, ...))`.

### Add a webhook event type for customers

The `box.exception_raised` / `box.exception_resolved` / `box.outcome_recorded` webhooks are already emitted by the lifecycle store. If you want to surface a new event:

1. Call `emit_webhook_event(organization_id=..., event_type="my.new.event", payload={...})` from wherever the event happens. [`clearledgr/services/webhook_delivery.py`](../clearledgr/services/webhook_delivery.py).

2. Customers subscribe to it by including `"my.new.event"` in their `event_types` array when creating the subscription via `POST /webhooks`.

3. Document the payload shape and the event name somewhere customer-facing before shipping.

---

## Where code lives (the mental model)

Every file in the repo answers one of three questions:

**"What's our story?"** — doctrine files in repo root:
- `DESIGN_THESIS.md`, `AGENT_DESIGN_SPECIFICATION.md`, `CLEARLEDGR_MEMO.md`, `commission-clawback-spec.md`, `vendor-onboarding-spec.md`.
- These are the contracts. If you disagree with them, the conversation is with product/leadership, not a code PR.

**"What does the product do?"** — `clearledgr/` + `ui/`:
- `clearledgr/core/` — engine, contracts, shared primitives. If it's in `core/`, 3+ services use it.
- `clearledgr/services/` — one module per business capability. These call `core/` and each other.
- `clearledgr/integrations/` — per-third-party adapters (four ERPs).
- `clearledgr/api/` — HTTP routers. Thin translation layer between HTTP and services.
- `clearledgr/workflows/` — tiny today, reserved for named multi-step flows.
- `ui/gmail-extension/` — the Preact/InboxSDK extension. Built with `npm run build`.

**"Does it work?"** — `tests/`, plus:
- `scripts/` — one-off operational scripts. Review before running in prod.
- `docs/` — older audit-time snapshots, not living doctrine. Read sparingly.
- `tasks/` — legacy task queues. Read sparingly.

When you're not sure where a new module goes, ask: will 3+ services use it? If yes, `core/`. If it wraps a single third-party, `integrations/`. Otherwise `services/`.

---

## Code review bar

What gets you a fast LGTM:

- **Narrow diff.** One concern per PR. A 200-line PR across one file gets reviewed faster than an 80-line PR across ten files.
- **Tests that prove the invariant, not just the happy path.** "It works when X" is fine; "it fails safely when Y" is what we actually need.
- **No silent excepts.** `except Exception: pass` is almost always wrong. Either classify the failure (transient vs permanent) or let it propagate.
- **Comments only where WHY is non-obvious.** "Workaround for Xero 400 when due_date is a Sunday" is a good comment. "Set x to 5" is a bad comment.
- **No LLM calls outside the five spec §7.1 actions.** If you need a new one, the PR includes a spec §7.1 update + `SPEC_LLM_ACTIONS` update + justification.
- **Atomic writes stay atomic.** If you're touching `ap_store.py:update_ap_item` or any other funnel, the atomicity test fence must still pass.

What slows a review down:

- A PR that touches top-level README or `DESIGN_THESIS.md` in the same commit as code. Do those separately.
- A PR with no tests touching a store method. Stores are the blast radius zone; they need tests.
- A PR that adds a new `if claude_result.recommendation == "approve"` branch. See the note above.

---

## Working with the AI tools

We use Claude Code (the CLI tool that most of us work alongside) for a lot of the heavy lifting. A few learned patterns:

- **Let it write the diff, you review it.** Claude is fast at mechanical refactors. You're the one who knows the business constraints.
- **Don't trust its summaries of what it changed without opening the diff.** Especially for multi-file changes.
- **It will confidently do the wrong thing if the spec drifts from reality.** Keep `AGENT_DESIGN_SPECIFICATION.md` and Salis docs current — these are what Claude reads when it plans.
- **If a test fails after Claude's change, read the test.** Claude is liable to delete tests it doesn't understand. The drift fences are exactly the tests it's most tempted to delete — don't let it.

`CLAUDE.md` at the repo root has the working-rules file Claude reads on every session. Update it when you land a new invariant you want future-you to remember.

---

## Release rhythm

We're pre-revenue and shipping fast. Current cadence:

- Commits land on `main`. There are no release branches today.
- Railway deploys on push to main via `.github/workflows/railway-deploy-workers.yml` + Railway's own auto-deploy on `api` service.
- Feature flags gate anything we're not ready to expose (`FEATURE_TEAMS_ENABLED`, `FEATURE_OUTLOOK_ENABLED`, etc.).
- If you ship something customer-visible, say so in Slack `#eng`. If it's a breaking change to an external contract (webhook payload, API response shape), flag it loudly.

When we have paying customers this rhythm will change. That's Suleiman's call.

---

## When Salis is wrong

Every doc in this directory has a "last verified" line at the top. If it's older than 30 days and you're relying on it, re-verify. If you find a mismatch, the PR fixing the doc is welcome — no design review needed for doc corrections that align with the code.

If you're not sure whether a doc is stale or you're reading it wrong, ask in Slack before you build on the assumption.
