# Clearledgr API Reference (AP v1-Aligned)

This document is the AP v1-aligned API reference for Clearledgr’s canonical product surfaces and operational endpoints.

It does **not** attempt to exhaustively document every route currently registered in the codebase. The repository contains legacy and experimental endpoints that may still appear in OpenAPI.

Use this document for AP v1 product-facing and operator/admin-facing APIs, and use runtime OpenAPI for exhaustive route discovery.

## Canonical References

- Doctrine + launch gates + interface expectations: `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
- AP v1 backend contract semantics: `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_BACKEND_CONTRACTS.md`
- Runtime OpenAPI (exhaustive route listing): `/docs`

## Base URLs

```text
Production: https://api.clearledgr.com
Development: http://localhost:8000
```

## Authentication and Security

Clearledgr uses multiple auth/security patterns depending on surface:

1. **JWT bearer auth** for user/admin APIs
2. **API key** for some operational/dev endpoints (where enabled)
3. **Slack request verification** for Slack callbacks/actions
4. **Teams callback verification** for Teams callbacks/actions

Do not assume all endpoints use the same auth mechanism.

---

## 1. System and Console Endpoints

### Health

`GET /health`

Purpose:
- service health status
- version info
- health checks

### Metrics

`GET /metrics`

Purpose:
- backend metrics and operational statistics (exact contents may vary by environment/build)

### Workspace Shell UI

`GET /workspace`

Purpose:
- customer-facing Workspace Shell UI (feature-gated)

Notes:
- may return `404` when `WORKSPACE_SHELL_ENABLED` is disabled

### Internal Admin Page (dev/test)

`GET /admin`

Purpose:
- internal QA/testing page (dev-oriented; not the customer Workspace Shell)

---

## 2. Authentication APIs (AP v1 Relevant)

Authentication routes can vary by environment and enabled integrations. AP v1 commonly uses:

### JWT Login / Register (web/admin)

Examples:
- `POST /auth/login`
- `POST /auth/register`

Use cases:
- Workspace Shell access
- API access for admin/operator workflows

### Google-based Identity / Login (Gmail-linked flows)

Google auth and identity routes are used for:
1. Gmail-linked auth flows
2. Workspace Shell Google login (where configured)
3. Gmail integration setup

Note:
- Legacy `GET /gmail/authorize` has been removed.
- Canonical setup path is `POST /api/workspace/integrations/gmail/connect/start`.

Use runtime OpenAPI (`/docs`) for the exact enabled route set in your build.

---

## 3. Workspace Shell APIs (`/api/workspace/*`)

These endpoints support the customer-facing Workspace Shell (`/workspace`) and first-time setup.

### Bootstrap / Health / Status

- `GET /api/workspace/bootstrap`
- `GET /api/workspace/health`
- `GET /api/workspace/onboarding/status`

Purpose:
1. load org/user/integration/health state for the console
2. surface required actions (connect Gmail, connect Slack/Teams, connect ERP, etc.)
3. track onboarding progress

### Integrations (Gmail / Slack / ERP)

Examples:
- `GET /api/workspace/integrations`
- `POST /api/workspace/integrations/gmail/connect/start`
- Slack install/start/callback endpoints
- Slack channel configuration/test endpoints

Purpose:
1. integration status
2. installation/configuration actions (Admin-initiated OAuth start for Gmail/Slack/ERP)
3. test actions (for example approval card tests)

### AP Policy and Org Settings

Examples:
- `GET /api/workspace/policies/ap`
- `PUT /api/workspace/policies/ap`
- `GET /api/workspace/org/settings`
- `PATCH /api/workspace/org/settings`

Purpose:
1. configure AP policy defaults and org-level settings
2. keep policy changes and org behavior aligned to AP v1 doctrine

### Team and Subscription

Examples:
- `GET /api/workspace/team/invites`
- `POST /api/workspace/team/invites`
- `POST /api/workspace/team/invites/{invite_id}/revoke`
- `GET /api/workspace/subscription`
- `PATCH /api/workspace/subscription/plan`

Purpose:
1. invite-based team onboarding
2. org plan/usage visibility and controls

---

## 4. Gmail Extension APIs (AP v1 Embedded Workflow)

These endpoints power the Gmail embedded AP experience.

### AP Worklist (Preferred)

`GET /extension/worklist`

Purpose:
- invoice-centric AP worklist for the Gmail sidebar/workspace

Contract expectations (see canonical contract docs):
- status, confidence, exceptions, next action, source linkage

### Legacy Compatibility Pipeline (Compatibility Path)

`GET /extension/pipeline`

Purpose:
- compatibility path for older extension flows

Notes:
- AP v1 product contract prefers `/extension/worklist`
- do not treat `/extension/pipeline` as the canonical AP v1 worklist contract unless explicitly required for compatibility

### Triage / Submission / Extension Actions

The Gmail extension may call AP workflow endpoints for:
1. triage
2. submit for approval
3. confidence verification
4. review/override actions
5. retry posting

Exact endpoint names vary by the active implementation and compatibility layers. Use `/docs` for exact route names and this document + `PLAN.md` for canonical semantics.

---

## 5. AP Item APIs (Context, Audit, Item State)

These are core AP v1 APIs for embedded surfaces and operator tooling.

### Get AP Item

`GET /api/ap/items/{ap_item_id}`

Purpose:
- retrieve AP item summary and workflow state

### Get AP Item Context

`GET /api/ap/items/{ap_item_id}/context`

Purpose:
- retrieve normalized cross-system AP context for the selected invoice item

### Get AP Item Audit Trail

`GET /api/ap/items/{ap_item_id}/audit`

Purpose:
- retrieve AP audit breadcrumbs/events for transparency and support

### Get Linked Sources / Source Linking (if enabled in your build)

AP item source-linking endpoints may include:
- source listing
- source linking/unlinking/debug actions

These support the invoice-centric aggregation model and multi-source context.

---

## 6. AP Policy APIs

AP v1 relies on runtime-editable, auditable AP policies.

### AP Policies

Examples:
- `GET /api/ap/policies`
- `PUT /api/ap/policies`

Purpose:
1. configure tenant AP business rules
2. enforce deterministic validation and approval routing
3. support policy versioning and auditability

Exact route variants may differ (`collection` vs `named policy` style). Follow `/docs` for exact signatures.

---

## 7. Ops APIs (AP v1 Operational Visibility)

These endpoints support operational status and KPI visibility for AP v1.

### Autopilot Status

`GET /api/ops/autopilot-status`

Purpose:
- expose AP/Gmail autopilot status used by embedded surfaces and admin/ops UI

### AP KPIs

`GET /api/ops/ap-kpis`

Purpose:
- return AP KPI payload (for embedded or admin visibility)

Examples of metric categories:
- cycle time
- exception rate
- approval turnaround
- throughput / processing counts

### Additional Ops / Diagnostics

Other ops endpoints may exist (browser-agent metrics, connector diagnostics, etc.) depending on build and enabled modules.

---

## 8. Slack and Teams APIs (AP Approvals)

Slack and Teams integrations use callback/webhook endpoints and internal handlers to process approval decisions.

### Slack

AP-v1 relevant behaviors:
1. approval action callbacks
2. message/card interactions
3. verification of Slack request signatures

### Teams

AP-v1 relevant behaviors:
1. approval action callbacks
2. Teams message/action handling
3. callback verification

### Contract requirement (canonical)

Slack and Teams must map to the same approval action semantics for AP v1:
- `approve`
- `reject`
- `request_info`

See:
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_BACKEND_CONTRACTS.md`
- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`

---

## 9. ERP and Integration APIs (AP v1 Context)

The repository may expose ERP onboarding, OAuth, and connector management APIs.

These are relevant to AP v1 when used for:
1. ERP connectivity/auth setup
2. ERP connector readiness checks
3. AP posting support

Important distinction:
- **Connector endpoint exists** != **connector is operationally parity-enabled for AP v1 GA**

Operational parity requirements are defined in:
- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md` (Section 6 ERP parity contract)

---

## 10. API Contract Principles for AP v1 (Normative Summary)

When implementing or consuming AP v1 APIs, the following rules apply:

1. **Server-enforced state machine**
   - clients request actions; they do not set arbitrary AP states

2. **Idempotent approval and posting actions**
   - duplicate callbacks or retries must not duplicate business outcomes

3. **Policy before write**
   - deterministic checks and policy gates before mutating external actions

4. **Audit completeness**
   - every transition and external mutating action must be auditable

5. **Operator-safe errors**
   - errors should be actionable and reason-coded where possible

---

## 11. Legacy and Non-Canonical Endpoints (Important)

This codebase still includes legacy and/or experimental endpoints for other workflows (including reconciliation, Sheets-driven flows, and earlier product directions).

Rules for AP v1 work:
1. Do not assume all routes shown in `/docs` are in AP v1 product scope.
2. Use `PLAN.md` for scope and launch-gate truth.
3. Use `V1_BACKEND_CONTRACTS.md` for AP v1 contract semantics.
4. Treat this `API_REFERENCE.md` as the AP v1-aligned operational map, not an exhaustive generated spec.
5. In production/staging strict profile mode (`AP_V1_STRICT_SURFACES=true`), legacy/non-canonical route families are disabled unless `CLEARLEDGR_ENABLE_LEGACY_SURFACES=true` is explicitly set.

---

## 12. How to Get the Exact Route Signatures

For exact request/response schemas in your running build:

1. Start the backend
2. Open:
   - `http://localhost:8000/docs`
   - `http://localhost:8000/redoc`
3. Verify routes against:
   - `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
   - `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_BACKEND_CONTRACTS.md`

This is the safest workflow because route registration can vary with enabled modules and environment flags.
