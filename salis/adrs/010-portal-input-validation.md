# ADR-010: Why NFKC + RTL-override rejection on vendor portal input

Status: Accepted
Date: 2026-04-20 (hardening pass this session, commit `e593e20`)
Author: Mo

## Context

The vendor portal (`clearledgr/api/vendor_portal.py`) is the **only unauthenticated surface in the product that accepts user input.** Every other input-accepting surface is gated by an authenticated JWT; the portal is gated only by a magic-link token that carries its own per-vendor scope.

That means a vendor (who is not a Clearledgr customer — they're the customer's vendor) submits:

- `registration_number` — business registration ID
- `vat_number` — VAT ID (optional)
- `registered_address` — multi-line business address
- `director_names` — newline-separated list of directors
- `iban` — bank IBAN
- `account_holder_name`, `bank_name` — bank context

Defaults: FastAPI `Form` with `max_length`. That's it.

The threat surface is narrow but real:

1. **Jinja2 autoescape** already blocks script-tag XSS. Good.
2. **Parameterized SQL** already blocks injection. Good.
3. Remaining threats Jinja2 and parameterized SQL don't cover:
   - Control characters / null bytes that confuse CSV exports, ERP payload rendering, or audit-log display.
   - RTL-override codepoints (U+202A..U+202E) — the classic filename-spoofing class. A vendor named `ACME\u202E EVIL` renders differently in some UIs than a safe audit/ERP record would want.
   - Zero-width joiners (U+200B..U+200F, U+2060) — invisible chars that make the same-looking-name compare differently.
   - Homoglyph baits (full-width digits, Cyrillic "а" vs Latin "a") that slip through length checks and look identical to humans.
   - Oversized whitespace runs that skew rendering.

## Decision

**Every portal form field goes through a field-specific validator in `clearledgr/core/portal_input.py` before reaching the store.**

Each validator does:

1. **NFKC normalization.** `unicodedata.normalize("NFKC", value)` collapses compatibility codepoints (full-width digits become ASCII digits, certain ligatures expand) so equality + format checks stay boring.

2. **Control-char + invisible-char rejection.** A regex rejects:
   - Control chars except TAB/LF/CR
   - U+202A..U+202E (LTR/RTL embedding/override)
   - U+200B..U+200F and U+2060..U+206F (zero-width + invisible format chars)

3. **Field-specific character allowlist.** Each field has a regex that says what IS allowed, not what's banned. Examples:
   - `registration_number`: `[A-Za-z0-9/\- ]{1,128}` (UK Companies House, US EIN, German HRB, French SIREN all fit)
   - `vat_number`: `[A-Za-z0-9]{4,15}` (country prefix + digits; upper-cased + stripped)
   - `account_holder_name`: `[A-Za-z\u00C0-\u024F .'\-]{1,128}` (accented letters, spaces, hyphens, apostrophes, periods — no digits)
   - `bank_name`: word chars + basic punctuation
   - `director_names`: multiple names separated by newlines, each name constrained like `account_holder_name`, max 32 directors

4. **Length re-check after strip.** FastAPI's `Form(..., max_length=X)` is checked on the raw input, but a string of 500 whitespace chars strips to 0 and would otherwise slip through. Validators re-check length on the cleaned value.

5. **Vendor-facing error message.** When a field fails, `PortalInputError` is raised with a short, plain-language message. The portal POST handlers catch it and re-render the form with `_redirect_with_error(token, "Please check the registered address field: contains characters we can't accept")`.

## Consequences

**Wins:**

1. **The remaining 10% of input-layer attacks is closed.** Jinja2 autoescape + parameterized SQL covered 90%. NFKC + allowlist + control-char rejection is the last 10% that most apps skip and most get burned by eventually.

2. **Audit trails stay clean.** A vendor name submitted through the portal can appear in audit logs, CSV exports, and ERP payloads without rendering surprises.

3. **Single regression fence.** `tests/test_portal_input_validation.py` has 38 tests that lock in the allowlist. A future loosening of the regex trips the test.

4. **Vendor-facing errors are human-readable.** No "validation failed on field x" with a regex pasted into the error. The vendor sees "contains characters we can't accept — please retype this."

**Costs:**

1. **The allowlist is moderately tight.** A vendor with a business name that includes a character we didn't anticipate (an unusual punctuation mark, a language character class we forgot) will hit "can't accept" until we widen the regex. The fix is a one-line regex update, but the first instance is a support ticket.

2. **NFKC normalization is one-way.** A vendor who typed full-width digits (common on some CJK keyboards) will see their data persisted as ASCII digits. That's desired for downstream consistency but it's a silent transformation from the vendor's perspective.

3. **Performance cost.** Each field runs a regex and an NFKC normalize on submit. Negligible at portal-volume traffic; called out for completeness.

## Alternatives considered

- **Rely on Pydantic validators.** Pydantic validators on Form params don't fire before the field is parsed; adding them post-parse would be redundant with the existing `max_length`. Our validators ARE Pydantic-adjacent functions, just called explicitly rather than via Pydantic's metaclass. Same outcome, simpler code path.

- **Block only the worst characters (control chars + null bytes); skip NFKC + RTL-override rejection.** Rejected — RTL-override specifically is a known CVE-class (filename spoofing, Trojan Source). Skipping it because we haven't been attacked yet is the wrong posture for a product with a compliance-tier trust promise.

- **Escape at render time instead of validating at input time.** Rejected — data persists. We'd have to escape consistently at every render site (Jinja2 templates, ERP payloads, audit-log readers, CSV exports) and any miss would leak. Easier to reject at the boundary.

- **Shared "sanitize everything" helper.** Rejected — the right allowlist for `vat_number` is different from the right allowlist for `registered_address`. One function per field is ~30 extra lines of code and avoids the "what does `sanitize()` actually allow?" ambiguity.

## Reference

- `clearledgr/core/portal_input.py` — the validator module.
- `clearledgr/api/vendor_portal.py:230-316` — where the validators are called inline during `submit_kyc`.
- `clearledgr/api/vendor_portal.py:356-412` — same for `submit_bank_details`.
- `tests/test_portal_input_validation.py` — the 38-test regression fence.
