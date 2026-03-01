# Full-Scale Remaining Gaps Tracker

Date opened: 2026-02-28  
Source of truth: `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`, `/Users/mombalam/Desktop/Clearledgr.v1/README.md`

## Guardrails
- Preserve Clearledgr as one Finance AI Agent runtime with AP as Skill #1.
- Keep Gmail primary, Slack/Teams approvals, ERP system-of-record write-back.
- Do not remove agentic behavior to close launch gaps.
- `DONE` requires: code/docs landed, tests run, and evidence command output recorded.

## Status Summary

| ID | Priority | Category | Status | Type |
|---|---|---|---|---|
| FS01 | P0 | doctrine | DONE | docs-conflict |
| FS02 | P1 | runtime-hardening | DONE | fragile |
| FS03 | P0 | launch-evidence | IN_PROGRESS | missing |
| FS04 | P1 | e2e-confidence | IN_PROGRESS | partial |
| FS05 | P1 | durability-architecture | DONE | substitution |
| FS06 | P2 | extraction-quality | IN_PROGRESS | confidence-gap |

## Items

### FS01
- Priority: `P0`
- Category: `doctrine`
- Status: `DONE`
- Problem: `PLAN.md` contains conflicting ERP scope statements (`NetSuite-first post-GA expansions` vs `all four GA ERPs`), while runtime strategy currently supports NetSuite/QuickBooks/Xero/SAP as API-primary.
- Plan refs:
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:24`
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:358`
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:478`
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:692`
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_connector_strategy.py`
- Acceptance criteria:
  - PLAN ERP scope is internally consistent and matches intended supported ERP set.
  - README language does not conflict with PLAN.
- Validation/tests:
  - `rg -n "NetSuite-first|all four GA ERPs|Post‑GA expansions|ERP defaults" PLAN.md README.md`
- Evidence:
  - `rg -n "connector scope|NetSuite, QuickBooks, Xero, and SAP|ERP defaults" PLAN.md README.md` confirms aligned doctrine text in both docs (`2026-02-28`).
  - Removed NetSuite-only phrasing in `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md` Section `9.4`.

### FS02
- Priority: `P1`
- Category: `runtime-hardening`
- Status: `DONE`
- Problem: Strict AP-v1 profile can still be disabled in production-like environments by setting `AP_V1_STRICT_SURFACES=false` even when legacy surfaces are not explicitly requested.
- Plan refs:
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:13`
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:109`
  - `/Users/mombalam/Desktop/Clearledgr.v1/README.md:155`
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/main.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_runtime_surface_scope.py`
- Acceptance criteria:
  - In production/staging, strict profile is forced on unless explicit legacy override (`CLEARLEDGR_ENABLE_LEGACY_SURFACES=true` and `AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION=true`) is active.
  - Runtime contract exposes forced behavior via warning.
- Validation/tests:
  - `PYTHONPATH=. pytest -q tests/test_runtime_surface_scope.py`
- Evidence:
  - `5 passed` on `2026-02-28`.
  - Production strict-surface forcing and warning exposure implemented in `/Users/mombalam/Desktop/Clearledgr.v1/main.py` and validated by `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_runtime_surface_scope.py`.

### FS03
- Priority: `P0`
- Category: `launch-evidence`
- Status: `IN_PROGRESS`
- Problem: GA launch readiness tracker remains mostly open; staging E2E evidence bundle is not complete.
- Plan refs:
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:483`
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:510`
- Code/ops touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_LAUNCH_READINESS_TRACKER.md`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/STAGING_DRILL_RUNBOOK.md`
- Acceptance criteria:
  - L01-L04 and L11 evidence artifacts recorded with owner/date/results.
- Validation/tests:
  - `python3 /Users/mombalam/Desktop/Clearledgr.v1/scripts/validate_launch_evidence.py --mode pilot --json`
- Evidence:
  - New validator script added at `/Users/mombalam/Desktop/Clearledgr.v1/scripts/validate_launch_evidence.py` with tests in `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_validate_launch_evidence.py`.
  - Pilot-critical items now have non-`TBD` owners in `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_LAUNCH_READINESS_TRACKER.md` (`L01`, `L02`, `L03`, `L04`, `L11`).
  - Current validator output is `passed=false` with only `status_not_done` blockers on `L01-L04`, `L11`; manifest placeholder count is now `0`.
  - `L01` Gmail runtime auth blocker is cleared; authenticated runtime evidence passed (`status=passed`, `extension_worker_detected=true`, `mounted_sections=3`).
  - `L01` automated AP end-to-end baseline is now captured in `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/PILOT_E2E_EVIDENCE.md` (`tests/test_e2e_ap_flow.py` -> `6 passed`).
  - Focused automated bundles for `L02/L03/L04/L11` were re-run on `2026-02-28` and all targeted tests passed (`7`, `3`, `4`, `12` respectively), leaving staging-only closure work.
  - Latest validator snapshot is tracked at `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/launch-evidence-validation.json`.

