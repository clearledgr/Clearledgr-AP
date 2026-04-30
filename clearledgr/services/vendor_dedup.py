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

from clearledgr.services.fuzzy_matching import normalize_vendor, vendor_similarity

logger = logging.getLogger(__name__)

# Default similarity threshold for suggesting a merge
DEFAULT_SIMILARITY_THRESHOLD = 0.75


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

        Returns a list of duplicate clusters, each containing:
        - canonical: the profile with the most invoices (suggested primary)
        - duplicates: list of profiles that may be duplicates
        - similarity: score between canonical and each duplicate
        """
        profiles = self._load_all_profiles()
        if len(profiles) < 2:
            return []

        # Build clusters using single-linkage: if A~B and B~C, they're one cluster
        visited = set()
        clusters: List[List[Tuple[Dict, Dict, float]]] = []

        for i, p1 in enumerate(profiles):
            if p1["vendor_name"] in visited:
                continue
            cluster_pairs: List[Tuple[Dict, Dict, float]] = []
            for j, p2 in enumerate(profiles):
                if i >= j or p2["vendor_name"] in visited:
                    continue
                sim = vendor_similarity(p1["vendor_name"], p2["vendor_name"])
                if sim >= threshold:
                    cluster_pairs.append((p1, p2, sim))

            if cluster_pairs:
                # All names in this cluster
                names_in_cluster = {p1["vendor_name"]}
                for _, dup, _ in cluster_pairs:
                    names_in_cluster.add(dup["vendor_name"])
                visited.update(names_in_cluster)
                clusters.append(cluster_pairs)

        # Format output
        results = []
        for pairs in clusters:
            # Gather all unique profiles in this cluster
            all_profiles: Dict[str, Dict] = {}
            for p1, p2, _ in pairs:
                all_profiles[p1["vendor_name"]] = p1
                all_profiles[p2["vendor_name"]] = p2

            # Canonical = most invoices
            sorted_profiles = sorted(
                all_profiles.values(),
                key=lambda p: p.get("invoice_count", 0),
                reverse=True,
            )
            canonical = sorted_profiles[0]
            duplicates = []
            for dup in sorted_profiles[1:]:
                sim = vendor_similarity(canonical["vendor_name"], dup["vendor_name"])
                duplicates.append({
                    "vendor_name": dup["vendor_name"],
                    "invoice_count": dup.get("invoice_count", 0),
                    "similarity": round(sim, 3),
                })

            results.append({
                "canonical": {
                    "vendor_name": canonical["vendor_name"],
                    "invoice_count": canonical.get("invoice_count", 0),
                },
                "duplicates": duplicates,
                "total_invoices": sum(
                    p.get("invoice_count", 0) for p in all_profiles.values()
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
