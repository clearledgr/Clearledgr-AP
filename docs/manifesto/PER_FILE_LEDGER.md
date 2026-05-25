# Manifesto Per-File Ledger — GENUINE coverage (every in-scope file)

Scope (Mo, 2026-05-25): EVERY file — solden/ (454) + tests/ (304) + ui/ (155) + main.py = 914.

Each file gets ONE banked verdict line. No directory summaries. The honest
remaining count is `grep -c 'PENDING' this file`.

Verdict codes:
- `ALIGNED` — fits its yardstick, no drift
- `DRIFT:<what>` — real drift (→ fix one-at-a-time, then note the fix)
- `DEAD:<what>` — unwired / lying / orphaned surface
- `MECHANICAL` — util/DTO/__init__/fixture with no manifesto surface
- `PENDING` — not yet genuinely reviewed

Yardsticks by area: solden/ = 5 primitives (State/Ownership/Dependencies/
Exceptions/History) + agent-bounded tenets (rules decide / model describes /
never moves money / no vendor-facing text / audited+reversible / sovereign).
tests/ = does it actually assert the invariant; dead/flaky/lying tests.
ui/ = matches the API contract + DESIGN.md; dead components; brand drift.

---

## (root)  (1)
- `main.py` — PENDING

## solden  (2)
- `solden/__init__.py` — MECHANICAL: package docstring
- `solden/_envboot.py` — ALIGNED: side-effect dotenv loader; E402 rationale accurate

## solden/api  (90)
- `solden/api/__init__.py` — MECHANICAL: lazy router-shim via __getattr__
- `solden/api/accrual_journal_entry.py` — ALIGNED: authed, org from session, by-id verifies org; allowlisted; JE gated upstream
- `solden/api/africa_einvoice.py` — ALIGNED: authed, org from session, by-id caller-org checks; allowlisted
- `solden/api/agent_intents.py` — ALIGNED: preview get_current_user, execute require_ops_user; org via runtime; allowlisted
- `solden/api/ap_audit.py` — ALIGNED: read-only, require_org rejects body-org; allowlisted
- `solden/api/ap_item_contracts.py` — MECHANICAL: pure pydantic request models
- `solden/api/ap_item_detail.py` — DRIFT:stale 'mint-green' brand + 'Sonnet path' LLM-vendor tell in docstrings
- `solden/api/ap_items.py` — MECHANICAL: router composition (read + action sub-routers)
- `solden/api/ap_items_action_routes.py` — ALIGNED: mutators require_ops_user, _session_org, _require_item enforce caller-org
- `solden/api/ap_items_read_routes.py` — DRIFT:/consolidated calls undefined verify_org_access (NameError 500); /audit/export not allowlisted (prod 404)
- `solden/api/ap_policies.py` — DRIFT:PUT policy mutates AP business policy but guards only get_current_user (member-writable)
- `solden/api/api_keys.py` — ALIGNED: ops scoped to user.organization_id, show-once secret; allowlisted
- `solden/api/audit_chain.py` — ALIGNED: read-only, org from session no-default 403; allowlisted
- `solden/api/auth.py` — ALIGNED: OAuth/login/invite; org from invite/domain, state HMAC+TTL, mutations admin-gated
- `solden/api/bank_match_routes.py` — ALIGNED: authed, _session_org, _require_bank_match + parent-AP gate verify org
- `solden/api/bank_statements.py` — ALIGNED: authed, org from session, by-id reads verify caller org
- `solden/api/box_exceptions_admin.py` — ALIGNED: get_current_user + _require_admin + _assert_org_match
- `solden/api/box_export.py` — DRIFT:docstring frames bank-match/generic export as 'future' but already implemented in-file
- `solden/api/box_owner_routes.py` — ALIGNED: reassign authed, org from session, AP-item cross-tenant 404
- `solden/api/box_revert_routes.py` — ALIGNED: bounded approval-revert authed, org from session, tenant 404
- `solden/api/cycle_time_metrics.py` — ALIGNED: read-only report, org from user.organization_id; allowlisted
- `solden/api/dashboard.py` — ALIGNED: read/SSE authed, org strictly from session; allowlisted
- `solden/api/deps.py` — ALIGNED: org-isolation helpers; soft_org_guard/verify_org_access enforce token-vs-claimed
- `solden/api/dispute_reopen.py` — ALIGNED: reopen/read authed, org from session, by-id verifies org; correction bill within boundary
- `solden/api/dual_approval.py` — DRIFT:PUT /policy/dual-approval sets second-signature threshold but member-writable (no admin guard)
- `solden/api/erp_connection_ops.py` — DRIFT:/rotate-credentials + /test are credential governance but member-writable (no admin guard)
- `solden/api/erp_connections.py` — DRIFT:connect/disconnect/gl-map mutations member-writable; defines unused _ADMIN_ROLES
- `solden/api/erp_oauth.py` — ALIGNED: org from session not URL/body, OAuth state bound to (org,user); allowlisted
- `solden/api/erp_webhooks.py` — ALIGNED: HMAC-as-auth, per-tenant secret constant-time, cross-tenant guards, fail-closed
- `solden/api/escalation_policies.py` — DRIFT:full CRUD over governance escalation policies member-writable (no admin guard)
- `solden/api/fraud_controls.py` — ALIGNED: mutating PUT requires CFO + cross-tenant guard; allowlisted
- `solden/api/fx_rates.py` — ALIGNED: authed, org from session, delete org-scoped; allowlisted
- `solden/api/gdpr.py` — ALIGNED: allowlisted, authed, by-id verifies org (404 no leak)
- `solden/api/gmail_extension.py` — ALIGNED: fail-closed org resolution, routes allowlisted, no vendor email
- `solden/api/gmail_extension_common.py` — MECHANICAL: helper module; tenant guards fail-closed
- `solden/api/gmail_extension_models.py` — MECHANICAL: pure pydantic request models
- `solden/api/gmail_extension_support_routes.py` — ALIGNED: under /extension, authed + org-scoped, allowlisted
- `solden/api/gmail_webhooks.py` — DRIFT:OAuth success page stale Streak-era cream/green serif, not Solden navy/teal
- `solden/api/iban_verification.py` — ALIGNED: write factors require CFO + cross-tenant guard; allowlisted
- `solden/api/journal_entry_preview.py` — ALIGNED: read-only, authed, org-scoped; allowlisted
- `solden/api/leads.py` — ALIGNED: intentionally public marketing endpoint, allowlisted, parameterized SQL
- `solden/api/match_config.py` — ALIGNED: PUT requires admin, GET member-read, org from session; allowlisted
- `solden/api/multi_invoice_split.py` — ALIGNED: stateless PDF utility, both POSTs authed; allowlisted
- `solden/api/netsuite_panel.py` — DRIFT:POST approve/reject/request-info paths not allowlisted (prod 404)
- `solden/api/notification_preferences.py` — ALIGNED: per-user data keyed by JWT, self-scoped PATCH; allowlisted
- `solden/api/ops.py` — DRIFT:/api/ops/box-health mounted but not allowlisted (prod 404); retry/skip cross-tenant fixed
- `solden/api/org_config.py` — DRIFT:governance PUT/PATCH lack admin gate, member-writable (router disabled in strict prod)
- `solden/api/outbox_ops.py` — ALIGNED: writes require ops/admin, by-id verifies org; allowlisted
- `solden/api/outlook_routes.py` — ALIGNED: flag-gated, allowlisted, fail-closed webhook constant-time, self-scoped OAuth
- `solden/api/paddle_billing.py` — ALIGNED: billing mutations require admin; webhook HMAC-verified fail-closed
- `solden/api/payment_confirmations.py` — DRIFT:remittance-config docstring claims active vendor auto-send (sender deleted; zero vendor email)
- `solden/api/peppol.py` — ALIGNED: authed, import writes AP item from session org, credit-note verifies org, no vendor email
- `solden/api/pipelines.py` — DRIFT:delete_saved_view keyed by id only, NO org filter — cross-tenant deletion
- `solden/api/policies.py` — DRIFT:POST create-version/rollback of governance policies member-writable (no admin gate)
- `solden/api/projections_ops.py` — ALIGNED: reads require_org, rebuild require ops/admin + verify_org_access; allowlisted
- `solden/api/purchase_order_routes.py` — ALIGNED: 8 actions allowlisted via pattern, org from session, by-id 404
- `solden/api/reclassification_je.py` — ALIGNED: pattern-allowlisted, authed, org-scoped, ERP does the post
- `solden/api/report_subscriptions.py` — ALIGNED: CRUD authed, by-id verifies org, update/delete pass org; allowlisted
- `solden/api/saml.py` — ALIGNED: admin config requires admin+org; public IdP flows signature-authed, ACS redirect-guarded
- `solden/api/sample_data.py` — ALIGNED: load/clear require workspace admin, reads member; org from session; allowlisted
- `solden/api/sanctions.py` — ALIGNED: authed, org from session, by-id org match; sanctions is money boundary
- `solden/api/sap_extension.py` — ALIGNED: per-tenant XSUAA RS256, org from issuer not body, cross-tenant guards, audited
- `solden/api/settings.py` — ALIGNED: router-level _require_org_match 403; migration cutover financial_controller-gated
- `solden/api/slack_invoices.py` — ALIGNED: signature-verified, team->org fail-closed, tenant-mismatch refused, idempotent
- `solden/api/team_offboarding.py` — ALIGNED: admin-gated, cross-tenant 404, self/last-owner protection, key-cascade + audit
- `solden/api/teams_invoices.py` — ALIGNED: 404 off-flag, tid->install org binding, ap-item org-mismatch refused, audited
- `solden/api/three_way_match.py` — ALIGNED: authed, org from session, runner scoped by org; per-id allowlisted
- `solden/api/threshold_policy.py` — ALIGNED: writes require_admin_user, reads get_current_user, org from session
- `solden/api/ui_perf.py` — ALIGNED: intentionally unauth telemetry beacon, drops unscoped, always-200
- `solden/api/user_preferences.py` — ALIGNED: self-scoped, org_access_denied on mismatch
- `solden/api/v1.py` — ALIGNED: API-key auth, audit pinned to agent.organization_id server-side
- `solden/api/v1_auth.py` — ALIGNED: hash-validated keys, revoked/expired fail-closed, scope+org enforced
- `solden/api/v1_idempotency.py` — ALIGNED: org+key scoped cache, payload-hash 409, fail-open lookup
- `solden/api/v1_intents.py` — ALIGNED: scope-gated execute/preview, org from key, idempotent, typed errors
- `solden/api/v1_rate_limit.py` — ALIGNED: per-key+per-org windows, Redis/memory, fail-open, audit
- `solden/api/v1_records.py` — ALIGNED: deny-by-default field allowlist, org-pinned SQL, cross-tenant id 404
- `solden/api/v1_webhooks.py` — ALIGNED: scope-gated CRUD, org-pinned, secret-once+redaction, HTTPS-only
- `solden/api/vat.py` — ALIGNED: authed, org from session, by-id verifies org; VAT is money boundary
- `solden/api/vendor_domains.py` — ALIGNED: read members, add/remove require_cfo, _assert_same_org, audited
- `solden/api/vendor_inquiry.py` — ALIGNED: read-only sanitized lookup, org from session, Solden sends nothing
- `solden/api/vendor_kyc.py` — DEAD:dormant-VO (deliberate): FC-gated + org-guarded but feeds parked VO surface
- `solden/api/vendor_match.py` — ALIGNED: authed, org from session, matcher scoped by org; per-id allowlisted
- `solden/api/vendor_onboarding.py` — DEAD:dormant-VO (deliberate): router not mounted per 2026-04-30 deferral
- `solden/api/vendor_portal.py` — DEAD:dormant-VO (deliberate): public magic-link portal not mounted; bank Fernet-encrypted
- `solden/api/vendor_status.py` — DRIFT:verify-registration POST not admin-gated + auto-creates vendor stub (siblings are gated)
- `solden/api/workflow_routes.py` — ALIGNED: org from session 403-if-missing, by-id verifies org+box_type, pinned spec
- `solden/api/workflow_spec_routes.py` — ALIGNED: writes require_workspace_admin, reads member, validation before persist
- `solden/api/workspace_reports.py` — ALIGNED: every report org-scoped, read-only, fixed five-report set
- `solden/api/workspace_rules.py` — ALIGNED: writes require_workspace_admin, by-id verifies org, conflict-gated
- `solden/api/workspace_shell.py` — ALIGNED: governance mutations _require_admin, _resolve_org_id clamps to session, by-id org-checked

