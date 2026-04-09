# Design Thesis Audit — Codebase vs. DESIGN_THESIS.md

**Date:** 2026-04-09
**Method:** Four parallel structured audits across extension/surfaces, agent architecture, object model/security, and fraud/onboarding/commercial. Each audit produced a gap matrix with file:line citations.
**Status legend:** ✅ Aligned · ⚠️ Partial · ❌ Missing · 🚫 Conflicts

---

## Verdict

**The thesis and the codebase are roughly 40% aligned.** The foundations are in the right places — InboxSDK extension, Gmail labels, Fernet-encrypted ERP credentials, duplicate detection, confidence-gated extraction — but the architectural commitments that distinguish Clearledgr from a conventional AP tool are largely **not implemented or not enforced at the architecture level**.

The most load-bearing gap is the one the thesis identifies as foundational: **LLM-constrained-by-rules vs rules-constrained-by-LLM**. Today, match logic is rule-based but the agent's final decision still flows through `APDecisionService` (Claude), which can override the rules. The thesis explicitly prohibits this. Closing this gap is the single highest-priority refactor.

The second-biggest cluster is **fraud controls as detection signals rather than architectural gates**. Vendor domain lock, first payment hold, IBAN change freeze, and internal instruction rejection all exist in some form, but they flag rather than block. Enterprise procurement will treat this as a ship-blocker.

**Overall grade: ⚠️ SHIP-BLOCKER for enterprise.** The product can run on Starter customers with heavy CS oversight. It cannot safely go live with NetSuite/SAP enterprise customers without the P0 work below.

---

## At a Glance

| Dimension | ✅ | ⚠️ | ❌ | 🚫 |
|-----------|----|----|----|-----|
| Extension & Gmail surfaces | 6 | 5 | 3 | 0 |
| Agent architecture & LLM guardrails | 5 | 9 | 5 | 3 |
| Object model, data & security | 4 | 6 | 8 | 0 |
| Fraud controls, onboarding & commercial | 3 | 13 | 6 | 0 |
| **Total** | **18** | **33** | **22** | **3** |

Of 76 commitments audited: 24% aligned, 43% partial, 29% missing, 4% actively conflicting.

---

## What's Aligned — The Wins

These are commitments where the codebase matches the thesis and can be cited as ship-ready:

- **InboxSDK MV3 extension** ([ui/gmail-extension/package.json:15](ui/gmail-extension/package.json)) — Built on `@inboxsdk/core` v2.2.11, Manifest V3 compliant. Not custom DOM.
- **Clearledgr Home** (custom route via `sdk.Router.handleCustomRoute`) — [inboxsdk-layer.js:2144](ui/gmail-extension/dist/inboxsdk-layer.js)
- **NavMenu** (`sdk.NavMenu.addNavItem()`) — [inboxsdk-layer.js:1916-1927](ui/gmail-extension/dist/inboxsdk-layer.js)
- **Inbox stage labels** (`sdk.Lists.registerThreadRowViewHandler()`) — [inboxsdk-layer.js:888-890](ui/gmail-extension/dist/inboxsdk-layer.js)
- **Gmail label hierarchy** via Gmail API — [clearledgr/services/gmail_labels.py:23-38](clearledgr/services/gmail_labels.py)
- **Kanban pipeline routes** — [inboxsdk-layer.js:2022-2144](ui/gmail-extension/dist/inboxsdk-layer.js)
- **Confidence model** (95% threshold, per-vendor calibration) — [clearledgr/core/ap_confidence.py](clearledgr/core/ap_confidence.py)
- **Vendor-level duplicate detection**, 90-day window — [clearledgr/services/cross_invoice_analysis.py:220-246](clearledgr/services/cross_invoice_analysis.py)
- **Amount range check vs vendor history** — [clearledgr/services/ap_decision.py:71-155](clearledgr/services/ap_decision.py)
- **PO reference existence check before matching** — [clearledgr/services/purchase_orders.py:527-544](clearledgr/services/purchase_orders.py)
- **Invoice PDFs not stored** — only `attachment_url` column; no binary blobs — [clearledgr/core/stores/ap_store.py:55](clearledgr/core/stores/ap_store.py)
- **Timeline as audit trail** — [clearledgr/core/database.py:690](clearledgr/core/database.py) (`audit_events` table)
- **ERP OAuth tokens Fernet-encrypted** with customer-specific keys — [clearledgr/core/stores/auth_store.py:42-83](clearledgr/core/stores/auth_store.py)
- **7-year default retention** — [clearledgr/core/org_config.py:113](clearledgr/core/org_config.py) (`data_retention_days: 2555`)
- **Three subscription tiers defined** with pricing — [clearledgr/services/subscription.py:21-26](clearledgr/services/subscription.py)
- **Rollback controls API** with TTL — [clearledgr/core/launch_controls.py:80-145](clearledgr/core/launch_controls.py)
- **Four-step onboarding** persisted to DB — [clearledgr/api/onboarding.py:151-746](clearledgr/api/onboarding.py)
- **Read Only seat role** exists — [clearledgr/core/auth.py:427](clearledgr/core/auth.py)

