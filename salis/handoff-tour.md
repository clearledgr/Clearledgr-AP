# Handoff Tour

This is the doc I wish someone had written for me before I started building Clearledgr. It's not the spec. It's the walkthrough.

If you're Suleiman or Little reading this for the first time, this is the one document you should read before you touch any code. Everything else in `salis/` is reference. This is orientation.

— Mo

---

## Who this is for

- **Suleiman (CTO):** you're inheriting the product. You'll decide the architecture from here. This doc is the mental model I used, not the mental model you have to adopt. Keep what holds, change what doesn't.
- **Little (founding engineer):** you're going to ship the next 100 commits. This doc is the map. You don't need to understand every box on day 1; you need to know where to go when you get stuck.
- **Future hires:** this is how you earn the right to have opinions about the architecture. Read this. Read the ADRs. Then form an opinion.

Mo is always reachable post-handoff for the things that aren't in docs. After that, the docs are the source of truth. If the docs and the code disagree, the code wins and the doc gets a PR.

---

## What's in your head after reading this

1. The product is a coordination agent, not an automation tool.
2. The **Box** is the central abstraction. Everything else is adapters.
3. Every agent action writes its audit row BEFORE it runs. This is Rule 1.
4. There are three surfaces: Gmail for work, Slack for decisions, ERP for record.
5. One engine drives every workflow type. Teams, Outlook, clawback are adapters behind feature flags.
6. The code is well-tested and well-architected. It has never run end-to-end against live Gmail/Slack/ERP with a real customer. Closing that gap is the next priority.

---

## Start here: three files, in this order, one hour

If you have one hour, read these three files and skim `CLAUDE.md`:

### 1. `main.py`

Read lines 1-300. This is the app bootstrap + the **strict runtime surface profile** — the mechanism that mounts only V1 routes in production. Every route not on the allow-list returns `endpoint_disabled_in_ap_v1_profile`. This is how we keep V1 narrow while letting code for V1.1 features (Teams, Outlook) live in the tree without exposure.

Then read around lines 1228-1309 — the `/health` endpoint. It tells Railway whether to route traffic. If Redis is unreachable in production, it flips to unhealthy and Railway pulls the service out of rotation. This is deliberate.

### 2. `clearledgr/core/coordination_engine.py`

Read the class docstring and `execute()` at line 243. This is the engine that drives every Box. Steps 1-7 of its execution loop are literally spelled out in the code. Pay attention to `_pre_write` at 375 — this is where Rule 1 is enforced. Pay attention to `_Rule1PreWriteFailed` at 73 — this is what happens when Rule 1 can't hold and the engine has to fail closed.

### 3. `clearledgr/core/plan.py`

Read the whole file. It's short. `Action` and `Plan` are the dataclasses the engine consumes. Every handler in `coordination_engine.py`'s `_register_handlers` maps an action name to a method. That's the whole dispatch model.

### Skim `CLAUDE.md`

Not code, but context. The "Working Rules" section at the bottom is how I think about building this. Adopt what makes sense, discard what doesn't.

---

## The mental model

Here's the sentence I use when I have to explain Clearledgr in 15 seconds:

> Clearledgr is an AP coordination agent that lives in your Gmail, routes approvals through Slack, and posts bills into your ERP.

Unpack that:

- **Coordination agent, not automation.** Automation tools run scripts. Clearledgr advances Boxes through state machines while preserving an auditable trail. The agent plans; deterministic code executes.
- **Lives in your Gmail.** Email is the intake surface and the contextual work surface. Via InboxSDK (MV3). The sidebar shows Box state, not inbox metadata.
- **Routes approvals through Slack.** Slack cards carry the approve/reject/needs-info buttons. Teams is V1.1 (feature-flagged off).
- **Posts bills into your ERP.** We write, we don't own. ERP remains the ledger of record. Four ERPs supported: QuickBooks, Xero, NetSuite, SAP.

The product thesis is: finance work today is scattered across tools that don't talk. ERP holds results. Email holds fragments. Slack holds conversation. State lives in human memory. Clearledgr gives every workflow instance a persistent home — a **Box** — that renders into every surface without losing the thread.

---

## The Box, explained

This is the abstraction that makes the product make sense. Everything else is adapters on top of it.

A **Box** is a persistent home for one workflow instance. Today we have two Box types live:

