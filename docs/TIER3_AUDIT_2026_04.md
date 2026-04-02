# Tier 3 Audit — Core Logic That Could Produce Wrong Results

Date: 2026-04-02
Auditor: Claude Opus 4.6
Scope: AP state machine, confidence scoring, duplicate detection, GL coding, amount extraction, policy evaluation, fuzzy matching
Total issues: 22 (4 critical, 7 high, 9 medium, 2 low)

---

## G. AP State Machine & Confidence Scoring (12 issues)

### G1. [HIGH] Confidence normalization can produce values > 1.0
**File:** `clearledgr/core/ap_confidence.py:95-106`
**What produces wrong results:** `normalize_confidence_value()` has unreachable dead code at lines 102-105 (clamping to 0.0-1.0). Values between 100.0 and 100.5 are divided by 100 but the result (1.0-1.005) is never clamped. Returns values > 1.0.
**When it happens:** When a caller passes a percentage value like 100.3 expecting normalization to 1.0.
**Impact:** Out-of-range confidence values bypass downstream >= 1.0 checks or cause NaN in calculations.
**Fix:** Add `return min(1.0, max(0.0, num))` before the final return.

### G2. [MEDIUM] Threshold override can exceed 1.0
**File:** `clearledgr/core/ap_confidence.py:299`
**What produces wrong results:** `merged_threshold_overrides[field] = max(static_value, learned_value)` — no upper clamp. If either value is > 1.0 (from G1), the threshold becomes impossible to fail, silently disabling confidence checks.
**Fix:** Clamp merged threshold to [0.0, 1.0].

### G3. [MEDIUM] Cross-invoice duplicate check uses asymmetric division
**File:** `clearledgr/services/cross_invoice_analysis.py:218`
**What produces wrong results:** `amount_diff = abs(amount - inv_amount) / amount` divides by the new invoice amount, not the average. A $10 new invoice vs a $1000 historical invoice produces 99x variance (false negative). A $1000 new invoice vs a $1010 historical produces 1% (correct match).
**Fix:** Use `max(amount, inv_amount)` as denominator for symmetric comparison.

### G4. [MEDIUM] Cross-invoice analysis can produce NaN/Infinity
**File:** `clearledgr/services/cross_invoice_analysis.py:278`
**What produces wrong results:** If all historical amounts are very small (rounding errors, test data), `avg_amount` approaches 0. `deviation_pct = abs(amount - avg_amount) / avg_amount` produces Infinity.
**Fix:** Guard against avg_amount < 0.01 before computing deviation.

### G5. [MEDIUM] Fuzzy match score can exceed 1.0
**File:** `clearledgr/services/fuzzy_matching.py:248-253`
**What produces wrong results:** Weights default to summing to 1.0, but custom config weights are not validated. If caller sets all weights to 0.6, max score becomes 2.4.
**Fix:** Normalize weights to sum to 1.0 before scoring.

### G6. [MEDIUM] AP decision confidence can be NaN
**File:** `clearledgr/services/ap_decision.py:562`
**What produces wrong results:** `_safe_float()` successfully parses `"NaN"` to `float('nan')`. The `or 0.0` fallback doesn't catch NaN because NaN is truthy in Python. Confidence becomes NaN, causing all downstream comparisons (`confidence >= threshold`) to return False.
**Fix:** Add `math.isnan()` check after `_safe_float()`.

### G7. [HIGH] Fallback decision confidence not clamped to 1.0
**File:** `clearledgr/services/ap_decision.py:641, 677, 695, 716`
**What produces wrong results:** `max(0.7, confidence - 0.2)` applies lower bound but no upper clamp. If input confidence is 1.5 (from G1), result is 1.3. Propagates out-of-range values through the entire decision flow.
**Fix:** Add `min(1.0, ...)` wrapper on all confidence calculations.

