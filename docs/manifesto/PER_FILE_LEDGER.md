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
- `main.py` — ALIGNED: FastAPI entrypoint; strict-profile surface enforcement (_apply_runtime_surface_profile + per-request guard) verified + allowlists corrected this session

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
- `tests/conftest.py` — MECHANICAL: pytest fixtures, DB setup, HTTP mocking, service cleanup
- `tests/factories.py` — MECHANICAL: test data builders (orgs, AP items, vendor profiles, users)
- `tests/test_accrual_journal_entry.py` — ALIGNED: accrual JE builder respects period dates, GL fallback, tenant isolation
- `tests/test_accrual_journal_entry_post.py` — ALIGNED: ERP post + reversal sweep + audit + duplicate-period block + tenant isolation
- `tests/test_action_idempotency.py` — ALIGNED: read-only actions + pre_post_validate safely repeatable
- `tests/test_adaptive_thresholds.py` — ALIGNED: per-vendor learned thresholds adapt + clamped to bounds
- `tests/test_admin_launch_controls.py` — ALIGNED: rollback controls, GA readiness evidence, connector config, learning calibration
- `tests/test_africa_einvoice.py` — ALIGNED: FIRS/eTIMS/SARS payload shapes + dispatcher routing
- `tests/test_africa_einvoice_submission.py` — ALIGNED: submitter resolution, pending ledger, provider_reference, audit, supersede
- `tests/test_agent_anomaly_detection.py` — ALIGNED: rule-based z-score gates; LLM augmentation fails safe
- `tests/test_agent_background.py` — DRIFT: claims per-org isolation in _check_overdue_tasks but outer try/except may bail on first org failure (VERIFY)
- `tests/test_agent_credit_pool.py` — ALIGNED: monthly grant idempotence, ledger arithmetic, enterprise unlimited bypass
- `tests/test_agent_end_to_end.py` — ALIGNED: planning engine action sequences + plan serialization round-trip
- `tests/test_agent_intents_router.py` — ALIGNED: intent preview structured error shape + handler registry delegation
- `tests/test_agent_memory_service.py` — ALIGNED: memory persists profile, events, beliefs, patterns + recall
- `tests/test_agent_reasoning.py` — ALIGNED: agent decision logic with persisted profile thresholds
- `tests/test_agent_retry_jobs.py` — ALIGNED: retry drain claims + completes jobs; runtime delegates to drain
- `tests/test_annotation_targets.py` — ALIGNED: registry, policy-kind integration, disabled targets, slice/merge round-trip
- `tests/test_ap_aggregation_api.py` — ALIGNED: ops endpoint multi-system metrics + auth enforced
- `tests/test_ap_aging_report.py` — ALIGNED: aging buckets, currency awareness, closed/no-due exclusion, API shape
- `tests/test_ap_audit_recent_api.py` — ALIGNED: auth, surface profile override, tenant isolation (read-only)
- `tests/test_ap_confidence.py` — ALIGNED: per-field severity tiers; blocks only critical failures
- `tests/test_ap_decision.py` — ALIGNED: deterministic cascade routes trusted->approve, fraud->escalate, model="rules"
- `tests/test_ap_decision_override_reasoning.py` — ALIGNED: human override reason captured in audit metadata
- `tests/test_ap_extraction_drift_metrics.py` — ALIGNED: ops drift metrics with time-series bucketing
- `tests/test_ap_intent_contracts.py` — ALIGNED: audit contract registry canonical names + eligibility copy
- `tests/test_ap_intent_handlers.py` — ALIGNED: handler registry covers all intents; skill delegates precheck/execute
- `tests/test_ap_item_detail.py` — ALIGNED: detail response shape, cross-tenant 404, action state-machine filtering
- `tests/test_ap_item_resolution.py` — ALIGNED: cross-tenant org-swap fix; foreign org rows rejected
- `tests/test_ap_items_merge_and_audit_guardrails.py` — ALIGNED: merge via metadata linkage, resubmit honors audit guardrails
- `tests/test_ap_multi_system_context.py` — ALIGNED: context builder links bank + spreadsheet sources
- `tests/test_ap_operator_audit.py` — ALIGNED: audit normalization maps codes to operator messages w/ severity/hint
- `tests/test_ap_policy_framework.py` — ALIGNED: policy versioning, auditability, thresholds, reminder/escalation
- `tests/test_ap_projection_contract.py` — ALIGNED: worklist projection contract, next_action, SLA breach detection
- `tests/test_ap_record_surfaces.py` — ALIGNED: strict profile enforcement + entity resolution for record surfaces
- `tests/test_ap_role_guards.py` — ALIGNED: ops mutation routes carry require_ops_user; read-only rejected
- `tests/test_ap_scenario_matrix.py` — ALIGNED: received->closed transitions + policy compliance (missing-PO escalation)
- `tests/test_ap_store_approval_followup.py` — ALIGNED: resolves pending approvers from metadata + approval chain
- `tests/test_ap_wedge_black_box.py` — ALIGNED: e2e request_approval intent drives workflow + audit trail
- `tests/test_api_endpoints.py` — ALIGNED: large endpoint surface; endpoint-exists/removed are legit regression guards, auth+HTTP mocking is necessary
- `tests/test_api_keys_admin.py` — ALIGNED: show-once contract, soft-delete, tenant isolation, rotation idempotency
- `tests/test_app_startup.py` — ALIGNED: deferred startup task scheduling + cancellation contract
- `tests/test_approval_delegation.py` — ALIGNED: delegation rule CRUD, date filtering, approver resolution
- `tests/test_approval_dispatch_outbox.py` — ALIGNED: dispatch state machine, idempotent re-entry, recovery paths
- `tests/test_approval_revert.py` — ALIGNED: approval window bounds, state-machine edges, tenant isolation
- `tests/test_approver_workload.py` — ALIGNED: pending-chain aggregation, oldest-pending age, cross-tenant isolation
- `tests/test_audit_chain_integrity.py` — ALIGNED: hash chain bootstrap, linkage, per-org isolation, recompute verify
- `tests/test_audit_chain_status_endpoint.py` — ALIGNED: chain health endpoint, tamper detection, per-tenant scope
- `tests/test_audit_entity_scope.py` — ALIGNED: entity-scoped audit filtering, role resolution, scope clause
- `tests/test_audit_governance_columns.py` — ALIGNED: tool_scope + policy_version threading on audit rows
- `tests/test_audit_policy_version.py` — ALIGNED: policy_version stamped on every audit event
- `tests/test_audit_trail_service.py` — ALIGNED: audit trail persistence + typed event stream retrieval
- `tests/test_auth_token_reconciliation.py` — ALIGNED: token reconciliation falls back to DB-canonical role (two-axis)
- `tests/test_authorization_denied.py` — ALIGNED: structured denial funnel, audit emission, never-raises-on-db-failure
- `tests/test_autonomy_config.py` — ALIGNED: org-level autonomy threshold merge, malformed-settings fallback
- `tests/test_bank_details_tokenisation.py` — ALIGNED: encryption round-trip, masking, backfill, no-plaintext-in-logs
- `tests/test_bank_match_box.py` — ALIGNED: bank_match Box type state machine, audit threading, export shape
- `tests/test_bank_reconciliation.py` — ALIGNED: CAMT.053/OFX parsing, auto-match, end-to-end reconciliation
- `tests/test_box_audit_reader.py` — ALIGNED: generic list_box_audit_events reader (3 VO tests skipped, dormant by design)
- `tests/test_box_cas.py` — ALIGNED: compare-and-swap happy/retry/exhausted + column whitelisting
- `tests/test_box_exceptions_admin_api.py` — ALIGNED: exceptions queue severity ordering, filtering, org scoping, resolve
- `tests/test_box_export_api.py` — ALIGNED: portable Box export sovereignty, completeness, tenant isolation (404 not 403)
- `tests/test_box_extraction.py` — ALIGNED: spec-driven LLM extraction for declarative Box types
- `tests/test_box_health.py` — ALIGNED: Box health drill-down, stuck threshold, terminal-state exclusion
- `tests/test_box_invariants.py` — DEAD: module-level skip (vendor_onboarding_deferred), never runs (dormant VO by design)
- `tests/test_box_lifecycle_store.py` — ALIGNED: exception round-trip, outcomes uniqueness, audit narration
- `tests/test_box_owner.py` — ALIGNED: ownership resolution, delegation override, reassign endpoint
- `tests/test_box_projection.py` — ALIGNED: read-side projector registry + rebuild contract
- `tests/test_bulk_batch_ops.py` — ALIGNED: bulk action batch cap, per-item result capture, pre-write Rule 1
- `tests/test_calendar_ooo.py` — ALIGNED: OOO checks fail-open, cache TTL, routing to backup
- `tests/test_channel_approval_contract.py` — ALIGNED: Slack/Teams callbacks verify install before AP lookup, stale/dup handling
- `tests/test_chart_of_accounts.py` — ALIGNED: ERP router caching, dispatcher, GL validation, per-connection retrieval
- `tests/test_compounding_learning_tenant_isolation.py` — ALIGNED: org A hints don't bleed to org B, Postgres persistence
- `tests/test_confidence_calibration.py` — ALIGNED: confidence reduction from historical corrections, per-vendor
- `tests/test_correction_learning.py` — ALIGNED: correction event persistence, vendor layout stats, reviewed export
- `tests/test_cross_invoice_analysis.py` — ALIGNED: dup/anomaly detection; high-conf dups block model relabeling
- `tests/test_cycle_time_metrics.py` — ALIGNED: touchless rate per stage, per-org isolation, transitions recorded
- `tests/test_decision_context_capture.py` — ALIGNED: decision context snapshot on every transition (auto + override)
- `tests/test_declarative_workflow.py` — ALIGNED: declarative box (contract_review) gets full runtime: CRUD, audit, engine, exception
- `tests/test_discount_optimizer.py` — ALIGNED: parse_discount_terms + annualized return with expired/active
- `tests/test_dispute_reopen.py` — ALIGNED: terminal-state gate, idempotent correction spawn, back-links, audit both boxes
- `tests/test_dispute_workflow.py` — ALIGNED: full lifecycle CRUD + open/escalate/resolve; cross-tenant isolation
- `tests/test_dual_approval.py` — ALIGNED: two-person gate, SOX self-approval block, distinct approvers, threshold CRUD
- `tests/test_e2e_ap_flow.py` — ALIGNED: received->closed with audit; exception/retry/rejection; cross-tenant isolation
- `tests/test_e2e_rollback_controls.py` — ALIGNED: erp_posting_disabled + per-connector + channel flags block side-effects
- `tests/test_email_parser_amount_selection.py` — ALIGNED: total-due precedence, PDF source-of-truth, OCR fallback, provenance
- `tests/test_email_parser_document_types.py` — ALIGNED: refund/credit_note/payment/statement classification
- `tests/test_endpoint_idempotency.py` — ALIGNED: Idempotency-Key dedup on bulk approve; replay returns cached
- `tests/test_engine_async_hygiene.py` — ALIGNED: pre/post-write async, asyncio.sleep, cancellation emits cancelled
- `tests/test_engine_box_lock.py` — ALIGNED: per-box advisory lock serializes engine; lock_held; no audit while held
- `tests/test_engine_idempotency.py` — ALIGNED: audit dedupe on correlation_id, erp_reference prevents re-post
- `tests/test_engine_resume_plan.py` — ALIGNED: CAS read+clear atomic, resumed plan inherits correlation_id
- `tests/test_erp_adapter_contracts.py` — ALIGNED: adapter validate/post/reconcile round-trips + posted lookup
- `tests/test_erp_api_first.py` — ALIGNED: API-first post records attempt + success/failed; rollback blocks
- `tests/test_erp_beta_fixes.py` — ALIGNED: GL map defaults/overrides, SAP preflight, token refresh retry, QB/Xero dedup
- `tests/test_erp_field_mapping_posters.py` — ALIGNED: workflow field resolution + dimension name mapping
- `tests/test_erp_follow_on.py` — ALIGNED: finance_effect_review blockers, non-invoice dispatch, connector routing, macros
- `tests/test_erp_journal_entry_capture.py` — ALIGNED: per-ERP journal entry id capture
- `tests/test_erp_native_intake_pipeline.py` — ALIGNED: synthetic gmail_id, observer short-circuit, posting skip for ERP-native
- `tests/test_erp_netsuite_e2e.py` — ALIGNED: mocked e2e preflight + bill post + 202 polling + multi-subsidiary
- `tests/test_erp_oauth.py` — ALIGNED: OAuth callback validation (error/code/state/realm raises 400/403)
- `tests/test_erp_payment_dispatcher.py` — ALIGNED: QB envelope, Xero filter, NS sync parser, orphan audit
- `tests/test_erp_po_write.py` — ALIGNED: flag-disabled skipped, already-issued idempotent, QB/Xero reference adapters
- `tests/test_erp_preflight.py` — ALIGNED: bill lookup + vendor existence + GL validation per ERP
- `tests/test_erp_readiness.py` — ALIGNED: connector readiness eval; rollback-disabled blocks readiness
- `tests/test_erp_reversal.py` — ALIGNED: reverse_bill per-connector, already-reversed/payment-applied idempotency
- `tests/test_erp_router_query_safety.py` — ALIGNED: vendor name queries sanitized; SQL injection prevented
- `tests/test_erp_sap_s4hana_write_surface.py` — ALIGNED: B1 vs S/4HANA dispatch, per-line tax code, composite-key return
- `tests/test_erp_vendor_list.py` — ALIGNED: paginated vendor list per ERP, cache TTL + force_refresh
- `tests/test_erp_webhook_security.py` — ALIGNED: per-ERP signature verify, replay-window reject, fail-closed unconfigured
- `tests/test_escalation_policies.py` — ALIGNED: threshold_hours gate, idempotency UNIQUE, cross-org, SMTP skip non-fatal
- `tests/test_exception_graph.py` — ALIGNED: graph nodes/edges, cause clustering, weight decay
- `tests/test_exception_resolver.py` — ALIGNED: dispatch to per-exception strategies; resolve returns reason + code
- `tests/test_execution_engine.py` — ALIGNED: handler registry, LLM boundary fence, concurrency, failure classification, SLA
- `tests/test_extraction_guardrails.py` — ALIGNED: three deterministic guardrails + fail-open behavior
- `tests/test_extraction_provenance_coverage.py` — ALIGNED: every extraction producer emits field_provenance to audit chain
- `tests/test_finance_agent_governance.py` — ALIGNED: blocks forbidden actions, doctrine checks, gate enforcement at waist
- `tests/test_finance_agent_runtime.py` — ALIGNED: skill-contract mechanics; bounded-agent invariant covered in test_ap_decision/test_execution_engine/test_gate_constraint_enforcement
- `tests/test_finance_contracts.py` — MECHANICAL: contract dataclass serialization, no behavioral invariant
- `tests/test_finance_email_store.py` — ALIGNED: email upsert refreshes extracted fields + metadata
- `tests/test_finance_learning_service.py` — ALIGNED: runtime outcome calibration + shadow decision match rate
- `tests/test_fraud_controls_gate.py` — ALIGNED: FX fail-closed, gate contributions, severity filter, e2e forces escalate
- `tests/test_fx_conversion.py` — ALIGNED: FX conversion (same/ECB/unknown/fallback) + supported list
- `tests/test_gate_constraint_enforcement.py` — ALIGNED: enforce_gate_constraint matrix, defensive backstop, waist re-enforcement
- `tests/test_gdpr_retention.py` — ALIGNED: anonymize PII, expired identify, purge, DSR CRUD, tenant isolation, idempotency
- `tests/test_generic_engine_bank_match.py` — ALIGNED: bank_match runs through same CoordinationEngine primitives
- `tests/test_generic_engine_purchase_order.py` — ALIGNED: PO peer Box runs through generic engine, zero AP-specific code
- `tests/test_gl_correction_wiring.py` — ALIGNED: DB-backed persistence, history in suggestion payload, org-scoped analytics
- `tests/test_gmail_activities.py` — ALIGNED: Slack escalation threads, normalizes confidence, fallback
- `tests/test_gmail_autopilot.py` — ALIGNED: _tick isolates user failures, max concurrency, catchup + background loop
- `tests/test_gmail_classification.py` — ALIGNED: invoice classification + parser false-positive avoidance
- `tests/test_gmail_label_sync.py` — ALIGNED: intent_for_label maps action verbs only, guarded set, dispatch
- `tests/test_gmail_labels.py` — ALIGNED: three-level label hierarchy, backward-compat, AP_STATE_TO_LABEL, cleanup migration
- `tests/test_gmail_labels_bidirectional.py` — ALIGNED: label event enqueue idempotency, resolved id->intent, box lookup
- `tests/test_gmail_oauth_error_surfacing.py` — ALIGNED: exchange surfaces Google's actual error w/ description
- `tests/test_gmail_webhooks.py` — ALIGNED: Pub/Sub validation, OAuth state signing, user/org resolution, push verifier
- `tests/test_governance_event_path.py` — ALIGNED: engine runs doctrine + autonomy gate before financial writes; truthful gate status
- `tests/test_historical_replay.py` — ALIGNED: _values_match type/case normalization + no-corrections clean path (9 asserts)
- `tests/test_iban_change_freeze.py` — ALIGNED: three-factor IBAN verify, freeze accessors, blocks frozen vendor, audit
- `tests/test_iban_validation.py` — ALIGNED: mod-97 checksum, country/length, typo rejection, normalization
- `tests/test_intake_audit_coverage.py` — ALIGNED: seed/merge/exception_cleared emit audit rows before write
- `tests/test_invoice_archive.py` — ALIGNED: PDF round-trip, content-addressing dedup, append-only, retention, audit
- `tests/test_invoice_extraction_eval_harness.py` — ALIGNED: eval harness returns metrics (weighted_score, field_accuracy)
- `tests/test_invoice_extraction_golden.py` — ALIGNED: golden thresholds (94% overall, 99% critical) + vendor pack gates
- `tests/test_invoice_workflow_controls.py` — ALIGNED: PO-required forces manual, match exception blocks auto-approve
- `tests/test_invoice_workflow_runtime_state_transitions.py` — ALIGNED: service methods drive real AP transitions + audit
- `tests/test_journal_entry_preview.py` — ALIGNED: JE preview by treatment, balance invariant, GL override, cross-org
- `tests/test_learning_calibration.py` — ALIGNED: recompute snapshot from feedback, latest roundtrip, no-feedback case
- `tests/test_learning_service_persistence.py` — ALIGNED: suggest persists across instances (Postgres), org-scoped
- `tests/test_llm_budget_cap.py` — ALIGNED: hard-cap guard pauses org, paused fast-fails, new month clears, role-gated
- `tests/test_llm_call_box_link.py` — ALIGNED: llm_call_log persists box_id/correlation_id; audit join reconstructs trail
- `tests/test_llm_cost_summary.py` — ALIGNED: per-tenant LLM cost aggregation + windowing
- `tests/test_llm_email_parser.py` — ALIGNED: LLM result mapping, attachment evidence merge, authoritative path
- `tests/test_llm_gateway.py` — ALIGNED: action registry, token budgets, system prompt, boundary on unregistered
- `tests/test_llm_no_gateway_bypass.py` — ALIGNED: source scan for raw provider HTTP calls bypassing gateway
- `tests/test_mandatory_gl_gate.py` — ALIGNED: GL requirement at posting, per-tenant disable, alias field
- `tests/test_match_config_api.py` — ALIGNED: match-mode/tolerance persistence, version advance, role gate, org isolation
- `tests/test_match_engine.py` — ALIGNED: engine registry, scoring, decide() outcomes, tolerance, persistence, override
- `tests/test_match_mode_dispatch.py` — ALIGNED: three_way/two_way/policy_only dispatcher + state persistence
- `tests/test_metrics_persistence.py` — ALIGNED: metrics durability, retention pruning, fire-and-forget drain
- `tests/test_migration_v42.py` — DEAD: module-level skip (vendor_onboarding_deferred) + SQLite-only backfill (dormant VO)
- `tests/test_modules_5_6_carry_overs.py` — ALIGNED: ERP test-connection, credential rotation, SAML SLO, entity invite
- `tests/test_money_decimal.py` — ALIGNED: penny-exactness, money_sum exact, Pydantic JSON roundtrip, quantization
- `tests/test_monitoring.py` — ALIGNED: health checks (dead letters, auth, stale autopilot, overdue, posting), thresholds
- `tests/test_multi_entity.py` — ALIGNED: entity CRUD, AP/ERP scoping, routing, zero-entity backward compat
- `tests/test_multi_invoice_intake.py` — ALIGNED: split_email_attachments + e2e multi-invoice fanout w/ disambiguated IDs
- `tests/test_multi_invoice_splitter.py` — ALIGNED: attachment boundary detection + invoice splitting
- `tests/test_multi_tenant_isolation.py` — ALIGNED: read/write isolation, concurrent org creation zero bleed, soft_org_guard
- `tests/test_needs_info_recovery.py` — ALIGNED: recovery plan gen, action whitelist, 3-step cap, graceful gateway failure
- `tests/test_netsuite_panel_audit_integration.py` — ALIGNED: NS panel dispatch routes erp_native_netsuite through audit
- `tests/test_no_currency_leaks.py` — ALIGNED: frontend scan for forbidden USD/dollar patterns
- `tests/test_no_legacy_orchestrator_runtime_calls.py` — ALIGNED: legacy agent_orchestrator removed, callsites scrubbed
- `tests/test_notification_preferences.py` — ALIGNED: schema, GET defaults, PATCH merge, unknown scrub, should_notify gate
- `tests/test_onboarding_gates.py` — ALIGNED: AP policy completeness + ERP error classification structured payloads
- `tests/test_onboarding_token_single_use.py` — DEAD: module-level skip (vendor_onboarding_deferred), dormant VO
- `tests/test_org_config_roundtrip.py` — ALIGNED: save/get round-trip no value loss, from_dict inverts to_dict, drift-tolerant
- `tests/test_org_purge.py` — ALIGNED: purge scopes to target org, never touches audit_events, safe on empty
- `tests/test_org_utils.py` — ALIGNED: assert_org_id, require_org, coerce_org_id edge cases
- `tests/test_outbox.py` — ALIGNED: outbox serialization, handler registry, enqueue/claim/succeed/retry/dead-letter
- `tests/test_outgoing_webhooks.py` — ALIGNED: subscription CRUD, HMAC, delivery, event emission, state-change hook
- `tests/test_outlook_integration.py` — MECHANICAL: token expiry + token store CRUD plumbing, no invariant
- `tests/test_override_window.py` — ALIGNED: state machine, window CRUD, open/expiry/reversal, Slack builder, REST
- `tests/test_override_window_durability.py` — ALIGNED: expired window reaped without plan action, idempotent, concurrent-safe
- `tests/test_payment_confirmations.py` — ALIGNED: store CRUD, list scoping, idempotent redelivery, state walk, audit
- `tests/test_payment_confirmations_api.py` — ALIGNED: POST drives state walk, idempotency, invalid status reject, isolation
- `tests/test_payment_request_persistence.py` — ALIGNED: requests survive restart, approve/reject/mark_paid persist + audit
- `tests/test_payment_state_machine.py` — ALIGNED: four payment states, valid transitions, normalize, legacy map intact
- `tests/test_payment_status_polling.py` — ALIGNED: ERP payment status polling + state derivation
- `tests/test_payment_tracking.py` — MECHANICAL: PaymentRecord serialization + PaymentStore CRUD plumbing
- `tests/test_peppol_inbound.py` — ALIGNED: VAT treatment derivation + cross-field consistency
- `tests/test_peppol_outbound.py` — ALIGNED: round-trip UBL generation + parser idempotency
- `tests/test_period_close.py` — ALIGNED: period lock/unlock state, accrual detection, posting gates
- `tests/test_pipeline_hardening.py` — ALIGNED: callback retry, post-posting verification, attachment forwarding
- `tests/test_plan_acceptance.py` — ALIGNED: state-machine transitions, rejection metadata, cross-tenant isolation
- `tests/test_planning_engine.py` — ALIGNED: deterministic plan generation + LLM boundary enforcement
- `tests/test_planning_engine_vo_deprecation.py` — ALIGNED: gates deprecated VO event types, records operator queue exceptions
- `tests/test_policy_branches.py` — ALIGNED: branch create/commit/diff/merge lifecycle + version isolation
- `tests/test_policy_linter.py` — ALIGNED: approval threshold structure + config hazard detection
- `tests/test_policy_service.py` — ALIGNED: hash stability, slice/merge per kind, replay strategies
- `tests/test_portal_input_validation.py` — ALIGNED: guards injection on unauthenticated vendor portal fields
- `tests/test_proactive_insights_narration.py` — ALIGNED: narration fallbacks preserve rule output + insight IDs
- `tests/test_procurement_chat.py` — ALIGNED: card building + decision dispatch through AP workflow
- `tests/test_procurement_skill.py` — ALIGNED: tiered approval autonomy gates + PO lifecycle transitions
- `tests/test_prompt_guard.py` — ALIGNED: detects/blocks injection at deterministic validation gate
- `tests/test_purchase_order_routes.py` — ALIGNED: PO lifecycle endpoints + illegal-transition rejection
- `tests/test_quickbooks_xero_intake.py` — ALIGNED: envelope parsing, signature verify, state-update derivation
- `tests/test_rate_limit.py` — ALIGNED: rate-limit memory backend + middleware integration
- `tests/test_recalibrate_confidence_gate.py` — ALIGNED: idempotent re-eval of old flat-gate records under tiered gates
- `tests/test_reclassification_je.py` — ALIGNED: JE proposal shape, idempotency keys, cross-org isolation
- `tests/test_report_export.py` — ALIGNED: aging/spend/posting-status report gen + CSV serialization
- `tests/test_report_subscriptions.py` — ALIGNED: cadence math, delivery tracking, auto-pause on repeated failures
- `tests/test_request_latency_fixes.py` — ALIGNED: non-blocking record_* paths + token-validation short-circuit
- `tests/test_role_taxonomy.py` — ALIGNED: workspace/AP role rank ordering + legacy-predicate delegation
- `tests/test_route_auth_policy_inventory.py` — ALIGNED: guards sensitive prefixes against unauthenticated regressions
- `tests/test_runtime_surface_scope.py` — ALIGNED: strict-profile blocking of legacy surfaces + contract invariants
- `tests/test_runtime_tenant_isolation.py` — ALIGNED: rejects empty org_id on runtime init, platform-mode semantics
- `tests/test_runtime_triage_group8.py` — ALIGNED: failure classification, exception cascades, DB handle staleness
- `tests/test_saml_sso.py` — ALIGNED: e2e SAML parse w/ signature verify, replay, audience-restriction gates
- `tests/test_sample_data.py` — ALIGNED: idempotent load/clear + contamination guarantee at SQL layer
- `tests/test_sanctions_screening.py` — ALIGNED: vendor screening state transitions + payment-gate enforcement
- `tests/test_sap_adapter_fail_closed.py` — ALIGNED: non-live park fails closed when flag off
- `tests/test_sap_b1_poll_celery_task.py` — ALIGNED: beat schedule registration + per-org isolation in polling
- `tests/test_sap_fiori_audit_integration.py` — ALIGNED: e2e audit chain from SAP extension to decision_context
- `tests/test_sap_s4hana_payment_path.py` — ALIGNED: connection-shape heuristic + S/4HANA payment polling
- `tests/test_scheduled_reports.py` — ALIGNED: schedule retrieval, due-checking, cadence logic
- `tests/test_secrets.py` — ALIGNED: deterministic dev generation, caching, env-var precedence
- `tests/test_services_tenant_isolation.py` — ALIGNED: rejects empty org_id on services layer, platform-mode defaults
- `tests/test_single_pass_cache.py` — ALIGNED: caching determinism + miss/hit behavior
- `tests/test_single_pass_processor.py` — ALIGNED: prompt structure, response parsing, schema validation gate
- `tests/test_slack_notifications.py` — ALIGNED: delivery retry queue + per-org channel routing
- `tests/test_sod_enforcement.py` — ALIGNED: SoD validation, mode resolver, violation detection
- `tests/test_specialist_agent.py` — ALIGNED: specialist wrapper isolation + router dispatch contract
- `tests/test_specialist_circuit_breaker.py` — ALIGNED: circuit state machine + per-specialist quarantine
- `tests/test_spend_analysis.py` — ALIGNED: spend aggregation, vendor metrics, GL categorization
- `tests/test_state_audit_atomicity.py` — ALIGNED: state+audit commit atomicity, torn-write prevention
- `tests/test_state_mutation_discipline.py` — MECHANICAL: regex fence detecting raw state mutations outside update_ap_item
- `tests/test_state_observers.py` — ALIGNED: observer dispatch isolation + audit/vendor/notification side effects
- `tests/test_subscription_quota_enforcement.py` — ALIGNED: tier-based saved-view caps + retention filtering
- `tests/test_subscription_service.py` — ALIGNED: UsageStats legacy field mapping
- `tests/test_subscription_tier_features.py` — ALIGNED: tier-comparison features + annual discount arithmetic
- `tests/test_synthetic_invoice_suite.py` — ALIGNED: fixture integrity + extraction/validation against synthetic invoices
- `tests/test_task_scheduler_tenant.py` — ALIGNED: org-scoped task notification routing + overdue isolation
- `tests/test_tax_compliance.py` — ALIGNED: VAT ID formats, reverse charge, tax return calculations
- `tests/test_team_invite_email.py` — ALIGNED: email composition + delivery state translation
- `tests/test_team_invite_role_normalisation.py` — ALIGNED: thesis-role acceptance + normalisation
- `tests/test_team_offboarding.py` — ALIGNED: deactivation, reactivation, auth layer enforcement
- `tests/test_teams_audit_integration.py` — ALIGNED: Teams dispatch lands ui_surface in decision_context audit
- `tests/test_teams_installations.py` — ALIGNED: AAD tenant installation mapping + fail-closed contract
- `tests/test_teams_verify.py` — ALIGNED: JWT verification, JWKS caching, error mapping
- `tests/test_tenant_isolation.py` — ALIGNED: query-param spoofing + resource-level org mismatch guards
- `tests/test_three_way_match.py` — ALIGNED: match logic, line-item breakdown, idempotent audit emission
- `tests/test_threshold_policy.py` — ALIGNED: resolution layering, persistence, vendor override CRUD
- `tests/test_trust_arc.py` — ALIGNED: trust-arc activation phases + tier expansion lifecycle
- `tests/test_user_offboarding.py` — ALIGNED: invite entity restrictions + offboarding cascade
- `tests/test_v1_auth.py` — ALIGNED: legacy scopes -> new vocab mapping + revocation/expiry
- `tests/test_v1_boundary_flags.py` — ALIGNED: feature flags gate routes + strict-profile allowlist
- `tests/test_v1_core_completion.py` — ALIGNED: extension pipeline normalization + Teams interactive contract
- `tests/test_v1_idempotency.py` — ALIGNED: hash determinism + idempotency key extraction
- `tests/test_v1_integration.py` — ALIGNED: e2e agent flow w/ auth, rate limit, audit attribution
- `tests/test_v1_rate_limit.py` — ALIGNED: per-key + per-org rate limit counters
- `tests/test_v1_records.py` — ALIGNED: cursor encoding + public-field allowlist
- `tests/test_v1_webhooks.py` — ALIGNED: secret generation, redaction, event-name allowlist
- `tests/test_validate_launch_evidence.py` — ALIGNED: launch tracker parsing + readiness validation
- `tests/test_validation_per_rule_audit.py` — ALIGNED: per-rule validation audit trail records every rule
- `tests/test_vat_modeling.py` — ALIGNED: VAT calculation treatments + return box rollup
- `tests/test_vat_return_forms.py` — ALIGNED: VAT form mapping preserves invariants per jurisdiction + API isolation
- `tests/test_vendor_activation_sla.py` — DEAD: module-level skip (vendor_onboarding_deferred), dormant VO
- `tests/test_vendor_activation_slack.py` — DEAD: module-level skip (vendor_onboarding_deferred), dormant VO
- `tests/test_vendor_attribute_matcher.py` — ALIGNED: vendor profile matching + cross-org isolation
- `tests/test_vendor_csv_import.py` — DEAD: module-level skip (vendor_onboarding_deferred), dormant VO
- `tests/test_vendor_dedup.py` — ALIGNED: dedup detection, merge consolidation, alias management
- `tests/test_vendor_domain_lock.py` — ALIGNED: sender domain validation gates, processor bypass, mismatch block
- `tests/test_vendor_domain_lookalike.py` — ALIGNED: homoglyph + edit-distance + TLD-swap impersonation detection
- `tests/test_vendor_erp_push.py` — DEAD: module-level skip (vendor_onboarding_deferred), dormant VO
- `tests/test_vendor_erp_sync.py` — ALIGNED: vendor master sync detects new/deactivated/reactivated
- `tests/test_vendor_inquiry.py` — ALIGNED: tenant isolation + status mapping on vendor invoice lookup (read-only)
- `tests/test_vendor_issue_payloads.py` — MECHANICAL: shapes vendor detail + summary payloads, no invariant
- `tests/test_vendor_kyc.py` — ALIGNED: KYC schema, risk scoring, iban_verified derivation
- `tests/test_vendor_master_check.py` — ALIGNED: ERP master lookup gates AP intake + fuzzy fallback
- `tests/test_vendor_onboarding_exceptions.py` — DEAD: module-level skip (vendor_onboarding_deferred), dormant VO
- `tests/test_vendor_onboarding_lifecycle.py` — DEAD: module-level skip (vendor_onboarding_deferred), dormant VO
- `tests/test_vendor_onboarding_state_machine.py` — DEAD: module-level skip (vendor_onboarding_deferred), dormant VO
- `tests/test_vendor_portal.py` — DEAD: module-level skip (vendor_onboarding_deferred), dormant VO
- `tests/test_vendor_revalidation.py` — ALIGNED: in-flight AP items flagged when vendor status changes
- `tests/test_vendor_risk_payload.py` — ALIGNED: risk score components in list + detail payloads
- `tests/test_vendor_search.py` — ALIGNED: fuzzy matching primitives + hybrid scoring
- `tests/test_vendor_statement_recon.py` — ALIGNED: reference + amount/date matching in reconciliation
- `tests/test_vendor_status.py` — ALIGNED: vendor status gate on AP validation + audit emission
- `tests/test_webhook_auth_hardening.py` — ALIGNED: source-inspection regression fence on auth landmines
- `tests/test_workflow_hooks.py` — ALIGNED: expression conditions, WASM sandbox isolation, effect dispatch
- `tests/test_workflow_isolation.py` — ALIGNED: tenant isolation on workflow specs + declarative boxes
- `tests/test_workflow_specs.py` — ALIGNED: versioned spec authoring + version pinning for in-flight boxes
- `tests/test_workspace_audit_export.py` — ALIGNED: async CSV export job lifecycle + cross-tenant gating
- `tests/test_workspace_audit_search.py` — ALIGNED: audit search filtering, pagination, tenant isolation
- `tests/test_workspace_audit_webhook_fanout.py` — ALIGNED: webhook dispatch enqueue + delivery log record
- `tests/test_workspace_connection_health.py` — ALIGNED: connection health from audit events + webhook counters
- `tests/test_workspace_custom_roles.py` — ALIGNED: custom role CRUD + permission resolver precedence
- `tests/test_workspace_entity_roles.py` — ALIGNED: entity role override + approval ceiling enforcement
- `tests/test_workspace_erp_field_mappings.py` — ALIGNED: field mapping CRUD w/ validation + audit on diff
- `tests/test_workspace_fx.py` — ALIGNED: FX rate store, conversion paths, cross-currency rollup
- `tests/test_workspace_org_settings.py` — ALIGNED: org rename + domain change with audit emission
- `tests/test_workspace_reports.py` — ALIGNED: five fixed reports w/ time bucketing + cross-tenant isolation
- `tests/test_workspace_rules.py` — ALIGNED: rule engine validation, conflict detection, wiring into decision service

