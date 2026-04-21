# Architecture

Single-page system map. Companion to `handoff-tour.md` (which is narrative); this one is structural.

**One-sentence version:** one coordination engine drives typed Boxes through state machines, with thin adapters for each inbound surface (Gmail, Slack, ERPs) and outbound action, backed by Postgres + Redis, with every side effect gated by a pre-written audit row.

---

## Three layers

Every file in the repo belongs to one of these three layers. If you're not sure where a new module goes, the layer it fits determines the directory.

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                     SURFACES (inbound)                          │
  │  Gmail │ Slack │ Teams* │ Outlook* │ ERP webhooks │ Portal      │
  │   Pub/Sub    events    events      events      HMAC    HTTP    │
  └────────────────────────────┬────────────────────────────────────┘
                               │  every inbound is signature-verified
                               ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                    CORE (the product)                           │
  │                                                                 │
  │   API routers  ──────►  Services (business logic)               │
  │   (clearledgr/api/)     (clearledgr/services/)                  │
  │                              │                                  │
  │                              ▼                                  │
  │      DeterministicPlanningEngine ──► CoordinationEngine         │
  │      (event → Plan via rules)        (Rule 1, action dispatch)  │
  │                              │                                  │
  │                              ▼                                  │
  │        BoxRegistry  ─  State machines  ─  Box lifecycle         │
  │        (ap_item,       (ap_states,       records (exceptions    │
  │         vendor_ob..)    vendor_ob..)      + outcomes tables)    │
  └────────────────────────────┬────────────────────────────────────┘
                               │  stores + gateways
                               ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                  PERSISTENCE (state)                            │
  │                                                                 │
  │   ClearledgrDB (store mixins)  ──►  Postgres (prod)/SQLite      │
  │   Event queue                   ──►  Redis Streams/in-memory    │
  │   LLM Gateway                   ──►  Anthropic API + call log   │
  │   ERP router                    ──►  QBO / Xero / NS / SAP      │
  └─────────────────────────────────────────────────────────────────┘

  * Teams, Outlook = V1.1 feature-flagged off by default.
