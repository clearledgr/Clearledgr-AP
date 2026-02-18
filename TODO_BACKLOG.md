# Clearledgr AP v1 Execution TODO Backlog

## Summary
Canonical engineering backlog derived from `/Users/mombalam/Desktop/Clearledgr.v1/gaps_opportunities`, prioritized as `Now / Next / Later`, with mixed granularity:
- `Now`: detailed, implementable tasks.
- `Next`: detailed but shorter implementation criteria.
- `Later`: strategic epics with clear scope and ownership.

## Scope and Defaults
- Canonical backlog file: `/Users/mombalam/Desktop/Clearledgr.v1/TODO_BACKLOG.md`
- Strategic source remains: `/Users/mombalam/Desktop/Clearledgr.v1/gaps_opportunities`
- Priority model: `Now / Next / Later`
- Existing implemented foundations are tracked under `Deferred / Skip` and are not reopened as net-new TODOs.

## Release Buckets
- `v1-core`: required to ship AP v1.
- `v1.1`: important follow-on work immediately after v1 ship.
- `later`: strategic roadmap beyond near-term releases.

## TODO Item Schema
`ID | Priority | Type (Foundational/Polish) | Scope | Owner Role | Dependencies | Code Touchpoints | API/Type Changes | Acceptance Criteria | Status | Release`

## Now (Foundational)
- `CL-AP-001 | Now | Foundational | PO/receipt/budget validation gate in workflow | Owner Role: Backend AP | Dependencies: ERP read adapter for PO+receipt, budget source adapter | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/policy_compliance.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/budget_awareness.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/purchase_orders.py | API/Type Changes: extend AP item metadata with po_match_result and budget_check_result | Acceptance Criteria: invoices with PO mismatch or over-budget route deterministically with reason codes and audit events | Status: Implemented (2026-02-18) | Release: v1-core`
- `CL-AP-002 | Now | Foundational | Budget-aware approval context in Gmail + Slack/Teams | Owner Role: Backend + Extension | Dependencies: CL-AP-001 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py; /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py | API/Type Changes: add budget block + explicit budget decision endpoint/actions | Acceptance Criteria: approver sees remaining budget + overage impact and explicit decision path in Gmail and chat approvals | Status: Implemented (2026-02-18) | Release: v1-core`
- `CL-AP-003 | Now | Foundational | Declarative tenant AP policy framework (beyond env vars) | Owner Role: Backend Platform | Dependencies: none | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/policy_compliance.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_policies.py | API/Type Changes: add AP policy read/write/version/audit APIs + policy-driven routing gates | Acceptance Criteria: org-level thresholds/routing rules editable at runtime, versioned, audit-visible, and applied in approval routing | Status: Implemented (2026-02-18) | Release: v1-core`
- `CL-AP-004 | Now | Foundational | Exception taxonomy + exception-first queue ordering | Owner Role: Backend + Extension | Dependencies: CL-AP-001, CL-AP-003 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py; /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/queue-manager.js | API/Type Changes: add exception_code and exception_severity to worklist item | Acceptance Criteria: queue defaults to exception-priority with deterministic ordering and visible exception reason | Status: Implemented (2026-02-18) | Release: v1-core`
- `CL-AP-005 | Now | Foundational | AP KPI primitives + ops API for automation KPIs | Owner Role: Backend Ops | Dependencies: CL-AP-004 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ops.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py | API/Type Changes: add AP KPI endpoint (touchless rate, cycle time, exception rate, on-time approvals, missed discounts baseline) | Acceptance Criteria: KPI endpoint returns tenant-scoped metrics with tests and stable API contract | Status: Implemented (2026-02-18) | Release: v1-core`

