"""Configurable confidence threshold policy (Wave 5 / G4).

Three-layer threshold resolution for AP routing decisions:

  1. **Vendor manual override** (operator-set, lock-in)
     ``vendor_profiles.metadata['threshold_override']``
  2. **Vendor learned threshold** (existing G4-precursor — adaptive
     thresholds learned from operator feedback)
     ``vendor_profiles.metadata['learned_auto_approve_threshold']``
  3. **Org default policy**
     ``organizations.settings_json['routing_thresholds']``
  4. **Hard-coded fallback** (0.95 auto-approve / 0.70 escalate)

Each layer can supply some or all of the three thresholds:

  * ``auto_approve_min`` — at or above this confidence, auto-approve
    if no other gate fails.
  * ``escalate_below`` — below this confidence, escalate to operator
    automatically (don't even attempt auto-approve).
  * ``po_required_above`` — bills above this gross amount must
    carry a PO reference (vendor PO required regardless of confidence).

This module is config + resolution. The actual routing decisions
are made by ap_decision (which already reads thresholds via
adaptive_thresholds); G4 widens that surface to ALL three
thresholds + adds operator overrides.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


_DEFAULT_AUTO_APPROVE_MIN = 0.95
_DEFAULT_ESCALATE_BELOW = 0.70
_DEFAULT_PO_REQUIRED_ABOVE = 5000.0  # in the bill's currency

_MIN_AUTO_APPROVE = 0.50
_MAX_AUTO_APPROVE = 0.99


@dataclass
class ThresholdResolution:
    """Result of a per-vendor threshold lookup."""

    organization_id: str
    vendor_name: Optional[str]
    auto_approve_min: float
    escalate_below: float
    po_required_above: Optional[float]
    source_chain: Dict[str, str]   # which layer supplied each value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "vendor_name": self.vendor_name,
            "auto_approve_min": self.auto_approve_min,
            "escalate_below": self.escalate_below,
            "po_required_above": self.po_required_above,
            "source_chain": dict(self.source_chain),
        }


# ── Validation ─────────────────────────────────────────────────────


def _clamp_threshold(value: Any, *, default: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return max(_MIN_AUTO_APPROVE, min(_MAX_AUTO_APPROVE, f))


def _coerce_amount(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, f)


def _validate_threshold_pair(
    auto_approve_min: Optional[float],
    escalate_below: Optional[float],
) -> None:
    """``auto_approve_min`` must be > ``escalate_below`` — otherwise
    the routing window is undefined."""
    if (
        auto_approve_min is not None
        and escalate_below is not None
        and auto_approve_min <= escalate_below
    ):
        raise ValueError(
            f"auto_approve_min ({auto_approve_min}) must be > "
            f"escalate_below ({escalate_below})"
        )


# ── Org-level config ──────────────────────────────────────────────


def get_org_thresholds(db, organization_id: str) -> Dict[str, Any]:
    """Read the org's threshold policy from
    ``settings_json['routing_thresholds']``."""
    try:
        org = db.get_organization(organization_id) or {}
    except Exception:
        return {}
    settings: Any = org.get("settings") or org.get("settings_json") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except (ValueError, TypeError):
            settings = {}
    if not isinstance(settings, dict):
        return {}
    block = settings.get("routing_thresholds") or {}
    if not isinstance(block, dict):
        return {}
    return block


def set_org_thresholds(
    db,
    organization_id: str,
    *,
    auto_approve_min: Optional[float] = None,
    escalate_below: Optional[float] = None,
    po_required_above: Optional[float] = None,
) -> Dict[str, Any]:
    """Persist org-level thresholds. Partial writes preserve any
    fields not present in the call."""
    _validate_threshold_pair(auto_approve_min, escalate_below)
    org = db.get_organization(organization_id) or {}
    settings: Any = org.get("settings") or org.get("settings_json") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except (ValueError, TypeError):
            settings = {}
    if not isinstance(settings, dict):
        settings = {}
    block = settings.get("routing_thresholds") or {}
    if not isinstance(block, dict):
        block = {}
    if auto_approve_min is not None:
        block["auto_approve_min"] = _clamp_threshold(
            auto_approve_min, default=_DEFAULT_AUTO_APPROVE_MIN,
        )
    if escalate_below is not None:
        block["escalate_below"] = _clamp_threshold(
            escalate_below, default=_DEFAULT_ESCALATE_BELOW,
        )
    if po_required_above is not None:
        block["po_required_above"] = _coerce_amount(po_required_above)
    settings["routing_thresholds"] = block
    db.update_organization(organization_id, settings=settings)
    return block


# ── Vendor-level overrides ────────────────────────────────────────


def get_vendor_threshold_overrides(
    db,
    organization_id: str,
    vendor_name: str,
) -> Dict[str, Any]:
    """Read manual operator overrides from
    ``vendor_profiles.metadata['threshold_override']``."""
    profile = None
    try:
        profile = db.get_vendor_profile(organization_id, vendor_name)
    except Exception:
        return {}
    if not profile:
        return {}
    meta = profile.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if not isinstance(meta, dict):
        return {}
    block = meta.get("threshold_override") or {}
    if not isinstance(block, dict):
        return {}
    return block


def set_vendor_threshold_overrides(
    db,
    organization_id: str,
    vendor_name: str,
    *,
    auto_approve_min: Optional[float] = None,
    escalate_below: Optional[float] = None,
    po_required_above: Optional[float] = None,
    clear: bool = False,
) -> Dict[str, Any]:
    """Persist per-vendor manual overrides. ``clear=True`` wipes the
    block entirely (revert to org default + learned)."""
    _validate_threshold_pair(auto_approve_min, escalate_below)
    profile = db.get_vendor_profile(organization_id, vendor_name)
    if not profile:
        raise ValueError(f"vendor_not_found:{vendor_name!r}")
    meta = profile.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    if clear:
        meta.pop("threshold_override", None)
        db.upsert_vendor_profile(organization_id, vendor_name, metadata=meta)
        return {}
    block = meta.get("threshold_override") or {}
    if not isinstance(block, dict):
        block = {}
    if auto_approve_min is not None:
        block["auto_approve_min"] = _clamp_threshold(
            auto_approve_min, default=_DEFAULT_AUTO_APPROVE_MIN,
        )
    if escalate_below is not None:
        block["escalate_below"] = _clamp_threshold(
            escalate_below, default=_DEFAULT_ESCALATE_BELOW,
        )
    if po_required_above is not None:
        block["po_required_above"] = _coerce_amount(po_required_above)
    meta["threshold_override"] = block
    db.upsert_vendor_profile(organization_id, vendor_name, metadata=meta)
    return block


# ── Resolver ──────────────────────────────────────────────────────


def resolve_thresholds(
    db,
    *,
    organization_id: str,
    vendor_name: Optional[str] = None,
) -> ThresholdResolution:
    """Walk the layered config and return effective thresholds for
    the given vendor.

    Resolution order per field:
      vendor manual override → vendor learned → org default → fallback
    """
    org_block = get_org_thresholds(db, organization_id)
    vendor_block: Dict[str, Any] = {}
    learned: Optional[float] = None
    if vendor_name:
        vendor_block = get_vendor_threshold_overrides(
            db, organization_id, vendor_name,
        )
        try:
            profile = db.get_vendor_profile(organization_id, vendor_name) or {}
            meta = profile.get("metadata") or {}
            if isinstance(meta, str):
                meta = json.loads(meta) if meta else {}
            raw = (meta or {}).get("learned_auto_approve_threshold")
            if raw is not None:
                learned = _clamp_threshold(
                    raw, default=_DEFAULT_AUTO_APPROVE_MIN,
                )
        except Exception:
            learned = None

    source_chain: Dict[str, str] = {}

    # auto_approve_min
    if "auto_approve_min" in vendor_block:
        auto_approve_min = _clamp_threshold(
            vendor_block["auto_approve_min"],
            default=_DEFAULT_AUTO_APPROVE_MIN,
        )
        source_chain["auto_approve_min"] = "vendor_override"
    elif learned is not None:
        auto_approve_min = learned
        source_chain["auto_approve_min"] = "vendor_learned"
    elif "auto_approve_min" in org_block:
        auto_approve_min = _clamp_threshold(
            org_block["auto_approve_min"],
            default=_DEFAULT_AUTO_APPROVE_MIN,
        )
        source_chain["auto_approve_min"] = "org_default"
    else:
        auto_approve_min = _DEFAULT_AUTO_APPROVE_MIN
        source_chain["auto_approve_min"] = "hardcoded_fallback"

    # escalate_below
    if "escalate_below" in vendor_block:
        escalate_below = _clamp_threshold(
            vendor_block["escalate_below"],
            default=_DEFAULT_ESCALATE_BELOW,
        )
        source_chain["escalate_below"] = "vendor_override"
    elif "escalate_below" in org_block:
        escalate_below = _clamp_threshold(
            org_block["escalate_below"],
            default=_DEFAULT_ESCALATE_BELOW,
        )
        source_chain["escalate_below"] = "org_default"
    else:
        escalate_below = _DEFAULT_ESCALATE_BELOW
        source_chain["escalate_below"] = "hardcoded_fallback"

    # po_required_above (None means feature disabled at this layer)
    if "po_required_above" in vendor_block:
        po_required_above = _coerce_amount(vendor_block["po_required_above"])
        source_chain["po_required_above"] = "vendor_override"
    elif "po_required_above" in org_block:
        po_required_above = _coerce_amount(org_block["po_required_above"])
        source_chain["po_required_above"] = "org_default"
    else:
        po_required_above = None
        source_chain["po_required_above"] = "unset"

    # Final consistency: vendor override may have set escalate_below
    # > new auto_approve_min when one came from a different layer.
    # Keep auto_approve_min strictly above by nudging up.
    if auto_approve_min <= escalate_below:
        auto_approve_min = min(_MAX_AUTO_APPROVE, escalate_below + 0.01)
        source_chain["auto_approve_min"] += "+nudged_above_escalate"

    return ThresholdResolution(
        organization_id=organization_id,
        vendor_name=vendor_name,
        auto_approve_min=auto_approve_min,
        escalate_below=escalate_below,
        po_required_above=po_required_above,
        source_chain=source_chain,
    )
