"""
Fuzzy Matching Service for Solden v1

Provides intelligent matching beyond exact/tolerance comparisons:
- Vendor name fuzzy matching (handles variations)
- Transaction description similarity
- Reference ID partial matching
- Amount clustering for related transactions
"""
import logging
import unicodedata
from typing import Dict, List, Optional, Tuple
import re
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


def normalize_vendor(vendor: str) -> str:
    """
    Normalize vendor name for comparison.

    Examples:
        "STRIPE INC" -> "stripe"
        "Stripe.com" -> "stripe"
        "STRIPE PAYMENTS UK" -> "stripe payments uk"
        "Café Société" -> "cafe societe"         (diacritics stripped)
        "Acme Co.™" -> "acme co"                  (™ stripped like other punctuation)
        "Acme Co."  -> "acme co"                  (same bucket as above)

    The Unicode rules are load-bearing. Without them,
      - "é" (precomposed U+00E9) and "e + combining acute" (U+0065
        U+0301) landed in different buckets, so the same French
        vendor typed two different ways looked like two vendors.
      - "Acme Co.™" carried the trademark symbol through the regex
        because it's neither \\w nor \\s, but only AFTER earlier
        upstream code had mangled it; depending on the code path
        callsites saw "acme co" or "acme cotm". Now uniform.
    """
    if not vendor:
        return ""

    # 1. Strip symbol-category characters BEFORE NFKD so ™, ©, ® etc.
    #    don't get expanded into stray letters ("TM", "C", "R") that
    #    then leak into the normalized form. NFKD would otherwise turn
    #    "Acme Co.™" into "Acme Co.TM" which regex \\w preserves as
    #    "acme cotm" — different bucket than "Acme Co." → "acme co".
    pre_stripped = "".join(
        ch for ch in vendor
        if not unicodedata.category(ch).startswith("S")  # So/Sm/Sk/Sc
    )

    # 2. NFKD decomposes compatibility sequences: é -> e + combining
    #    acute, ﬁ -> fi, etc. Filter out combining marks (Mn) so
    #    diacritic variants collapse to the base letter. Precomposed
    #    "é" and decomposed "e + ́" now land on the same string.
    decomposed = unicodedata.normalize("NFKD", pre_stripped)
    stripped = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )

    # 3. Casefold (stronger than lower() — "ß" -> "ss", Turkish I).
    normalized = stripped.casefold().strip()

    # 4. Remove common suffixes (compares against casefolded strings).
    suffixes = [
        ' inc', ' inc.', ' llc', ' ltd', ' ltd.', ' limited',
        ' corp', ' corp.', ' corporation', ' co', ' co.',
        ' gmbh', ' ag', ' plc', ' pty', ' sa', ' nv', ' bv',
        '.com', '.io', '.co', '.org', '.net',
    ]
    for suffix in suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]

    # 5. Drop remaining non-word, non-space chars. Python's \w
    #    matches Unicode letters by default — but after NFKD strip
    #    above, anything that survives is already ASCII-friendly.
    normalized = re.sub(r"[^\w\s]", "", normalized)

    # 6. Collapse runs of whitespace.
    normalized = " ".join(normalized.split())

    return normalized


def vendor_similarity(vendor1: str, vendor2: str) -> float:
    """Calculate similarity between two vendor names (0.0 to 1.0).

    Sprint 3 upgrade: now hybrid-fused over five signals — exact /
    containment / Jaccard / SequenceMatcher / Levenshtein / trigram
    Jaccard — via :func:`vendor_similarity_hybrid`. The legacy API
    returns just the fused float; callers that want the per-mode
    breakdown (audit / debug / dashboards) should use
    :func:`vendor_similarity_modes`.

    Returns:
        float: Similarity score 0.0 to 1.0
    """
    fused, _ = vendor_similarity_hybrid(vendor1, vendor2)
    return fused


# ─── Sprint 3 multi-modal upgrade ──────────────────────────────────


