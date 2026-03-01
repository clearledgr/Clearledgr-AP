# Runbook Validations (Working)

Release ID: `ap-v1-2026-02-25-pilot-rc1`  
Environment: `local-ci (automated baseline), staging pending`  
Date: `2026-02-28`  
Owner: `platform-eng`  
Status: `in_progress`

## Runbook Validation Records

| Runbook | Owner | Last validated date | Environment | Result | Evidence links | Notes |
|---|---|---|---|---|---|---|
| ERP posting disablement / rollback control | platform-eng | 2026-02-28 | local-ci | PASS (automated) | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_e2e_rollback_controls.py::test_erp_posting_block_clears_and_reinstates` | Staging manual execution pending |
| Channel action disablement (Slack/Teams) | platform-eng | 2026-02-28 | local-ci | PASS (automated) | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_e2e_rollback_controls.py::test_channel_action_block_per_channel` | Staging manual execution pending |
| Browser fallback runner outage | platform-eng | 2026-02-28 | local-ci | PARTIAL | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_e2e_rollback_controls.py::test_browser_fallback_block` | Needs live runner outage drill |
| Callback verification failures (Slack/Teams) | security-eng | 2026-02-28 | local-ci | PARTIAL | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_teams_verify.py` | Teams crypto path covered; Slack live-signature drill still pending |
| Audit investigation via correlation ID | qa-eng | 2026-02-28 | local-ci | PARTIAL | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py::test_browser_evidence_is_queryable_via_audit_endpoint` | Full end-to-end correlation walkthrough pending (`L12`) |

## Automated Bundle Status

- `L02` rollback/admin controls subset: `7 passed` (`tests/test_e2e_rollback_controls.py`, `tests/test_admin_launch_controls.py`)
- `L04` durable restart/dead-letter subset: `4 passed` (`tests/test_agent_orchestrator_durable_retry.py` targeted cases)
