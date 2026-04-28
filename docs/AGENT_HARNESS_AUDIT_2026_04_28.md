# Agent Harness Audit — 2026-04-28

**Scope.** Verify whether Clearledgr's agent harness (the runtime + planning + coordination + governance + state-machine + lifecycle + audit + durability stack) is actually live across the entire product, or whether parts of it are shelfware and parts of the product bypass it.

**Method.** Three parallel read-only audits: (A) every inbound event surface mapped to whether it routes through `FinanceAgentRuntime`, (B) internal harness wiring verified at the call-site level, (C) persistence + observability verified against the data model. File:line evidence below; no live-DB or live-traffic validation — that is called out explicitly where it would change a verdict.

**One-line verdict.** The harness is real, but it is not one harness — it is **three execution paths with three different sets of guarantees**, and the surface customers click most often (workspace SPA AP item actions) is on the path with the **fewest** guarantees.

---

## TL;DR — what's actually true

| Claim from the thesis | Reality |
|---|---|
| Every event flows through `FinanceAgentRuntime` | **Partial.** Async events (Gmail push, Slack interactives, vendor portal, ERP webhooks) flow through it. Workspace SPA AP item actions (approve / reject / field-review / bulk / merge / split / retry / reverse) bypass it and write directly to the DB. |
| Planning engine decides what to do | **Partial.** Only invoked from the Celery `process_agent_event` background drain. Synchronous skill calls (`runtime.execute_skill_request`) do not consult it. |
| Coordination engine executes the plan | **Partial.** Same as planning — async path only. Synchronous skills run skill contracts directly. |
| Governance gate vetoes unsafe actions | **Wired** on the synchronous skill path (loop service calls `build_deliberation` before execution). Not on the workspace SPA bypass path because the runtime isn't reached at all there. |
| AP state machine validates every transition | **Solid at the data layer.** Every `update_ap_item(state=...)` call funnels through [ap_store.py:237](../clearledgr/core/stores/ap_store.py#L237), which calls `transition_or_raise` ([L303](../clearledgr/core/stores/ap_store.py#L303)) and writes the audit event atomically in the same transaction ([L343-381](../clearledgr/core/stores/ap_store.py#L343-L381)). No raw `UPDATE ap_items` exists anywhere. **One real defect remains:** [coordination_engine.py:594](../clearledgr/core/coordination_engine.py#L594) wraps the call in `try: ... except Exception: pass`, which silently swallows `IllegalTransitionError` if the transition is illegal. |
| Box lifecycle records every transition | **Correct by design — audit was misframed on first read.** `BoxLifecycleStore` was never intended to record transitions. Per its docstring ([box_lifecycle_store.py:1-29](../clearledgr/core/stores/box_lifecycle_store.py#L1-L29)): "state and timeline already have first-class homes (state-field on the source table; `audit_events`); this mixin makes the *other two* (exceptions, outcomes) first-class too." Transitions live as the column + an audit_events row. No shelfware; clean separation. |
| Append-only audit trail | **Solid.** ~101 `append_audit_event` call sites; DB triggers in [database.py:374-428](../clearledgr/core/database.py#L374-L428) reject UPDATE/DELETE on `audit_events` and `ap_policy_audit_events`. (Live-DB confirmation: `SELECT tgname FROM pg_trigger WHERE tgname LIKE 'trg_audit%'` — not run in this audit.) |
| Durable task runs, resumable on restart | **Gap.** Rows are created in `task_runs`. Startup recovery (`resume_pending_agent_tasks`) drains a different table (`agent_retry_jobs`). An api crash mid-coordination orphans `task_runs` rows; nothing scans for them on boot. |

**Summary score (revised): 5/9 fully wired, 3/9 partial, 1/9 gap.**

**Revisions on close-reading.** Two findings in the first draft of this audit were overstated:
1. **P1 ("6 state-machine bypasses")** was wrong. Five of the six sites correctly rely on store-level enforcement in `update_ap_item`, which validates and audits atomically. Only one site (`coordination_engine.py:594`) is a real defect — it silently swallows `IllegalTransitionError`.
2. **P5 ("BoxLifecycleStore is shelfware for transitions")** was wrong. The lifecycle store was never meant to record transitions; that's the audit_events row's job, and it's already there. The lifecycle store handles exceptions + outcomes — by design.

Both corrections are reflected in the table above and in the per-finding sections below. The original draft's framing is preserved in the "Revisions" section at the end of this doc so the trajectory is honest.

---

## The three execution paths

This is the single most important finding. The thesis describes one harness. The code has three.

### Path 1 — Async event drain (`Celery → planning → coordination → governance`)

The canonical, fully-realised harness path. Used by:
- Gmail push: webhook enqueues `process_gmail_push` Celery task → AgentEvent → `process_agent_event` → `_dispatch_event` → `get_planning_engine(db).plan(event)` → `CoordinationEngine.run(plan)`
- Slack approve/reject button: [slack_invoices.py:626](../clearledgr/api/slack_invoices.py#L626) enqueues `APPROVAL_RECEIVED` to event queue
- Vendor portal KYC + IBAN: [vendor_portal.py:310, 459](../clearledgr/api/vendor_portal.py#L310) enqueue `KYC_DOCUMENT_RECEIVED` / `IBAN_CHANGE_SUBMITTED`
- ERP webhooks (NetSuite/SAP/Xero/QBO): enqueue via intake adapter
- Gmail label sync (Phase 2): `LABEL_CHANGED` event

Verdict: **architecturally clean.** Planning is deterministic, coordination executes, audit fires, governance is consulted (per agent B at [finance_agent_loop.py:79](../clearledgr/services/finance_agent_loop.py#L79)).

### Path 2 — Synchronous skill execution (`runtime → loop → skill contract`)

Used by:
- Gmail extension actions: every sidebar action that calls `_build_finance_runtime` ([gmail_extension.py](../clearledgr/api/gmail_extension.py) — 11 sites) → `runtime.execute_*()` → `FinanceAgentLoopService.execute_skill_request`
- Agent intents API: [agent_intents.py:127, 139](../clearledgr/api/agent_intents.py#L139) — `runtime.preview_intent()` / `runtime.execute_intent()`

What this path has: governance deliberation (`build_deliberation` blocks before skill runs), audit emission, skill-contract execution, agent memory.

What this path **does not have**: the planning engine, the coordination engine. The skill contract is called directly. So claims like "deterministic rules-first planning" describe Path 1, not Path 2.

Verdict: **partial harness.** Strong governance, no planning. Acceptable if we're explicit about it; misleading if we describe the product as "everything goes through the runtime + planning + coordination."

### Path 3 — Direct-DB bypass

Used by **the workspace SPA AP item action surface** — the buttons your customers click. Specific routes in [ap_items_action_routes.py](../clearledgr/api/ap_items_action_routes.py):

| Route | Function | What it calls |
|---|---|---|
| `POST /api/ap-items/{id}/field-review/resolve` | `resolve_ap_item_field_review` (L299) | `db.update_ap_item()` directly |
| `POST /api/ap-items/field-review/bulk-resolve` | `bulk_resolve_ap_item_field_review` (L318) | per-item `db.update_ap_item()` in a loop |
| `POST /api/ap-items/{id}/non-invoice-resolve` | (L378) | `ap_item_service._execute_non_invoice_resolution` → direct DB |
| `POST /api/ap-items/{id}/entity-route` | (L537) | shared service → direct DB |
| `POST /api/ap-items/{id}/snooze` etc. | snooze / unsnooze / retry / reverse / merge / split | `ap_item_service` → direct DB |
| Bulk approve / reject (BatchOps) | `_execute_approve` etc. | direct DB |

**Evidence.** `ap_item_service.py` has exactly **one** runtime invocation across the whole file (line 2992, a fallback auto-resume call). Every other state mutation is a direct `db.update_ap_item(...)` ([ap_item_service.py:768, 2893](../clearledgr/services/ap_item_service.py#L768)).

What this path has: route-level `transition_or_raise` validation in some cases (e.g. `snooze` at [ap_items_action_routes.py:2000](../clearledgr/api/ap_items_action_routes.py#L2000)), per-action audit events.

What this path **does not have**: the runtime, planning, coordination, governance, agent memory, agent-loop deliberation. The Box-lifecycle-store. None of it.

Verdict: **bypass.** This is the most-used customer surface in the product, and it is on the path with the weakest harness coverage.

---

## Detailed findings — internal wiring

### 1. Lifespan / startup wiring of `FinanceAgentLoopService`

`FinanceAgentLoopService` is **lazy-instantiated on first skill call**, not started as an autonomous loop at boot. [finance_agent_runtime.py:1505-1514](../clearledgr/services/finance_agent_runtime.py#L1505-L1514) constructs it on demand from `_agent_loop_service()`. There is no `asyncio.create_task` ticking it; "agent loop" here means the **synchronous control-flow spine** of a skill request, not a background heartbeat.

That's a defensible architecture choice, but the marketing and thesis language ("autonomous agent loop") is misleading — the loop is reactive, not autonomous. Verdict: **wired, but architecturally different from how it's described externally.**

### 2. Runtime → planning engine

The planning engine is called from exactly one place: [celery_tasks.py:122-136](../clearledgr/services/celery_tasks.py#L122) inside `_dispatch_event`. The synchronous runtime does not call it. Verdict: **partial.**

### 3. Planning → coordination engine

Same call site as #2 — coordination only runs on the async path. Synchronous skills bypass it. Verdict: **partial.**

### 4. Governance gate

Live on the synchronous skill path: [finance_agent_loop.py:79](../clearledgr/services/finance_agent_loop.py#L79) calls `build_deliberation`; [finance_agent_loop.py:106-149](../clearledgr/services/finance_agent_loop.py#L106) blocks the skill if `should_execute=False`. Self-recovery fires post-failure ([finance_agent_governance.py:382](../clearledgr/services/finance_agent_governance.py#L382)). Verdict: **wired** for Path 2; **not reached** on Path 3.

### 5. AP state machine

`VALID_TRANSITIONS` is defined in [ap_states.py:64-85](../clearledgr/core/ap_states.py#L64-L85). **Validation is enforced at the data layer**, not in callers — every state write in the codebase goes through `update_ap_item` ([ap_store.py:237](../clearledgr/core/stores/ap_store.py#L237)), which:

- Calls `transition_or_raise(prev_state, new_state, ap_item_id)` at [L303](../clearledgr/core/stores/ap_store.py#L303)
- Writes a `state_transition` audit event in the same DB transaction at [L343-381](../clearledgr/core/stores/ap_store.py#L343-L381)
- Emits a state-change webhook post-commit at [L387-405](../clearledgr/core/stores/ap_store.py#L387-L405)

This is correct architecture: callers can't accidentally bypass validation because there's only one chokepoint. A grep for raw `UPDATE ap_items` returns nothing outside DDL.

**One real defect:** [coordination_engine.py:594](../clearledgr/core/coordination_engine.py#L594) — `_move_to_exception` wraps the validated call in `try: ... except Exception: pass`. If the current state can't legally transition to `needs_info`, the `IllegalTransitionError` is silently swallowed and the box is left in its broken state with no log, no audit, and no caller signal. Other sites that call `update_ap_item(state=...)` either let the exception propagate or `logger.warning + continue`, which is fine.

Verdict: **wired** at the data layer; **one site needs the silent except removed.**

### 6. Box lifecycle (state transitions)

Initially I flagged this as "shelfware for transitions" — that was wrong on close-read. `BoxLifecycleStore`'s docstring ([box_lifecycle_store.py:1-29](../clearledgr/core/stores/box_lifecycle_store.py#L1-L29)) is explicit: "state and timeline already have first-class homes (state-field on the source table; `audit_events` keyed on `(box_id, box_type)`); this mixin makes the **other two** first-class too: exceptions and outcomes."

So the four pieces of "Box lifecycle" the deck promises are:
- **State** → `ap_items.state` column (validated by `update_ap_item`).
- **Timeline** → `audit_events` rows (atomically written; append-only triggers).
- **Exceptions** → `box_exceptions` rows via `BoxLifecycleStore.raise_box_exception`.
- **Outcomes** → `box_outcomes` rows via `BoxLifecycleStore.record_outcome`.

All four have homes; all four are written. Verdict: **wired by design.**

---

## Detailed findings — persistence & observability

### 7. `audit_events` insertions

101 `append_audit_event` call sites across the codebase. Centralised funnel through the `APStore` mixin. CoordinationEngine's Rule 1 enforces a pre-write audit event before any action. No fire-and-forget audit gaps detected. Verdict: **solid.**

### 8. `audit_events` triggers (append-only)

Triggers defined in [database.py:374-428](../clearledgr/core/database.py#L374-L428): `clearledgr_prevent_append_only_mutation()` raises on UPDATE/DELETE for both `audit_events` and `ap_policy_audit_events`. Installed unconditionally in `initialize()`, no flag gate. Verdict: **solid in code.** Live confirmation deferred to a one-line `pg_trigger` query.

### 9. `task_runs` durability

`TaskStore.create_task_run` ([task_store.py:54-121](../clearledgr/core/stores/task_store.py#L54-L121)) persists task rows; status updates work. Verdict: **partial** — see #10.

### 10. Task resume on startup

This one is a real gap. `app_startup.run_deferred_startup` calls `runtime.resume_pending_agent_tasks()` ([app_startup.py:79](../clearledgr/services/app_startup.py#L79)) which delegates to `drain_agent_retry_jobs()` — that targets the `agent_retry_jobs` table, **not `task_runs`**. So:

- An api crash mid-`CoordinationEngine.run` orphans the `task_runs` row.
- Nothing on boot scans `task_runs` for `pending` or in-flight rows.
- The retry-job queue is drained, but the planning-loop checkpoint is not.

Verdict: **gap.** Either resume `task_runs` on boot or document the orphan behaviour as expected.

### 11. Box lifecycle persistence

Explicit `raise_box_exception` / `record_outcome` calls write durably. Direct `update_ap_item(state=...)` calls (especially the three in `coordination_engine.py`) do **not** write a corresponding lifecycle row. Verdict: **partial.**

### 12. Observability — "what did the agent decide and why?"

Queryable today: `ap_items.state`, `rejected_by`, `rejected_at`, `rejection_reason`, `audit_events` timeline by `box_id` + `box_type`, `box_exceptions`, `box_outcomes`, `box_summary.build_box_summary` reads timeline + state.

Not directly queryable: agent reasoning summary, governance verdict, plan snapshot. These live as **JSON blobs inside `audit_events.payload_json`**, not as columns. So "give me every invoice the agent rejected because of governance veto" is a JSON-extract query, not a SQL `WHERE`. Verdict: **partial — works for a customer asking about one invoice; doesn't work for product analytics or post-hoc model evaluation.**

---

## Top findings, ranked by severity

### P0 — A subset of workspace SPA actions bypass the harness (corrected scope)

Initial framing claimed every AP item action was a bypass. Closer inspection of [ap_items_action_routes.py](../clearledgr/api/ap_items_action_routes.py) shows the picture is more nuanced:

**Already routed through `runtime.execute_intent` (4 routes — correct):**
- `/bulk-approve` (L2178), `/bulk-reject` (L2268), `/bulk-retry-post` (L2454), `/{id}/retry-post` (L1722)

**State-mutating bypasses (the actual P0 list — 10 routes):**
- `/{id}/snooze`, `/{id}/unsnooze`, `/bulk-snooze` — change `state` to/from `snoozed`
- `/{id}/reverse` — change `state` to `reversed`
- `/{id}/classify` — re-classify document type (mutates state when re-routing)
- `/{id}/resubmit` — creates supersession + new item
- `/{id}/merge`, `/{id}/split` — Box-level operations
- `/{id}/non-invoice/resolve` — change state via direct `update_ap_item`
- `/{id}/entity-route/resolve` — *partial* — already calls `runtime.append_runtime_audit`, but no `execute_intent` and no governance gate
- `/{id}/fields` (PATCH) — field-level changes including invoice_number, amount

**Non-mutating routes (correctly skip runtime):** field-review/resolve, sources/link, gmail-link, compose-link, compose/create, tasks/*, notes, comments, files. These don't mutate Box state — runtime gating is unnecessary.

**Why this matters:** for the 10 bypass routes, data integrity is still preserved (the store enforces state machine and writes audit atomically), but **governance can't veto, agent memory has no record, and the audit row carries no `decision_reason` or governance verdict**. Severity: **P0 — the moat claim ("every state-mutating action goes through the agent") is false for these 10 routes**, but the count and risk are smaller than first claimed.

### P1 — Silent `IllegalTransitionError` swallow at one site

**Single real defect.** [coordination_engine.py:594](../clearledgr/core/coordination_engine.py#L594) wraps `update_ap_item(state="needs_info", ...)` in `try: ... except Exception: pass`. If the current state can't transition to `needs_info` (e.g. the box is already terminal), the exception is silently swallowed: no log, no audit, no caller signal, no recovery. The box stays in its prior broken state and the operator never finds out. Severity: **P1 — narrow, but a true silent-failure bug.** Fix: log + propagate, or audit the violation and route to a manual queue.

### P2 — `task_runs` orphan on api crash; no startup resume

`task_runs` rows are durable but not resumed; the named "resume" function drains a different queue. An api crash mid-coordination is unrecoverable from `task_runs`. Severity: **P2 — only matters during crashes / deploys mid-flight, but Railway redeploys are frequent.**

### P3 — Two execution paths with two governance profiles

Synchronous skills get governance; async events get planning + coordination + governance; workspace SPA actions get neither. Three reliability profiles is two too many. Severity: **P3 — architectural debt, not a bug.**

### P4 — Agent reasoning is JSON-blob, not queryable

`payload_json` holds the why; columns hold the what. Fine for individual-invoice trace; insufficient for analytics, model evaluation, or audit at scale. Severity: **P4 — design choice with cost; not urgent.**

### P5 — *withdrawn after close-read*

Initially I claimed `BoxLifecycleStore` was shelfware for transitions. That was wrong: it was never meant to record transitions; it records exceptions + outcomes by design. Transitions live in `ap_items.state` (column) + `audit_events` (timeline). All four pieces of the lifecycle have homes. **No remediation needed.**

---

## What this means for the moat claim

**Honest version:** Clearledgr has the components of an agent harness, and on the async event path (Gmail webhooks → Slack action → ERP write) all of them fire correctly. That path is genuinely defensible and harder to replicate than "an LLM with a prompt."

**What we cannot truthfully claim today:**
- "Every agent action goes through deterministic planning + coordination + governance." → only on Path 1.
- "The state machine prevents invalid transitions." → DB enforces values, not transitions, and the engine itself violates this in three places.
- "Crashes resume safely." → only `agent_retry_jobs` resume; `task_runs` orphan.
- "Workspace SPA actions are governed." → no, they bypass the runtime.

The moat language can be defended once P0 and P1 are fixed. Until then, leaning on it in fundraising or sales conversations risks any technical due-diligence engineer reproducing this audit and surfacing the same gaps.

---

## Recommended remediation order

1. **P1 fix (small, ~30 min)** — Remove the silent `except Exception: pass` at [coordination_engine.py:594](../clearledgr/core/coordination_engine.py#L594). On `IllegalTransitionError`, audit the rejected transition and propagate so the orchestrator surfaces the failure.
2. **P2 fix (~2-3 hours)** — Add a `task_runs` startup scan in `app_startup.run_deferred_startup` that for each in-flight row decides: resume if recent, mark `failed` with reason `api_crash_orphan` if older than a threshold.
3. **P0 fix (~2-3 days)** — Re-route workspace SPA AP item actions through `runtime.execute_intent()`. Inventory existing intents vs. needed actions; fill gaps; migrate `ap_items_action_routes` to thin HTTP→intent wrappers. Preserve bulk-endpoint performance via batched intent execution.
4. **P3 fix (~1 week)** — Make the synchronous skill path emit a `plan_observed` audit event before execution so the planning layer is observable on both paths, even when the skill is a 1-step intent. Document the two-path contract explicitly.
5. **P4 fix (~1-2 days)** — Add structured columns (or a join table) for `decision_reason`, `governance_verdict`, `confidence_score`, `plan_id` so agent reasoning is queryable, not buried in `payload_json`.
6. ~~**P5** — withdrawn.~~

Sequencing: P1+P2 first (~half a day, low risk, builds momentum); then P0 (the customer-facing fix); then P3+P4 (architectural enhancements).

---

## What this audit does not cover

- **Live trigger verification.** Code says triggers are installed unconditionally; production confirmation is a one-line `pg_trigger` query.
- **Live agent-loop traffic.** This is a static read; whether the loop service actually serves a real Gmail-extension click in production was not exercised.
- **Cross-tenant isolation in the agent path.** Out of scope; covered by `tests/test_tenant_isolation*`.
- **Skill catalogue audit.** Whether every registered skill is well-defined / well-bounded is a separate review.
- **Governance policy correctness.** This audit verifies the gate fires; it does not audit whether the policies it consults are tight.