## tests/erp_dom_regression  (1)
- `tests/erp_dom_regression/profiles.py` — MECHANICAL: ERP form fixture registry (QB/Xero/NS/SAP DOM profiles)

## ui/gmail-extension  (6)
- `ui/gmail-extension/background.js` — ALIGNED: service worker, retry + route handling (internal clearledgr storage keys, pre-rebrand)
- `ui/gmail-extension/config.js` — ALIGNED: BACKEND_URL/WORKSPACE_URL point to soldenai.com
- `ui/gmail-extension/content-script.js` — DRIFT: host-pin guard checks stale api.clearledgr.com vs config api.soldenai.com (self-heal never fires)
- `ui/gmail-extension/queue-manager.js` — DRIFT: host-pin guard checks stale api.clearledgr.com vs config api.soldenai.com
- `ui/gmail-extension/route-capture.js` — MECHANICAL: sessionStorage capture for pending routes
- `ui/gmail-extension/vitest.config.js` — MECHANICAL: test config, Preact JSX factory

## ui/gmail-extension/build  (6)
- `ui/gmail-extension/build/background.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)
- `ui/gmail-extension/build/config 2.js` — DEAD: macOS duplicate-copy cruft (" 2.js"), should be removed/gitignored
- `ui/gmail-extension/build/config.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)
- `ui/gmail-extension/build/content-script.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)
- `ui/gmail-extension/build/queue-manager.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)
- `ui/gmail-extension/build/route-capture.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)

## ui/gmail-extension/build/clients  (14)
- `ui/gmail-extension/build/clients/BaseClient 2.js` — DEAD: macOS duplicate-copy cruft (" 2.js"), should be removed/gitignored
- `ui/gmail-extension/build/clients/BaseClient.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)
- `ui/gmail-extension/build/clients/CategorizationClient 2.js` — DEAD: macOS duplicate-copy cruft (" 2.js"), should be removed/gitignored
- `ui/gmail-extension/build/clients/CategorizationClient.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)
- `ui/gmail-extension/build/clients/ClassificationClient 2.js` — DEAD: macOS duplicate-copy cruft (" 2.js"), should be removed/gitignored
- `ui/gmail-extension/build/clients/ClassificationClient.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)
- `ui/gmail-extension/build/clients/ExceptionClient 2.js` — DEAD: macOS duplicate-copy cruft (" 2.js"), should be removed/gitignored
- `ui/gmail-extension/build/clients/ExceptionClient.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)
- `ui/gmail-extension/build/clients/ExtractionClient 2.js` — DEAD: macOS duplicate-copy cruft (" 2.js"), should be removed/gitignored
- `ui/gmail-extension/build/clients/ExtractionClient.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)
- `ui/gmail-extension/build/clients/MatchingClient 2.js` — DEAD: macOS duplicate-copy cruft (" 2.js"), should be removed/gitignored
- `ui/gmail-extension/build/clients/MatchingClient.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)
- `ui/gmail-extension/build/clients/emailParsing 2.js` — DEAD: macOS duplicate-copy cruft (" 2.js"), should be removed/gitignored
- `ui/gmail-extension/build/clients/emailParsing.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)