## solden/box_specs  (1)
- `solden/box_specs/__init__.py` — ALIGNED: eagerly imported by main.py; empty-but-registered declarative package

## solden/cli  (8)
- `solden/cli/__init__.py` — MECHANICAL: package docstring matching subcommand modules
- `solden/cli/__main__.py` — ALIGNED: argparse entry wiring all five subcommand groups
- `solden/cli/_common.py` — ALIGNED: stdlib-only DB/table/JSON helpers; lazy get_db
- `solden/cli/audit.py` — ALIGNED: exports append-only chain ordered by chain_seq (History)
- `solden/cli/health.py` — DRIFT:stale docstring promises an M19 source-scan check the code never performs
- `solden/cli/migrations_cmd.py` — ALIGNED: reads _MIGRATIONS + get_schema_version; output matches
- `solden/cli/policy.py` — ALIGNED: subcommands map to real PolicyService methods; append-only rollback
- `solden/cli/tenants.py` — ALIGNED: list/info over SoldenDB; dotted settings_json lookup correct

## solden/core  (52)
- `solden/core/__init__.py` — MECHANICAL: package marker
- `solden/core/ap_confidence.py` — ALIGNED: deterministic per-field confidence gate, severity tiers
- `solden/core/ap_entity_routing.py` — ALIGNED: deterministic entity routing; DB source-of-truth, no LLM
- `solden/core/ap_item_resolution.py` — ALIGNED: org-scoped lookups, fails closed on foreign-org rows
- `solden/core/ap_states.py` — ALIGNED: State primitive; transitions/override/dual-approval coherent
- `solden/core/approval_action_contract.py` — ALIGNED: action normalize + SoD + approver-auth + state preflight
- `solden/core/auth.py` — ALIGNED: two-axis v89 auth (workspace_role + user_box_roles), JWT
- `solden/core/authorization.py` — ALIGNED: typed denials into org-scoped audit; never raises on audit fail
- `solden/core/bank_match_states.py` — ALIGNED: AP-subordinate state machine, terminal states, policy version
- `solden/core/box_lock.py` — ALIGNED: per-(org,box) advisory locks; no cross-org collision
- `solden/core/box_registry.py` — ALIGNED: flat BoxType registry, three types + dynamic resolver
- `solden/core/box_summary.py` — ALIGNED: context-efficient Box summary from timeline
- `solden/core/business_days.py` — MECHANICAL: Mon-Fri elapsed-day arithmetic
- `solden/core/clock.py` — MECHANICAL: tz-aware UTC datetime helpers
- `solden/core/coordination_engine.py` — ALIGNED: executes plans, DET/LLM boundary, never-assume-success, audited
- `solden/core/database.py` — ALIGNED: Postgres-only, org-scoped sha256 audit hash-chain, org-keyed
- `solden/core/deployment_window.py` — ALIGNED: Tue-Thu deploy gate; wired in settings.py
- `solden/core/erp_webhook_verify.py` — ALIGNED: per-ERP HMAC, fail-closed, constant-time compare
- `solden/core/error_codes.py` — DEAD:zero importers; live ErrorCode enum is services/errors.py (never migrated)
- `solden/core/errors.py` — ALIGNED: safe_error logs trace, returns only ref ID
- `solden/core/event_queue.py` — ALIGNED: durable Redis-streams + in-memory fallback; dedup/reclaim/priority
- `solden/core/events.py` — ALIGNED: AgentEvent DTO; from_dict asserts org_id
- `solden/core/feature_flags.py` — ALIGNED: V1 + money-write gates default-false, single source
- `solden/core/finance_contracts.py` — ALIGNED: skill request/response/audit DTOs; from_intent asserts org_id
- `solden/core/fraud_controls.py` — ALIGNED: CFO-only, audit-fails-the-save, fail-closed FX ceiling
- `solden/core/http_client.py` — MECHANICAL: shared httpx AsyncClient lifecycle
- `solden/core/idempotency.py` — ALIGNED: replay protection on unique audit_events key
- `solden/core/launch_controls.py` — ALIGNED: GA-readiness + rollback kill switches in org settings
- `solden/core/llm_gateway.py` — ALIGNED: ACTION_REGISTRY has no ap_decision; model describes, never decides
- `solden/core/migrations.py` — ALIGNED: additive idempotent advisory-locked; clearledgr_* DB names intentional
- `solden/core/models.py` — MECHANICAL: convenience DTOs + id aliases; authoritative store is ap_items
- `solden/core/money.py` — ALIGNED: penny-exact Decimal; JSON-number boundary documented
- `solden/core/observability.py` — MECHANICAL: background-loop exception capture; never raises
- `solden/core/org_config.py` — ALIGNED: per-org config with restored from_dict + CAS save
- `solden/core/org_utils.py` — ALIGNED: org-id resolver rejects default/_unprovisioned, fails closed
- `solden/core/permissions.py` — ALIGNED: bounded permission catalog, no open-ended grants
- `solden/core/plan.py` — ALIGNED: Plan/Action DTOs box_type-agnostic; from_json asserts org_id
- `solden/core/planning_engine.py` — ALIGNED: pure deterministic planner; rules decide
- `solden/core/portal_auth.py` — ALIGNED: magic-link portal auth (dormant VO, still imported by router)
- `solden/core/portal_input.py` — ALIGNED: vendor-portal input validation; imported by router
- `solden/core/procurement_thresholds.py` — ALIGNED: tiered PO routing, audited save, PO-currency comparison
- `solden/core/prompt_guard.py` — ALIGNED: fail-closed injection detector; gate rejects, no sanitize-continue
- `solden/core/purchase_order_states.py` — ALIGNED: AP-peer PO state machine, terminal states, policy version
- `solden/core/secrets.py` — ALIGNED: prod crashes on missing secret, dev fallback; dual-read window
- `solden/core/sentry_config.py` — ALIGNED: before_send scrubs PII/secrets; never drops event
- `solden/core/sla_tracker.py` — ALIGNED: per-step SLA metrics, per-tenant (asserts org_id)
- `solden/core/slack_verify.py` — ALIGNED: HMAC v0 verify + 5-min replay window, constant-time
- `solden/core/teams_verify.py` — ALIGNED: Bot Framework JWT verify against JWKS, fail-closed
- `solden/core/typed_dicts.py` — MECHANICAL: documentation-only TypedDict shapes
- `solden/core/utils.py` — MECHANICAL: safe_int/safe_float coercion
- `solden/core/vendor_onboarding_states.py` — ALIGNED: VO state machine (service-level, dormant Box); chase loop operator-facing, no vendor email
- `solden/core/workflow_spec.py` — ALIGNED: declarative Box-type spec + validator; hooks inert until sandbox, honestly flagged

## solden/core/effects  (2)
- `solden/core/effects/__init__.py` — MECHANICAL: package docstring for effect boundary
- `solden/core/effects/catalog.py` — ALIGNED: tenant-scoped effect catalog; DNS-rebind/SSRF guard; best-effort

## solden/core/hooks  (4)
- `solden/core/hooks/__init__.py` — MECHANICAL: package docstring (two hook tiers)
- `solden/core/hooks/dispatcher.py` — ALIGNED: flag-gated; conditions always enforced; records hook runs to History
- `solden/core/hooks/expressions.py` — ALIGNED: AST-allowlist evaluator, no eval/exec; size caps
- `solden/core/hooks/sandbox.py` — ALIGNED: no-imports capability gate + fuel/epoch/memory limits, fail-closed

## solden/core/stores  (34)
- `solden/core/stores/__init__.py` — MECHANICAL: pure re-export of store mixins
- `solden/core/stores/ap_runtime_store.py` — ALIGNED: legacy AP-runtime helpers, reads org-scoped
- `solden/core/stores/ap_store.py` — ALIGNED: state machine + atomic transition audit; bank details encrypted
- `solden/core/stores/approval_chain_store.py` — ALIGNED: chain/step CRUD org-scoped
- `solden/core/stores/auth_store.py` — ALIGNED: secrets encrypted; GDPR purge excludes audit; fails closed
- `solden/core/stores/bank_details.py` — ALIGNED: encryption/masking/IBAN mod97; no plaintext leak
- `solden/core/stores/bank_match_store.py` — ALIGNED: AP-subordinate BoxType, transition validation + audit
- `solden/core/stores/bank_statement_store.py` — ALIGNED: org-scoped CRUD, match-status whitelist, idempotent dedup
- `solden/core/stores/box_lifecycle_store.py` — ALIGNED: Exceptions+Outcomes, idempotent, audit-mirrored
- `solden/core/stores/custom_roles_store.py` — ALIGNED: org-scoped, fails closed cross-tenant, role quota
- `solden/core/stores/dispute_store.py` — ALIGNED: org-scoped; vendor_email is tracking, not outbound
- `solden/core/stores/entity_store.py` — ALIGNED: entity CRUD org-scoped
- `solden/core/stores/escalation_policy_store.py` — ALIGNED: org-scoped CRUD, idempotent worker query
- `solden/core/stores/fx_rate_store.py` — ALIGNED: org-scoped rate CRUD+lookup, ISO validation
- `solden/core/stores/generic_box_store.py` — ALIGNED: declarative-Box CRUD, transition validation, audit+exception mirror
- `solden/core/stores/integration_store.py` — ALIGNED: ERP/Slack/Teams CRUD org-scoped, tokens encrypted
- `solden/core/stores/learning_store.py` — ALIGNED: every method org-scoped (org in PK)
- `solden/core/stores/metrics_store.py` — ALIGNED: read-only aggregation, org-scoped
- `solden/core/stores/onboarding_token_store.py` — ALIGNED: hashed magic-link tokens, constant-time compare (dormant-VO)
- `solden/core/stores/override_window_store.py` — ALIGNED: human-reversal escape hatch, state guards
- `solden/core/stores/payment_confirmations_store.py` — ALIGNED: org-scoped ledger, idempotent compound key
- `solden/core/stores/payment_request_store.py` — ALIGNED: org-scoped (fail-closed), update whitelist
- `solden/core/stores/payment_store.py` — ALIGNED: tracking-only, org-scoped, append-only events
- `solden/core/stores/pipeline_store.py` — ALIGNED: org-scoped CRUD, source_table+filter whitelists
- `solden/core/stores/policy_store.py` — ALIGNED: AP-policy versions org-scoped, audit per upsert
- `solden/core/stores/purchase_order_store.py` — ALIGNED: PO/GR/match CRUD; caller-enforced org documented
- `solden/core/stores/recon_store.py` — ALIGNED: reads/writes org-scoped (fail-closed)
- `solden/core/stores/report_subscription_store.py` — ALIGNED: writes org-scoped, cadence whitelist, auto-pause
- `solden/core/stores/rules_store.py` — ALIGNED: rule CRUD org-scoped, immutable version ledger + revert
- `solden/core/stores/sanctions_store.py` — ALIGNED: org-scoped screen ledger; review-update caller-enforced
- `solden/core/stores/user_entity_roles_store.py` — ALIGNED: per-(user,entity) CRUD, writes carry org, transactional replace
- `solden/core/stores/vendor_store.py` — DRIFT:stale remittance auto-send schema comment (L68-73; sender removed 2026-05-02)
- `solden/core/stores/webhook_store.py` — ALIGNED: CRUD org-scoped (fail-closed), protects HMAC secret
- `solden/core/stores/workflow_spec_store.py` — ALIGNED: per-tenant versioned spec CRUD, one-active-per-type, quota

## solden/di  (2)
- `solden/di/__init__.py` — MECHANICAL: re-exports ServiceContainer + singleton
- `solden/di/container.py` — ALIGNED: stateless-only singleton container; warns against org-scoped state

