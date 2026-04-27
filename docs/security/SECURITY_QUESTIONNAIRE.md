# Security Questionnaire — Pre-fills

Pre-filled answers to the recurring 80% of SIG / CAIQ / vendor security
questionnaires. Sales engineers can copy from here verbatim. If a
question requires more nuance, link to [CONTROLS.md](CONTROLS.md) or
[INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md) instead of paraphrasing.

Last reviewed: 2026-04-26.

---

## Company / governance

**Q: Years in business?**
Founded 2025. GA in 2026.

**Q: Number of employees with access to customer data?**
Single-digit, all under signed confidentiality agreements. Access
follows least-privilege; production secrets are gated behind Railway
RBAC.

**Q: Background checks?**
Performed for all employees with production access prior to onboarding.

**Q: Security training cadence?**
Annual security-awareness training; ad-hoc training after any incident.

---

## Data handling

**Q: What customer data does your service process?**
Workspace user identifiers, vendor metadata, invoice content, bank
details, and email content from explicitly-connected mailboxes. Full
list in [DPA.md](DPA.md).

**Q: Is customer data segregated?**
Yes. Logical multi-tenancy via `organization_id`. Every query is
scoped on `organization_id`. Tenant isolation tested in
`tests/test_tenant_isolation*.py`.

**Q: Where is data stored?**
US-East (Railway-managed Postgres). EU-region is on the roadmap when
demand justifies it.

**Q: Encryption at rest?**
Yes. AES-256 disk-level via Railway. Field-level Fernet encryption for
bank details, OAuth refresh tokens, ERP credentials. See CC7.1 in
[CONTROLS.md](CONTROLS.md).

**Q: Encryption in transit?**
TLS 1.2+ enforced. HSTS with `max-age=31536000; includeSubDomains`.
HTTP→HTTPS redirect at the proxy layer. See CC6.6 in
[CONTROLS.md](CONTROLS.md).

**Q: Key management?**
Application keys managed via Railway environment variables (encrypted
at rest, role-gated access). Rotation procedure: documented; rotated
after any departure or suspected exposure.

**Q: Backup retention?**
30 days, managed by Railway. Restoration tested ad-hoc; dedicated DR
drill on the 2026 roadmap.

**Q: Data destruction on termination?**
Deletion within 30 days of termination notice. `audit_events` retained
for the contractually-agreed retention window before sanitization.

---

## Access control

**Q: Authentication options?**
Email/password (bcrypt), Google OAuth, Microsoft OAuth (Azure AD v2
multi-tenant). MFA available via the IdP layer (Google / Microsoft).

**Q: SSO support?**
Google + Microsoft today. SAML for the workspace login page is on the
roadmap; SCIM provisioning is an Enterprise-tier feature post-GA.

**Q: Role-based access control?**
Yes. 7 roles with hierarchical enforcement. See CC6.2 in
[CONTROLS.md](CONTROLS.md).

**Q: Session management?**
HttpOnly cookies with `SameSite=lax` and `Secure` flag in production.
Session lifetime: 7 days, refreshed on activity. CSRF token issued
alongside the session and validated on every mutating request.

**Q: Audit logging?**
Append-only. Every state transition writes a row to `audit_events`.
DB-level triggers REJECT UPDATE/DELETE on the audit table. See CC7.2
in [CONTROLS.md](CONTROLS.md).

---

## Application security

**Q: SDLC?**
PR-required for every change to `main`. Pre-commit lint + type-check.
Dependabot enabled across pip, npm, and github-actions ecosystems.

**Q: Penetration testing?**
Not yet engaged. Scheduled pre-Type-2 SOC2.

**Q: Vulnerability scanning?**
Dependabot for dependency CVEs. Manual `gitleaks` sweep on a quarterly
cadence; CI integration pending.

**Q: Secure coding training?**
Annual OWASP Top 10 review for engineers with production access.

**Q: Security headers?**
CSP, HSTS, X-Frame-Options, X-Content-Type-Options, X-XSS-Protection,
Cache-Control on tenant-scoped routes. See CC6.6 in
[CONTROLS.md](CONTROLS.md).

**Q: SQL injection prevention?**
Parameterised queries throughout. Dynamic UPDATE constructions use
column whitelists (`_AP_ITEM_ALLOWED_COLUMNS`). See CC6.2 in
[CONTROLS.md](CONTROLS.md).

**Q: XSS prevention?**
Preact's default escape on rendering. CSP `default-src 'self'` plus
explicit allowlists for trusted CDNs.

**Q: CSRF prevention?**
`WorkspaceSessionCSRFMiddleware` validates `X-CSRF-Token` header
against `clearledgr_workspace_csrf` cookie via `secrets.compare_digest()`
on every mutating cookie-authenticated request. See CC7.4 in
[CONTROLS.md](CONTROLS.md).

**Q: Rate limiting?**
Yes. 300 req/60s default. Redis-backed in production; fails closed if
Redis is missing. See CC7.4 in [CONTROLS.md](CONTROLS.md).

---

## Operational security

**Q: How are production secrets stored?**
Railway environment variables (encrypted at rest, role-gated access).
No secrets in source control. `clearledgr/core/secrets.py:require_secret()`
fails closed in production if a required secret is missing.

**Q: Logging + monitoring?**
Structured request logging via `RequestLoggingMiddleware`. Sentry for
errors with PII scrubbing applied at the SDK boundary
(`build_sentry_before_send` in `clearledgr/core/sentry_config.py`).

**Q: Incident response plan?**
Yes. See [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md). 72-hour breach
notification SLA aligned with GDPR.

**Q: Disaster recovery RPO/RTO?**
RPO: 24h (Railway managed-Postgres point-in-time recovery within 30
days). RTO: best-effort 4h for total-region failure; we have not yet
executed a tabletop drill.

---

## Compliance

**Q: SOC2?**
Type 1 audit not yet completed. Controls equivalent to SOC2 are
implemented and documented in [CONTROLS.md](CONTROLS.md). Audit
engagement expected to complete ~6 weeks after auditor selection.

**Q: ISO 27001?**
Not certified.

**Q: GDPR?**
DPA available; GDPR-aligned controls in place. See [DPA.md](DPA.md).

**Q: HIPAA?**
Not applicable. Clearledgr is not a covered entity or business
associate; we do not process PHI.

**Q: PCI-DSS?**
Not in scope. Customer payments via Stripe (Stripe is PCI Level 1).
Clearledgr does not store payment-card numbers.

---

## Sub-processors

**Q: List of sub-processors?**
See [SUB_PROCESSORS.md](SUB_PROCESSORS.md).

**Q: Sub-processor change notification?**
30 days advance notice via email to the workspace `Owner`.

---

## Termination

**Q: Data export at termination?**
JSON export of all org-scoped data, available on request within 30
days of termination notice.

**Q: Data deletion?**
Within 30 days of termination notice. `audit_events` retained per the
contracted retention window.

---

## Contact

For questions not covered here, email `security@clearledgr.com`.
