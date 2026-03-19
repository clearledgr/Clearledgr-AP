# Staging Drill Runbook — Clearledgr AP v1

Step-by-step guide for running a live staging drill with real Slack, ERP, and Gmail connectors. Designed for a 1-hour session with an internal operator or design partner.

**Automated coverage that runs before this drill:**
```bash
pytest tests/test_e2e_ap_flow.py tests/test_e2e_rollback_controls.py tests/test_admin_launch_controls.py -v
```
All 13 tests must be green before proceeding to live staging.

---

## 1. Prerequisites

Before starting, confirm all of the following:

| Item | Check |
|---|---|
| Staging backend running or deployed | `GET /health` returns `{"status": "ok"}` |
| `WORKSPACE_SHELL_ENABLED=true` | Workspace shell endpoints reachable |
| Slack workspace + bot token configured | `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET` set |
| Slack test channel created | e.g. `#ap-staging-test` |
| ERP sandbox access | NetSuite/Xero/QuickBooks sandbox, or `MOCK_ERP_ENABLED=true` |
| Gmail extension installed | Chrome extension loaded in staging profile |
| Backend URL configured in extension | `queue-manager.js` → `backendUrl` points to staging |
| Admin user created | `POST /api/auth/register` with role `owner` |

### 1b. Gmail runtime E2E preflight (evidence pipeline)

Before manual drill execution, run the authenticated Gmail runtime evidence command:

```bash
cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension
npm run test:e2e-auth:evidence -- --release-id ap-v1-2026-02-25-pilot-rc1
```

Expected artifacts:
- `docs/ga-evidence/releases/<release_id>/artifacts/gmail-e2e-evidence.json`
- `docs/ga-evidence/releases/<release_id>/artifacts/gmail-e2e-screenshot.png`
- `docs/ga-evidence/releases/<release_id>/GMAIL_RUNTIME_E2E.md`

If this command fails, do not mark L01 complete; capture failure cause and rerun after fixing browser/profile/auth prerequisites.

---

## 2. Setup Phase

### 2a. Bootstrap check

```bash
curl -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/workspace/bootstrap?organization_id=default"
```

Expected: all integrations show `"status": "connected"` or `"status": "configured"`. Note any `"status": "missing"` entries — those must be resolved before drill.

### 2b. Configure Slack channel

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"channel": "#ap-staging-test", "organization_id": "default"}' \
  "$BASE_URL/api/workspace/integrations/slack/channel"
```

### 2c. Send test Slack message

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"channel": "#ap-staging-test", "organization_id": "default"}' \
  "$BASE_URL/api/workspace/integrations/slack/test"
```

Verify: message appears in `#ap-staging-test`. If not, fix `SLACK_BOT_TOKEN` and retry.

---

## 3. Drill 1 — Happy Path (10 min)

**Goal:** Prove the end-to-end flow from email to ERP post.

**Steps:**

1. **Send test invoice email** to the Gmail account being monitored.
   - Use subject: `Invoice INV-DRILL-001 from Acme Industrial Supply`
   - Include: vendor name, amount ($4,250.00), invoice number, due date (2 weeks out)

2. **Open Gmail extension sidebar.** Wait up to 60 seconds for the item to appear in the worklist (Gmail watch or polling interval).

3. **Verify extraction:**
   - Vendor = "Acme Industrial Supply" (or normalised form)
   - Amount = $4,250.00
   - Invoice number populated
   - Confidence > 80% (ideally > 95%)
   - No exception banner shown (or exception is expected and documented)

4. **Click "Approve & Post"** in the sidebar.
   - If confidence < 95%: justification prompt appears — provide "Staging drill approval"
   - Confirm action

5. **Verify Slack notification** arrives in `#ap-staging-test` with vendor + amount + status.

6. **Check AP item state:**
   ```bash
   curl -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/workspace/health?organization_id=default"
   ```
   Expected: `pending_approval_count` decremented, `posted_today` incremented.

