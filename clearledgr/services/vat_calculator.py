"""VAT calculation + tax-treatment derivation (Wave 3 / E2).

Produces the canonical VAT split for one bill given:

  * The org's home country (where the buyer is registered)
  * The seller's country (from the bill / vendor profile)
  * The seller's VAT id (intra-EU B2B requires both sides VAT-registered)
  * The bill's gross amount
  * Optional override VAT rate / code (operator forces a specific
    treatment, e.g. domestic exempt for medical goods)

Treatments:

  domestic        — buyer & seller in same country.
                    JE: Dr expense net, Dr VAT input, Cr AP gross.
                    box6 += net, box4 += vat, box7 += net.
  reverse_charge  — intra-EU B2B + seller has VAT id, buyer ≠ seller.
                    JE: Dr expense gross, Cr AP gross. Self-assess:
                    Dr VAT input vat, Cr VAT output vat (net cash zero).
                    box1 += vat (output), box4 += vat (reclaim),
                    box7 += net, box9 += net (acquisitions).
  zero_rated      — UK post-Brexit cross-border, EU export to
                    non-EU, certain exempt goods.
                    JE: Dr expense gross, Cr AP gross.
                    box7 += net (only).
  exempt          — domestic exempt supply (medical, education).
                    JE: Dr expense gross, Cr AP gross. box7 += net.
  out_of_scope    — services with place-of-supply outside the org's
                    jurisdiction (e.g. SaaS consumed in another
                    country). No VAT row contribution.

The calculator is purely deterministic — no DB writes. Caller stores
the result on ``ap_items`` via ``update_ap_item``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Optional

from clearledgr.services.tax_compliance import (
    EU_COUNTRIES,
    STANDARD_VAT_RATES,
)

logger = logging.getLogger(__name__)


_TWO_PLACES = Decimal("0.01")
_THREE_PLACES = Decimal("0.001")


@dataclass
class VATResult:
    """Full VAT picture for one bill, ready to land on ap_items."""

    gross_amount: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    vat_rate: Decimal           # in percent (e.g. 20.0 for UK)
    vat_code: str
    tax_treatment: str
    bill_country: Optional[str]
    home_country: Optional[str]
    note: Optional[str] = None

    def to_ap_item_kwargs(self) -> Dict[str, Any]:
        """Subset of fields directly writable via ``update_ap_item``."""
        return {
            "net_amount": self.net_amount,
            "vat_amount": self.vat_amount,
            "vat_rate": self.vat_rate,
            "vat_code": self.vat_code,
            "tax_treatment": self.tax_treatment,
            "bill_country": self.bill_country,
        }


def _money(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    try:
        return Decimal(str(value)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError(f"amount must be numeric: {value!r}")


def _rate(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0.000")
    try:
        return Decimal(str(value)).quantize(_THREE_PLACES, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError(f"rate must be numeric: {value!r}")


def _split_gross(gross: Decimal, rate_percent: Decimal) -> tuple[Decimal, Decimal]:
    """Given gross + rate, return (net, vat) rounded to 2dp.

    Rate of 0 → net=gross, vat=0.
    """
    if rate_percent == 0:
        return gross, Decimal("0.00")
    factor = (Decimal("100") + rate_percent) / Decimal("100")
    net = (gross / factor).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    vat = (gross - net).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    return net, vat


def calculate_vat(
    *,
    gross_amount: Any,
    home_country: Optional[str],
    bill_country: Optional[str],
    seller_has_vat_id: bool = False,
    rate_override: Optional[Any] = None,
    treatment_override: Optional[str] = None,
) -> VATResult:
    """Derive the canonical VAT split for one bill.

    All amounts are returned as Decimal so callers don't lose precision
    on the JE round-trip.
    """
    gross = _money(gross_amount)
    home = (home_country or "").strip().upper() or None
    bill = (bill_country or "").strip().upper() or None

    # Operator override path: the operator told us exactly what
    # treatment to apply. Honour it without re-deriving.
    if treatment_override:
        treatment = treatment_override.strip().lower()
    else:
        treatment = _derive_treatment(
            home=home, bill=bill, seller_has_vat_id=seller_has_vat_id,
        )

    # Resolve rate
    if rate_override is not None:
        rate = _rate(rate_override)
    elif treatment == "domestic" and home:
        rate = _rate(STANDARD_VAT_RATES.get(home, 0.0))
    else:
        # zero_rated / exempt / out_of_scope / reverse_charge:
        # the rate the OPERATOR sees on the bill is 0 (or for RC, the
        # net is the full gross). For RC we still need a rate to
        # self-assess input + output VAT against — fall back to the
        # buyer's domestic rate.
        if treatment == "reverse_charge" and home:
            rate = _rate(STANDARD_VAT_RATES.get(home, 0.0))
        else:
            rate = Decimal("0.000")

    # Compute net + vat. For RC the "headline" net is the full gross
    # (no VAT is on the invoice). The vat_amount column carries the
    # SELF-ASSESSED amount so the VAT return boxes 1 + 4 balance.
    note: Optional[str] = None
    if treatment == "domestic":
        net, vat = _split_gross(gross, rate)
        code = "T1" if rate > 0 else "T0"
    elif treatment == "reverse_charge":
        net = gross
        # vat = net * rate%
        vat = (
            (net * rate / Decimal("100"))
            .quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        )
        code = "RC"
        note = (
            "B2B intra-EU reverse charge. Buyer self-accounts; net cash "
            "to vendor = gross."
        )
    elif treatment == "zero_rated":
        net = gross
        vat = Decimal("0.00")
        code = "T0"
    elif treatment == "exempt":
        net = gross
        vat = Decimal("0.00")
        code = "T2"
    elif treatment == "out_of_scope":
        net = gross
        vat = Decimal("0.00")
        code = "OO"
    else:
        # Unknown treatment — fail closed: net = gross, no VAT.
        net = gross
        vat = Decimal("0.00")
        code = "OO"
        note = f"unknown_treatment:{treatment}"

    return VATResult(
        gross_amount=gross,
        net_amount=net,
        vat_amount=vat,
        vat_rate=rate,
        vat_code=code,
        tax_treatment=treatment,
        bill_country=bill,
        home_country=home,
        note=note,
    )


def _derive_treatment(
    *,
    home: Optional[str],
    bill: Optional[str],
    seller_has_vat_id: bool,
) -> str:
    """Default policy for tax_treatment given bill country relative
    to the org's home country."""
    if not home or not bill:
        # Without a home country we cannot disambiguate — default to
        # domestic so the operator at least sees the bill go through
        # with the configured rate. Misconfigured orgs see the same
        # treatment, which is the safer of two weak defaults.
        return "domestic"
    if home == bill:
        return "domestic"
    if home in EU_COUNTRIES and bill in EU_COUNTRIES:
        # Intra-EU B2B with seller VAT-registered → reverse charge.
        # Without a seller VAT id, fall through to zero_rated (the
        # seller is treating us as a non-business consumer; we
        # shouldn't self-assess on their behalf).
        return "reverse_charge" if seller_has_vat_id else "zero_rated"
    # Cross-border outside EU (UK ↔ EU post-Brexit, EU ↔ non-EU).
    # Treated as zero-rated for VAT purposes; customs duty + import
    # VAT handled separately.
    return "zero_rated"


# ── Org-level config helpers ───────────────────────────────────────


def get_org_home_country(db, organization_id: str) -> Optional[str]:
    """Read the org's home country from settings_json["tax"]["home_country"].

    Falls back to None — the calculator's safe default (treat as
    domestic with no VAT) keeps existing tenants working.
    """
    try:
        org = db.get_organization(organization_id) or {}
    except Exception:
        return None
    settings: Any = org.get("settings") or org.get("settings_json") or {}
    if isinstance(settings, str):
        import json
        try:
            settings = json.loads(settings)
        except (ValueError, TypeError):
            return None
    if not isinstance(settings, dict):
        return None
    tax = settings.get("tax") or {}
    if not isinstance(tax, dict):
        return None
    home = (tax.get("home_country") or "").strip().upper()
    return home or None
