# Tier-2 Verification — 2026-04-22

Follow-up record from the four-track hygiene sprint (`plans/floofy-napping-ripple.md`). This file captures what was found, what was fixed, what remains open, and — importantly — what turned out NOT to be a gap when actually verified.

Context: yesterday's audit produced a list of hypothesized gaps. Today's re-verification sweep opened files instead of pattern-matching. Four of six hypothesized gaps dissolved on contact with real code; a few smaller, real items got closed; one non-trivial design question remains open.

---

## B.1 — Multi-tenant isolation: one real gap, closed

**Status:** closed.

**Finding:** Comprehensive cross-org regression coverage already existed for every major org-scoped resource — AP items, audit events, box exceptions, vendor KYC, vendor domain lock — *except* webhook subscriptions. The route handlers at [clearledgr/api/workspace_shell.py:2060-2134](../clearledgr/api/workspace_shell.py#L2060-L2134) enforce `_resolve_org_id` (raises HTTPException 403 on org mismatch), but that enforcement was untested end-to-end.

**Fix:** `TestWebhookCrossOrgIsolation` added to [tests/test_outgoing_webhooks.py](../tests/test_outgoing_webhooks.py). Four tests cover LIST (with and without query-param spoofing), CREATE (with query-param spoofing), and DELETE (by id on another org's webhook). All assert 403 on cross-org access or strict same-org scoping on valid requests.

---

## B.2 — LLM cost budget per workspace: real gap, design question

**Status:** open. Design input needed before implementation.

**What exists today:**
- Per-call cost logging at [clearledgr/core/llm_gateway.py:389-449](../clearledgr/core/llm_gateway.py#L389-L449). Every Claude call writes `{organization_id, cost_estimate_usd, input_tokens, output_tokens, action, model}` to the `llm_call_log` table.
- Monthly per-org aggregation at [clearledgr/services/subscription.py:885-913](../clearledgr/services/subscription.py#L885-L913) — `_get_llm_cost_this_month()` returns call count + total USD since `month_start`. Surfaces in the ops dashboard.
- Per-action input-token budget at [llm_gateway.py:225-297](../clearledgr/core/llm_gateway.py#L225-L297) — messages are truncated if they exceed the action's token ceiling.

**What's missing:** a hard per-workspace monthly cost cap that blocks or throttles Claude calls when hit. Today's posture is observe-only — you can see a workspace burning through budget but nothing stops it.

**Design decisions required before building:**

| Question | Options |
|---|---|
| Cap shape | Fixed USD per tier; customer-configurable per workspace; soft-alert only; hard-block at threshold |
| Behavior when hit | Queue until next billing cycle; fall over to Haiku from Sonnet; block and require operator acknowledgement; alert CS team |
| Notification | Slack to CS channel; email to account owner; in-product banner; webhook event `billing.budget_exceeded` |
| Override | Can an operator lift the block mid-cycle? With audit? |
| Multi-entity | Budget per-entity or roll up to parent org? (Relevant when multi-entity workspace structure lands in Q2.) |

**Recommended next step:** small product discussion with Joseph (CRO) + Mo on tier economics. Once the shape is decided, implementation is ~100 lines in `llm_gateway.py:call()` — pre-flight check against `_get_llm_cost_this_month` vs `sub.limits.monthly_llm_usd`, raise or queue on breach.

**Not in this sprint:** implementation. This is a business-model question, not a code hygiene one.

---

## B.3 — GDPR: the gap that wasn't, and the one that remains

### B.3 gap 1 — Scheduled hard-purge of soft-deleted orgs

**Status:** **not a gap.** Hypothesis dissolved on inspection.

**What yesterday's audit said:** "No scheduled job invokes `list_orgs_eligible_for_purge` + `purge_organization_data`. Soft-deleted orgs sit past legal-hold indefinitely."

**What the code actually says:**
- Celery task `purge_soft_deleted_orgs` at [clearledgr/services/celery_tasks.py:332-408](../clearledgr/services/celery_tasks.py#L332-L408) — full GDPR Article 17 flow: calls `list_orgs_eligible_for_purge(legal_hold_days=ORG_LEGAL_HOLD_DAYS)`, iterates eligible orgs, runs `purge_organization_data(org_id)`, stamps `purged_at`, emits `organization_hard_purged` audit event per org.
- Scheduled daily at [clearledgr/services/celery_app.py:99-108](../clearledgr/services/celery_app.py#L99-L108) (`"purge-soft-deleted-orgs"` entry in `beat_schedule`, `24 * 60 * 60.0` seconds).
- Legal-hold window is configurable via `ORG_LEGAL_HOLD_DAYS` env var (default 30).
- Underlying store methods covered by [tests/test_org_purge.py](../tests/test_org_purge.py) (108 lines, exercises `purge_organization_data` directly with mixed-org fixtures and no-op edge cases).

**Known follow-up (small, not in this sprint):** the Celery orchestration layer (`purge_soft_deleted_orgs` as a whole) has no direct test. The function it calls is well-covered; the wrapper that iterates orgs + stamps `purged_at` + emits audit events is not. Worth adding ~30 lines of test eventually, but not a gap that blocks shipping.

### B.3 gap 2 — Data export endpoint is a stub

**Status:** open. Scope sketch captured; implementation deferred.

**Finding:** [clearledgr/api/org_config.py:745-776](../clearledgr/api/org_config.py#L745-L776) — `POST /api/organizations/{id}/gdpr/data-export-request` returns a fabricated request ID, status `queued`, and an "estimated 24-hour completion" message. The comment at L767 explicitly says *"would queue a background job"*. There is no background job.

**Scope sketch for a real implementation:**
1. New Celery task `export_organization_data(org_id, request_id)` that:
   - Streams rows from every org-scoped table (same discovery mechanism as `purge_organization_data`)
   - Applies PII scrubbing policy (tokenize IBANs, redact internal system identifiers that belong to Clearledgr, not the customer)
   - Writes to a signed, expiring S3/GCS URL
   - Emits `organization_data_exported` audit event
2. Enqueue from the endpoint instead of the fabricated response.
3. Handle completion: send email to account owner with signed URL + 72h expiry.
4. Audit trail: every export request logged, retained indefinitely.

**Estimate:** ~200-300 lines, 1-2 days. Needs a storage decision (S3 vs GCS) + PII scrub policy.

**Not in this sprint:** needs product input on PII policy and storage infra decision before build.

---

## B.4 — Runbooks: 10 scenarios enumerated, not written

**Status:** partial. Gap list surfaced in [docs/RUNBOOKS.md](./RUNBOOKS.md) as "Known gaps — unwritten runbooks." Individual runbooks deferred to CS/on-call input.

**Already documented** (from the Explore survey): ERP auth expiry (all 4 providers), Slack callback failures, Teams callback failures, ERP posting failure recovery, Gmail watch renewal, per-tenant rollback procedures, AP item resubmission, database maintenance, nightly Gmail runtime smoke.

**Gaps enumerated in RUNBOOKS.md:** open-banking adapter failures, IBAN verification timeout, vendor portal outage, mass invoice spike / queue back-pressure, stuck waiting conditions, Postgres failover / replica lag, Redis cache failure, Celery fleet down, Slack platform outage (not just callback failures), subscription limit breach operator response.

**Not in this sprint:** writing the runbooks themselves. Each needs real operational context — the response steps for "Postgres failover" depend on whether the production cluster is managed (Railway, Neon, RDS, etc.), and "Celery fleet down" depends on what monitoring/alerting infra the ops team uses. This is a CS/on-call docs sprint, not a code hygiene one.

---

## C — SQLite/Postgres dual-track: staged plan, awaiting separate approval

Not executed this sprint. Scope + stage plan captured in [plans/floofy-napping-ripple.md](../../..//Users/mombalam/.claude/plans/floofy-napping-ripple.md) (Phase C). Three stages: make Postgres the default dev DB (C.1), delete SQLite branches (C.2), cleanup (C.3). Total ~600-700 lines removed across 20-24 files if all stages execute. Requires separate approval before any stage lands.

---

## D — Naming sediment: one item, closed

**Status:** closed.

Systematic survey across `clearledgr/**/*.py + ui/gmail-extension/src/**/*.js` found exactly one real sediment item: a description string in [clearledgr/workflows/ap_workflow.py:98](../clearledgr/workflows/ap_workflow.py) referenced an "API-first with browser-agent fallback" that was removed. Fixed in commit `b7dd51d`.

Everything else in the sediment survey was either (a) legitimate historical migration records (`LEGACY_STATE_MAP`, `_LEGACY_ROLE_MAP`, `SkillResponse.from_legacy`), (b) sensible fallback patterns (`_fallback_password_context`), or (c) cosmetic-only rename candidates not worth touching (`looksEphemeralStoredHost`, migration function underscore prefixes).

---

## Process observation

Four of six hypothesized Tier-2 gaps turned out not to be gaps when the files were actually read:

| Hypothesis | Reality |
|---|---|
| Governance / deliberation layer is thin | 456-line `finance_agent_governance.py` exists, wired, sophisticated |
| Trust arc is not wired | 461-line `trust_arc.py` implements thesis §7.5 end-to-end |
| Monitoring / alerting doesn't exist | 586-line `monitoring.py` with configurable thresholds + multi-channel delivery |
| Subscription-level usage quota not enforced | `check_limit()` called at every post-to-ERP endpoint |
| Hard-purge scheduler not wired | Scheduled daily at celery_app.py:99-108 |

Plus the one real genuine gap that did turn up (webhook isolation test) which was closed this sprint.

**Pattern:** the codebase is consistently more complete than the hypothesis list suggested. Going forward, when a gap is claimed, the default assumption should be *"open the file and read before trusting the claim,"* including claims from Explore agents. This document exists so the full accounting is on the record, not just the narrow "closed gap" commit.

---

## What's left before shipping the deck

- **B.2 (LLM cost cap):** product decision, then ~1 day build.
- **B.3 gap 2 (data export):** product decision on PII + storage, then 1-2 days build.
- **B.4 (runbooks):** CS/on-call sprint.
- **C (SQLite/Postgres):** separate approval, 1-3 days for C.1, more for C.2-C.3.

None of these block the Problem + Solution pitch narrative. They're operational hygiene for what comes after the deck.
