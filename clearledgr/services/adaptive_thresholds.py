"""Adaptive decision thresholds — learns from operator feedback.

When an operator overrides the agent's decision (approves what the agent
wanted to escalate, or rejects what the agent wanted to approve), the
system adjusts its thresholds for that vendor/amount range.

This is the learning loop that Stampli's Billy uses to reach 86% automation.
Without it, every invoice is processed with the same generic thresholds.
With it, vendors with consistent approval history get lower thresholds,
and vendors with frequent overrides get higher ones.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Default auto-approve confidence threshold
DEFAULT_THRESHOLD = 0.95

# Minimum invoices before adjusting threshold
MIN_HISTORY_FOR_ADJUSTMENT = 5

# How much to adjust per consistent signal
ADJUSTMENT_STEP = 0.02

# Floor and ceiling
MIN_THRESHOLD = 0.80
MAX_THRESHOLD = 0.99


class AdaptiveThresholdService:
    """Learn auto-approve thresholds from operator feedback."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    def get_threshold_for_vendor(self, vendor_name: str) -> float:
        """Get the effective auto-approve threshold for a vendor.

        Uses vendor-specific learned threshold if available,
        otherwise falls back to org default.
        """
        profile = self.db.get_vendor_profile(self.organization_id, vendor_name) or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        learned = meta.get("learned_auto_approve_threshold")
        if learned is not None:
            try:
                return max(MIN_THRESHOLD, min(MAX_THRESHOLD, float(learned)))
            except (TypeError, ValueError):
                pass

        return self._org_default_threshold()

    def record_decision_outcome(
        self,
        vendor_name: str,
        agent_recommendation: str,
        operator_decision: str,
        confidence: float,
    ) -> Optional[float]:
        """Record an operator decision and adjust the vendor threshold.

        Returns the new threshold if adjusted, None otherwise.

        Logic:
        - Agent said approve, operator approved → threshold can decrease (more automation)
        - Agent said escalate, operator approved → threshold should decrease (agent was too cautious)
        - Agent said approve, operator rejected → threshold should increase (agent was too lenient)
        - Agent said escalate, operator rejected → threshold stays (both agreed it was risky)
        """
        profile = self.db.get_vendor_profile(self.organization_id, vendor_name) or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        # Track decision history
        history = meta.get("decision_history") or []
        history.append({
            "agent": agent_recommendation,
            "operator": operator_decision,
            "confidence": confidence,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        # Keep last 50
        history = history[-50:]
        meta["decision_history"] = history

        # Only adjust after enough history
        if len(history) < MIN_HISTORY_FOR_ADJUSTMENT:
            meta["learned_auto_approve_threshold"] = DEFAULT_THRESHOLD
            self.db.upsert_vendor_profile(self.organization_id, vendor_name, metadata=meta)
            return None

        # Calculate adjustment
        current = self.get_threshold_for_vendor(vendor_name)
        recent = history[-20:]  # Last 20 decisions

        agent_approve_operator_approve = sum(
            1 for d in recent if d["agent"] == "approve" and d["operator"] in ("approve", "approved")
        )
        agent_escalate_operator_approve = sum(
            1 for d in recent if d["agent"] in ("escalate", "needs_info") and d["operator"] in ("approve", "approved")
        )
        agent_approve_operator_reject = sum(
            1 for d in recent if d["agent"] == "approve" and d["operator"] in ("reject", "rejected")
        )

        total = len(recent)
        if total == 0:
            return None

        # Agent too cautious: escalated but operator approved → lower threshold
        if agent_escalate_operator_approve / total > 0.3:
            new_threshold = max(MIN_THRESHOLD, current - ADJUSTMENT_STEP)
        # Agent too lenient: approved but operator rejected → raise threshold
        elif agent_approve_operator_reject / total > 0.1:
            new_threshold = min(MAX_THRESHOLD, current + ADJUSTMENT_STEP)
        # Agent and operator agree most of the time → gradually lower threshold
        elif agent_approve_operator_approve / total > 0.8:
            new_threshold = max(MIN_THRESHOLD, current - ADJUSTMENT_STEP * 0.5)
        else:
            new_threshold = current

        if new_threshold != current:
            meta["learned_auto_approve_threshold"] = round(new_threshold, 3)
            meta["threshold_adjusted_at"] = datetime.now(timezone.utc).isoformat()
            meta["threshold_adjustment_reason"] = (
                f"approve_agree={agent_approve_operator_approve}/{total}, "
                f"escalate_overridden={agent_escalate_operator_approve}/{total}, "
                f"approve_rejected={agent_approve_operator_reject}/{total}"
            )
            self.db.upsert_vendor_profile(self.organization_id, vendor_name, metadata=meta)
            logger.info(
                "[AdaptiveThresholds] %s threshold adjusted: %.3f → %.3f (%s)",
                vendor_name, current, new_threshold, meta["threshold_adjustment_reason"],
            )
            return new_threshold

        meta["learned_auto_approve_threshold"] = current
        self.db.upsert_vendor_profile(self.organization_id, vendor_name, metadata=meta)
        return None

    def _org_default_threshold(self) -> float:
        try:
            org = self.db.get_organization(self.organization_id) or {}
            settings = org.get("settings_json") or org.get("settings") or {}
            if isinstance(settings, str):
                settings = json.loads(settings)
            return float(settings.get("auto_approve_threshold", DEFAULT_THRESHOLD))
        except Exception:
            return DEFAULT_THRESHOLD


def get_adaptive_threshold_service(organization_id: str = "default") -> AdaptiveThresholdService:
    return AdaptiveThresholdService(organization_id=organization_id)
