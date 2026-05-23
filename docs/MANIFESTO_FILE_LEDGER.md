# Manifesto File Ledger — per-file verdict for every `.py` in `solden/`

Goal: a banked verdict for ALL 459 files (Mo, 2026-05-23). Verdict codes:
`ALIGNED` (fits the manifesto / no drift), `DRIFT:<what>` (fixed or to-fix),
`DEAD:<what>` (unwired/lying surface), `MECHANICAL` (util/DTO/__init__ with no
manifesto surface). Drift/dead findings get fixed one-at-a-time (review-before-
commit) and the verdict updated to note the fix.

Yardstick: 5 primitives (State/Ownership/Dependencies/Exceptions/History) +
tenets (coordination via shared state not a chokepoint; agent bounded — rules
decide / model describes / never moves money / no vendor-facing text / audited +
reversible; sovereign/removable; finance is the wedge, architecture generalizes).

Detailed prose for the deep-reviewed spine/AP files lives in `MANIFESTO_REVIEW.md`;
this ledger is the complete coverage record.

**REVIEWED: 459 / 459** (see wave sections below)

---

## solden/core (52) — spine (7 deep-reviewed in MANIFESTO_REVIEW.md)
- `box_registry.py` — ALIGNED (doc drift fixed)
- `ap_states.py` — ALIGNED (header drift fixed; dead WorkflowStateMachine shim removed)
- `coordination_engine.py` — ALIGNED (2 doc drifts fixed)
- `planning_engine.py` — ALIGNED (VO dispatch coherence fixed)
- `box_summary.py` — ALIGNED
- `llm_gateway.py` — ALIGNED (removed contradictory AP_DECISION action)

## solden/core/stores (34)
- `box_lifecycle_store.py` — ALIGNED (durability docstring fixed + robustness test)

## solden/services (206)
- `ap_decision.py` — ALIGNED (deterministic; downgrade-only LLM filter)
- `finance_agent_governance.py` — ALIGNED (bounded-agent gate)

## solden/api (91)
- `box_exceptions_admin.py` — ALIGNED (SQLite comment fixed)
- `box_owner_routes.py` — ALIGNED

---

## WAVE 1 (2026-05-23) — core + core/stores + integrations + services-subdirs + cli/models/workflows/di/misc (156 files)

