# Solden /v1 API

The public API your agents call to interact with the Box.

## Mental model

Solden is a workflow runtime. Each workflow type (today: `ap_item`) lives
as a **Box** — a persistent state machine with its own gates, audit
chain, and render targets (Gmail, Slack, Teams, NetSuite, SAP).

Your agent has three ways to interact with a Box:

1. **Read records** — `GET /v1/records?box_type=ap_item` to enumerate,
   `GET /v1/records/{id}` to fetch one. Only the public field set
   surfaces (no bank details, no Slack thread refs, no metadata blobs).
2. **Dispatch intents** — `POST /v1/intents/execute` to make something
   happen ("approve_invoice", "request_more_info"). Intents are typed,
   scope-gated, and idempotent.
3. **Subscribe to events** — register a webhook with
   `POST /v1/webhooks` and Solden POSTs every matching state change to
   your URL with an HMAC-SHA256 signature.

Every action your agent takes is attributed in the audit chain by
`agent_id` and `agent_version` — both pinned to the API key that
authenticated the call.

## Quickstart

See [quickstart.md](quickstart.md) — bash + Python in under 5 minutes.

## Endpoints

Full reference at [api-reference.md](api-reference.md). Briefly:

| Endpoint                                    | Scope             | Purpose                            |
|--------------------------------------------|-------------------|------------------------------------|
| `GET  /v1/health`                          | (none)            | Liveness probe                     |
| `GET  /v1/me`                              | (auth only)       | Echo the resolved key identity     |
| `GET  /v1/records?box_type=…`              | `records:read`    | List records                       |
| `GET  /v1/records/{id}?box_type=…`         | `records:read`    | Read one record                    |
| `POST /v1/intents/preview`                 | `intents:preview` | Dry-run an intent                  |
| `POST /v1/intents/execute`                 | `intents:execute` | Commit an intent (idempotent)      |
| `GET  /v1/intents`                         | (auth only)       | List intents the caller can run    |
| `GET  /v1/audit`                           | `audit:read`      | Read the org's audit chain         |
| `*    /v1/webhooks/*`                      | `webhooks:manage` | CRUD + rotate + test + deliveries  |

## Concepts you'll meet

* **Idempotency keys** — every `/v1/intents/execute` call accepts an
  `Idempotency-Key`. Same key + same payload replays the cached
  response. Same key + different payload returns 409. TTL 24h.
  See [recipes.md](recipes.md#idempotency).
* **Rate limits** — 100 req/min per key + 1000 req/min per org.
  429 responses carry `Retry-After`. See
  [recipes.md](recipes.md#rate-limits).
* **HMAC signatures** — outbound webhooks carry
  `X-Solden-Signature: sha256=<hex>`. Verify before trusting the
  payload. See [webhooks.md](webhooks.md).
* **Scope grammar** — noun:verb (`records:read`, `intents:execute`).
  Older keys minted under the verb:noun vocab (`read:ap_items`,
  `write:ap_items`) still work — a synonym map covers both during
  the deprecation window.

## Errors

Every error response is a typed envelope:

```json
{
  "error_code": "invalid_scope",
  "message": "missing_scope:intents:execute",
  "request_id": "req_abc123"
}
```

`request_id` lets support correlate your call with the server-side
audit row. The full catalogue lives in
[recipes.md](recipes.md#error-codes).

## Authentication

```bash
curl https://api.soldenai.com/v1/me \
  -H "Authorization: Bearer sk_live_…"
```

Both `Authorization: Bearer <key>` and `X-API-Key: <key>` are accepted.
Issue keys from the workspace at
[/api-keys](https://app.soldenai.com/api-keys).