## solden/integrations  (18)
- `solden/integrations/__init__.py` — MECHANICAL: re-exports routers; docstring correct (no payment gateways)
- `solden/integrations/erp_netsuite.py` — ALIGNED: real TBA REST; fail-closed; settlement gated upstream
- `solden/integrations/erp_netsuite_intake.py` — ALIGNED: best-effort read enrichment, no fake success
- `solden/integrations/erp_netsuite_intake_adapter.py` — ALIGNED: registered adapter, honest thin fallback
- `solden/integrations/erp_po_write.py` — ALIGNED: PO-create flag-gated; honest errors; unimplemented returns not_implemented
- `solden/integrations/erp_quickbooks.py` — ALIGNED: real QBO calls; settlement gated; reads fail-closed
- `solden/integrations/erp_quickbooks_intake_adapter.py` — ALIGNED: registered Bill adapter, honest thin fallback
- `solden/integrations/erp_rate_limiter.py` — ALIGNED: token-bucket, Redis+in-memory fallback
- `solden/integrations/erp_router.py` — ALIGNED: dispatch with idempotency, pre-post validation, settlement flag-gated
- `solden/integrations/erp_sanitization.py` — MECHANICAL: injection-prevention helpers/query builders
- `solden/integrations/erp_sap.py` — ALIGNED: real B1/S4HANA OData; fail-closed; settlement gated upstream
- `solden/integrations/erp_sap_s4hana.py` — ALIGNED: real OData read/PATCH/action; honest errors; wired
- `solden/integrations/erp_sap_s4hana_intake.py` — ALIGNED: best-effort read enrichment, no fake success
- `solden/integrations/erp_sap_s4hana_intake_adapter.py` — ALIGNED: registered adapter, defers 'paid' to dispatcher
- `solden/integrations/erp_xero.py` — ALIGNED: real httpx Xero connector, fail-closed, quantized money, idempotency-key
- `solden/integrations/erp_xero_intake_adapter.py` — ALIGNED: registered adapter, filters ACCREC vs ACCPAY, honest skip
- `solden/integrations/field_mapping_catalog.py` — MECHANICAL: static catalog + validation/diff helpers
- `solden/integrations/oauth.py` — DRIFT:in-memory _erp_connections; get_erp_connection_record/ensure_valid_token read only the dict (lost on restart/multi-worker)

## solden/models  (9)
- `solden/models/__init__.py` — MECHANICAL: live model cluster re-exports; __all__ matches
- `solden/models/base.py` — MECHANICAL: pydantic base (extra=forbid)
- `solden/models/erp.py` — MECHANICAL: SAP/ERP request-response DTOs; dry_run default True
- `solden/models/exceptions.py` — MECHANICAL: ExceptionItem/ApprovalDecision DTOs
- `solden/models/ingestion.py` — MECHANICAL: ingestion-event DTOs; tz-aware defaults
- `solden/models/invoices.py` — MECHANICAL: invoice extraction/categorization DTOs; confidence bounded
- `solden/models/patterns.py` — DEAD:orphaned MatchPattern; sole consumer pattern_store.py has no real callers
- `solden/models/requests.py` — MECHANICAL: API request DTOs; in live __init__ cluster
- `solden/models/transactions.py` — MECHANICAL: Money/transaction DTOs; amount ge=0

