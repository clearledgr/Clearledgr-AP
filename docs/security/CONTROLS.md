# Clearledgr Controls Map

Mapping of Trust Service Criteria (TSC) to implemented controls, with
citations into the source tree. Every claim below points at a concrete
file:line so prospects (or future auditors) can verify rather than take
our word for it.

Last reviewed: 2026-04-26.

---

## CC6.1 — Logical Access (Authentication)

**Implemented.**

- **Password hashing**: bcrypt via `passlib.CryptContext` —
  [clearledgr/core/auth.py:137-170](../../clearledgr/core/auth.py#L137-L170)
  (`hash_password` + `verify_password`).
- **Session cookies**: `HttpOnly=True`, `SameSite=lax`, `Secure` flag
  driven by environment —
  [clearledgr/api/auth.py:52-84](../../clearledgr/api/auth.py#L52-L84)
  (`_set_workspace_session_cookies`).
- **OAuth (Google)**: signed-state CSRF, post-callback session issuance
  in same handler — `clearledgr/api/auth.py` (Google OAuth section).
- **OAuth (Microsoft)**: Azure AD v2 multi-tenant, identical state
  signing — `clearledgr/api/auth.py` (Microsoft OAuth section).
- **Secret management**: `require_secret()` fails closed in production
  if a required secret is missing —
  [clearledgr/core/secrets.py:22-48](../../clearledgr/core/secrets.py#L22-L48).

## CC6.2 — Logical Access (Authorization)

**Implemented.**

- **Role-based access control**: 7 roles
  (`read_only`, `ap_clerk`, `ap_manager`, `financial_controller`, `cfo`,
  `owner`, `api`) with explicit ranks —
  [clearledgr/core/auth.py:507-526](../../clearledgr/core/auth.py#L507-L526).
- **Hierarchical enforcement**: `has_at_least(role, required)` —
  [clearledgr/core/auth.py:560-565](../../clearledgr/core/auth.py#L560-L565).
- **Endpoint guards**: `require_ops_user()`, `require_admin_user()`,
  `require_cfo()`, `require_role(allowed_roles)` —
  [clearledgr/core/auth.py:627-726](../../clearledgr/core/auth.py#L627-L726).
- **Tenant isolation**: every store query is scoped on
  `organization_id`. AP item update path enforces a column whitelist
  (`_AP_ITEM_ALLOWED_COLUMNS`) before constructing the UPDATE —
  [clearledgr/core/stores/ap_store.py:57-93](../../clearledgr/core/stores/ap_store.py#L57-L93).

## CC6.6 — Logical Access (Network)

**Implemented.**

- **TLS-only ingress**: `ProxyAwareHTTPSRedirectMiddleware` issues 307
  redirects from any HTTP request —
  [main.py:263-273](../../main.py#L263-L273) (registered conditionally
  in production at [main.py:1161-1162](../../main.py#L1161-L1162)).
- **HSTS**: `Strict-Transport-Security: max-age=31536000;
  includeSubDomains` — [main.py:969](../../main.py#L969).
- **Content Security Policy**: separate policies for the workspace SPA
  vs API surfaces — [main.py:942-989](../../main.py#L942-L989)
  (`SecurityHeadersMiddleware`).
- **Frame protection**: `X-Frame-Options: SAMEORIGIN` and
  `X-Content-Type-Options: nosniff` —
  [main.py:965-969](../../main.py#L965-L969).
- **Cache hygiene**: `Cache-Control: private, no-store` on every
  tenant-scoped route prefix —
  [main.py:987-988](../../main.py#L987-L988).

## CC6.7 — Restriction of Information Transmission

**Implemented.**

- **TLS 1.2+ enforced** at the proxy layer (Railway/Cloudflare).
  Cipher suites: TLS 1.2 + TLS 1.3 only.
- **PII scrubbing in error pipeline**: `build_sentry_before_send()`
  redacts vendor names, invoice numbers, bank details, and email body
  before any error report ships —
  [clearledgr/core/sentry_config.py:102](../../clearledgr/core/sentry_config.py#L102).
- **`send_default_pii=False`** on Sentry init —
  [main.py:175-191](../../main.py#L175-L191).

## CC7.1 — System Operations (Encryption at Rest)

**Implemented.**

- **Field-level encryption** for sensitive columns. Bank details (IBAN
  + routing + account number), ERP credentials, OAuth refresh tokens
  are stored as Fernet ciphertext — never plaintext.
- **Key derivation**: SHA256 hash of `CLEARLEDGR_SECRET_KEY` →
  base64-urlsafe encoded Fernet key —
  [clearledgr/core/database.py:466-473](../../clearledgr/core/database.py#L466-L473)
  (`_get_fernet`).
- **Encrypt/decrypt API**: `_encrypt_secret`/`_decrypt_secret` —
  [clearledgr/core/database.py:475-482](../../clearledgr/core/database.py#L475-L482).
- **Bank-detail encryption boundary**:
  [clearledgr/core/stores/bank_details.py:215-231](../../clearledgr/core/stores/bank_details.py#L215-L231)
  (`encrypt_bank_details`).
- **Postgres-on-Railway** disk encryption is AES-256 at rest by
  default (Railway-managed, documented in their security docs).

## CC7.2 — Detection of Anomalies

**Implemented.**

- **Append-only audit log**: every state transition writes a row to
  `audit_events`. The table has DB-level triggers that REJECT any
  UPDATE or DELETE —
  [clearledgr/core/database.py:813-833](../../clearledgr/core/database.py#L813-L833) (DDL),
  [clearledgr/core/database.py:395-428](../../clearledgr/core/database.py#L395-L428)
  (`clearledgr_prevent_append_only_mutation` trigger function +
  `trg_audit_events_no_update`/`_no_delete` triggers).
- **Idempotency**: audit inserts use a UNIQUE `idempotency_key`
  constraint to prevent duplicate event recording —
  [clearledgr/core/stores/ap_store.py:1733-1779](../../clearledgr/core/stores/ap_store.py#L1733-L1779)
  (`append_audit_event`).

## CC7.3 — Incident Response

**Implemented.** See [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md) for
the full plan. Detection signals:

- **Sentry** error aggregation with PII scrubbing —
  [main.py:170-196](../../main.py#L170-L196).
- **Structured request logging** —
  [main.py:814-832](../../main.py#L814-L832)
  (`RequestLoggingMiddleware`).
- **Health endpoint** for external uptime monitors — `/health`
  (rendered on the workspace `/status` page in the SPA).

## CC7.4 — Mitigation of Disruptions (Rate Limiting + DoS)

**Implemented.**

- **Rate-limit middleware** with Redis backend (in-memory fallback in
  dev only):
  [clearledgr/services/rate_limit.py:278-309](../../clearledgr/services/rate_limit.py#L278-L309)
  (`RateLimitMiddleware`). Default budget: 300 requests / 60 seconds
  per identity.
- **Production fails closed if Redis is missing** —
  [clearledgr/services/rate_limit.py:62-75](../../clearledgr/services/rate_limit.py#L62-L75).
- **CSRF**: `WorkspaceSessionCSRFMiddleware` validates the
  `X-CSRF-Token` header against the `clearledgr_workspace_csrf` cookie
  via `secrets.compare_digest()` for cookie-authenticated mutating
  requests — [main.py:852-889](../../main.py#L852-L889).

## CC8.1 — Change Management

**Implemented (operational).**

- **Code review**: every change to `main` ships via pull request.
- **Pre-commit hooks**: lint + type-check run on commit (project-level).
- **Dependency updates**: Dependabot watches `pip`, `npm` (web-app +
  extension), and `github-actions` ecosystems —
  [.github/dependabot.yml](../../.github/dependabot.yml).
- **Migrations**: schema changes ship through `clearledgr/core/migrations.py`
  with version tracking in the `schema_versions` table.

## CC9.2 — Vendor Risk

**Documented.** See [SUB_PROCESSORS.md](SUB_PROCESSORS.md) for the
authoritative sub-processor list, scope of data shared, and DPA links
for each.

---

## Privacy / GDPR specifics

| Right | Status | Mechanism |
|---|---|---|
| Right to access | Available on request | Customer-data export endpoint scoped on `organization_id`. |
| Right to erasure | Available on request | Tenant-scoped DELETE pipeline; audit_events retained for retention period before sanitization. |
| Data portability | Available on request | JSON export of all org-scoped data. |
| Sub-processor list | Public | [SUB_PROCESSORS.md](SUB_PROCESSORS.md). |
| Breach notification | 72-hour SLA | Per [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md). |
| Cross-border transfer | EU-US DPF / SCCs | See [DPA.md](DPA.md). |

---

## Known gaps + roadmap

Honesty matters in security review. These are gaps we have not yet
closed; they are not hidden:

1. **SOC2 attestation**: Type 1 audit not yet engaged. Expected to
   complete ~6 weeks after auditor selection. Type 2 requires a 6-month
   observation window.
2. **Penetration testing**: no third-party pen test on file. Planned
   pre-Type-2.
3. **Backup / DR drills**: Postgres backups are managed by Railway with
   30-day retention. We have not yet executed a tabletop DR drill.
4. **Secret scanning in CI**: not yet automated. Manual `gitleaks`
   sweep run on a quarterly cadence; CI integration pending.

Customers can request a current gap list under NDA. We will not hide
deltas from prospects who ask.
