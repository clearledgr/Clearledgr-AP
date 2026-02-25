# ERP Connector Readiness Checklists

Per PLAN.md §6.6, each ERP connector must pass this checklist before GA can be declared.
All eight items must be signed off; GA cannot proceed with unresolved items.

---

## How to use these checklists

For each ERP, fill in the **Evidence / Notes** column and mark each item ✅ (pass),
⚠️ (partial / conditional), or ❌ (blocked).  The checklist signer is the engineer
who ran the test, not the requester.  Collected evidence should be committed to
`docs/ga-evidence/<erp>/` and referenced by path in the Evidence column.

---

## QuickBooks Online (QBO)

| # | Requirement (PLAN.md §6.6) | Status | Evidence / Notes | Signed-off by | Date |
|---|----------------------------|--------|-----------------|---------------|------|
| 1 | **Auth / connectivity** — OAuth2 PKCE flow works end-to-end; refresh token obtained and persisted; re-auth triggers within 48 h of expiry | ❌ | | | |
| 2 | **Sandbox E2E AP posting** — Bill creation in QBO sandbox passes; `erp_reference` returned and stored | ❌ | | | |
| 3 | **Idempotent retry** — Duplicate `idempotency_key` does not create double-bill; second call returns existing `bill_id` | ❌ | | | |
| 4 | **Error mapping** — `QuickBooksAPIError` maps to normalised `error_code`; no raw API errors exposed to operator | ❌ | | | |
| 5 | **Audit coverage** — `erp_post_attempted`, `erp_post_success` / `erp_post_failed` events written to `audit_events` with `correlation_id` | ❌ | | | |
| 6 | **Operator exception behaviour** — `failed_post → ready_to_post` retry surfaced in worklist; clear failure reason visible | ❌ | | | |
| 7 | **Runbook completeness** — Entry in `docs/RUNBOOKS.md` for QBO auth expiry and bill-posting failure | ❌ | See RUNBOOKS.md | | |
| 8 | **Signed-off validation evidence** — Screenshots / logs committed; QA sign-off recorded | ❌ | | | |

**Connector module:** `clearledgr/services/erp/quickbooks.py`

---

## Xero

| # | Requirement (PLAN.md §6.6) | Status | Evidence / Notes | Signed-off by | Date |
|---|----------------------------|--------|-----------------|---------------|------|
| 1 | **Auth / connectivity** — OAuth2 PKCE flow works; refresh token persisted; re-auth triggers before expiry | ❌ | | | |
| 2 | **Sandbox E2E AP posting** — Invoice creation in Xero demo company passes; `InvoiceID` returned and stored | ❌ | | | |
| 3 | **Idempotent retry** — `Reference` field used as idempotency key; duplicate rejected gracefully | ❌ | | | |
| 4 | **Error mapping** — `XeroAPIError` mapped to normalised `error_code`; Xero validation messages surfaced operator-safe | ❌ | | | |
| 5 | **Audit coverage** — `erp_post_attempted`, `erp_post_success` / `erp_post_failed` events written with `correlation_id` | ❌ | | | |
| 6 | **Operator exception behaviour** — Retry path surfaced; clear failure reason visible in worklist | ❌ | | | |
| 7 | **Runbook completeness** — Entry in `docs/RUNBOOKS.md` for Xero auth expiry and posting failure | ❌ | See RUNBOOKS.md | | |
| 8 | **Signed-off validation evidence** — Screenshots / logs committed | ❌ | | | |

**Connector module:** `clearledgr/services/erp/xero.py`

---

## NetSuite

| # | Requirement (PLAN.md §6.6) | Status | Evidence / Notes | Signed-off by | Date |
|---|----------------------------|--------|-----------------|---------------|------|
| 1 | **Auth / connectivity** — TBA OAuth1.0a token-based auth; credentials stored encrypted; connection test endpoint available | ❌ | | | |
| 2 | **Sandbox E2E AP posting** — VendorBill creation in NetSuite sandbox passes; `internalId` returned and stored | ❌ | | | |
| 3 | **Idempotent retry** — Custom `externalId` field used; duplicate lookup returns existing record | ❌ | | | |
| 4 | **Error mapping** — SOAP / REST fault codes mapped to normalised `error_code`; no raw SuiteScript errors exposed | ❌ | | | |
| 5 | **Audit coverage** — `erp_post_attempted`, `erp_post_success` / `erp_post_failed` written with `correlation_id` | ❌ | | | |
| 6 | **Operator exception behaviour** — Retry path surfaced; clear failure reason | ❌ | | | |
| 7 | **Runbook completeness** — Entry in `docs/RUNBOOKS.md` for NetSuite credential rotation and posting failure | ❌ | See RUNBOOKS.md | | |
| 8 | **Signed-off validation evidence** — Screenshots / logs committed | ❌ | | | |

**Connector module:** `clearledgr/services/erp/netsuite.py`

---

## SAP (Business One / S/4HANA)

| # | Requirement (PLAN.md §6.6) | Status | Evidence / Notes | Signed-off by | Date |
|---|----------------------------|--------|-----------------|---------------|------|
| 1 | **Auth / connectivity** — Bearer token obtained from SAP service layer; `base_url` + `bearer_token` injected at runtime; connection test returns 200 | ⚠️ | OData client implemented; live env test pending | | |
| 2 | **Sandbox E2E AP posting** — `PurchaseInvoices` POST to SAP B1 service layer passes in sandbox; `DocEntry` returned and stored as `erp_reference` | ❌ | | | |
| 3 | **Idempotent retry** — `DocNum` / `ExternalReference` field used as idempotency check; duplicate returns existing `DocEntry` | ❌ | | | |
| 4 | **Error mapping** — SAP OData error responses mapped to normalised `error_code`; no raw XML/JSON SAP faults exposed | ❌ | | | |
| 5 | **Audit coverage** — `erp_post_attempted`, `erp_post_success` / `erp_post_failed` written with `correlation_id`; status polling (`_sync_sap_bill`) audited | ⚠️ | Status polling implemented in `erp_sync.py`; live audit test pending | | |
| 6 | **Operator exception behaviour** — Retry path surfaced; `PaymentStatus` mapping (O/P/C) verified | ⚠️ | Mapping implemented; live verification pending | | |
| 7 | **Runbook completeness** — Entry in `docs/RUNBOOKS.md` for SAP token expiry and posting failure | ❌ | See RUNBOOKS.md | | |
| 8 | **Signed-off validation evidence** — Screenshots / logs committed | ❌ | | | |

**Connector module:** `clearledgr/services/erp/sap.py`
**Status polling:** `clearledgr/services/erp_sync.py` → `_sync_sap_bill()`
**GL lookup:** `SAPAdapter.list_gl_accounts()` — real OData call implemented; dry_run=True returns mock for testing.

---

## GA Gate Summary

All four ERPs must have **all 8 items ✅** before GA can be declared (PLAN.md §6.8, §7.8).

Current status:
| ERP | Items ✅ | Items ⚠️ | Items ❌ | GA-ready? |
|-----|----------|----------|----------|-----------|
| QuickBooks | 0 / 8 | 0 | 8 | ❌ No |
| Xero | 0 / 8 | 0 | 8 | ❌ No |
| NetSuite | 0 / 8 | 0 | 8 | ❌ No |
| SAP | 0 / 8 | 3 | 5 | ❌ No |

_Last updated: 2026-02-25_