def levenshtein_distance(a: str, b: str) -> int:
    """Iterative Levenshtein edit distance.

    Pure Python — no new dependencies. Used for typo-shaped variants
    like ``"ABC Logistic"`` vs ``"ABC Logistics"`` (1 edit) where
    Jaccard / SequenceMatcher under-score because the strings are
    near-identical at character level but Jaccard is token-shaped
    and SequenceMatcher is matching-block shaped.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    # Wagner-Fischer with a single rolling row.
    prev = list(range(len(b) + 1))
    curr = [0] * (len(b) + 1)
    for i, ch_a in enumerate(a, start=1):
        curr[0] = i
        for j, ch_b in enumerate(b, start=1):
            cost = 0 if ch_a == ch_b else 1
            curr[j] = min(
                prev[j] + 1,        # deletion
                curr[j - 1] + 1,    # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev, curr = curr, prev
    return prev[len(b)]


def levenshtein_ratio(a: str, b: str) -> float:
    """Levenshtein-based similarity in [0, 1].

    ``1 - distance / max(len)`` — 1.0 means identical, 0.0 means
    every character differs. Sensitive to length disparities (long
    vs short names always score low even if the short is fully
    contained), which is desirable for typo detection but not for
    abbreviation expansion. Containment and trigram cover the
    expansion cases.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    dist = levenshtein_distance(a, b)
    longest = max(len(a), len(b))
    return 1.0 - (dist / longest)


def _trigrams(s: str) -> set:
    """Character-level 3-grams with ``$`` boundary markers.

    Boundary markers make ``"abc"`` and ``"xabc"`` share more
    structure than the raw substring would suggest, and keep short
    strings (< 3 chars) from collapsing to the empty set.
    """
    if not s:
        return set()
    padded = f"$${s}$$"
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def trigram_jaccard(a: str, b: str) -> float:
    """Character n-gram Jaccard similarity in [0, 1].

    Robust to single-character typos (``"Logistics"`` vs
    ``"Logisitcs"`` shares most trigrams), partial abbreviation
    (``"International"`` vs ``"Intl"`` shares the leading boundary
    trigrams), and word-order shuffles. Complements Levenshtein
    (which over-penalizes word-order changes).
    """
    if not a and not b:
        return 1.0
    tg_a, tg_b = _trigrams(a), _trigrams(b)
    if not tg_a or not tg_b:
        return 0.0
    inter = len(tg_a & tg_b)
    union = len(tg_a | tg_b)
    return inter / union if union else 0.0


def vendor_similarity_modes(vendor1: str, vendor2: str) -> Dict[str, float]:
    """Compute every individual similarity signal between two vendor
    names. Returns a dict so callers can inspect components for
    audit (``why did the matcher fire?``) or feed individual signals
    into downstream ranking.

    Signals:

    * ``exact``        — 1.0 if normalized names match, else 0.0.
    * ``containment``  — ``shorter / longer * 0.95`` if one normalized
                         name contains the other, else 0.0.
    * ``jaccard``      — token-set Jaccard (word overlap) with a +0.2
                         boost when both first words match.
    * ``sequence``     — Ratcliff / Obershelp ratio
                         (``difflib.SequenceMatcher``) on normalized
                         strings.
    * ``levenshtein``  — edit-distance ratio. Strong for typos.
    * ``trigram``      — character 3-gram Jaccard. Strong for partial
                         abbreviations and small character drift.
    """
    if not vendor1 or not vendor2:
        return {
            "exact": 0.0, "containment": 0.0, "jaccard": 0.0,
            "sequence": 0.0, "levenshtein": 0.0, "trigram": 0.0,
        }

    norm1 = normalize_vendor(vendor1)
    norm2 = normalize_vendor(vendor2)

    if norm1 == norm2:
        return {
            "exact": 1.0, "containment": 1.0, "jaccard": 1.0,
            "sequence": 1.0, "levenshtein": 1.0, "trigram": 1.0,
        }

    # Containment.
    if norm1 in norm2 or norm2 in norm1:
        shorter = min(len(norm1), len(norm2))
        longer = max(len(norm1), len(norm2))
        containment = (shorter / longer) * 0.95 if longer else 0.0
    else:
        containment = 0.0

    # Token-set Jaccard with first-word boost.
    tokens1 = norm1.split()
    tokens2 = norm2.split()
    if tokens1 and tokens2:
        set1, set2 = set(tokens1), set(tokens2)
        intersection = set1 & set2
        union = set1 | set2
        jaccard = (len(intersection) / len(union)) if union else 0.0
        if tokens1[0] == tokens2[0]:
            jaccard = min(1.0, jaccard + 0.2)
    else:
        jaccard = 0.0

    sequence = SequenceMatcher(None, norm1, norm2).ratio()
    lev = levenshtein_ratio(norm1, norm2)
    tg = trigram_jaccard(norm1, norm2)

    return {
        "exact": 0.0,
        "containment": containment,
        "jaccard": jaccard,
        "sequence": sequence,
        "levenshtein": lev,
        "trigram": tg,
    }


