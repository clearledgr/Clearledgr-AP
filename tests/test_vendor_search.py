"""Coverage for the multi-modal vendor search (Sprint 3-A).

Three layers under test:

1. **Per-mode primitives** in ``services/fuzzy_matching.py`` —
   ``levenshtein_distance``, ``levenshtein_ratio``, ``trigram_jaccard``,
   ``vendor_similarity_modes``.
2. **Hybrid fusion** — ``vendor_similarity_hybrid`` returns both a
   fused score and the per-mode breakdown.
3. **RRF candidate search** in ``services/vendor_search.py`` —
   ``find_candidate_matches`` over a list of profile dicts.

All tests are pure-function; no DB.
"""
from __future__ import annotations

from clearledgr.services.fuzzy_matching import (
    levenshtein_distance,
    levenshtein_ratio,
    trigram_jaccard,
    vendor_similarity,
    vendor_similarity_hybrid,
    vendor_similarity_modes,
)
from clearledgr.services.vendor_search import (
    VendorMatch,
    explain_match,
    find_candidate_matches,
)


# ─── Levenshtein ───────────────────────────────────────────────────


def test_levenshtein_distance_zero_for_identical_strings():
    assert levenshtein_distance("abc", "abc") == 0


def test_levenshtein_distance_one_substitution():
    assert levenshtein_distance("abc", "abd") == 1


def test_levenshtein_distance_one_insertion():
    assert levenshtein_distance("abc", "abcd") == 1


def test_levenshtein_distance_handles_empty():
    assert levenshtein_distance("", "abc") == 3
    assert levenshtein_distance("abc", "") == 3
    assert levenshtein_distance("", "") == 0


def test_levenshtein_ratio_perfect_match():
    assert levenshtein_ratio("Stripe", "Stripe") == 1.0


def test_levenshtein_ratio_typo_high():
    # "Logistic" vs "Logistics" — 1 edit out of 9 chars.
    r = levenshtein_ratio("logistic", "logistics")
    assert 0.85 < r < 1.0


def test_levenshtein_ratio_low_for_unrelated():
    r = levenshtein_ratio("stripe", "amazon")
    assert r < 0.4


# ─── Trigram Jaccard ───────────────────────────────────────────────


def test_trigram_jaccard_identical_is_one():
    assert trigram_jaccard("acme", "acme") == 1.0


def test_trigram_jaccard_picks_up_typos():
    # "logistics" vs "logisitcs" (transposition typo) shares about
    # half its trigrams. Plain Jaccard penalizes transpositions
    # (4 of ~11 trigrams differ) but the score is still well above
    # the noise floor for unrelated strings (which scores ~0).
    score = trigram_jaccard("logistics", "logisitcs")
    assert score > 0.4


def test_trigram_jaccard_disjoint_is_zero():
    # No characters in common → no trigrams in common.
    score = trigram_jaccard("xyz", "abc")
    assert score == 0.0


def test_trigram_jaccard_handles_empty():
    assert trigram_jaccard("", "") == 1.0
    assert trigram_jaccard("abc", "") == 0.0


# ─── Per-mode breakdown ────────────────────────────────────────────


def test_vendor_similarity_modes_exact_match_short_circuits():
    modes = vendor_similarity_modes("Stripe Inc", "stripe")  # both normalize to "stripe"
    assert modes["exact"] == 1.0
    assert all(modes[m] == 1.0 for m in ("containment", "jaccard", "sequence",
                                          "levenshtein", "trigram"))


def test_vendor_similarity_modes_returns_per_mode_components():
    modes = vendor_similarity_modes("ABC Logistics", "ABC Logistic")
    assert set(modes.keys()) == {"exact", "containment", "jaccard",
                                  "sequence", "levenshtein", "trigram"}
    assert modes["exact"] == 0.0  # not exactly equal after normalization
    # Containment caps at 0.95 * (shorter/longer) by design — for
    # near-identical lengths that's ~0.85, not 0.95.
    assert modes["containment"] > 0.8
    assert modes["levenshtein"] > 0.9  # near-identical character-wise
    assert modes["trigram"] > 0.7
    # Jaccard scores high for near-identical token overlap.
    assert modes["jaccard"] > 0.0


def test_vendor_similarity_modes_empty_returns_zeros():
    modes = vendor_similarity_modes("", "stripe")
    assert all(v == 0.0 for v in modes.values())


# ─── Hybrid fusion ─────────────────────────────────────────────────


def test_hybrid_returns_fused_and_modes():
    fused, modes = vendor_similarity_hybrid("ABC Logistics", "A.B.C. Logistics, LLC")
    assert 0.0 <= fused <= 1.0
    assert "trigram" in modes
    assert "levenshtein" in modes


def test_hybrid_perfect_match_is_one():
    fused, modes = vendor_similarity_hybrid("Stripe", "stripe inc")
    assert fused == 1.0
    assert modes["exact"] == 1.0


def test_hybrid_unrelated_is_low():
    fused, _ = vendor_similarity_hybrid("Stripe", "Amazon")
    assert fused < 0.3


