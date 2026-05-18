# Webhooks

Solden POSTs every event matching a subscription's `event_types` to
your registered URL. This doc covers the request shape, signature
verification, and retry behaviour.

## Request shape

```http
POST /your-webhook-url HTTP/1.1
Content-Type: application/json
X-Solden-Event: invoice.approved
X-Solden-Delivery: whd_8f2c…
X-Solden-Signature: sha256=4f5a…hex
X-Clearledgr-Event: invoice.approved
X-Clearledgr-Delivery: whd_8f2c…
X-Clearledgr-Signature: sha256=4f5a…hex
```

```json
{
  "event": "invoice.approved",
  "delivery_id": "whd_8f2c…",
  "timestamp": "2026-05-18T10:14:22Z",
  "data": {
    "ap_item_id": "ap_…",
    "vendor_name": "Acme",
    "amount": 1492.50,
    "currency": "EUR",
    "approved_by": "alice@acme.com",
    "approved_at": "2026-05-18T10:14:22Z"
  }
}
```

The `X-Clearledgr-*` headers are legacy aliases — both sets carry the
same values. New integrations should read `X-Solden-*` and ignore the
legacy ones.

## Verifying the signature

Always verify `X-Solden-Signature` before trusting the body.

### Python

```python
import hmac, hashlib

def verify(body_bytes: bytes, header: str, secret: str) -> bool:
    """Returns True iff the signature matches.

    body_bytes:  raw request body (bytes, NOT the parsed JSON)
    header:      value of X-Solden-Signature, e.g. "sha256=4f5a…"
    secret:      whsec_… from the create response
    """
    if not header or not header.startswith("sha256="):
        return False
    expected = header[len("sha256="):]
    computed = hmac.new(
        secret.encode("utf-8"), body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, expected)
```

Use the **raw** request body bytes. Re-serialising the parsed JSON
will reorder keys and break verification.

### Node.js

```js
const crypto = require("crypto");

function verify(body, header, secret) {
  if (!header || !header.startsWith("sha256=")) return false;
  const expected = header.slice("sha256=".length);
  const computed = crypto
    .createHmac("sha256", secret)
    .update(body)            // body is a Buffer of the raw bytes
    .digest("hex");
  return crypto.timingSafeEqual(
    Buffer.from(computed, "hex"),
    Buffer.from(expected, "hex"),
  );
}
```

### Ruby

```ruby
require "openssl"

def verify(body, header, secret)
  return false unless header&.start_with?("sha256=")
  expected = header.sub(/^sha256=/, "")
  computed = OpenSSL::HMAC.hexdigest("SHA256", secret, body)
  Rack::Utils.secure_compare(computed, expected)
end
```

## Idempotency on the receiver

Deliveries can repeat. Solden re-POSTs an event whenever:

* Your endpoint returned a non-2xx status.
* The connection timed out (>10s).
* The HTTP client raised an exception.

Use `X-Solden-Delivery` as the idempotency key on **your** side —
store delivery IDs you've processed and short-circuit duplicates.
The same `delivery_id` will appear across retries.

## Retry semantics

Failed deliveries are retried with the existing webhook retry
pipeline. Status flows through `webhook_deliveries`; you can query
the recent log via `GET /v1/webhooks/{id}/deliveries`.

After the retry budget is exhausted, the delivery is marked
permanently failed and Solden stops trying. If you bring your
endpoint back, fire a manual `POST /v1/webhooks/{id}/test` to
confirm signature verification before live events resume.

## Rotating the signing secret

1. `POST /v1/webhooks/{id}/rotate-secret` returns the new secret
   once. The old secret is invalidated immediately.
2. Solden does **not** double-sign during rotation. To rotate without
   downtime, deploy the new secret to your verifier first, then call
   rotate.

## Quick checklist

- [ ] Endpoint URL is HTTPS (HTTP rejected at registration).
- [ ] Signature verified on every request, against the **raw body**.
- [ ] `X-Solden-Delivery` deduped on the receiver.
- [ ] Endpoint returns 2xx quickly (<10s). Defer heavy work to a queue.
- [ ] Retry-aware: handle the same event arriving twice.
- [ ] Rotation playbook documented in your runbook.