## solden/services  (203)
- `solden/services/__init__.py` — MECHANICAL: lazy __getattr__ re-export shim
- `solden/services/accrual_journal_entry.py` — ALIGNED: deterministic accrual JE proposals, org-scoped, operator-reviewed
- `solden/services/accrual_journal_entry_post.py` — ALIGNED: posts JE to ERP (ERP writes), idempotent, audited, org-scoped
- `solden/services/adaptive_thresholds.py` — ALIGNED: deterministic threshold learning from overrides, DB-backed, org-scoped
- `solden/services/africa_einvoice.py` — ALIGNED: deterministic FIRS/eTIMS/SARS payload generators, no LLM
- `solden/services/africa_einvoice_submission.py` — ALIGNED: submit-to-ASP orchestrator, DB-ledgered, audited, org-scoped, idempotent
- `solden/services/agent_anomaly_detection.py` — ALIGNED: z-score rules decide; LLM only refines operator explanation
- `solden/services/agent_background.py` — ALIGNED: per-org background tick (confirmed)
- `solden/services/agent_command_dispatch.py` — ALIGNED: runtime construction + org-isolation guard, no silent default
- `solden/services/agent_credit_pool.py` — ALIGNED: DB-ledger credit pool, SQL balance invariant, org-scoped, no money movement
- `solden/services/agent_memory.py` — ALIGNED: Postgres-backed agent memory, org+skill scoped
- `solden/services/agent_monitoring.py` — MECHANICAL: pure date/hash/threshold utilities
- `solden/services/agent_reasoning.py` — DRIFT:LLM-derived confidence drives _make_decision auto_approve label; gmail_extension bumps/clamps the auto-approve gate (LLM influences routing)
- `solden/services/agent_reflection.py` — ALIGNED: deterministic field self-check; LLM refines extraction only, not routing
- `solden/services/agent_retry_jobs.py` — ALIGNED: durable retry drain, org-asserted, dead-letters + audited
- `solden/services/ap_agent_sync.py` — ALIGNED: syncs events to memory+learning, org-scoped, best-effort
- `solden/services/ap_aging_report.py` — ALIGNED: read-only aging, org-filtered, Decimal math, never raises
- `solden/services/ap_classifier.py` — ALIGNED: LLM classifies unstructured email (allowed) with deterministic fallback
- `solden/services/ap_context_connectors.py` — ALIGNED: best-effort context aggregation, org-asserted, fail-open read-only
- `solden/services/ap_decision.py` — ALIGNED: deterministic policy cascade; rules decide, no LLM (confirmed)
- `solden/services/ap_field_review.py` — ALIGNED: deterministic field-review builders + mutable-field whitelist, no LLM
- `solden/services/ap_item_service.py` — ALIGNED: org-scoped AP helpers (verify_org_access, allowed-columns filter)
- `solden/services/ap_operator_audit.py` — MECHANICAL: operator-facing label/copy normalization over audit rows
- `solden/services/ap_projection.py` — MECHANICAL: bulk read-model projection helper
- `solden/services/ap_vendor_analysis.py` — ALIGNED: deterministic vendor summary/risk builders, org-asserted, read-only
- `solden/services/app_startup.py` — ALIGNED: deferred startup launcher, feature-flag gated (confirmed)
- `solden/services/approval_card_builder.py` — ALIGNED: stateless Slack/Teams block presentation, no DB/network/LLM
- `solden/services/approval_delegation.py` — ALIGNED: org-scoped OOO delegation + auto-reassign, SQL org-filtered
- `solden/services/approval_revert.py` — ALIGNED: bounded reversible revert, state-validated, audited, org-checked
- `solden/services/approver_workload.py` — ALIGNED: read-only per-approver workload aggregation, org-filtered
- `solden/services/ask_the_agent.py` — ALIGNED: bounded read-only Q&A; LLM writes operator prose only, org-scoped
- `solden/services/audit.py` — MECHANICAL: thin wrapper to audit_trail.record_audit_event
- `solden/services/audit_chain_verify.py` — ALIGNED: org-scoped SHA-256 hash-chain verification, read-only
- `solden/services/audit_entity_scope.py` — ALIGNED: query-time per-entity audit scoping, fail-closed for anonymous
- `solden/services/audit_trail.py` — ALIGNED: append-only audit/timeline with DID-WHY-NEXT, org-scoped
- `solden/services/bank_reconciliation_matcher.py` — ALIGNED: deterministic amount/date/currency matcher, org-scoped, ambiguous->manual
- `solden/services/bank_statement_parsers.py` — MECHANICAL: pure CAMT.053/OFX parsers, stdlib only
- `solden/services/box_cas.py` — ALIGNED: compare-and-swap with column whitelist + tenancy fail-close + audit
- `solden/services/box_extraction.py` — ALIGNED: LLM reads unstructured text into spec fields only; org-scoped, makes no decision
- `solden/services/box_owner.py` — ALIGNED: deterministic owner/delegation resolver, atomic persist, audited, org-scoped
- `solden/services/box_projection.py` — ALIGNED: durable outbox-driven read projections, org-scoped, idempotent
- `solden/services/box_seed.py` — ALIGNED: box-type-agnostic seed registry; mechanical, no LLM/money
- `solden/services/budget_awareness.py` — DRIFT:in-memory _spending accumulator lost on restart/2nd worker
- `solden/services/calendar_ooo.py` — DRIFT:in-process TTL cache for OOO availability; workers disagree
- `solden/services/celery_app.py` — ALIGNED: task_acks_late + prefetch=1 + visibility timeout give durability; pure config
- `solden/services/celery_tasks.py` — ALIGNED: durable event drain/retry; deterministic plan->coordinate; cross-tenant guard; payments recorded not initiated
- `solden/services/circuit_breaker.py` — ALIGNED: deterministic override-rate safety net, audited, org-scoped; moves no money
- `solden/services/compounding_learning.py` — ALIGNED: Postgres-backed per-org cache; learning hints only, never moves money
- `solden/services/confidence_calibration.py` — ALIGNED: tighten-only calibration persisted to vendor profile; describes, no routing
- `solden/services/connection_health.py` — ALIGNED: thin org-scoped derivation over store aggregates; pure read
- `solden/services/conversational_agent.py` — DRIFT:_conversations in-memory dict holds question state; lost on restart/2nd worker
- `solden/services/correction_learning.py` — DRIFT:get_recent_corrections reads self._rules which doesn't exist (only _learned_rules) -> AttributeError path
- `solden/services/cross_invoice_analysis.py` — ALIGNED: deterministic dup/anomaly; model relaxes only weak matches (downgrade-only floor)
- `solden/services/cycle_time_metrics.py` — ALIGNED: org-scoped audit_events reads for cycle-time/touchless KPIs; no decisions
- `solden/services/data_subject_request.py` — ALIGNED: GDPR access/erasure/portability, org-scoped; anonymizes not deletes SOX records
- `solden/services/discount_optimizer.py` — ALIGNED: deterministic discount-terms math; recommends, decides nothing
- `solden/services/dispute_reopen.py` — ALIGNED: reopen spawns correction AP item, original immutable, idempotent, dual audit
- `solden/services/dispute_service.py` — ALIGNED: org-scoped dispute lifecycle, cross-tenant read guard, no vendor email
- `solden/services/document_routing.py` — ALIGNED: deterministic doc-type->workflow routing table with alias normalize
- `solden/services/dual_approval.py` — ALIGNED: deterministic two-person rule, self/requester blocked + audited, org threshold
- `solden/services/email_parser.py` — ALIGNED: deterministic regex/OCR/fuzzy extraction + provenance + conflict flags; no LLM
- `solden/services/email_sharing.py` — ALIGNED: posts timeline + AP Slack notice for individual-inbox emails; no vendor-facing text
- `solden/services/email_tasks.py` — DRIFT:module-level db=get_db() at import binds one instance (breaks test singleton reset / per-worker rebind)
- `solden/services/erp_api_first.py` — ALIGNED: API-first posting, pre-post validation, audited, no browser fallback; ERP writes
- `solden/services/erp_connector_strategy.py` — MECHANICAL: declarative per-ERP capability table + route planner; fails safe
- `solden/services/erp_follow_on_reconciliation.py` — ALIGNED: org-scoped split-brain status repair, audited; mechanical metadata sync
- `solden/services/erp_follow_on_result.py` — ALIGNED: deterministic finance-effect summary + blockers, column-whitelisted, dual audit
- `solden/services/erp_intake_po_sync.py` — ALIGNED: idempotent PO/GR upsert from structured ERP intake, org-scoped, no LLM
- `solden/services/erp_native_approval.py` — ALIGNED: Slack approve releases ERP-side hold + walks state machine, audited, org-guarded
- `solden/services/erp_payment_dispatcher.py` — ALIGNED: parses ERP payment webhooks/polls, records confirmations idempotently; never initiates payment
- `solden/services/erp_readiness.py` — ALIGNED: org-scoped readiness evaluator over capability+checklist+rollback; pure read
- `solden/services/erp_test_probe.py` — ALIGNED: per-ERP read-only ping probe, org-scoped creds; no writes
- `solden/services/error_messages.py` — MECHANICAL: DID-WHY-NEXT error templating (some templates reference dormant VO)
- `solden/services/errors.py` — MECHANICAL: structured exception types + HTTP mapping; leaky decorator removed
- `solden/services/escalation_runner.py` — ALIGNED: deterministic threshold sweep emailing operators, idempotent UNIQUE event, SMTP-skip safe
- `solden/services/exception_graph.py` — ALIGNED: pure org-scoped node/edge builder over exceptions; deterministic clustering
- `solden/services/exception_resolver.py` — ALIGNED: vendor-not-in-ERP surfaces (no auto-create); AI suggests prose; idempotent re-post
- `solden/services/exception_routing.py` — ALIGNED: maps exception code->priority/handler/channel + creates task; no money/vendor text
- `solden/services/extraction_provenance.py` — MECHANICAL: provenance/evidence DTO builder for structured intakes
- `solden/services/finance_agent_governance.py` — ALIGNED: runtime spine deep-reviewed (rules decide; bounded)
- `solden/services/finance_agent_loop.py` — ALIGNED: observe->deliberate->act loop deep-reviewed (bounded)
- `solden/services/finance_agent_runtime.py` — ALIGNED: runtime facade deep-reviewed (bounded)
- `solden/services/finance_learning.py` — ALIGNED: Postgres-backed org-scoped; empty org raises; records outcomes only
- `solden/services/finance_runtime_actions.py` — ALIGNED: builds operator summaries + finance-lead drafts (not vendor); audited; org-asserted
- `solden/services/finance_runtime_autonomy.py` — ALIGNED: per-vendor earned-autonomy gates (rules decide); org-scoped; no money
- `solden/services/finance_runtime_invoice_processing.py` — ALIGNED: seeds AP item, deterministic workflow, blocks on field review, cross-tenant guarded
- `solden/services/finance_runtime_readiness.py` — ALIGNED: deterministic gate evaluation for skill readiness; no money path
- `solden/services/fuzzy_matching.py` — MECHANICAL: string/amount similarity utils; documents pairwise fusion non-production
- `solden/services/fx_conversion.py` — ALIGNED: ECB + fallback FX lookups, read-only rate math, cached, no posting
- `solden/services/fx_erp_sync.py` — ALIGNED: pulls FX from ERP and upserts; honest scope; no money movement
- `solden/services/gdpr_retention.py` — ALIGNED: anonymizes vendor/AP PII (never audit_events), advisory retention, audited, org-scoped
- `solden/services/gl_correction.py` — ALIGNED: DB-backed org-scoped GL-correction persistence + history/stats (just wired)
- `solden/services/gmail_api.py` — ALIGNED: OAuth/read/label client; gmail.send removed, draft/send deleted; encrypted tokens (LOW: EXCHANGE_DIAG_* logs dump OAuth payload, remove pre-GA)
- `solden/services/gmail_autopilot.py` — ALIGNED: watch/poll intake; unbound users skipped not bucketed to default; sub-limit gated; DB-backed
- `solden/services/gmail_extension_support.py` — ALIGNED: extension enrichment payloads; LLM writes operator prose only; vendor draft removed
- `solden/services/gmail_labels.py` — ALIGNED: label taxonomy + state<->label sync (narrow decision verbs); display layer
- `solden/services/gmail_mailbox_defaults.py` — MECHANICAL: mailbox settings + approval-target resolution helpers
- `solden/services/gmail_triage_service.py` — ALIGNED: orchestrates extraction/classification; single-pass hints advisory (APDecisionService owns routing); fails closed
- `solden/services/iban_change_freeze.py` — ALIGNED: three-factor IBAN freeze, payment-hold on change, audits factor names only
- `solden/services/implementation_service.py` — ALIGNED: onboarding checklist + step validation in settings; audited; no money
- `solden/services/intake_adapter.py` — ALIGNED: channel-agnostic intake dispatch, signature-verified, validate_transition gated, audited
- `solden/services/invoice_archive.py` — ALIGNED: SOX content-addressed immutable store, org-scoped PK, audited; retention advisory (honest)
- `solden/services/invoice_models.py` — MECHANICAL: channel-agnostic InvoiceData dataclass; pure DTO
- `solden/services/invoice_posting.py` — ALIGNED: posts AP bill, fails vendor_not_in_erp (no auto-create), SOD+chain fail-closed, payment record informational
- `solden/services/invoice_validation.py` — ALIGNED: deterministic gate cascade (shape/range/3-way/preflight/dup/sanctions); LLM no vote; audited
- `solden/services/invoice_workflow.py` — ALIGNED: routing via deterministic APDecisionService cascade; observer fan-out audit/labels/projections
- `solden/services/journal_entry_preview.py` — ALIGNED: deterministic Dr/Cr preview (no LLM), VAT, balance check; used verbatim for card+post
- `solden/services/learning.py` — ALIGNED: Postgres-backed org-scoped vendor->GL learning, write-through cache+TTL, org-asserted
- `solden/services/learning_calibration.py` — ALIGNED: calibration snapshots from feedback, org-scoped; auto-apply hard-bounded [0.70,0.99], audited
- `solden/services/llm_email_parser.py` — ALIGNED: LLM reads unstructured email for extraction + operator prose only; never routes; regex fallback
- `solden/services/llm_multimodal.py` — ALIGNED: vision/text extraction via bounded llm_gateway (EXTRACT_INVOICE_FIELDS); no Mistral bypass
- `solden/services/logging.py` — MECHANICAL: structured JSON/console logging helpers
- `solden/services/match_engine.py` — DEAD:generic match-engine registry never imported in prod (empty registry, run_match test-only); self-documented dormant
- `solden/services/metrics.py` — MECHANICAL: HTTP/recon metrics with durable DB + in-memory fallback; observability
- `solden/services/monitoring.py` — ALIGNED: org-scoped health checks; gmail-watch join scopes to tenant; alerts non-blocking
- `solden/services/multi_invoice_intake.py` — MECHANICAL: pre-split bridge producing intake units; no decisions
- `solden/services/multi_invoice_splitter.py` — MECHANICAL: deterministic regex boundary + pypdf split, no LLM
- `solden/services/needs_info_recovery.py` — ALIGNED: LLM proposes advisory plan only (whitelist), persisted as metadata, executes nothing
- `solden/services/notification_preferences.py` — ALIGNED: typed schema over users.preferences_json, DB-backed, per-user
- `solden/services/opencorporates_verifier.py` — ALIGNED: external registry read for due-diligence, audit envelope, no writes
- `solden/services/outbox.py` — ALIGNED: transactional outbox, org-scoped, FOR UPDATE SKIP LOCKED, dead-letter
- `solden/services/outlook_api.py` — ALIGNED: Graph client; Mail.Send dropped, send_message removed, no vendor email
- `solden/services/outlook_autopilot.py` — ALIGNED: per-user org resolution skips unbound users; in-memory _status is transient ops only
- `solden/services/outlook_email_processor.py` — ALIGNED: bridges Outlook attachments into shared triage path
- `solden/services/override_window.py` — ALIGNED: reversal via erp_router, audited transitions, agent never moves money, org-scoped
- `solden/services/paddle_billing.py` — ALIGNED: own-SaaS billing; webhook HMAC-verified, refuses unsigned, org-marker enforced
- `solden/services/pattern_store.py` — DEAD:orphaned (only lazy __init__ shim, no real callers/tests; also lacks org column)
- `solden/services/payment_models.py` — MECHANICAL: DTO + status/method frozensets; agent never executes payment
- `solden/services/payment_request.py` — ALIGNED: records/routes requests, DB-backed + audited, mark_paid records external ref only
- `solden/services/payment_tracking.py` — ALIGNED: records bank-confirmed payments, sanctions-gated, idempotent; no vendor email
- `solden/services/peppol_ubl_generator.py` — ALIGNED: generates UBL the operator transmits; Solden sends nothing to vendors
- `solden/services/peppol_ubl_parser.py` — ALIGNED: stdlib deterministic UBL parse, provenance METHOD_UBL_PARSER (not LLM)
- `solden/services/period_close.py` — ALIGNED: org-scoped close/accrual/lock, settings_json-backed, deterministic
- `solden/services/policy_compliance.py` — ALIGNED: deterministic rule cascade -> routing recommendation, org-scoped, DB-backed
- `solden/services/policy_linter.py` — MECHANICAL: static-analysis rules over threshold bands, no I/O
- `solden/services/policy_service.py` — ALIGNED: versioned/branchable policy store, org-scoped, mirrors to settings_json, audited
- `solden/services/priority_detection.py` — ALIGNED: deterministic weighted prioritization (advisory), org-scoped
- `solden/services/proactive_insights.py` — ALIGNED: rules decide which insights surface; LLM narrates operator prose only, rule fallback
- `solden/services/procurement_chat.py` — ALIGNED: PO card + decision routes through skill (policy precheck + audit), flag-gated
- `solden/services/purchase_orders.py` — ALIGNED: deterministic 3-way/2-way match, audited override; LLM only maps invoice->PO line after deterministic fail
- `solden/services/rate_limit.py` — ALIGNED: Redis-backed fail-closed in prod; in-memory only with explicit dev override
- `solden/services/reclassification_je.py` — ALIGNED: additive JE proposal, org-scoped, idempotent + audited, never amends original
- `solden/services/report_delivery.py` — ALIGNED: scheduled report email, per-subscription isolation, auto-pause on failure
- `solden/services/report_export.py` — ALIGNED: read-only CSV/JSON, org-scoped, never raises; onboarding report hasattr-guarded (VO dormant)
- `solden/services/role_resolver.py` — ALIGNED: read-only per-entity/org role resolution, tenant-scoped custom roles, fail-closed
- `solden/services/rule_engine.py` — ALIGNED: deterministic schema-validated rule eval + conflict detection
- `solden/services/saml_sso.py` — ALIGNED: per-tenant SAML, signature-verified assertions, replay-protected, JIT-provision org-scoped
- `solden/services/saml_validator.py` — ALIGNED: hardened XML parse + pinned-cert verify (no SHA-1/skip), full conditions/audience
- `solden/services/sample_data.py` — ALIGNED: is_sample-tagged rows, idempotent, org-scoped, clear deletes only samples
- `solden/services/sanctions_screening.py` — ALIGNED: provider screen persisted + audited, hard pre-payment block, org-scoped
- `solden/services/scheduled_reports.py` — DRIFT:_deliver_to_sheets calls SheetsAPIClient.extract_spreadsheet_id (module fn, not a method) -> AttributeError swallowed; sheets channel silently broken
- `solden/services/sheets_api.py` — MECHANICAL: thin Google Sheets REST client reusing Gmail OAuth; extract_spreadsheet_id is module-level (correct)
- `solden/services/sheets_export.py` — ALIGNED: org-scoped export (assert_org_id); writes operator's own Sheet, no agent action
- `solden/services/single_pass_cache.py` — ALIGNED: content-keyed idempotency cache; caches LLM extraction, org context applied post-hit
- `solden/services/single_pass_processor.py` — ALIGNED: LLM reads unstructured intake only; routing explicitly out of scope, fail-to-None
- `solden/services/slack_api.py` — ALIGNED: per-org token+channel via resolve_slack_runtime, shared fallback default-off, fail-closed
- `solden/services/slack_cards.py` — ALIGNED: undo/reversal cards resolve org channel; reversible override window, operator decides
- `solden/services/slack_digest.py` — DRIFT:send_digest reads runtime['channel']/['token'] but resolver returns approval_channel/bot_token -> every digest send fails (wired via coordination_engine)
- `solden/services/slack_notifications.py` — DRIFT:send_vendor_response/escalation_notification are dead orphans referencing the deleted vendor-followup feature (zero callers)
- `solden/services/sod_check.py` — ALIGNED: deterministic SoD gate, org-scoped audit query, per-tenant mode
- `solden/services/specialist_agent.py` — ALIGNED: error-boundary wrapper around skills, per-specialist actor_id audit, stateless
- `solden/services/specialist_circuit_breaker.py` — MECHANICAL: in-process three-state breaker; process-local by design
- `solden/services/specialist_router.py` — ALIGNED: per-runtime tenant-scoped intent->specialist dispatch with breaker
- `solden/services/spend_analysis.py` — ALIGNED: org-filtered (assert_org_id) read-only analytics, never raises
- `solden/services/state_observers.py` — ALIGNED: durable outbox fan-out, org-scoped writer; override-window observer reversible
- `solden/services/subscription.py` — ALIGNED: no vendor_outreach_draft key present; entity-aware billing, pool-ledger gating, LLM cost cap
- `solden/services/task_notifications.py` — ALIGNED: per-org Slack via resolve_slack_runtime, skips when no Slack, no global channel
- `solden/services/task_scheduler.py` — ALIGNED: per-org scans (org-scoped), reminders tenant-scoped
- `solden/services/tax_compliance.py` — ALIGNED: deterministic VAT/WHT/reverse-charge, org-scoped, per-tenant config
- `solden/services/team_invite_email.py` — MECHANICAL: operator/team invite compose+send; not vendor-facing
- `solden/services/teams_api.py` — ALIGNED: per-org webhook via get_organization_integration; approval cards, reversible, retries
- `solden/services/teams_notifications.py` — ALIGNED: Bot Framework cards (note: uses TEAMS_APP_SECRET vs teams_api TEAMS_APP_PASSWORD)
- `solden/services/three_way_match_runner.py` — ALIGNED: deterministic PO/GR/invoice match, idempotent audit, no LLM/money
- `solden/services/threshold_policy.py` — ALIGNED: layered deterministic threshold resolution; control changes fail-closed audited, clamped
- `solden/services/transactional_email.py` — MECHANICAL: stdlib SMTP relay; no-op when unconfigured, operator-facing
- `solden/services/trust_arc.py` — DRIFT:_send_slack_message calls _post_slack_blocks(org_id,text,...) but signature is (blocks,text,...,organization_id) -> wrong arg order, milestones misfire (wired via agent_background)
- `solden/services/user_offboarding.py` — ALIGNED: org-scoped soft-delete + cross-surface revoke, best-effort, audited
- `solden/services/vat_calculator.py` — MECHANICAL: deterministic VAT split (Decimal), fail-closed on unknown treatment
- `solden/services/vat_return.py` — ALIGNED: deterministic 9-box rollup over org-scoped posted bills; draft/supersede, parameterized SQL
- `solden/services/vat_return_forms.py` — MECHANICAL: per-country box-mapping of canonical rollup; raises on unsupported jurisdiction
- `solden/services/vendor_attribute_matcher.py` — ALIGNED: multi-attribute fraud scoring; IBAN-mismatch wins; org-scoped entry
- `solden/services/vendor_bootstrap.py` — DEAD:dormant-VO (deliberate): backfills vendor_profiles/history; tied to deferred VO scaffolding
- `solden/services/vendor_csv_import.py` — DEAD:dormant-VO (deliberate): bulk vendor master import; deferred vendor-onboarding feature
- `solden/services/vendor_dedup.py` — ALIGNED: org-scoped RRF dedup + merge with AP reassignment; deterministic, parameterized SQL
- `solden/services/vendor_domain_lock.py` — ALIGNED: deterministic sender-domain allowlist gate, TOFU via observer, audited, org-scoped
- `solden/services/vendor_domain_lookalike.py` — MECHANICAL: homoglyph/TLD/edit-distance detection; never raises
- `solden/services/vendor_enrichment.py` — DRIFT:audit actor_type='agent' for deterministic registry fetch (mislabel, low; otherwise org-scoped best-effort)
- `solden/services/vendor_erp_push.py` — DEAD:dormant-VO (deliberate): reverse vendor-master push to ERP; deferred VO (bank details excluded)
- `solden/services/vendor_erp_sync.py` — ALIGNED: pull ERP vendor master into profiles, org-scoped upsert, change detection, never raises
- `solden/services/vendor_inquiry.py` — ALIGNED: read-only sanitized status lookup operator copies into own reply; Solden sends nothing
- `solden/services/vendor_intelligence.py` — ALIGNED: static vendor-enrichment/GL-suggestion helper, read-only, wired into validation+sidebar; suggests never decides
- `solden/services/vendor_master_check.py` — ALIGNED: AP-side ERP-master gate, three-tier read-only lookup, org-scoped, no auto-bind
- `solden/services/vendor_onboarding_exceptions.py` — DEAD:dormant-VO (deliberate): read-only exception rows for parked VO Box type
- `solden/services/vendor_onboarding_lifecycle.py` — DEAD:dormant-VO (deliberate): chase loop operator-facing (no vendor email); vendor-master author only on unregistered VO path
- `solden/services/vendor_revalidation.py` — ALIGNED: eager flag-propagation to in-flight AP items, org-scoped, idempotent, audited, signals only
- `solden/services/vendor_risk.py` — ALIGNED: deterministic read-time risk score from profile, no I/O, no LLM, additive formula
- `solden/services/vendor_search.py` — MECHANICAL: RRF fusion ranking over passed-in candidates, no DB/network/LLM
- `solden/services/vendor_statement_recon.py` — ALIGNED: org-scoped read-only reconciliation report, money-safe, never raises, no writes
- `solden/services/webhook_delivery.py` — ALIGNED: HMAC-signed outbound, canonical X-Solden headers + retry-queue, org-scoped lookup
- `solden/services/worker_runtime.py` — MECHANICAL: process-role entrypoint wiring startup + signal shutdown
- `solden/services/workspace_fx.py` — ALIGNED: org-stored-rate FX conversion, Decimal math, None on no-rate, never raises
- `solden/services/workspace_reports.py` — ALIGNED: five org-scoped read-only reports, parameterized SQL, FX-aware, empty-but-valid on failure
- `solden/services/workspace_semaphore.py` — DRIFT:stale 'clearledgr:semaphore:' Redis key namespace (backend-untouched brand; soft)