```

---

## Module map (what lives where)

### `clearledgr/api/` — HTTP routers

37 route modules. Each owns a domain:

- `gmail_webhooks.py` — Pub/Sub push handler, label-change webhook, Gmail OAuth helpers.
- `gmail_extension.py` + `gmail_extension_models.py` + support routes — everything the Gmail sidebar talks to.
- `slack_invoices.py` — Slack card callbacks (approve/reject/needs-info).
- `teams_invoices.py` — Teams bot (V1.1, feature-flagged).
- `outlook_routes.py` — Outlook autopilot + webhook (V1.1, feature-flagged).
- `erp.py`, `erp_connections.py`, `erp_oauth.py`, `erp_webhooks.py` — the four ERP endpoints.
- `ap_items.py` + `ap_items_action_routes.py` + `ap_items_read_routes.py` — AP item CRUD and action endpoints.
- `vendor_portal.py` — the only unauthenticated HTML surface (magic-link KYC + bank submission).
- `ops.py` — operational endpoints (health, KPIs, tenant-health, monitoring).
- `auth.py`, `deps.py` — JWT auth, soft_org_guard, verify_org_access.

### `clearledgr/core/` — engine + contracts

- `planning_engine.py` — `DeterministicPlanningEngine`. Event → Plan via rules (no LLM here). Raises on unhandled event types and records a `box_exceptions` row if the event names a Box.
- `coordination_engine.py` — `CoordinationEngine`. Consumes Plans. Enforces Rule 1 via `_pre_write`. Dispatches actions via `_handlers`.
- `plan.py` — `Action` and `Plan` dataclasses.
- `box_registry.py` — `BoxType` dataclass + registered types.
- `ap_states.py` — AP state machine (APState enum + VALID_TRANSITIONS).
- `vendor_onboarding_states.py` — Vendor onboarding state machine.
- `event_queue.py` — Redis Streams wrapper with in-memory fallback.
- `events.py` — AgentEventType enum + AgentEvent dataclass.
- `llm_gateway.py` — Anthropic client + cost + call log.
- `idempotency.py` — API-layer idempotency helper.
- `erp_webhook_verify.py` — HMAC verification for all four ERPs.
- `portal_auth.py`, `portal_input.py` — magic-link auth + vendor-portal input validation.
- `migrations.py` — schema migrations (currently v43).
- `database.py` — `ClearledgrDB` — the database singleton (composes store mixins).
- `errors.py` — `safe_error()` — the exception-sanitizer for API responses.
- `stores/` — 21 store mixins (including `box_lifecycle_store.py` for first-class exceptions + outcomes). See below.

### `clearledgr/core/stores/` — database mixins

Each mixin handles one domain's SQL. `ClearledgrDB` inherits all of them; read/write methods get grouped by domain naturally.

- `ap_store.py` — AP items, audit_events, channel_threads. The big one. `update_ap_item` is the state-change funnel — atomic state UPDATE + audit INSERT in a single `conn.commit()`, and post-commit mirrors exception_code / terminal-state transitions into `box_exceptions` / `box_outcomes`.
- `box_lifecycle_store.py` — `box_exceptions` (multiple per Box, severity + raised_by + resolved_by) and `box_outcomes` (UNIQUE per Box, terminal record). Every mutation narrates to `audit_events` AND emits a `box.exception_raised` / `box.exception_resolved` / `box.outcome_recorded` webhook to subscribed customers.
- `vendor_store.py` — vendor profiles, vendor_invoice_history, vendor onboarding sessions.
- `onboarding_token_store.py` — magic-link tokens for vendor portal.
- `task_store.py` — task_runs (crash-resume primitive).
- `purchase_order_store.py` — POs, GRs, 3-way matches.
- `approval_chain_store.py` — approval policy chains.
- `bank_details.py` — Fernet-encrypted bank details.
- `entity_store.py` — multi-entity routing (V1.1).
- `integration_store.py` — integration registry (Gmail tokens, Slack installs, ERP connections).
- `metrics_store.py` — Box health drill-downs, KPIs.
- `payment_store.py` — payment confirmation tracking.
- `pipeline_store.py` — Pipeline views + box_links.
- Others: `ap_runtime_store`, `auth_store`, `dispute_store`, `override_window_store`, `policy_store`, `recon_store`, `webhook_store`.

### `clearledgr/services/` — business logic (50+ modules)

Top offenders to know:

- `finance_agent_runtime.py` — `FinanceAgentRuntime`. Public facade over the planning + coordination engines.
- `agent_orchestrator.py` — `process_invoice()`. The entry point from gmail_webhooks into the runtime.
- `invoice_workflow.py` — `InvoiceWorkflowService`. State machine transitions + approval logic.
- `invoice_validation.py` — deterministic validation gate (PO required, duplicates, fraud controls).
- `invoice_posting.py` — `approve_invoice()`, `reject_invoice()`. The canonical approval entry points.
- `ap_item_service.py` — the AP item context builder (`_build_context_payload`).
- `ap_decision.py` — `APDecisionService`. Rule-based routing cascade (approve / needs_info / escalate / reject). No LLM — deck promise "rules decide, LLM describes."
- `agent_background.py` — the continuous background loops (chases, snoozes, reconciliation, approval timeouts).
- `app_startup.py` — deferred startup tasks (autopilots, agent resume, one-shot reaper sweep).
- `email_parser.py` — Claude-driven invoice field extraction.
- `gmail_labels.py` — canonical label taxonomy + bidirectional sync.
- `gmail_autopilot.py` — the watch-gmail-for-new-messages loop.
- `slack_cards.py` + `slack_api.py` — outbound Slack card rendering + dispatch.
- `rate_limit.py` — Redis-backed sliding window.
- `monitoring.py` — health check implementations.
- `webhook_delivery.py` — outbound webhook subscriptions + delivery.
- `report_export.py` — AP aging / vendor spend / posting status reports.

### `clearledgr/integrations/` — ERP connectors

- `erp_router.py` — dispatcher. Picks QB/Xero/NS/SAP per-org. `ERPConnection` dataclass. Refresh-token lock.
- `erp_quickbooks.py`, `erp_xero.py`, `erp_netsuite.py`, `erp_sap.py` — one file per ERP.
- `erp_sanitization.py` — shared sanitization.
- `erp_rate_limiter.py` — per-ERP rate limiting.
- `oauth.py` — shared OAuth helpers.

### `clearledgr/workflows/` — named workflow handlers

- `gmail_activities.py` — `classify_email`, `match_bank_feed`, `match_erp` — the pieces the planning engine composes.

### `clearledgr/di/` — dependency injection container

- `container.py` — service singletons (audit, llm, exception router, sap adapter).

### `clearledgr/templates/` — server-rendered HTML

- Jinja2 templates for the vendor portal (the only server-rendered HTML surface).

### `ui/`

- `gmail-extension/` — MV3 Chrome extension built on InboxSDK.
- `outlook-addin/` — V1.1 feature-flagged off.
- `slack/` — Slack manifest + bot assets.
- `shared/` — shared UI assets.

---

## System diagram — one invoice, end to end

```
  GMAIL INBOX
     │  new email
     ▼
  Google Pub/Sub
     │  push notification
     ▼
  ┌────────────────────────────────────────────────────────────┐
  │ POST /gmail/push  (gmail_webhooks.py)                      │
  │   verify OIDC JWT  ──►  enqueue background task            │
  └─────────────────────────────┬──────────────────────────────┘
                                ▼
  ┌────────────────────────────────────────────────────────────┐
  │ process_gmail_notification (gmail_webhooks.py:991)         │
  │   classify_email_with_llm  ──►  email_parser.parse_email   │
  │   (Claude call, logged     │    (Claude call, fields +     │
  │    to llm_call_log)        │     confidences)              │
  │                            ▼                               │
  │                  db.create_ap_item  (Box is born)          │
  │                            │                               │
  │                            ▼                               │
  │            invoice_validation.run_gate  (deterministic)    │
  │                            │                               │
  │                            ▼                               │
  │            agent_orchestrator.process_invoice              │
  └─────────────────────────────┬──────────────────────────────┘
                                ▼
  ┌────────────────────────────────────────────────────────────┐
  │ FinanceAgentRuntime.execute_ap_invoice_processing          │
  │   ──► DeterministicPlanningEngine.plan(event, box_state)   │
  │        ──► rules produce Plan (no LLM — deck §7.1)         │
  │   ──► CoordinationEngine.execute(plan)                     │
  │        ┌──────────────────────────────────────┐            │
  │        │ for each Action in plan:             │            │
  │        │   _pre_write (Rule 1 — audit first)  │            │
  │        │   dispatch to _handle_<action>       │            │
  │        │   _post_write (outcome audit)        │            │
  │        └──────────────────────────────────────┘            │
  │   Five actions call Claude (§7.1): classify_email,         │
  │   extract_invoice_fields, generate_exception_reason,       │
  │   classify_vendor_response, draft_vendor_response.         │
  │   Everything else is deterministic.                        │
  └─────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
   ┌──────────────────┬─────────────────┬─────────────────┐
   │ send_slack_      │ update_ap_label │ link_vendor_    │
   │ approval         │ (Gmail label    │ to_box          │
   │ (slack_api)      │  sync)          │ (box_links)     │
   └────────┬─────────┴─────────────────┴─────────────────┘
            │
            ▼
  SLACK CARD rendered in the configured channel
            │
            │  user clicks approve
            ▼
  ┌────────────────────────────────────────────────────────────┐
  │ POST /slack/events  (slack_invoices.py)                    │
  │   verify HMAC v0  (slack_verify.py)                        │
  │   find channel_threads row  ──►  look up Box               │
  │   invoice_posting.approve_invoice                          │
  └─────────────────────────────┬──────────────────────────────┘
                                ▼
  ┌────────────────────────────────────────────────────────────┐
  │ erp_router.post_bill                                       │
  │   route by erp_type ──►  post_bill_to_quickbooks /         │
  │                          post_bill_to_xero / netsuite/sap  │
  │   on 401 ──► refresh token (Redis lock) ──► retry once     │
  │   on success ──► ap_items.erp_reference = <id>             │
  └─────────────────────────────┬──────────────────────────────┘
                                ▼
  AUDIT TIMELINE
  list_box_audit_events(box_type='ap_item', box_id=...)
    ──► full reconstruction of what just happened