def vendor_similarity_hybrid(
    vendor1: str,
    vendor2: str,
) -> Tuple[float, Dict[str, float]]:
    """Pairwise multi-modal vendor similarity + per-mode breakdown.

    Returns ``(fused_score, modes_dict)`` where ``fused_score`` is
    the maximum signal across modes — a single strong signal is
    enough to declare a candidate match for audit / debug purposes.

    **Critical context — read before tuning the fusion**:

    Pairwise multi-modal *fusion* at this layer is a structural
    dead-end. For two names, mode scores are absolute numbers with
    no reference frame: "containment 0.6 + lev 0.5 + trigram 0.5"
    looks like multi-mode agreement but is also exactly what
    unrelated-but-substring-overlapping pairs produce. Any
    agreement-based boost (noisy-OR, top-K mean, count-of-firing-
    modes) over-fires on the false-positive class. That isn't a
    tuning problem; it's a fundamental information limit of pairwise
    scoring.

    The honest multi-modal benefit lives in the **ranking layer** —
    see ``services/vendor_search.py:find_candidate_matches``, which
    runs each mode as an independent ranking over a corpus and
    fuses via Reciprocal Rank Fusion. RRF cancels false-positive
    correlations because partial-substring matches that score high
    pairwise also score high against unrelated queries (so they
    never reach top-K consistently across modes).

    What this function gives you:

    * Max-of-modes back-compat for legacy callers.
    * Per-mode breakdown for audit logging ("matched on trigram=
      0.92, lev=0.85") so operators can sanity-check why the
      matcher fired.

    What this function does NOT give you:

    * Production decision-making for the dedup pipeline. Use
      :func:`vendor_search.find_candidate_matches` (RRF over a
      candidate set) for that, or
      :func:`detect_duplicates_via_rrf` for the corpus-wide
      clustering pass.

    A **calibrated** pairwise probability score is in
    :func:`match_probability` — it's heuristically tuned (we don't
    have labeled vendor-merge data yet) so should drive UI
    confidence labels, not production gating. Refine with labeled
    data from the ``vendor_invoice_history`` post-merge trail when
    available.
    """
    modes = vendor_similarity_modes(vendor1, vendor2)
    if modes["exact"] >= 1.0:
        return 1.0, modes

    # Max of every mode except ``exact`` (which is binary and
    # already covered by the short-circuit above). See the docstring
    # above — this is back-compat / audit, not the production fusion
    # layer.
    fused = max(score for mode, score in modes.items() if mode != "exact")
    fused = max(0.0, min(1.0, fused))
    return fused, modes


# ─── Calibrated pairwise match probability ─────────────────────────


# Per-mode log-likelihood-ratio coefficients + a base prior favoring
# non-match (most pairs from a real corpus are NOT duplicates of each
# other). Each mode is treated as evidence for/against a "true match"
# hypothesis; modes only contribute when their score crosses a noise
# floor so silent modes don't drag log-odds either way.
#
# Discriminative ordering (highest precision first):
#
# * ``exact`` — binary; if scores 1.0, overwhelming evidence.
# * ``levenshtein`` — high gain. A 0.85+ lev requires near-identity
#   at character level, which is the canonical typo signature. Low
#   false-positive rate on length-matched strings.
# * ``trigram`` — high gain. N-gram overlap requires substantial
#   shared character structure; low false-positive rate.
# * ``sequence`` — moderate gain. SequenceMatcher over-fires on
#   common substring patterns ("Inc", "LLC", years).
# * ``containment`` — moderate gain. Substring overlap is the
#   classic partial-substring false-positive trap (``"Stripe"`` is
#   in ``"Striperock"`` but they're unrelated).
# * ``jaccard`` — lowest gain. One shared common word
#   (``"Services"``, ``"Group"``) inflates the score on unrelated.
#
# Coefficients are first-principles estimates, NOT regression-fit on
# labeled data. They produce stable RELATIVE ORDERING of match
# strength (true match > partial substring > unrelated) but absolute
# probabilities can drift ±0.15 from a calibrated truth depending on
# the corpus distribution. Refine when ``vendor_invoice_history``
# post-merge labels are available.
_BASE_PRIOR_LOG_ODDS = -1.5  # ~0.18 base probability of match
_NOISE_FLOOR = 0.1           # modes below this don't contribute
_LOG_LR_COEFS = {
    # (gain, intercept, max_pos)
    "exact":       (4.0,  -2.0, 4.0),
    "levenshtein": (5.5,  -3.0, 3.0),
    "trigram":     (5.0,  -2.0, 3.0),
    "sequence":    (2.5,  -1.5, 2.5),
    "containment": (3.0,  -1.5, 2.5),
    "jaccard":     (2.0,  -1.0, 2.0),
}