## ui/gmail-extension/build/engines  (2)
- `ui/gmail-extension/build/engines/DiscoveryEngine 2.js` — DEAD: macOS duplicate-copy cruft (" 2.js"), should be removed/gitignored
- `ui/gmail-extension/build/engines/DiscoveryEngine.js` — MECHANICAL: checked-in build artifact (generated from src/, candidate for .gitignore)

## ui/gmail-extension/build/workflows  (2)
- `ui/gmail-extension/build/workflows/registry 2.js` — DEAD: macOS duplicate-copy cruft (" 2.js"), should be removed/gitignored
- `ui/gmail-extension/build/workflows/registry.js` — MECHANICAL: built workflow registry stub

## ui/gmail-extension/clients  (7)
- `ui/gmail-extension/clients/BaseClient.js` — MECHANICAL: abstract base client
- `ui/gmail-extension/clients/CategorizationClient.js` — ALIGNED: GL categorization heuristics, sensible fallback codes
- `ui/gmail-extension/clients/ClassificationClient.js` — ALIGNED: email type classification, no dead endpoints
- `ui/gmail-extension/clients/ExceptionClient.js` — ALIGNED: routes exceptions via ensureExceptionTask, no contract drift
- `ui/gmail-extension/clients/ExtractionClient.js` — DRIFT: en-US locale fallback in formatAmount (EU product)
- `ui/gmail-extension/clients/MatchingClient.js` — ALIGNED: matchInvoiceViaAPI + getVendorInsightsViaAPI, contract matches
- `ui/gmail-extension/clients/emailParsing.js` — DRIFT: inconsistent EUR vs USD currency handling

