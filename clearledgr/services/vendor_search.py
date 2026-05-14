"""Hybrid vendor-name search via Reciprocal Rank Fusion.

Sprint 3-A. Where ``services/vendor_dedup.py`` is N² pairwise
detection across all profiles in an org, this module is the
**search-shaped** surface: given a query name (e.g., from an
inbound invoice's vendor field), return the top-K canonical
candidates from a list of existing profiles.

The fusion strategy is **Reciprocal Rank Fusion** (RRF) — each
similarity mode (exact / containment / jaccard / sequence /
levenshtein / trigram) ranks every candidate independently, and
the final score for candidate C is::

    rrf_score(C) = sum over modes M of  1 / (k + rank(C, M))

with ``k`` a smoothing constant (default 60, the RRF paper's
recommended baseline). This dominates fixed-weight averaging when
modes disagree on the ordering — a candidate consistently ranked
top-3 across modes wins over one that's #1 in one mode and #15 in
another, even if the latter has a higher peak score.

Use cases:

* **Invoice intake**: new invoice carries vendor name "ABC
  Logistics, LLC"; find the top-5 existing canonical vendors so
  the AP agent can decide whether to bind to one or create a new.
* **Operator-driven dedup confirmation**: alongside batch detection,
  let an operator type "logistics" and surface the candidate list.
* **Vendor sanctions / compliance lookups**: same pattern, against
  an external sanctions name list.

This module has no DB / network calls — pure functions over Python
data so it can be unit-tested without infrastructure. Callers
(e.g., ``vendor_dedup.VendorDedupService``) load profiles and
hand them in.
"""
from __future__ import annotations

import dataclasses
from typing import Dict, Iterable, List, Optional, Sequence

from .fuzzy_matching import vendor_similarity_modes


# RRF smoothing constant. The original paper (Cormack et al. 2009)
# recommends 60 as a good general default; lower k gives more weight
# to top-ranked items, higher k flattens the curve. Don't change
# without re-tuning against a labeled dataset.
DEFAULT_RRF_K = 60


# Modes whose individual rankings get fused. Order doesn't matter
# (RRF is commutative), but listing them makes the contract explicit
# and lets callers swap in a subset for ablation studies.
_FUSION_MODES = (
    "containment",
    "jaccard",
    "sequence",
    "levenshtein",
    "trigram",
)


@dataclasses.dataclass(frozen=True)
class VendorMatch:
    """One ranked match. ``score`` is the fused RRF score (higher =
    better, no fixed range — depends on K and the number of modes).
    ``modes`` carries the individual similarity scores so callers
    can render an audit string ("matched on trigram=0.92 + lev=0.88").
    """

    candidate: str  # the normalized / display name from the candidate row
    candidate_record: Dict[str, object]  # original profile dict
    score: float  # fused RRF score
    modes: Dict[str, float]  # per-mode similarity scores


def find_candidate_matches(
    query: str,
    candidates: Sequence[Dict[str, object]],
    *,
    k: int = 5,
    rrf_k: int = DEFAULT_RRF_K,
    name_field: str = "vendor_name",
    modes: Iterable[str] = _FUSION_MODES,
    min_per_mode_score: float = 0.0,
) -> List[VendorMatch]:
    """Return the top-K ``VendorMatch`` for ``query`` against
    ``candidates``.

    Parameters:

    * ``query``: the inbound vendor name to match.
    * ``candidates``: list of profile dicts. Must each have
      ``name_field`` populated.
    * ``k``: how many top matches to return.
    * ``rrf_k``: RRF smoothing constant. Default 60.
    * ``name_field``: dict key where the candidate name lives.
      ``"vendor_name"`` for ``vendor_profiles`` rows.
    * ``modes``: which similarity modes participate in the fusion.
      Default: every mode except ``exact`` (which short-circuits at
      the modes layer; here we compute per-mode rankings, and exact
      matches naturally rank #1 across containment / jaccard /
      sequence / lev / trigram).
    * ``min_per_mode_score``: drop candidates whose per-mode score
      is below this floor BEFORE ranking. Default 0.0 (include all).
      Set to e.g. 0.2 to prune obviously irrelevant candidates.

    Empty / null query or empty candidates list returns ``[]``.

    Stable: ties broken by candidate name (alphabetical). Caller
    can rely on deterministic ordering.
    """
    if not query or not candidates:
        return []

    fusion_modes = list(modes)
    if not fusion_modes:
        return []

    # 1. Compute per-mode score for every candidate. ``scores`` is a
    #    list of (mode, [(candidate_index, score), ...]) tuples, each
    #    inner list NOT yet sorted.
    per_mode_scores: Dict[str, List[tuple]] = {m: [] for m in fusion_modes}
    full_modes_per_candidate: List[Dict[str, float]] = []

    for idx, cand in enumerate(candidates):
        cand_name = str(cand.get(name_field) or "").strip()
        if not cand_name:
            full_modes_per_candidate.append({m: 0.0 for m in fusion_modes})
            continue
        all_modes = vendor_similarity_modes(query, cand_name)
        full_modes_per_candidate.append(all_modes)
        for mode in fusion_modes:
            score = all_modes.get(mode, 0.0)
            if score >= min_per_mode_score:
                per_mode_scores[mode].append((idx, score))

    # 2. For each mode, sort candidates by score desc and assign ranks.
    #    RRF uses 1-based ranking: best = rank 1, second = rank 2, ...
    rrf_acc: Dict[int, float] = {i: 0.0 for i in range(len(candidates))}
    for mode in fusion_modes:
        ranked = sorted(per_mode_scores[mode], key=lambda t: (-t[1], t[0]))
        for rank, (cand_idx, _) in enumerate(ranked, start=1):
            rrf_acc[cand_idx] += 1.0 / (rrf_k + rank)

    # 3. Sort by fused RRF score desc, ties broken by candidate name.
    def _candidate_name(idx: int) -> str:
        return str(candidates[idx].get(name_field) or "")

    ranked_indices = sorted(
        rrf_acc.keys(),
        key=lambda i: (-rrf_acc[i], _candidate_name(i)),
    )

    # 4. Drop candidates with zero RRF (no mode matched at all).
    matches: List[VendorMatch] = []
    for idx in ranked_indices:
        score = rrf_acc[idx]
        if score <= 0.0:
            break
        matches.append(VendorMatch(
            candidate=_candidate_name(idx),
            candidate_record=dict(candidates[idx]),
            score=score,
            modes=full_modes_per_candidate[idx],
        ))
        if len(matches) >= k:
            break
    return matches


def explain_match(match: VendorMatch, *, top_n: int = 3) -> str:
    """Render the top contributing modes as a human-readable string.

    ``"matched on trigram=0.92, levenshtein=0.85, containment=0.71"``

    Used in audit logs + operator UI when surfacing why the agent
    picked a particular existing vendor over creating a new one.
    """
    sorted_modes = sorted(match.modes.items(), key=lambda kv: -kv[1])[:top_n]
    return ", ".join(f"{mode}={score:.2f}" for mode, score in sorted_modes if score > 0.0)
