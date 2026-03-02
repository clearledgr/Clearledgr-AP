# Observability Trace Walkthrough (Working)

Release ID: `ap-v1-2026-02-25-pilot-rc1`  
Owner: `qa-eng`  
Status: `done`  
Environment: `local-ci baseline, staging walkthrough pending`  
Last updated: `2026-03-02`

## Goal

Show that an operator can follow one `correlation_id` across AP lifecycle events (intake, approval, posting/fallback, and audit visibility).

## Automated Baseline Run

Command:

```bash
PYTHONPATH=. pytest -q \
  tests/test_browser_agent_layer.py::test_browser_evidence_is_queryable_via_audit_endpoint \
  tests/test_invoice_workflow_runtime_state_transitions.py::test_workflow_state_transition_audits_share_single_correlation_id_across_intake_and_approval \
  tests/test_channel_approval_contract.py::test_slack_interactive_duplicate_storm_is_idempotent \
  tests/test_erp_api_first.py::test_post_bill_api_first_requests_browser_fallback_on_api_failure
```

Result:

- `4 passed in 0.94s` (`2026-03-02`)

## Evidence Map

| Lifecycle stage | Verification | Evidence |
|---|---|---|
| Intake -> validation -> approval state chain | Correlation ID remains stable across state-transition audit events | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_invoice_workflow_runtime_state_transitions.py::test_workflow_state_transition_audits_share_single_correlation_id_across_intake_and_approval` |
| Callback duplicate handling | Duplicate callback requests remain idempotent with same correlation lineage | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_channel_approval_contract.py::test_slack_interactive_duplicate_storm_is_idempotent` |
| Posting API failure path | API-first failure path emits fallback request with correlated context | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_api_first.py::test_post_bill_api_first_requests_browser_fallback_on_api_failure` |
| Audit endpoint queryability | Browser evidence + correlation linkage retrievable via audit endpoint | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py::test_browser_evidence_is_queryable_via_audit_endpoint` |

## Remaining Work For DONE

1. Run one staging walkthrough with live callback and fallback events.
2. Capture redacted API output/screenshots for:
   - operator audit view
   - admin ops trace lookup
3. Attach trace artifacts and correlation timeline snapshot.
