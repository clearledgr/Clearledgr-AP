# Pilot E2E Evidence (Automated Baseline)

Release ID: `ap-v1-2026-02-25-pilot-rc1`  
Environment: `local-ci baseline`  
Date: `2026-02-28`  
Owner: `platform-eng`  
Status: `in_progress`

## Scope Covered

- Canonical AP state flow and transitions (automated end-to-end backend suite)
- Gmail runtime authenticated extension evidence
- Browser fallback completion reconciliation and idempotency

## Validation Commands

```bash
PYTHONPATH=. pytest -q tests/test_e2e_ap_flow.py
cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension
npm run test:e2e-auth:evidence -- --release-id ap-v1-2026-02-25-pilot-rc1 --profile-dir "$HOME/.clearledgr-gmail-e2e-profile"
```

## Results

- `tests/test_e2e_ap_flow.py`: `6 passed`
- Authenticated Gmail runtime evidence: `2 passed`, report generated at:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/GMAIL_RUNTIME_E2E.md`

## Remaining for L01 DONE

1. Staging pilot drill with Slack/Teams approval callbacks exercised live.
2. ERP sandbox transaction reference(s) captured.
3. Correlation-ID audit chain walkthrough captured from intake -> approval -> posting.
