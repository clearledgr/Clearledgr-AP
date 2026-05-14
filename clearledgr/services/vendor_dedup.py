"""Vendor deduplication service — detect, merge, and manage vendor aliases.

Detects duplicate vendor profiles using fuzzy name matching, provides
merge suggestions, and executes merges by consolidating data into a
canonical profile with aliases.

Uses the existing fuzzy_matching.py for similarity scoring and
vendor_profiles.vendor_aliases for alias persistence.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from clearledgr.services.fuzzy_matching import (
    match_probability,
    normalize_vendor,
    vendor_similarity,
    vendor_similarity_modes,
)
from clearledgr.services.vendor_search import (
    DEFAULT_RRF_K,
    find_candidate_matches,
)

logger = logging.getLogger(__name__)

# Default similarity threshold for suggesting a merge.
DEFAULT_SIMILARITY_THRESHOLD = 0.75

# Sprint 3-A — RRF-based clustering defaults. The detect-duplicates
# pipeline now runs each profile's name as a query against the
# corpus and treats top-K matches as edge candidates. ``DEFAULT_TOP_K``
# is how many candidates each profile considers (small numbers
# prevent quadratic blowup on large orgs); ``MIN_RRF_SCORE_GATE``
# drops candidates whose RRF aggregate is too weak to be a real
# match (the score is unitless, scaled by ``DEFAULT_RRF_K``).
DEFAULT_TOP_K = 6
MIN_RRF_SCORE_GATE = 0.05  # ~3 mid-ranked appearances across modes


class VendorDedupService:
    """Detect and merge duplicate vendor profiles for a single tenant."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_duplicates(
        self, threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> List[Dict[str, Any]]:
        """Find groups of vendor profiles that look like duplicates.

        Sprint 3-A: forwards to :meth:`detect_duplicates_via_rrf`.
        The legacy N² pairwise loop scaled poorly and used max-of-
        modes which can't distinguish "modes agree because match is
        real" from "modes agree because partial-substring overlap".
        RRF-over-rankings cancels that ambiguity — see
        ``services/vendor_search.py`` and the architectural note at
        the top of ``services/fuzzy_matching.py:vendor_similarity_
        hybrid``.

        Returns a list of duplicate clusters, each containing:
        - canonical: profile with the most invoices (suggested primary)
        - duplicates: list of profiles that may be duplicates
        - similarity: legacy max-of-modes pairwise score (audit only)
        - confidence: heuristic match probability via
          :func:`match_probability` (uncalibrated; UI label, not gate)
        """
        return self.detect_duplicates_via_rrf(threshold=threshold)

    def detect_duplicates_via_rrf(
        self,
        *,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        top_k: int = DEFAULT_TOP_K,
        rrf_k: int = DEFAULT_RRF_K,
        min_rrf_score: float = MIN_RRF_SCORE_GATE,
    ) -> List[Dict[str, Any]]:
        """Detect duplicates via Reciprocal Rank Fusion over per-mode
        rankings, with mutual-top-K clustering.

        Algorithm:

        1. For each profile, run its name as a query against the
           rest of the corpus via
           :func:`vendor_search.find_candidate_matches`. Each mode
           (containment / jaccard / sequence / lev / trigram)
           ranks every other candidate independently; RRF fuses
           the rankings so a candidate consistently top-ranked
           across modes wins over one with a single high spike.
        2. Filter candidate edges to those with RRF score >=
           ``min_rrf_score`` AND legacy similarity >= ``threshold``.
           The RRF gate handles "ranked highly across multiple
           modes"; the similarity gate keeps the low-end behavior
           comparable to the legacy detector.
        3. Build an undirected mutual-edge graph: edge ``(A, B)``
           survives only if both ``A`` ranks ``B`` in its top-K
           AND ``B`` ranks ``A`` in its top-K. Mutual top-K is the
           agreement signal pairwise scoring can't deliver — it
           rejects partial-substring false positives that look
           similar TO a query but don't reciprocate (because the
           false-positive candidate has many other near-equal
           neighbors that bump the query out of its top-K).
        4. Connected components via union-find = duplicate clusters.

        Complexity: O(N * top_k * M) where M is the number of fusion
        modes (~5). Replaces the legacy O(N²) pairwise scan.
        """
        profiles = self._load_all_profiles()
        if len(profiles) < 2:
            return []

        # 1. Top-K candidate retrieval per profile.
        # Index by normalized name so ranks are stable when the
        # corpus has duplicate display names.
        indexed: List[Dict[str, Any]] = []
        for idx, prof in enumerate(profiles):
            row = dict(prof)
            row["_rrf_idx"] = idx
            indexed.append(row)

        top_k_per_profile: Dict[int, List[Tuple[int, float, Dict[str, float]]]] = {}
        for source in indexed:
            corpus = [r for r in indexed if r["_rrf_idx"] != source["_rrf_idx"]]
            matches = find_candidate_matches(
                source["vendor_name"],
                corpus,
                k=top_k,
                rrf_k=rrf_k,
            )
            top_k_per_profile[source["_rrf_idx"]] = [
                (m.candidate_record["_rrf_idx"], m.score, m.modes)
                for m in matches
                if m.score >= min_rrf_score
            ]

        # 2. Edge candidates filtered by legacy similarity threshold.
        #    Keeps the threshold knob meaningful for callers
        #    used to it.
        edges: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for src_idx, candidates in top_k_per_profile.items():
            for dst_idx, rrf_score, modes in candidates:
                # ``modes`` is the per-mode dict from the perspective
                # of src→dst. Pairwise similarity is symmetric for
                # the modes we use, so this is fine.
                pair = tuple(sorted((src_idx, dst_idx)))
                # Take the worse of the two RRF scores (set on first
                # write, update on second). Edges only survive if
                # BOTH directions reach top-K — handled by the mutual
                # filter below.
                existing = edges.get(pair)
                pairwise_sim = max(s for k, s in modes.items() if k != "exact") \
                    if modes else 0.0
                edge = {
                    "rrf_score": rrf_score,
                    "modes": modes,
                    "similarity": pairwise_sim,
                    "src_seen": True if src_idx == pair[0] else False,
                    "dst_seen": True if src_idx == pair[1] else False,
                }
                if existing is None:
                    edges[pair] = edge
                else:
                    # Merge: track that BOTH directions have been seen.
                    edges[pair] = {
                        "rrf_score": min(existing["rrf_score"], rrf_score),
                        "modes": modes,
                        "similarity": min(existing["similarity"], pairwise_sim),
                        "src_seen": existing.get("src_seen") or (src_idx == pair[0]),
                        "dst_seen": existing.get("dst_seen") or (src_idx == pair[1]),
                    }

        # 3. Mutual top-K filter + similarity threshold gate.
        #    ``src_seen=True`` means pair[0] reached pair[1]'s top-K
        #    (because we recorded that direction when src_idx ==
        #    pair[0]); ``dst_seen=True`` means pair[1] reached
        #    pair[0]'s top-K. Both required.
        keep_edges: List[Tuple[Tuple[int, int], Dict[str, Any]]] = []
        for pair, edge in edges.items():
            if not (edge["src_seen"] and edge["dst_seen"]):
                continue
            if edge["similarity"] < threshold:
                continue
            keep_edges.append((pair, edge))

        # 4. Connected components via union-find.
        parent = {i: i for i in range(len(indexed))}

        def _find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]  # path compression
                x = parent[x]
            return x

        def _union(a: int, b: int) -> None:
            ra, rb = _find(a), _find(b)
            if ra != rb:
                parent[ra] = rb

        for (a, b), _ in keep_edges:
            _union(a, b)

        components: Dict[int, List[int]] = {}
        for idx in range(len(indexed)):
            root = _find(idx)
            components.setdefault(root, []).append(idx)

        # 5. Materialize clusters. A cluster needs >= 2 members and
        #    at least one surviving edge connecting them (the union-
        #    find groups members; the kept-edges set tells us they're
        #    connected by a surviving edge, not just by transitive
        #    threshold-grazes).
        edge_lookup: Dict[Tuple[int, int], Dict[str, Any]] = dict(keep_edges)

        results: List[Dict[str, Any]] = []
        for root, members in components.items():
            if len(members) < 2:
                continue

            # Canonical = profile with the most invoices.
            member_profiles = [indexed[m] for m in members]
            sorted_profiles = sorted(
                member_profiles,
                key=lambda p: p.get("invoice_count", 0),
                reverse=True,
            )
            canonical = sorted_profiles[0]
            canonical_idx = canonical["_rrf_idx"]

            duplicates: List[Dict[str, Any]] = []
            for dup_profile in sorted_profiles[1:]:
                dup_idx = dup_profile["_rrf_idx"]
                pair = tuple(sorted((canonical_idx, dup_idx)))
                edge = edge_lookup.get(pair)
                if edge is None:
                    # Connected only transitively (A~B~C, but A↔C
                    # never had a direct mutual top-K edge). Compute
                    # similarity / modes / probability on demand so
                    # the surfaced duplicate row still has audit info.
                    direct_modes = vendor_similarity_modes(
                        canonical["vendor_name"], dup_profile["vendor_name"],
                    )
                    direct_sim = max(
                        s for k, s in direct_modes.items() if k != "exact"
                    ) if direct_modes else 0.0
                    edge_data = {
                        "similarity": direct_sim,
                        "modes": direct_modes,
                        "rrf_score": 0.0,  # transitive — no direct RRF edge
                        "transitive": True,
                    }
                else:
                    edge_data = {
                        "similarity": edge["similarity"],
                        "modes": edge["modes"],
                        "rrf_score": edge["rrf_score"],
                        "transitive": False,
                    }
                duplicates.append({
                    "vendor_name": dup_profile["vendor_name"],
                    "invoice_count": dup_profile.get("invoice_count", 0),
                    "similarity": round(edge_data["similarity"], 3),
                    "rrf_score": round(edge_data["rrf_score"], 4),
                    "confidence": round(match_probability(edge_data["modes"]), 3),
                    "modes": {k: round(v, 3) for k, v in edge_data["modes"].items()},
                    "transitive": edge_data["transitive"],
                })

            results.append({
                "canonical": {
                    "vendor_name": canonical["vendor_name"],
                    "invoice_count": canonical.get("invoice_count", 0),
                },
                "duplicates": duplicates,
                "total_invoices": sum(
                    p.get("invoice_count", 0) for p in member_profiles
                ),
            })

        return results

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge_vendors(
        self,
        canonical_name: str,
        duplicate_names: List[str],
    ) -> Dict[str, Any]:
        """Merge duplicate vendor profiles into the canonical one.

        Steps:
        1. Add duplicate names to canonical's vendor_aliases
        2. Aggregate invoice_count, update stats
        3. Reassign AP items from duplicates to canonical vendor name
        4. Delete duplicate profiles

        Returns a summary of the merge.
        """
        if not duplicate_names:
            return {"merged": 0, "error": "no_duplicates_provided"}

        # Load canonical profile
        canonical = self.db.get_vendor_profile(self.organization_id, canonical_name)
        if not canonical:
            # Create it if it doesn't exist
            canonical = self.db.upsert_vendor_profile(
                self.organization_id, canonical_name,
            )

        # Current aliases
        existing_aliases = canonical.get("vendor_aliases") or []
        if isinstance(existing_aliases, str):
            try:
                existing_aliases = json.loads(existing_aliases)
            except (json.JSONDecodeError, TypeError):
                existing_aliases = []

        merged_count = 0
        reassigned_items = 0

        for dup_name in duplicate_names:
            if dup_name == canonical_name:
                continue

            dup_profile = self.db.get_vendor_profile(self.organization_id, dup_name)

            # Add to aliases (if not already there)
            if dup_name not in existing_aliases:
                existing_aliases.append(dup_name)

            # Also add the duplicate's own aliases
            if dup_profile:
                dup_aliases = dup_profile.get("vendor_aliases") or []
                if isinstance(dup_aliases, str):
                    try:
                        dup_aliases = json.loads(dup_aliases)
                    except (json.JSONDecodeError, TypeError):
                        dup_aliases = []
                for alias in dup_aliases:
                    if alias not in existing_aliases and alias != canonical_name:
                        existing_aliases.append(alias)

            # Reassign AP items from duplicate to canonical
            try:
                count = self._reassign_ap_items(dup_name, canonical_name)
                reassigned_items += count
            except Exception as exc:
                logger.warning(
                    "[VendorDedup] Failed to reassign AP items from %s to %s: %s",
                    dup_name, canonical_name, exc,
                )

            # Delete duplicate profile
            if dup_profile:
                self._delete_vendor_profile(dup_name)

            merged_count += 1

        # Update canonical with merged aliases
        self.db.upsert_vendor_profile(
            self.organization_id,
            canonical_name,
            vendor_aliases=existing_aliases,
        )

        logger.info(
            "[VendorDedup] Merged %d vendor(s) into '%s' for org=%s (%d AP items reassigned)",
            merged_count, canonical_name, self.organization_id, reassigned_items,
        )

        return {
            "canonical": canonical_name,
            "merged_count": merged_count,
            "merged_names": duplicate_names,
            "aliases": existing_aliases,
            "reassigned_items": reassigned_items,
        }

    # ------------------------------------------------------------------
    # Alias management
    # ------------------------------------------------------------------

    def add_alias(self, vendor_name: str, alias: str) -> Dict[str, Any]:
        """Add an alias to a vendor profile."""
        profile = self.db.get_vendor_profile(self.organization_id, vendor_name)
        if not profile:
            return {"error": "vendor_not_found"}

        aliases = profile.get("vendor_aliases") or []
        if isinstance(aliases, str):
            try:
                aliases = json.loads(aliases)
            except (json.JSONDecodeError, TypeError):
                aliases = []

        if alias not in aliases:
            aliases.append(alias)
            self.db.upsert_vendor_profile(
                self.organization_id, vendor_name, vendor_aliases=aliases,
            )

        return {"vendor_name": vendor_name, "aliases": aliases}

    def remove_alias(self, vendor_name: str, alias: str) -> Dict[str, Any]:
        """Remove an alias from a vendor profile."""
        profile = self.db.get_vendor_profile(self.organization_id, vendor_name)
        if not profile:
            return {"error": "vendor_not_found"}

        aliases = profile.get("vendor_aliases") or []
        if isinstance(aliases, str):
            try:
                aliases = json.loads(aliases)
            except (json.JSONDecodeError, TypeError):
                aliases = []

        if alias in aliases:
            aliases.remove(alias)
            self.db.upsert_vendor_profile(
                self.organization_id, vendor_name, vendor_aliases=aliases,
            )

        return {"vendor_name": vendor_name, "aliases": aliases}

    def resolve_vendor_name(self, raw_name: str) -> str:
        """Resolve a raw vendor name to its canonical name via aliases.

        Checks all vendor profiles' aliases to find a match.
        Returns the canonical name if found, otherwise the raw name.
        """
        profiles = self._load_all_profiles()
        normalized_raw = normalize_vendor(raw_name)

        for profile in profiles:
            # Check exact match
            if profile["vendor_name"] == raw_name:
                return profile["vendor_name"]

            # Check aliases
            aliases = profile.get("vendor_aliases") or []
            if isinstance(aliases, str):
                try:
                    aliases = json.loads(aliases)
                except (json.JSONDecodeError, TypeError):
                    aliases = []

            for alias in aliases:
                if alias == raw_name or normalize_vendor(alias) == normalized_raw:
                    return profile["vendor_name"]

        return raw_name

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_all_profiles(self) -> List[Dict[str, Any]]:
        """Load all vendor profiles for this org."""
        sql = (
            "SELECT * FROM vendor_profiles WHERE organization_id = %s "
            "ORDER BY invoice_count DESC"
        )
        try:
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id,))
                rows = [dict(r) for r in cur.fetchall()]

            for row in rows:
                for field in ("vendor_aliases", "sender_domains", "anomaly_flags"):
                    val = row.get(field)
                    if isinstance(val, str):
                        try:
                            row[field] = json.loads(val)
                        except (json.JSONDecodeError, TypeError):
                            row[field] = []
                if isinstance(row.get("metadata"), str):
                    try:
                        row["metadata"] = json.loads(row["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        row["metadata"] = {}

            return rows
        except Exception as exc:
            logger.warning("[VendorDedup] Failed to load profiles: %s", exc)
            return []

    def _reassign_ap_items(self, from_name: str, to_name: str) -> int:
        """Reassign AP items from one vendor name to another."""
        sql = (
            "UPDATE ap_items SET vendor_name = %s, updated_at = %s "
            "WHERE organization_id = %s AND vendor_name = %s"
        )
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (to_name, now, self.organization_id, from_name))
                conn.commit()
                return cur.rowcount
        except Exception as exc:
            logger.warning("[VendorDedup] _reassign_ap_items failed: %s", exc)
            return 0

    def _delete_vendor_profile(self, vendor_name: str) -> bool:
        """Delete a vendor profile."""
        sql = (
            "DELETE FROM vendor_profiles WHERE organization_id = %s AND vendor_name = %s"
        )
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id, vendor_name))
                conn.commit()
                return cur.rowcount > 0
        except Exception as exc:
            logger.warning("[VendorDedup] _delete_vendor_profile failed: %s", exc)
            return False


def get_vendor_dedup_service(organization_id: str = "default") -> VendorDedupService:
    return VendorDedupService(organization_id=organization_id)
