"""VendorRiskScoreService — Phase 2.4.

DESIGN_THESIS.md §3 lists ``risk_score`` as a first-class field on the
Vendor object. This service computes the score at read time from live
signals rather than storing it (a stored value goes stale the moment
an invoice posts, which defeats the point of a risk signal).

The score is an integer in [0, 100] where higher means higher risk.
The formula is intentionally transparent: each component contributes a
fixed number of points and the API response includes a breakdown so
clients can render an explanation tooltip.

Formula (v1 — iterate after production signals):

    Component                                    Points
    ─────────────────────────────────────────────────────
    New vendor (invoice_count == 0)                 +30
    Active IBAN change freeze                       +50
    Bank details changed in last 30 days            +15
    Override rate > 30%                             +20
    KYC never completed                             +15
    KYC stale (> 12 months old)                     +10
    Missing registration_number                      +5
    Missing vat_number                               +5
    Missing director_names                           +5

The score is clamped to [0, 100]. Components are additive — a new
vendor with an active freeze and no KYC can reach 100 quickly, which
is the correct signal.

This module is pure Python — no network I/O, no LLM calls. It takes
a VendorStore handle + vendor_name and returns a structured result
that the API layer serialises directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


MAX_SCORE = 100
STALE_KYC_DAYS = 365
RECENT_BANK_CHANGE_DAYS = 30
HIGH_OVERRIDE_RATE = 0.3


# Component weights — declared as module constants so tests can assert
# on them directly and so future tuning doesn't require code review
# plus prose spelunking.
WEIGHT_NEW_VENDOR = 30
WEIGHT_IBAN_FREEZE = 50
WEIGHT_RECENT_BANK_CHANGE = 15
WEIGHT_HIGH_OVERRIDE_RATE = 20
WEIGHT_KYC_MISSING = 15
WEIGHT_KYC_STALE = 10
WEIGHT_MISSING_REGISTRATION_NUMBER = 5
WEIGHT_MISSING_VAT_NUMBER = 5
WEIGHT_MISSING_DIRECTOR_NAMES = 5


@dataclass(frozen=True)
class RiskComponent:
    """A single contribution to the composite risk score."""

    code: str
    label: str
    points: int
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VendorRiskScore:
    """Structured risk-score result returned by the service."""

    score: int
    components: List[RiskComponent] = field(default_factory=list)
    computed_at: str = ""
    vendor_found: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "components": [
                {
                    "code": c.code,
                    "label": c.label,
                    "points": c.points,
                    "details": c.details,
                }
                for c in self.components
            ],
            "computed_at": self.computed_at,
            "vendor_found": self.vendor_found,
        }


class VendorRiskScoreService:
    """Compute a composite risk score for a vendor at read time."""

    def __init__(self, organization_id: str, db: Any = None) -> None:
        from clearledgr.core.database import get_db
        self.organization_id = organization_id
        self.db = db or get_db()

    def compute(self, vendor_name: str) -> VendorRiskScore:
        """Return a ``VendorRiskScore`` for ``vendor_name``.

        Returns ``score=0`` with ``vendor_found=False`` when the vendor
        doesn't exist in the database. Callers that need to distinguish
        "unknown vendor" from "low risk vendor" should check that flag.
        """
        now = datetime.now(timezone.utc)
        profile = self.db.get_vendor_profile(self.organization_id, vendor_name)
        if not profile:
            return VendorRiskScore(
                score=0,
                components=[],
                computed_at=now.isoformat(),
                vendor_found=False,
            )

        components: List[RiskComponent] = []

        # 1. New vendor
        invoice_count = int(profile.get("invoice_count") or 0)
        if invoice_count == 0:
            components.append(
                RiskComponent(
                    code="new_vendor",
                    label="Vendor has no posted invoices yet",
                    points=WEIGHT_NEW_VENDOR,
                    details={"invoice_count": invoice_count},
                )
            )

        # 2. Active IBAN change freeze (Phase 2.1.b)
        if profile.get("iban_change_pending"):
            components.append(
                RiskComponent(
                    code="iban_change_freeze_active",
                    label="IBAN change freeze in progress",
                    points=WEIGHT_IBAN_FREEZE,
                    details={
                        "iban_change_detected_at": profile.get(
                            "iban_change_detected_at"
                        ),
                    },
                )
            )

        # 3. Recent bank-details change (within the last 30 days)
        bank_changed_at = profile.get("bank_details_changed_at")
        if bank_changed_at:
            days = _days_since(bank_changed_at, now)
            if days is not None and 0 <= days <= RECENT_BANK_CHANGE_DAYS:
                components.append(
                    RiskComponent(
                        code="recent_bank_change",
                        label=(
                            f"Bank details changed {days} day(s) ago "
                            f"(threshold: {RECENT_BANK_CHANGE_DAYS})"
                        ),
                        points=WEIGHT_RECENT_BANK_CHANGE,
                        details={"days_since_change": days},
                    )
                )

        # 4. High human override rate
        override_rate = float(profile.get("approval_override_rate") or 0.0)
        if override_rate > HIGH_OVERRIDE_RATE:
            components.append(
                RiskComponent(
                    code="high_override_rate",
                    label=(
                        f"Human override rate {override_rate:.0%} exceeds "
                        f"threshold {HIGH_OVERRIDE_RATE:.0%}"
                    ),
                    points=WEIGHT_HIGH_OVERRIDE_RATE,
                    details={"override_rate": override_rate},
                )
            )

        # 5. KYC missing / stale
        kyc_date = profile.get("kyc_completion_date")
        if not kyc_date:
            components.append(
                RiskComponent(
                    code="kyc_missing",
                    label="KYC has never been completed for this vendor",
                    points=WEIGHT_KYC_MISSING,
                )
            )
        else:
            days = _days_since(kyc_date, now)
            if days is not None and days > STALE_KYC_DAYS:
                components.append(
                    RiskComponent(
                        code="kyc_stale",
                        label=(
                            f"KYC is {days} day(s) old "
                            f"(stale threshold: {STALE_KYC_DAYS})"
                        ),
                        points=WEIGHT_KYC_STALE,
                        details={"days_since_kyc": days},
                    )
                )

        # 6. Missing individual KYC fields
        if not (profile.get("registration_number") or "").strip():
            components.append(
                RiskComponent(
                    code="missing_registration_number",
                    label="Vendor has no registration number on file",
                    points=WEIGHT_MISSING_REGISTRATION_NUMBER,
                )
            )
        if not (profile.get("vat_number") or "").strip():
            components.append(
                RiskComponent(
                    code="missing_vat_number",
                    label="Vendor has no VAT number on file",
                    points=WEIGHT_MISSING_VAT_NUMBER,
                )
            )
        director_names = profile.get("director_names") or []
        if not isinstance(director_names, list) or not director_names:
            components.append(
                RiskComponent(
                    code="missing_director_names",
                    label="Vendor has no director names on file",
                    points=WEIGHT_MISSING_DIRECTOR_NAMES,
                )
            )

        total = sum(c.points for c in components)
        clamped = min(MAX_SCORE, max(0, total))

        return VendorRiskScore(
            score=clamped,
            components=components,
            computed_at=now.isoformat(),
            vendor_found=True,
        )


def _days_since(iso_value: Any, now: Optional[datetime] = None) -> Optional[int]:
    """Return integer days between ``iso_value`` and ``now``, or None on parse failure."""
    if not iso_value:
        return None
    try:
        value_str = str(iso_value).strip()
        if not value_str:
            return None
        # Accept bare dates and ISO datetimes
        if "T" not in value_str and len(value_str) == 10:
            value_str = f"{value_str}T00:00:00+00:00"
        parsed = datetime.fromisoformat(value_str.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    delta = now - parsed
    return delta.days


def get_vendor_risk_score_service(
    organization_id: str, db: Any = None
) -> VendorRiskScoreService:
    """Factory mirror of the other service modules."""
    return VendorRiskScoreService(organization_id, db=db)