## solden/services/annotation_targets  (7)
- `solden/services/annotation_targets/__init__.py` — MECHANICAL: imports the five concrete targets so they self-register
- `solden/services/annotation_targets/base.py` — ALIGNED: protocol + registry + outbox-backed dispatcher; per-attempt audit; per-tenant policy-gated
- `solden/services/annotation_targets/customer_webhook.py` — ALIGNED: HMAC-signed fan-out to org subscriptions; is_active=1 matches INTEGER schema; raises for retry
- `solden/services/annotation_targets/gmail_label.py` — ALIGNED: skips ERP-native/non-Gmail, applies finance labels, org-scoped via AP-item
- `solden/services/annotation_targets/netsuite_custom_field.py` — ALIGNED: SuiteTalk PATCH custbody_clearledgr_state; doc matches code; 4xx surfaced
- `solden/services/annotation_targets/sap_z_field.py` — DRIFT:docstring says Z_CLEARLEDGR_STATE but code default is YY1_CLEARLEDGR_STATE (doc-vs-code)
- `solden/services/annotation_targets/slack_card_update.py` — ALIGNED: chat.update existing card, skips no-op/no-thread, permanent errors surfaced

## solden/services/erp  (3)
- `solden/services/erp/__init__.py` — MECHANICAL: re-exports SAPAdapter + bill-adapter contracts
- `solden/services/erp/contracts.py` — ALIGNED: provider-agnostic adapter Protocols + router-backed delegates
- `solden/services/erp/sap.py` — ALIGNED: honest dry-run; non-dry-run park fails closed (FEATURE_SAP_LIVE_WRITE off)

## solden/services/finance_skills  (9)
- `solden/services/finance_skills/__init__.py` — MECHANICAL: re-exports skill classes (recon+procurement intentionally not exported here)
- `solden/services/finance_skills/ap_intent_contracts.py` — MECHANICAL: per-intent audit-contract + operator-copy tables + pure lookups
- `solden/services/finance_skills/ap_intent_handlers.py` — ALIGNED: 20 bounded handlers, deterministic prechecks, money/ERP only via workflow/override-window, audited, org-asserted
- `solden/services/finance_skills/ap_skill.py` — ALIGNED: AP skill behind runtime, bounded intents, deterministic prechecks (confirmed via handlers)
- `solden/services/finance_skills/base.py` — MECHANICAL: FinanceSkill ABC + preview_contract/execute_contract wrappers
- `solden/services/finance_skills/procurement_skill.py` — ALIGNED: PO intents via box_registry; _fetch_po org-checks agent path; refuses autonomy above dual-approval
- `solden/services/finance_skills/recon_skill.py` — DRIFT:contract-mismatch — registered but overrides preview/execute with SkillRequest sigs + SYNC execute; runtime execute_contract awaits execute(runtime,intent,payload) -> breaks at dispatch
- `solden/services/finance_skills/vendor_compliance_skill.py` — ALIGNED: read-only org-scoped vendor compliance snapshot, matches base contract, bounds-clamped
- `solden/services/finance_skills/workflow_health_skill.py` — ALIGNED: read-only org-scoped AP queue health, matches base contract, bounds-clamped

## solden/services/match_engines  (3)
- `solden/services/match_engines/__init__.py` — MECHANICAL: eager-imports engines so they self-register
- `solden/services/match_engines/ap_three_way.py` — ALIGNED: real 3-way match over PO service; deterministic tolerance
- `solden/services/match_engines/bank_reconciliation.py` — DRIFT:date window filtered on created_at (import time) not business posted_at it scores on

## solden/services/onboarding  (5)
- `solden/services/onboarding/__init__.py` — DEAD:dormant-VO (deliberate): re-exports KYC/bank-verifier abstractions for parked VO
- `solden/services/onboarding/bank_verifier.py` — DEAD:dormant-VO (deliberate): ABC + NotConfigured default + factory; only parked VO planner calls it
- `solden/services/onboarding/complyadvantage_provider.py` — DEAD:dormant-VO (deliberate): real KYC adapter self-registers only for unregistered VO path
- `solden/services/onboarding/kyc_policy.py` — DEAD:dormant-VO (deliberate): tier resolver from settings_json; only parked VO planner consumes
- `solden/services/onboarding/kyc_provider.py` — DEAD:dormant-VO (deliberate): KYC provider ABC + NotConfigured default + factory

## solden/workflows  (2)
- `solden/workflows/__init__.py` — MECHANICAL: package docstring pointing at gmail_activities
- `solden/workflows/gmail_activities.py` — ALIGNED: fail-loud org scoping, rules-first classify, no vendor-facing text

