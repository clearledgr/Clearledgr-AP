"""
Optimal Matching Algorithm for Clearledgr v1

Implements the Hungarian (Kuhn-Munkres) algorithm for globally optimal transaction matching,
plus split payment detection and many-to-one matching support.
"""
from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime
import heapq


@dataclass
class MatchCandidate:
    """Represents a potential match between transactions."""
    source_idx: int
    target_idx: int
    score: float  # Lower is better
    amount_diff: float
    date_diff: int
    source_type: str = "gateway"
    target_type: str = "bank"


@dataclass
class SplitMatch:
    """Represents a split payment match (many-to-one)."""
    target_idx: int
    target_amount: float
    source_indices: List[int]
    source_amounts: List[float]
    combined_amount: float
    variance: float
    variance_pct: float


class HungarianMatcher:
    """
    Implements optimal bipartite matching using the Hungarian algorithm.
    
    This finds the globally optimal pairing of transactions, not just
    the first acceptable match (greedy approach).
    """
    
    INF = float('inf')
    
    @staticmethod
    def compute_cost_matrix(
        sources: List[Dict],
        targets: List[Dict],
        source_amount_key: str,
        target_amount_key: str,
        tolerance_pct: float,
        window_days: int,
        parse_date_fn
    ) -> List[List[float]]:
        """
        Build a cost matrix for optimal matching.
        
        Cost = amount_diff_normalized + date_diff_normalized
        INF for impossible matches (outside tolerance/window)
        """
        n = len(sources)
        m = len(targets)
        
        if n == 0 or m == 0:
            return []
        
        # Build cost matrix
        cost = [[HungarianMatcher.INF] * m for _ in range(n)]
        
        for i, src in enumerate(sources):
            src_amount = src.get(source_amount_key)
            src_date = parse_date_fn(src.get("date"))
            
            if src_amount is None or src_date is None:
                continue
            
            for j, tgt in enumerate(targets):
                tgt_amount = tgt.get(target_amount_key)
                tgt_date = parse_date_fn(tgt.get("date"))
                
                if tgt_amount is None or tgt_date is None:
                    continue
                
                # Check tolerance
                if src_amount == 0 and tgt_amount == 0:
                    amount_ok = True
                    amount_diff = 0
                elif src_amount == 0 or tgt_amount == 0:
                    amount_ok = False
                    amount_diff = HungarianMatcher.INF
                else:
                    diff = abs(src_amount - tgt_amount)
                    max_amount = max(abs(src_amount), abs(tgt_amount))
                    tolerance = max_amount * (tolerance_pct / 100.0)
                    amount_ok = diff <= tolerance
                    amount_diff = diff / max_amount if max_amount > 0 else 0
                
                # Check date window
                date_diff_days = abs((src_date - tgt_date).days)
                date_ok = date_diff_days <= window_days
                
                if amount_ok and date_ok:
                    # Normalize date difference (0-1 scale based on window)
                    date_diff_norm = date_diff_days / window_days if window_days > 0 else 0
                    
                    # Cost = weighted combination (amount more important)
                    cost[i][j] = (amount_diff * 0.7) + (date_diff_norm * 0.3)
        
        return cost
    
    @staticmethod
    def hungarian_algorithm(cost: List[List[float]]) -> List[Tuple[int, int]]:
        """
        Hungarian algorithm for minimum cost bipartite matching.
        
        Returns list of (source_idx, target_idx) pairs for optimal matching.
        """
        if not cost or not cost[0]:
            return []
        
        n = len(cost)  # sources
        m = len(cost[0])  # targets
        
        # Check if any valid matches exist
        has_valid = False
        for i in range(n):
            for j in range(m):
                if cost[i][j] < HungarianMatcher.INF:
                    has_valid = True
                    break
            if has_valid:
                break
        
        if not has_valid:
            return []  # No valid matches possible
        
        # Pad to square matrix if needed
        size = max(n, m)
        padded_cost = [[HungarianMatcher.INF] * size for _ in range(size)]
        for i in range(n):
            for j in range(m):
                padded_cost[i][j] = cost[i][j]
        
        # Standard Hungarian algorithm implementation
        u = [0.0] * (size + 1)
        v = [0.0] * (size + 1)
        p = [0] * (size + 1)
        way = [0] * (size + 1)
        
        for i in range(1, size + 1):
            p[0] = i
            j0 = 0
            minv = [HungarianMatcher.INF] * (size + 1)
            used = [False] * (size + 1)
            
            iterations = 0
            max_iterations = size * size + 10  # Safety limit
            
            while p[j0] != 0 and iterations < max_iterations:
                iterations += 1
                used[j0] = True
                i0 = p[j0]
                delta = HungarianMatcher.INF
                j1 = 0
                
                for j in range(1, size + 1):
                    if not used[j]:
                        cur = padded_cost[i0 - 1][j - 1] - u[i0] - v[j]
                        if cur < minv[j]:
                            minv[j] = cur
                            way[j] = j0
                        if minv[j] < delta:
                            delta = minv[j]
                            j1 = j
                
                # Safety: if no valid j found, break
                if delta == HungarianMatcher.INF:
                    break
                
                for j in range(size + 1):
                    if used[j]:
                        u[p[j]] += delta
                        v[j] -= delta
                    else:
                        minv[j] -= delta
                
                j0 = j1
            
            while j0:
                j1 = way[j0]
                p[j0] = p[j1]
                j0 = j1
        
        # Extract result
        result = []
        for j in range(1, size + 1):
            if p[j] != 0 and p[j] <= n and j <= m:
                if cost[p[j] - 1][j - 1] < HungarianMatcher.INF:
                    result.append((p[j] - 1, j - 1))
        
        return result