- `ap_item` — one AP invoice. Goes from `received` → `validated` → `needs_approval` → `approved` → `ready_to_post` → `posted_to_erp`. Defined in `clearledgr/core/ap_states.py`.
- `vendor_onboarding_session` — one vendor going through KYC + bank verification. Goes `invited` → `kyc` → `bank_verify` → `bank_verified` → `ready_for_erp` → `active`. Defined in `clearledgr/core/vendor_onboarding_states.py`.

One committed but frozen:

- `clawback_case` — one commission clawback. See `commission-clawback-spec.md`. Frozen pending V1 launch.

Every Box has:

- An **id** (globally unique).
- A **state** (from a typed state machine).
- A **timeline** — the audit chain, one row per meaningful event, in `audit_events`.
- Optional **links** to other Boxes (the `box_links` table — e.g., this AP invoice is linked to this vendor onboarding session).

Every write to shared primitives (`audit_events`, `llm_call_log`, `pending_notifications`) is keyed on `(box_id, box_type)`. There's a `BoxType` registry at `clearledgr/core/box_registry.py`. When we add clawback, the registry gets one new entry and 90% of the infrastructure works for free.

This is Migration v42. Before it, these tables were keyed on `ap_item_id`, and vendor onboarding had to pass empty strings — the "semantic lie" I fixed in the Box Foundation commit (see ADR-001 when it's written).

---

## Rule 1: audit BEFORE action

Read `coordination_engine.py:375` — `_pre_write`.

The rule is: **every agent action writes its timeline entry BEFORE it executes.** If the pre-write can't land (DB blip, network fail, whatever), the action is aborted and `_Rule1PreWriteFailed` is raised. No ERP post happens without an audit row. No vendor email goes out without an audit row. No Slack card is sent without an audit row.

Why: if Clearledgr's value proposition is "trustworthy audit trail," then the audit trail has to be stronger than the side effects. If a bill posts to QuickBooks with no audit row, the customer's compliance story just broke. The thesis (DESIGN_THESIS.md §7.6) rests on this being airtight.

I spent real time getting this right. The engine retries the pre-write 3 times with backoff. Only after 3 failures does it give up. Even then, it parks the Box in an exception state with a clear error, so an operator can see the Rule 1 failure in the audit chain.

**Don't loosen this invariant.** Don't add a code path that "just this once" skips the pre-write. If you find yourself wanting to, read ADR-004 (once it's written) first.

---

## A real invoice, step by step

Imagine a vendor sends an invoice to the customer's AP inbox. Here's the code path.

1. **Gmail Pub/Sub push → `clearledgr/api/gmail_webhooks.py:855`**
   The `@router.post("/push")` handler receives the notification. It verifies the Pub/Sub JWT (OIDC from Google, at line 712) before anything else — fail-closed on signature failure.

2. **Background task → `process_gmail_notification()` at line 991**
   The push handler kicks off a background task so it can return 200 fast. The actual work happens in the loop.

3. **Email classification → `classify_email_with_llm()` around line 1213**
   Claude is asked: is this an invoice? If the classification confidence is below 0.7, we drop (line 1234 area). That's a silent drop point — something I'd want better observability on eventually.

4. **AP item creation**
   If it's an invoice, we create an `ap_items` row with `state=received`. The audit chain starts here.

5. **Extraction → `clearledgr/services/email_parser.py`**
   Claude extracts vendor, amount, invoice number, due date, PO number. Each extracted field carries a confidence score. Confidence is visible in the UI so operators know what to double-check.

6. **Validation gate → `clearledgr/services/invoice_validation.py`**
   Deterministic rules: PO required? Duplicate? Over threshold? Each rule produces a reason code. If any rule fails hard, the Box moves to `needs_info` and surfaces in the sidebar with a reason.

7. **Planning → `clearledgr/core/agent_runtime.py`**
   The `AgentPlanningEngine` runs a Claude tool-use loop to decide the next actions. The plan is written to `task_runs` for crash-resume.

8. **Coordination → `clearledgr/core/coordination_engine.py`**
   The engine consumes the plan, one action at a time, with Rule 1 pre-writes between every step.

9. **Slack card → `clearledgr/services/slack_api.py`**
   If the action is `send_slack_approval`, a card posts to the configured channel. The `channel_threads` table records (ap_item_id, channel=slack, thread_id, state=pending).

10. **Approval received → `clearledgr/api/slack_invoices.py`**
    The Slack interactive handler verifies HMAC (`clearledgr/core/slack_verify.py`), finds the `channel_threads` row, looks up the Box, advances state. `channel_threads.state` goes to `approved`.

11. **ERP post → `clearledgr/integrations/erp_router.py`**
    Dispatcher picks QuickBooks / Xero / NetSuite / SAP based on the org's connection. Each connector is in `clearledgr/integrations/erp_<name>.py`. Bill posts. `ap_items.erp_reference` gets set.

12. **Timeline reconstruction**
    The Box's audit chain now has every step. `list_box_audit_events(box_type='ap_item', box_id=...)` returns the complete story. This is the customer-facing "show me what happened with this invoice" feature.

For each step, the pre-write at step N means an audit row exists that says "about to try X" before X runs. If X fails, a post-write records "X failed because Y." The timeline is self-narrating.

---

## The abstractions you'll meet

| Abstraction | File | What it is |
|---|---|---|
| **Box** | `clearledgr/core/box_registry.py` | Registered types. Add a type here to add a workflow class. |
| **State machine** | `clearledgr/core/ap_states.py`, `vendor_onboarding_states.py` | Typed enum + `VALID_TRANSITIONS` dict. `transition_or_raise` enforces. |
| **Plan + Action** | `clearledgr/core/plan.py` | Dataclasses the engine consumes. Plans are JSON-serializable for crash resume. |
| **Skill** | `clearledgr/core/skills/` | APSkill, CompoundSkill. Exposes Claude-callable tools. Skills don't execute actions — they're the LLM interface. |
| **Coordination engine** | `clearledgr/core/coordination_engine.py` | Executes plans. Enforces Rule 1. Handles retries, waits, post-writes. |
| **Planning engine** | `clearledgr/core/agent_runtime.py` | `AgentPlanningEngine`. Runs the Claude tool-use loop. Produces Plans. |
| **Finance agent runtime** | `clearledgr/services/finance_agent_runtime.py` | The public facade. `execute_ap_invoice_processing()` is what `agent_orchestrator.process_invoice` calls. |
| **Event queue** | `clearledgr/core/event_queue.py` | Redis Streams in prod, in-memory in dev. Events drive the planning engine. |
| **TaskRun** | `clearledgr/core/stores/task_store.py` | Crash-resume primitive. Idempotency key on `(task_type, key)`. |
| **audit_events** | `clearledgr/core/stores/ap_store.py:append_audit_event` | The universal timeline. Every write goes through this funnel. |
| **channel_threads** | `clearledgr/core/stores/ap_store.py:upsert_channel_thread` | Per-Box per-channel state (Slack thread, Teams conversation). |
| **box_links** | `clearledgr/core/stores/pipeline_store.py:link_boxes` | Cross-Box relationships. Invoice ↔ vendor-onboarding. |
| **LLM call log** | `clearledgr/core/llm_gateway.py` | Every Claude call logged with `box_id` for cost + reconstructability. |

You can walk any of these on your own. They're all short and have docstrings that explain the why, not just the what.

---

## Where state lives

This is the question that will confuse every new engineer. Write it down once, internalize:

- **Box record + metadata** → `ap_items` (AP) or `vendor_onboarding_sessions` (vendor). Authoritative for current state.
- **Timeline** → `audit_events`. Keyed on `(box_id, box_type)`. Append-only.
- **Cross-surface state** → `channel_threads` for Slack/Teams per-Box state. Not in the Box record.
- **Cross-Box relationships** → `box_links`. Pair-oriented.
- **Planning + retry state** → `task_runs`. Survives process crash.
- **Agent event log** → `agent_events` + Redis streams (if REDIS_URL set).
- **LLM cost + linkage** → `llm_call_log`.
- **ERP state** → ERP is the source of truth post-post. We reflect `ap_items.erp_reference`, never mirror the full ERP record.

If you're looking at a Box in an exception state and wondering "what actually happened?", the answer is always `list_box_audit_events`. If that doesn't tell you, something broke Rule 1 and you have a bigger problem than the bug you came in looking for.

---

## The hardest parts — where I spent the most time

- **Rule 1 atomicity.** State UPDATE + audit INSERT have to share one transaction. `vendor_store.transition_onboarding_session_state` (line 1785) is the reference implementation. When in doubt, copy its shape.
- **Idempotency.** It's everywhere. DB UNIQUE on `audit_events.idempotency_key`. API-layer caching via `clearledgr/core/idempotency.py`. Per-decision keys on approvals. If you add a mutating endpoint, ask: "what happens if this fires twice?" If you don't know, you're about to ship a bug.
- **Multi-tenant isolation.** Every query filters by `organization_id`. `soft_org_guard` (in `clearledgr/api/deps.py`) catches query-param spoofing. `verify_org_access` is per-handler. `tests/test_tenant_isolation.py` is the regression fence. If you add a new list/get endpoint and it doesn't filter by org, the test will catch it. If it doesn't, update the test.
- **Gmail token refresh race.** QuickBooks and Xero invalidate the prior refresh token on every refresh. Two concurrent 401s → both try to refresh → one wins, the others burn the RT and brick the connection. See the big comment in `erp_router.py:215`. Two-tier lock (Redis SETNX + in-process asyncio). Do not remove this without understanding why it exists.
- **The `ap_item_id` → `box_id` migration (v42).** The whole point is that audit, LLM log, and pending notifications are Box-keyed now. Don't assume `ap_item_id` anywhere new.

---

## What I'm proud of

{{ TODO Mo: fill in your own list. Mine would be:

- The audit-first invariant actually holds end-to-end. I watched it work this session.
- Four ERPs behind one contract with per-ERP signature verification following each vendor's documented standard.
- Box reconstructability. A Box's state can be rebuilt from its timeline alone. The timeline IS the state.
- Feature-flagged surface — Teams/Outlook are code-ready, not customer-ready, and that's the right shape.
- The hardening pass: idempotency, cross-tenant fence, RTL-override rejection on portal input. Small things that would be very expensive to retrofit.

Pick the ones that feel true to you and add any I missed. Delete this TODO block once done. }}

---

## What I'd change if I had time

{{ TODO Mo: this is the most honest section — where you admit what you know is wrong. Be direct. Candidates from my observation this session:

- The classification confidence drop at `gmail_webhooks.py:1234` is silent. Should emit an audit event.
- The `ap_item_service._SharedProxy` pattern is clever but confusing. A new engineer will stare at `shared._require_item` and not know where it resolves. Worth either documenting better or just inlining.
- The legacy `append_ap_audit_event` alias (kept for callers) should be removed now that the big migration is done.
- Tests use a mix of temp-path SQLite and mock DB patterns. Standardize on one (probably the temp-path pattern, since it catches more real bugs).
- Some of the `try/except: logger.warning(); pass` patterns deserve the same scrutiny we gave `gmail_labels.remove_label` this session.
- The extension has a bunch of source-grep tests that are brittle. Replace with behavioral tests where the value is real.

Fill in your own. The honest list matters more than the complete list. }}

---

## Gotchas and footguns

Things that will bite you.

1. **Don't widen `except Exception`.** If you see `except Exception: pass` or `except Exception as e: logger.warning(...); pass`, you're probably looking at a bug in waiting. I narrowed two of these this session. There are more.

2. **Don't add a state transition without updating `VALID_TRANSITIONS`.** The state machine is a dict. Miss an edge, the transition raises `IllegalVendorOnboardingTransitionError` (or AP equivalent). This is *good* — it tells you to update the dict. Don't just add states in the handler code.

3. **Don't add action handlers without registering in `coordination_engine._register_handlers`.** The engine dispatches by action name. Unregistered action = abort.

4. **`append_audit_event` requires `(box_id, box_type)` or `ap_item_id`.** It raises ValueError if you pass neither. This is intentional — audit rows without Box context are useless.

5. **`save_three_way_match` in `purchase_orders.py` is audit-first after the override fix we did this session.** Match state commits AFTER the audit row. If audit fails, state doesn't move. Don't revert this.

6. **Railway healthcheck is at `/health`, not `/healthz`.** `railway.toml:32`. If you move the endpoint, update both.

7. **The strict runtime profile is enforced at mount time.** Adding a new route means adding it to one of the allow-lists in `main.py` (`STRICT_PROFILE_ALLOWED_*`) or it returns `endpoint_disabled_in_ap_v1_profile`. This is annoying but it's how V1 stays narrow.

8. **Fernet keys are derived from `TOKEN_ENCRYPTION_KEY` via SHA-256 + base64.** If you rotate the key, bank details and onboarding tokens become undecryptable. This is a real rotation story we have not solved.

9. **The in-memory event queue is a non-durable fallback.** If REDIS_URL is unset in prod, the `/health` endpoint flips to unhealthy. Don't disable that check to silence an alarm.

10. **Every new handler should be tested with tmp-path SQLite.** The `conftest.py` autouse fixtures reset the DB singleton and service caches. Follow the existing pattern in `tests/test_box_invariants.py`.

---

## Operational reality (as of handoff)

Be honest with yourselves about where the product actually is:

- **Tests:** 2375 backend tests passing. 100 extension tests passing. 0 integration tests with live external services.
- **Railway:** the production deploy is cold as of this writing. Needs to be warm before any customer work.
- **Integrations:** each ERP + Gmail + Slack has been tested against its mock. None has been exercised against its real sandbox with a real customer invoice.
- **Staging:** we don't have one separate from prod. The first customer's "prod" is also their staging. Fix this before customer #2.
- **Monitoring:** Sentry is wired, nobody is paged on it. PagerDuty / OpsGenie is not set up.
- **Runbooks:** `salis/operations.md` has them (will have them — write it). Until then: restart = Railway redeploy. Rollback = `git revert`. Incident response = read logs, hope.

None of this is a blocker for the first pilot. All of it will become blocking once pilot #2 shows up. The CEO plan (`~/.gstack/projects/clearledgr-Clearledgr-AP/ceo-plans/2026-04-21-path-to-first-customer.md`) treats the first pilot as the validation moment for the whole integration layer. It is.

---

## Reading order (if you have a week)

Week 1:
1. This doc.
2. `DESIGN_THESIS.md` §1-§10 — the product thesis. If you disagree with the thesis, raise it fast before you ship code against it.
3. `AGENT_DESIGN_SPECIFICATION.md` §1-§13 — the agent architecture.
4. `salis/architecture.md` (once written) — the single-page map.
5. Read `main.py`, `coordination_engine.py`, `plan.py` with the model in mind.

Week 2:
6. Pick one Box type (AP item is easier than vendor onboarding) and trace it end-to-end. Stdoutsourced trace: `grep -n "ap_items" clearledgr/**/*.py` gets you started.
7. Read the `tests/test_box_invariants.py` file. Those tests encode the thesis-level guarantees. If they fail, the product is broken in a way that matters.
8. Pair with Mo on one real Cowrywise or test invoice going end-to-end if possible. Nothing beats watching it run.

Week 3+:
9. Pick up a real task. Ship something small. Ask questions in PR comments, not in DM — that way the answers are captured.

---

## Where to go when stuck

Order of lookup:

1. **This doc + the rest of `salis/`** — check here first.
2. **`CLAUDE.md`** — the active instruction set for how we work.
3. **`DESIGN_THESIS.md`** — the product doctrine. When in doubt about "should we build X?", the thesis has usually already answered.
4. **`AGENT_DESIGN_SPECIFICATION.md`** — the agent's canonical design.
5. **`salis/adrs/`** — why we chose the architecture we did. If something looks wrong, the ADR usually explains why it's actually right.
6. **The tests.** `tests/test_box_invariants.py`, `tests/test_endpoint_idempotency.py`, `tests/test_tenant_isolation.py` — these three encode the load-bearing guarantees. Read them like documentation.
7. **The code.** Handlers have docstrings. Comments near complex sections explain the tradeoffs.
8. **Mo** — first two weeks post-handoff for non-docs questions. After that, async only, and only for things that weren't in docs but should have been (then a doc PR is the answer).

---

## What I won't be around to tell you

Most of the "why" lives in `salis/adrs/`. Write new ones as you make architectural decisions. The rule I used: if someone six months later might come along and want to undo this decision without the context that produced it, write an ADR.

The ADRs I know need writing (see `salis/adrs/`):
- ADR-001: Why the Box abstraction
- ADR-002: Why feature-flagged surface (Teams/Outlook) instead of separate services
- ADR-003: Why Gmail-first + Slack-decision + ERP-of-record
- ADR-004: Why Rule 1 (audit before action)
- ADR-005: Why four ERPs at V1
- ADR-006: Why no payment execution (not V1, not V2)
- ADR-007: Why `idempotency_key` piggybacks on audit_events
- ADR-008: Why commission clawback is frozen (not cancelled)
- ADR-009: Why KYC + open-banking providers are stubbed
- ADR-010: Why NFKC + RTL rejection on portal input

Each is short — half a page. Title, context, decision, consequences, alternatives considered. Don't write essays.

---

## A note on voice and opinion

{{ TODO Mo: write a short closing — something like:

"I had strong opinions while I built this. Some are right, some aren't. Keep the ones that make the product better. Change the ones that don't. The product is not the code. The product is what a finance team experiences when they use it. Everything in this codebase is in service of that."

Or whatever feels true to you. Sign it. }}

— Mo
