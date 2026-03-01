# ERP Parity Matrix (Working)

Release ID: `ap-v1-2026-02-25-pilot-rc1`  
Environment: `local-ci (automated baseline), staging pending`  
Date: `2026-02-28`  
Owner: `platform-eng`  
Status: `in_progress`

## Automated Baseline

- Command:
  - `PYTHONPATH=. pytest -q tests/test_erp_api_first.py`
- Result:
  - `9 passed` (contract-level API-first + fallback + idempotency coverage)
- Additional browser fallback integrity coverage:
  - `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py::test_browser_fallback_complete_success_finalizes_ap_item_and_is_idempotent tests/test_browser_agent_layer.py::test_browser_fallback_complete_failure_keeps_failed_post_and_audits tests/test_browser_agent_layer.py::test_browser_fallback_full_e2e_api_fail_to_posted_to_erp`
  - `3 passed`

## Matrix

| ERP | API-first success | API fail -> fallback request | Fallback completion success | Fallback completion failure | Canonical response fields verified | Idempotency verified | Evidence links | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|
| QuickBooks | PASS (contract-level) | PASS | PASS | PASS | PASS | PASS | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_api_first.py`, `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py` | PARTIAL | Live sandbox posting evidence pending |
| Xero | PASS (contract-level) | PASS | PASS | PASS | PASS | PASS | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_api_first.py`, `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py` | PARTIAL | Live sandbox posting evidence pending |
| NetSuite | PASS (contract-level) | PASS | PASS | PASS | PASS | PASS | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_api_first.py`, `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py` | PARTIAL | Live sandbox posting evidence pending |
| SAP | PASS (contract-level) | PASS | PASS | PASS | PASS | PASS | `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_api_first.py`, `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py` | PARTIAL | Live sandbox posting evidence pending |

## Remaining To Close

1. Run staging/sandbox posting for each enabled connector and record external references.
2. Attach redacted request/response traces per connector.
3. Promote status to `done` only after reviewer signoff.
