# GA Launch Readiness Tracker (Execution Phase)

Date created: 2026-02-25
Source doctrine: `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
Evidence process: `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_READINESS_EVIDENCE_PROCESS.md`

Implementation baseline (completed and archived):
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/PLAN_IMPLEMENTATION_GAP_TRACKER_2026-02-25_COMPLETE.md`
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/PLAN_REMAINING_GAPS_TRACKER_2026-02-25_COMPLETE.md`

Latest validation baseline:
- AP v1 regression slice + durable retry tests: `114 passed` (recorded in remaining-gaps tracker archive)

## Scope and Doctrine Guardrails

This tracker is for launch execution and evidence collection after implementation remediation.
It does not replace the archived implementation trackers.

Non-negotiable product/doctrine guardrails:
- Preserve Clearledgr AP v1 as an embedded, agentic finance execution layer.
- Gmail remains the primary operator surface.
- Slack and Teams remain approval/decision surfaces.
- ERP remains the system of record.
- Preserve browser-agent policy/preview/confirmation/audit semantics.
- Harden operations and prove readiness; do not de-scope agentic behavior into a generic automation platform.
- Any temporary disablement must be via rollout/rollback controls, not code removal.

## Tracker Usage Rules

Statuses:
- `OPEN`
- `IN_PROGRESS`
- `BLOCKED`
- `DONE`
- `WAIVED` (must include approver + rationale + expiration/review date)

`DONE` requires:
- evidence artifact link/path recorded
- validation result recorded
- owner + date updated

## Status Summary Table

| Priority | Total | OPEN | IN_PROGRESS | BLOCKED | DONE | WAIVED |
|---|---:|---:|---:|---:|---:|---:|
| P0 | 5 | 4 | 1 | 0 | 0 | 0 |
| P1 | 7 | 6 | 1 | 0 | 0 | 0 |
| P2 | 4 | 3 | 0 | 0 | 1 | 0 |
| **All** | **16** | **13** | **2** | **0** | **1** | **0** |

## Release Context

- Target release id: `ap-v1-2026-02-25-pilot-rc1` (format: `ap-v1-<yyyy-mm-dd>-<pilot|ga>-<tag>`)
- Current target mode: `pilot` (switch to `ga` when signoff scope expands)
- Enabled surfaces in scope:
  - Gmail: `TBD`
  - Slack: `TBD`
  - Teams: `TBD`
  - Browser fallback: `TBD`
- Enabled ERP connectors in scope:
  - QuickBooks: `TBD`
  - Xero: `TBD`
  - NetSuite: `TBD`
  - SAP: `TBD`

## Milestone Checklist

### Pilot Milestone
- [ ] Staging E2E pilot drill completed (Gmail -> approval -> ERP -> audit)
- [ ] Rollback controls verified in staging/prod-like
- [ ] Failure-mode matrix completed for pilot scope (or explicit waivers)
- [ ] Browser fallback success/failure + completion reconciliation evidence captured
- [ ] Durable retry restart-recovery evidence captured
- [ ] Pilot signoffs recorded (Eng/Product/Ops/Security-equivalent)
- [x] Release manifest created and linked

### GA Candidate Milestone
- [ ] ERP parity matrix complete for enabled ERP set
- [ ] Failure-mode matrix complete (GA scope)
- [ ] Runbooks validated within agreed window
- [ ] GA signoffs recorded
- [ ] Canary rollout observations captured and reviewed
- [ ] Release manifest finalized and linked

## Launch Execution Items (Lxx)

Schema per item:
- `ID`, `Category`, `Priority`, `Plan refs`, `Status`
- `Goal`
- `Owner`
- `Evidence required`
- `Validation / success criteria`
- `Artifact links`
- `Notes / blockers`

### L01
- ID: `L01`
- Category: `pilot-e2e`
- Priority: `P0`
- Plan refs: `PLAN.md` `4.1`, `4.6`, `5.1`, `5.2`, `5.3`, `7.4`
- Status: `IN_PROGRESS`
- Goal: Run a staging E2E pilot drill covering Gmail intake -> Slack/Teams approval -> ERP posting -> audit verification.
- Owner: `TBD`
- Evidence required:
  - redacted screenshots or recordings for Gmail + Slack/Teams flows
  - ERP sandbox transaction reference
  - AP audit event trace with correlation ID
- Validation / success criteria:
  - canonical AP states observed in expected order
  - approval callback handled once (duplicate-safe)
  - ERP result visible to operator and persisted
  - audit chain is queryable by AP item
- Artifact links:
  - runtime evidence report: `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/<release_id>/GMAIL_RUNTIME_E2E.md`
  - evidence json: `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/<release_id>/artifacts/gmail-e2e-evidence.json`
- Notes / blockers:
  - use `npm run test:e2e-auth:evidence -- --release-id <release_id>` from `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension`
  - prioritize one tenant and one ERP first, then expand

### L02
- ID: `L02`
- Category: `rollback-controls`
- Priority: `P0`
- Plan refs: `PLAN.md` `8.4`, `8.5`, `9.4`
- Status: `OPEN`
- Goal: Validate rollback controls in staging/prod-like (ERP posting disablement, channel action disablement, fallback controls).
- Owner: `TBD`
- Evidence required:
  - admin control screenshots
  - request/response traces (redacted)
  - proof of blocked behavior + operator-safe messaging
- Validation / success criteria:
  - controls take effect without deploy
  - blocked actions are audited
  - rollback activation and recovery runbook steps succeed
- Artifact links:
  - manifest section: `TBD`
- Notes / blockers:
  - use same tenant as L01 to reduce setup overhead

### L03
- ID: `L03`
- Category: `failure-mode`
- Priority: `P0`
- Plan refs: `PLAN.md` `7.7`
- Status: `OPEN`
- Goal: Execute pilot failure-mode matrix for callback duplication/delay, posting failure after approval, and browser fallback failure.
- Owner: `TBD`
- Evidence required:
  - scenario matrix with expected vs observed
  - logs/audit traces per scenario
  - pass/fail and follow-up actions
- Validation / success criteria:
  - duplicate callbacks are idempotent
  - posting failure lands in `failed_post` and recovery path is clear
  - fallback failure remains auditable and operator-visible
- Artifact links:
  - matrix: `TBD`
- Notes / blockers:
  - start with the 3 highest-value scenarios and expand in L09

### L04
- ID: `L04`
- Category: `durability-proof`
- Priority: `P0`
- Plan refs: `PLAN.md` `7.6`, `7.7`
- Status: `OPEN`
- Goal: Prove durable retry behavior survives restart in staging/prod-like conditions.
- Owner: `TBD`
- Evidence required:
  - pre-restart queued retry job evidence
  - restart timestamp/log
  - post-restart retry processing outcome
- Validation / success criteria:
  - retry job persists across restart
  - retry processing resumes without manual DB edits
  - AP state/audit trail remains consistent
- Artifact links:
  - scenario evidence: `TBD`
- Notes / blockers:
  - align with L03 posting-failure scenario for reuse

### L05
- ID: `L05`
- Category: `signoff`
- Priority: `P0`
- Plan refs: `PLAN.md` `9.4`, `9.5`
- Status: `OPEN`
- Goal: Collect pilot signoffs (Engineering, Product, Operations/Support, Security-equivalent).
- Owner: `TBD`
- Evidence required:
  - signed/approved release signoff record with scope and blockers/accepted risks
- Validation / success criteria:
  - all required pilot approvers recorded with release id and scope
- Artifact links:
  - signoff doc: `TBD`
- Notes / blockers:
  - release id must be set before final signoff

### L06
- ID: `L06`
- Category: `erp-parity`
- Priority: `P1`
- Plan refs: `PLAN.md` `6.6`, `9.3`
- Status: `OPEN`
- Goal: Build ERP parity matrix for all enabled ERP connectors in the release scope.
- Owner: `TBD`
- Evidence required:
  - parity matrix per enabled ERP
  - normalized response contract proof (`erp_type`, `erp_reference`, `error_code`, `error_message`)
  - API-first and fallback path evidence as applicable
- Validation / success criteria:
  - each enabled ERP has pass/fail status with evidence links
  - connector-specific behavior does not leak into operator-facing contracts
- Artifact links:
  - parity matrix: `TBD`
- Notes / blockers:
  - can scope to enabled connectors for pilot, full enabled set for GA

### L07
- ID: `L07`
- Category: `runbooks`
- Priority: `P1`
- Plan refs: `PLAN.md` `6.8`, `7.6`
- Status: `OPEN`
- Goal: Validate runbooks (ERP disablement, Slack/Teams action disablement, browser runner outage, callback verification failures, correlation-ID audit investigation).
- Owner: `TBD`
- Evidence required:
  - runbook validation records (owner/date/env/result)
  - screenshots/logs proving each procedure works
- Validation / success criteria:
  - required runbooks validated within target window
  - operators can execute trace lookup using correlation ID
- Artifact links:
  - runbook validation index: `TBD`
- Notes / blockers:
  - coordinate with Ops/Support schedule

### L08
- ID: `L08`
- Category: `manifest`
- Priority: `P1`
- Plan refs: `PLAN.md` `8.4`, `9.4`, `9.5`
- Status: `IN_PROGRESS`
- Goal: Create and maintain the repository release evidence manifest for the target release.
- Owner: `platform-eng` (temporary; assign named owner)
- Evidence required:
  - `docs/ga-evidence/releases/<release_id>/MANIFEST.md`
  - links to all external evidence artifacts
- Validation / success criteria:
  - manifest contains release scope, enabled surfaces/connectors, signoff table, rollback summary, accepted risks (pilot only)
- Artifact links:
  - manifest path: `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/MANIFEST.md`
- Notes / blockers:
  - Release id assigned and manifest scaffold created on `2026-02-25`; remaining work is evidence population and external link curation.

### L09
- ID: `L09`
- Category: `failure-mode`
- Priority: `P1`
- Plan refs: `PLAN.md` `7.7`
- Status: `OPEN`
- Goal: Expand failure-mode matrix to full GA scenario set (connector auth expiry, delayed callbacks, late confidence-gate block, restart during fallback/retry, etc.).
- Owner: `TBD`
- Evidence required:
  - complete matrix with expected/observed/evidence/follow-up
- Validation / success criteria:
  - all plan-listed scenarios covered or explicitly waived with approval
- Artifact links:
  - matrix: `TBD`
- Notes / blockers:
  - depends on pilot L03 patterns and templates

### L10
- ID: `L10`
- Category: `canary`
- Priority: `P1`
- Plan refs: `PLAN.md` `8.4`, `8.5`, `9.4`
- Status: `OPEN`
- Goal: Run canary rollout and capture first-live observations for callback reliability, fallback completions, retry jobs, and operator outcomes.
- Owner: `TBD`
- Evidence required:
  - canary run log
  - metrics snapshots
  - incident/near-miss notes
- Validation / success criteria:
  - no unresolved P0 incidents
  - rollback path tested or confirmed available
  - go/no-go decision documented
- Artifact links:
  - canary report: `TBD`
- Notes / blockers:
  - only after L01-L08 are materially complete

### L11
- ID: `L11`
- Category: `security-validation`
- Priority: `P1`
- Plan refs: `PLAN.md` `7.3`, `7.4`, `7.7`
- Status: `OPEN`
- Goal: Validate callback verification and auth-boundary behavior in staging with real secrets/config (Slack signing secret, Teams token verification, API/JWT auth).
- Owner: `TBD`
- Evidence required:
  - staging config verification checklist
  - positive/negative callback verification evidence
  - unauthorized access rejection evidence
- Validation / success criteria:
  - real callback verification succeeds
  - invalid signatures/tokens are rejected and audited
  - protected app surfaces reject unauthenticated requests
- Artifact links:
  - security validation record: `TBD`
- Notes / blockers:
  - requires staging secret provisioning

### L12
- ID: `L12`
- Category: `observability`
- Priority: `P1`
- Plan refs: `PLAN.md` `7.6`
- Status: `OPEN`
- Goal: Validate operator observability path (correlation ID trace from intake -> approval -> ERP/fallback -> audit).
- Owner: `TBD`
- Evidence required:
  - one or more trace walkthroughs using correlation ID
  - screenshots/API outputs for audit and ops surfaces
- Validation / success criteria:
  - operator can reconstruct lifecycle with one correlation ID
  - callback/fallback degradation signals are visible
- Artifact links:
  - trace walkthrough: `TBD`
- Notes / blockers:
  - pair with L01/L03 scenarios

### L13
- ID: `L13`
- Category: `deployment`
- Priority: `P2`
- Plan refs: `PLAN.md` `8.4`, `8.5`
- Status: `OPEN`
- Goal: Freeze pilot/GA deployment config and produce a release configuration checklist (env vars, trust modes, feature gates, rollback defaults).
- Owner: `TBD`
- Evidence required:
  - config checklist
  - approved env var set per environment (redacted)
- Validation / success criteria:
  - no undocumented required env vars
  - safe defaults confirmed for production-like environments
- Artifact links:
  - config checklist: `TBD`
- Notes / blockers:
  - align with infra owner

### L14
- ID: `L14`
- Category: `evidence-process`
- Priority: `P2`
- Plan refs: `PLAN.md` `9.4`, `9.5`
- Status: `DONE`
- Goal: Create release evidence directory structure and templates for the target release.
- Owner: `platform-eng` (temporary; assign named owner)
- Evidence required:
  - release folder scaffold under `docs/ga-evidence/releases/<release_id>/`
  - manifest and template pointers
- Validation / success criteria:
  - repo manifest exists and links to external artifacts
- Artifact links:
  - release folder: `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/`
  - templates: `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/templates/`
- Notes / blockers:
  - Completed `2026-02-25`: release scaffold + templates + manifest placeholders created.

### L15
- ID: `L15`
- Category: `signoff`
- Priority: `P2`
- Plan refs: `PLAN.md` `9.4`, `9.5`
- Status: `OPEN`
- Goal: Prepare GA signoff packet template (scope, evidence summary, accepted risks, rollback summary, go/no-go decision).
- Owner: `TBD`
- Evidence required:
  - signoff packet template path
  - approver mapping
- Validation / success criteria:
  - template covers all required signoff fields from evidence process
- Artifact links:
  - signoff template: `TBD`
- Notes / blockers:
  - can start before release id is finalized

### L16
- ID: `L16`
- Category: `post-launch`
- Priority: `P2`
- Plan refs: `PLAN.md` `7.6`, `8.5`
- Status: `OPEN`
- Goal: Define first 2-week post-launch monitoring/report cadence and ownership for incidents, fallback usage, retry behavior, and approval-channel reliability.
- Owner: `TBD`
- Evidence required:
  - monitoring/reporting schedule
  - owner/on-call mapping
- Validation / success criteria:
  - cadence agreed before canary/GA rollout
- Artifact links:
  - monitoring plan: `TBD`
- Notes / blockers:
  - coordinate with Ops/Support and Eng

## Artifact Registry (Release-Level)

Fill these as artifacts are created for the selected release id.

- Release manifest: `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/MANIFEST.md`
- ERP parity matrix: `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ERP_PARITY_MATRIX.md`
- Failure-mode matrix: `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/FAILURE_MODE_MATRIX.md`
- Runbook validation index: `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/RUNBOOK_VALIDATIONS.md`
- Signoff record: `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/SIGNOFFS.md`
- Canary report: `TBD`
- Rollback controls verification record: `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ROLLBACK_CONTROLS_VERIFICATION.md`

## Immediate Next Actions (recommended)

1. Assign a named human owner for `L08` (manifest) and confirm final pilot scope (tenant + ERP connectors).
2. Run the first staging drill bundle:
   - `L03` scenario subset (posting failure after approval, browser fallback success/failure, restart during retry)
   - `L04` durable retry restart proof
   - `L12` correlation-ID trace walkthrough
3. Record evidence links in the manifest and mark `L01`, `L03`, `L04`, `L12` statuses accordingly.

## Change Log

- `2026-02-25`: Created launch execution tracker seeded from `GA_READINESS_EVIDENCE_PROCESS.md` after archiving completed implementation/remediation trackers.
- `2026-02-25`: Created `docs/ga-evidence/` templates and seeded pilot release scaffold + manifest for `ap-v1-2026-02-25-pilot-rc1`; updated `L08` to `IN_PROGRESS` and `L14` to `DONE`.
- `2026-02-27`: Added a deterministic real-browser Gmail sidebar harness at `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-layer.browser-harness.test.cjs` with script `npm run test:browser-harness`; this strengthens browser-runtime UI wiring confidence while live authenticated Gmail E2E remains tracked under `L01`.
- `2026-02-27`: Extended `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-layer.e2e-smoke.test.cjs` with optional evidence JSON output (`GMAIL_E2E_EVIDENCE_JSON`) so manual Gmail smoke/auth runs produce auditable artifacts for pilot/GA evidence collection.
- `2026-02-27`: Removed non-durable AP post-processing fallback in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/agent_orchestrator.py` so post-processing is durable-queue-only (or explicitly gated/audited) and updated regression coverage in `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_agent_orchestrator_durable_retry.py`.
- `2026-02-27`: Enforced strict AP-v1 runtime surface pruning in `/Users/mombalam/Desktop/Clearledgr.v1/main.py` (legacy route families are no longer mounted when strict profile is active), with verification in `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_runtime_surface_scope.py`.
- `2026-02-28`: Added authenticated Gmail runtime evidence wrapper + validation pipeline (`npm run test:e2e-auth:evidence`) with normalized report output at `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/<release_id>/GMAIL_RUNTIME_E2E.md`; moved `L01` to `IN_PROGRESS` pending live staging run artifact capture.

## Archive Protocol

When pilot/GA launch execution for the tracked release is complete:
1. Freeze this tracker with final statuses, artifact links, and validation outcomes.
2. Archive to `docs/archive/GA_LAUNCH_READINESS_TRACKER_<release_id>_<date>_<status>.md`.
3. Replace this file with a pointer stub containing archive path and checksum (same pattern as plan trackers).
