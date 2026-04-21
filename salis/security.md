# Security

Threat model. Tenant isolation. Secrets. What we've got, what we don't, what needs to come next.

*Last verified: 2026-04-21 against commit `94c98eb`.*

---

## Threat model (one page)

Five things we care about:

| Threat | Worst case | What stops it |
|---|---|---|
| **Cross-tenant data leak** | Org A reads / writes Org B's invoices | `organization_id` on every row + `verify_org_access` + `soft_org_guard` in every route + `test_tenant_isolation.py` fence |
| **Unauthorized ERP post** | Attacker gets bill posted to a customer's QBO | JWT auth + admin role for approvals + HMAC-verified Slack inbound + OAuth token scope limits |
| **Invoice fraud (bank swap)** | Vendor changes their bank details the day before a payment | Bank-change detection in `ap_decision.py` (escalate if changed within 30 days) + `check_iban_change` action + vendor portal input validation + auditable change history |
| **LLM abuse / token exfil** | Attacker injects prompt that makes Claude exfiltrate data | Prompt guard (`prompt_guard.py`) clips untrusted input + tool-use schemas are closed-set (no free-form JSON) + LLM output never used to pick routing decisions (rules decide) |
| **Webhook spoofing** | Attacker forges an ERP webhook to mark a bill paid | Per-ERP HMAC signature verification in `erp_webhook_verify.py` + fail-closed on unconfigured secrets (503) + signed-payload-only POST endpoints |

---

## Tenant isolation

**Invariant:** every database row has `organization_id`. Every route checks the authenticated user's `organization_id` matches the target row's.

**How it's enforced:**

1. `TokenData` dataclass ([`clearledgr/core/auth.py`](../clearledgr/core/auth.py)) carries `organization_id` on every JWT.
2. `verify_org_access(row_org_id, user)` raises 403 if mismatch.
3. Store methods take `organization_id` as a required argument. They WHERE-clause every query.
4. FastAPI dependencies `soft_org_guard` (read) + `hard_org_guard` (write) apply at the route level.

**Drift fence:** [`tests/test_tenant_isolation.py`](../tests/test_tenant_isolation.py) seeds two orgs with overlapping data shapes and asserts no endpoint leaks one org's data to the other.

**Cross-tenant data-isolation regression fence:** [`tests/test_cross_tenant_isolation.py`](../tests/test_cross_tenant_isolation.py) (commit `087dcf3`) exhaustively walks the API surface and asserts the isolation invariant across every mutation path.

**Where this can go wrong:**

- Any new API route that doesn't take `organization_id` or use the guard. Review for `Depends(get_current_user)` + some form of `verify_org_access`.
- Any new store method that accepts an ID without the org scope. Review for `WHERE id = ? AND organization_id = ?` pattern.
- Direct SQL from outside the store mixins. This is why we funnel everything through `ClearledgrDB` — the guard lives there.

---

## Secrets

**Stored at rest:**