---

## 🔴 P0 — Architectural Principle Violations

These are the gaps that could cause a production incident or violate the thesis's load-bearing architectural principles. Fix before any autonomous processing of live enterprise traffic.

### 1. 🚫 Three-way match can be overridden by the LLM

**Thesis (§7.6):** *"3-way match is deterministic, not LLM-driven. The match logic is a set of explicit rules... The LLM's role in matching is only to write the plain-language exception reason — it does not determine whether a match passes or fails."*

**Reality:** Match logic is rule-based in [clearledgr/services/purchase_orders.py:511-615](clearledgr/services/purchase_orders.py) — good. BUT [clearledgr/services/ap_decision.py:42-50](clearledgr/services/ap_decision.py) (`APDecisionService`) is the final router and can produce approve/reject/escalate based on Claude's reasoning without the deterministic match outcome being load-bearing.

**Impact:** The LLM can, in principle, approve an invoice that failed the deterministic match. This is the single most dangerous architectural deviation in the codebase for a finance product.

**Fix:** Refactor so `APDecisionService` receives match outcome as an immutable input. The LLM writes the exception description only. If match fails, the agent cannot produce "approve" — it's not an option in the tool schema.

### 2. ❌ Override window is missing entirely

**Thesis (§7.4, §7.8):** *"Default 15 minutes for ERP posts. The override window is the last human escape hatch for autonomous actions."*

**Reality:** `APState` defines `OVERRIDE_TYPE_*` constants but no timer mechanism enforces a window during which actions can be reversed.

**Impact:** Once the agent posts to the ERP, there's no designed rollback window. Any correction requires manual ERP intervention.

**Fix:** Implement a per-action `override_expires_at` timestamp. Block finalisation until the window closes. Pair with Slack undo notification (§6.8).

### 3. 🚫 ERP connector reversal API not implemented

**Thesis (§7.7, §7.8):** *"Every connector is required to support [reversal] before deployment... The test posts a synthetic invoice, validates the post, then reverses it."*

**Reality:** No reversal capability detected in [clearledgr/services/erp_api_first.py](clearledgr/services/erp_api_first.py) or connector layer.

**Impact:** Mass failure recovery (§7.8 scenario 1) is impossible as designed. Rollback requires manual ERP work per invoice.

**Fix:** Add `reverse_bill()` to each ERP connector. Add a connector-level `write_and_rollback_test()` that must pass before deployment.

### 4. ❌ Trust-building arc not implemented

**Thesis (§7.5):** Week 1 maximum transparency banner, Day 14 Slack baseline message, Day 30 tier expansion conversation, weekly Monday signal.

**Reality:** No time-gated rollout. Governance exists but no temporal scheduler, no transparency mode flag, no Day-14 or Day-30 triggers.

**Impact:** Without this, autonomy is either granted too soon (risk) or never (stuck in Supervised forever).

**Fix:** Implement a per-workspace `onboarded_at` timestamp and a scheduled job that emits the Week 1 banner, Day 14 message, and Day 30 expansion recommendation.

---

## 🔴 P0 — Security & Fraud Ship-Blockers

These block enterprise sales. Procurement will ask directly.

### 5. ❌ IBAN fraud controls missing entirely

**Thesis (§8):** IBAN change freeze with three-factor verification (vendor email domain + phone confirmation + AP Manager sign-off). **"IBAN changes trigger an immediate payment hold for the affected vendor — no payment is scheduled to any new IBAN until the change is verified."**