## tests  (303)
- `tests/conftest.py` — PENDING
- `tests/factories.py` — PENDING
- `tests/test_accrual_journal_entry.py` — PENDING
- `tests/test_accrual_journal_entry_post.py` — PENDING
- `tests/test_action_idempotency.py` — PENDING
- `tests/test_adaptive_thresholds.py` — PENDING
- `tests/test_admin_launch_controls.py` — PENDING
- `tests/test_africa_einvoice.py` — PENDING
- `tests/test_africa_einvoice_submission.py` — PENDING
- `tests/test_agent_anomaly_detection.py` — PENDING
- `tests/test_agent_background.py` — PENDING
- `tests/test_agent_credit_pool.py` — PENDING
- `tests/test_agent_end_to_end.py` — PENDING
- `tests/test_agent_intents_router.py` — PENDING
- `tests/test_agent_memory_service.py` — PENDING
- `tests/test_agent_reasoning.py` — PENDING
- `tests/test_agent_retry_jobs.py` — PENDING
- `tests/test_annotation_targets.py` — PENDING
- `tests/test_ap_aggregation_api.py` — PENDING
- `tests/test_ap_aging_report.py` — PENDING
- `tests/test_ap_audit_recent_api.py` — PENDING
- `tests/test_ap_confidence.py` — PENDING
- `tests/test_ap_decision.py` — PENDING
- `tests/test_ap_decision_override_reasoning.py` — PENDING
- `tests/test_ap_extraction_drift_metrics.py` — PENDING
- `tests/test_ap_intent_contracts.py` — PENDING
- `tests/test_ap_intent_handlers.py` — PENDING
- `tests/test_ap_item_detail.py` — PENDING
- `tests/test_ap_item_resolution.py` — PENDING
- `tests/test_ap_items_merge_and_audit_guardrails.py` — PENDING
- `tests/test_ap_multi_system_context.py` — PENDING
- `tests/test_ap_operator_audit.py` — PENDING
- `tests/test_ap_policy_framework.py` — PENDING
- `tests/test_ap_projection_contract.py` — PENDING
- `tests/test_ap_record_surfaces.py` — PENDING
- `tests/test_ap_role_guards.py` — PENDING
- `tests/test_ap_scenario_matrix.py` — PENDING
- `tests/test_ap_store_approval_followup.py` — PENDING
- `tests/test_ap_wedge_black_box.py` — PENDING
- `tests/test_api_endpoints.py` — PENDING
- `tests/test_api_keys_admin.py` — PENDING
- `tests/test_app_startup.py` — PENDING
- `tests/test_approval_delegation.py` — PENDING
- `tests/test_approval_dispatch_outbox.py` — PENDING
- `tests/test_approval_revert.py` — PENDING
- `tests/test_approver_workload.py` — PENDING
- `tests/test_audit_chain_integrity.py` — PENDING
- `tests/test_audit_chain_status_endpoint.py` — PENDING
- `tests/test_audit_entity_scope.py` — PENDING
- `tests/test_audit_governance_columns.py` — PENDING
- `tests/test_audit_policy_version.py` — PENDING
- `tests/test_audit_trail_service.py` — PENDING
- `tests/test_auth_token_reconciliation.py` — PENDING
- `tests/test_authorization_denied.py` — PENDING
- `tests/test_autonomy_config.py` — PENDING
- `tests/test_bank_details_tokenisation.py` — PENDING
- `tests/test_bank_match_box.py` — PENDING
- `tests/test_bank_reconciliation.py` — PENDING
- `tests/test_box_audit_reader.py` — PENDING
- `tests/test_box_cas.py` — PENDING
- `tests/test_box_exceptions_admin_api.py` — PENDING
- `tests/test_box_export_api.py` — PENDING
- `tests/test_box_extraction.py` — PENDING
- `tests/test_box_health.py` — PENDING
- `tests/test_box_invariants.py` — PENDING
- `tests/test_box_lifecycle_store.py` — PENDING
- `tests/test_box_owner.py` — PENDING
- `tests/test_box_projection.py` — PENDING
- `tests/test_bulk_batch_ops.py` — PENDING
- `tests/test_calendar_ooo.py` — PENDING
- `tests/test_channel_approval_contract.py` — PENDING
- `tests/test_chart_of_accounts.py` — PENDING
- `tests/test_compounding_learning_tenant_isolation.py` — PENDING
- `tests/test_confidence_calibration.py` — PENDING
- `tests/test_correction_learning.py` — PENDING
- `tests/test_cross_invoice_analysis.py` — PENDING
- `tests/test_cycle_time_metrics.py` — PENDING
- `tests/test_decision_context_capture.py` — PENDING
- `tests/test_declarative_workflow.py` — PENDING
- `tests/test_discount_optimizer.py` — PENDING
- `tests/test_dispute_reopen.py` — PENDING
- `tests/test_dispute_workflow.py` — PENDING
- `tests/test_dual_approval.py` — PENDING
- `tests/test_e2e_ap_flow.py` — PENDING
- `tests/test_e2e_rollback_controls.py` — PENDING
- `tests/test_email_parser_amount_selection.py` — PENDING
- `tests/test_email_parser_document_types.py` — PENDING
- `tests/test_endpoint_idempotency.py` — PENDING
- `tests/test_engine_async_hygiene.py` — PENDING
- `tests/test_engine_box_lock.py` — PENDING
- `tests/test_engine_idempotency.py` — PENDING
- `tests/test_engine_resume_plan.py` — PENDING
- `tests/test_erp_adapter_contracts.py` — PENDING
- `tests/test_erp_api_first.py` — PENDING
- `tests/test_erp_beta_fixes.py` — PENDING
- `tests/test_erp_field_mapping_posters.py` — PENDING
- `tests/test_erp_follow_on.py` — PENDING
- `tests/test_erp_journal_entry_capture.py` — PENDING
- `tests/test_erp_native_intake_pipeline.py` — PENDING
- `tests/test_erp_netsuite_e2e.py` — PENDING
- `tests/test_erp_oauth.py` — PENDING
- `tests/test_erp_payment_dispatcher.py` — PENDING
- `tests/test_erp_po_write.py` — PENDING
- `tests/test_erp_preflight.py` — PENDING
- `tests/test_erp_readiness.py` — PENDING
- `tests/test_erp_reversal.py` — PENDING
- `tests/test_erp_router_query_safety.py` — PENDING
- `tests/test_erp_sap_s4hana_write_surface.py` — PENDING
- `tests/test_erp_vendor_list.py` — PENDING
- `tests/test_erp_webhook_security.py` — PENDING
- `tests/test_escalation_policies.py` — PENDING
- `tests/test_exception_graph.py` — PENDING
- `tests/test_exception_resolver.py` — PENDING
- `tests/test_execution_engine.py` — PENDING
- `tests/test_extraction_guardrails.py` — PENDING
- `tests/test_extraction_provenance_coverage.py` — PENDING
- `tests/test_finance_agent_governance.py` — PENDING
- `tests/test_finance_agent_runtime.py` — PENDING
- `tests/test_finance_contracts.py` — PENDING
- `tests/test_finance_email_store.py` — PENDING
- `tests/test_finance_learning_service.py` — PENDING
- `tests/test_fraud_controls_gate.py` — PENDING
- `tests/test_fx_conversion.py` — PENDING
- `tests/test_gate_constraint_enforcement.py` — PENDING
- `tests/test_gdpr_retention.py` — PENDING
- `tests/test_generic_engine_bank_match.py` — PENDING
- `tests/test_generic_engine_purchase_order.py` — PENDING
- `tests/test_gl_correction_wiring.py` — PENDING
- `tests/test_gmail_activities.py` — PENDING
- `tests/test_gmail_autopilot.py` — PENDING
- `tests/test_gmail_classification.py` — PENDING
- `tests/test_gmail_label_sync.py` — PENDING
- `tests/test_gmail_labels.py` — PENDING
- `tests/test_gmail_labels_bidirectional.py` — PENDING
- `tests/test_gmail_oauth_error_surfacing.py` — PENDING
- `tests/test_gmail_webhooks.py` — PENDING
- `tests/test_governance_event_path.py` — PENDING
- `tests/test_historical_replay.py` — PENDING
- `tests/test_iban_change_freeze.py` — PENDING
- `tests/test_iban_validation.py` — PENDING
- `tests/test_intake_audit_coverage.py` — PENDING
- `tests/test_invoice_archive.py` — PENDING
- `tests/test_invoice_extraction_eval_harness.py` — PENDING
- `tests/test_invoice_extraction_golden.py` — PENDING
- `tests/test_invoice_workflow_controls.py` — PENDING
- `tests/test_invoice_workflow_runtime_state_transitions.py` — PENDING
- `tests/test_journal_entry_preview.py` — PENDING
- `tests/test_learning_calibration.py` — PENDING
- `tests/test_learning_service_persistence.py` — PENDING
- `tests/test_llm_budget_cap.py` — PENDING
- `tests/test_llm_call_box_link.py` — PENDING
- `tests/test_llm_cost_summary.py` — PENDING
- `tests/test_llm_email_parser.py` — PENDING
- `tests/test_llm_gateway.py` — PENDING
- `tests/test_llm_no_gateway_bypass.py` — PENDING
- `tests/test_mandatory_gl_gate.py` — PENDING
- `tests/test_match_config_api.py` — PENDING
- `tests/test_match_engine.py` — PENDING
- `tests/test_match_mode_dispatch.py` — PENDING
- `tests/test_metrics_persistence.py` — PENDING
- `tests/test_migration_v42.py` — PENDING
- `tests/test_modules_5_6_carry_overs.py` — PENDING
- `tests/test_money_decimal.py` — PENDING
- `tests/test_monitoring.py` — PENDING
- `tests/test_multi_entity.py` — PENDING
- `tests/test_multi_invoice_intake.py` — PENDING
- `tests/test_multi_invoice_splitter.py` — PENDING
- `tests/test_multi_tenant_isolation.py` — PENDING
- `tests/test_needs_info_recovery.py` — PENDING
- `tests/test_netsuite_panel_audit_integration.py` — PENDING
- `tests/test_no_currency_leaks.py` — PENDING
- `tests/test_no_legacy_orchestrator_runtime_calls.py` — PENDING
- `tests/test_notification_preferences.py` — PENDING
- `tests/test_onboarding_gates.py` — PENDING
- `tests/test_onboarding_token_single_use.py` — PENDING
- `tests/test_org_config_roundtrip.py` — PENDING
- `tests/test_org_purge.py` — PENDING
- `tests/test_org_utils.py` — PENDING
- `tests/test_outbox.py` — PENDING
- `tests/test_outgoing_webhooks.py` — PENDING
- `tests/test_outlook_integration.py` — PENDING
- `tests/test_override_window.py` — PENDING
- `tests/test_override_window_durability.py` — PENDING
- `tests/test_payment_confirmations.py` — PENDING
- `tests/test_payment_confirmations_api.py` — PENDING
- `tests/test_payment_request_persistence.py` — PENDING
- `tests/test_payment_state_machine.py` — PENDING
- `tests/test_payment_status_polling.py` — PENDING
- `tests/test_payment_tracking.py` — PENDING
- `tests/test_peppol_inbound.py` — PENDING
- `tests/test_peppol_outbound.py` — PENDING
- `tests/test_period_close.py` — PENDING
- `tests/test_pipeline_hardening.py` — PENDING
- `tests/test_plan_acceptance.py` — PENDING
- `tests/test_planning_engine.py` — PENDING
- `tests/test_planning_engine_vo_deprecation.py` — PENDING
- `tests/test_policy_branches.py` — PENDING
- `tests/test_policy_linter.py` — PENDING
- `tests/test_policy_service.py` — PENDING
- `tests/test_portal_input_validation.py` — PENDING
- `tests/test_proactive_insights_narration.py` — PENDING
- `tests/test_procurement_chat.py` — PENDING
- `tests/test_procurement_skill.py` — PENDING
- `tests/test_prompt_guard.py` — PENDING
- `tests/test_purchase_order_routes.py` — PENDING
- `tests/test_quickbooks_xero_intake.py` — PENDING
- `tests/test_rate_limit.py` — PENDING
- `tests/test_recalibrate_confidence_gate.py` — PENDING
- `tests/test_reclassification_je.py` — PENDING
- `tests/test_report_export.py` — PENDING
- `tests/test_report_subscriptions.py` — PENDING
- `tests/test_request_latency_fixes.py` — PENDING
- `tests/test_role_taxonomy.py` — PENDING
- `tests/test_route_auth_policy_inventory.py` — PENDING
- `tests/test_runtime_surface_scope.py` — PENDING
- `tests/test_runtime_tenant_isolation.py` — PENDING
- `tests/test_runtime_triage_group8.py` — PENDING
- `tests/test_saml_sso.py` — PENDING
- `tests/test_sample_data.py` — PENDING
- `tests/test_sanctions_screening.py` — PENDING
- `tests/test_sap_adapter_fail_closed.py` — PENDING
- `tests/test_sap_b1_poll_celery_task.py` — PENDING
- `tests/test_sap_fiori_audit_integration.py` — PENDING
- `tests/test_sap_s4hana_payment_path.py` — PENDING
- `tests/test_scheduled_reports.py` — PENDING
- `tests/test_secrets.py` — PENDING
- `tests/test_services_tenant_isolation.py` — PENDING
- `tests/test_single_pass_cache.py` — PENDING
- `tests/test_single_pass_processor.py` — PENDING
- `tests/test_slack_notifications.py` — PENDING
- `tests/test_sod_enforcement.py` — PENDING
- `tests/test_specialist_agent.py` — PENDING
- `tests/test_specialist_circuit_breaker.py` — PENDING
- `tests/test_spend_analysis.py` — PENDING
- `tests/test_state_audit_atomicity.py` — PENDING
- `tests/test_state_mutation_discipline.py` — PENDING
- `tests/test_state_observers.py` — PENDING
- `tests/test_subscription_quota_enforcement.py` — PENDING
- `tests/test_subscription_service.py` — PENDING
- `tests/test_subscription_tier_features.py` — PENDING
- `tests/test_synthetic_invoice_suite.py` — PENDING
- `tests/test_task_scheduler_tenant.py` — PENDING
- `tests/test_tax_compliance.py` — PENDING
- `tests/test_team_invite_email.py` — PENDING
- `tests/test_team_invite_role_normalisation.py` — PENDING
- `tests/test_team_offboarding.py` — PENDING
- `tests/test_teams_audit_integration.py` — PENDING
- `tests/test_teams_installations.py` — PENDING
- `tests/test_teams_verify.py` — PENDING
- `tests/test_tenant_isolation.py` — PENDING
- `tests/test_three_way_match.py` — PENDING
- `tests/test_threshold_policy.py` — PENDING
- `tests/test_trust_arc.py` — PENDING
- `tests/test_user_offboarding.py` — PENDING
- `tests/test_v1_auth.py` — PENDING
- `tests/test_v1_boundary_flags.py` — PENDING
- `tests/test_v1_core_completion.py` — PENDING
- `tests/test_v1_idempotency.py` — PENDING
- `tests/test_v1_integration.py` — PENDING
- `tests/test_v1_rate_limit.py` — PENDING
- `tests/test_v1_records.py` — PENDING
- `tests/test_v1_webhooks.py` — PENDING
- `tests/test_validate_launch_evidence.py` — PENDING
- `tests/test_validation_per_rule_audit.py` — PENDING
- `tests/test_vat_modeling.py` — PENDING
- `tests/test_vat_return_forms.py` — PENDING
- `tests/test_vendor_activation_sla.py` — PENDING
- `tests/test_vendor_activation_slack.py` — PENDING
- `tests/test_vendor_attribute_matcher.py` — PENDING
- `tests/test_vendor_csv_import.py` — PENDING
- `tests/test_vendor_dedup.py` — PENDING
- `tests/test_vendor_domain_lock.py` — PENDING
- `tests/test_vendor_domain_lookalike.py` — PENDING
- `tests/test_vendor_erp_push.py` — PENDING
- `tests/test_vendor_erp_sync.py` — PENDING
- `tests/test_vendor_inquiry.py` — PENDING
- `tests/test_vendor_issue_payloads.py` — PENDING
- `tests/test_vendor_kyc.py` — PENDING
- `tests/test_vendor_master_check.py` — PENDING
- `tests/test_vendor_onboarding_exceptions.py` — PENDING
- `tests/test_vendor_onboarding_lifecycle.py` — PENDING
- `tests/test_vendor_onboarding_state_machine.py` — PENDING
- `tests/test_vendor_portal.py` — PENDING
- `tests/test_vendor_revalidation.py` — PENDING
- `tests/test_vendor_risk_payload.py` — PENDING
- `tests/test_vendor_search.py` — PENDING
- `tests/test_vendor_statement_recon.py` — PENDING
- `tests/test_vendor_status.py` — PENDING
- `tests/test_webhook_auth_hardening.py` — PENDING
- `tests/test_workflow_hooks.py` — PENDING
- `tests/test_workflow_isolation.py` — PENDING
- `tests/test_workflow_specs.py` — PENDING
- `tests/test_workspace_audit_export.py` — PENDING
- `tests/test_workspace_audit_search.py` — PENDING
- `tests/test_workspace_audit_webhook_fanout.py` — PENDING
- `tests/test_workspace_connection_health.py` — PENDING
- `tests/test_workspace_custom_roles.py` — PENDING
- `tests/test_workspace_entity_roles.py` — PENDING
- `tests/test_workspace_erp_field_mappings.py` — PENDING
- `tests/test_workspace_fx.py` — PENDING
- `tests/test_workspace_org_settings.py` — PENDING
- `tests/test_workspace_reports.py` — PENDING
- `tests/test_workspace_rules.py` — PENDING