## ui/gmail-extension/engines  (1)
- `ui/gmail-extension/engines/DiscoveryEngine.js` — ALIGNED: transaction classification + extraction, real endpoints

## ui/gmail-extension/src/components  (9)
- `ui/gmail-extension/src/components/ActionDialog.js` — MECHANICAL: reason-sheet modal UI
- `ui/gmail-extension/src/components/ActionDialog.test.js` — MECHANICAL: test
- `ui/gmail-extension/src/components/BudgetPausedBanner.js` — DRIFT: "Claude" vendor name in comment + hardcoded USD cost display
- `ui/gmail-extension/src/components/InviteVendorModal.js` — DEAD: posts /api/vendors/{name}/onboarding/invite (vendor onboarding parked dormant)
- `ui/gmail-extension/src/components/OnboardingFlow.js` — ALIGNED: onboarding steps wired to bootstrap/integrations/policies, £ example
- `ui/gmail-extension/src/components/SidebarApp.js` — ALIGNED: AP-first sidebar, real /api/ap/items + approval routes
- `ui/gmail-extension/src/components/SidebarApp.test.js` — MECHANICAL: test
- `ui/gmail-extension/src/components/ThreadSidebar.js` — ALIGNED: fixed sections (Invoice/Match/Vendor/Actions), real contract endpoints
- `ui/gmail-extension/src/components/ThreadSidebar.test.js` — MECHANICAL: test