def _log_lr(mode: str, score: float) -> float:
    """Per-mode log-likelihood-ratio. Linear in score above the noise
    floor; modes that fire weakly contribute zero (NOT negative)
    because weak evidence shouldn't actively argue *against* a match
    — it just doesn't help. Strong evidence contributes positively
    up to ``max_pos``.

    The asymmetric clamping (zero floor, positive max) is the
    principled choice in absence of labeled data: false-positive
    rates and base rates would calibrate the negative side, but we
    don't have them. Without that calibration, treating weak signals
    as neutral rather than negative avoids over-penalizing genuinely
    related pairs (e.g., entity siblings whose names share a common
    token but otherwise differ).
    """
    if score < _NOISE_FLOOR:
        return 0.0
    if mode not in _LOG_LR_COEFS:
        return 0.0
    gain, intercept, max_pos = _LOG_LR_COEFS[mode]
    raw = gain * score + intercept
    return max(0.0, min(max_pos, raw))


def match_probability(modes: Dict[str, float]) -> float:
    """Heuristic pairwise match probability in [0, 1].

    Sums per-mode log-LRs and converts to a probability via sigmoid.
    Each mode's coefficient encodes its empirical false-positive
    sensitivity (containment + trigram are higher-precision, jaccard
    lower); the sum across modes is the log-odds.

    **Uncalibrated** — coefficients are first-principles estimates,
    not regression-fit on labeled data. Use for:

    * UI confidence labels ("strong match", "needs review")
    * Tie-breaking in top-K search results
    * Audit trail ("agent decided based on a 0.83 confidence match")

    Do NOT use for hard production gating — the absolute probability
    can drift up to ±0.15 from a calibrated truth depending on the
    name distribution. For gating, use the RRF rank from
    :func:`vendor_search.find_candidate_matches` (relative ranking
    is more robust than absolute probability).

    Calibration path: when ``vendor_invoice_history`` accumulates
    enough post-merge labels, fit a logistic regression on (modes ->
    merged_yes_no) and replace the coefficients in
    ``_LOG_LR_COEFS`` with the regression weights.
    """
    if not modes:
        return 0.0
    if modes.get("exact", 0.0) >= 1.0:
        return 1.0
    # Start from the base prior (most random pairs aren't duplicates).
    # Modes contribute log-LR only when above the noise floor — silent
    # modes leave the prior unchanged rather than dragging it down.
    log_odds = _BASE_PRIOR_LOG_ODDS + sum(
        _log_lr(mode, score) for mode, score in modes.items()
    )
    # Sigmoid; clip exponent to avoid overflow on extreme inputs.
    import math
    clipped = max(-30.0, min(30.0, log_odds))
    return 1.0 / (1.0 + math.exp(-clipped))


def fuzzy_match_vendors(
    source_vendor: str,
    candidates: List[Dict],
    vendor_field: str = "vendor",
    threshold: float = 0.7
) -> List[Tuple[Dict, float]]:
    """
    Find candidates with similar vendor names.
    
    Args:
        source_vendor: Vendor to match
        candidates: List of candidate transactions
        vendor_field: Field name containing vendor in candidates
        threshold: Minimum similarity threshold
    
    Returns:
        List of (candidate, similarity_score) tuples, sorted by score
    """
    matches = []
    
    for candidate in candidates:
        candidate_vendor = candidate.get(vendor_field, "") or candidate.get("description", "")
        similarity = vendor_similarity(source_vendor, candidate_vendor)
        
        if similarity >= threshold:
            matches.append((candidate, similarity))
    
    # Sort by similarity descending
    matches.sort(key=lambda x: x[1], reverse=True)
    
    return matches


