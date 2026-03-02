# Failure-Mode Matrix (Working)

Release ID: `ap-v1-2026-02-25-pilot-rc1`  
Environment: `local-ci (automated baseline), staging pending`  
Date: `2026-02-28`  
Owner: `qa-eng`  
Status: `done`

## Automated Validation Bundle (latest)

- Command:
  - `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py::test_duplicate_result_is_idempotent tests/test_erp_api_first.py::test_post_bill_api_first_requests_browser_fallback_on_api_failure tests/test_browser_agent_layer.py::test_browser_fallback_complete_failure_keeps_failed_post_and_audits`
- Result:
  - `3 passed` on `2026-02-28`

## Matrix

| Scenario | Expected behavior | Observed behavior | Evidence links | Pass/Fail | Follow-up owner | Follow-up item | Notes |
|---|---|---|---|---|---|---|---|
| Callback duplication | Duplicate callback/action is idempotent and auditable | Duplicate result callbacks are idempotent in browser-agent channel path | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py::test_duplicate_result_is_idempotent` | PASS (channel-path) | qa-eng | L03 | Slack/Teams live callback duplication still pending staging evidence |
| Callback delay | Delayed callback does not create illegal transitions or duplicate posting | Not yet validated in staging callback runtime | `TBD` | OPEN | qa-eng | L03 | Requires live callback timing drill |
| ERP auth expiry | Connector auth degradation is surfaced and handled safely | Not verifiable from automated suite as a full live expiry drill | `TBD` | OPEN | platform-eng | L09 | Requires sandbox token-expiry scenario |
| Posting failure after approval | Item moves to failure path with clear recovery options | API-failure and fallback request behavior covered in automated tests | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_api_first.py::test_post_bill_api_first_requests_browser_fallback_on_api_failure` | PASS | qa-eng | L03 | Staging confirmation pending |
| Browser fallback failure | Failure remains auditable and operator-visible in `failed_post` | Automated tests confirm failure remains in failed state with audit event | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py::test_browser_fallback_complete_failure_keeps_failed_post_and_audits` | PASS | qa-eng | L03 | Staging confirmation pending |
| Confidence gate block before posting | Low-confidence invoice requires manual intervention/escalation | Decision-layer tests cover low-confidence escalation behavior | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_decision.py::test_fallback_low_confidence` | PASS (decision-layer) | qa-eng | L09 | Needs full staging scenario with operator flow |
| Restart during retry/fallback | Durable retry jobs persist and resume after restart | Durable retry and post-process restart coverage passes in automated suite | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_agent_orchestrator_durable_retry.py::test_durable_retry_job_survives_restart_and_posts_to_erp`, `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_agent_orchestrator_durable_retry.py::test_durable_post_process_job_survives_restart_and_completes` | PASS | platform-eng | L04 | Staging restart drill still required for pilot evidence |

## Waivers (Pilot Only)

| Scenario | Reason | Approver | Expiration | Rollback/mitigation |
|---|---|---|---|---|
| Callback delay live-drill | Staging exercise not yet run | Pending | Pending | Keep rollback controls ready; block GA until closed |
| ERP auth expiry live-drill | Sandbox expiry simulation not yet run | Pending | Pending | Connector disablement runbook + alerting |
