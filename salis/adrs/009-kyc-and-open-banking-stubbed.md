# ADR-009: Why KYC and open-banking providers are stubbed

Status: Accepted
Date: 2026-03-27 (stub boundaries introduced during vendor onboarding work)
Author: Mo

## Context

Vendor onboarding is a real workflow in the product. A vendor gets a magic-link email, submits KYC details (registered address, registration number, VAT, directors), submits bank details (IBAN + account holder + bank name), and the session transitions toward `active`.

Two integrations would make this real-world usable:

1. **KYC provider** (Companies House for UK, equivalents for other jurisdictions) — for verifying the business identity provided on the KYC form. Without this, the KYC data is vendor-self-attested and not corroborated.
2. **Open-banking provider** (TrueLayer for UK/EU, Adyen or Plaid for others) — for verifying that the bank account the vendor submitted actually belongs to them. The old micro-deposit verification flow (two small deposits + vendor confirms the amounts) was removed in favor of open-banking account ownership checks.

Both require signed contracts with real providers. Contract negotiation includes pricing, data-processing agreements, production-grade access approval, and sometimes regional certification.

No such contract is signed for either at V1.

## Decision

**Ship vendor onboarding as infrastructure, stub both provider integrations.**

Specifically:

- KYC form submission saves the data to the vendor profile (`vendor_profiles` table, encrypted where sensitive) and transitions the session to `bank_verify`. No external verification.
- Bank details submission encrypts the IBAN with Fernet (`TOKEN_ENCRYPTION_KEY`-derived) and saves. IBAN is validated structurally (mod-97 checksum) but account-ownership is not verified. Session transitions directly `bank_verify` → `bank_verified` on submit (the direct transition was introduced when micro-deposit was removed).
- Integration points for real providers exist in `clearledgr/services/vendor_onboarding_lifecycle.py` but each provider call is a no-op stub awaiting implementation.

This is documented in the vendor onboarding spec (`vendor-onboarding-spec.md`) and in the session's memory files.

## Consequences

**Wins:**

1. The onboarding UX ships. Vendors get a real magic-link email, a real portal, real form submission, real Box state transitions. A pilot can exercise the end-to-end flow without provider integration.

2. Infrastructure (Box state machine, token issuance + single-use revocation, portal input validation with RTL/ZWJ rejection, encrypted bank details storage) is all real and production-grade. The stub boundary is genuinely the last mile — provider API call, response handling, failure path.

3. No premature contract. We don't want to be locked into Companies House's pricing or TrueLayer's terms before we have a customer. Both providers are easier to negotiate with when there's real volume to discuss.

**Costs:**

1. **The product does not actually onboard a vendor to full production-grade state today.** Marketing this as "vendor onboarding shipped" would be misleading. Internal framing is correct: infrastructure shipped, providers stubbed. External framing should match.

2. **The stub boundaries have to hold when we integrate real providers.** If a stub quietly accepts inputs that a real provider would reject, the integration will surface bugs the stub was covering.

3. **The IBAN change freeze flow** (when a vendor updates their bank details on an existing active profile, a manual verification step is required before the change takes effect — see `ap_items.iban_change_*` columns) was partly motivated by open-banking re-verification being the replacement for micro-deposit-retrigger. Without the open-banking provider, the freeze flow is a manual human-in-the-loop process. Works, but slower than it'll be post-integration.

## Alternatives considered

- **Don't ship vendor onboarding at all until providers are contracted.** Rejected — the infrastructure (Box abstraction, magic-link tokens, portal input validation, Fernet encryption) has reusable value even without providers. Also: having the flow available lets us show customers and design partners what onboarding looks like, which informs pricing negotiations with providers.

- **Ship with micro-deposit verification instead.** Rejected — the micro-deposit flow was removed in commit `0d7578b` (per session memory). Micro-deposits are slow (2-3 business days) and have their own support burden. Open-banking is the correct destination.

- **Ship with mock provider responses (fake Companies House data returned from a local fixtures file).** Rejected — this crosses the line from "infrastructure + stubs" to "pretend product." If a customer's legal team asks whether we verify businesses, the honest answer with stubs is "not yet, here's the integration roadmap." With mocks, the answer would be muddied.

## When to un-stub

Trigger condition: first customer whose contract requires verified vendor onboarding. At that point:

1. Select provider (probably Companies House API for UK KYC; probably TrueLayer for UK/EU open-banking).
2. Sign contract.
3. Implement `clearledgr/services/vendor_onboarding_lifecycle.enrich_vendor_on_kyc` for real against the provider.
4. Implement the open-banking verification adapter against the session transition `bank_verify` → `bank_verified`.
5. Update the direct transition to a provider-gated one.
6. Write tests against provider sandboxes.
7. Remove the stub comments; add provider-specific error handling.

Effort estimate when triggered: 1-2 weeks per provider depending on sandbox access + contract turnaround.

## Reference

- `vendor-onboarding-spec.md` — the engineering spec (1500 lines).
- `clearledgr/services/vendor_onboarding_lifecycle.py` — where stub boundaries live.
- `clearledgr/core/stores/vendor_store.py:1785` — `transition_onboarding_session_state` with the direct `bank_verify` → `bank_verified` edge documented in the comment.
- `clearledgr/api/vendor_portal.py` — the portal endpoints that drive the flow.
- `clearledgr/core/portal_input.py` — input validation (real, not stubbed).
- `clearledgr/core/portal_auth.py` — magic-link auth (real, not stubbed).
