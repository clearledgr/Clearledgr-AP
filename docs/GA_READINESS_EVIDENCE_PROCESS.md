# GA Readiness Evidence Process (AP v1)

Source doctrine: `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`

Purpose:
- Define how GA-readiness evidence is produced, stored, referenced, and approved.
- Prevent GA claims from relying only on mutable in-app metadata.
- Keep Clearledgr AP v1 evidence aligned with the embedded/agentic product doctrine.

## Scope

This process covers evidence for:
- ERP parity validation (`PLAN.md` `6.6`, `9.3`)
- Fallback policy/runbook readiness (`PLAN.md` `6.7`, `6.8`)
- Failure-mode validation (`PLAN.md` `7.7`)
- Rollback controls and launch signoff (`PLAN.md` `8.4`, `8.5`, `9.4`, `9.5`)

It does not replace:
- in-app launch control metadata (`/api/admin/ga-readiness`, `/api/admin/rollback-controls`)
- code-level tests and CI outputs

In-app metadata is the index; artifacts below are the proof.

## Artifact Locations

Repository-tracked (templates / pointers / summaries):
- `docs/ga-evidence/README.md` (index file, optional)
- `docs/ga-evidence/templates/` (checklists, parity matrix templates, signoff templates)
- `docs/ga-evidence/releases/<release_id>/` (lightweight pointers/manifests only; no secrets)

External (system of record for bulky or sensitive evidence):
- Shared drive / internal docs / ticketing system (screenshots, ERP sandbox logs, runbook drills)
- CI artifact storage (test logs, coverage reports, failure-mode runs)

Each release must have one repository manifest that links to external artifacts.

## Release ID and Naming Convention

Use a stable release identifier:
- `ap-v1-<yyyy-mm-dd>-<pilot|ga>-<tag>`

Examples:
- `ap-v1-2026-03-04-pilot-erp-parity`
- `ap-v1-2026-03-18-ga-candidate-1`

Artifact filenames should include:
- release id
- environment (`staging`, `sandbox`, `prod-like`)
- date

Example:
- `erp-parity-matrix_ap-v1-2026-03-18-ga-candidate-1_sandbox.md`

## Required Evidence by Category

### 1. ERP Parity Matrix

Required for each enabled ERP in scope:
- QuickBooks
- Xero
- NetSuite
- SAP (if enabled for the release)

Must include:
- success path (API-first)
- API failure -> browser fallback request path
- fallback completion success/failure reconciliation
- normalized ERP response contract fields (`erp_type`, `erp_reference`, `error_code`, `error_message`)
- idempotency duplicate-action/posting behavior

Evidence examples:
- test runs
- sandbox transaction screenshots/IDs
- redacted request/response traces
- operator-visible result screenshots (Gmail/Slack/Teams)

### 2. Failure-Mode Matrix

Must cover plan-listed scenarios (or explicit deferrals with accepted risk):
- callback duplication/delay
- connector auth expiry
- posting failure after approval
- browser fallback failure
- confidence gate block before posting
- restart/recovery scenarios (for enabled retry behavior)

For each scenario:
- expected behavior
- observed behavior
- evidence link
- pass/fail
- owner/follow-up if failed

### 3. Runbooks and Operational Readiness

Required runbooks (minimum):
- ERP posting disabled / rollback control activation
- Channel action disablement (Slack/Teams)
- Browser fallback runner outage
- Callback verification failures (Slack/Teams)
- Audit investigation / trace lookup using correlation ID

Each runbook record must include:
- owner
- last validated date
- validation environment
- validation result

### 4. Launch / Signoff Records

Required signoffs:
- Engineering
- Product
- Operations / Support
- Security (or equivalent approver for pilot)

Each signoff record must include:
- approver
- date/time
- release id
- explicit scope (pilot/GA + tenant/channel/ERP scope)
- blockers / accepted risks

## Accepted Risk Rules (Pilot Only)

Accepted risks are allowed only for pilot and must include:
- item ID (e.g., `R07`)
- owner
- expiration date
- rollback / feature-gate strategy
- GA closure requirement

Accepted risks must be reflected in both:
- the tracker (`PLAN_REMAINING_GAPS_TRACKER`)
- the release evidence manifest

## Repository Manifest Contract (Per Release)

Create a manifest file (lightweight, link-oriented) at:
- `docs/ga-evidence/releases/<release_id>/MANIFEST.md`

Required sections:
- Release scope
- Enabled surfaces (Gmail / Slack / Teams / browser fallback)
- Enabled ERP connectors
- Open accepted risks (pilot only)
- Links to parity matrix artifacts
- Links to failure-mode matrix artifacts
- Links to runbook validations
- Signoff table
- Rollback controls verification summary

## In-App Metadata Integration (What the app should store)

In-app GA readiness metadata should store references, not bulky proof:
- release id
- artifact manifest URL/path
- parity summary status
- signoff summary status
- rollback validation status
- last reviewed timestamp

This keeps admin surfaces useful without turning the application DB into the evidence archive.

## Review Cadence

- Pilot: before each tenant/channel/ERP expansion
- GA candidate: every release candidate
- Post-GA: every material connector/channel/runtime change

## Minimum Checklist Before Claiming “GA Ready”

- Parity matrix complete for enabled ERP set
- Failure-mode matrix complete (or pilot-only accepted risks documented)
- Runbooks validated within agreed window
- Signoffs recorded
- Rollback controls verified in staging/prod-like environment
- Tracker open items resolved or explicitly accepted-risk (pilot only)