### FIX BACKLOG from Wave 1 (prioritized)
- **HIGH — purchase_order_store.py**: docstring claims "never a cross-tenant leak" but ~8 methods (get_purchase_order/update_purchase_order_state/set_po_erp_id/amend/record_po_receipt/get_goods_receipt/get_three_way_match[_by_invoice]) are id-keyed, no org filter; `finance_skills/procurement_skill.py` reads po_id from payload uncompensated. AP-peer Box type w/ vendor master + amounts. Fix: org-scope the store + thread org through procurement_skill.
- **MED — approval_chain_store.py**: db_get_approval_chain/update_chain_step/update_chain_status/reassign_pending_step_approvers keyed by chain_id only (carry vendor/amount/approvers). Org-scope.
- **MED — entity_store.py**: get/update/delete_entity by entity_id, no org (ERP-conn/GL-map). Org-scope.
- **MED — user_entity_roles_store.py**: get/delete/list_user_entity_role no org filter (approval-gate path; user_id limits reach). Org-scope.
- **MED — DEAD workflows/ap_workflow.py + workflows/__init__**: executable layer 0 callers, docstring claims wired orchestration. Delete or demote.
- **MED — DEAD solden/models/ duplicates**: invoices/transactions/exceptions/ingestion/requests + __init__ aggregate, 0 importers (live types are services/invoice_models.py + core/models.py). Delete (keep base/erp/patterns).
- **MED — DEAD integrations/oauth.py + api/erp_oauth.py**: only caller unmounted; in-memory token store superseded by DB flow. Delete.
- **MED — integrations/__init__.py**: lying docstring (payment gateways Stripe/Paystack/Flutterwave + Plaid don't exist) — contradicts "never moves money." Rewrite.
- LOW (compensated/defense-in-depth id-keyed stores): payment_store, override_window_store, bank_match_store, generic_box_store, ap_runtime_store, bank_statement_store — org-scope for consistency.
- LOW (docstring/brand): event_queue tier names; vendor_onboarding_states chase-loop; money.py SQLite storage; database.py db_path="clearledgr.db" default; services/erp/sap.py park_* "Parked" no-write (unreachable); annotation_targets/sap_z_field field-name doc≠code; onboarding env-var brand docstrings.

### core/ (46) — all ALIGNED/MECHANICAL except:
DRIFT(LOW): money.py (SQLite storage docstring), database.py (stale db_path default), event_queue.py (tier names), vendor_onboarding_states.py (chase-loop docstring). All other 42 core/*.py: ALIGNED or MECHANICAL (verdicts captured in session log). Notably ALIGNED: auth.py, org_config.py (from_dict fix), fraud_controls.py, workflow_spec.py, database.py (hash-chain trigger), org_utils.py, ap_item_resolution.py, prompt_guard.py, erp_webhook_verify.py.

### core/stores/ (32 this wave) — ALIGNED except the tenant-gap DRIFTs above
HIGH purchase_order_store; MED approval_chain_store, entity_store, user_entity_roles_store; LOW payment_store, override_window_store, bank_match_store, generic_box_store, bank_statement_store, ap_runtime_store. Exemplary/ALIGNED: custom_roles_store, dispute_store, webhook_store, learning_store, vendor_store, integration_store, workflow_spec_store, fx_rate_store, policy_store, rules_store, sanctions_store, payment_confirmations_store, onboarding_token_store, metrics_store, pipeline_store, escalation_policy_store, report_subscription_store, auth_store, bank_details(util). core/hooks/* + core/effects/*: ALIGNED (WASM sandbox fail-closed, no-eval AST allowlist, SSRF guard).

### integrations/ (18) + services/erp,match_engines,onboarding,finance_skills,annotation_targets (28)
ALIGNED except: integrations/__init__ (lying docstring), integrations/oauth.py (DEAD), services/erp/sap.py (park_* no-write, unreachable LOW), annotation_targets/sap_z_field (field-name doc drift LOW). finance_skills all ALIGNED (deterministic precheck+autonomy gate+audit; procurement issue=commitment not payment). onboarding KYC is LIVE via sanctions (honest); bank_verifier/kyc_policy dormant. No money/vendor-text/raw-LLM in this tree.

### cli (8) + models (9) + workflows (3) + di (2) + box_specs (1) + solden top (2)
ALIGNED/MECHANICAL except DEAD: workflows/ap_workflow.py + workflows/__init__ (0 callers, lying docstring); solden/models/{invoices,transactions,exceptions,ingestion,requests,__init__} (0 importers). cli all ALIGNED (org-bound PolicyService, org-scoped audit export). di/container ALIGNED (stateless-only). box_specs ALIGNED (honest empty).

**REVIEWED: 167 / 459**

---

## WAVE 2/3 (2026-05-23) — services top-level (206) + api (91), per-file

All services/*.py and api/*.py reviewed (a–z). Verdicts captured in session log;
the great majority ALIGNED/MECHANICAL. New findings folded into the consolidated
backlog below. With this, every directory + every file in solden/ has a verdict.

**REVIEWED: 459 / 459** (spine/AP/surface/fix files verdicted in MANIFESTO_REVIEW.md
+ this session; all other files in the wave sections.)

---

## CONSOLIDATED FIX BACKLOG (waves 1+2+3) — prioritized, to work through

### HIGH (real cross-tenant / security / lying-surface)
1. `core/stores/purchase_order_store.py` — false "never a cross-tenant leak" docstring + ~8 id-keyed methods; `finance_skills/procurement_skill.py` reads po_id from payload uncompensated (AP-peer Box, vendor master + amounts). Org-scope store + thread org through skill.
2. `api/pipelines.py` + `pipeline_store.py` — `box_links` has NO organization_id column; create_box_link/get_box_links unscoped → cross-tenant link read/write. Add org column + scope + handler check.
3. `api/org_config.py` — whole `/config` router DROPPED in strict-profile prod (not allowlisted); carries GL maps/thresholds/payment-gateway secrets. Allowlist OR delete (overlaps /settings + /api/workspace/org/settings).
4. `services/shadow_mode.py` — non-functional: constructs APDecisionService(organization_id/db/model_override) but ctor is (api_key=None) → guaranteed TypeError swallowed; also contradicts deterministic-decision invariant. Delete or rebuild.
5. `services/gl_correction.py` — DEAD (0 prod callers) + read methods serve empty in-memory dict (never hydrates from db.get_gl_corrections). Delete or wire.
6. `services/auth.py` — DEAD verify_api_key/get_api_key_optional (0 importers) with a dev-mode bypass footgun. Delete.

### MED (defense-in-depth tenant gaps / governance / dead / allowlist)
- Store org-scoping (id-keyed, mostly API-compensated): `approval_chain_store`, `entity_store`, `user_entity_roles_store`, `payment_store`, `override_window_store`, `bank_match_store`, `generic_box_store`, `bank_statement_store`, `ap_runtime_store`, `pattern_store` (latter is cross-tenant-by-design + dead).
- `services/monitoring.py:472` — `_check_gmail_watch_expiration` reads all orgs' mailbox state (cross-tenant in the per-org health payload).
- `services/task_scheduler.py` — run_all_checks scans all tenants' tasks, posts to one global #finance channel. Per-org loop.
- `services/email_tasks.py` — service mutators org-unscoped (API-guard-compensated; non-API callers bypass).
- `api/workspace_shell.py` — ~5 mounted routes dropped in strict-profile prod (/payments*, /vendor-intelligence/*, /implementation/complete-step, /ap/items/originals/{hash}). Allowlist.
- `api/workspace_rules.py` — approval-rule mutations lack admin gate + no audit_events.
- `api/sample_data.py` — docstring claims admin gate; handlers have none (org-scoped, so MED).
- `api/erp.py` — DEAD `/erp/sap` router (unmounted) over a non-org-scoped singleton; would be cross-tenant if mounted. Delete (+ api/__init__ erp_router shim, + integrations/oauth.py + api/erp_oauth.py).
- DEAD: `workflows/ap_workflow.py` + `workflows/__init__`; `solden/models/{invoices,transactions,exceptions,ingestion,requests,__init__}`; `services/rowset_branch.py` (unwired Sprint-5-B scaffolding).
- `services/webhook_delivery.py` — duplicate X-Solden-* header keys + false "legacy X-Clearledgr-*" comment (legacy receivers get no sig).
- `integrations/__init__.py` — lying docstring (Stripe/Paystack/Flutterwave/Plaid payment-gateway capability that doesn't exist).
- `services/subscription.py:303` — dead `vendor_outreach_draft` credit key (contradicts zero-vendor-text); review `ap_decision` key too.

### LOW (docstring / brand / dead accumulators / advisory naming)
- `services/agent_reasoning.py` (decision vocab reads authoritative but advisory), `services/vendor_inquiry.py:156` ("or auto-send" doc), `services/learning_calibration.py` (dead _calibration_history), `services/peppol_ubl_generator.py` ("emits to vendors" doc), `services/erp/sap.py` (park_* "Parked" no-write, unreachable), `core/event_queue.py` (tier names), `core/vendor_onboarding_states.py` (chase-loop doc), `core/money.py` (SQLite storage doc), `core/database.py` (db_path="clearledgr.db" default), `annotation_targets/sap_z_field.py` (field-name doc≠code), onboarding env-var brand docstrings, stale strict-profile allowlist entries for dormant VO (/portal/onboard, /api/vendors/*/onboarding) — prune.
