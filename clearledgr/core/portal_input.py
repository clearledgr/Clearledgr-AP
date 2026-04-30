"""Vendor-portal input validation.

The vendor portal is the only unauthenticated surface that accepts
user input. Form fields flow into Jinja2 templates and into the
vendor profile. Jinja2's default autoescape blocks script-tag XSS,
and :mod:`clearledgr.core.stores` only uses parameterized SQL — so
the remaining threat surface is:

* Control characters / null bytes that confuse downstream consumers
  (CSV exports, ERP payloads, audit-log renderers).
* Oversized or pathological whitespace that skews rendering.
* Country-code bait (e.g., ``registration_number`` with emoji or
  homoglyphs) designed to fool human reviewers.

Each validator here enforces:

1. Non-empty after strip (for required fields).
2. Length bound (FastAPI already enforces ``max_length``; we re-check
   post-strip because the caller can pass 512 whitespace chars).
3. Character-class allowlist: letters, digits, ASCII-safe
   punctuation. No control chars, no null bytes, no RTL override
   codepoints, no zero-width joiners.
4. Whitespace normalization — multiple spaces collapse to one,
   newlines collapse to one (except in director-list fields where
   newlines are the record separator).

Every validator returns the cleaned string on success, or raises
:class:`PortalInputError` with a vendor-friendly message on failure.
The POST handlers catch ``PortalInputError`` and re-render the form
with the error message inline.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List


class PortalInputError(ValueError):
    """Raised when a portal form field fails validation.

    The ``message`` attribute is vendor-facing — it must be
    short, plain-language, and contain no internal jargon.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


# ---------------------------------------------------------------------------
# Primitive guards
# ---------------------------------------------------------------------------

# Reject: any control char except TAB + LF + CR; reject RTL override
# (U+202D/E); reject zero-width joiners (U+200B..U+200F, U+2060).
_ILLEGAL_CODEPOINTS_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F"  # control
    r"\u202A-\u202E"                    # LTR/RTL embedding/override
    r"\u200B-\u200F\u2060-\u206F"       # zero-width + invisible
    r"]"
)

# Whitespace collapse — any run of whitespace → single space.
_WS_COLLAPSE_RE = re.compile(r"\s+")


def _reject_if_illegal_codepoints(field: str, value: str) -> None:
    if _ILLEGAL_CODEPOINTS_RE.search(value):
        raise PortalInputError(
            field, "contains characters we can't accept — please retype this"
        )


def _canonicalize(value: str) -> str:
    """NFKC-normalize then strip. NFKC collapses compatibility
    codepoints (e.g. full-width digits) into their canonical ASCII
    equivalents so downstream equality + format checks stay boring.
    """
    return unicodedata.normalize("NFKC", value or "").strip()


# ---------------------------------------------------------------------------
# Field-specific validators
# ---------------------------------------------------------------------------


# Registration number: alphanumeric + dashes + spaces. Covers UK
# Companies House (8 chars alphanumeric), US EIN (10 chars w/ dash),
# German HRB format (alphanumeric + slash), French SIREN (9 digits).
_REGISTRATION_NUMBER_RE = re.compile(r"^[A-Za-z0-9/\- ]{1,128}$")


def validate_registration_number(value: str) -> str:
    """Required. 1-128 chars of [A-Za-z0-9/- ] after NFKC + strip."""
    cleaned = _canonicalize(value or "")
    if not cleaned:
        raise PortalInputError("registration_number", "required")
    _reject_if_illegal_codepoints("registration_number", cleaned)
    if not _REGISTRATION_NUMBER_RE.match(cleaned):
        raise PortalInputError(
            "registration_number",
            "only letters, digits, dashes, and slashes are allowed",
        )
    return cleaned


# VAT number: country prefix (letters) + digits/letters. EU format
# allows up to 2-letter prefix + up to 12 alphanumeric; non-EU
# varies. Keep it loose: 4-15 chars, alphanumeric only.
_VAT_NUMBER_RE = re.compile(r"^[A-Za-z0-9]{4,15}$")


