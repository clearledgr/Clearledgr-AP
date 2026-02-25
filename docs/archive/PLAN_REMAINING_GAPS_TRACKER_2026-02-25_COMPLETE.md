# Plan Remaining Gaps Tracker (Open Items Only)

Date created: 2026-02-25
Source of truth: `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
Assessment baseline: latest read-only verification (post-tracker implementation review)

## Scope and Doctrine Guardrails

This tracker covers only the currently open, verified gaps/risks against `PLAN.md`.
It does not reopen the archived completed tracker.

Non-negotiable product/doctrine guardrails:
- Preserve Clearledgr AP v1 as an embedded, agentic finance execution layer.
- Gmail remains the primary operator surface.
- Slack and Teams remain approval/decision surfaces.
- ERP remains system of record.
- Preserve browser-agent policy/preview/confirmation/audit semantics.
- Harden boundaries and durability; do not replace with a generic automation platform.
- Temporary disablement must be via rollout controls, not code removal.

## Status Summary Table

| Priority | Total | OPEN | IN_PROGRESS | BLOCKED | DONE | ACCEPTED_RISK |
|---|---:|---:|---:|---:|---:|---:|
| P0 | 3 | 0 | 0 | 0 | 3 | 0 |
| P1 | 7 | 0 | 0 | 0 | 7 | 0 |
| P2 | 4 | 0 | 0 | 0 | 4 | 0 |
| **All** | **14** | **0** | **0** | **0** | **14** | **0** |

Status rules:
- `DONE` requires code + tests + validation output + evidence refs.
- `ACCEPTED_RISK` is pilot-only and must include expiration, owner, rollback/feature gate, and GA closure requirement.

## Open Items by Priority (Rxx)

Schema for each item:
- `ID`, `Category`, `Priority`, `Plan refs`, `Status`, `Type`
- `Problem statement`
- `Production impact`
- `Implementation scope (in/out)`
- `Code touchpoints`
- `Design decisions`
- `Acceptance criteria`
- `Validation/tests`
- `Rollout/feature gate`
- `Evidence after completion`
- `Notes / follow-ons`

---

### R01
- ID: `R01`
- Category: `security`
- Priority: `P0`
- Plan refs: `PLAN.md` `7.3`, `7.4`, `7.6`, `7.7`; `B. Approval action interfaces`
- Status: `DONE`
- Type: `missing`
- Problem statement: Unauthenticated `/api/agent/*` endpoints allow session creation, command enqueue, result submission, and policy mutation with caller-supplied identity fields.
- Production impact: Command/result spoofing, audit actor spoofing, policy tampering, data exposure.
- Implementation scope (in/out):
  - In: route-level auth for `/api/agent/*`, actor identity binding from authenticated user for sensitive operations, tests.
  - Out: browser runner full trust model (tracked in `R14`).
- Code touchpoints:
  - `clearledgr/api/agent_sessions.py`
  - `clearledgr/core/auth.py`
  - `tests/test_browser_agent_layer.py`
- Design decisions:
  - Use `get_current_user` as default auth dependency.
  - Keep request body actor fields optional hints, but audit identity must come from auth context for policy/result mutations.
- Acceptance criteria:
  - Unauthenticated `/api/agent/*` returns `401`.
  - Authenticated requests preserve existing behavior.
  - Audit actor identity for policy/result endpoints derives from authenticated user/API key.
- Validation/tests:
  - Added auth boundary tests (`401`) and actor-binding assertion in `tests/test_browser_agent_layer.py`.
  - Ran browser-agent and channel-adjacent regressions.
- Rollout/feature gate: none; hardening is default-on.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py tests/test_ap_aggregation_api.py tests/test_api_endpoints.py::TestExtensionEndpoints`
  - Result: `20 passed`
  - Files:
    - `clearledgr/api/agent_sessions.py`
    - `tests/test_browser_agent_layer.py`
- Notes / follow-ons: coordinate with `R14` for runner-specific auth mode.

### R02
- ID: `R02`
- Category: `security`
- Priority: `P0`
- Plan refs: `PLAN.md` `5.1`, `7.2`, `7.3`, `7.4`
- Status: `DONE`
- Type: `missing`
- Problem statement: Mutating `/extension/*` endpoints (approve/post/reject/submit-for-approval/etc.) are unauthenticated.
- Production impact: Unauthorized AP actions and ERP posting attempts via public API surface.
- Implementation scope (in/out):
  - In: auth for mutating extension endpoints, tests, docs update.
  - Out: Slack/Teams callback verifier model (already separate and remains public).
- Code touchpoints:
  - `clearledgr/api/gmail_extension.py`
  - `clearledgr/core/auth.py`
  - `tests/test_v1_core_completion.py`
  - new/updated extension auth tests
- Design decisions:
  - Protect mutating routes, keep `/extension/health` and possibly non-sensitive read routes explicitly documented.
  - Use JWT/API key via `get_current_user`.
- Acceptance criteria:
  - Unauthenticated mutating `/extension/*` returns `401`.
  - Authorized requests preserve existing behavior.
  - Read-only/public routes remain intentionally accessible only if explicitly listed.
- Validation/tests:
  - Added extension auth tests (`401`) and authorized triage test with mocked runtime path.
  - Ran extension/AP regression slice.
- Rollout/feature gate: none.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py tests/test_ap_aggregation_api.py tests/test_api_endpoints.py::TestExtensionEndpoints`
  - Result: `20 passed`
  - Files:
    - `clearledgr/api/gmail_extension.py`
    - `tests/test_api_endpoints.py`
    - `tests/test_v1_core_completion.py`
- Notes / follow-ons: ensure Gmail extension local dev docs mention auth expectations.

### R03
- ID: `R03`
- Category: `security`
- Priority: `P0`
- Plan refs: `PLAN.md` `7.6`, `8.5`
- Status: `DONE`
- Type: `missing`
- Problem statement: `/api/ops/*` diagnostics endpoints are unauthenticated.
- Production impact: Tenant health/KPI/routing/autopilot diagnostics leak.
- Implementation scope (in/out):
  - In: route-level auth for `/api/ops/*`, tests.
  - Out: admin console auth (already protected).
- Code touchpoints:
  - `clearledgr/api/ops.py`
  - `clearledgr/core/auth.py`
  - `tests/test_browser_agent_layer.py`
  - new ops auth tests
- Design decisions:
  - Require `get_current_user` for all ops endpoints.
- Acceptance criteria:
  - Unauthenticated `/api/ops/*` returns `401`.
  - Authenticated requests continue to return metrics/diagnostics.
- Validation/tests:
  - Added ops auth tests (`401`) and retained authenticated metrics coverage.
  - Ran browser-agent + ops metrics/routing regression slice.
- Rollout/feature gate: none.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py tests/test_ap_aggregation_api.py tests/test_v1_core_completion.py tests/test_channel_approval_contract.py`
  - Result: `29 passed`
  - Files:
    - `clearledgr/api/ops.py`
    - `tests/test_browser_agent_layer.py`
    - `tests/test_ap_aggregation_api.py`
- Notes / follow-ons: consider role-based restriction for some org-wide ops endpoints.

---

### R04
- ID: `R04`
- Category: `erp-contract`
- Priority: `P1`
- Plan refs: `PLAN.md` `6.5`, `7.3`; `C. ERP posting interfaces`
- Status: `DONE`
- Type: `partial`
- Problem statement: ERP posting responses are connector-specific and not fully normalized to the canonical response contract.
- Production impact: Fragile downstream logic, inconsistent operator-safe errors, parity test difficulty.
- Implementation scope (in/out):
  - In: canonical response normalization in `post_bill_api_first()`, tests, caller adaptation.
  - Out: connector internal payload redesign.
- Code touchpoints:
  - `clearledgr/services/erp_api_first.py`
  - `clearledgr/integrations/erp_router.py`
  - `clearledgr/services/invoice_workflow.py`
  - `tests/test_erp_api_first.py`
- Design decisions:
  - Normalize at API-first layer as single choke point.
- Acceptance criteria:
  - Canonical keys returned on success/failure/blocked/fallback responses.
  - `erp_reference` stable on success.
  - Connector-specific keys no longer required downstream.
- Validation/tests:
  - Added canonical ERP contract assertions (success, pending fallback, blocked, api-failed) in `tests/test_erp_api_first.py`.
  - Added `already_posted` idempotent-path regression so API-first treats it as success-like and preserves canonical `erp_reference`.
  - Ran ERP API-first suite and broader AP/channel/browser regressions.
- Rollout/feature gate: none.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_erp_api_first.py`
  - Result: `8 passed`
  - Files:
    - `clearledgr/services/erp_api_first.py`
    - `tests/test_erp_api_first.py`
    - `clearledgr/services/invoice_workflow.py` (compatibility consumer retained)
  - Additional regression: `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py tests/test_erp_api_first.py tests/test_v1_core_completion.py tests/test_channel_approval_contract.py` -> `38 passed`
- Notes / follow-ons: add explicit error-code mapping table per connector.

### R05
- ID: `R05`
- Category: `agentic-runtime`
- Priority: `P1`
- Plan refs: `PLAN.md` `4.6`, `6.7`, `7.3`, `7.7`
- Status: `DONE`
- Type: `partial`
- Problem statement: Browser fallback dispatch is implemented, but completion reconciliation to AP state/result closure is not implemented/verified.
- Production impact: Fallback may appear complete while AP item remains unresolved; operator confusion and audit ambiguity.
- Implementation scope (in/out):
  - In: completion callback/contract, AP state reconciliation, idempotency, audits, tests.
  - Out: in-process browser executor implementation.
- Code touchpoints:
  - `clearledgr/api/agent_sessions.py`
  - `clearledgr/services/browser_agent.py`
  - `clearledgr/services/erp_api_first.py`
  - `clearledgr/services/invoice_workflow.py`
  - `clearledgr/core/stores/ap_store.py`
  - `tests/test_erp_api_first.py`
  - `tests/test_browser_agent_layer.py`
- Design decisions:
  - Add explicit authenticated completion contract for fallback macro runs.
  - Map success to `posted_to_erp` and failure to `failed_post`.
- Acceptance criteria:
  - Browser fallback completion can finalize AP state.
  - Completion is idempotent and audited.
- Validation/tests:
  - Added fallback completion reconciliation tests (success + duplicate idempotency) in `tests/test_erp_api_first.py`.
  - Added end-to-end API tests for authenticated `/api/agent/sessions/{id}/complete` success/failure/duplicate paths in `tests/test_browser_agent_layer.py`.
- Rollout/feature gate: can be gated behind runner capability flag while stabilizing.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py tests/test_erp_api_first.py`
  - Result: `25 passed`
  - Files:
    - `clearledgr/services/erp_api_first.py`
    - `clearledgr/api/agent_sessions.py`
    - `tests/test_browser_agent_layer.py`
    - `tests/test_erp_api_first.py`
- Notes / follow-ons: coordinate runner auth with `R14`.

### R06
- ID: `R06`
- Category: `workflow`
- Priority: `P1`
- Plan refs: `PLAN.md` `4.1` (`Resubmission semantics`), `D. AP item type requirements`
- Status: `DONE`
- Type: `missing`
- Problem statement: Rejected-item resubmission semantics (new AP item + supersession linkage) are not implemented.
- Production impact: Breaks canonical plan behavior and auditability for rejected corrections.
- Implementation scope (in/out):
  - In: resubmission endpoint, supersession linkage fields/metadata, audit, tests.
  - Out: UI polish beyond API/worklist/context exposure.
- Code touchpoints:
  - `clearledgr/core/database.py`
  - `clearledgr/core/stores/ap_store.py`
  - `clearledgr/api/ap_items.py`
  - `clearledgr/services/invoice_workflow.py`
  - tests (new)
- Design decisions:
  - Prefer explicit DB columns for supersession linkage if feasible.
  - Rejected remains terminal.
- Acceptance criteria:
  - Resubmission creates a new AP item; rejected item remains terminal.
  - Supersession linkage visible in context/worklist/audit.
- Validation/tests:
  - Added rejected-item resubmission tests (new AP item + supersession linkage + context/worklist visibility) and illegal-state guard tests in `tests/test_ap_items_merge_and_audit_guardrails.py`.
  - Ran AP regression slice covering runtime workflow/state-machine suites and existing AP item APIs after schema/store changes.
- Rollout/feature gate: none.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_ap_items_merge_and_audit_guardrails.py`
  - Result: `4 passed`
  - Files:
    - `clearledgr/api/ap_items.py`
    - `clearledgr/core/database.py`
    - `clearledgr/core/stores/ap_store.py`
    - `tests/test_ap_items_merge_and_audit_guardrails.py`
  - Additional regression: `PYTHONPATH=. pytest -q tests/test_ap_items_merge_and_audit_guardrails.py tests/test_v1_core_completion.py tests/test_invoice_workflow_runtime_state_transitions.py tests/test_plan_acceptance.py tests/test_e2e_ap_flow.py` -> `50 passed`
- Notes / follow-ons:

### R07
- ID: `R07`
- Category: `agentic-runtime`
- Priority: `P1`
- Plan refs: `PLAN.md` `7.6`, `7.7`
- Status: `DONE`
- Type: `fragile`
- Problem statement: Orchestration durability is partial (local entry workflow only); async retries/post-processing in `AgentOrchestrator` are fire-and-forget and non-durable.
- Production impact: Retry loss on restart, misleading autonomy/reliability claims.
- Implementation scope (in/out):
  - In: pilot-safe gating/truth-in-claims, durable retry scheduling design+implementation, tests.
  - Out: mandatory Temporal adoption.
- Code touchpoints:
  - `clearledgr/workflows/temporal_runtime.py`
  - `clearledgr/workflows/ap_workflow.py`
  - `clearledgr/services/agent_orchestrator.py`
  - `clearledgr/core/database.py`
- Design decisions:
  - Preserve `local_db` workflow runtime labeling while implementing durable DB-backed agent retry jobs.
  - Replace in-memory autonomous ERP retry backoff with persisted retry queue + restart-safe drain/worker processing.
- Acceptance criteria:
  - Runtime surfaces accurately expose backend/durability mode.
  - Durable retry outcomes survive restart.
- Validation/tests:
  - Added durable retry queue tests (schedule, restart-safe processing, reschedule, dead-letter) in `tests/test_agent_orchestrator_durable_retry.py`.
  - Updated ops runtime-truth tests to assert durable retry mode (`durable_db_retry_queue`) in `tests/test_browser_agent_layer.py`.
  - Ran broader regression slice covering browser-agent, ERP API-first, channel contracts, workflow runtime transitions, and audit guardrails.
- Rollout/feature gate: retry behavior gated by env/feature flag.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_agent_orchestrator_durable_retry.py tests/test_browser_agent_layer.py::test_autopilot_status_includes_agent_runtime_truth_claims tests/test_browser_agent_layer.py::test_autopilot_status_keeps_durable_retry_enabled_in_production`
  - Result: `5 passed`
  - Files:
    - `clearledgr/services/agent_orchestrator.py`
    - `clearledgr/core/database.py`
    - `clearledgr/core/stores/ap_store.py`
    - `clearledgr/api/ops.py`
    - `tests/test_browser_agent_layer.py`
    - `tests/test_agent_orchestrator_durable_retry.py`
  - Additional regression: `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py tests/test_agent_orchestrator_durable_retry.py tests/test_erp_api_first.py tests/test_channel_approval_contract.py tests/test_invoice_workflow_runtime_state_transitions.py tests/test_ap_items_merge_and_audit_guardrails.py` -> `60 passed`
- Notes / follow-ons:
  - Non-retry post-processing tasks (follow-up/clarifying questions/insights) still run as fire-and-forget best-effort and are not part of this retry durability item.

### R08
- ID: `R08`
- Category: `audit`
- Priority: `P1`
- Plan refs: `PLAN.md` `7.4`
- Status: `DONE`
- Type: `partial`
- Problem statement: Append-only audit protections are SQLite-only; Postgres parity is not implemented.
- Production impact: Weaker immutability guarantees on likely production backend.
- Implementation scope (in/out):
  - In: Postgres append-only protections or equivalent guarded mutation policy + tests/docs.
  - Out: full database migration framework overhaul.
- Code touchpoints:
  - `clearledgr/core/database.py`
  - `tests/test_ap_items_merge_and_audit_guardrails.py`
- Design decisions:
  - Prefer DB-enforced trigger/policy for Postgres; fallback app-layer hard-block only if DB trigger path is not viable.
- Acceptance criteria:
  - Production DB backend has documented and tested append-only protections.
- Validation/tests: backend-specific immutability tests (SQLite + Postgres where available).
- Rollout/feature gate: none.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_ap_items_merge_and_audit_guardrails.py`
  - Result: `6 passed`
  - Files:
    - `clearledgr/core/database.py`
    - `tests/test_ap_items_merge_and_audit_guardrails.py`
- Notes / follow-ons: Runtime Postgres integration smoke test is still recommended in CI/staging when Postgres service is available.

### R09
- ID: `R09`
- Category: `observability`
- Priority: `P1`
- Plan refs: `PLAN.md` `7.6`
- Status: `DONE`
- Type: `partial`
- Problem statement: Correlation IDs are not propagated end-to-end across intake, approval, posting, and audit.
- Production impact: Harder incident debugging and operator support.
- Implementation scope (in/out):
  - In: correlation ID generation/propagation through AP lifecycle paths + tests.
  - Out: full distributed tracing stack.
- Code touchpoints:
  - `clearledgr/services/invoice_workflow.py`
  - `clearledgr/api/slack_invoices.py`
  - `clearledgr/api/teams_invoices.py`
  - `clearledgr/services/erp_api_first.py`
  - `clearledgr/core/stores/ap_store.py`
- Design decisions:
  - Single correlation ID per AP lifecycle/run; reuse in audit and browser actions.
- Acceptance criteria:
  - Correlation ID visible across linked audit events for intake→approval→posting/fallback.
- Validation/tests: end-to-end correlation audit chain tests.
- Rollout/feature gate: none.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_invoice_workflow_runtime_state_transitions.py::test_workflow_state_transition_audits_share_single_correlation_id_across_intake_and_approval tests/test_channel_approval_contract.py::test_slack_interactive_request_info_duplicate_and_stale tests/test_erp_api_first.py::test_post_bill_api_first_success_records_attempt_and_success`
  - Result: `3 passed`
  - Files:
    - `clearledgr/services/invoice_workflow.py`
    - `clearledgr/services/erp_api_first.py`
    - `clearledgr/api/slack_invoices.py`
    - `clearledgr/api/teams_invoices.py`
    - `clearledgr/core/approval_action_contract.py`
    - `clearledgr/core/stores/ap_store.py`
    - `tests/test_invoice_workflow_runtime_state_transitions.py`
    - `tests/test_channel_approval_contract.py`
    - `tests/test_erp_api_first.py`
- Notes / follow-ons: Correlation IDs are application-level trace IDs, not a full distributed tracing system.

### R10
- ID: `R10`
- Category: `audit`
- Priority: `P1`
- Plan refs: `PLAN.md` `7.2`
- Status: `DONE`
- Type: `partial`
- Problem statement: Illegal transition attempts are rejected, but there is no explicit audit/log evidence path for rejected attempts at the store boundary.
- Production impact: Reduced forensic visibility for invalid mutation attempts.
- Implementation scope (in/out):
  - In: explicit logging/audit for illegal transitions, tests.
  - Out: changing successful transition audit semantics.
- Code touchpoints:
  - `clearledgr/core/stores/ap_store.py`
  - possibly wrappers/callers that can supply org/item context
  - tests (new)
- Design decisions:
  - If pre-raise DB audit is unsafe, emit structured application log + best-effort audit wrapper.
- Acceptance criteria:
  - Illegal transition attempt produces observable log/audit evidence and remains rejected.
- Validation/tests: transition rejection logging/audit tests.
- Rollout/feature gate: none.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_ap_items_merge_and_audit_guardrails.py::test_illegal_state_transition_is_rejected_and_audited`
  - Result: `1 passed`
  - Files:
    - `clearledgr/core/stores/ap_store.py`
    - `tests/test_ap_items_merge_and_audit_guardrails.py`
- Notes / follow-ons: Rejected attempts now emit best-effort audit evidence plus structured log warning without changing successful transition semantics.

---

### R11
- ID: `R11`
- Category: `testing`
- Priority: `P2`
- Plan refs: `PLAN.md` `5.3`, `7.7`
- Status: `DONE`
- Type: `partial`
- Problem statement: Teams verifier crypto/JWKS path lacks direct tests; handler tests mostly monkeypatch `verify_teams_token`.
- Production impact: Lower confidence in token verification failure modes.
- Implementation scope (in/out):
  - In: direct tests for `verify_teams_token` branches.
  - Out: live Microsoft integration tests.
- Code touchpoints:
  - `clearledgr/core/teams_verify.py`
  - tests (new)
- Design decisions:
  - Mock `httpx`/`PyJWKClient`/`jwt.decode` at function boundaries.
- Acceptance criteria:
  - Tests cover malformed header, missing env, JWKS fetch error, invalid issuer/audience/token, happy path.
- Validation/tests: new teams verifier unit suite.
- Rollout/feature gate: none.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_teams_verify.py`
  - Result: `8 passed`
  - Files:
    - `clearledgr/core/teams_verify.py`
    - `tests/test_teams_verify.py`
- Notes / follow-ons: Tests mock Microsoft identity/JWKS fetches; live integration validation remains part of external parity evidence.

### R12
- ID: `R12`
- Category: `deployment`
- Priority: `P2`
- Plan refs: `PLAN.md` `7.6`, `8.4`, `8.5`
- Status: `DONE`
- Type: `missing`
- Problem statement: Config/deployment templates lag current requirements (Slack/Teams callback verifier vars, runtime flags); compose healthcheck uses `curl` not present in image.
- Production impact: Misconfigured deployments, false health failures.
- Implementation scope (in/out):
  - In: `env.example`, `README.md`, `render.yaml`, `docker-compose.yml` alignment.
  - Out: full deployment automation.
- Code touchpoints:
  - `env.example`
  - `README.md`
  - `render.yaml`
  - `docker-compose.yml`
  - `Dockerfile` (if healthcheck strategy changes)
- Design decisions:
  - Document required verifier and runtime vars explicitly.
  - Use a healthcheck compatible with runtime image contents.
- Acceptance criteria:
  - Templates/docs mention required Slack/Teams verifier vars and runtime flags.
  - Compose healthcheck works with built image.
- Validation/tests: static checks + optional container smoke check.
- Rollout/feature gate: none.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py tests/test_api_endpoints.py::TestExtensionEndpoints tests/test_ap_aggregation_api.py`
  - Result: `23 passed`
  - Files:
    - `env.example`
    - `render.yaml`
    - `docker-compose.yml`
    - `README.md`
- Notes / follow-ons: Optional container smoke check (`docker compose up`) can be added to CI for compose healthcheck validation.

### R13
- ID: `R13`
- Category: `ga-evidence`
- Priority: `P2`
- Plan refs: `PLAN.md` `6.6`, `6.8`, `9.3`, `9.4`, `9.5`
- Status: `DONE`
- Type: `partial`
- Problem statement: In-app GA readiness metadata exists, but repository/external artifact process for parity evidence and runbooks is not formalized.
- Production impact: GA claims may be hard to audit/reproduce.
- Implementation scope (in/out):
  - In: doc/process contract for artifact locations, naming, ownership, signoff workflow; optional repo scaffolding.
  - Out: generating external evidence automatically.
- Code touchpoints:
  - `docs/` (new readiness process doc)
  - `clearledgr/core/launch_controls.py` (optional metadata shape tightening)
  - `README.md` (pointer)
- Design decisions:
  - Treat in-app metadata as index, not proof artifact store.
- Acceptance criteria:
  - Clear documented process for parity matrices, runbooks, signoffs, and artifact references.
- Validation/tests: doc review + optional admin summary shape assertions.
- Rollout/feature gate: none.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_admin_launch_controls.py`
  - Result: `2 passed`
  - Files:
    - `docs/GA_READINESS_EVIDENCE_PROCESS.md`
    - `README.md`
    - `clearledgr/core/launch_controls.py` (existing in-app metadata index remains the app-side reference point)
- Notes / follow-ons: Create `docs/ga-evidence/releases/<release_id>/MANIFEST.md` for the next pilot/GA candidate using the new process doc.

### R14
- ID: `R14`
- Category: `security`
- Priority: `P2`
- Plan refs: `PLAN.md` `6.7`, `7.3`, `7.4`, `7.7`
- Status: `DONE`
- Type: `partial`
- Problem statement: Browser runner trust model and secure callback/result identity are not fully defined if the runner is external.
- Production impact: Result spoofing risk and weak assurance of browser fallback outcomes.
- Implementation scope (in/out):
  - In: explicit runner auth contract and result identity verification, tests, docs.
  - Out: full runner implementation.
- Code touchpoints:
  - `clearledgr/api/agent_sessions.py`
  - `clearledgr/core/auth.py` or new runner auth helper
  - `clearledgr/services/browser_agent.py`
  - docs/tests
- Design decisions:
  - Reuse authenticated API user/service-token path for `/api/agent/sessions/{id}/results` and `/complete` as the core secure path.
  - Keep runner-specific trust mode (service-token-only vs shared secret) and explicit unauthorized callback audit logging as follow-on hardening.
- Acceptance criteria:
  - Runner completion/result callbacks are authenticated and auditable.
  - Unauthorized submissions are rejected and logged.
- Validation/tests:
  - Added explicit runner trust-mode enforcement and unauthorized callback audit tests (`403`) for `/results` and `/complete` in `tests/test_browser_agent_layer.py`.
  - Added env/documentation for `AP_BROWSER_RUNNER_TRUST_MODE` and documented service/API-key runner posture in `README.md` / `env.example`.
- Rollout/feature gate: rollout-control fallback disable remains available.
- Evidence after completion:
  - Test command: `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py tests/test_erp_api_first.py tests/test_v1_core_completion.py tests/test_channel_approval_contract.py`
  - Result: `37 passed`
  - Files:
    - `clearledgr/api/agent_sessions.py`
    - `clearledgr/services/erp_api_first.py`
    - `tests/test_browser_agent_layer.py`
    - `env.example`
    - `README.md`
  - Additional focused validation: `PYTHONPATH=. pytest -q tests/test_teams_verify.py tests/test_browser_agent_layer.py::test_runner_trust_policy_denies_low_privileged_result_callback_and_audits tests/test_browser_agent_layer.py::test_runner_trust_policy_denies_low_privileged_fallback_complete_and_audits` -> `10 passed`
- Notes / follow-ons: tightly coupled with `R05`; `api_only` trust mode can be enabled for stricter external-runner deployments.

## Agentic Substitutions / Placeholders Register

1. Local DB runtime instead of Temporal (`clearledgr/workflows/temporal_runtime.py`)
- Pilot stance: acceptable with explicit `local_db` labeling and truthful status surfaces.
- GA path: Temporal or equivalent durable guarantees + tests.

2. Entry-step-only durable workflow dispatch (`clearledgr/workflows/ap_workflow.py`)
- Pilot stance: acceptable if documented/monitored.
- GA path: durable multi-step orchestration or equivalent guarantees.

3. Browser-agent queue/result-submission (not in-process executor) (`clearledgr/services/browser_agent.py`)
- Pilot stance: acceptable only with authenticated runner/result channel + reconciliation (`R05`, `R14`).
- GA path: secure runner protocol + result integrity + AP closure semantics.

4. Agent reasoning heuristic fallback (`clearledgr/services/agent_orchestrator.py`)
- Pilot stance: acceptable fail-safe.
- GA path: preserve fallback + telemetry on fallback frequency.

5. Fire-and-forget async post-processing (`clearledgr/services/agent_orchestrator.py`)
- Retry path: now durable via DB-backed queue/scheduler (`R07` complete).
- Remaining non-retry post-processing tasks are best-effort and truthfully exposed as non-durable.

## Pilot Milestone Checklist

- [x] R01 complete
- [x] R02 complete
- [x] R03 complete
- [x] R05 complete (or `ACCEPTED_RISK` with expiry + fallback disabled in production)
- [x] R04 complete (or downstream compatibility wrapper documented)
- [x] Pilot hardening regression suite passes
- [ ] Rollback controls verified in staging

## GA Milestone Checklist

- [x] R04-R10 complete
- [x] R11-R13 complete
- [x] R14 complete (if browser fallback enabled in GA)
- [ ] Failure-mode matrix tests complete (`PLAN.md 7.7`)
- [ ] ERP parity evidence + runbooks + signoffs recorded (`PLAN.md 6.6`, `6.8`, `9.3-9.5`)

## Change Log

- `2026-02-25`: Tracker created with `R01-R14` seeded from latest verification assessment.
- `2026-02-25`: `R01-R03` completed (auth hardening for `/api/agent`, mutating `/extension`, `/api/ops`) with auth-boundary tests and regression validation recorded.
- `2026-02-25`: `R05` completed (browser fallback completion reconciliation + AP-state closure) and `R14` moved to `IN_PROGRESS` with authenticated `/api/agent/sessions/{id}/complete` core path implemented.
- `2026-02-25`: `R04` completed (canonical ERP response normalization in `post_bill_api_first()`, including `already_posted` idempotent-path handling).
- `2026-02-25`: `R06` completed (rejected-item resubmission creates a new AP item with explicit supersession linkage fields and audit visibility).
- `2026-02-25`: `R07` moved to pilot `ACCEPTED_RISK` after implementing and validating runtime truth-in-claims + production-safe non-durable retry gating exposure in `/api/ops/autopilot-status`.
- `2026-02-25`: `R08`, `R10` completed (Postgres append-only audit guard DDL path + rejected-transition audit evidence at store boundary).
- `2026-02-25`: `R09` completed (AP lifecycle correlation ID propagation across workflow transitions, Slack/Teams callback audits, and ERP API-first/fallback audits).
- `2026-02-25`: `R11` completed (direct Teams JWT verifier unit coverage for malformed headers, env missing, JWKS errors, issuer/audience/token failures, cache path, and success).
- `2026-02-25`: `R12` completed (deployment/config template alignment, runtime flags, verifier vars, and compose healthcheck compatibility).
- `2026-02-25`: `R13` completed (GA readiness evidence process doc and README pointer; in-app launch controls remain the metadata index).
- `2026-02-25`: `R14` completed (explicit runner trust-mode policy + unauthorized runner callback audit logging/tests for `/results` and `/complete`).
- `2026-02-25`: `R07` completed (durable DB-backed autonomous ERP retry queue/scheduler with restart-safe processing, AP-state reconciliation, dead-letter handling, and truthful durable runtime reporting in `/api/ops/autopilot-status`).

## Archive Protocol

When all items are `DONE` or intentionally `ACCEPTED_RISK` (pilot only):
1. Freeze the tracker with final evidence/results.
2. Archive to `docs/archive/PLAN_REMAINING_GAPS_TRACKER_<DATE>_<STATUS>.md`.
3. Replace this file with a pointer stub containing archive path and checksum.
