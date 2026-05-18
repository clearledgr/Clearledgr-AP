# /v1 recipes

Patterns that come up in nearly every agent integration.

## Idempotency

`/v1/intents/execute` accepts `Idempotency-Key` (header preferred, body
field fallback). The contract:

| Scenario                                           | Behaviour                                  |
|----------------------------------------------------|--------------------------------------------|
| First call with key `K`, payload `P`               | Executes, caches response                  |
| Same `K`, same `P` (any retry within 24h)          | Replays cached response, sets `Solden-Idempotent-Replay: true` |
| Same `K`, different `P`                            | 409 `idempotency_conflict` — use a fresh key |
| Different `K`, any `P`                             | Fresh execution                            |
| `K` older than 24h                                 | Cache row expired, executes fresh          |

Key recommendations:

* **One key per logical operation**, not per retry. Generate it
  before the first attempt and reuse on every retry.
* **UUIDv4 is fine.** Anything ≥ 16 bytes of entropy is.
* **Don't reuse keys across operations.** A retried "approve" call
  reuses its own key; the next operation gets a new one.

Python pattern:

```python
import uuid

class IntentRunner:
    def __init__(self, client):
        self.client = client

    def approve(self, ap_item_id: str):
        idem = f"approve-{ap_item_id}-{uuid.uuid4()}"
        return self.client.post(
            "/v1/intents/execute",
            headers={"Idempotency-Key": idem},
            json={"intent": "approve_invoice",
                  "input": {"ap_item_id": ap_item_id}},
        )
```

If the call fails with a network error, retry with the **same** `idem`
— a cached response from the first attempt (which may have succeeded
server-side before the network glitched) replays without
double-executing.

## Rate limits

Two sliding 60-second windows, both enforced after auth + scope pass:

* **Per-key**: 100 req/min. Trips first — narrow fence.
* **Per-org**: 1000 req/min. Aggregate cap when an org has many keys.

A breach returns 429 with `Retry-After` (seconds) and a typed body:

```json
{
  "error_code": "rate_limit_exceeded",
  "message": "per_key rate limit exceeded (100/60s). Retry after 17s.",
  "scope": "per_key",
  "limit": 100,
  "window_seconds": 60,
  "retry_after_seconds": 17,
  "request_id": "req_…"
}
```

Recommended handling:

```python
import time

def with_retry(fn, *, max_attempts=3):
    for attempt in range(max_attempts):
        try:
            return fn()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429:
                raise
            wait = int(e.response.headers.get("Retry-After", "5"))
            time.sleep(wait)
    raise RuntimeError("rate limit kept tripping")
```

Every breach writes one `rate_limit_exceeded` row to the audit chain
(`actor_type=agent`, payload carries `scope`, `limit`,
`request_path`, `retry_after_seconds`). So "why did my agent stop at
14:03 UTC?" remains answerable from the audit endpoint.

## Error codes

Every error response has the shape:

```json
{
  "error_code": "<machine-readable>",
  "message": "<human-readable>",
  "request_id": "req_…"   // when correlation is available
}
```

| HTTP | error_code               | When                                                                     |
|------|--------------------------|--------------------------------------------------------------------------|
| 400  | `invalid_request`        | Input validation failed (missing field, wrong type)                      |
| 400  | `invalid_url`            | Webhook URL was not `https://`                                            |
| 400  | `invalid_event_type`     | Webhook event_types contained an unknown name (the name is quoted)       |
| 400  | `unsupported_box_type`   | `/v1/records` called with a `box_type` not yet exposed publicly          |
| 400  | `empty_update`           | PATCH with no fields                                                     |
| 401  | `missing_api_key`        | No `Authorization` or `X-API-Key` header                                  |
| 401  | `invalid_api_key`        | Key doesn't match any active row                                          |
| 403  | `api_key_revoked`        | Key was revoked                                                          |
| 403  | `api_key_expired`        | Past `expires_at`                                                        |
| 403  | `invalid_scope`          | Key lacks the scope this endpoint requires                                |
| 404  | `not_found`              | Resource missing — or wrong tenant (indistinguishable by design)         |
| 409  | `state_conflict`         | Box state machine rejects the transition                                  |
| 409  | `idempotency_conflict`   | Same `Idempotency-Key`, different payload                                 |
| 429  | `rate_limit_exceeded`    | See "Rate limits" above                                                  |
| 500  | `internal_error`         | Server-side. `request_id` is logged with full detail                      |

## Scope grammar

Scopes follow the noun:verb pattern. Six scopes cover the entire /v1
surface today:

| Scope             | Grants                                                                  |
|-------------------|-------------------------------------------------------------------------|
| `records:read`    | `GET /v1/records`, `GET /v1/records/{id}`                              |
| `records:write`   | Reserved — no record-mutating endpoints yet                            |
| `intents:preview` | `POST /v1/intents/preview`                                              |
| `intents:execute` | `POST /v1/intents/execute`                                              |
| `audit:read`      | `GET /v1/audit`                                                         |
| `webhooks:manage` | All `/v1/webhooks/*`                                                    |

Legacy verb:noun scopes (`read:ap_items`, `write:ap_items`,
`read:audit`, `manage:webhooks`) still work — a synonym map covers
both during the deprecation window. New keys should use the noun:verb
form for forward-compat.

## Pagination

`/v1/records` returns an opaque `next_cursor` when more results
exist:

```python
records = []
cursor = None
while True:
    page = client.get(
        "/v1/records",
        params={"box_type": "ap_item", "limit": 200, "cursor": cursor},
    ).json()
    records.extend(page["records"])
    cursor = page["next_cursor"]
    if not cursor:
        break
```

Cursors are not durable forever — treat them as session-scoped. If a
cursor decodes to an offset past the current dataset, the page is
empty and `next_cursor` is `null`.

## Auditing your agent's footprint

Pull the audit chain for the org and filter by your agent:

```bash
curl "https://api.soldenai.com/v1/audit?limit=500" \
  -H "Authorization: Bearer $SOLDEN_KEY" \
  | jq '.events[] | select(.actor_id == "agent:cs-bot-prod")'
```

Every row carries `actor_type`, `actor_id`, `agent_version`,
`decision_reason`, `governance_verdict`, `agent_confidence`, and
`policy_version`. Useful for compliance reviews, regression analysis,
and "what changed when the agent shipped v2.5?" investigations.