### G8. [HIGH] Recoverability classification defaults to "recoverable"
**File:** `clearledgr/core/ap_states.py:199, 217`
**What produces wrong results:** When an ERP error message doesn't match any known token, it defaults to "recoverable". This means unknown permanent failures (e.g., "Custom ERP validation: invoice not found") are retried infinitely.
**Fix:** Default to non-recoverable for unmatched errors. Require explicit recoverable classification.

### G9. [LOW] AP store state transition atomicity (appears safe but fragile)
**File:** `clearledgr/core/stores/ap_store.py:213-248`
**What produces wrong results:** State UPDATE and audit INSERT are in the same connection/transaction. Appears safe because exceptions cause rollback. But fragile if connection mode changes.
**Fix:** Add explicit transaction wrapper for clarity.

### G10. [MEDIUM] Policy merge silently overwrites enabled policies with disabled ones
**File:** `clearledgr/services/policy_compliance.py:627-629`
**What produces wrong results:** Mailbox policies override org policies by `policy_id` without checking `enabled` status. A disabled mailbox policy silently replaces an enabled org policy.
**Fix:** Only override if mailbox policy is enabled, or log when override changes enabled status.

### G11. [MEDIUM] Policy edge case: $0 invoices incorrectly flagged
**File:** `clearledgr/services/policy_compliance.py:355`
**What produces wrong results:** For policy `operator: "gte", threshold: 0`, a $0 invoice triggers `0 >= 0 = True`, incorrectly requiring a PO for zero-value invoices (adjustments, credits, test data).
**Fix:** Skip policy checks for amount <= 0 or add explicit $0 exemption.

### G12. [LOW] Cross-invoice vendor stats zero denominator (guarded but fragile)
**File:** `clearledgr/services/cross_invoice_analysis.py:343`
**What produces wrong results:** The guard `if amounts and sum(amounts) > 0` prevents division by zero. But relies on type coercion of stored amounts. Safe today, fragile if storage format changes.
**Fix:** Add explicit `avg > 0.001` guard for safety.

---

## H. Duplicate Detection & GL Coding (10 issues)

### H1. [CRITICAL] Vendor name comparison is case-sensitive
**File:** `clearledgr/core/stores/ap_store.py:643-655`
**What produces wrong results:** SQL `WHERE vendor_name = ?` uses exact string match. "Acme Corp" does not match "ACME CORP" or "acme corp". Two invoices from the same vendor with different casing bypass duplicate detection.
**Concrete example:** Invoice 1: vendor="Acme Corp", INV-001, $5000. Invoice 2: vendor="ACME CORP", INV-001, $5000. Both created. Duplicate payment.
**Fix:** Normalize vendor name to lowercase before comparison. Add `COLLATE NOCASE` to SQL or use `LOWER(vendor_name) = LOWER(?)`.

### H2. [HIGH] Invoice number normalization inconsistent across checkers
**File:** `cross_invoice_analysis.py:211` vs `ap_store.py:653`
**What produces wrong results:** `cross_invoice_analysis` compares with `.lower()`. `ap_store` uses exact match. "INV-001" matches in one system but not the other.
**Fix:** Normalize invoice numbers in both systems (lowercase, strip whitespace, remove common prefixes like "INV-", "#").

### H3. [HIGH] Invoices with missing invoice_number bypass ALL duplicate checks
**File:** `clearledgr/services/invoice_validation.py:1520`
**What produces wrong results:** Duplicate check gated by `if invoice.vendor_name and invoice.invoice_number`. If invoice_number is None (common for recurring subscriptions), the entire check is skipped. Same vendor can submit identical amounts monthly without dedup.
**Fix:** When invoice_number is missing, fall back to vendor + amount + date range matching.

### H4. [MEDIUM] Race condition between duplicate check and insert
**File:** `clearledgr/core/stores/ap_store.py:60-135`
**What produces wrong results:** Duplicate check and insert are separate transactions. Two simultaneous emails with the same invoice can both pass the check and both insert, creating a duplicate.
**Fix:** Use INSERT with ON CONFLICT or add a UNIQUE constraint on (organization_id, vendor_name, invoice_number).