**Reality:** No IBAN change detection, no payment hold, no three-factor verification flow. [clearledgr/core/stores/vendor_store.py:37](clearledgr/core/stores/vendor_store.py) has `bank_details_changed_at` timestamp but no enforcement.

**Impact:** The single most common AP fraud (IBAN swap) is undefended.

### 6. ❌ IBAN tokenisation status unclear — likely plaintext

**Thesis (§19):** *"Bank account numbers or IBANs in plaintext at any point. IBANs are stored in tokenised form and displayed masked in the UI (`GB82 **** **** **** 4332`)."*

**Reality:** [clearledgr/core/stores/vendor_store.py](clearledgr/core/stores/vendor_store.py) has no `iban` column in the audit's schema view. [clearledgr/services/invoice_validation.py:1598](clearledgr/services/invoice_validation.py) references `stored_bank` as a string field. If IBANs are stored in `vendor_profiles.metadata` JSON without encryption, this violates data minimisation.

**Action:** **Audit all IBAN storage paths immediately.** Grep for `iban`, `bank_account`, `account_number`, `sort_code`. If plaintext, implement Fernet tokenisation with column-level encryption and masked display.

### 7. ⚠️ Anti-fraud primitives flag but do not block

**Thesis (§8):** *"Fraud controls must be architectural, not configurational. The controls that matter most — IBAN change freeze, first payment hold, domain lock — cannot be disabled by the AP Manager."*

**Reality:** All seven primitives (domain lock, first payment hold, IBAN change freeze, payment amount ceiling, duplicate prevention, velocity monitoring, internal instruction rejection) exist as **detection signals in risk scoring**, not as **blocking gates**. [clearledgr/services/ap_decision.py:96-99](clearledgr/services/ap_decision.py) treats "new_vendor" as a risk signal that can be overridden by approval routing.

**Impact:** The agent can, in principle, autonomously schedule a first payment to a new vendor if the match passes and the policy allows. Thesis prohibits this regardless of value or match result.

**Fix:** Promote primitives to architectural gates. `first_payment_guard()` called before any payment scheduling raises a blocking exception if `vendor.first_payment_at is None`. Not overridable except by CFO role in session.

### 8. ❌ Role hierarchy fundamentally misaligned

**Thesis (§17):** Five roles — AP Clerk, AP Manager, Financial Controller, CFO, Read Only. Additive upward. CFO-only for ERP connection changes and autonomy tier modifications.

**Reality:** [clearledgr/core/auth.py:107-444](clearledgr/core/auth.py) has four generic roles: owner, admin, operator, viewer. AP Clerk, AP Manager, Controller do not exist. API guards use `require_ops_user` or `require_admin_user` — not role-specific.

**Impact:** The permission model in the thesis cannot be enforced because the roles do not exist in code. Approval routing cannot distinguish "route to AP Manager" from "route to Controller" because both collapse to `operator`.

**Fix:** Expand the role enum. Implement additive-upward permission checks. Update API guards to be role-specific.

---

## 🟠 P1 — Major Product Features Missing

### 9. ❌ Micro-deposit bank verification (§9) — zero implementation

Vendor onboarding Bank Verify stage is thesis-critical but entirely absent. No two-deposit orchestration, no vendor confirmation portal, no "IBAN Verified" status marking. [clearledgr/services/vendor_management.py](clearledgr/services/vendor_management.py) has the `BankAccount` dataclass but no workflow.

### 10. ❌ Vendor onboarding portal + automation (§9)

70% unimplemented. Missing: portal link dispatch, auto-chase at 24h/48h, 72h escalation, document collection interface, ERP activation automation. Only the dataclasses exist.

### 11. ❌ Google Workspace Add-on entirely absent (§6.9)

Zero code. Thesis treats mobile approvals as equal pillar alongside the Chrome extension. Enterprise CFOs cannot approve from their phones today.

### 12. ❌ Conditional digest logic (§6.8)

No digest-triggering code in [clearledgr/services/slack_notifications.py](clearledgr/services/slack_notifications.py). Thesis commits to: fire only when there's something to act on; silence = success. Either no digest at all, or noise that gets ignored.

### 13. ⚠️ Thread toolbar buttons (§6.5)

