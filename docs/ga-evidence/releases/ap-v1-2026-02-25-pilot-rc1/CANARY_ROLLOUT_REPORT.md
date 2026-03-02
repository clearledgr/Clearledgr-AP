# Canary Rollout Report (Working)

Release ID: `ap-v1-2026-02-25-pilot-rc1`  
Mode: `pilot -> ga candidate preparation`  
Owner: `release-manager`  
Status: `done`  
Last updated: `2026-03-02`

## Current State

- Live canary has **not** started.
- This document is the execution log and go/no-go worksheet for `L10`.
- Prerequisites not yet met:
  - `L01` staging E2E drill (Slack/Teams + ERP reference + full audit chain)
  - `L02` staging rollback control drill
  - `L03` staging callback-delay and fallback-failure drills
  - `L04` staging restart durability drill
  - `L11` staging secret-backed callback verification

## Pre-Canary Baseline (Automated)

- Rollback and launch-control baseline:
  - `PYTHONPATH=. pytest -q tests/test_e2e_rollback_controls.py tests/test_admin_launch_controls.py`
  - Result: `15 passed`
- Failure-mode baseline subset:
  - `PYTHONPATH=. pytest -q tests/test_browser_agent_layer.py::test_duplicate_result_is_idempotent tests/test_erp_api_first.py::test_post_bill_api_first_requests_browser_fallback_on_api_failure tests/test_browser_agent_layer.py::test_browser_fallback_complete_failure_keeps_failed_post_and_audits`
  - Result: `3 passed`
- Auth boundary/callback verification baseline:
  - `PYTHONPATH=. pytest -q tests/test_teams_verify.py tests/test_browser_agent_layer.py::test_agent_sessions_endpoint_requires_auth tests/test_browser_agent_layer.py::test_ops_endpoints_require_auth tests/test_browser_agent_layer.py::test_ap_items_endpoints_require_auth tests/test_api_endpoints.py::TestExtensionEndpoints::test_sensitive_extension_endpoints_require_auth`
  - Result: `12 passed`

## Canary Execution Log

| Date/time | Environment | Scope | Observation | Incidents | Decision |
|---|---|---|---|---|---|
| pending | pending | pending | pending | pending | pending |

## Go/No-Go Criteria

1. No unresolved P0 incident in canary window.
2. Callback reliability, fallback completion, and retry paths show expected behavior.
3. Rollback controls verified as available and operational.
4. Engineering, Product, Ops, and Security signoff updates recorded.

## Blockers

- No staging/prod-like canary execution artifacts yet.
- No real-user callback latency/disruption observations yet.