class SplitPaymentDetector:
    """
    Detects and matches split payments (many-to-one matching).
    
    Examples:
    - One bank deposit of $1000 matching 3 gateway payments of $333.33 each
    - One invoice payment split across multiple gateway transactions
    """
    
    @staticmethod
    def find_split_matches(
        sources: List[Dict],
        targets: List[Dict],
        source_amount_key: str,
        target_amount_key: str,
        tolerance_pct: float,
        window_days: int,
        parse_date_fn,
        already_matched_sources: Set[int],
        already_matched_targets: Set[int],
        max_split_size: int = 5
    ) -> List[SplitMatch]:
        """
        Find cases where multiple source transactions sum to one target.
        
        Uses a subset-sum approach with pruning for efficiency.
        """
        splits = []
        
        # Get unmatched targets
        unmatched_targets = [
            (j, targets[j])
            for j in range(len(targets))
            if j not in already_matched_targets
        ]
        
        # Get unmatched sources with valid amounts
        unmatched_sources = []
        for i in range(len(sources)):
            if i in already_matched_sources:
                continue
            src = sources[i]
            amount = src.get(source_amount_key)
            date = parse_date_fn(src.get("date"))
            if amount is not None and date is not None:
                unmatched_sources.append((i, src, amount, date))
        
        # For each unmatched target, try to find source combinations
        for target_idx, target in unmatched_targets:
            target_amount = target.get(target_amount_key)
            target_date = parse_date_fn(target.get("date"))
            
            if target_amount is None or target_date is None:
                continue
            
            # Filter sources by date window
            candidate_sources = [
                (i, src, amt, dt)
                for i, src, amt, dt in unmatched_sources
                if abs((dt - target_date).days) <= window_days
            ]
            
            if len(candidate_sources) < 2:
                continue
            
            # Limit candidates to prevent combinatorial explosion
            if len(candidate_sources) > 10:
                # Sort by amount closeness to target/split count
                avg_expected = target_amount / 2  # Assume 2-way split as baseline
                candidate_sources.sort(key=lambda x: abs(x[2] - avg_expected))
                candidate_sources = candidate_sources[:10]
            
            # Sort by amount (helps with pruning)
            candidate_sources.sort(key=lambda x: x[2], reverse=True)
            
            # Find subsets that sum to target (with tolerance)
            tolerance = abs(target_amount) * (tolerance_pct / 100.0)
            best_match = SplitPaymentDetector._find_subset_sum(
                candidate_sources,
                target_amount,
                tolerance,
                max_split_size
            )
            
            if best_match:
                source_indices, source_amounts = zip(*best_match)
                combined = sum(source_amounts)
                variance = abs(combined - target_amount)
                variance_pct = (variance / abs(target_amount) * 100) if target_amount != 0 else 0
                
                splits.append(SplitMatch(
                    target_idx=target_idx,
                    target_amount=target_amount,
                    source_indices=list(source_indices),
                    source_amounts=list(source_amounts),
                    combined_amount=combined,
                    variance=variance,
                    variance_pct=variance_pct
                ))
        
        return splits
    
    @staticmethod
    def _find_subset_sum(
        candidates: List[Tuple[int, Dict, float, datetime]],
        target: float,
        tolerance: float,
        max_size: int
    ) -> Optional[List[Tuple[int, float]]]:
        """
        Find subset of amounts that sum to target within tolerance.
        Uses branch-and-bound for efficiency.
        """
        n = len(candidates)
        best_match = None
        best_variance = float('inf')
        
        # Use iterative deepening for efficiency
        for size in range(2, min(max_size + 1, n + 1)):
            match = SplitPaymentDetector._subset_of_size(
                candidates, target, tolerance, size
            )
            if match:
                variance = abs(sum(m[1] for m in match) - target)
                if variance < best_variance:
                    best_variance = variance
                    best_match = match
                    if variance == 0:  # Perfect match
                        return best_match
        
        return best_match
    
    @staticmethod
    def _subset_of_size(
        candidates: List[Tuple[int, Dict, float, datetime]],
        target: float,
        tolerance: float,
        size: int
    ) -> Optional[List[Tuple[int, float]]]:
        """Find a subset of exactly 'size' elements that sum to target."""
        from itertools import combinations
        
        for combo in combinations(range(len(candidates)), size):
            amounts = [candidates[i][2] for i in combo]
            total = sum(amounts)
            if abs(total - target) <= tolerance:
                return [(candidates[i][0], candidates[i][2]) for i in combo]
        
        return None


