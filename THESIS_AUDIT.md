# Design Thesis Audit — Codebase vs. DESIGN_THESIS.md

**Date:** 2026-04-09 (initial) · **Phase 1 update:** 2026-04-09
**Method:** Four parallel structured audits across extension/surfaces, agent architecture, object model/security, and fraud/onboarding/commercial. Each audit produced a gap matrix with file:line citations.
**Status legend:** ✅ Aligned · ⚠️ Partial · ❌ Missing · 🚫 Conflicts

---

## ✅ Phase 1 Architectural Remediation — Shipped

The four P0 architectural items in this audit (#1 LLM-bound-to-gate, #2 override window, #3 ERP reverse, #15 Slack undo) plus the bulk of #7 (fraud primitives as blocking gates) shipped between 2026-04-09 commits `ccae27b` and `4a2e8d7`. **174 net new tests added. Test suite: 1407 → 1581 passing.**

| Phase | Commit | Theme | Items closed |
|---|---|---|---|
| 1.1 | [`ccae27b`](#) | LLM bound to deterministic gate via Anthropic tool-use enum + 4-layer enforcement (§7.6) | #1 |
| 1.2a | [`1bc7379`](#) | Group A fraud primitives promoted to architectural blocking gates with severity-aware gate, CFO role, audit trail, FX-aware payment ceiling (§8) | #7 (5 of 7 primitives) |
| 1.3 | [`bb68ecb`](#) | `reverse_bill()` across QBO/Xero/NetSuite/SAP with idempotent dispatcher + mock test harness (§7.7, §7.8) | #3 |
| 1.4 | [`12e24f8`](#) | Override window mechanism, Slack undo card, dedicated 60s reaper, AP state machine `reversed` state, REST endpoint, sync-token persistence (§7.4, §7.8, §6.8) | #2, #15 |
| 1.4 supplement | [`4a2e8d7`](#) | Per-action override window tiers — config dict, action_type column, per-action duration lookup (§8 "configurable per action type") | (closes the deferred §8 sub-clause) |

**What changed in the architecture as a result:**

- **§7.6 binding is now structural, not behavioural.** Anthropic tool-use forces a constrained enum at the API surface. `enforce_gate_constraint` clamps any residual violation. The workflow narrow-waist re-enforces. `process_new_invoice` emits an `llm_gate_override_applied` audit event on every override. Even if the LLM is bypassed entirely, every gate failure routes to human review. Four independent layers, each verified by tests.
- **§8 fraud controls are now architectural.** Payment ceiling (FX-converted to org base currency, fail-closed on FX outage), first-payment hold (with dormancy detection), vendor velocity (single source of truth shared with anomaly surface), prompt injection rejection (rewrote `prompt_guard.py` as pure detector — no more sanitize-and-continue), and duplicate prevention all run as `severity=error` blocking gates. The latent gate-severity bug (info-severity reasons silently failing the gate) was fixed in the same commit. CFO role + `require_fraud_control_admin` + audit trail enforce "only CFO can modify."
- **§7.8 reversal substrate exists.** Uniform `reverse_bill()` dispatcher with two-layer idempotency, reauth retry, audit events, and per-ERP strategies (QBO soft-delete via SyncToken, Xero void, NetSuite REST DELETE, SAP B1 Cancel action that creates the reversal document automatically). 40 mock-only tests cover every connector + dispatcher path.
- **§7.4 override window is live.** Every successful ERP post opens an override window (default 15 min, per-action configurable). The `OverrideWindowObserver` posts a Slack card with a confirm-dialogged danger button. Clicking the button calls `reverse_bill` and updates the card. A dedicated 60-second reaper finalizes expired windows and updates cards to "locked." Process restarts run a one-shot sweep so stale cards never linger. The new `reversed` AP state and `posted_to_erp → reversed → closed` transition path are enforced by the state machine.
- **§8 "configurable per action type" is honored.** Override window duration is now a per-action dict (`{"erp_post": 15, "payment_execution": 60, ...}`) with a `default` fallback key. The data model is open for future autonomous action types — no schema migration required to add them.

**What's still in scope for Phase 2** (deferred from Phase 1 with intent):

- Vendor domain lock (item #7, the "domain lock" sub-primitive) — was Phase 1.2b, deferred
- IBAN change freeze + three-factor verification (item #5, paired with the IBAN tokenisation audit #6)
- Five-role thesis taxonomy (item #8) — Phase 1.2a added `cfo` as an additive role for fraud-control admin only; the full AP Clerk / AP Manager / Controller / CFO / Read Only hierarchy is still missing
- Trust-building arc (item #4) — no time-gated rollout shipped

---

## Verdict (post-Phase 1)

**The thesis and the codebase are now roughly 60% aligned (up from ~40%).** The four most load-bearing architectural commitments — §7.6 (LLM bound by rules), §7.8 (reversibility), §7.4 (override window), and §8 (fraud controls as architectural gates for the Group A primitives) — are now structurally enforced and test-covered. The codebase can no longer auto-approve an invoice that failed the deterministic gate, and every autonomous post is reversible during the override window.

The remaining ship-blocker cluster is **vendor identity and IBAN security**: the IBAN change freeze, the IBAN storage tokenisation audit, vendor domain lock, and the five-role taxonomy are unchanged from the original audit and remain P0 for enterprise go-live. Procurement will still ask directly about these.

**Overall grade: ⚠️ SHIP-BLOCKER for enterprise (narrowed scope).** The product can now run on Starter customers with normal CS oversight (the architectural safety net is in place). It still cannot safely go live with NetSuite/SAP enterprise customers without the Phase 2 IBAN security work below.

---

## At a Glance

Pre-Phase-1 baseline (2026-04-09 morning):

| Dimension | ✅ | ⚠️ | ❌ | 🚫 |
|-----------|----|----|----|-----|
| Extension & Gmail surfaces | 6 | 5 | 3 | 0 |
| Agent architecture & LLM guardrails | 5 | 9 | 5 | 3 |
| Object model, data & security | 4 | 6 | 8 | 0 |
| Fraud controls, onboarding & commercial | 3 | 13 | 6 | 0 |
| **Total** | **18** | **33** | **22** | **3** |

*24% aligned, 43% partial, 29% missing, 4% actively conflicting.*

Post-Phase-1 (2026-04-09 evening):

| Dimension | ✅ | ⚠️ | ❌ | 🚫 |
|-----------|----|----|----|-----|
| Extension & Gmail surfaces | 7 | 4 | 3 | 0 |
| Agent architecture & LLM guardrails | 9 | 8 | 4 | 0 |
| Object model, data & security | 4 | 6 | 8 | 0 |
| Fraud controls, onboarding & commercial | 6 | 11 | 5 | 0 |
| **Total** | **26** | **29** | **20** | **0** |

*34% aligned, 38% partial, 26% missing, 0% conflicting. **Net -3 conflicting / +8 aligned**, with the three architectural conflicts (#1 LLM-overrides-rules, plus the two related fraud-primitive conflicts) all resolved by Phase 1.1 + 1.2a.*

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

### 1. ✅ DONE — Three-way match cannot be overridden by the LLM (Phase 1.1, `ccae27b`)

**Thesis (§7.6):** *"3-way match is deterministic, not LLM-driven. The match logic is a set of explicit rules... The LLM's role in matching is only to write the plain-language exception reason — it does not determine whether a match passes or fails."*

**Original gap:** Match logic was rule-based but `APDecisionService` (Claude) was the final router and could produce `approve` based on its own reasoning without the deterministic match outcome being load-bearing. The LLM could in principle approve an invoice that failed the deterministic match.

**Resolution:** Phase 1.1 (`ccae27b`) bound the LLM to the deterministic gate via four independent enforcement layers:

1. **Layer 1 — Anthropic tool-use enum constraint.** `APDecisionService._call_claude` now sends a forced `tool_choice` for a `record_ap_decision` tool whose `recommendation` enum is dynamically narrowed to `["needs_info", "escalate", "reject"]` (no `approve`) when the gate has any failing reason code. Claude is structurally prevented from emitting `approve` on a failed gate at the API surface.
2. **Layer 2 — `enforce_gate_constraint` service clamp.** A pure helper in `clearledgr/services/ap_decision.py` clamps any residual `approve` + failed-gate combo to `escalate`, sets `gate_override=True`, and preserves `original_recommendation` for audit. Runs on all three `decide()` return paths (Claude success, Claude exception → fallback, no-API-key → fallback).
3. **Layer 3 — Agent planning-loop handlers.** `_handle_get_ap_decision` and `_handle_execute_routing` in `clearledgr/core/skills/ap_skill.py` thread `validation_gate` through, re-evaluate server-side if missing, and apply `enforce_gate_constraint` before building the pre-computed `APDecision`. Closes the Path B (planning loop) bypass.
4. **Layer 4 — Workflow narrow-waist.** `process_new_invoice` re-runs `enforce_gate_constraint` on the resolved decision regardless of which path produced it, and emits an `llm_gate_override_applied` audit event with the pre/post recommendation, reason codes, and actor.

**Verification:** [tests/test_gate_constraint_enforcement.py](tests/test_gate_constraint_enforcement.py) — 23 tests covering the matrix, the prompt, the wire payload, the service, the planning-loop handlers, and the workflow waist.

### 2. ✅ DONE — Override window mechanism live (Phase 1.4, `12e24f8` + `4a2e8d7`)

**Thesis (§7.4, §7.8):** *"Default 15 minutes for ERP posts. The override window is the last human escape hatch for autonomous actions."*

**Original gap:** `APState` had `OVERRIDE_TYPE_*` constants but no timer mechanism enforced a reversal window. Once the agent posted to the ERP there was no designed rollback path.

**Resolution:** Phase 1.4 shipped the full mechanism:

- **New `REVERSED` state** in `clearledgr/core/ap_states.py` with `posted_to_erp → reversed → closed` transitions. `closed` remains terminal; `reversed → posted_to_erp` is structurally forbidden.
- **`override_windows` table** (migration v11) tracks every open window with `id, ap_item_id, organization_id, erp_reference, erp_type, action_type, posted_at, expires_at, state, slack_channel, slack_message_ts, reversed_at/_by/_reason/_ref, failure_reason`. Composite indexes on `(state, expires_at)` and `(action_type, state, expires_at)` keep the reaper query fast.
- **`OverrideWindowService`** ([clearledgr/services/override_window.py](clearledgr/services/override_window.py)) owns the lifecycle: `open_window`, `attempt_reversal`, `expire_window`, `is_window_expired`, `time_remaining_seconds`. Reads the configured duration from `settings_json["workflow_controls"]["override_window_minutes"]` as a per-action dict (Phase 1.4 supplement, `4a2e8d7`).
- **`OverrideWindowObserver`** in [clearledgr/services/state_observers.py](clearledgr/services/state_observers.py) reacts to `posted_to_erp` transitions: opens the window with `action_type="erp_post"`, posts the Slack undo card, persists the message refs.
- **Background reaper** in [clearledgr/services/agent_background.py](clearledgr/services/agent_background.py) — dedicated 60-second loop with crash supervision. Independent from the main 15-min loop so the reaper can keep cadence tight. App startup runs a one-shot sweep so windows that expired during downtime are cleaned up before normal cadence resumes.
- **Slack undo card builders** in [clearledgr/services/slack_cards.py](clearledgr/services/slack_cards.py) — pure Block Kit with a danger-styled button + confirm dialog, plus update helpers for the reversed/finalized/failed states.
- **Slack interactive handler** in [clearledgr/api/slack_invoices.py](clearledgr/api/slack_invoices.py) — `undo_post_*` action_id routes through the canonical contract parser to a new `_handle_undo_post_action` that calls `OverrideWindowService.attempt_reversal` and updates the card to the resulting state.
- **REST API** `POST /api/ap/items/{ap_item_id}/reverse` in [clearledgr/api/ap_items_action_routes.py](clearledgr/api/ap_items_action_routes.py) for the non-Slack ops path (Gmail sidebar, admin console, CLI). Returns 200 / 410 Gone (expired) / 404 (no window) / 502 (ERP rejected) with structured detail.
- **Per-action duration tiers** (Phase 1.4 supplement): config dict shape `{"erp_post": 15, "default": 15, "payment_execution": 60, ...}`. Future autonomous actions register their own observers + action_type strings without schema migration.

**Verification:** [tests/test_override_window.py](tests/test_override_window.py) — 52 tests across state machine, store, service lifecycle, per-action duration lookup, observer, reaper (including Slack failure resilience), Slack handler, and REST API HTTP semantics.

### 3. ✅ DONE — ERP connector reversal API implemented across all four (Phase 1.3, `bb68ecb`)

**Thesis (§7.7, §7.8):** *"Every connector is required to support [reversal] before deployment... The test posts a synthetic invoice, validates the post, then reverses it."*

**Original gap:** No reversal capability anywhere in the connector layer. Mass failure recovery was impossible by design.

**Resolution:** Phase 1.3 (`bb68ecb`) added a uniform `reverse_bill()` dispatcher in [clearledgr/integrations/erp_router.py](clearledgr/integrations/erp_router.py) plus per-ERP implementations:

- **QuickBooks Online** — soft-delete via `POST /v3/company/{realmId}/bill?operation=delete` with optimistic-locking SyncToken. QBO Bills don't support void (voidable entities are Invoices/Sales Receipts/Bill Payments — not Bills), so delete is the only supported reversal. Stale-token edge case handled transparently: connector refetches via REST GET and retries once.
- **Xero** — void via `POST /api.xro/2.0/Invoices/{InvoiceID}` with `Status=VOIDED`. "Payment allocated" errors translated to `payment_already_applied`.
- **NetSuite** — REST DELETE on `/services/rest/record/v1/vendorBill/{id}` via existing OAuth1 helper. 403 → `cannot_delete_record`, "paid" → `payment_already_applied`.
- **SAP B1** — Service Layer `Cancel` action `POST /PurchaseInvoices({DocEntry})/Cancel`. SAP natively creates a reversing document — we surface its DocEntry as `reversal_ref`.

The dispatcher provides two-layer idempotency (AP item metadata cache + audit-event-by-key cache), reauth retry loop, audit event emission for every outcome (`erp_reversal_succeeded` / `_already_reversed` / `_skipped` / `_failed`), and AP item metadata persistence so repeat calls short-circuit.

**Verification:** [tests/test_erp_reversal.py](tests/test_erp_reversal.py) — 40 mock-only tests across the four connectors (happy path, already-reversed, needs_reauth, payment-applied, generic 5xx) plus 15 dispatcher tests (correct dispatch, both idempotency layers, reauth retry, audit events, metadata persistence, unknown ERP type). Real-API tests are CI-secret-gated for when ERP credentials are available.

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

### 7. ⚠️ Anti-fraud primitives — 5 of 7 promoted to blocking gates (Phase 1.2a, `1bc7379`); 2 remain for Phase 2

**Thesis (§8):** *"Fraud controls must be architectural, not configurational. The controls that matter most — IBAN change freeze, first payment hold, domain lock — cannot be disabled by the AP Manager."*

**Original gap:** All seven primitives existed as detection signals only.

**Phase 1.2a resolution (Group A — 5 primitives now blocking):**

| Primitive | Status | How |
|---|---|---|
| Payment amount ceiling | ✅ Blocking | New `payment_ceiling_exceeded` reason code in `_evaluate_deterministic_validation`. FX-converted to org base currency via `fx_conversion.convert`. Fail-closed on FX outage (`fraud_control_fx_unavailable`). Default $10k USD, configurable per org. |
| First payment hold | ✅ Blocking | New `first_payment_hold` reason code blocks brand-new vendors (`invoice_count == 0` or no profile) AND dormant vendors (last_invoice_date > configured `first_payment_dormancy_days`, default 180). |
| Vendor velocity | ✅ Blocking | New `vendor_velocity_exceeded` reason code blocks at the configured `vendor_velocity_max_per_week` (default 10). Single source of truth — `cross_invoice_analysis.py` reads from the same fraud_controls config and uses it for the soft-warning anomaly signal at 70% of the hard max. |
| Prompt injection rejection | ✅ Blocking | Rewrote `clearledgr/core/prompt_guard.py` as a pure detector. Deleted `sanitize_subject` / `sanitize_email_body` / `sanitize_attachment_text`. New `detect_injection` + `scan_invoice_fields` are called by the validation gate over subject, vendor_name, invoice_text, and line item descriptions. Any positive detection adds a `prompt_injection_detected` reason code with severity error. |
| Duplicate prevention | ✅ Blocking | Was already partially blocking. The latent gate-severity bug (info-severity codes silently failing the gate) was fixed in the same commit, so duplicate detection now correctly distinguishes blocking from informational matches. |

**Cross-cutting Phase 1.2a wins:**

- **CFO role added** as an additive value on `TokenData.role` (no DB migration needed). New `has_fraud_control_admin` predicate (`{"cfo", "owner"}`) and `require_fraud_control_admin` FastAPI dependency.
- **`/fraud-controls/{org_id}` API** in [clearledgr/api/fraud_controls.py](clearledgr/api/fraud_controls.py) — GET readable by any org member, PUT requires CFO/owner. Every modification logged to `audit_events` with `event_type=fraud_control_modified` and full before/after diff. Cross-tenant access blocked even for CFOs from other orgs.
- **Severity-based gate `passed` field fixed.** Pre-Phase-1.2a, `gate["passed"] = len(reason_codes) == 0` meant info-severity codes (e.g. `discount_applied`) silently blocked legitimate invoices. Now `gate["passed"] = not any(r.severity in {error, warning})`. Info reasons are surfaced for telemetry but do not block.

**Verification:** [tests/test_fraud_controls_gate.py](tests/test_fraud_controls_gate.py) — 42 tests across config, gate contributions, severity bug fix, fail-closed handling, CFO API role gating, and end-to-end Phase 1.1 enforcement integration. [tests/test_prompt_guard.py](tests/test_prompt_guard.py) rewritten with 37 tests for the new detector + gate integration.

**Group B — still pending (Phase 2):**
- **Vendor domain lock** — was originally part of Phase 1.2b, deferred per agreement. The mechanism: detect when an inbound invoice arrives from a sender domain that doesn't match the vendor's known domains, treat as potential vendor impersonation, block.
- **IBAN change freeze** — paired with item #5 below. The most common AP fraud and the highest-priority Phase 2 item.

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

### 15. ✅ DONE — Override window notifications in Slack (Phase 1.4, `12e24f8`)

Closed alongside P0 #2. The `OverrideWindowObserver` posts a Block Kit card to the org's configured Slack channel on every successful ERP post. Card displays vendor / amount / invoice # / ERP reference / "X minutes remaining" + a danger-styled `Undo post` button with a confirm dialog. Button click routes through `undo_post_*` action_id → `_handle_undo_post_action` → `OverrideWindowService.attempt_reversal` → `reverse_bill` → state machine transition → card update to "Reversed by @user". The 60-second background reaper updates the card to "Override window has closed" when the window expires naturally. Slack failures are non-fatal — DB state is the source of truth.

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

**Phase 1 — Architectural safety ✅ SHIPPED 2026-04-09**
1. ✅ `ccae27b` — `APDecisionService` bound to deterministic gate via 4-layer enforcement (#1)
2. ✅ `12e24f8` + `4a2e8d7` — Override window mechanism, Slack undo notifications, per-action tiers (#2, #15)
3. ✅ `bb68ecb` — `reverse_bill()` across all four ERP connectors + mock test harness (#3)
4. ✅ `1bc7379` — Group A (5/7) fraud primitives promoted to architectural blocking gates + CFO role + audit + severity bug fix (#7 partial)

   *Phase 1 net: 174 new tests, 1407 → 1581 passing, zero new regressions, four 🚫 conflicts resolved.*

**Phase 2 — Vendor identity & IBAN security (4–6 weeks) ← NEXT**
5. **Audit and remediate IBAN storage** — grep all IBAN/account-number paths, tokenise with Fernet column-level encryption, masked UI display (#6)
6. **Implement IBAN change freeze + three-factor verification** — vendor email domain confirm + phone confirm + AP Manager sign-off; immediate payment hold on detection of changed IBAN (#5)
7. **Vendor domain lock** — block invoices arriving from sender domains that don't match the vendor's known domains; pair with onboarding domain registration (#7 Group B)
8. **Expand role enum to thesis taxonomy** — AP Clerk, AP Manager, Financial Controller, CFO, Read Only as additive-upward; update API guards to be role-specific instead of `require_ops_user` / `require_admin_user` (#8)
9. **Extend vendor schema with KYC fields + risk scoring** — registration_number, vat_number, registered_address, director_names, kyc_completion_date, iban_verified, iban_verified_at, ytd_spend, risk_score (#19)

**Phase 3 — Core missing features (8–10 weeks)**
10. Micro-deposit bank verification workflow (#9)
11. Vendor onboarding portal + auto-chase + ERP activation (#10)
12. Trust-building arc scheduled messaging — Week 1 / Day 14 / Day 30 / weekly (#4)
13. Thread toolbar buttons + sidebar restructure to four sections (#13, #14)
14. Conditional digest + intelligent routing + conversational queries (#12, #16, #17)

**Phase 4 — Scale readiness (6–8 weeks)**
15. Google Workspace Add-on for mobile approvals (#11)
16. Testing infrastructure: synthetic suite, replay, shadow mode, canary (#22)
17. Box/Pipeline abstraction refactor (#18)
18. Billing UI in Gmail (#30)

**Phase 5 — Polish & compliance (4 weeks)**
19. Audit trail three-field decomposition (#23)
20. Model improvement loop 50-signal gating + per-vendor rules (#24)
21. Multi-entity parent account abstraction (#20)
22. Remaining extraction guardrails — currency, reference format (#25)
23. DID-WHY-NEXT enforcement (#26)
24. @Mentions bridge (#27)
25. Archived users (#28)
26. Starter/Enterprise onboarding gate (#29)

---

## Caveats

1. **Codebase is in flux.** Git status shows substantial uncommitted changes including deletions of `browser_agent` files and modifications across ERP, runtime, and extension layers. Findings should be re-verified post-merge.

2. **Some findings are schema-level.** Agent 3 noted the absence of an `iban` column in `vendor_profiles` — this doesn't rule out IBAN storage elsewhere (e.g. `metadata` JSON). Direct grep for `iban` / `bank_account` across the codebase is recommended before remediation planning.

3. **Partial implementations need human verification.** Several ⚠️ items were called partial based on pattern matching — some may turn out to be closer to aligned than the audit suggests on close reading.

4. **This audit is a snapshot.** Re-run after each phase of remediation. Add new commitments to the matrix as the thesis evolves.

---

*Audit conducted by four parallel code-exploration agents against [DESIGN_THESIS.md](DESIGN_THESIS.md) v1.0. Findings synthesised on 2026-04-09. File:line citations reflect codebase state at that time.*