## Next (Scale + UX Intelligence)
- `CL-AP-006 | Next | Foundational | Expand source/context connectors (ERP docs, procurement, DMS, payment portals) | Owner Role: Backend Integrations | Dependencies: CL-AP-005 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/ap_context_connectors.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/accruals.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py; /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js | API/Type Changes: extend normalized sources/context payload for non-email systems | Acceptance Criteria: selected invoice context can include bank/procurement/payroll/spreadsheet refs with graceful partial failures | Status: Implemented (2026-02-18) | Release: v1-core`
- `CL-AP-007 | Next | Foundational | Approval friction analytics (handoffs, wait time, SLA breach) + simplification signals | Owner Role: Backend Analytics | Dependencies: CL-AP-004, CL-AP-005 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ops.py | API/Type Changes: extend ops KPI surface with routing friction metrics | Acceptance Criteria: metrics and top-friction paths are queryable per tenant | Status: Partial (2026-02-18, friction metrics are live; dedicated simplification recommendation layer is still open) | Release: v1.1`
- `CL-AP-008 | Next | Foundational | Payment outcome signals (discount opportunity + late-payment risk score) in approval context | Owner Role: Backend AP | Dependencies: CL-AP-001, CL-AP-005 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py | API/Type Changes: add risk_signals block to context payload | Acceptance Criteria: approval context includes discount opportunity and late-payment risk with source evidence | Status: Partial (2026-02-18, discount handling exists; normalized risk_signals generation is not fully implemented) | Release: v1.1`
- `CL-AP-009 | Next | Polish | Manual merge/split controls with audit logs for ambiguous clusters | Owner Role: Backend + Extension | Dependencies: existing merge model | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py; /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js | API/Type Changes: add merge/split action endpoints and action audit entries | Acceptance Criteria: operator can merge/split with explicit rationale and full audit trail | Status: Implemented (2026-02-18) | Release: v1-core`
- `CL-AP-010 | Next | Polish | Source quality/recency ranking and context freshness SLAs with stale badges | Owner Role: Extension + Backend | Dependencies: CL-AP-006 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js; /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/queue-manager.js; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py | API/Type Changes: add freshness metadata and source ranking fields to context response | Acceptance Criteria: tabs show stale/fresh state and preferred source ordering | Status: Partial (2026-02-18, freshness/stale indicators are live; source ranking remains basic metadata) | Release: v1.1`
- `CL-AP-011 | Next | Polish | Queue navigator intelligence (urgency/risk/SLA) and conflict action cards | Owner Role: Extension + Backend | Dependencies: CL-AP-004, CL-AP-010 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js; /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/queue-manager.js; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py | API/Type Changes: add priority_score and conflict action metadata to worklist/context | Acceptance Criteria: next item ordering is risk/SLA-aware and conflict cards are actionable | Status: Partial (2026-02-18, conflict action cards and priority sorting are live; advanced urgency/risk synthesis is still maturing) | Release: v1.1`
- `CL-AP-012 | Next | Foundational | Embedded KPI visibility in Gmail + Slack/Teams digests (no standalone dashboard) | Owner Role: Extension + Integrations | Dependencies: CL-AP-005 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/slack_api.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ops.py | API/Type Changes: consume AP KPI endpoint in extension/chat summary surfaces | Acceptance Criteria: KPI summaries are visible in embedded surfaces without introducing a standalone dashboard | Status: Partial (2026-02-18, Gmail KPI snapshot and digest API are live; fully wired Slack/Teams delivery flows remain open) | Release: v1.1`

## Later (Strategic Expansion)
- `CL-AP-013 | Later | Foundational | AP automation maturity scorecard APIs and onboarding checks | Owner Role: Backend Analytics | Dependencies: CL-AP-005 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ops.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py | API/Type Changes: maturity scorecard endpoint and model | Acceptance Criteria: onboarding/review can compute maturity from measured signals | Status: Open | Release: later`
- `CL-AP-014 | Later | Foundational | Fraud/compliance controls (sanctions, TIN/VAT, bank-change alerts) | Owner Role: Backend Compliance | Dependencies: CL-AP-001, CL-AP-003 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py | API/Type Changes: compliance result fields in validation/context payloads | Acceptance Criteria: high-risk compliance failures block or escalate with auditable reason codes | Status: Open | Release: later`
- `CL-AP-015 | Later | Foundational | Vendor self-service onboarding/status portal | Owner Role: Product + Backend | Dependencies: CL-AP-003 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py | API/Type Changes: vendor onboarding and status APIs | Acceptance Criteria: vendors can submit required docs and track AP status through a controlled flow | Status: Open | Release: later`
- `CL-AP-016 | Later | Foundational | Multi-entity/multi-currency/multi-language support hardening | Owner Role: Platform | Dependencies: CL-AP-001, CL-AP-003 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py | API/Type Changes: extended tenant/entity/currency locale contracts | Acceptance Criteria: AP workflow supports multi-entity and currency-safe processing semantics | Status: Open | Release: later`
- `CL-AP-017 | Later | Polish | Advanced AI anomaly and recommendation layer | Owner Role: ML/Backend | Dependencies: CL-AP-004, CL-AP-005 | Code Touchpoints: /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services; /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ops.py | API/Type Changes: anomaly recommendation payload contract | Acceptance Criteria: recommendation quality is measurable and non-deterministic paths remain policy-gated | Status: Open | Release: later`

## Deferred / Skip (already implemented foundations)
1. Focus-first single-item workspace baseline
2. Invoice-centric worklist baseline
3. Linked source persistence baseline
4. Context endpoint and cache baseline
5. Browser-agent and agentic enhancements baseline (Point 7: scoped policy, preflight preview, retry/recovery, observability, API-first ERP fallback)

## Planned Public API / Interface / Type Changes
1. AP policy management APIs:
- `GET /api/ap/policies?organization_id=...`
- `PUT /api/ap/policies/{policy_name}`

2. Worklist contract extensions:
- `/extension/worklist` item adds `exception_code`, `exception_severity`, `budget_status`, `priority_score`

3. Context contract extensions:
- `/api/ap/items/{ap_item_id}/context` adds normalized `po_match`, `budget`, `risk_signals`, `freshness`

4. Ops KPI API additions:
- `GET /api/ops/ap-kpis?organization_id=...` with touchless/cycle/exception/on-time/missed-discount metrics

5. Merge control APIs:
- `POST /api/ap/items/{ap_item_id}/merge`
- `POST /api/ap/items/{ap_item_id}/split`

## Backlog Validation Scenarios
1. Mapping integrity: every open/partial item in Sections 8 and 9 maps to one backlog ID.
2. Deduplication: overlapping Gap/Opportunity/TODO statements map once only.
3. Priority consistency: foundational controls are all in `Now`.
4. Contract clarity: each API-changing TODO names endpoint and payload deltas.
5. Acceptance readiness: each `Now` TODO has a verifiable done condition.
6. Coverage: `Deferred / Skip` only contains already-implemented foundations.
7. Review UX: approver path receives budget/PO context without technical clutter.
8. Ops usage: tenant KPI payloads can power embedded digest delivery.
