# Release Evidence Manifest

Release ID: `ap-v1-2026-02-25-pilot-rc1`
Mode: `pilot`
Created: `2026-02-25`
Status: `draft`
Owner: `platform-eng` (assign named owner)

Source doctrine:
- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_READINESS_EVIDENCE_PROCESS.md`

Implementation baseline (completed):
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/PLAN_IMPLEMENTATION_GAP_TRACKER_2026-02-25_COMPLETE.md`
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/PLAN_REMAINING_GAPS_TRACKER_2026-02-25_COMPLETE.md`

## Release Scope

- Target mode: `pilot`
- Target environment(s): `staging`, `prod-like` (TBD)
- Tenant scope: `TBD`
- Channel scope:
  - Gmail: `enabled`
  - Slack: `enabled` (TBD verify)
  - Teams: `enabled` (TBD verify)
  - Browser fallback: `enabled` (TBD verify)
- ERP connector scope (enabled in this release):
  - QuickBooks: `TBD`
  - Xero: `TBD`
  - NetSuite: `TBD`
  - SAP: `TBD`

## Evidence Artifacts (Repository Pointers + External Links)

Repository-local working artifacts:
- ERP parity matrix (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ERP_PARITY_MATRIX.md`
- Failure-mode matrix (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/FAILURE_MODE_MATRIX.md`
- Runbook validations (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/RUNBOOK_VALIDATIONS.md`
- Signoffs (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/SIGNOFFS.md`
- Rollback controls verification (working): `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/ROLLBACK_CONTROLS_VERIFICATION.md`

External artifact system-of-record links (fill in):
- ERP sandbox traces/screenshots: `TBD`
- Staging drill recordings: `TBD`
- CI/staging logs bundle: `TBD`
- Ticket/approval thread for signoff: `TBD`

## Open Accepted Risks (Pilot Only)

- None currently in `R01-R14` tracker scope (`R01-R14` all closed).
- Add pilot-only launch waivers here if failure-mode or parity coverage is deferred.

## Readiness Summary (to update)

| Category | Status | Evidence link | Notes |
|---|---|---|---|
| ERP parity matrix | OPEN | TBD | |
| Failure-mode matrix | OPEN | TBD | |
| Runbook validation | OPEN | TBD | |
| Rollback controls verification | OPEN | TBD | |
| Pilot signoffs | OPEN | TBD | |

## Signoff Table (Pilot)

| Function | Approver | Date/time | Decision | Notes |
|---|---|---|---|---|
| Engineering | TBD | TBD | TBD | |
| Product | TBD | TBD | TBD | |
| Operations / Support | TBD | TBD | TBD | |
| Security (or equivalent) | TBD | TBD | TBD | |

## Rollback Controls Verification Summary

- ERP posting disablement: `TBD`
- Slack/Teams action disablement: `TBD`
- Browser fallback controls: `TBD`
- Verification date/environment: `TBD`

## Links to Launch Tracker Items

- Launch tracker: `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_LAUNCH_READINESS_TRACKER.md`
- `L01` pilot E2E drill
- `L02` rollback controls
- `L03` pilot failure-mode subset
- `L04` durable retry restart proof
- `L05` pilot signoffs
- `L08` manifest
- `L12` observability trace walkthrough
