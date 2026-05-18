# /v1 API reference

Every endpoint, the scope it requires, and the request / response
shapes. All responses are JSON unless noted; all errors are typed
envelopes (`{error_code, message, request_id?}`).

Base URL: `https://api.soldenai.com`

Auth: `Authorization: Bearer <api-key>` **or** `X-API-Key: <api-key>`.

---

## Health

### `GET /v1/health`

Liveness probe. **No auth.**

```json
{"status": "ok", "service": "clearledgr-core"}
```

---

## Identity

### `GET /v1/me`

Echoes the resolved caller identity. **Auth only**, no scope required.

```json
{
  "key_id": "k_8f2c…",
  "organization_id": "org_x",
  "agent_id": "agent:cs-bot-prod",
  "agent_version": "2.4.1",
  "scopes": ["records:read", "intents:execute"],
  "actor_label": "agent:cs-bot-prod"
}
```

`scopes` is `null` for legacy keys minted before the scope system —
those keys carry full access.

---

## Records

### `GET /v1/records`

List records. Scope: `records:read`.

Query parameters:

| Name        | Required | Description                              |
|-------------|----------|------------------------------------------|
| `box_type`  | yes      | `ap_item` (only supported type today)    |
| `state`     | no       | Filter to records currently in this state |
| `cursor`    | no       | Opaque pagination cursor                 |
| `limit`     | no       | Page size, 1–200, default 50             |

Response:

```json
{
  "records": [
    {
      "id": "ap_abc",
      "box_type": "ap_item",
      "state": "needs_approval",
      "organization_id": "org_x",
      "created_at": "…",
      "updated_at": "…",
      "data": {
        "vendor_name": "Acme",
        "amount": 1492.50,
        "currency": "EUR",
        "invoice_number": "INV-…",
        "invoice_date": "2026-05-01",
        "due_date": "2026-06-01",
        "po_number": "PO-…",
        "approval_required": true,
        "approval_surface": "slack",
        "match_status": "matched",
        "exception_code": null,
        "owner_id": "u_…",
        "confidence": 0.97
      }
    }
  ],
  "next_cursor": "<opaque>" or null
}
```

Sensitive fields (bank details, Slack/Teams refs, raw error strings)
are filtered server-side and never leave the API.

### `GET /v1/records/{box_id}`

Read one record. Scope: `records:read`. `box_type` query param is
required (every box type owns its own id namespace; the API stays
explicit). 404 on missing **or** cross-tenant — the two cases are
indistinguishable by design.

---

## Intents

### `POST /v1/intents/execute`

Commit an intent. Scope: `intents:execute`.

```json
POST /v1/intents/execute
Authorization: Bearer sk_live_…
Idempotency-Key: 8f2c-…   (optional but recommended)
Content-Type: application/json

{
  "intent": "approve_invoice",
  "input": {"ap_item_id": "ap_abc"},
  "idempotency_key": "8f2c-…"      // optional fallback if header absent
}
```

Response on success:

```json
{
  "ok": true,
  "result": {<intent-specific payload>}
}
```

Headers on idempotent replay: `Solden-Idempotent-Replay: true`.

Errors:

| HTTP | error_code             | Meaning                                              |
|------|------------------------|------------------------------------------------------|
| 400  | `invalid_request`      | Input failed validation                              |
| 403  | `invalid_scope`        | Key lacks `intents:execute`                          |
| 404  | `not_found`            | Intent name unknown or target record not found       |
| 409  | `state_conflict`       | Record not in a state that allows this transition    |
| 409  | `idempotency_conflict` | Same `Idempotency-Key`, different payload — fix it   |
| 429  | `rate_limit_exceeded`  | See `Retry-After`                                    |
| 500  | `internal_error`       | Server-side. `request_id` carries the trace          |

### `POST /v1/intents/preview`

Same shape as `execute`, no side effects. Scope: `intents:preview`.

```json
{
  "ok": true,
  "preview": {<what execute would do>}
}
```

### `GET /v1/intents`

List intents the key is authorised to run. **Auth only.**

```json
{
  "agent_id": "agent:cs-bot-prod",
  "agent_version": "2.4.1",
  "organization_id": "org_x",
  "intents": ["approve_invoice", "reject_invoice", "request_more_info", …]
}
```

---

## Audit

### `GET /v1/audit`

Read the org's audit chain. Scope: `audit:read`. Always pinned to the
key's org — no parameter can widen the result beyond that boundary.