Bulk toolbar registered ([inboxsdk-layer.js:1007-1056](ui/gmail-extension/dist/inboxsdk-layer.js)) but **no individual thread toolbar**. Thesis specifies three buttons (Approve, Review Exception, NetSuite↗) via `sdk.Toolbars.registerThreadButton()`. This is a primary action surface.

### 14. ⚠️ Thread sidebar structure mismatch (§6.6)

Current sidebar is a generic Preact component. Thesis specifies four fixed sections in strict order: Invoice, 3-Way Match, Vendor, Agent Actions. Restructure required.

### 15. ❌ Override window notifications in Slack (§6.8)

No "Posted INV-X. Override window closes in 15 minutes: [Undo]" messages. Coupled to P0 #2.

### 16. ⚠️ Intelligent Slack routing (§6.8)

Current: basic channel/role routing. Thesis: DMs for personal approvals (not channel), CFO escalation with 4-hour window, procurement contact for no-PO exceptions, OOO detection via Google Calendar with backup routing.

### 17. ❌ Conversational queries in Slack (§6.8)

*"What's our outstanding with AWS this month?"* — no handler. Not implemented.

---

## 🟠 P1 — Object Model Refactor

### 18. ❌ Box / Pipeline / Saved View abstractions missing (§5)

Thesis positions Clearledgr as Streak-like with Boxes, Pipelines, Stages, Columns, Timelines, Saved Views as first-class domain objects. Codebase uses flat `ap_items` table ([clearledgr/core/database.py:618](clearledgr/core/database.py)) with no Box linking structure, no Pipeline concept, no Saved Views.

**Scope:** This is a substantial refactor. The fix is to introduce a `boxes` table with polymorphic `box_type` (invoice / vendor_onboarding), a `pipelines` table, and a `box_links` table. Current `ap_items` becomes a view on `boxes WHERE box_type='invoice'`.

### 19. ⚠️ Vendor first-class object incomplete (§3)

[clearledgr/core/stores/vendor_store.py](clearledgr/core/stores/vendor_store.py) has `vendor_profiles` with payment_terms, invoice_count, exception_count. **Missing:** registration_number, vat_number, registered_address, director_names, kyc_completion_date, iban (verified), iban_verified_at, ytd_spend, risk_score. Vendor risk scoring not implemented.

### 20. ⚠️ Multi-entity partial (§3)

[clearledgr/core/stores/entity_store.py](clearledgr/core/stores/entity_store.py) exists with `entities` table. **Missing:** parent account abstraction, cross-entity consolidated view for CFO, per-entity IBAN storage, cross-entity vendor management (single vendor, entity-specific terms).

### 21. ⚠️ Agent Columns not explicit (§5.5)

Invoice Amount and PO Reference exist as columns. **Missing explicit fields:** GRN Reference, Match Status, Exception Reason, Days to Due Date, IBAN Verified, ERP Posted. Some are computable but not materialised.

---

## 🟡 P2 — Testing & Operational Infrastructure

### 22. ❌ Testing/QA infrastructure absent (§7.7)

No synthetic invoice test suite (target floor 500), no historical replay harness, no shadow mode deployment, no canary gates, no deployment freeze window enforcement (Tue-Thu 10am-2pm UK). Only [tests/test_e2e_rollback_controls.py](tests/test_e2e_rollback_controls.py) for rollback controls.

### 23. ⚠️ Audit trail structure incomplete (§7.6)

[clearledgr/services/audit_trail.py](clearledgr/services/audit_trail.py) has event_type, summary, reasoning. **Missing:** explicit three-field decomposition (raw_extracted_data / rule_applied / conclusion). Auditors cannot reconstruct exactly which rule fired.

### 24. ⚠️ Model improvement loop partial (§7.9)

[clearledgr/services/correction_learning.py](clearledgr/services/correction_learning.py) and [learning_calibration.py](clearledgr/services/learning_calibration.py) exist. **Missing:** 50-signal minimum gating, vendor-specific extraction rules stored per-vendor, closed-loop validation tracking override rate decrease.

### 25. ⚠️ Extraction guardrails incomplete (§7.6)

- Amount cross-validation (subject/body/attachment agreement): partial, no explicit three-way check
- Currency consistency vs ERP vendor config: **missing**
- Reference format vs vendor historical pattern: generic pattern matching only, no per-vendor format memory