## ui/gmail-extension/src  (4)
- `ui/gmail-extension/src/inboxsdk-layer.js` — DRIFT: legacy clearledgr/* route-ids + __clearledgr_* storage keys (internal, pre-rebrand)
- `ui/gmail-extension/src/settings-tab.js` — DRIFT: legacy clearledgr-settings-tab id + clearledgr_onboarding_dismissed key (internal)
- `ui/gmail-extension/src/styles.js` — DRIFT: legacy mint #00D67E accent + navy #0A1628 + stale "mint green" DESIGN.md comment (extension not rebrand-swept)
- `ui/gmail-extension/src/thesis-compliance.test.js` — ALIGNED: enforces Gmail thread-sidebar no-approve-action doctrine

## ui/gmail-extension/src/routes  (3)
- `ui/gmail-extension/src/routes/oauth-bridge.js` — MECHANICAL: OAuth popup coordinator, postMessage only
- `ui/gmail-extension/src/routes/route-helpers.js` — ALIGNED: role capability mapping + state labels
- `ui/gmail-extension/src/routes/workspace-shell-api.js` — ALIGNED: /api/workspace/* adapter, bearer auth, handles 401/403

## ui/gmail-extension/src/test-utils  (1)
- `ui/gmail-extension/src/test-utils/happy-dom-env.js` — MECHANICAL: test harness utility

## ui/gmail-extension/src/utils  (13)
- `ui/gmail-extension/src/utils/capabilities.js` — ALIGNED: role-based capability checks, safe fallbacks
- `ui/gmail-extension/src/utils/document-types.js` — ALIGNED: doc-type aliases + labels + guidance
- `ui/gmail-extension/src/utils/formatters.js` — DRIFT: legacy mint #00D67E state colors (not teal/success token)
- `ui/gmail-extension/src/utils/formatters.test.js` — ALIGNED: formatAmount tests cover EUR/GBP without USD bias
- `ui/gmail-extension/src/utils/inbox-route.js` — DRIFT: legacy clearledgr/ route IDs (internal navigation, pre-rebrand)
- `ui/gmail-extension/src/utils/perf-budget.js` — MECHANICAL: perf SLA tracking
- `ui/gmail-extension/src/utils/record-route.js` — DRIFT: legacy clearledgr/invoice/:id route id (internal, pre-rebrand)
- `ui/gmail-extension/src/utils/roles.js` — MECHANICAL: role normalization helpers
- `ui/gmail-extension/src/utils/store.js` — MECHANICAL: reactive state store
- `ui/gmail-extension/src/utils/store.test.js` — MECHANICAL: store unit tests
- `ui/gmail-extension/src/utils/vendor-route.js` — DRIFT: legacy clearledgr/vendor/:name route id (internal, pre-rebrand)
- `ui/gmail-extension/src/utils/work-actions.js` — ALIGNED: doc-type-aware workflow state logic
- `ui/gmail-extension/src/utils/workspace-link.js` — ALIGNED: workspace deep-links to soldenai.com

## ui/gmail-extension/utils  (2)
- `ui/gmail-extension/utils/ap_classifier.js` — ALIGNED: email classification heuristics, currency-agnostic
- `ui/gmail-extension/utils/retry.js` — MECHANICAL: exponential backoff retry utility

## ui/gmail-extension/workflows  (1)
- `ui/gmail-extension/workflows/registry.js` — ALIGNED: workflow registry, correct workflow IDs

## ui/outlook-addin/src  (1)
- `ui/outlook-addin/src/outlook-entry.js` — ALIGNED: Office.js entry, backend auth, shared Preact sidebar, action handlers

## ui/shared  (3)
- `ui/shared/hooks.js` — MECHANICAL: shared Preact hooks (ErrorBoundary etc.)
- `ui/shared/intent-labels.js` — ALIGNED: intent label map matches backend available_intents contract
- `ui/shared/tokens.js` — MECHANICAL: shared design tokens (STATE_LABELS/CSS classes, no hardcoded hex)

## ui/web-app  (2)
- `ui/web-app/server.js` — MECHANICAL: Express static proxy, no user-facing surface
- `ui/web-app/vite.config.js` — MECHANICAL: build config, API proxy

## ui/web-app/src  (2)
- `ui/web-app/src/App.js` — ALIGNED: route IA (Primary/WORKFLOWS/DATA/ADMIN) wired to real endpoints
- `ui/web-app/src/main.js` — MECHANICAL: Preact render entry + style imports

## ui/web-app/src/api  (1)
- `ui/web-app/src/api/client.js` — ALIGNED: CSRF handling, real /auth + /api base, retries on 502/503/504

## ui/web-app/src/auth  (6)
- `ui/web-app/src/auth/AuthGate.js` — ALIGNED: gate wired to useSession, redirects to /login when unauthenticated
- `ui/web-app/src/auth/InviteAcceptPage.js` — ALIGNED: /auth/invites/preview + accept, plain copy
- `ui/web-app/src/auth/LegalPages.js` — DRIFT: "Railway-managed infrastructure" names infra vendor in user-facing legal copy
- `ui/web-app/src/auth/LoginPage.js` — ALIGNED: Google/Microsoft OAuth start + password login, real endpoints
- `ui/web-app/src/auth/OAuthIcons.js` — MECHANICAL: official Google/Microsoft SVG marks
- `ui/web-app/src/auth/useSession.js` — ALIGNED: session cache, /auth/me probe, 401 stale-session event

## ui/web-app/src/components  (2)
- `ui/web-app/src/components/AgentActivityRibbon.js` — ALIGNED: /api/workspace/dashboard/recent-activity, on-brand empty state
- `ui/web-app/src/components/StatePrimitives.js` — ALIGNED: reusable empty/loading/error primitives

## ui/web-app/src/lib  (2)
- `ui/web-app/src/lib/faviconBadge.js` — ALIGNED: favicon badge logic, red badge justified
- `ui/web-app/src/lib/faviconBadge.test.js` — MECHANICAL: unit tests for badge formatting

## ui/web-app/src/pages  (1)
- `ui/web-app/src/pages/PlaceholderPage.js` — ALIGNED: stub for in-progress pages, accurate copy

## ui/web-app/src/routes/pages  (37)
- `ui/web-app/src/routes/pages/ActivityPage.js` — ALIGNED: recent-activity + SSE stream, on-brand copy
- `ui/web-app/src/routes/pages/ActivityRoute.js` — MECHANICAL: thin route wrapper
- `ui/web-app/src/routes/pages/ApiKeysPage.js` — ALIGNED: /api/workspace/api-keys + scopes catalog, real
- `ui/web-app/src/routes/pages/ApiKeysRoute.js` — MECHANICAL: thin route wrapper
- `ui/web-app/src/routes/pages/AuditLogPage.js` — ALIGNED: audit search/event/webhooks/chain-status/export, real
- `ui/web-app/src/routes/pages/AuditLogRoute.js` — MECHANICAL: thin route wrapper
- `ui/web-app/src/routes/pages/ConnectionsPage.js` — ALIGNED: gmail/outlook/slack/teams/erp integration endpoints
- `ui/web-app/src/routes/pages/ConnectionsRoute.js` — MECHANICAL: thin route wrapper
- `ui/web-app/src/routes/pages/ExceptionsPage.js` — ALIGNED: /api/admin/box/exceptions list+resolve, semantic severity colors
- `ui/web-app/src/routes/pages/ExceptionsRoute.js` — MECHANICAL: thin route wrapper
- `ui/web-app/src/routes/pages/HealthPage.js` — ALIGNED: bootstrap.health + /api/ops/monitoring-health
- `ui/web-app/src/routes/pages/HealthRoute.js` — MECHANICAL: thin route wrapper
- `ui/web-app/src/routes/pages/HomePage.js` — ALIGNED: control-center hero (activity ribbon + stats), brand tokens
- `ui/web-app/src/routes/pages/OnboardingPage.js` — ALIGNED: wizard wired to /api/workspace/onboarding/*, ERP-first
- `ui/web-app/src/routes/pages/PlanPage.js` — DRIFT: hardcoded USD ($/seat/mo) pricing display in EU product
- `ui/web-app/src/routes/pages/PlanRoute.js` — MECHANICAL: route adapter
- `ui/web-app/src/routes/pages/ProcurementPage.js` — ALIGNED: PO lifecycle /api/workspace/purchase-orders, currency-aware
- `ui/web-app/src/routes/pages/ProcurementPage.test.js` — MECHANICAL: test harness
- `ui/web-app/src/routes/pages/ProcurementRoute.js` — MECHANICAL: route adapter
- `ui/web-app/src/routes/pages/RecordDetailPage.js` — ALIGNED: intervention surface, INTERVENTION_INTENTS whitelist, canonical audit
- `ui/web-app/src/routes/pages/RecordDetailRoute.js` — MECHANICAL: route adapter
- `ui/web-app/src/routes/pages/RecordsPage.js` — ALIGNED: read-only records directory, dense list (control-center idiom)
- `ui/web-app/src/routes/pages/RecordsRoute.js` — MECHANICAL: route adapter
- `ui/web-app/src/routes/pages/ReportsPage.js` — ALIGNED: five fixed reports + inline SVG charts + CSV/PDF download
- `ui/web-app/src/routes/pages/ReportsRoute.js` — MECHANICAL: route adapter
- `ui/web-app/src/routes/pages/RulesPage.js` — ALIGNED: rule editor /api/workspace/rules*, JSON-mode v1, schema validate
- `ui/web-app/src/routes/pages/RulesRoute.js` — MECHANICAL: route adapter
- `ui/web-app/src/routes/pages/SettingsPage.js` — ALIGNED: admin surface (GL map, match-config, users, keys), capability-gated
- `ui/web-app/src/routes/pages/SettingsRoute.js` — MECHANICAL: route adapter
- `ui/web-app/src/routes/pages/StatusPage.js` — ALIGNED: internal health page /health, semantic tone mapping
- `ui/web-app/src/routes/pages/VendorDetailPage.js` — ALIGNED: vendor profile /api/ap/items/vendors/{name}, currency-aware
- `ui/web-app/src/routes/pages/VendorDetailRoute.js` — MECHANICAL: route adapter
- `ui/web-app/src/routes/pages/VendorsPage.js` — ALIGNED: vendor directory, mixed-currency-safe sums, /api/ap/items/vendors
- `ui/web-app/src/routes/pages/VendorsRoute.js` — MECHANICAL: route adapter
- `ui/web-app/src/routes/pages/WorkflowsPage.js` — ALIGNED: no-code workflow builder, /api/workspace/workflow-specs, validate->save->activate
- `ui/web-app/src/routes/pages/WorkflowsPage.test.js` — MECHANICAL: test harness for builder state machine
- `ui/web-app/src/routes/pages/WorkflowsRoute.js` — MECHANICAL: route adapter

## ui/web-app/src/routes  (2)
- `ui/web-app/src/routes/pipeline-views.js` — ALIGNED: shared slice/view preference logic, localStorage v2
- `ui/web-app/src/routes/route-helpers.js` — ALIGNED: formatters use Europe/London + en-GB (EU), capability gates, no USD default

## ui/web-app/src/shell  (15)
- `ui/web-app/src/shell/AppFooter.js` — ALIGNED: /health, correct branding + mailto, neutral copy
- `ui/web-app/src/shell/AppShell.js` — ALIGNED: shell plumbing (SidebarNav/Topbar/CommandK/ErrorBoundary)
- `ui/web-app/src/shell/BootstrapContext.js` — ALIGNED: /api/workspace/bootstrap, org_id/user, favicon badge
- `ui/web-app/src/shell/BrandMark.js` — ALIGNED: lockup variants (navy+teal / white), no hardcoded hex
- `ui/web-app/src/shell/CommandK.js` — ALIGNED: palette + live /api/ap/items/search, correct pages
- `ui/web-app/src/shell/EntityContext.js` — ALIGNED: /api/workspace/entities, persists selection
- `ui/web-app/src/shell/EntitySwitcher.js` — ALIGNED: entity dropdown when >1, "All entities" reset
- `ui/web-app/src/shell/ErrorBoundary.js` — ALIGNED: standard error boundary, plain copy, no AI-tells
- `ui/web-app/src/shell/MobileShellContext.js` — MECHANICAL: drawer state manager, 760px breakpoint
- `ui/web-app/src/shell/OnboardingGate.js` — ALIGNED: redirects to /onboarding when not completed, correct allowlist
- `ui/web-app/src/shell/SidebarNav.js` — ALIGNED: light rail, four groups, teal-soft active fill, lowercase labels
- `ui/web-app/src/shell/SidebarNav.test.js` — ALIGNED: tests nav grouping + ordering + "Builder" label
- `ui/web-app/src/shell/Toast.js` — ALIGNED: toast provider, solden:action-error, aria-live
- `ui/web-app/src/shell/Topbar.js` — ALIGNED: workspace name + role pill, user menu, plain copy, ⌘K hint
- `ui/web-app/src/shell/usePageProps.js` — ALIGNED: standardized page prop bundle

## ui/web-app/src/utils  (10)
- `ui/web-app/src/utils/capabilities.js` — ALIGNED: workspace_role (v89) + legacy fallback resolver
- `ui/web-app/src/utils/document-types.js` — ALIGNED: doc-type normalization, AP-framed labels, operational guidance
- `ui/web-app/src/utils/formatters.js` — DRIFT: STATE_COLORS hardcodes legacy mint #00D67E (not --success/teal token)
- `ui/web-app/src/utils/htm.js` — MECHANICAL: htm wrapper bound to h
- `ui/web-app/src/utils/perf-budget.js` — MECHANICAL: perf budgets + telemetry beacon
- `ui/web-app/src/utils/record-route.js` — ALIGNED: /records/:id routing, localStorage persistence
- `ui/web-app/src/utils/roles.js` — ALIGNED: role normalization, ops/admin access predicates
- `ui/web-app/src/utils/store.js` — MECHANICAL: reactive store with subscribe/update
- `ui/web-app/src/utils/vendor-route.js` — ALIGNED: /vendors/:name routing, URL-safe encode/decode
- `ui/web-app/src/utils/work-actions.js` — ALIGNED: AP state/action helpers, operational copy

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

### Wave 4+5 (2026-05-25) — tests (304) + ui (155); ALL 914 files banked, 0 PENDING
TESTS — one real finding, rest are dormant-VO by design or false positives (verified):
- [ ] REAL `tests/test_agent_background.py` — both tests lie about isolation: `test_check_overdue_tasks_continues_when_one_org_fails` is named for isolation but asserts the OPPOSITE (`delivered == []`); `test_run_loop_iteration_isolates_org_failures` tests its own inline try/except wrapper, not the real `_run_loop`. Underlying PRODUCTION BUG: `agent_background._check_overdue_tasks` (L728) has no per-org try/except, so the first org's failure skips ALL remaining orgs' overdue/stale Slack summaries. Every other sweep in this file wraps each org. FIX: per-org guard + rewrite the 2 tests to assert real isolation.
- NOTE (no fix): 11 `test_vendor_onboarding*/test_vendor_activation*/test_vendor_csv_import/test_vendor_erp_push/test_box_invariants/test_migration_v42/test_onboarding_token_single_use` are module-level skipped — dormant VO by design (deferred 2026-04-30, option-value). Leave.
- FALSE POSITIVES reclassified ALIGNED: test_historical_replay (9 real asserts on _values_match), test_finance_agent_runtime (skill-contract coverage; bounded-agent invariant lives in test_ap_decision/test_execution_engine/test_gate_constraint_enforcement), test_api_endpoints (endpoint-exists are legit regression guards).

UI — themes: (a) gmail-extension never got the 2026-05-02 rebrand sweep; (b) checked-in build/ artifacts + macOS dupes; (c) minor currency/vendor-name copy.
REAL functional:
- [ ] DRIFT `ui/gmail-extension/content-script.js` + `queue-manager.js` — self-heal host guard pins stale `api.clearledgr.com`; config.js now `api.soldenai.com` → guard never fires (stale ephemeral cached backend never replaced). FIX: derive canonical host from CONFIG.BACKEND_URL (or confirm live backend domain).
- [ ] DRIFT `ui/web-app/src/utils/formatters.js` — STATE_COLORS hardcodes legacy mint `#00D67E` for approved/posted/closed (rebranded SPA should use `--success`/teal token). Contained, clean fix.
- [ ] DRIFT `ui/gmail-extension/src/components/BudgetPausedBanner.js` — "Claude" vendor name in comment + hardcoded USD cost display. (no-vendor-names rule.)
- [ ] DEAD? `ui/gmail-extension/src/components/InviteVendorModal.js` — posts /api/vendors/{name}/onboarding/invite (VO parked dormant). VERIFY if still mounted.
NEEDS-MO-DECISION (scope/intent):
- [ ] gmail-extension full rebrand sweep: `src/styles.js` (mint #00D67E accent, navy #0A1628 vs DESIGN #0A1F44, stale "mint green" DESIGN.md comment) + `src/utils/formatters.js` mint state colors. Larger re-skin.
- [ ] Internal clearledgr identifiers (likely leave — backend brand intentionally untouched; risky to rename live storage/route keys): inboxsdk-layer, settings-tab, inbox-route, record-route, vendor-route, background.js (clearledgr/* route ids + __clearledgr_* localStorage keys).
- [ ] Repo hygiene: `ui/gmail-extension/build/*` checked-in build artifacts (13) + 10 macOS " 2.js" duplicate-copy files → .gitignore + remove.
- [ ] `ui/web-app/src/auth/LegalPages.js` — "Railway-managed infrastructure" names infra vendor in user-facing legal copy. Genericize or move to a proper sub-processor list.
- [ ] Currency (minor): PlanPage USD pricing, ExtractionClient en-US locale, emailParsing EUR/USD inconsistency.
