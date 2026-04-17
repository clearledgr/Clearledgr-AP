"""Tests for clearledgr/services/vendor_domain_lookalike.py.

Coverage against DESIGN_THESIS §8 attack vectors:
  - Homoglyph (digit-for-letter): str1pe.com vs stripe.com
  - Homoglyph (Cyrillic/Greek): cyrillic-a impersonation
  - Homoglyph (rn → m): modern.com vs moder.com
  - TLD swap: stripe.co vs stripe.com
  - Edit distance: stripes.com vs stripe.com (plural attack)
  - Edit distance with transposition: stirpe.com vs stripe.com
  - No-match cases: genuinely unrelated domains don't false-positive
  - Legitimate multi-brand: acme-ltd.com vs acme.com (substring, skipped)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from clearledgr.services.vendor_domain_lookalike import (
    _canonicalize_homoglyphs,
    _damerau_levenshtein,
    _registrable_base,
    _split_sld_tld,
    collect_org_trusted_domains,
    detect_lookalike,
)


class TestHomoglyphCanonicalisation:
    def test_digit_for_letter(self):
        assert _canonicalize_homoglyphs("str1pe.com") == "strlpe.com"
        assert _canonicalize_homoglyphs("stripe.com") == "stripe.com"
        # Canonical form maps both sides consistently: 'i' stays 'i',
        # '1' becomes 'l'. The detection works because the CANONICAL
        # forms of "str1pe" and "stripe" don't match exactly — instead
        # the canonical of "str1pe" happens to equal "strlpe" which has
        # edit distance 1 to "stripe". This is covered by the
        # edit-distance pass, not homoglyph.

    def test_cyrillic_a_impersonation(self):
        # "аpple.com" with Cyrillic 'а' should canonicalise to latin 'apple.com'
        cyrillic_apple = "\u0430" + "pple.com"
        assert _canonicalize_homoglyphs(cyrillic_apple) == "apple.com"

    def test_rn_to_m_substitution(self):
        # "moder.com" after canonicalisation should also match "modern.com"
        # when the attacker uses "rn" → "m" confusable.
        # "rnoder.com" → "moder.com" after rn→m collapse
        assert _canonicalize_homoglyphs("rnoder.com") == "moder.com"
        assert _canonicalize_homoglyphs("mode.com") == "mode.com"

    def test_empty_input(self):
        assert _canonicalize_homoglyphs("") == ""


class TestRegistrableBase:
    def test_simple_domain(self):
        assert _registrable_base("stripe.com") == "stripe.com"

    def test_subdomain_stripped(self):
        assert _registrable_base("billing.stripe.com") == "stripe.com"
        assert _registrable_base("mail.sub.stripe.com") == "stripe.com"

    def test_multi_part_tld(self):
        assert _registrable_base("mail.acme.co.uk") == "acme.co.uk"
        assert _registrable_base("acme.co.uk") == "acme.co.uk"

    def test_single_label_passes_through(self):
        # Not a real domain — return as-is rather than crash.
        assert _registrable_base("localhost") == "localhost"


class TestSLDTLDSplit:
    def test_com(self):
        assert _split_sld_tld("stripe.com") == ("stripe", "com")

    def test_co_uk(self):
        assert _split_sld_tld("acme.co.uk") == ("acme", "co.uk")


class TestDamerauLevenshtein:
    def test_identical(self):
        assert _damerau_levenshtein("stripe", "stripe") == 0

    def test_single_substitution(self):
        assert _damerau_levenshtein("stripe", "strlpe") == 1

    def test_single_insertion(self):
        assert _damerau_levenshtein("stripe", "stripes") == 1

    def test_single_deletion(self):
        assert _damerau_levenshtein("stripe", "stipe") == 1

    def test_adjacent_transposition_is_one_edit(self):
        # "stirpe" ↔ "stripe" is ONE transposition (i↔r), not two.
        # Standard Levenshtein would return 2.
        assert _damerau_levenshtein("stirpe", "stripe") == 1

    def test_empty_strings(self):
        assert _damerau_levenshtein("", "") == 0
        assert _damerau_levenshtein("abc", "") == 3
        assert _damerau_levenshtein("", "abc") == 3


class TestDetectLookalike:
    def _trusted(self):
        return ["stripe.com", "acme.com", "notion.so", "linear.app"]

    def test_homoglyph_digit_substitution(self):
        # str1pe.com → strlpe after canonicalisation, then edit distance 1
        # to stripe.com. The detector returns the closest trusted match.
        result = detect_lookalike("str1pe.com", self._trusted())
        assert result is not None
        assert result.suspected_impersonation == "stripe.com"

    def test_cyrillic_homoglyph(self):
        # Cyrillic 'а' (U+0430) → Latin 'a' after canonicalisation.
        # "аcme.com" then canonicalises exactly to "acme.com".
        cyrillic_acme = "\u0430" + "cme.com"
        result = detect_lookalike(cyrillic_acme, self._trusted())
        assert result is not None
        assert result.category == "homoglyph"
        assert result.suspected_impersonation == "acme.com"

    def test_tld_swap(self):
        result = detect_lookalike("stripe.co", self._trusted())
        assert result is not None
        assert result.category == "tld_swap"
        assert result.suspected_impersonation == "stripe.com"

    def test_edit_distance_plural(self):
        # stripes.com → stripe.com is edit distance 1 (insertion).
        result = detect_lookalike("stripes.com", self._trusted())
        assert result is not None
        assert result.suspected_impersonation == "stripe.com"
        # Could be classified as edit_distance (SLD differs by 1 char)
        assert result.category in {"edit_distance", "homoglyph"}

    def test_transposition(self):
        # stirpe.com ↔ stripe.com: one adjacent transposition.
        result = detect_lookalike("stirpe.com", self._trusted())
        assert result is not None
        assert result.suspected_impersonation == "stripe.com"
        assert result.score == 1

    def test_unrelated_domain_returns_none(self):
        result = detect_lookalike("microsoft.com", self._trusted())
        assert result is None

    def test_substring_not_flagged(self):
        # acme-ltd.com contains "acme" — that's the legitimate multi-brand
        # case, not an impersonation. The detector skips it via the
        # substring guard.
        result = detect_lookalike("acme-ltd.com", self._trusted())
        assert result is None

    def test_exact_match_returns_none(self):
        # If the sender IS in the trusted list, no lookalike — the
        # domain-lock service would have returned status=match upstream.
        result = detect_lookalike("stripe.com", self._trusted())
        assert result is None

    def test_empty_sender_returns_none(self):
        assert detect_lookalike("", self._trusted()) is None

    def test_empty_trusted_list_returns_none(self):
        assert detect_lookalike("str1pe.com", []) is None

    def test_sender_with_subdomain_matched_to_trusted_base(self):
        # "billing.str1pe.com" should match stripe.com too — we compare
        # registrable bases, not full domains.
        result = detect_lookalike("billing.str1pe.com", self._trusted())
        assert result is not None
        assert result.suspected_impersonation == "stripe.com"

    def test_length_gap_too_large_skipped(self):
        # acme.com vs verylongname.com — length gap far exceeds the
        # edit-distance ceiling, skipped without computing distance.
        result = detect_lookalike("verylongname.com", ["acme.com"])
        assert result is None


class TestCollectOrgTrustedDomains:
    def test_collects_deduplicated(self):
        # Use non-processor domains here so the PAYMENT_PROCESSOR_DOMAINS
        # exclusion doesn't interfere with the dedup test.
        db = MagicMock()
        db.list_vendor_profiles.return_value = [
            {"sender_domains": ["notion.so", "billing.notion.so"]},
            {"sender_domains": ["acme.com"]},
            {"sender_domains": ["notion.so"]},  # duplicate — deduped
        ]
        result = collect_org_trusted_domains(db, "default")
        # Registrable bases only, deduplicated.
        assert "notion.so" in result
        assert "acme.com" in result
        assert len(result) == len(set(result))

    def test_excludes_payment_processors(self):
        # stripe.com is in the payment-processor bypass set — when it
        # appears as a trusted domain, we exclude it from the lookalike
        # comparison set so routine processor traffic doesn't get
        # flagged everywhere.
        db = MagicMock()
        db.list_vendor_profiles.return_value = [
            {"sender_domains": ["stripe.com"]},  # processor — excluded
            {"sender_domains": ["acme.com"]},
        ]
        result = collect_org_trusted_domains(db, "default")
        assert "stripe.com" not in result
        assert "acme.com" in result

    def test_handles_json_string_sender_domains(self):
        # sender_domains may be stored as a JSON string on some rows
        # (legacy shape); decoder should handle both.
        db = MagicMock()
        db.list_vendor_profiles.return_value = [
            {"sender_domains": '["acme.com", "notion.so"]'},
        ]
        result = collect_org_trusted_domains(db, "default")
        assert "acme.com" in result
        assert "notion.so" in result

    def test_db_error_returns_empty(self):
        db = MagicMock()
        db.list_vendor_profiles.side_effect = RuntimeError("db down")
        result = collect_org_trusted_domains(db, "default")
        assert result == []