def reference_id_similarity(ref1: str, ref2: str) -> float:
    """
    Calculate similarity between reference IDs.
    Handles partial matches, prefix/suffix variations.
    
    Examples:
        "INV-2024-001" vs "2024-001" -> 0.8
        "TXN12345" vs "12345" -> 0.7
    """
    if not ref1 or not ref2:
        return 0.0
    
    # Normalize
    clean1 = re.sub(r'[^A-Za-z0-9]', '', ref1.upper())
    clean2 = re.sub(r'[^A-Za-z0-9]', '', ref2.upper())
    
    # Exact match
    if clean1 == clean2:
        return 1.0
    
    # One contains the other
    if clean1 in clean2:
        return len(clean1) / len(clean2) * 0.9
    if clean2 in clean1:
        return len(clean2) / len(clean1) * 0.9
    
    # Extract numeric portions
    nums1 = re.findall(r'\d+', clean1)
    nums2 = re.findall(r'\d+', clean2)
    
    if nums1 and nums2:
        # Compare longest numeric sequences
        longest1 = max(nums1, key=len)
        longest2 = max(nums2, key=len)
        
        if longest1 == longest2:
            return 0.85
        if longest1 in longest2 or longest2 in longest1:
            return 0.7
    
    # Sequence matching
    return SequenceMatcher(None, clean1, clean2).ratio()


def amount_cluster_match(
    amount: float,
    candidates: List[Dict],
    amount_field: str = "amount",
    tolerance_pct: float = 0.5,
    include_related: bool = True
) -> List[Tuple[Dict, str, float]]:
    """
    Find candidates with matching or related amounts.
    
    Handles:
    - Exact matches
    - Within tolerance
    - Split transactions (amount is sum of multiple)
    - Partial payments
    
    Returns:
        List of (candidate, match_type, score) tuples
    """
    if not amount or amount == 0:
        return []
    
    matches = []
    tolerance = abs(amount) * (tolerance_pct / 100)
    
    for candidate in candidates:
        cand_amount = candidate.get(amount_field, 0) or 0
        if not cand_amount:
            continue
        
        diff = abs(amount - cand_amount)
        
        if diff == 0:
            # Exact match
            matches.append((candidate, "exact", 1.0))
        elif diff <= tolerance:
            # Within tolerance
            score = 1.0 - (diff / tolerance) * 0.1
            matches.append((candidate, "tolerance", score))
        elif include_related:
            # Check for round number relationships
            ratio = amount / cand_amount if cand_amount != 0 else 0
            
            # Check if one is multiple of other (split/combined transactions)
            if 0.9 <= ratio <= 1.1:
                matches.append((candidate, "approximate", 0.8))
            elif 1.9 <= ratio <= 2.1:
                matches.append((candidate, "double", 0.6))
            elif 0.45 <= ratio <= 0.55:
                matches.append((candidate, "half", 0.6))
    
    # Sort by score
    matches.sort(key=lambda x: x[2], reverse=True)
    
    return matches


