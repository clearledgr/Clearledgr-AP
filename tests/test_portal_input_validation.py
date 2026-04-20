"""Vendor-portal input validation contract tests.

The vendor portal is the only unauthenticated surface that accepts
user input. These tests lock in the allowlist for every field
:mod:`clearledgr.core.portal_input` guards, so a future refactor
that loosens the regex has an audible failure signal.

Threat model covered:
- Control characters / null bytes slipping through FastAPI's ``Form``
- RTL-override / zero-width-joiner baiting
- Oversized whitespace
- Country-code and format injection (VAT number especially)
- Director-list abuse (too many, too long, injection chars)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core.portal_input import (  # noqa: E402
    PortalInputError,
    validate_account_holder_name,
    validate_bank_name,
    validate_director_names,
    validate_registered_address,
    validate_registration_number,
    validate_vat_number,
)


class TestRegistrationNumber:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("12345678", "12345678"),             # UK Companies House
            ("SC123456", "SC123456"),             # UK Scottish prefix
            ("12-3456789", "12-3456789"),         # US EIN
            ("HRB 12345", "HRB 12345"),           # German HRB
            ("  123456  ", "123456"),             # strip whitespace
        ],
    )
    def test_valid_formats(self, value, expected):
        assert validate_registration_number(value) == expected

    def test_required(self):
        with pytest.raises(PortalInputError):
            validate_registration_number("")
        with pytest.raises(PortalInputError):
            validate_registration_number("   ")

    def test_rejects_control_chars(self):
        with pytest.raises(PortalInputError):
            validate_registration_number("1234\x00567")

    def test_rejects_rtl_override(self):
        # U+202E Right-to-Left Override — the classic filename-spoofing
        # attack vector. Must not survive.
        with pytest.raises(PortalInputError):
            validate_registration_number("12\u202E34")

    def test_rejects_emoji(self):
        with pytest.raises(PortalInputError):
            validate_registration_number("1234\U0001F4A9")

    def test_rejects_sql_like_payload(self):
        with pytest.raises(PortalInputError):
            validate_registration_number("1234' OR 1=1--")


class TestVATNumber:
    @pytest.mark.parametrize(
        "raw,cleaned",
        [
            ("GB 123 456 789", "GB123456789"),      # strip spaces
            ("gb123456789", "GB123456789"),         # upper
            ("FR12345678901", "FR12345678901"),     # EU
        ],
    )
    def test_valid_formats(self, raw, cleaned):
        assert validate_vat_number(raw) == cleaned

    def test_optional(self):
        assert validate_vat_number("") == ""
        assert validate_vat_number("   ") == ""

    def test_too_short(self):
        with pytest.raises(PortalInputError):
            validate_vat_number("GB1")

    def test_too_long(self):
        with pytest.raises(PortalInputError):
            validate_vat_number("A" * 16)

    def test_rejects_punctuation(self):
        with pytest.raises(PortalInputError):
            validate_vat_number("GB-123456789")


class TestRegisteredAddress:
    def test_multiline_normalization(self):
        raw = "  123 Main St  \n\n  Suite 4B  \n  London EC1A 1BB  "
        assert validate_registered_address(raw) == (
            "123 Main St\nSuite 4B\nLondon EC1A 1BB"
        )

    def test_allows_accented_chars(self):
        raw = "35 Rue de l'Opéra, 75002 Paris"
        assert validate_registered_address(raw) == raw

    def test_required(self):
        with pytest.raises(PortalInputError):
            validate_registered_address("")

    def test_rejects_null_byte(self):
        with pytest.raises(PortalInputError):
            validate_registered_address("123 Main\x00 St")

    def test_rejects_zero_width_joiner(self):
        with pytest.raises(PortalInputError):
            validate_registered_address("123 Main\u200B St")

    def test_bounded_length(self):
        raw = "A" * 600
        with pytest.raises(PortalInputError):
            validate_registered_address(raw)


class TestDirectorNames:
    def test_splits_on_newlines(self):
        raw = "Alice Smith\nBob Jones\n\nCharlie Brown"
        assert validate_director_names(raw) == [
            "Alice Smith", "Bob Jones", "Charlie Brown",
        ]

    def test_optional(self):
        assert validate_director_names("") == []

    def test_accepts_accented_characters(self):
        raw = "José García\nÁngel Núñez"
        assert validate_director_names(raw) == ["José García", "Ángel Núñez"]

    def test_accepts_hyphens_and_apostrophes(self):
        raw = "Mary-Ann O'Connor"
        assert validate_director_names(raw) == ["Mary-Ann O'Connor"]

    def test_rejects_digits_in_names(self):
        with pytest.raises(PortalInputError):
            validate_director_names("Alice Smith\nBob2 Jones")

    def test_rejects_excess_directors(self):
        raw = "\n".join(f"Person {chr(65 + i)}" for i in range(40))
        with pytest.raises(PortalInputError):
            validate_director_names(raw)

    def test_rejects_control_chars_in_name(self):
        with pytest.raises(PortalInputError):
            validate_director_names("Alice\x01 Smith")


class TestAccountHolderName:
    def test_valid(self):
        assert validate_account_holder_name("Acme UK Ltd.") == "Acme UK Ltd."

    def test_strips_and_collapses_whitespace(self):
        assert validate_account_holder_name("  Acme    Ltd  ") == "Acme Ltd"

    def test_required(self):
        with pytest.raises(PortalInputError):
            validate_account_holder_name("")

    def test_rejects_digits(self):
        # Accounts with digits in holder name are almost always a
        # bait-and-switch; real company names rarely carry them.
        with pytest.raises(PortalInputError):
            validate_account_holder_name("Acme 123 Ltd")


class TestBankName:
    def test_valid(self):
        assert validate_bank_name("Barclays plc") == "Barclays plc"

    def test_accepts_digits_and_punctuation(self):
        assert validate_bank_name("1st Financial (UK) Ltd.") == "1st Financial (UK) Ltd."

    def test_optional(self):
        assert validate_bank_name("") == ""

    def test_rejects_rtl_override(self):
        with pytest.raises(PortalInputError):
            validate_bank_name("Good Bank\u202E evil")