## tests/erp_dom_regression  (1)
- `tests/erp_dom_regression/profiles.py` — PENDING

## ui/gmail-extension  (6)
- `ui/gmail-extension/background.js` — PENDING
- `ui/gmail-extension/config.js` — PENDING
- `ui/gmail-extension/content-script.js` — PENDING
- `ui/gmail-extension/queue-manager.js` — PENDING
- `ui/gmail-extension/route-capture.js` — PENDING
- `ui/gmail-extension/vitest.config.js` — PENDING

## ui/gmail-extension/build  (6)
- `ui/gmail-extension/build/background.js` — PENDING
- `ui/gmail-extension/build/config 2.js` — PENDING
- `ui/gmail-extension/build/config.js` — PENDING
- `ui/gmail-extension/build/content-script.js` — PENDING
- `ui/gmail-extension/build/queue-manager.js` — PENDING
- `ui/gmail-extension/build/route-capture.js` — PENDING

## ui/gmail-extension/build/clients  (14)
- `ui/gmail-extension/build/clients/BaseClient 2.js` — PENDING
- `ui/gmail-extension/build/clients/BaseClient.js` — PENDING
- `ui/gmail-extension/build/clients/CategorizationClient 2.js` — PENDING
- `ui/gmail-extension/build/clients/CategorizationClient.js` — PENDING
- `ui/gmail-extension/build/clients/ClassificationClient 2.js` — PENDING
- `ui/gmail-extension/build/clients/ClassificationClient.js` — PENDING
- `ui/gmail-extension/build/clients/ExceptionClient 2.js` — PENDING
- `ui/gmail-extension/build/clients/ExceptionClient.js` — PENDING
- `ui/gmail-extension/build/clients/ExtractionClient 2.js` — PENDING
- `ui/gmail-extension/build/clients/ExtractionClient.js` — PENDING
- `ui/gmail-extension/build/clients/MatchingClient 2.js` — PENDING
- `ui/gmail-extension/build/clients/MatchingClient.js` — PENDING
- `ui/gmail-extension/build/clients/emailParsing 2.js` — PENDING
- `ui/gmail-extension/build/clients/emailParsing.js` — PENDING

## ui/gmail-extension/build/engines  (2)
- `ui/gmail-extension/build/engines/DiscoveryEngine 2.js` — PENDING
- `ui/gmail-extension/build/engines/DiscoveryEngine.js` — PENDING

## ui/gmail-extension/build/workflows  (2)
- `ui/gmail-extension/build/workflows/registry 2.js` — PENDING
- `ui/gmail-extension/build/workflows/registry.js` — PENDING

## ui/gmail-extension/clients  (7)
- `ui/gmail-extension/clients/BaseClient.js` — PENDING
- `ui/gmail-extension/clients/CategorizationClient.js` — PENDING
- `ui/gmail-extension/clients/ClassificationClient.js` — PENDING
- `ui/gmail-extension/clients/ExceptionClient.js` — PENDING
- `ui/gmail-extension/clients/ExtractionClient.js` — PENDING
- `ui/gmail-extension/clients/MatchingClient.js` — PENDING
- `ui/gmail-extension/clients/emailParsing.js` — PENDING

## ui/gmail-extension/engines  (1)
- `ui/gmail-extension/engines/DiscoveryEngine.js` — PENDING

## ui/gmail-extension/src/components  (9)
- `ui/gmail-extension/src/components/ActionDialog.js` — PENDING
- `ui/gmail-extension/src/components/ActionDialog.test.js` — PENDING
- `ui/gmail-extension/src/components/BudgetPausedBanner.js` — PENDING
- `ui/gmail-extension/src/components/InviteVendorModal.js` — PENDING
- `ui/gmail-extension/src/components/OnboardingFlow.js` — PENDING
- `ui/gmail-extension/src/components/SidebarApp.js` — PENDING
- `ui/gmail-extension/src/components/SidebarApp.test.js` — PENDING
- `ui/gmail-extension/src/components/ThreadSidebar.js` — PENDING
- `ui/gmail-extension/src/components/ThreadSidebar.test.js` — PENDING

## ui/gmail-extension/src  (4)
- `ui/gmail-extension/src/inboxsdk-layer.js` — PENDING
- `ui/gmail-extension/src/settings-tab.js` — PENDING
- `ui/gmail-extension/src/styles.js` — PENDING
- `ui/gmail-extension/src/thesis-compliance.test.js` — PENDING

## ui/gmail-extension/src/routes  (3)
- `ui/gmail-extension/src/routes/oauth-bridge.js` — PENDING
- `ui/gmail-extension/src/routes/route-helpers.js` — PENDING
- `ui/gmail-extension/src/routes/workspace-shell-api.js` — PENDING

## ui/gmail-extension/src/test-utils  (1)
- `ui/gmail-extension/src/test-utils/happy-dom-env.js` — PENDING

## ui/gmail-extension/src/utils  (13)
- `ui/gmail-extension/src/utils/capabilities.js` — PENDING
- `ui/gmail-extension/src/utils/document-types.js` — PENDING
- `ui/gmail-extension/src/utils/formatters.js` — PENDING
- `ui/gmail-extension/src/utils/formatters.test.js` — PENDING
- `ui/gmail-extension/src/utils/inbox-route.js` — PENDING
- `ui/gmail-extension/src/utils/perf-budget.js` — PENDING
- `ui/gmail-extension/src/utils/record-route.js` — PENDING
- `ui/gmail-extension/src/utils/roles.js` — PENDING
- `ui/gmail-extension/src/utils/store.js` — PENDING
- `ui/gmail-extension/src/utils/store.test.js` — PENDING
- `ui/gmail-extension/src/utils/vendor-route.js` — PENDING
- `ui/gmail-extension/src/utils/work-actions.js` — PENDING
- `ui/gmail-extension/src/utils/workspace-link.js` — PENDING

## ui/gmail-extension/utils  (2)
- `ui/gmail-extension/utils/ap_classifier.js` — PENDING
- `ui/gmail-extension/utils/retry.js` — PENDING

## ui/gmail-extension/workflows  (1)
- `ui/gmail-extension/workflows/registry.js` — PENDING

## ui/outlook-addin/src  (1)
- `ui/outlook-addin/src/outlook-entry.js` — PENDING

## ui/shared  (3)
- `ui/shared/hooks.js` — PENDING
- `ui/shared/intent-labels.js` — PENDING
- `ui/shared/tokens.js` — PENDING

## ui/web-app  (2)
- `ui/web-app/server.js` — PENDING
- `ui/web-app/vite.config.js` — PENDING

## ui/web-app/src  (2)
- `ui/web-app/src/App.js` — PENDING
- `ui/web-app/src/main.js` — PENDING

## ui/web-app/src/api  (1)
- `ui/web-app/src/api/client.js` — PENDING

## ui/web-app/src/auth  (6)
- `ui/web-app/src/auth/AuthGate.js` — PENDING
- `ui/web-app/src/auth/InviteAcceptPage.js` — PENDING
- `ui/web-app/src/auth/LegalPages.js` — PENDING
- `ui/web-app/src/auth/LoginPage.js` — PENDING
- `ui/web-app/src/auth/OAuthIcons.js` — PENDING
- `ui/web-app/src/auth/useSession.js` — PENDING

## ui/web-app/src/components  (2)
- `ui/web-app/src/components/AgentActivityRibbon.js` — PENDING
- `ui/web-app/src/components/StatePrimitives.js` — PENDING

## ui/web-app/src/lib  (2)
- `ui/web-app/src/lib/faviconBadge.js` — PENDING
- `ui/web-app/src/lib/faviconBadge.test.js` — PENDING

## ui/web-app/src/pages  (1)
- `ui/web-app/src/pages/PlaceholderPage.js` — PENDING

## ui/web-app/src/routes/pages  (37)
- `ui/web-app/src/routes/pages/ActivityPage.js` — PENDING
- `ui/web-app/src/routes/pages/ActivityRoute.js` — PENDING
- `ui/web-app/src/routes/pages/ApiKeysPage.js` — PENDING
- `ui/web-app/src/routes/pages/ApiKeysRoute.js` — PENDING
- `ui/web-app/src/routes/pages/AuditLogPage.js` — PENDING
- `ui/web-app/src/routes/pages/AuditLogRoute.js` — PENDING
- `ui/web-app/src/routes/pages/ConnectionsPage.js` — PENDING
- `ui/web-app/src/routes/pages/ConnectionsRoute.js` — PENDING
- `ui/web-app/src/routes/pages/ExceptionsPage.js` — PENDING
- `ui/web-app/src/routes/pages/ExceptionsRoute.js` — PENDING
- `ui/web-app/src/routes/pages/HealthPage.js` — PENDING
- `ui/web-app/src/routes/pages/HealthRoute.js` — PENDING
- `ui/web-app/src/routes/pages/HomePage.js` — PENDING
- `ui/web-app/src/routes/pages/OnboardingPage.js` — PENDING
- `ui/web-app/src/routes/pages/PlanPage.js` — PENDING
- `ui/web-app/src/routes/pages/PlanRoute.js` — PENDING
- `ui/web-app/src/routes/pages/ProcurementPage.js` — PENDING
- `ui/web-app/src/routes/pages/ProcurementPage.test.js` — PENDING
- `ui/web-app/src/routes/pages/ProcurementRoute.js` — PENDING
- `ui/web-app/src/routes/pages/RecordDetailPage.js` — PENDING
- `ui/web-app/src/routes/pages/RecordDetailRoute.js` — PENDING
- `ui/web-app/src/routes/pages/RecordsPage.js` — PENDING
- `ui/web-app/src/routes/pages/RecordsRoute.js` — PENDING
- `ui/web-app/src/routes/pages/ReportsPage.js` — PENDING
- `ui/web-app/src/routes/pages/ReportsRoute.js` — PENDING
- `ui/web-app/src/routes/pages/RulesPage.js` — PENDING
- `ui/web-app/src/routes/pages/RulesRoute.js` — PENDING
- `ui/web-app/src/routes/pages/SettingsPage.js` — PENDING
- `ui/web-app/src/routes/pages/SettingsRoute.js` — PENDING
- `ui/web-app/src/routes/pages/StatusPage.js` — PENDING
- `ui/web-app/src/routes/pages/VendorDetailPage.js` — PENDING
- `ui/web-app/src/routes/pages/VendorDetailRoute.js` — PENDING
- `ui/web-app/src/routes/pages/VendorsPage.js` — PENDING
- `ui/web-app/src/routes/pages/VendorsRoute.js` — PENDING
- `ui/web-app/src/routes/pages/WorkflowsPage.js` — PENDING
- `ui/web-app/src/routes/pages/WorkflowsPage.test.js` — PENDING
- `ui/web-app/src/routes/pages/WorkflowsRoute.js` — PENDING

