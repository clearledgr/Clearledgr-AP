# Customer-side agent connection — implementation plan

**Status:** in progress. Authored 2026-05-18. Steps 1-5 shipped to main;
decisions on the 7 open questions settled below.

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

Nine sub-pieces (expanded from five after Mo's call on the 7 open
questions: Box-type-agnostic vocab, idempotency keys, rate limits,
webhooks, and a Python SDK are all in v1):

1. **API key + service-account auth** for agent callers. **[shipped]**
2. **Agent identity in the audit chain**: extend `audit_events.actor`
   with `actor_type` ∈ {`human`, `service`, `agent`, `system`},
   `agent_id`, `agent_version`. Additive migration. Hash chain stays
   backward-compatible. **[shipped]**
3. **Public intent endpoint**: existing `/api/agent/intents/execute`
   re-exposed at `/v1/intents/execute` with API-key auth and a stable
   schema. **[shipped]**
4. **Generic Box read API**: new `/v1/records` (and
   `/v1/records/{id}`) returning the canonical record shape.
   Box-type-agnostic by construction so the second Box type ships
   without re-architecting the API. **[shipped]**
5. **Baseline `/v1` surface**: `/v1/health`, `/v1/me`, `/v1/audit`.
   **[shipped]**
6. **Box-type-agnostic scope vocab**: ship `records:read`,
   `records:write`, `intents:execute`, `intents:preview`,
   `audit:read`, `webhooks:manage` as canonical scopes. The auth dep
   accepts either the new vocab OR the old AP-pinned vocab
   (`read:ap_items` covers `records:read` for AP-keyed records)
   during a 6-month deprecation window.
7. **Idempotency keys** on `/v1/intents/execute`: client passes
   `Idempotency-Key: <uuid>` header (or body field). Server caches
   the response in a new `intent_responses` table for 24h. Same
   key + same payload returns the cached response; same key +
   different payload returns 409 conflict. Stripe pattern.
8. **Per-key rate limits**: 100 req/min per API key, 1000 req/min
   per organisation. Cheap counter middleware that runs after
   `require_agent_key`. Org-level cap protects shared capacity;
   per-key cap protects against a single runaway agent.
9. **Webhooks**: `/v1/webhooks` CRUD (subscribe, list, revoke),
   HMAC-SHA256 outbound signatures, retries with exponential
   backoff. Foundation already exists (`webhook_subscriptions` +
   `webhook_deliveries` tables); v1 is the public surface + docs.
10. **Developer surface**: API-keys management page in the workspace,
    reference docs, one quickstart, plus a thin Python SDK published
    to PyPI as `solden`.

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

Steps 1-5 are shipped to main. Status under each step.

### Step 1 — Migration: extend api_keys + audit_events **[SHIPPED — commit 30d8fa3a]**

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

### Step 2 — API key auth dependency for the public surface **[SHIPPED — commit 95c26cbd]**

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

### Step 3 — `/v1/intents` router **[SHIPPED — commit 95c26cbd]**

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

### Step 4 — `/v1/records` router **[SHIPPED — commit 2dc04e2b]**

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

### Step 5 — `/v1/audit`, `/v1/me`, `/v1/health` (minimum surface) **[SHIPPED — commit 2dc04e2b]**

**Files:** extend [clearledgr/api/v1.py](clearledgr/api/v1.py).