class OptimalReconciler:
    """
    Orchestrates optimal matching with split payment detection.
    """
    
    @staticmethod
    def reconcile(
        sources: List[Dict],
        targets: List[Dict],
        source_amount_key: str,
        target_amount_key: str,
        tolerance_pct: float,
        window_days: int,
        parse_date_fn,
        enable_splits: bool = True
    ) -> Dict:
        """
        Perform optimal reconciliation with optional split detection.
        
        Returns:
            {
                "matches": [(source_idx, target_idx), ...],
                "splits": [SplitMatch, ...],
                "unmatched_sources": [idx, ...],
                "unmatched_targets": [idx, ...]
            }
        """
        # Step 1: Build cost matrix
        cost = HungarianMatcher.compute_cost_matrix(
            sources, targets,
            source_amount_key, target_amount_key,
            tolerance_pct, window_days, parse_date_fn
        )
        
        # Step 2: Find optimal 1:1 matches using Hungarian algorithm
        matches = HungarianMatcher.hungarian_algorithm(cost)
        
        matched_sources = {m[0] for m in matches}
        matched_targets = {m[1] for m in matches}
        
        # Step 3: Find split payments among unmatched
        splits = []
        if enable_splits:
            splits = SplitPaymentDetector.find_split_matches(
                sources, targets,
                source_amount_key, target_amount_key,
                tolerance_pct, window_days, parse_date_fn,
                matched_sources, matched_targets
            )
            
            # Mark split sources/targets as matched
            for split in splits:
                matched_targets.add(split.target_idx)
                for src_idx in split.source_indices:
                    matched_sources.add(src_idx)
        
        # Step 4: Collect unmatched
        unmatched_sources = [
            i for i in range(len(sources))
            if i not in matched_sources
        ]
        unmatched_targets = [
            j for j in range(len(targets))
            if j not in matched_targets
        ]
        
        return {
            "matches": matches,
            "splits": splits,
            "unmatched_sources": unmatched_sources,
            "unmatched_targets": unmatched_targets
        }