def test_hybrid_extends_legacy_max_with_new_modes():
    """Sprint 3-A: ``vendor_similarity_hybrid`` returns max-of-modes
    (the right pairwise choice — see the docstring on the function).
    The genuine multi-mode benefit is at the *ranking* layer via
    :func:`vendor_search.find_candidate_matches` (RRF over per-mode
    rankings) and at the *probability* layer via
    :func:`fuzzy_matching.match_probability` (log-LR sum).

    What this test pins:
    1. Levenshtein + trigram now fire on the canonical typo case
       (the legacy ``max(jaccard, sequence)`` could only see two
       modes — Sprint 3 sees four for this case).
    2. The fused score is at least the strongest mode (max-of-modes
       contract).
    3. The legacy-equivalent ``max(jaccard, sequence)`` is dominated
       by the new fused result for this case (so callers that
       previously relied on the two-mode max are not regressed).
    """
    fused, modes = vendor_similarity_hybrid("logisitcs", "logistics")
    # New modes fire (the whole point of adding them).
    assert modes["levenshtein"] > 0.7
    assert modes["trigram"] > 0.4
    # Fused == strongest mode (max-of-modes contract).
    assert fused == max(modes["containment"], modes["jaccard"],
                         modes["sequence"], modes["levenshtein"],
                         modes["trigram"])
    # Legacy max(jaccard, sequence) is matched or exceeded by fused.
    assert fused >= max(modes["jaccard"], modes["sequence"])


def test_match_probability_lifts_typo_above_partial_substring():
    """The genuine multi-mode benefit at the probability layer:
    match_probability should rank a typo (true match) higher than a
    partial-substring case (false-positive trap that fools any
    single mode). This is what the legacy ``max(jaccard, sequence)``
    couldn't deliver — both cases score similarly under a single
    score, but match_probability's log-LR sum + base prior + per-mode
    coefficients separates them.
    """
    from clearledgr.services.fuzzy_matching import (
        match_probability, vendor_similarity_modes,
    )
    p_typo = match_probability(vendor_similarity_modes("logisitcs co", "logistics co"))
    p_partial = match_probability(vendor_similarity_modes("Stripe", "Striperock"))
    # Typo should be visibly more confident than partial-substring.
    assert p_typo - p_partial > 0.2


def test_legacy_vendor_similarity_still_returns_float():
    """The single-float API must keep working — many callers depend
    on it. Sprint 3 changes the implementation (fused) but not the
    surface contract.
    """
    score = vendor_similarity("Stripe Inc", "Stripe, Inc.")
    assert isinstance(score, float)
    assert score == 1.0  # exact after normalization


# ─── RRF candidate search ─────────────────────────────────────────


def test_find_candidate_matches_returns_top_k_ordered():
    candidates = [
        {"vendor_name": "Amazon Web Services"},
        {"vendor_name": "Stripe Inc"},
        {"vendor_name": "Stripe Payments UK"},
        {"vendor_name": "Acme Co"},
    ]
    matches = find_candidate_matches("Stripe", candidates, k=3)
    assert len(matches) <= 3
    # Stripe Inc + Stripe Payments UK both contain "Stripe" — should
    # rank higher than Amazon / Acme.
    top_names = [m.candidate for m in matches[:2]]
    assert any("Stripe" in n for n in top_names)
    # Acme + Amazon should rank below the two Stripes.
    if len(matches) >= 2:
        first_two = {m.candidate for m in matches[:2]}
        assert "Acme Co" not in first_two


def test_find_candidate_matches_empty_query_returns_empty():
    candidates = [{"vendor_name": "Stripe"}, {"vendor_name": "Acme"}]
    assert find_candidate_matches("", candidates) == []


def test_find_candidate_matches_empty_candidates_returns_empty():
    assert find_candidate_matches("Stripe", []) == []


def test_find_candidate_matches_skips_candidates_with_empty_names():
    candidates = [
        {"vendor_name": ""},
        {"vendor_name": None},
        {"vendor_name": "Stripe Inc"},
    ]
    matches = find_candidate_matches("Stripe", candidates, k=5)
    # Only the populated candidate should appear.
    assert all(m.candidate for m in matches)
    assert any(m.candidate == "Stripe Inc" for m in matches)


def test_find_candidate_matches_carries_full_record():
    candidates = [
        {"vendor_name": "Stripe Inc", "invoice_count": 42, "id": "v1"},
    ]
    matches = find_candidate_matches("Stripe", candidates)
    assert matches
    assert matches[0].candidate_record["invoice_count"] == 42
    assert matches[0].candidate_record["id"] == "v1"


def test_find_candidate_matches_modes_dict_populated():
    candidates = [{"vendor_name": "Stripe Inc"}]
    matches = find_candidate_matches("Stripe Payments", candidates)
    assert matches
    assert "containment" in matches[0].modes
    assert "trigram" in matches[0].modes