### FS04
- Priority: `P1`
- Category: `e2e-confidence`
- Status: `IN_PROGRESS`
- Problem: Real Gmail/Chrome runtime remains environment-gated and not always-on for local/CI deterministic proof.
- Plan refs:
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:513`
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:535`
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-layer.browser-harness.test.cjs`
  - `/Users/mombalam/Desktop/Clearledgr.v1/.github/workflows/gmail-runtime-smoke-nightly.yml`
- Acceptance criteria:
  - Deterministic CI harness remains required.
  - Nightly authenticated runtime evidence pipeline has successful runs and artifacts.
- Validation/tests:
  - `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && npm run test:browser-harness`
- Evidence:
  - Deterministic harness CI workflow exists at `/Users/mombalam/Desktop/Clearledgr.v1/.github/workflows/gmail-extension-browser-harness.yml`.
  - Nightly authenticated runtime pipeline exists at `/Users/mombalam/Desktop/Clearledgr.v1/.github/workflows/gmail-runtime-smoke-nightly.yml`.
  - Local run `npm run test:browser-harness` (`2026-02-28`) passed with browser prereq skip in this environment (`pass=1`, `skip=1`, `fail=0`).
  - Live authenticated runtime evidence is now successful for active release (`npm run test:e2e-auth:evidence -- --release-id ap-v1-2026-02-25-pilot-rc1 --profile-dir "$HOME/.clearledgr-gmail-e2e-profile"`).
  - Remaining blocker is successful nightly pipeline artifact success history for the active release bundle.

### FS05
- Priority: `P1`
- Category: `durability-architecture`
- Status: `DONE`
- Problem: Runtime durability is DB-backed local orchestration (`local_db`) rather than Temporal backend; acceptable substitution but requires explicit product decision and claims discipline.
- Plan refs:
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:435`
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:447`
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/workflows/temporal_runtime.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/agent_orchestrator.py`
- Acceptance criteria:
  - GA claim language explicitly reflects chosen durability backend.
  - Runtime status endpoints continue exposing truth-in-claims.
- Validation/tests:
  - `PYTHONPATH=. pytest -q tests/test_agent_orchestrator_durable_retry.py`
- Evidence:
  - `15 passed` on `2026-02-28`.
  - Doctrine now explicitly states `local_db` default durable backend and Temporal optional truth-in-claims:
    - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md` (Locked decisions, `7.8`, runtime defaults)
    - `/Users/mombalam/Desktop/Clearledgr.v1/README.md` (Product direction + runtime expectations)

### FS06
- Priority: `P2`
- Category: `extraction-quality`
- Status: `IN_PROGRESS`
- Problem: Benchmark score is excellent, but dataset is largely synthetic; production drift risk remains.
- Plan refs:
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:463`
  - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:475`
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_data/invoice_extraction_eval_cases.json`
  - `/Users/mombalam/Desktop/Clearledgr.v1/scripts/evaluate_invoice_extraction.py`
- Acceptance criteria:
  - Add and evaluate larger anonymized real-email set with target accuracy thresholds.
- Validation/tests:
  - `python3 /Users/mombalam/Desktop/Clearledgr.v1/scripts/evaluate_invoice_extraction.py --dataset /Users/mombalam/Desktop/Clearledgr.v1/tests/test_data/invoice_extraction_eval_cases.json --json`
- Evidence:
  - Dataset expanded to `60` cases (`tests/test_data/invoice_extraction_eval_cases.json`) and currently scores `rating=perfect` on the local parser benchmark.
  - Remaining gap: dataset metadata still marks it `synthetic_anonymized`; real anonymized production-email benchmark intake is still required.

## Change Log
- `2026-02-28`: Tracker created from full-scale assessment; seeded FS01-FS06.
- `2026-02-28`: Marked FS01/FS02 as `DONE` after doctrine/runtime hardening validation and tests.
- `2026-02-28`: Added launch evidence validator script/tests and moved FS03 to `IN_PROGRESS` with explicit L01-L04/L11 blockers.
- `2026-02-28`: Populated pilot release evidence files (manifest, parity/failure/runbook/rollback/signoff working docs) with automated baseline evidence and reduced unresolved manifest placeholders.
- `2026-02-28`: Ran authenticated Gmail runtime evidence command for release `ap-v1-2026-02-25-pilot-rc1`; artifacts were generated but run failed due unauthenticated Gmail profile (login redirect), leaving FS03/FS04 open with explicit blocker evidence.
- `2026-02-28`: Cleared profile lock/auth blocker and reran authenticated Gmail evidence successfully (`2/2 tests`); updated launch/manifests to reflect `L01` runtime evidence completion while full pilot drill remains in progress.
- `2026-02-28`: Re-ran focused automated evidence bundles for `L02/L03/L04/L11`; tracker remains open only on status/staging evidence completion.
- `2026-02-28`: Added automated AP end-to-end baseline evidence for `L01` (`6 passed`) and linked artifact into launch manifest/tracker.
- `2026-02-28`: Marked FS05 `DONE` after durability truth-in-claims doctrine updates and durable retry regression pass.
- `2026-02-28`: Moved FS04/FS06 to `IN_PROGRESS` with current evidence and remaining external/runtime-data blockers.
