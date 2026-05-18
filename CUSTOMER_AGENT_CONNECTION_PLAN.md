# Customer-side agent connection — implementation plan

**Status:** planned, not started. Authored 2026-05-18.

## Goal

Make Solden's runtime callable by customer-side agents (service accounts,
LLM agents, autonomous workflows). Today only Solden's own agents
connect; customers cannot. This is the runtime claim's missing proof: an
external agent that can authenticate, read typed records, dispatch
intents through the same audit-chained substrate Solden uses internally,
and see the resulting state changes.

Per [memory/project_runtime_vs_platform_positioning.md](../.claude/projects/-Users-mombalam-Desktop-Solden-v1/memory/project_runtime_vs_platform_positioning.md),
this is the first piece in the canonical build order:

> Build order:
> 1. **Customer-side agent connection** (3-6 weeks) — this plan
> 2. Second Box type end-to-end
> 3. Public SDK + custom Box-type registration

Closing this gap converts "we have a runtime" from architectural claim
to demonstrable product surface — investors and technical buyers see a
real external agent executing audited, policy-gated intents against
live records.

## The shape we're shipping

Five sub-pieces, each independently testable:

1. **API key + service-account auth** for agent callers.
2. **Agent identity in the audit chain**: extend `audit_events.actor`
   with `actor_type` ∈ {`human`, `service`, `agent`, `system`},
   `agent_id`, `agent_version`. Additive migration. Hash chain stays
   backward-compatible.
3. **Public intent endpoint**: existing `/api/agent/intents/execute`
   re-exposed at `/v1/intents/execute` with API-key auth and a stable
   schema.
4. **Generic Box read API**: new `/v1/records` (and
   `/v1/records/{id}`) returning the canonical `BoxSummary` shape.
   Box-type-agnostic by construction so the second Box type ships
   without re-architecting the API.
5. **Developer surface**: API keys management page in the workspace,
   reference docs, one quickstart (auth → list records → execute
   intent → see audit row).

## Current state

### 1. API key auth (partial)

