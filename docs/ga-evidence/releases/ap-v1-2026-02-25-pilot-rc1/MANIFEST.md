# Release Evidence Manifest

Release ID: `ap-v1-2026-02-25-pilot-rc1`
Mode: `pilot`
Created: `2026-02-25`
Status: `finalized`
Owner: `platform-eng`

Source doctrine:
- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_READINESS_EVIDENCE_PROCESS.md`

Implementation baseline (completed):
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/PLAN_IMPLEMENTATION_GAP_TRACKER_2026-02-25_COMPLETE.md`
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/PLAN_REMAINING_GAPS_TRACKER_2026-02-25_COMPLETE.md`

## Implementation Snapshot

This manifest records pilot evidence collection on top of an implemented AP v1 codebase, not a partially built product.

- Gmail is the primary embedded operator surface with a thread workspace and Gmail-native routed pages.
- Backend Gmail autopilot, invoice validation/workflow, approval routing, audit capture, and ops/readiness APIs are implemented.
- Slack and Teams approval callbacks are implemented and covered by handler-path tests.
- ERP execution is API-primary across QuickBooks, Xero, NetSuite, and SAP, with browser fallback controls for unsupported or staged paths.

Pending items in this manifest are live-environment capture and operational proof gaps, not absence of the core AP product.

## Release Scope

- Target mode: `pilot`
- Target environment(s): `local-ci baseline complete`, `staging evidence pending`, `prod-like pending`
- Tenant scope: `default` (pilot), partner tenant assignment pending
- Channel scope:
  - Gmail: `enabled`
  - Slack: `implemented_in_code`, `staging_verify_pending`
  - Teams: `implemented_in_code`, `staging_verify_pending`
  - Browser fallback: `implemented_in_code`, `staging_verify_pending`
- ERP connector scope (enabled in this release):
  - QuickBooks: `implemented_in_code`, `sandbox_verify_pending`
  - Xero: `implemented_in_code`, `sandbox_verify_pending`
  - NetSuite: `implemented_in_code`, `sandbox_verify_pending`
  - SAP: `implemented_in_code`, `sandbox_verify_pending`

## Evidence Artifacts (Repository Pointers + External Links)

Repository-local working artifacts:
- ERP parity matrix (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ERP_PARITY_MATRIX.md`
- Failure-mode matrix (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/FAILURE_MODE_MATRIX.md`
- Runbook validations (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/RUNBOOK_VALIDATIONS.md`
- Signoffs (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/SIGNOFFS.md`
- Rollback controls verification (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ROLLBACK_CONTROLS_VERIFICATION.md`
- Canary rollout report (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/CANARY_ROLLOUT_REPORT.md`
- Observability trace walkthrough (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/OBSERVABILITY_TRACE_WALKTHROUGH.md`
- Gmail runtime E2E report:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/GMAIL_RUNTIME_E2E.md`
- Pilot E2E baseline summary:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/PILOT_E2E_EVIDENCE.md`
- Gmail runtime evidence JSON/screenshot:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/gmail-e2e-evidence.json`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/gmail-e2e-screenshot.png`
- Gmail sidebar reset evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/GMAIL_SIDEBAR_RESET_EVIDENCE.md`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/sidebar-reset-before.png`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/sidebar-reset-after-work.png`
- UI/UX hardening closure evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/UI_UX_HARDENING_CLOSURE_EVIDENCE.md`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/sidebar-work-audit-expanded.png`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/sidebar-auth-required.png`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/sidebar-reason-sheet.png`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/admin-console-setup.png`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/admin-console-ops.png`
- Launch evidence validator snapshot:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/launch-evidence-validation.json`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/launch-evidence-validation-ga.json`
- Extraction benchmark snapshot:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/extraction-benchmark-summary.json`

External artifact system-of-record links (fill in):
- ERP sandbox traces/screenshots: `pending_capture`
- Staging drill recordings: `pending_capture`
- CI/staging logs bundle: `pending_capture`
- Ticket/approval thread for signoff: `pending_capture`

## Open Accepted Risks (Pilot Only)

- None currently in `R01-R14` tracker scope (`R01-R14` all closed).
- Add pilot-only launch waivers here if failure-mode or parity coverage is deferred.

## Readiness Summary (to update)

Repository-local evidence is stronger than live-environment verification. Where broad-launch claims still require staging or sandbox proof, that is called out in the notes below and in the release scope above.

| Category | Status | Evidence link | Notes |
|---|---|---|---|
| ERP parity matrix | DONE | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ERP_PARITY_MATRIX.md` | Release-package baseline verified; live sandbox parity capture still pending for broader launch claims |
| Failure-mode matrix | DONE | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/FAILURE_MODE_MATRIX.md` | Required repository scenarios evidenced; live staging replay still pending where noted |
| Runbook validation | DONE | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/RUNBOOK_VALIDATIONS.md` | Validation records complete for release package; live-window revalidation remains part of launch execution |
| Rollback controls verification | DONE | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ROLLBACK_CONTROLS_VERIFICATION.md` | Automated local-ci verification complete; staging verification still pending |
| Gmail runtime E2E | DONE | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/GMAIL_RUNTIME_E2E.md` | Authenticated runtime evidence passed on `2026-02-28T16:16Z` |
| Pilot signoffs | DONE | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/SIGNOFFS.md` | Approvals recorded |
| Canary rollout | DONE | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/CANARY_ROLLOUT_REPORT.md` | Canary observations and decision captured |
| Correlation-ID observability trace | DONE | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/OBSERVABILITY_TRACE_WALKTHROUGH.md` | Repository-local trace walkthrough captured; live staging walkthrough still pending for broad-launch claims |

## Signoff Table (Pilot)

| Function | Approver | Date/time | Decision | Notes |
|---|---|---|---|---|
| Engineering | platform-eng | 2026-03-02 18:10 UTC | approved | Evidence package accepted |
| Product | product-owner | 2026-03-02 18:12 UTC | approved | Pilot scope accepted |
| Operations / Support | ops-lead | 2026-03-02 18:14 UTC | approved | Runbook/rollback validations accepted |
| Security (or equivalent) | security-eng | 2026-03-02 18:16 UTC | approved | Security validation accepted |

## Rollback Controls Verification Summary

- ERP posting disablement: `automated_verified` (staging pending)
- Slack/Teams action disablement: `automated_verified` (staging pending)
- Browser fallback controls: `automated_verified` (staging pending)
- Verification date/environment: `2026-02-28 local-ci` (staging pending)

## Links to Launch Tracker Items

- Launch tracker: `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_LAUNCH_READINESS_TRACKER.md`
- `L01` pilot E2E drill
- `L02` rollback controls
- `L03` pilot failure-mode subset
- `L04` durable retry restart proof
- `L05` pilot signoffs
- `L08` manifest
- `L12` observability trace walkthrough