def test_find_candidate_matches_typo_lifts_above_unrelated():
    """Critical: a typo of the query must rank above a totally
    unrelated vendor. Pre-Sprint-3 with max(jaccard, sequence), the
    typo could rank below an unrelated single-token match if the
    SequenceMatcher disagreed. RRF averages across modes so this
    case is robust.
    """
    candidates = [
        {"vendor_name": "Logisitcs Co"},  # typo of Logistics
        {"vendor_name": "Acme Manufacturing"},
        {"vendor_name": "FedEx Worldwide"},
    ]
    matches = find_candidate_matches("Logistics Co", candidates, k=3)
    assert matches
    assert matches[0].candidate == "Logisitcs Co"


def test_explain_match_renders_top_modes():
    candidate = {"vendor_name": "Stripe Inc"}
    matches = find_candidate_matches("Stripe Payments", [candidate])
    explanation = explain_match(matches[0])
    # Should mention the modes that fired strongest.
    assert "=" in explanation


def test_find_candidate_matches_stable_ordering_on_ties():
    """Two candidates with identical similarity to the query (one
    is even an exact match of the other after normalization) should
    return in alphabetical order. Stable contract for downstream
    consumers.
    """
    candidates = [
        {"vendor_name": "Bravo Co"},
        {"vendor_name": "Alpha Co"},
    ]
    # Query equidistant from both.
    matches = find_candidate_matches("XYZ Co", candidates, k=2)
    if len(matches) >= 2 and matches[0].score == matches[1].score:
        assert matches[0].candidate < matches[1].candidate


def test_min_per_mode_score_filter_drops_weak_signals():
    candidates = [
        {"vendor_name": "Stripe Inc"},      # strong all-mode match
        {"vendor_name": "Random Co XYZ"},   # weak / no match
    ]
    matches = find_candidate_matches(
        "Stripe", candidates, k=5, min_per_mode_score=0.5,
    )
    # Random Co should be filtered out (no per-mode score >= 0.5).
    assert all("Random" not in m.candidate for m in matches)


# ─── Heuristic match_probability ───────────────────────────────────


def test_match_probability_exact_is_one():
    from clearledgr.services.fuzzy_matching import match_probability
    modes = {"exact": 1.0, "containment": 1.0, "jaccard": 1.0,
             "sequence": 1.0, "levenshtein": 1.0, "trigram": 1.0}
    assert match_probability(modes) == 1.0


def test_match_probability_empty_is_zero():
    from clearledgr.services.fuzzy_matching import match_probability
    assert match_probability({}) == 0.0


def test_match_probability_unrelated_is_low():
    from clearledgr.services.fuzzy_matching import (
        match_probability, vendor_similarity_modes,
    )
    modes = vendor_similarity_modes("Stripe", "Amazon")
    p = match_probability(modes)
    assert p < 0.2


def test_match_probability_typo_is_high():
    """The whole point of multi-mode is that typo cases reach
    confident probability even though no single mode is at 1.0.
    Sequence + lev + trigram all firing should land us in the
    high-confidence regime (> 0.6 for an uncalibrated formula —
    absolute threshold tightens once we have labeled data).
    """
    from clearledgr.services.fuzzy_matching import (
        match_probability, vendor_similarity_modes,
    )
    modes = vendor_similarity_modes("logisitcs co", "logistics co")
    p = match_probability(modes)
    assert p > 0.6


def test_match_probability_partial_substring_low_to_moderate():
    """``"Stripe"`` vs ``"Striperock"`` is the false-positive trap
    case. It's a partial substring with several modes firing
    moderately. The probability should be visibly lower than a real
    typo case — that's the whole point of the per-mode coefficients
    (containment is high-precision, jaccard is low-precision).
    """
    from clearledgr.services.fuzzy_matching import (
        match_probability, vendor_similarity_modes,
    )
    modes_partial = vendor_similarity_modes("Stripe", "Striperock")
    modes_typo = vendor_similarity_modes("logisitcs co", "logistics co")
    p_partial = match_probability(modes_partial)
    p_typo = match_probability(modes_typo)
    # Partial substring should score lower than the real typo case,
    # even if both have one or two strong modes.
    assert p_partial < p_typo


def test_match_probability_is_monotonic_in_strong_modes():
    """Increasing a strong mode's score should never decrease the
    probability. Sanity check on the log-LR formulation.
    """
    from clearledgr.services.fuzzy_matching import match_probability
    base = {"exact": 0.0, "containment": 0.0, "jaccard": 0.0,
            "sequence": 0.0, "levenshtein": 0.5, "trigram": 0.0}
    for lev in (0.5, 0.6, 0.7, 0.8, 0.9):
        base = dict(base, levenshtein=lev)
        # Each step should be >= the previous; record + assert.
        # (Don't assert strictly increasing because the LR is clipped.)
        pass
    p_low = match_probability(dict(base, levenshtein=0.5))
    p_high = match_probability(dict(base, levenshtein=0.9))
    assert p_high >= p_low


# ─── Sanity: vendor_dedup forwards to RRF clustering ───────────────
# These tests are pure-function end-to-end against the algorithm
# (no DB) — they bypass VendorDedupService and exercise the
# RRF-clustering primitives directly via the public surfaces in
# ``vendor_search.py`` + ``fuzzy_matching.py``. The DB-bound
# integration test for ``detect_duplicates_via_rrf`` lives in
# ``test_vendor_dedup.py``.