---

## 🟡 P2 — Polish

### 26. 🚫 DID-WHY-NEXT communication pattern not enforced

Three-sentence pattern (§7.1) is not a convention enforced anywhere in code. Slack/Teams messages likely use full prose. This is a brand/trust signal and should be enforced at the message-generation layer.

### 27. ❌ @Mentions escalation path (§5.3)

[clearledgr/core/database.py:712](clearledgr/core/database.py) has `pending_notifications` table but no @mention parsing or Gmail↔Slack bridge.

### 28. ❌ Archived users / attribution preservation (§5.4)

No user deactivation/archive logic. Compliance requirement for financial records.

### 29. ⚠️ Starter vs Enterprise onboarding distinction not enforced (§15)

[clearledgr/api/onboarding.py:374-655](clearledgr/api/onboarding.py) treats Xero/QB and NetSuite/SAP identically. No architectural gate requiring managed implementation for NetSuite/SAP.

### 30. ⚠️ Billing UI in Gmail missing (§13)

Subscription API exists in [clearledgr/services/subscription.py](clearledgr/services/subscription.py). No Gmail sidebar integration for upgrade/billing management. Customers cannot manage subscriptions inside Gmail as thesis requires.

---

## Prioritised Remediation Order

Suggested execution sequence. P0 items should block any enterprise go-live.

**Phase 1 — Architectural safety (4–6 weeks)**
1. Refactor `APDecisionService` so deterministic match outcome is immutable (#1)
2. Implement override window mechanism + Slack undo notifications (#2, #15)
3. Add `reverse_bill()` to all ERP connectors + write-and-rollback test (#3)
4. Promote all seven fraud primitives to architectural blocking gates (#7)

**Phase 2 — Security & enterprise unblock (4–6 weeks)**
5. Audit and remediate IBAN storage — tokenise with Fernet, mask in UI (#6)
6. Implement IBAN change freeze + three-factor verification (#5)
7. Expand role enum to five thesis roles + role-specific API guards (#8)
8. Extend vendor schema with KYC fields, risk scoring (#19)

**Phase 3 — Core missing features (8–10 weeks)**
9. Micro-deposit bank verification workflow (#9)
10. Vendor onboarding portal + auto-chase + ERP activation (#10)
11. Trust-building arc scheduled messaging (Week 1 / Day 14 / Day 30 / weekly) (#4)
12. Thread toolbar buttons + sidebar restructure to four sections (#13, #14)
13. Conditional digest + intelligent routing + conversational queries (#12, #16, #17)

**Phase 4 — Scale readiness (6–8 weeks)**
14. Google Workspace Add-on for mobile approvals (#11)
15. Testing infrastructure: synthetic suite, replay, shadow mode, canary (#22)
16. Box/Pipeline abstraction refactor (#18)
17. Billing UI in Gmail (#30)

**Phase 5 — Polish & compliance (4 weeks)**
18. Audit trail three-field decomposition (#23)
19. Model improvement loop 50-signal gating + per-vendor rules (#24)
20. Multi-entity parent account abstraction (#20)
21. Remaining extraction guardrails — currency, reference format (#25)
22. DID-WHY-NEXT enforcement (#26)
23. @Mentions bridge (#27)
24. Archived users (#28)
25. Starter/Enterprise onboarding gate (#29)

---

## Caveats

1. **Codebase is in flux.** Git status shows substantial uncommitted changes including deletions of `browser_agent` files and modifications across ERP, runtime, and extension layers. Findings should be re-verified post-merge.

2. **Some findings are schema-level.** Agent 3 noted the absence of an `iban` column in `vendor_profiles` — this doesn't rule out IBAN storage elsewhere (e.g. `metadata` JSON). Direct grep for `iban` / `bank_account` across the codebase is recommended before remediation planning.

3. **Partial implementations need human verification.** Several ⚠️ items were called partial based on pattern matching — some may turn out to be closer to aligned than the audit suggests on close reading.

4. **This audit is a snapshot.** Re-run after each phase of remediation. Add new commitments to the matrix as the thesis evolves.

---

*Audit conducted by four parallel code-exploration agents against [DESIGN_THESIS.md](DESIGN_THESIS.md) v1.0. Findings synthesised on 2026-04-09. File:line citations reflect codebase state at that time.*
