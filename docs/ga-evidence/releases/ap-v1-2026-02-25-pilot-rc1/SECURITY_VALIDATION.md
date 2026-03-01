# Security Validation (Working)

Release ID: `ap-v1-2026-02-25-pilot-rc1`  
Environment: `local-ci baseline, staging pending`  
Owner: `security-eng`  
Status: `in_progress`

## Automated Baseline

- Command:
  - `PYTHONPATH=. pytest -q tests/test_teams_verify.py`
  - `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py::test_agent_sessions_endpoint_requires_auth tests/test_browser_agent_layer.py::test_ops_endpoints_require_auth tests/test_browser_agent_layer.py::test_ap_items_endpoints_require_auth tests/test_api_endpoints.py::TestExtensionEndpoints::test_sensitive_extension_endpoints_require_auth tests/test_teams_verify.py`
- Result:
  - `8 passed` (Teams verifier crypto-path)
  - `12 passed` (auth boundary + Teams verification subset)
- Notes:
  - Teams verification crypto-path has direct branch coverage.
  - Protected `/api/agent/*`, `/api/ops/*`, `/api/ap/items/*`, and sensitive `/extension/*` auth checks are validated in automated tests.
  - Staging secret-backed callback validation for Slack/Teams is still required.

## Pending Staging Checks

1. Valid Slack signature callback accepted.
2. Invalid Slack signature rejected and audited.
3. Valid Teams token accepted in staging config.
4. Invalid/malformed Teams token rejected and audited.
5. Unauthenticated protected endpoints return `401` in staging.
