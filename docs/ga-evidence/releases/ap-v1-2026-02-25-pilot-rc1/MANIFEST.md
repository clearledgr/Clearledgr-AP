# Release Evidence Manifest

Release ID: `ap-v1-2026-02-25-pilot-rc1`
Mode: `pilot`
Created: `2026-02-25`
Status: `draft`
Owner: `platform-eng`

Source doctrine:
- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_READINESS_EVIDENCE_PROCESS.md`

Implementation baseline (completed):
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/PLAN_IMPLEMENTATION_GAP_TRACKER_2026-02-25_COMPLETE.md`
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/PLAN_REMAINING_GAPS_TRACKER_2026-02-25_COMPLETE.md`

## Release Scope

- Target mode: `pilot`
- Target environment(s): `local-ci baseline complete`, `staging evidence pending`, `prod-like pending`
- Tenant scope: `default` (pilot), partner tenant assignment pending
- Channel scope:
  - Gmail: `enabled`
  - Slack: `enabled_in_code`, `staging_verify_pending`
  - Teams: `enabled_in_code`, `staging_verify_pending`
  - Browser fallback: `enabled_in_code`, `staging_verify_pending`
- ERP connector scope (enabled in this release):
  - QuickBooks: `adapter_ready_in_code`, `sandbox_verify_pending`
  - Xero: `adapter_ready_in_code`, `sandbox_verify_pending`
  - NetSuite: `adapter_ready_in_code`, `sandbox_verify_pending`
  - SAP: `adapter_ready_in_code`, `sandbox_verify_pending`

## Evidence Artifacts (Repository Pointers + External Links)

Repository-local working artifacts:
- ERP parity matrix (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ERP_PARITY_MATRIX.md`
- Failure-mode matrix (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/FAILURE_MODE_MATRIX.md`
- Runbook validations (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/RUNBOOK_VALIDATIONS.md`
- Signoffs (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/SIGNOFFS.md`
- Rollback controls verification (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ROLLBACK_CONTROLS_VERIFICATION.md`
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

| Category | Status | Evidence link | Notes |
|---|---|---|---|
| ERP parity matrix | IN_PROGRESS | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ERP_PARITY_MATRIX.md` | Local-ci baseline complete; sandbox traces pending |
| Failure-mode matrix | IN_PROGRESS | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/FAILURE_MODE_MATRIX.md` | Live callback-delay and auth-expiry drills pending |
| Runbook validation | IN_PROGRESS | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/RUNBOOK_VALIDATIONS.md` | Live operator drills pending |
| Rollback controls verification | IN_PROGRESS | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ROLLBACK_CONTROLS_VERIFICATION.md` | Automated baseline complete |
| Gmail runtime E2E | DONE | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/GMAIL_RUNTIME_E2E.md` | Authenticated runtime evidence passed on `2026-02-28T16:16Z` |
| Pilot signoffs | IN_PROGRESS | `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/SIGNOFFS.md` | Waiting on L01-L04/L11 completion |

## Signoff Table (Pilot)

| Function | Approver | Date/time | Decision | Notes |
|---|---|---|---|---|
| Engineering | platform-eng | pending | pending | Awaiting staging evidence closure |
| Product | product-owner | pending | pending | Awaiting pilot scope confirmation |
| Operations / Support | ops-lead | pending | pending | Awaiting runbook live validation |
| Security (or equivalent) | security-eng | pending | pending | Awaiting callback verification evidence |

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