```

Every step between `_pre_write` and `_post_write` is guarded. The audit row exists before the side effect runs.

---

## Box lifecycle

```
  ┌──────────┐   email arrives   ┌───────────┐   extraction ok   ┌───────────┐
  │ received │──────────────────►│ validated │──────────────────►│ needs_    │
  └──────────┘                   └─────┬─────┘                   │ approval  │
        │                              │                          └─────┬─────┘
        │                              │  validation fails              │
        │                              ▼                                │
        │                        ┌───────────┐                          │
        └───────────────────────►│ needs_info│                          │
                                 └─────┬─────┘                          │
                                       │  operator resolves             │
                                       └──────────────────────┐         │
                                                              ▼         │
                                                        (back to validated)
                                                                        │
                                                                        ▼
                                                                 ┌───────────┐
                                                                 │ approved  │
                                                                 └─────┬─────┘
                                                                       │
                                                                       ▼
                                                                 ┌───────────┐
                                                                 │ ready_to_ │
                                                                 │ post      │
                                                                 └─────┬─────┘
                                                                       │  ERP post
                                                                       ▼
                                                 ┌──────────────────────────┐
                                                 │  posted_to_erp           │
                                                 └──────────────────────────┘
                                                       │              │
                                                       │ reverse      │ fail
                                                       ▼              ▼
                                                 ┌──────────┐   ┌────────────┐
                                                 │ reversed │   │ failed_post│
                                                 └──────────┘   └────────────┘