7. **Verify ERP reference:**
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     "$BASE_URL/api/workspace/health?organization_id=default"
   ```
   Or check the worklist — item state = `posted_to_erp`, `erp_reference` populated.

**Exit criteria:** Item reaches `posted_to_erp` with non-empty `erp_reference`. ✓

---

## 4. Drill 2 — needs_info / Vendor Draft Reply (5 min)

**Goal:** Prove the `needs_info` path and the "Draft vendor reply" shortcut.

**Steps:**

1. **Send test invoice email** from a vendor configured to require a PO number, but **omit the PO number** from the email.

2. **Open Gmail extension sidebar.** Verify item appears with state `needs_info` and exception banner (e.g. "PO reference required for this vendor/category").

3. **Verify "Draft vendor reply" button** is visible in the sidebar action row.

4. **Click "Draft vendor reply":**
   - Gmail compose window should open with:
     - `To`: vendor sender email
     - `Subject`: `Re: <original subject>`
     - `Body`: pre-filled with vendor name, invoice number, and reason

5. **Provide PO number** via the sidebar (edit the PO field → save correction).

6. **Resubmit**: item transitions back to `validated` → `needs_approval`.

7. **Approve** the item → verify it reaches `posted_to_erp`.

**Exit criteria:** "Draft vendor reply" opened correctly; item resubmitted and posted. ✓

---

## 5. Drill 3 — Rollback Controls (5 min)

**Goal:** Prove rollback kill-switches actually block live operations.

**Steps:**

1. **Disable ERP posting:**
   ```bash
   curl -X PUT -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "organization_id": "default",
       "controls": {
         "erp_posting_disabled": true,
         "reason": "staging_drill_kill_switch_test"
       }
     }' "$BASE_URL/api/workspace/rollback-controls"
   ```

2. **Try to approve an invoice** via the Gmail sidebar (or Slack button).
   Expected: approval fails with a `erp_posting_disabled` error. Invoice stays in `needs_approval`.

3. **Verify health endpoint reflects the block:**
   ```bash
   curl -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/workspace/health?organization_id=default"
   ```
   Confirm `launch_controls.rollback_controls.erp_posting_disabled = true`.

4. **Re-enable:**
   ```bash
   curl -X PUT -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "organization_id": "default",
       "controls": {"erp_posting_disabled": false}
     }' "$BASE_URL/api/workspace/rollback-controls"
   ```

5. **Retry approval** → should succeed → `posted_to_erp`.

**Exit criteria:** Kill-switch blocked, re-enable restored, item posted. ✓

---

## 6. Drill 4 — ERP Failure → Browser Fallback (optional, 10 min)

*Skip if browser agent is not configured for staging.*

**Goal:** Prove the ERP-fail → browser fallback → posted path.

**Steps:**

1. Set `AP_ERP_MOCK_FAIL=true` (or equivalent) to force API failure.

2. **Approve an invoice.** Expect item transitions to `failed_post` with `last_error`.

3. **Verify browser agent session created:**
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     "$BASE_URL/api/agent/sessions?organization_id=default"
   ```
   Confirm a session exists for the failed item.

4. **Execute browser macro** via the workspace shell or session API.

5. **Submit fallback result** with `erp_reference`.

6. **Verify item** transitions to `posted_to_erp` with `erp_reference` from fallback.

**Exit criteria:** Fallback flow completed, `posted_to_erp` state reached. ✓

---

## 7. Drill 5 — Cross-Tenant Isolation (3 min)

**Goal:** Prove org boundaries are enforced.

**Steps:**

1. **Create a second org:**
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"organization_id": "staging-org-b", "name": "Staging Org B"}' \
     "$BASE_URL/api/workspace/org/settings"
   ```

2. **Get the worklist for org-B:**
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     "$BASE_URL/extension/worklist?organization_id=staging-org-b"
   ```
   Expected: empty worklist (no items from `default` org leak across).

3. **Try to approve** a `default` org item with an `org-B` token (if supported by your test setup).
   Expected: 403 `org_mismatch`.

**Exit criteria:** No cross-tenant item leakage observed. ✓

---

## 8. Monitoring Check (2 min)

Run after all drills complete.

```bash
# Overall monitoring thresholds
curl -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/ops/monitoring-thresholds?organization_id=default&window_hours=1"

# Retry queue — should be empty
curl -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/ops/retry-queue?organization_id=default"

# Extraction quality
curl -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/ops/extraction-quality?organization_id=default&window_hours=1"
```

**Expected outcomes:**
- `alert_count = 0` (or only expected warning from drill 3 rollback test)
- Retry queue depth = 0
- `correction_rate_pct < 10%`

---

## 9. GA Readiness Sign-Off

After all drills pass, record evidence and sign off:

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "organization_id": "default",
    "evidence": {
      "connector_checklists": {
        "netsuite": {"completed": true, "signed_off": true}
      },
      "runbooks": [
        {"name": "AP Posting Rollback", "url": "https://internal.example.com/runbooks/ap-rollback"},
        {"name": "Staging Drill Runbook", "url": "https://internal.example.com/runbooks/staging-drill"}
      ],
      "parity_evidence": [
        {"surface": "slack", "artifact": "staging_drill_slack_screenshots.zip"},
        {"surface": "gmail", "artifact": "staging_drill_gmail_card_screenshots.zip"}
      ],
      "signoffs": [
        {"role": "engineering", "signed_by": "eng-lead@example.com", "signed_at": "<ISO timestamp>"},
        {"role": "operations", "signed_by": "ops-lead@example.com", "signed_at": "<ISO timestamp>"}
      ],
      "notes": ["Staging drill completed. All 5 drills passed. Monitoring thresholds green."]
    }
  }' "$BASE_URL/api/workspace/ga-readiness"
```

Verify response: `"summary": {"ready_for_ga": true}`.

Validate launch evidence tracker completeness:

```bash
python3 /Users/mombalam/Desktop/Clearledgr.v1/scripts/validate_launch_evidence.py --mode pilot --json
```

Expected for pilot readiness: `"passed": true` (or explicit accepted-risk entries with owner/expiry captured in manifest + tracker).

---

## 10. Exit Criteria Checklist

Drill is complete when ALL of these are checked:

- [ ] **Drill 1** — Happy path invoice reached `posted_to_erp` with `erp_reference` populated
- [ ] **Drill 2** — `needs_info` item: "Draft vendor reply" opened correctly; item resubmitted and posted
- [ ] **Drill 3** — Rollback kill-switch blocked ERP post; re-enable restored flow
- [ ] **Drill 5** — No cross-tenant item leakage observed
- [ ] **Monitoring** — `GET /api/ops/monitoring-thresholds` returned `alert_count = 0`
- [ ] **GA Readiness** — `GET /api/workspace/ga-readiness` returns `summary.ready_for_ga = true`

When all boxes are checked, the system is cleared for GA launch.