| Secret | Storage | Encrypted? |
|---|---|---|
| User passwords | `users.password_hash` | bcrypt |
| Gmail OAuth tokens | `gmail_tokens` | Fernet (TOKEN_ENCRYPTION_KEY) |
| ERP OAuth tokens | `integration_store.credentials` | Fernet |
| Slack bot tokens | `slack_installs` | plaintext (TODO: encrypt) |
| Webhook secrets | `webhook_subscriptions.secret` | plaintext (customer-chosen; we're storing what they gave us) |
| Vendor bank details | `bank_details` | Fernet |

**Stored in env (Railway):**

- `CLEARLEDGR_SECRET_KEY` — JWT signing + CSRF
- `TOKEN_ENCRYPTION_KEY` — Fernet key for at-rest encryption of the items above
- `ANTHROPIC_API_KEY` — Claude access
- `DATABASE_URL`, `REDIS_URL` — connection strings
- Per-ERP OAuth client IDs + secrets

**Rotation story:** see [`operations.md`](./operations.md) → "Rotate secrets."

**Key derivation:** `TOKEN_ENCRYPTION_KEY` raw bytes → SHA-256 hash → base64 url-safe encode → Fernet-compatible key. Implementation in [`clearledgr/core/secrets.py`](../clearledgr/core/secrets.py). `require_secret()` crashes in prod if a required secret is missing; returns a random fallback in dev (with a prominent warning).

**Never-in-logs list:**

- OAuth tokens (we log the integration ID, not the token)
- User passwords (only hashes in DB; never in logs)
- ERP response bodies (we log the status + classified error, not the raw body — which might contain PII or creds)
- Full invoice contents (we log the invoice_number + vendor_name, not the attachment)

`core/errors.py:safe_error()` is the exception sanitizer — logs a full trace to the server logs + returns a ref ID to the client. Clients never see stack traces.

---

## Authentication

**User auth:** JWT via `core/auth.py`. Token issued on `POST /auth/login`, carries `user_id`, `email`, `organization_id`, `role`, `exp`. Signed with `CLEARLEDGR_SECRET_KEY`. Refresh tokens not implemented (users re-log after expiry).

**Role matrix:**

| Role | What it can do |
|---|---|
| `owner` | Everything, including admin config, integrations, billing |
| `admin` | Everything except billing + ownership transfer |
| `approver` | Approve/reject invoices in Slack + admin console |
| `viewer` | Read-only access |

Role gate is applied in routes: `_require_admin(user)` raises 403 if role isn't in `{"admin", "owner"}`.

**Service-to-service auth:**

- Gmail Pub/Sub → `POST /gmail/push`: OIDC JWT verification.
- Slack interactive actions → `POST /slack/events`: HMAC-SHA256 signature verification.
- ERP webhooks → `/erp/webhooks/{erp}/{org_id}`: per-ERP HMAC verification in `erp_webhook_verify.py`. Fail-closed with 503 if secret not configured.
- Vendor portal magic links → `POST /portal/onboard/{token}/submit`: single-use token check.

**Drift fences:**

- [`test_erp_webhook_security.py`](../tests/test_erp_webhook_security.py) — every ERP webhook rejects unsigned + wrong-signature requests.
- [`test_api_auth.py`](../tests/test_api_auth.py) — every route's auth requirement.

---

## Prompt injection defense

The five LLM actions (§7.1) all accept untrusted input (email body, vendor reply text, invoice PDF text). We apply layered defense:

1. **Input clipping:** [`core/prompt_guard.py`](../clearledgr/core/prompt_guard.py) limits subject + vendor name + body lengths. Truncates aggressively.
2. **Structured output only:** tool-use schemas are closed-set enums. Claude can't return a free-form "I will ignore all previous instructions and..." string in a routing role — there's no routing role.
3. **No routing from LLM output:** the `ap_decision.py` rewrite (Phase 4) made this explicit. Claude writes prose. Rules decide.
4. **Cost cap:** `llm_gateway.py` rate-limits per-org so a prompt-injection that tries to drive up the API bill gets stopped.

**What we haven't built yet:** model-in-the-loop output filtering ("scan Claude's output for malicious content"). The architectural decision (Phase 4) was to remove Claude from the routing path entirely, which eliminates most of the attack surface — there's no "Claude says approve, we post to ERP" path left to exploit.

---

## Input validation boundaries

**Vendor portal** (most exposed, because unauthenticated):

- `core/portal_input.py` — every field has an explicit type, max length, format allowlist.
- Integer fields use `int()` with exception handling — no silent coercion.
- HTML/script tags stripped from free-text fields.
- Files: MIME type allowlist, size cap (per ADR 010).

**API ingress** (authenticated):

- Pydantic models on every POST body. FastAPI validates before the handler runs.
- `core/portal_auth.py` — magic-link token format + expiry.

**Webhook ingress** (signature-verified):

- Signature check before parsing the body.
- After signature passes, Pydantic model validation.

**Database layer** (defense in depth):

- `_AP_ITEM_ALLOWED_COLUMNS` frozenset in `ap_store.py` — SQL injection prevention at the store layer. Even if a caller tried to pass an arbitrary column, the whitelist rejects it.
- Store methods use parameterized queries exclusively. No string concat for SQL.

---

## Audit trail (SOC 2 posture)

The deck's promise: every workflow instance becomes a persistent, attributable record. That's our SOC 2 story by default — we don't have to retrofit audit for compliance, it's already in the product.

**What's audited:**

| Event | Row ends up in |
|---|---|
| State transition | `audit_events` via `update_ap_item` (atomic UPDATE + INSERT) |
| Exception raise / resolve | `box_exceptions` + `audit_events` narration |
| Outcome recorded | `box_outcomes` + `audit_events` narration |
| LLM call | `llm_call_log` |
| ERP call | `audit_events` via Rule 1 + any retry narration |
| User login | `audit_events` event_type `user_login` |
| Admin config change | `audit_events` event_type `erp_admin_action:*`, `settings_change`, etc. |

**What's NOT in the audit trail:** routine reads (we don't log every GET), and things that failed BEFORE Rule 1's pre-write (by design — if the audit failed, we also didn't execute, so there's nothing to audit).

**Retention:** today, unlimited retention. We haven't hit a storage cost that would force a retention policy. When we do, the plan is:

- `audit_events` + `box_exceptions` + `box_outcomes` → keep indefinitely (required for compliance).
- `llm_call_log` → 90 days (cost + logs of LLM I/O which may contain customer data).

---

## SOC 2 gaps we know about

We're pre-SOC 2. Paying customers may ask. Current honest answer:

1. **We have the architectural pieces** — audit trail, tenant isolation, signed integrations, encrypted at rest.
2. **We don't have the operational evidence** — incident response runbooks beyond this file, employee access reviews, vendor risk management, formal change management. That's post-angel-check territory.
3. **We don't have a SOC 2 Type I or Type II report.** Engaging an auditor is a Q2 2026 conversation if we have paying customers by then.

The thesis: security practices that are *in the product* (audit, isolation, encryption) are the ones that scale. Security practices that are *in the org* (reviews, processes, policies) come with the second hire. Don't rush either out of order.

---

## If you find a security bug

1. Don't post in public channels.
2. DM Mo + Suleiman.
3. If customer data is at risk, stop the bleeding (flag, revert, whatever's fastest). Worry about the write-up after.
4. Fix on a private branch. Security fixes don't need a public PR thread until they're landed.
5. Post-fix: write a private post-mortem in a doc, share with the team.

If you find a vulnerability in a dependency we use, file with the vendor first. Add a mitigation here while waiting for the upstream fix.