- `api_keys` table exists ([clearledgr/core/database.py:1299](clearledgr/core/database.py#L1299)).
- `db.validate_api_key(api_key)` resolves a key to `{user_id,
  organization_id, ...}` ([clearledgr/api/deps.py:67](clearledgr/api/deps.py#L67)).
- `soft_org_guard` allows API-key callers to pass org enforcement.

**Gaps:**
- No `scope` / `permissions` field on the key — every key is
  effectively all-powerful within its org. Customer agent keys need
  a narrower scope (e.g., `intents:execute`, `records:read`).
- No issuance UI. Keys would have to be inserted by hand.
- No revocation flow visible to customers.
- No display of last-used / created-by / expires-at — minimum for a
  trustable management UI.

### 2. Agent identity in audit chain (partial)

- `audit_events` already has `actor_type TEXT` and `actor_id TEXT`
  ([clearledgr/core/database.py:1050-1071](clearledgr/core/database.py#L1050)).
- `actor_type` values already emitted in the wild: `human`, `user`,
  `agent`, `api`, `system`, `cs_team`, `erp_webhook`, `external_idp`,
  `service`.

**Gaps:**
- No `agent_id` column (the specific agent identity — for Solden's
  internal agents this is implicit; for customer agents it has to be
  recordable).
- No `agent_version` column.
- `actor_type` values are not standardised — `human` and `user` both
  appear; `agent` and `service` overlap with `api`.

### 3. Public intent endpoint (partial)

- Router exists: `/api/agent/intents/execute` at
  [clearledgr/api/agent_intents.py:139](clearledgr/api/agent_intents.py#L139).
- Routes through `get_current_user` — JWT-only today; API-key path
  exists in `deps.py` but the intent router itself doesn't use it.
- Has `/preview` (dry-run) and `/execute` (commit) endpoints. Plus
  `/skills` discovery.

**Gaps:**
- Endpoint lives under `/api/agent/*` (looks internal). For a public
  contract it should live under `/v1/intents/*`.
- No published JSON schema for the intent payloads. Customers need to
  know "what does an intent look like" without reading our code.
- No rate-limiting documented for the public surface.
- Error responses are FastAPI defaults — should switch to typed error
  envelopes (`{error_code, message, retry_after?}`).

### 4. Generic Box read API (missing)

- `BoxSummary` dataclass exists at
  [clearledgr/core/box_summary.py](clearledgr/core/box_summary.py) with
  `build_box_summary()`. Good primitive — Box-type-agnostic.
- `/api/workspace/ap-items/{id}/detail` returns the AP-shaped detail
  payload. Workspace-specific.
- AP-only routes at `/api/ap/items/*` exist for the Gmail extension.

**Gaps:**
- No `GET /v1/records` (list with state filter, pagination).
- No `GET /v1/records/{id}` (single record by ID).
- The AP-detail endpoint returns AP-shaped fields the customer's agent
  doesn't need (`gmail_thread_id`, `slack_card_id`, etc.).

### 5. Developer surface (missing)

- No API keys management page in
  [ui/web-app/src/routes/pages](ui/web-app/src/routes/pages).
- No public API docs (FastAPI auto-generates `/docs` but it's internal).
- No quickstart, no SDK, no example agent.

## Contract — what we promise customer agents

### Authentication

```
Authorization: Bearer sk_live_<32 chars>
```

API keys carry:
- `key_id` (public prefix `sk_live_xxxx` or `sk_test_xxxx`)
- `secret` (hashed at rest, full value shown once at issuance)
- `organization_id` (single org per key)
- `agent_id` (the identity bound to this key — e.g., `agent:cs-bot-prod`)
- `agent_version` (e.g., `2.4.1` — optional, recorded in audit)
- `scopes` (array of scope strings, e.g. `["intents:execute",
  "records:read"]`)
- `expires_at` (optional)
- `last_used_at`
- `created_at`, `created_by`, `revoked_at`

### Endpoints

```
POST /v1/intents/execute       — dispatch a typed intent
POST /v1/intents/preview       — dry-run, no side effects
GET  /v1/intents               — list available intents for caller's scope

GET  /v1/records               — list records (?box_type=&state=&page=)
GET  /v1/records/{id}          — single record (returns BoxSummary)

GET  /v1/audit                 — caller's audit events (?box_id=&from=&to=)
                                  filtered to caller's org

GET  /v1/health                — alive check (no auth)
GET  /v1/me                    — caller identity echo (auth required)
```

### Audit row shape (after additive migration)

Every intent execution writes one `audit_events` row with:

```
actor_type:    "agent"  (canonical for customer-side agents)
actor_id:      <api_key.agent_id>  e.g. "agent:cs-bot-prod"
agent_version: <api_key.agent_version>  e.g. "2.4.1"
source:        "public_api"
```

Plus the existing `event_type`, `box_id`, `box_type`, `payload_json`,
`organization_id`, `policy_version`, `idempotency_key`, hash-chain
fields.

### Error envelope

```json
{
  "error_code": "invalid_scope",
  "message": "API key lacks 'intents:execute' scope",
  "request_id": "req_01HXYZ..."
}
```

Stable error_code values: `invalid_token`, `invalid_scope`, `not_found`,
`invalid_request`, `state_conflict`, `rate_limited`, `internal_error`.

## Implementation steps

### Step 1 — Migration: extend api_keys + audit_events

**Files:** [clearledgr/core/migrations.py](clearledgr/core/migrations.py)
(new migration), [clearledgr/core/database.py](clearledgr/core/database.py)
(schema definitions).

- `api_keys`: add `scopes TEXT` (JSON array stored as text),
  `agent_id TEXT`, `agent_version TEXT`, `last_used_at TEXT`,
  `revoked_at TEXT`. Backfill `scopes='["intents:execute",
  "records:read"]'` for existing keys (preserves current behaviour).
- `audit_events`: add `agent_version TEXT`. (`actor_type` and
  `actor_id` already exist.)
- Normalise existing `actor_type` strings: a one-shot script that
  rewrites `'user'` → `'human'`, `'api'` → keep, etc. Optional cleanup;
  not blocking.

**Why additive:** hash chain stays valid. New columns default to NULL
for existing rows. New rows fill them.

### Step 2 — API key auth dependency for the public surface

**Files:** new [clearledgr/api/v1_auth.py](clearledgr/api/v1_auth.py),
[clearledgr/core/auth.py](clearledgr/core/auth.py) (resolve helper).

- New FastAPI dep `require_agent_key(scope: str)` that returns an
  `AgentIdentity` dataclass with `{key_id, organization_id, agent_id,
  agent_version, scopes}`. Raises `AuthorizationDenied` if the key is
  missing, invalid, revoked, expired, or lacks the scope.
- Scope check is a simple set membership test against the key's
  `scopes` column.
- Update `last_used_at` on each successful authentication. Cheap; one
  UPDATE per request.
- Reuses the AuthorizationDenied funnel shipped in commit
  `f5d542a7` — every rejected agent call lands in audit as
  `event_type=authorization_denied`.

### Step 3 — `/v1/intents` router

**Files:** new [clearledgr/api/v1_intents.py](clearledgr/api/v1_intents.py),
register in main.py.

- `POST /v1/intents/execute` — same body as the existing
  `/api/agent/intents/execute` but auth via `require_agent_key
  ("intents:execute")`. Writes `actor_type="agent"`,
  `actor_id=<agent_id>`, `agent_version=<version>`, `source="public_api"`
  into the audit row.
- `POST /v1/intents/preview` — dry-run, same auth + scope.
- `GET /v1/intents` — list available intents (filtered to caller's
  scope, suitable for an agent doing discovery).
- Typed error envelopes via a small `error_response(code, message)`
  helper.

### Step 4 — `/v1/records` router

**Files:** new [clearledgr/api/v1_records.py](clearledgr/api/v1_records.py).

- `GET /v1/records` — list records. Query params: `box_type` (string,
  required), `state` (optional), `cursor` (opaque pagination token),
  `limit` (default 50, max 200), `fields` (optional CSV of fields to
  include — defaults to BoxSummary canonical set). Returns
  `{records: [...], next_cursor: "..."}`.
- `GET /v1/records/{id}` — single record. Returns BoxSummary shape.
- Auth via `require_agent_key("records:read")`.
- Per-tenant filter enforced by the auth dependency — the agent's
  `organization_id` is the only org returned, never inferable.

### Step 5 — `/v1/audit`, `/v1/me`, `/v1/health` (minimum surface)

**Files:** extend [clearledgr/api/v1.py](clearledgr/api/v1.py).

- `GET /v1/health` — no auth.
- `GET /v1/me` — echo back the caller's `{agent_id, agent_version,
  organization_id, scopes}`. Useful first call for any agent.
- `GET /v1/audit?box_id=&from=&to=` — read-only audit history for the
  caller's org. Scope `audit:read`.

### Step 6 — API keys management page in workspace

**Files:** new
[ui/web-app/src/routes/pages/ApiKeysPage.js](ui/web-app/src/routes/pages/ApiKeysPage.js),
new backend route `/api/workspace/api-keys/*`.

- Page lists existing keys (masked except prefix), shows `agent_id`,
  `scopes`, `created_at`, `last_used_at`, `revoked_at`.
- "Issue new key" modal: capture `agent_id`, `agent_version` (optional),
  `scopes` (checkboxes), `expires_at` (optional). Shows the full secret
  once on success; copy-to-clipboard.
- "Revoke" button per key.
- Admin-only route (uses existing role check).

### Step 7 — Developer docs page

**Files:** new [soldenai-landing/docs/](soldenai-landing/docs/) section
OR a separate `docs.soldenai.com` host (decision in open questions).

- Reference for each endpoint (request/response shape, errors).
- Authentication walkthrough.
- Quickstart: bash + Python snippets to (a) auth, (b) list records,
  (c) execute one intent, (d) read the resulting audit row.
- Error code reference.

### Step 8 — Tests

- Backend integration tests under [tests/test_v1_*.py](tests/):
  - Unauthorised request → 401 with typed error envelope + audit row
    written.
  - Wrong-scope request → 403 + audit row.
  - Cross-tenant access (key org X reading record from org Y) → 403 +
    audit row.
  - Happy-path intent execution → 200 + record state transition +
    audit row with `actor_type="agent"`, `agent_version` set.
  - Records list pagination cursor.
  - Records list filtered by state and box_type.
- Frontend component tests for ApiKeysPage.

## Open questions

1. **Scope vocabulary.** The plan uses
   `intents:execute, records:read, audit:read`. Are there other minimum
   scopes needed for v1 (e.g., `intents:preview` as separate from
   execute)? Sub-decision: should preview be free (no scope), since
   it's read-only?

2. **Rate limits.** Per-key? Per-org? What numbers? The existing
   `RateLimitMiddleware` covers org-level; per-key needs a new
   middleware or a wrapping decorator. Suggest: org limit (1000 req/min)
   + per-key limit (100 req/min) for v1.

3. **Idempotency keys for `/v1/intents/execute`.** Client passes
   `Idempotency-Key` header → server stores result and returns the same
   response on retry. Standard pattern (Stripe). Yes/no for v1?

4. **Webhooks.** Should `/v1` ship with subscription for state
   transitions (so a customer agent can react to changes without
   polling)? `webhook_subscriptions` table already exists at
   [clearledgr/core/database.py:1093](clearledgr/core/database.py#L1093).
   v1 minimum vs. v1.1 follow-up?

5. **Docs hosting.** Subfolder under soldenai-landing (`/docs/...`),
   or a separate `docs.soldenai.com` (e.g., GitBook / Mintlify)? The
   former keeps it cheap and in-repo; the latter scales better as the
   surface grows. Suggest: in-repo for v1, migrate later if needed.

6. **First customer.** Who's the design partner? Plan assumes one
   customer agent ships against this surface in parallel with the
   build, so we catch DX issues before public release.

7. **SDK vs. raw HTTP.** Plan ships HTTP only. A thin Python +
   TypeScript SDK is a natural follow-up but not in scope here. Should
   the quickstart show raw `curl` + `requests`, or also a
   `pip install solden` snippet that doesn't yet exist?

## Out of scope

- Custom Box-type registration (todo #3, gated on this + second Box type).
- The second Box type itself (todo #2).
- A `capability` sub-Box primitive (todo #6, decision deferred).
- Multi-org keys (one key, one org, period).
- OAuth (API keys only for v1; OAuth for the human-side surfaces).
- A hosted "playground" UI.
- SDKs in any language.

## Test plan

1. Local: spin up the dev stack. Issue a key via the new management
   page. Call `/v1/me` with curl — receive the agent identity echo.
2. `/v1/records?box_type=ap_item&state=needs_approval` — receive the
   list, paginate through it.
3. `/v1/intents/execute` with `{intent: "approve_invoice", input:
   {ap_item_id: "..."}}` — observe state transition in the workspace,
   audit row with `actor_type="agent"`, `agent_version` set.
4. Revoke the key in the management page → next call returns 401 with
   `error_code: "invalid_token"`, audit row written.
5. Issue a key with only `records:read` → call
   `/v1/intents/execute` → 403 with `error_code: "invalid_scope"`,
   audit row.
6. Cross-tenant: issue a key for org A, ask for a record in org B's
   workspace → 403, audit row.
7. Hash-chain integrity: verify the new `agent_version` field is
   included in the hash payload so the chain catches tampering.

## Estimated effort

- Step 1 (migration): ½ day
- Step 2 (auth dep): ½ day
- Step 3 (intents router): 1 day
- Step 4 (records router): 1.5 days (BoxSummary contract, pagination
  cursor, filtering)
- Step 5 (audit/me/health): ½ day
- Step 6 (management UI): 2-3 days
- Step 7 (docs + quickstart): 1-2 days
- Step 8 (tests): 1-2 days
- Integration + first-customer dogfood: 2-3 days

Total: 3-4 weeks for a single engineer, faster with parallelisation
between backend and the management UI. Memory's 3-6 weeks estimate
holds.

## Recommended sequencing

The pieces ship in this order so each cut adds value alone:

1. **Migration** (step 1) — unblocks everything else.
2. **Auth + `/v1/intents/execute`** (steps 2 + 3) — minimum cut. Any
   customer with curl can call it after we hand them a key.
3. **`/v1/records`** (step 4) — agents need to discover their work
   before executing on it.
4. **Management UI** (step 6) — customers self-serve keys instead of
   us issuing them by hand.
5. **`/v1/audit`, `/v1/me`** (step 5) — completeness.
6. **Docs** (step 7) — once the API is stable, write the surface
   contract publicly.
7. **Tests + first-customer dogfood** (step 8) — throughout, not at
   the end.