- `GET /v1/health` — no auth.
- `GET /v1/me` — echo back the caller's `{agent_id, agent_version,
  organization_id, scopes}`. Useful first call for any agent.
- `GET /v1/audit?box_id=&from=&to=` — read-only audit history for the
  caller's org. Scope `audit:read`.

### Step 6 — Box-type-agnostic scope vocab

**Files:** [clearledgr/api/api_keys.py](clearledgr/api/api_keys.py)
(extend `_SCOPE_CATALOG`),
[clearledgr/api/v1_auth.py](clearledgr/api/v1_auth.py) (accept-either
fallback),
[clearledgr/api/v1_intents.py](clearledgr/api/v1_intents.py) +
[clearledgr/api/v1_records.py](clearledgr/api/v1_records.py) +
[clearledgr/api/v1.py](clearledgr/api/v1.py) (switch the scope strings
each route asks for).

- Add `records:read`, `records:write`, `intents:execute`,
  `intents:preview`, `audit:read`, `webhooks:manage` to
  `_SCOPE_CATALOG`.
- Auth dep's `has_scope(target)` falls back to a synonym map:
  `records:read` ← `read:ap_items` (for AP-only keys), etc. So
  customers don't get locked out on rename day.
- /v1 routes ask for the new vocab. The old AP-pinned vocab still
  works through the synonym fallback for 6 months.

### Step 7 — Idempotency keys on `/v1/intents/execute` ✅ SHIPPED

**Files:** migration 87 added `intent_responses` table;
[clearledgr/api/v1_idempotency.py](clearledgr/api/v1_idempotency.py)
holds the helpers; `/v1/intents/execute` wires the four-call
sequence around `runtime.execute_intent`.

- New `intent_responses` table: `(organization_id, idempotency_key,
  payload_hash, response_json, http_status, ts, expires_at)` with
  composite PK `(organization_id, idempotency_key)`. TTL 24h via
  `expires_at`; expired rows are functionally absent at read time.
- `/v1/intents/execute` reads `Idempotency-Key` from header first,
  then body. Generates SHA-256 hash of canonical-JSON `(intent, input)`.
  If a row exists with that key:
  - same hash → return cached response, HTTP 200, header
    `Solden-Idempotent-Replay: true`.
  - different hash → 409 `{error_code: "idempotency_conflict"}`.
- Otherwise: execute the intent, `INSERT ... ON CONFLICT DO UPDATE`
  the response with hash + TTL, return.
- Cache lookup/write failures are swallowed — they never block the
  request. Worst case the intent runs twice, which the
  `audit_events.idempotency_key UNIQUE` constraint still catches at
  the substrate layer.

### Step 8 — Per-key rate limits ✅ SHIPPED

**Files:**
[clearledgr/api/v1_rate_limit.py](clearledgr/api/v1_rate_limit.py)
holds the counters + typed `RateLimitExceeded` exception;
`require_agent_key` in
[clearledgr/api/v1_auth.py](clearledgr/api/v1_auth.py) calls
`enforce_v1_rate_limit` after auth + scope pass; `main.py` has the
typed exception handler that emits the audit row and returns 429.

- Per-key counter: **100 req/min** sliding window
  (`V1_KEY_LIMIT_PER_MIN`, env-overridable). Trips first because
  it's the narrower bound — a single misbehaving agent fails at its
  own bucket before it can affect siblings under the same tenant.
- Per-org counter: **1000 req/min** sliding window
  (`V1_ORG_LIMIT_PER_MIN`). Broader fence — caps blast radius when
  an org has many keys distributed across teams.
- Backend: Redis when `REDIS_URL` is set (shared across workers),
  otherwise per-process in-memory (dev/test). Mirrors
  `solden.services.rate_limit`. Fails open on Redis errors
  matching the existing contract.
- Limit-breach response: HTTP 429 with `Retry-After` header + typed
  error envelope (`error_code: rate_limit_exceeded`, scope, limit,
  window_seconds, retry_after_seconds, request_id). One
  `rate_limit_exceeded` row written to `audit_events` so "why did
  my agent stop at 14:03 UTC?" stays answerable forever.
- Kill switch: `V1_RATE_LIMIT_ENABLED=false` lets everything
  through (for incident response when limits themselves are the
  problem).

### Step 9 — Webhooks (`/v1/webhooks/*`) ✅ SHIPPED

**Files:** [clearledgr/api/v1_webhooks.py](clearledgr/api/v1_webhooks.py)
holds the public CRUD. Reuses the battle-tested
[clearledgr/services/webhook_delivery.py](clearledgr/services/webhook_delivery.py)
(retry queue, HMAC computation) and
[clearledgr/core/stores/webhook_store.py](clearledgr/core/stores/webhook_store.py)
(persistence, tenant-pinned reads/writes — migrations 3 + 52
already shipped the `webhook_subscriptions` + `webhook_deliveries`
tables).

- `POST /v1/webhooks` — register `{url, event_types, description}`.
  Server generates the signing secret (`whsec_<43-char-token-urlsafe>`,
  Stripe shape) and returns it **once** in `secret`. Scope:
  `webhooks:manage`. URL must be `https://`; event names checked
  against an allowlist (`invoice.*`, `payment.*`,
  `billing.llm_budget_exceeded`, `webhook.test`, plus `*` wildcard).
- `GET /v1/webhooks` — list subscriptions for the caller's org;
  every read shows `secret_preview` (last 4 chars only), never the
  full secret.
- `GET /v1/webhooks/{id}` — read one. 404 on missing-or-wrong-tenant
  (the two cases are indistinguishable by design).
- `PATCH /v1/webhooks/{id}` — update url / event_types / description
  / is_active. Secret is not updateable here — use rotate-secret.
- `DELETE /v1/webhooks/{id}` — revoke. 204 on success.
- `POST /v1/webhooks/{id}/rotate-secret` — generate a fresh secret,
  invalidate the old one immediately, return the new one once.
- `POST /v1/webhooks/{id}/test` — fire a `webhook.test` event
  through the real delivery pipeline so the customer can verify
  their signature check before live events arrive.
- `GET /v1/webhooks/{id}/deliveries` — recent attempt log from
  `webhook_deliveries` (status, response code, latency).
- Outbound delivery sends both `X-Solden-Signature` (canonical) and
  `X-Solden-Signature` (legacy — for the deprecation window) on
  every webhook. Both carry the same `sha256=<hex>` value computed
  over the JSON body. Retries are inherited from the existing
  `webhook_delivery` pipeline.

### Step 10 — API keys management page in workspace ✅ SHIPPED

**Files:** new
[ui/web-app/src/routes/pages/ApiKeysPage.js](ui/web-app/src/routes/pages/ApiKeysPage.js)
+ [ApiKeysRoute.js](ui/web-app/src/routes/pages/ApiKeysRoute.js);
backend `/api/workspace/api-keys/*` was already in place — extended
in this commit to accept `agent_id`, `agent_version`, `expires_at`
on create + rotate, and to surface those fields plus `revoked_at`
in the list / get responses (migration 86 had shipped the columns
already; the store SELECT + INSERT statements just hadn't been
extended to read/write them).

- Page lives at `/api-keys`, registered in the sidebar under OPERATE.
- Lists existing keys with `key_prefix`, `agent_id`, `agent_version`
  badges, `scopes` chips, `created_at`, `last_used_at`,
  `expires_at` (with "expires soon" amber chip when <7d out),
  `revoked_at`. "Show revoked" toggle for the historical view.
- "Issue new key" modal: required `label`; optional `agent_id`,
  `agent_version`, `expires_at`; scope checkboxes grouped by new
  noun:verb vocab (default) vs legacy verb:noun (collapsed
  `<details>`). Submit disabled until label + at least one scope.
- Secret reveal: after create or rotate, a warning banner shows the
  raw key with a copy-to-clipboard button. Customer dismisses
  manually — the value isn't re-fetchable.
- Per-row Rotate / Revoke with confirmation prompts that warn about
  immediate breakage. Rotate carries `agent_id`/`agent_version`/
  `expires_at` across (swap secret, keep identity).
- Backend store changes: `auth_store.create_api_key` now persists
  the three new columns; `list_api_keys` + `get_api_key` SELECTs
  include them.

### Step 12 — Python SDK ✅ SHIPPED (publish to PyPI pending creds)

**Files:** new [sdk/python/](sdk/python/) package, name `solden`.

- Sync (`Solden`) and async (`AsyncSolden`) clients with identical
  resource surface: `me`, `records`, `intents`, `webhooks`, `audit`.
- Single dependency: `httpx>=0.25,<1.0`. No pydantic, no other deps —
  stays under 30KB installed.
- Auto-generated UUID4 `Idempotency-Key` on `intents.execute` (caller
  can override with an explicit key for retry-aware callers).
- 429 auto-retry honouring `Retry-After` (default `max_retries=3`).
- Typed exception per error_code (`InvalidScope`, `StateConflict`,
  `IdempotencyConflict`, `RateLimitExceeded`, …) all inheriting from
  `SoldenError`.
- `verify_signature(body, header, secret=...)` helper for inbound
  webhook verification (constant-time `hmac.compare_digest`).
- `iter_records()` walks `next_cursor` automatically.
- Env-var auth via `SOLDEN_API_KEY`.
- 23 unit tests using `httpx.MockTransport` (no network, no backend).

**Publish step (left for human):** `cd sdk/python && python -m build
&& twine upload dist/*` once PyPI account is set up under the
`solden` package name.

### Step 11 — Developer docs ✅ SHIPPED

**Files:** new [docs/v1/](docs/v1/) section. Five focused docs that
cover what customers actually read (mental model, walkthrough,
reference, webhook verification, recipes) — not the bloated
per-endpoint-file shape that nobody navigates:

- [README.md](docs/v1/README.md) — mental model + endpoint index +
  concepts (idempotency, rate limits, HMAC signatures, scope grammar).
- [quickstart.md](docs/v1/quickstart.md) — 0-to-approving-invoices in
  under 5 minutes. Bash walk-through (issue key → confirm /me → list
  records → preview → execute with `Idempotency-Key` → subscribe to
  events) plus a Python end-to-end script.
- [api-reference.md](docs/v1/api-reference.md) — every endpoint, every
  query parameter, every response field, plus the full event-name
  taxonomy.
- [webhooks.md](docs/v1/webhooks.md) — signature verification snippets
  in Python / Node / Ruby, idempotency-on-the-receiver pattern, retry
  semantics, secret rotation playbook, checklist.
- [recipes.md](docs/v1/recipes.md) — idempotency patterns,
  rate-limit-aware retry loop, full error-code catalogue, scope
  grammar table, pagination, auditing-your-agent recipe.

- Reference for each endpoint (request/response shape, errors).
- Authentication walkthrough.
- Quickstart: bash + Python snippets to (a) auth, (b) list records,
  (c) execute one intent, (d) subscribe to a webhook, (e) read the
  audit row.
- Error code reference.

### Step 12 — Python SDK (published to PyPI as `solden`)

**Files:** new top-level [`sdk/python/`](sdk/python/) directory with
its own `pyproject.toml`, separate package release.

- `Solden(api_key)` client. Namespaced methods: `client.records.list(...)`,
  `client.records.get(...)`, `client.intents.execute(...)`,
  `client.intents.preview(...)`, `client.webhooks.subscribe(...)`,
  `client.webhooks.list(...)`, `client.webhooks.revoke(...)`,
  `client.audit.list(...)`, `client.me()`.
- Auto-retries with idempotency: every `intents.execute` call without
  an explicit `idempotency_key` gets one generated automatically;
  network retries reuse it.
- Typed error envelopes: `solden.errors.{InvalidScope, NotFound,
  StateConflict, RateLimited, ...}`.
- Test harness against a mock server.
- Publish to PyPI under `solden`.

### Step 13 — Integration tests ✅ SHIPPED (dogfood is operational follow-up)

**Files:** new
[tests/test_v1_integration.py](tests/test_v1_integration.py) —
FastAPI TestClient walking the full router stack against the
session-scoped Postgres harness (auto-spawned testcontainer in CI).

Two test classes:

* `TestV1AgentFlow` — the canonical 5-step quickstart:
  - `/v1/me` echoes the resolved identity + scopes
  - `/v1/records` returns the seeded `ap_item`
  - sensitive columns (`bank_details_encrypted`, `last_error`,
    Slack/Teams refs) are filtered server-side
  - `/v1/intents/execute` writes an audit row with
    `actor_type="agent"`, `actor_id="agent:cs-bot-prod"`,
    `agent_version="2.4.1"` — the whole point of Step 1
  - same `Idempotency-Key` + same payload replays with
    `Solden-Idempotent-Replay: true`
  - same key + different payload returns 409
  - `/v1/webhooks` full CRUD round-trip including secret rotation
* `TestV1ErrorEnvelopes` — every typed error code:
  401 (missing/invalid key), 403 (revoked, scope missing),
  400 (unsupported box type, http URL, unknown event), 404
  (cross-tenant read).

Together with the 98 /v1 unit tests already shipping, the public API
has end-to-end coverage from "agent issues a key" to "audit chain
shows the agent's footprint."

**Dogfood (operational, not code):** point an internal AP review bot
at a staging tenant via `/v1/intents/execute`. Tracked outside this
plan — when the bot is running for a real customer the agent
connection is GA.

## Decisions (settled 2026-05-18)

The seven open questions are answered. Two pulled new sub-pieces into
v1 (rate limits and the Python SDK); two confirmed existing direction
(Box-type-agnostic vocab, Stripe-pattern idempotency); two stayed open
(docs hosting, design partner).

1. **Scope vocabulary — Box-type-agnostic.** Ship
   `records:read`, `records:write`, `intents:execute`,
   `intents:preview`, `audit:read`, `webhooks:manage` as canonical
   scopes for the public /v1 surface. The existing
   `read:ap_items` / `write:ap_items` vocab keeps working at the
   internal-routes layer; the auth dep accepts either form on /v1
   during a 6-month deprecation window. Decision rationale: the
   runtime claim is workflow-type-agnostic; AP-pinned scope tokens
   force a deprecation cycle once the second Box type lands and
   leak AP-shape assumptions into the public contract.

2. **Rate limits — enforce in v1.** Per-key: 100 req/min.
   Per-organisation: 1000 req/min. Reasons: cost defense (runaway
   agent in a tight loop costs us $$ / CPU / DB), abuse (leaked
   keys), fairness (one tenant monopolises shared capacity). For
   the first design partner the risk is theoretical but the
   middleware is cheap to land, and shipping without it forces a
   second deprecation conversation later. Implementation: a tiny
   per-key counter that runs after `require_agent_key`; org-level
   cap reuses `RateLimitMiddleware`.

3. **Idempotency keys — yes, Stripe pattern.** Client passes
   `Idempotency-Key: <uuid>` header (or body field). Server caches
   `(key → response)` for 24h in a new `intent_responses` table.
   Same key + same payload returns the cached response; same key +
   different payload returns 409 conflict (a defensive guard
   against buggy clients reusing a key). The
   `audit_events.idempotency_key UNIQUE` constraint already covers
   the substrate side; the new table is just the response cache so
   the API hands back the original 200 instead of replaying.

4. **Webhooks — yes, in v1.** `/v1/webhooks` CRUD (subscribe with
   URL + event-type filter, list, revoke). Outbound calls signed
   with HMAC-SHA256, `Solden-Signature` header, retries with
   exponential backoff (5 attempts). The substrate is already
   there (`webhook_subscriptions` table + migration 52's
   `webhook_deliveries` per-attempt log); v1 is the public surface
   + docs. Without this, agents poll, and polling is expensive on
   both sides.

5. **Docs hosting — TBD.** Recommendation stands: in-repo
   `soldenai-landing/docs/` for v1, migrate to a dedicated host
   only if the surface grows past what a directory of static
   markdown can carry. Decision can wait until docs are drafted.

6. **First customer / design partner — TBD.** Engineering can ship
   the surface without naming a partner; partner names define
   target dates and DX feedback channels, not API shape.

7. **SDK — yes, Python first.** Thin wrapper around `requests`
   that exposes `Solden(api_key)` with namespaced methods
   (`client.records.list(...)`, `client.intents.execute(...)`,
   `client.webhooks.subscribe(...)`). Auto-retries with the
   idempotency key. Typed error envelopes. Published to PyPI as
   `solden`. TypeScript is a fast follow.

## Out of scope

- Custom Box-type registration (todo #3, gated on this + second Box type).
- The second Box type itself (todo #2).
- A `capability` sub-Box primitive (todo #6, decision deferred).
- Multi-org keys (one key, one org, period).
- OAuth (API keys only for v1; OAuth for the human-side surfaces).
- A hosted "playground" UI.
- TypeScript SDK (fast follow after Python lands).

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

Shipped tonight:
- Step 1 (migration): ½ day ✓
- Step 2 (auth dep): ½ day ✓
- Step 3 (intents router): 1 day ✓
- Step 4 (records router): 1.5 days ✓
- Step 5 (audit/me/health): ½ day ✓

Remaining:
- Step 6 (Box-type-agnostic vocab + accept-either fallback): ½ day
- Step 7 (idempotency keys + `intent_responses` table): 1 day
- Step 8 (per-key rate limits + 429 audit): 1 day
- Step 9 (webhooks: /v1/webhooks CRUD + outbound HMAC + retries): 2-3 days
- Step 10 (management UI): 2-3 days
- Step 11 (docs + quickstart): 1-2 days
- Step 12 (Python SDK + PyPI publish): 1-2 days
- Step 13 (integration tests + first-customer dogfood): 1-2 days

Total remaining: 10-15 engineering days. Memory's 3-6 weeks estimate
still holds; the new sub-pieces (rate limits, webhooks, SDK) added
~4 days but landed cleanly within the original envelope.

## Recommended sequencing

The pieces ship in this order so each cut adds value alone:

1. **Migration** (Step 1) ✓ — unblocked everything else.
2. **Auth + `/v1/intents/execute`** (Steps 2 + 3) ✓ — minimum cut.
3. **`/v1/records`** (Step 4) ✓ — agents discover their work.
4. **`/v1/audit`, `/v1/me`** (Step 5) ✓ — completeness.
5. **Box-type-agnostic vocab** (Step 6) — rename day; cheap.
6. **Idempotency keys** (Step 7) — every agent retry stops being
   a gamble.
7. **Rate limits** (Step 8) — cost / abuse / fairness defense.
8. **Webhooks** (Step 9) — agents stop polling; push beats poll.
9. **Management UI** (Step 10) — customers self-serve keys.
10. **Docs** (Step 11) — once the API is stable, write the contract
    publicly.
11. **Python SDK** (Step 12) — Python first because the agent
    ecosystem lives there.
12. **Tests + first-customer dogfood** (Step 13) — throughout, not
    at the end.