### H5. [CRITICAL] One wrong GL correction corrupts all future invoices
**File:** `clearledgr/services/gl_correction.py:288-294`, `correction_learning.py:1401-1412`
**What produces wrong results:** A learned GL rule is created after just ONE user correction. If the user corrects to the wrong GL code, all future invoices from that vendor auto-get the wrong GL. The rule has high confidence (1.0 after one correction). No validation, no review, no decay.
**Concrete example:** User incorrectly sets Slack to GL 7000 (Other Expenses) instead of 6150 (Software). Next 50 Slack invoices auto-coded to 7000. $50K miscategorized.
**Fix:** Require 2+ corrections before creating a rule. Add confidence decay over time. Allow GL rule review in admin.

### H6. [HIGH] Conflicting GL suggestions — first non-null wins
**File:** `clearledgr/services/gl_correction.py:274-326`
**What produces wrong results:** Four services suggest GL independently (local corrections, learning service, vendor intelligence, default). If they disagree, the first non-null result wins without conflict detection. A wrong local correction overrides correct vendor intelligence.
**Fix:** When services disagree, flag as "gl_conflict" and require human review instead of auto-selecting.

### H7. [MEDIUM] GL confidence never decays
**File:** `clearledgr/services/correction_learning.py:1401`
**What produces wrong results:** Learned GL rules persist with original confidence forever. If the company restructures GL accounts (e.g., 6200 deprecated, replaced by 5210), the old rule wins because it was learned first.
**Fix:** Apply time-based confidence decay (e.g., halve confidence after 90 days without reinforcement).

### H8. [HIGH] Tax-inclusive amounts flagged as extraction errors
**File:** `clearledgr/services/agent_reflection.py:257-264`
**What produces wrong results:** When line items sum to $150 but total is $165 (including 10% tax), the reflection layer flags "line items sum doesn't match total." This is correct behavior (tax IS included). False positive causes unnecessary human review.
**Fix:** Check for common tax rates (5%, 7%, 8%, 10%, 15%, 20%, 21%, 25%) before flagging as error. If `total / line_sum` matches a known tax multiplier, mark as "tax_inclusive" not "mismatch".

### H9. [MEDIUM] Currency aliases not normalized (GBP vs "£")
**File:** `clearledgr/services/llm_email_parser.py:261`
**What produces wrong results:** Currency normalized to uppercase but aliases not mapped. Vendor history stores "GBP", new invoice extracted as "£". Currency mismatch warning fires falsely.
**Fix:** Add currency alias mapping (£ → GBP, $ → USD, € → EUR, ¥ → JPY/CNY).

### H10. [MEDIUM] Zero/negative amounts can be persisted
**File:** `clearledgr/core/stores/ap_store.py:85-95`
**What produces wrong results:** `create_ap_item()` accepts any amount. Validation gate flags amount <= 0 as error but the flag is non-blocking. With approval override, a $0 invoice can be posted to ERP.
**Fix:** Reject amount <= 0 at the DB layer (CHECK constraint) or make the validation gate error blocking for amount.

---

## Priority Matrix

### Fix immediately (blocks pilot)
- H1 — Case-insensitive vendor name dedup (prevents duplicate payments)
- H5 — GL correction minimum threshold (prevents cascade of wrong GL codes)
- G8 — Default recoverability to non-recoverable (prevents infinite retries)

### Fix before enterprise (blocks scaling)
- G1, G7 — Confidence value clamping (prevents wrong auto-approval decisions)
- H2, H3 — Invoice number normalization and missing-number fallback
- H6 — GL conflict detection
- H8 — Tax-inclusive amount detection

### Fix when time permits (polish)
- G2, G3, G4, G5, G6 — Edge case math fixes
- G9, G10, G11, G12 — Policy and atomicity edge cases
- H4, H7, H9, H10 — Duplicate race condition, GL decay, currency aliases, zero amounts