Query parameters:

| Name        | Required | Description                              |
|-------------|----------|------------------------------------------|
| `box_id`    | no       | Filter to events about a specific Box id |
| `box_type`  | no       | Filter to events about a Box type        |
| `event_type`| no       | Filter to a specific event_type          |
| `limit`     | no       | 1–500, default 100                       |

Response:

```json
{
  "events": [
    {
      "id": "ae_…",
      "box_id": "ap_…",
      "box_type": "ap_item",
      "event_type": "state_transition",
      "prev_state": "needs_approval",
      "new_state": "approved",
      "actor_type": "agent",
      "actor_id": "agent:cs-bot-prod",
      "agent_version": "2.4.1",
      "source": "intents.execute",
      "organization_id": "org_x",
      "decision_reason": "auto_approved",
      "governance_verdict": "permitted",
      "agent_confidence": 0.94,
      "ts": "2026-05-18T10:14:22Z",
      "policy_version": "p17",
      "payload_json": {…}
    }
  ],
  "count": 1
}
```

---

## Webhooks

All endpoints scope: `webhooks:manage`.

### `POST /v1/webhooks`

Register a subscription. Server generates the signing secret.

```json
{
  "url": "https://your.app/solden-webhooks",
  "event_types": ["invoice.approved", "invoice.posted_to_erp"],
  "description": "Approval relay"
}
```

Response (201):

```json
{
  "id": "wh_…",
  "url": "https://your.app/solden-webhooks",
  "event_types": ["invoice.approved", "invoice.posted_to_erp"],
  "description": "Approval relay",
  "is_active": true,
  "secret": "whsec_…",          // shown ONCE — capture now
  "secret_preview": "whsec_***1234",
  "created_at": "…",
  "updated_at": "…"
}
```

URL must be `https://`. Event names are checked against an allowlist;
typos return 400 `invalid_event_type` with the bad name quoted.

### `GET /v1/webhooks`

List subscriptions. Add `?active_only=true` to filter to live ones.

### `GET /v1/webhooks/{id}`

Single subscription. 404 on missing-or-wrong-tenant.

### `PATCH /v1/webhooks/{id}`

Update `url` / `event_types` / `description` / `is_active`. Secret is
not editable here — use `rotate-secret`.

### `DELETE /v1/webhooks/{id}`

Revoke. 204 on success.

### `POST /v1/webhooks/{id}/rotate-secret`

Generate a new secret, invalidate the old immediately. Response shape
matches create — `secret` revealed once, `secret_preview` thereafter.

### `POST /v1/webhooks/{id}/test`

Fire a `webhook.test` event through the real delivery pipeline.

```json
{"delivered": true, "url": "https://…", "event": "webhook.test"}
```

### `GET /v1/webhooks/{id}/deliveries`

Recent delivery attempts. `?limit=` 1–200, default 50.

```json
{
  "deliveries": [
    {
      "id": "whd_…",
      "event_type": "invoice.approved",
      "status": "succeeded",
      "response_status": 200,
      "response_body_truncated": "{\"ok\":true}",
      "latency_ms": 142,
      "attempted_at": "…",
      "attempt_number": 1
    }
  ],
  "count": 1
}
```

---

## Event taxonomy

Names you can subscribe to via `event_types`. Use `*` to subscribe to
every event.

| Event                          | Fires when                                              |
|--------------------------------|---------------------------------------------------------|
| `invoice.received`             | New AP item enters the runtime                          |
| `invoice.validated`            | Validation gate passed                                  |
| `invoice.needs_approval`       | Approval surface engaged                                |
| `invoice.approved`             | Approver gave a "go" verdict                            |
| `invoice.rejected`             | Approver rejected                                       |
| `invoice.ready_to_post`        | All gates passed; queued for ERP                        |
| `invoice.posted_to_erp`        | ERP confirmed the bill is on the ledger                 |
| `invoice.closed`               | Lifecycle complete (paid + reconciled)                  |
| `invoice.needs_info`           | More information required to proceed                    |
| `invoice.failed_post`          | ERP posting failed                                      |
| `payment.completed`            | Outbound payment settled                                |
| `payment.failed`               | Payment rail rejected                                   |
| `payment.reversed`             | Payment clawed back                                     |
| `billing.llm_budget_exceeded`  | Workspace crossed its monthly LLM cost cap              |
| `webhook.test`                 | Test event from `POST /v1/webhooks/{id}/test`           |