def validate_vat_number(value: str) -> str:
    """Optional. When present, 4-15 alphanumeric chars after strip."""
    cleaned = _canonicalize(value or "").replace(" ", "").upper()
    if not cleaned:
        return ""
    _reject_if_illegal_codepoints("vat_number", cleaned)
    if not _VAT_NUMBER_RE.match(cleaned):
        raise PortalInputError(
            "vat_number", "should be 4-15 letters and digits (no symbols)"
        )
    return cleaned


# Registered address: free-form, but reject control chars + limit
# characters to Latin-1 printable + common punctuation. Collapse
# whitespace. Multi-line OK (keep \n as separator) but collapse
# consecutive newlines.
_ADDRESS_ALLOWED_RE = re.compile(r"^[\w\s,.\-'/&()#:;\u00C0-\u024F]+$")


def validate_registered_address(value: str) -> str:
    """Required. 1-512 chars of word chars + basic punctuation."""
    raw = _canonicalize(value or "")
    if not raw:
        raise PortalInputError("registered_address", "required")
    _reject_if_illegal_codepoints("registered_address", raw)
    # Normalize multi-line addresses: strip each line, drop blanks,
    # rejoin with single newlines, collapse inline spaces.
    lines = [
        _WS_COLLAPSE_RE.sub(" ", line).strip()
        for line in raw.splitlines()
    ]
    cleaned = "\n".join(line for line in lines if line)
    if len(cleaned) > 512:
        raise PortalInputError("registered_address", "too long (max 512 chars)")
    if not _ADDRESS_ALLOWED_RE.match(cleaned.replace("\n", " ")):
        raise PortalInputError(
            "registered_address",
            "contains unexpected characters — please use plain address text",
        )
    return cleaned


# Director name: letters (incl. accented), spaces, hyphens,
# apostrophes, periods. Max 128 per name.
_DIRECTOR_NAME_RE = re.compile(r"^[A-Za-z\u00C0-\u024F .'\-]{1,128}$")
_MAX_DIRECTORS = 32


def validate_director_names(value: str) -> List[str]:
    """Optional. Newline-separated list, 0-32 names, each ≤128 chars."""
    raw = _canonicalize(value or "")
    if not raw:
        return []
    _reject_if_illegal_codepoints("director_names", raw)
    candidates = [
        _WS_COLLAPSE_RE.sub(" ", line).strip()
        for line in raw.splitlines()
    ]
    names = [n for n in candidates if n]
    if len(names) > _MAX_DIRECTORS:
        raise PortalInputError(
            "director_names", f"too many directors (max {_MAX_DIRECTORS})"
        )
    for n in names:
        if not _DIRECTOR_NAME_RE.match(n):
            raise PortalInputError(
                "director_names",
                f"name {n!r} contains characters we can't accept",
            )
    return names


# Account holder name: Unicode letters, spaces, hyphens, apostrophes,
# periods. Max 128 chars. Required on bank-details submit.
_HOLDER_NAME_RE = re.compile(r"^[A-Za-z\u00C0-\u024F .'\-]{1,128}$")


def validate_account_holder_name(value: str) -> str:
    cleaned = _canonicalize(value or "")
    if not cleaned:
        raise PortalInputError("account_holder_name", "required")
    _reject_if_illegal_codepoints("account_holder_name", cleaned)
    cleaned = _WS_COLLAPSE_RE.sub(" ", cleaned)
    if not _HOLDER_NAME_RE.match(cleaned):
        raise PortalInputError(
            "account_holder_name",
            "use letters, spaces, hyphens, apostrophes, or periods only",
        )
    return cleaned


# Bank name: letters, digits, spaces, common punctuation. Max 128.
_BANK_NAME_RE = re.compile(r"^[\w\s.,'\-&()\u00C0-\u024F]{1,128}$")


def validate_bank_name(value: str) -> str:
    cleaned = _canonicalize(value or "")
    if not cleaned:
        return ""
    _reject_if_illegal_codepoints("bank_name", cleaned)
    cleaned = _WS_COLLAPSE_RE.sub(" ", cleaned)
    if not _BANK_NAME_RE.match(cleaned):
        raise PortalInputError(
            "bank_name",
            "contains characters we can't accept — please use plain text",
        )
    return cleaned