```

State machine is in `clearledgr/core/ap_states.py`. `VALID_TRANSITIONS` is the dict of allowed edges. `transition_or_raise` is the one-line enforcement. The same shape exists for vendor onboarding in `vendor_onboarding_states.py`.

---

## Box contract (the deck promise)

Every workflow instance becomes a persistent, attributable record of four things:

| Part | Home | Written by |
|---|---|---|
| **State** | `ap_items.state` (or `vendor_onboarding_sessions.state`) | `update_ap_item` — atomic UPDATE + audit INSERT |
| **Timeline** | `audit_events` keyed on `(box_type, box_id)` | `append_audit_event` — single funnel, 30+ callers |
| **Exceptions** | `box_exceptions` — multiple rows per Box, severity + raised_by + resolved_by | `raise_box_exception` / `resolve_box_exception` — mirrored automatically from `update_ap_item` when `exception_code` changes |
| **Outcome** | `box_outcomes` — UNIQUE `(box_type, box_id)`, terminal record | `record_box_outcome` — mirrored from `update_ap_item` on transitions to `posted_to_erp` / `rejected` / `reversed` |

Read the full Box as one payload: `GET /api/ap_items/{id}/box` returns `{state, timeline, exceptions, outcome}`. This is the canonical endpoint every surface (Gmail sidebar, Slack card, admin console) should consume instead of reading `ap_items.exception_code` / `erp_reference` directly.

Customers can subscribe to `box.exception_raised`, `box.exception_resolved`, `box.outcome_recorded` via the existing `/webhooks` subscription surface.

---

## Key contracts (the seams that matter)

These are the interfaces where layers meet. Changing any of these is a breaking change across the codebase.

1. **`append_audit_event(payload: dict)`** (`ap_store.py:1648`)
   The single funnel for every audit write. Requires `(box_id, box_type)` or `ap_item_id`. Enforces `idempotency_key` UNIQUE on insert. 30+ callers in services/ go through this.

2. **`CoordinationEngine._handlers`** (`coordination_engine.py:120`)
   The action-to-method registry. Every action name in a Plan must map here. Adding an action = adding a key + handler.

3. **`AgentEventType`** (`events.py:21`)
   The event enum. Every event the planning engine can react to. Adding an event type = adding to the enum + `_plan_<type>` in `planning_engine.py`.

4. **`ERPConnection` dataclass** (`erp_router.py:158`)
   Uniform across all four ERPs. When adding a new ERP, add the fields to this dataclass; `_erp_connection_from_row` handles the DB round-trip.

5. **ERP webhook contract** (`erp_webhook_verify.py`)
   Per-ERP signature verification functions. Adding a new ERP webhook = adding a verifier + a route in `erp_webhooks.py`.

6. **`BoxType` registry** (`box_registry.py`)
   Adding a Box type = registering in this module + updating state machine + updating `_register_handlers` in coordination_engine for any new action types.

---

## Feature-flagged / deferred components

These exist in code but are off by default in V1:

- `FEATURE_TEAMS_ENABLED=false` → `clearledgr/api/teams_invoices.py` routes don't register.
- `FEATURE_OUTLOOK_ENABLED=false` → `clearledgr/api/outlook_routes.py` + `clearledgr/services/outlook_autopilot.py` gated.
- `commission-clawback-spec.md` — frozen; Box type not registered yet. V1.2 clawback event types are in the enum but have no planners — `DeterministicPlanningEngine` raises and records a `box_exceptions` row if a producer fires one early.
- KYC + open-banking providers in vendor portal — stubbed until contracts signed. See `services/vendor_onboarding_lifecycle.py` for the stub boundaries.

Retired in the 2026-04-21 drift cleanup (history lives in ADR 011 if you write one):

- `AgentPlanningEngine` (Claude tool-use loop) — replaced by `DeterministicPlanningEngine`. Deck promise: rules decide, LLM describes.
- `clearledgr/core/skills/` (APSkill, CompoundSkill, ReconSkill) — dead code; all actions now dispatch via `CoordinationEngine._handlers`.
- Env vars `AGENT_PLANNING_LOOP`, `AGENT_LEGACY_FALLBACK_ON_ERROR`, `AGENT_RUNTIME_MODEL` — removed.
- `APDecisionService` Claude call path — retired; the 10-step rule cascade in `_compute_routing_decision` is now the single source of routing truth.

Strict runtime profile (`main.py:252`-onwards) removes V1.1 routes from the mounted app when running in production. Every route not on the `STRICT_PROFILE_ALLOWED_*` sets returns `endpoint_disabled_in_ap_v1_profile`.

---

## Process topology (Railway deploy)

```
  ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
  │  api (web)       │     │  worker          │     │  beat            │
  │  gunicorn +      │     │  celery worker   │     │  celery beat     │
  │  uvicorn         │     │  consumes Redis  │     │  fires periodic  │
  │  serves HTTP     │     │  Streams +       │     │  timers (chases, │
  │  (main.py)       │     │  processes       │     │  reaper sweeps)  │
  │  CLEARLEDGR_     │     │  agent events    │     │                  │
  │  PROCESS_ROLE=   │     │  CLEARLEDGR_     │     │  EXACTLY ONE     │
  │  web             │     │  PROCESS_ROLE=   │     │  INSTANCE        │
  │                  │     │  worker          │     │                  │
  │  scales 1-N      │     │  scales 2-50     │     │  scales 1-1      │
  └─────────┬────────┘     └─────────┬────────┘     └─────────┬────────┘
            │                        │                        │
            └────────────────────────┴────────────────────────┘
                                     │
                      ┌──────────────┴──────────────┐
                      ▼                             ▼
                ┌────────────┐              ┌──────────────┐
                │ Postgres   │              │  Redis       │
                │ (plugin)   │              │  (plugin)    │
                │            │              │              │
                │ all state  │              │  event queue │
                │ lives here │              │  rate limits │
                │            │              │  locks       │
                └────────────┘              └──────────────┘
```

Three services on Railway + two plugins. See `railway.toml` for the current config, `.github/workflows/railway-deploy-workers.yml` for the worker/beat deploy pipeline.

---

## Where to dive deeper

- **Agent internals:** [`agent-runtime.md`](./agent-runtime.md)
- **How to add a Box type / ERP / action:** [`contributing.md`](./contributing.md)
- **Per-surface wiring:** [`integrations.md`](./integrations.md)
- **Why the architecture looks this way:** [`adrs/`](./adrs/)

---

*Last verified: 2026-04-21 against the current main branch (commit `94c98eb` — post-drift-cleanup).*