## ui/web-app/src/routes  (2)
- `ui/web-app/src/routes/pipeline-views.js` — PENDING
- `ui/web-app/src/routes/route-helpers.js` — PENDING

## ui/web-app/src/shell  (15)
- `ui/web-app/src/shell/AppFooter.js` — PENDING
- `ui/web-app/src/shell/AppShell.js` — PENDING
- `ui/web-app/src/shell/BootstrapContext.js` — PENDING
- `ui/web-app/src/shell/BrandMark.js` — PENDING
- `ui/web-app/src/shell/CommandK.js` — PENDING
- `ui/web-app/src/shell/EntityContext.js` — PENDING
- `ui/web-app/src/shell/EntitySwitcher.js` — PENDING
- `ui/web-app/src/shell/ErrorBoundary.js` — PENDING
- `ui/web-app/src/shell/MobileShellContext.js` — PENDING
- `ui/web-app/src/shell/OnboardingGate.js` — PENDING
- `ui/web-app/src/shell/SidebarNav.js` — PENDING
- `ui/web-app/src/shell/SidebarNav.test.js` — PENDING
- `ui/web-app/src/shell/Toast.js` — PENDING
- `ui/web-app/src/shell/Topbar.js` — PENDING
- `ui/web-app/src/shell/usePageProps.js` — PENDING

## ui/web-app/src/utils  (10)
- `ui/web-app/src/utils/capabilities.js` — PENDING
- `ui/web-app/src/utils/document-types.js` — PENDING
- `ui/web-app/src/utils/formatters.js` — PENDING
- `ui/web-app/src/utils/htm.js` — PENDING
- `ui/web-app/src/utils/perf-budget.js` — PENDING
- `ui/web-app/src/utils/record-route.js` — PENDING
- `ui/web-app/src/utils/roles.js` — PENDING
- `ui/web-app/src/utils/store.js` — PENDING
- `ui/web-app/src/utils/vendor-route.js` — PENDING
- `ui/web-app/src/utils/work-actions.js` — PENDING

---

## FIX BACKLOG (drift/dead found during genuine review — work one-at-a-time)

### Wave 1 (2026-05-25)
- [ ] DRIFT `solden/core/stores/vendor_store.py` — stale schema comment (L68-73) claims a vendor-facing remittance auto-send default that was removed 2026-05-02. Doc-only fix (contradicts "Solden sends zero vendor email").
- [ ] DRIFT `solden/integrations/oauth.py` — `_erp_connections` in-memory dict; `get_erp_connection_record`/`ensure_valid_token` read only the dict (lost on restart / second worker) though `save_erp_connection` also persists to DB. Live via api/erp_oauth.py. Same class as the removed onboarding in-memory dict.
- [ ] DRIFT `solden/services/match_engines/bank_reconciliation.py` — `find_candidates` filters the date window on `created_at` (import time), not the business `posted_at` used in scoring; can silently drop matchable rows.
- [ ] DRIFT `solden/cli/health.py` — docstring promises an M19 source-scan check the code never performs (doc-only).
- [ ] DEAD `solden/models/patterns.py` + `solden/services/pattern_store.py` — orphaned cluster (pattern_store reachable only via a lazy export shim, zero real callers, no tests).
- [ ] FOLLOWUP `solden/integrations/erp_xero.py` — skipped by the Wave-1 subagent; still PENDING, re-review next wave.

### Wave 2 (2026-05-25) — core (52) + api (90) + erp_xero
HIGH (real bug / security):
- [ ] DRIFT `solden/api/pipelines.py` — `delete_saved_view` keyed by view_id only, NO org filter in handler or SQL → an ops user can delete another tenant's saved view. CROSS-TENANT WRITE.
- [ ] DRIFT `solden/api/ap_items_read_routes.py` — `/consolidated` calls undefined `verify_org_access` → NameError 500 for any caller past the FC check.
HIGH/systemic (member-writable governance — same class as the workspace_rules/sample_data fixes):
- [ ] DRIFT `solden/api/ap_policies.py` — PUT AP business policy guarded only by get_current_user (no admin gate).
- [ ] DRIFT `solden/api/dual_approval.py` — PUT /policy/dual-approval (second-signature threshold) member-writable.
- [ ] DRIFT `solden/api/escalation_policies.py` — full CRUD member-writable.
- [ ] DRIFT `solden/api/erp_connections.py` — connect/disconnect/gl-map member-writable (defines unused _ADMIN_ROLES).
- [ ] DRIFT `solden/api/erp_connection_ops.py` — rotate-credentials/test member-writable.
- [ ] DRIFT `solden/api/policies.py` — create-version/rollback of governance policies member-writable.
- [ ] DRIFT `solden/api/vendor_status.py` — verify-registration POST not admin-gated + auto-creates vendor stub.
- [ ] DRIFT `solden/api/org_config.py` — governance PUT/PATCH lack admin gate (LOWER: router disabled in strict prod).
MED (strict-profile prod-404 — the match-config failure mode):
- [ ] DRIFT `solden/api/ap_items_read_routes.py` — `/api/ap/items/audit/export` not allowlisted (two-segment path misses the single-segment pattern).
- [ ] DRIFT `solden/api/netsuite_panel.py` — POST approve/reject/request-info action paths not allowlisted.
- [ ] DRIFT `solden/api/ops.py` — `/api/ops/box-health` mounted but not allowlisted.
LOW (brand/doc/dead):
- [ ] DRIFT `solden/api/ap_item_detail.py` — stale "mint-green" brand + "Sonnet path" LLM-vendor tell in docstrings.
- [ ] DRIFT `solden/api/gmail_webhooks.py` — OAuth success page stale Streak-era cream/green serif theme.
- [ ] DRIFT `solden/api/box_export.py` — docstring frames already-implemented bank-match/generic export as "future".
- [ ] DRIFT `solden/api/payment_confirmations.py` — remittance-config docstring claims active vendor auto-send (deleted).
- [ ] DEAD `solden/core/error_codes.py` — zero importers; live ErrorCode enum is services/errors.py.

### RESOLVED (2026-05-25) — reds + governance cluster
- [x] pipelines.delete_saved_view cross-tenant write → org-scoped (commit + test).
- [x] /consolidated: route-shadowing (moved above /{ap_item_id}) + always-403 FC-arg bug (3 sites incl settings.py migration parallel/cutover, now require_financial_controller) + NameError (verify_org_access imported). Test added.
- [x] Governance cluster A: ap_policies PUT, dual_approval PUT, escalation create/patch/delete, policies versions+rollback → require_workspace_admin.
- [x] Governance cluster B: erp_connections (8 mutations), erp_connection_ops (test+rotate), vendor_status verify-registration → admin-gated.
STILL OPEN:
- [ ] org_config governance PUT/PATCH admin gate (LOWER: router disabled in strict prod).
- [ ] MED prod-404s: ap_items_read /audit/export, netsuite_panel POST actions, ops/box-health (strict-profile allowlist).
- [ ] LOW brand/doc/dead: ap_item_detail, gmail_webhooks, box_export, payment_confirmations, error_codes(DEAD), vendor_store remittance comment, oauth in-memory, bank_reconciliation date-col, cli/health docstring, models/patterns(DEAD).

### Wave 3a (2026-05-25) — services aa/ab/ac (114 files)
- [ ] DRIFT `solden/services/agent_reasoning.py` — LLM-derived confidence drives `_make_decision`'s auto_approve label, which `gmail_extension.py:1606-1611` uses to bump/clamp the auto-approve gate. Bounded-agent: the LLM must not move the routing gate (canonical path is deterministic ap_decision). VERIFY + fix.
- [ ] DRIFT `solden/services/correction_learning.py` — `get_recent_corrections` reads `self._rules` which doesn't exist (class has `_learned_rules`) → AttributeError path. VERIFY + fix.
- [ ] DRIFT `solden/services/budget_awareness.py` — in-memory `_spending` accumulator lost on restart/2nd worker.
- [ ] DRIFT `solden/services/calendar_ooo.py` — in-process TTL OOO-availability cache; workers disagree.
- [ ] DRIFT `solden/services/conversational_agent.py` — `_conversations` in-memory dict holds question state; lost on restart.
- [ ] DRIFT `solden/services/email_tasks.py` — module-level `db = get_db()` at import (breaks test singleton reset / per-worker rebind).
- [ ] DEAD `solden/services/match_engine.py` — generic match registry never imported in prod (self-documented dormant; LOW).
- [ ] LOW `solden/services/gmail_api.py` — EXCHANGE_DIAG_* warn logs dump OAuth payload (client_id/secret-len/code-prefix) + Google body; remove pre-GA.

### Wave 3b (2026-05-25) — services ad/ae/af (110 files); solden/ COMPLETE (454/454)
REAL broken-live-path bugs:
- [ ] DRIFT `solden/services/slack_digest.py` — send_digest reads runtime['channel']/['token'] but resolve_slack_runtime returns approval_channel/bot_token → every digest send fails (wired via coordination_engine send_slack_digest).
- [ ] DRIFT `solden/services/trust_arc.py` — _send_slack_message calls _post_slack_blocks(org_id, text, ...) but signature is (blocks, text, ..., organization_id) → wrong arg order, trust-arc milestones misfire (wired via agent_background.run_trust_arc_tick).
- [ ] DRIFT `solden/services/scheduled_reports.py` — _deliver_to_sheets calls SheetsAPIClient.extract_spreadsheet_id (a module-level fn, not a method) → AttributeError swallowed by broad except; sheets delivery silently broken.
- [ ] DRIFT `solden/services/finance_skills/recon_skill.py` — registered but overrides preview/execute with SkillRequest signatures + a SYNC execute; runtime execute_contract awaits execute(runtime,intent,payload) → recon intents break at dispatch.
LOWER:
- [ ] DRIFT `solden/services/slack_notifications.py` — send_vendor_response/escalation_notification are dead orphans (deleted vendor-followup feature; zero callers).
- [ ] DRIFT `solden/services/vendor_enrichment.py` — audit actor_type='agent' for a deterministic registry fetch (mislabel).
- [ ] DRIFT `solden/services/workspace_semaphore.py` — 'clearledgr:semaphore:' Redis key (backend brand untouched; soft).
- [ ] DRIFT `solden/services/annotation_targets/sap_z_field.py` — docstring Z_CLEARLEDGR_STATE vs code default YY1_CLEARLEDGR_STATE.
- [ ] DEAD `solden/services/pattern_store.py` — orphaned (lazy shim only, no callers/tests, no org column).
- [ ] NOTE: teams_notifications uses TEAMS_APP_SECRET vs teams_api TEAMS_APP_PASSWORD (cross-file cred inconsistency).
DEAD:dormant-VO (deliberate, deferred 2026-04-30): vendor_bootstrap, vendor_csv_import, vendor_erp_push, vendor_onboarding_exceptions, vendor_onboarding_lifecycle, onboarding/{__init__,bank_verifier,complyadvantage_provider,kyc_policy,kyc_provider}.

### RESOLVED (2026-05-25) — all solden findings fixed (then tests/ui review next)
Broken live paths: slack_digest (runtime keys), trust_arc (arg order), scheduled_reports + workspace_shell (extract_spreadsheet_id as module fn), recon_skill (removed — vestigial; recon is AP-subordinate), correction_learning (_learned_rules), agent_reasoning (downgrade-only: can't force auto-approve). In-memory: email_tasks (lazy db proxy + guarded init); calendar_ooo/budget_awareness/conversational_agent were FALSE POSITIVES (verified). Strict-profile 404s: ap audit/export, ops/box-health, netsuite_panel actions (allowlisted; cap 455->460). DEAD removed: error_codes, pattern_store + models/patterns + shim, slack_notifications 2 orphan fns (match_engine was a FALSE POSITIVE — live registry, kept). Doc/brand/hygiene: gmail_api OAuth-payload logging removed, gmail_webhooks OAuth page rebranded navy/teal/Inter, vendor_enrichment actor_type, ap_item_detail/payment_confirmations/vendor_store/box_export/sap_z_field/cli_health docstrings. SKIPPED: workspace_semaphore clearledgr: Redis key (backend brand intentional). DEFERRED: org_config admin gate (dormant — router disabled in strict profile, 4 tests assert it disabled, unreachable in prod).
