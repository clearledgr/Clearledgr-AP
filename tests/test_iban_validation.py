"""IBAN mod-97 + country + length validation.

The vendor portal is the only place payment routing data originates
from outside our system. A typo in any digit of an IBAN changes the
mod-97 checksum with overwhelming probability — which is the only
safeguard between "paid Acme" and "paid the stranger whose IBAN
differs by one digit from Acme's". These tests lock that safeguard
in at the function level so a future refactor can't accidentally
weaken it.
"""
from __future__ import annotations

from clearledgr.core.stores.bank_details import (
    is_valid_iban,
    normalize_iban,
    validate_iban,
)


# A spread of real-format IBANs from the SWIFT registry examples.
# These are the canonical "known good" values — if any ever start
# failing validation, something in the algorithm broke.
KNOWN_GOOD_IBANS = [
    "GB82WEST12345698765432",   # UK
    "DE89370400440532013000",   # Germany
    "FR1420041010050500013M02606",  # France (alphanumeric BBAN)
    "NL91ABNA0417164300",       # Netherlands
    "CH9300762011623852957",    # Switzerland
    "ES9121000418450200051332", # Spain
    "IT60X0542811101000000123456",  # Italy
    "BE68539007547034",         # Belgium
    "AE070331234567890123456",  # UAE
    "SA0380000000608010167519", # Saudi Arabia
]


def test_known_good_ibans_pass():
    for iban in KNOWN_GOOD_IBANS:
        assert is_valid_iban(iban), f"expected {iban} to validate"


def test_whitespace_and_dashes_tolerated():
    assert is_valid_iban("GB82 WEST 1234 5698 7654 32")
    assert is_valid_iban("GB82-WEST-1234-5698-7654-32")
    # Lowercase input normalises to uppercase
    assert is_valid_iban("gb82west12345698765432")


def test_single_digit_typo_rejected():
    """Any one-digit transposition should fail the checksum."""
    # Change one digit in the checksum region
    assert validate_iban("GB83WEST12345698765432") == "iban_checksum_invalid"
    # Change one digit in the BBAN
    assert validate_iban("GB82WEST12345698765431") == "iban_checksum_invalid"


def test_wrong_length_rejected():
    # GB is 22 chars; this is 21
    assert validate_iban("GB82WEST1234569876543") == "iban_length_mismatch:expected_22_got_21"
    # DE is 22; this is 23
    assert validate_iban("DE893704004405320130000") == "iban_length_mismatch:expected_22_got_23"


def test_unsupported_country_rejected():
    # ZZ isn't a country, shouldn't be in our table
    result = validate_iban("ZZ8200000000000000000000")
    assert result is not None and result.startswith("iban_country_unsupported")


def test_empty_and_too_short():
    assert validate_iban("") == "iban_empty"
    assert validate_iban(None) == "iban_empty"
    assert validate_iban("GB") == "iban_too_short"


def test_non_alpha_country_rejected():
    assert validate_iban("12345678901234567890") == "iban_country_not_letters"


def test_non_digit_checksum_rejected():
    assert validate_iban("GBAAWEST12345698765432") == "iban_checksum_not_digits"


def test_normalize_iban_strips_spaces_and_dashes():
    assert normalize_iban(" gb82 west 1234 5698 7654 32 ") == "GB82WEST12345698765432"
    assert normalize_iban("gb-82-west-1234") == "GB82WEST1234"
    assert normalize_iban(None) == ""
