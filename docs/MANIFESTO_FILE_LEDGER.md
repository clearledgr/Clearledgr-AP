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

**REVIEWED: 167 / 459** (see wave sections below)

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
