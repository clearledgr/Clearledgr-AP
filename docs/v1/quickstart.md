# /v1 Quickstart

Get from "I have an API key" to "my agent is approving invoices" in
about five minutes.

## 0. Issue a key

In the workspace, go to **API keys** (sidebar → OPERATE → API keys),
click **Issue new key**. Capture three things:

1. The `agent_id` you want the audit chain to remember (e.g.
   `agent:cs-bot-prod`).
2. The scopes the agent needs. For the walk-through below pick:
   - `records:read`
   - `intents:preview`
   - `intents:execute`
3. The raw key — it starts with `sk_` and is shown **once**. Store it
   in your secret manager now.

## 1. Confirm the key works

```bash
export SOLDEN_KEY="sk_live_…"

curl https://api.soldenai.com/v1/me \
  -H "Authorization: Bearer $SOLDEN_KEY"
```

You should see your `key_id`, `organization_id`, `agent_id`, and the
scope set you assigned. If you get a 401, the key is wrong; 403 means
the key is right but missing scope (`/v1/me` needs auth only, so 403
here means revoked or expired).

## 2. List records the agent can see

```bash
curl "https://api.soldenai.com/v1/records?box_type=ap_item&limit=5" \
  -H "Authorization: Bearer $SOLDEN_KEY"
```

Response:

```json
{
  "records": [
    {
      "id": "ap_abc123",
      "box_type": "ap_item",
      "state": "needs_approval",
      "organization_id": "org_x",
      "created_at": "2026-05-18T10:14:22Z",
      "updated_at": "2026-05-18T10:14:22Z",
      "data": {
        "vendor_name": "Acme Corp",
        "amount": 1492.50,
        "currency": "EUR",
        "invoice_number": "INV-2026-0042",
        "due_date": "2026-06-01",
        "approval_required": true
      }
    }
  ],
  "next_cursor": null
}
```

## 3. Preview an intent before committing

Find an `ap_item` in state `needs_approval` from the list. Then:

```bash
curl https://api.soldenai.com/v1/intents/preview \
  -H "Authorization: Bearer $SOLDEN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "intent": "approve_invoice",
    "input": {"ap_item_id": "ap_abc123"}
  }'
```

The preview tells you what `execute` would do — the state transition,
any audit rows that would land, any guards that would fire. No side
effects. Use this to build agent plans you can confirm with a human
before committing.

## 4. Execute the intent

Use an **idempotency key** so a retry doesn't double-execute:

```bash
curl https://api.soldenai.com/v1/intents/execute \
  -H "Authorization: Bearer $SOLDEN_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{
    "intent": "approve_invoice",
    "input": {"ap_item_id": "ap_abc123"}
  }'
```

If the agent retries the same call (same key, same body), Solden
replays the cached response and the `Solden-Idempotent-Replay: true`
header is set. If you reuse the key with a different body, you get
409 `idempotency_conflict` — that's a client bug, not a server
problem.

## 5. Subscribe to events

```bash
curl https://api.soldenai.com/v1/webhooks \
  -H "Authorization: Bearer $SOLDEN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your.app/solden-webhooks",
    "event_types": ["invoice.approved", "invoice.posted_to_erp"],
    "description": "Approval relay"
  }'
```

Capture the `secret` from the response — it is shown **once**. Verify
signatures on inbound deliveries using [webhooks.md](webhooks.md).

## Python end-to-end

```python
import os
import uuid

import httpx

KEY = os.environ["SOLDEN_KEY"]
BASE = "https://api.soldenai.com"
HEADERS = {"Authorization": f"Bearer {KEY}"}


def list_pending(limit: int = 25):
    r = httpx.get(
        f"{BASE}/v1/records",
        params={"box_type": "ap_item", "state": "needs_approval", "limit": limit},
        headers=HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["records"]


def approve(ap_item_id: str):
    r = httpx.post(
        f"{BASE}/v1/intents/execute",
        headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
        json={
            "intent": "approve_invoice",
            "input": {"ap_item_id": ap_item_id},
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    for item in list_pending():
        if item["data"]["amount"] < 1000:  # the agent's policy
            print(approve(item["id"]))
```

## Next steps

* [webhooks.md](webhooks.md) — verify signatures, plan retry semantics.
* [recipes.md](recipes.md) — idempotency, rate-limit handling, error
  taxonomy.
* [api-reference.md](api-reference.md) — every endpoint, full request /
  response shape.