def upgrade_to_optimal_matching(
    reconcile_fn,
    tolerance_pct: float,
    window_days: int
):
    """
    Factory function to upgrade existing reconcile logic to use optimal matching.
    Can be used as a drop-in replacement.
    """
    def optimal_reconcile(config: Dict, sources: Dict[str, List[Dict]]) -> Dict:
        from datetime import datetime
        
        def parse_date(date_str):
            if not date_str:
                return None
            try:
                return datetime.strptime(date_str, "%Y-%m-%d")
            except (ValueError, TypeError):
                return None
        
        gateway = sources.get("gateway", [])
        bank = sources.get("bank", [])
        internal = sources.get("internal", [])
        
        tol = config.get("amount_tolerance_pct", tolerance_pct)
        days = config.get("date_window_days", window_days)
        
        # Gateway ↔ Bank matching (optimal)
        gw_bank_result = OptimalReconciler.reconcile(
            gateway, bank,
            "net_amount", "amount",
            tol, days, parse_date,
            enable_splits=True
        )
        
        # Build groups from matches
        groups = []
        matched_gateway = set()
        matched_bank = set()
        
        # 1:1 matches
        for gw_idx, bank_idx in gw_bank_result["matches"]:
            matched_gateway.add(gw_idx)
            matched_bank.add(bank_idx)
            groups.append({
                "gateway": [gateway[gw_idx]],
                "bank": [bank[bank_idx]],
                "internal": [],
                "match_type": "optimal_1to1"
            })
        
        # Split matches (many-to-one)
        for split in gw_bank_result["splits"]:
            matched_bank.add(split.target_idx)
            gw_items = []
            for src_idx in split.source_indices:
                matched_gateway.add(src_idx)
                gw_items.append(gateway[src_idx])
            
            groups.append({
                "gateway": gw_items,
                "bank": [bank[split.target_idx]],
                "internal": [],
                "match_type": "split_payment",
                "split_info": {
                    "source_count": len(split.source_indices),
                    "combined_amount": split.combined_amount,
                    "target_amount": split.target_amount,
                    "variance": split.variance,
                    "variance_pct": split.variance_pct
                }
            })
        
        # Match internal to groups (using greedy for simplicity)
        matched_internal = set()
        for i, int_item in enumerate(internal):
            int_amount = int_item.get("amount")
            int_date = parse_date(int_item.get("date"))
            
            if int_amount is None or int_date is None:
                continue
            
            best_group = None
            best_score = float('inf')
            
            for group in groups:
                gw_items = group.get("gateway", [])
                if not gw_items:
                    continue
                
                gw_amount = sum(item.get("net_amount", 0) or 0 for item in gw_items)
                gw_date = parse_date(gw_items[0].get("date"))
                
                if gw_date is None:
                    continue
                
                # Check if internal matches (accounting for fees)
                if int_amount >= gw_amount:
                    fee_pct = ((int_amount - gw_amount) / int_amount * 100) if int_amount > 0 else 0
                    if fee_pct > 10:
                        continue
                else:
                    diff = abs(int_amount - gw_amount)
                    max_amt = max(abs(int_amount), abs(gw_amount))
                    if diff > max_amt * (tol / 100):
                        continue
                
                date_diff = abs((int_date - gw_date).days)
                if date_diff > days:
                    continue
                
                score = abs(int_amount - gw_amount) + date_diff * 1000
                if score < best_score:
                    best_score = score
                    best_group = group
            
            if best_group:
                best_group["internal"].append(int_item)
                matched_internal.add(i)
        
        # Exceptions
        exceptions = {
            "gateway": [gateway[i] for i in range(len(gateway)) if i not in matched_gateway],
            "bank": [bank[i] for i in range(len(bank)) if i not in matched_bank],
            "internal": [internal[i] for i in range(len(internal)) if i not in matched_internal]
        }
        
        # Stats
        stats = {
            "total_gateway": len(gateway),
            "total_bank": len(bank),
            "total_internal": len(internal),
            "matched_gateway": len(matched_gateway),
            "matched_bank": len(matched_bank),
            "matched_internal": len(matched_internal),
            "total_groups": len(groups),
            "split_matches": len(gw_bank_result["splits"]),
            "groups_with_internal": sum(1 for g in groups if g["internal"]),
            "unmatched_gateway": len(exceptions["gateway"]),
            "unmatched_bank": len(exceptions["bank"]),
            "unmatched_internal": len(exceptions["internal"]),
            "match_rate_gateway": (len(matched_gateway) / len(gateway) * 100) if gateway else 0,
            "match_rate_bank": (len(matched_bank) / len(bank) * 100) if bank else 0,
            "match_rate_internal": (len(matched_internal) / len(internal) * 100) if internal else 0,
            "algorithm": "hungarian_optimal"
        }
        
        return {
            "groups": groups,
            "exceptions": exceptions,
            "stats": stats
        }
    
    return optimal_reconcile

