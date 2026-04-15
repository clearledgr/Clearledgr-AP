"""Bank-details encryption, masking, and diff utilities.

DESIGN_THESIS.md §19: *"Bank account numbers or IBANs in plaintext at any
point. IBANs are stored in tokenised form and displayed masked in the UI
(`GB82 **** **** **** 4332`)."* This module is the single owner of bank
account data shape, encryption, and presentation.

Architectural rules:
  - Bank details are NEVER stored in raw JSON columns (e.g., the
    ``ap_items.metadata`` blob). They live in dedicated
    ``bank_details_encrypted`` columns containing Fernet ciphertext.
  - The encryption key is the same one ``_ClearledgrDBBase._get_fernet``
    derives from ``CLEARLEDGR_SECRET_KEY`` — already in production use
    for ERP OAuth tokens.
  - API responses ALWAYS return masked shapes via ``mask_bank_details``.
    There is no "show me the raw value" toggle exposed to clients.
  - The validation gate's bank-details-mismatch check persists only the
    list of mismatched FIELD NAMES (``["iban", "sort_code"]``), never
    the values themselves. The audit trail proves something differed
    without leaking the underlying data.
  - Logs MUST NOT contain raw bank fields. The ``no_plaintext_in_logs``
    test asserts this for the test suite.

This module is pure Python — no DB access. The encryption helpers take
a callable (``encrypt_fn``, ``decrypt_fn``) so callers can pass in the
DB instance's ``_encrypt_secret`` / ``_decrypt_secret`` bound methods
without coupling this module to ``ClearledgrDB``.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical field set
# ---------------------------------------------------------------------------
#
# The full surface of bank-detail fields the system tracks. Any new field
# added here must also be handled by ``mask_bank_details`` so it cannot
# leak unmasked through the API by accident.
# ---------------------------------------------------------------------------

BANK_DETAIL_FIELDS: tuple[str, ...] = (
    "iban",
    "account_number",
    "routing_number",
    "sort_code",
    "swift",
    "bic",
    "account_holder_name",
    "bank_name",
    "currency",
)

# Subset that must be masked when surfaced. ``bank_name``, ``currency``,
# and ``account_holder_name`` are not as sensitive as the actual numbers
# but we still mask the name partially because it can be PII.
SENSITIVE_NUMBER_FIELDS: tuple[str, ...] = (
    "iban",
    "account_number",
    "routing_number",
    "sort_code",
    "swift",
    "bic",
)


# ---------------------------------------------------------------------------
# IBAN validation
#
# An IBAN is country + 2-digit checksum + BBAN. The mod-97 rule is the
# universal integrity check: move the first 4 chars to the end, replace
# each letter with its A=10..Z=35 numeric equivalent, interpret the
# result as a big integer, and assert (n % 97) == 1. A typo in any
# position changes the checksum with overwhelming probability, which
# is the only thing between "I paid Acme" and "I paid the stranger
# whose IBAN differs by one digit from Acme's".
#
# Country-length table covers the SEPA zone + common non-SEPA IBANs we
# see on vendor forms. Countries outside the table are rejected
# (opt-in list, not opt-out) so an attacker can't invent a country
# code to get past the length check.
# ---------------------------------------------------------------------------

# Source: https://www.swift.com/standards/data-standards/iban — lengths
# are fixed per country.
IBAN_COUNTRY_LENGTHS: Dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28,
    "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BR": 29,
    "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24,
    "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29,
    "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22,
    "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28,
    "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23,
    "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20,
    "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20,
    "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19,
    "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15,
    "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29,
    "RO": 24, "RS": 22, "SA": 24, "SC": 31, "SE": 24,
    "SI": 19, "SK": 24, "SM": 27, "ST": 25, "SV": 28,
    "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22,
    "VG": 24, "XK": 20,
}


def normalize_iban(raw: Any) -> str:
    """Canonicalise an IBAN: strip whitespace, uppercase letters.

    Returns the canonical form on any input without raising; caller
    should then run ``validate_iban`` to decide if it's acceptable.
    Empty / non-string input returns an empty string.
    """
    if raw is None:
        return ""
    text = str(raw).strip().replace(" ", "").replace("-", "").upper()
    return text


def validate_iban(raw: Any) -> Optional[str]:
    """Return a human-readable error string if ``raw`` is not a valid
    IBAN, or ``None`` if it passes.

    Checks, in order:
      - non-empty after normalisation
      - two-letter country code present in IBAN_COUNTRY_LENGTHS
      - two-digit checksum position
      - length matches the country's fixed length
      - characters outside BBAN are alphanumeric
      - mod-97 equals 1

    Not a replacement for "does this bank actually exist" — only a
    structural + checksum check. Use in conjunction with whatever
    production-stage verification (Plaid, bank-name match) your
    compliance flow already runs.
    """
    iban = normalize_iban(raw)
    if not iban:
        return "iban_empty"
    if len(iban) < 4:
        return "iban_too_short"
    country = iban[:2]
    if not country.isalpha():
        return "iban_country_not_letters"
    expected_len = IBAN_COUNTRY_LENGTHS.get(country)
    if expected_len is None:
        return f"iban_country_unsupported:{country}"
    if len(iban) != expected_len:
        return f"iban_length_mismatch:expected_{expected_len}_got_{len(iban)}"
    checksum = iban[2:4]
    if not checksum.isdigit():
        return "iban_checksum_not_digits"
    bban = iban[4:]
    if not bban.isalnum():
        return "iban_bban_not_alphanumeric"
    # Mod-97: move first 4 chars to end, letter→digit substitution, check
    rearranged = bban + country + checksum
    digits = []
    for ch in rearranged:
        if ch.isdigit():
            digits.append(ch)
        else:
            digits.append(str(ord(ch) - 55))  # 'A' -> 10 ... 'Z' -> 35
    as_int = int("".join(digits))
    if as_int % 97 != 1:
        return "iban_checksum_invalid"
    return None


def is_valid_iban(raw: Any) -> bool:
    return validate_iban(raw) is None


def normalize_bank_details(raw: Any) -> Optional[Dict[str, Any]]:
    """Coerce arbitrary input to the canonical ``Dict[str, str]`` shape.

    Returns ``None`` for empty / None / non-dict input. Strings are
    stripped. Unknown keys are dropped (defensive — we don't want random
    fields slipping into the encrypted payload). Values that are not
    strings are stringified.

    Note: this function does NOT reject invalid IBANs — it only cleans
    the shape. Validation is a separate step (``validate_iban``) so
    callers can decide whether to hard-reject (vendor portal form),
    soft-warn (agent-extracted IBAN from email text), or flag for
    review.
    """
    if not isinstance(raw, dict):
        return None
    cleaned: Dict[str, str] = {}
    for field in BANK_DETAIL_FIELDS:
        value = raw.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if field == "iban":
            text = normalize_iban(text)
            if not text:
                continue
        cleaned[field] = text
    return cleaned or None


# ---------------------------------------------------------------------------
# Encrypt / decrypt
# ---------------------------------------------------------------------------


def encrypt_bank_details(
    details: Optional[Dict[str, Any]],
    *,
    encrypt_fn: Callable[[str], Optional[str]],
) -> Optional[str]:
    """Serialize + encrypt a bank-details dict for storage.

    ``encrypt_fn`` must be the DB instance's ``_encrypt_secret`` bound
    method (or any equivalent ``str -> str`` Fernet wrapper). Returns
    ``None`` when the input is empty so callers can store NULL in the
    column.
    """
    cleaned = normalize_bank_details(details)
    if not cleaned:
        return None
    payload = json.dumps(cleaned, sort_keys=True, separators=(",", ":"))
    return encrypt_fn(payload)


def decrypt_bank_details(
    ciphertext: Optional[str],
    *,
    decrypt_fn: Callable[[str], Optional[str]],
) -> Optional[Dict[str, str]]:
    """Decrypt + parse a stored bank-details ciphertext.

    Returns ``None`` for empty input. Raises nothing — on a corrupt
    ciphertext or malformed JSON, logs a warning and returns ``None``.
    Callers that need to distinguish "no data" from "decryption failed"
    must use a separate signaling channel.
    """
    if ciphertext is None:
        return None
    text = str(ciphertext).strip()
    if not text:
        return None
    try:
        plain = decrypt_fn(text)
    except Exception as exc:
        logger.warning("Bank details decryption raised: %s", exc)
        return None
    if plain is None:
        return None
    try:
        parsed = json.loads(plain)
    except json.JSONDecodeError:
        logger.warning("Bank details ciphertext decrypted to non-JSON payload")
        return None
    return normalize_bank_details(parsed)


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


def _mask_iban(value: str) -> str:
    """Mask all but the country prefix and last 4 digits.

    Examples:
        ``GB82WEST12345698765432`` → ``GB82 **** **** **** **** 5432``
        ``DE89370400440532013000`` → ``DE89 **** **** **** **** 3000``
    """
    cleaned = "".join(value.split())
    if len(cleaned) < 8:
        return "*" * len(cleaned)
    prefix = cleaned[:4]
    suffix = cleaned[-4:]
    middle_groups = (len(cleaned) - 8) // 4
    if middle_groups < 0:
        middle_groups = 0
    masked_middle = " ".join(["****"] * middle_groups) if middle_groups else ""
    if masked_middle:
        return f"{prefix} {masked_middle} {suffix}"
    return f"{prefix} {suffix}"


def _mask_last4(value: str) -> str:
    """Mask everything except the trailing 4 characters."""
    cleaned = value.strip()
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    return f"{'*' * (len(cleaned) - 4)}{cleaned[-4:]}"


def _mask_sort_code(value: str) -> str:
    """Mask a UK sort code, preserving its dashed shape.

    UK sort codes are 6 digits, conventionally rendered ``20-00-00``.
    We mask the first two segments (4 digits) and keep the final
    segment so the audit can distinguish branches without leaking the
    routing identity. Non-standard inputs fall back to last-4 masking.
    """
    cleaned = value.strip()
    digits_only = "".join(ch for ch in cleaned if ch.isdigit())
    if len(digits_only) == 6 and "-" in cleaned:
        return f"**-**-{digits_only[-2:]}"
    if len(digits_only) == 6:
        return f"****{digits_only[-2:]}"
    return _mask_last4(cleaned)


def _mask_holder_name(value: str) -> str:
    """Mask a holder name to ``F*** L***`` style.

    First letter of each whitespace-separated token, rest as asterisks.
    """
    parts = value.strip().split()
    if not parts:
        return ""
    masked_parts = []
    for part in parts:
        if len(part) <= 1:
            masked_parts.append(part)
        else:
            masked_parts.append(f"{part[0]}{'*' * (len(part) - 1)}")
    return " ".join(masked_parts)


def mask_bank_details(
    details: Optional[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    """Return a presentation-safe shape of a bank-details dict.

    Per-field masking strategy:

    - ``iban``                — country prefix + last-4 (groups of 4)
    - ``account_number``      — last 4 only
    - ``routing_number``      — last 4 only
    - ``sort_code``           — last 4 only (UK sort codes are 6 digits)
    - ``swift`` / ``bic``     — last 4 only
    - ``account_holder_name`` — initials + asterisks
    - ``bank_name``           — passes through unchanged (not sensitive)
    - ``currency``            — passes through unchanged (not sensitive)

    Returns ``None`` for empty input. Returns a NEW dict — does not
    mutate the caller's value.
    """
    cleaned = normalize_bank_details(details)
    if not cleaned:
        return None
    masked: Dict[str, str] = {}
    for field, value in cleaned.items():
        if field == "iban":
            masked[field] = _mask_iban(value)
        elif field == "sort_code":
            masked[field] = _mask_sort_code(value)
        elif field == "account_holder_name":
            masked[field] = _mask_holder_name(value)
        elif field in {"bank_name", "currency"}:
            masked[field] = value
        elif field in SENSITIVE_NUMBER_FIELDS:
            masked[field] = _mask_last4(value)
        else:
            # Defensive — every field in BANK_DETAIL_FIELDS should have
            # explicit handling above. If we miss one, mask it as
            # last-4 by default rather than leaking it.
            masked[field] = _mask_last4(value)
    return masked


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def diff_bank_details_field_names(
    extracted: Optional[Dict[str, Any]],
    stored: Optional[Dict[str, Any]],
) -> List[str]:
    """Return the list of field names that differ between two bank-details dicts.

    The validation gate's bank-details-mismatch check uses this so the
    audit trail can record "iban changed" without recording either the
    old or new IBAN. A mismatch is defined narrowly: BOTH sides carry a
    value for the field AND the values are not equal. Silence on either
    side is NOT a mismatch — invoices rarely echo every field the
    vendor profile tracks, and treating "not mentioned on this invoice"
    as "changed" would produce false-positive fraud signals. Returns an
    empty list when the inputs are equivalent or when one side is empty
    altogether (no signal to compare at all).
    """
    a = normalize_bank_details(extracted) or {}
    b = normalize_bank_details(stored) or {}
    if not a or not b:
        return []
    differing: List[str] = []
    for field in BANK_DETAIL_FIELDS:
        left = a.get(field)
        right = b.get(field)
        # Only flag a mismatch when both sides present a value.
        if not left or not right:
            continue
        if left != right:
            differing.append(field)
    return differing
