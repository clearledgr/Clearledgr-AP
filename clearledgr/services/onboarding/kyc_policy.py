"""KYC policy tiers — vendor-onboarding-spec §4.2.

The KYC action set the agent dispatches for a vendor is configurable
per workspace. The spec defines three tiers:

  completeness
      Document completeness only. No external provider calls. Cheapest
      and fastest onboarding; appropriate for low-risk vendors or
      workspaces that run their own compliance processes downstream.

  basic
      Completeness + company registry lookup + sanctions screening
      (OFAC / UN / EU / UK HM Treasury minimum). The default for most
      mid-market customers.

  full
      Basic + PEP screening + adverse media + UBO resolution to the
      depth the configured KYC provider supports. Required for
      enterprise customers with internal compliance programs.

The tier is stored on the organization's ``settings_json`` under
``onboarding.kyc_tier``. ``resolve_kyc_tier`` reads that field with
``basic`` as the safe default if nothing is configured — basic is
the right default for a finance product because it catches the
sanctions hit cases that are P0 compliance events and keeps the flow
fast enough for typical AP onboarding cadence.
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class KYCPolicyTier(str, Enum):
    """Canonical KYC tier names — vendor-onboarding-spec §4.2."""

    COMPLETENESS = "completeness"
    BASIC = "basic"
    FULL = "full"


# Default tier when a workspace has not explicitly configured one.
# Basic is the right default because it catches sanctions hits — a
# finance product that onboards a vendor without at least a sanctions
# screen has a compliance exposure most customers would not accept.
DEFAULT_KYC_TIER = KYCPolicyTier.BASIC


def resolve_kyc_tier(organization_id: str, db: Any = None) -> KYCPolicyTier:
    """Resolve the KYC tier for a workspace from its org settings.

    Reads ``settings_json["onboarding"]["kyc_tier"]`` on the
    organizations row and maps the value to a :class:`KYCPolicyTier`.
    Falls back to :data:`DEFAULT_KYC_TIER` if the field is missing,
    unparseable, or names a tier that does not exist.
    """
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    try:
        org = db.get_organization(organization_id) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[kyc_policy] get_organization failed for %s: %s — using default %s",
            organization_id, exc, DEFAULT_KYC_TIER.value,
        )
        return DEFAULT_KYC_TIER

    settings: Any = org.get("settings") or org.get("settings_json") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except (ValueError, TypeError):
            settings = {}
    if not isinstance(settings, dict):
        return DEFAULT_KYC_TIER

    onboarding = settings.get("onboarding") or {}
    if not isinstance(onboarding, dict):
        return DEFAULT_KYC_TIER

    raw = str(onboarding.get("kyc_tier") or "").strip().lower()
    if not raw:
        return DEFAULT_KYC_TIER
    try:
        return KYCPolicyTier(raw)
    except ValueError:
        logger.warning(
            "[kyc_policy] org %s has unknown kyc_tier %r — using default %s",
            organization_id, raw, DEFAULT_KYC_TIER.value,
        )
        return DEFAULT_KYC_TIER