def smart_match_score(
    source: Dict,
    candidate: Dict,
    config: Dict = None
) -> Tuple[float, List[Dict]]:
    """
    Calculate comprehensive match score using multiple factors.
    
    Args:
        source: Source transaction
        candidate: Candidate transaction to compare
        config: Optional config with weights
    
    Returns:
        (total_score, reasoning_steps)
    """
    config = config or {}
    
    # Default weights
    weights = {
        "amount": config.get("weight_amount", 0.35),
        "date": config.get("weight_date", 0.25),
        "vendor": config.get("weight_vendor", 0.25),
        "reference": config.get("weight_reference", 0.15)
    }
    
    reasoning = []
    total_score = 0.0
    
    # Amount matching
    source_amount = source.get("amount") or source.get("net_amount") or 0
    cand_amount = candidate.get("amount") or candidate.get("net_amount") or 0
    
    if source_amount and cand_amount:
        amount_matches = amount_cluster_match(source_amount, [candidate])
        if amount_matches:
            match_type, score = amount_matches[0][1], amount_matches[0][2]
            total_score += score * weights["amount"]
            reasoning.append({
                "factor": "Amount",
                "observation": f"${source_amount:,.2f} vs ${cand_amount:,.2f} ({match_type})",
                "impact": "positive" if score >= 0.8 else "neutral",
                "score": score
            })
        else:
            reasoning.append({
                "factor": "Amount",
                "observation": f"${source_amount:,.2f} vs ${cand_amount:,.2f} (no match)",
                "impact": "negative",
                "score": 0
            })
    
    # Date matching
    source_date = source.get("date")
    cand_date = candidate.get("date")
    
    if source_date and cand_date:
        try:
            from datetime import datetime
            if isinstance(source_date, str):
                sd = datetime.strptime(source_date[:10], "%Y-%m-%d")
            else:
                sd = source_date
            if isinstance(cand_date, str):
                cd = datetime.strptime(cand_date[:10], "%Y-%m-%d")
            else:
                cd = cand_date
            
            days_diff = abs((sd - cd).days)
            date_window = config.get("date_window_days", 3)
            
            if days_diff == 0:
                date_score = 1.0
                date_obs = "Same date"
            elif days_diff <= date_window:
                date_score = 1.0 - (days_diff / (date_window * 2))
                date_obs = f"{days_diff} day(s) apart"
            else:
                date_score = 0.0
                date_obs = f"{days_diff} days apart (outside window)"
            
            total_score += date_score * weights["date"]
            reasoning.append({
                "factor": "Date",
                "observation": date_obs,
                "impact": "positive" if date_score >= 0.5 else "negative",
                "score": date_score
            })
        except (ValueError, TypeError) as date_exc:
            logger.debug("Date comparison failed: %s", date_exc)
            reasoning.append({
                "factor": "Date",
                "observation": f"Date parse error: {date_exc}",
                "impact": "neutral",
                "score": 0.0,
            })

    # Vendor matching
    source_vendor = source.get("vendor") or source.get("description") or ""
    cand_vendor = candidate.get("vendor") or candidate.get("description") or ""
    
    if source_vendor and cand_vendor:
        vendor_score = vendor_similarity(source_vendor, cand_vendor)
        total_score += vendor_score * weights["vendor"]
        
        reasoning.append({
            "factor": "Vendor",
            "observation": f"'{source_vendor}' vs '{cand_vendor}' ({vendor_score*100:.0f}% similar)",
            "impact": "positive" if vendor_score >= 0.7 else "neutral" if vendor_score >= 0.4 else "negative",
            "score": vendor_score
        })
    
    # Reference ID matching
    source_ref = source.get("txn_id") or source.get("reference") or source.get("invoice_number") or ""
    cand_ref = candidate.get("txn_id") or candidate.get("bank_txn_id") or candidate.get("internal_id") or ""
    
    if source_ref and cand_ref:
        ref_score = reference_id_similarity(source_ref, cand_ref)
        if ref_score > 0.5:
            total_score += ref_score * weights["reference"]
            reasoning.append({
                "factor": "Reference",
                "observation": f"'{source_ref}' vs '{cand_ref}' ({ref_score*100:.0f}% similar)",
                "impact": "positive" if ref_score >= 0.7 else "neutral",
                "score": ref_score
            })
    
    total_score = min(1.0, max(0.0, total_score))
    return total_score, reasoning


def find_best_matches(
    source: Dict,
    candidates: List[Dict],
    config: Dict = None,
    top_n: int = 5,
    min_score: float = 0.3
) -> List[Tuple[Dict, float, List[Dict]]]:
    """
    Find best matching candidates for a source transaction.
    
    Returns:
        List of (candidate, score, reasoning) tuples, sorted by score
    """
    results = []
    
    for candidate in candidates:
        score, reasoning = smart_match_score(source, candidate, config)
        
        if score >= min_score:
            results.append((candidate, score, reasoning))
    
    # Sort by score descending
    results.sort(key=lambda x: x[1], reverse=True)
    
    return results[:top_n]

