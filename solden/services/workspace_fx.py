"""Workspace FX conversion — Module 9 (multi-currency reporting).

Distinct from ``services.fx_conversion`` (which calls the ECB free
API for ad-hoc rate fetches in fraud-controls + invoice-validation).
This module reads org-stored rates from the ``fx_rates`` table —
operator-entered or ERP-sourced — per spec §304 ("Currency conversion
uses ERP-provided rates, not third-party rates").

The reporting layer aggregates invoice amounts across currencies. To
keep cross-entity totals meaningful, every line is converted to the
org's *functional currency*
(``organizations.settings_json["functional_currency"]``, default USD)
before summation.

Conversion lookup chain (in order):

  1. Identity — from == to → rate 1.0.
  2. Direct — fx_rates row for (from, to) on or before the as-of date.
  3. Inverse — fx_rates row for (to, from) → 1 / rate.
  4. Triangulation through USD — (from, USD) and (USD, to) at the
     same as-of date, multiplied. Only attempted when neither leg is
     already USD.
  5. None — no rate found. Caller decides what to do (the reports
     skip the line and increment a `currency_unconverted` count;
     the operator sees the gap on the report page and can add the
     manual rate.)

Why this lookup order:
  - Identity short-circuits the common case of "same currency".
  - Direct beats inverse beats triangulation because every step
    introduces a small rounding loss.
  - USD as the hub covers the typical mid-market shape (most
    customers' functional currency is USD or has a USD path).

The service never raises across the public boundary; failure modes
return ``None`` and the caller sees an explicit "no rate" path.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, getcontext
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 28 digits of decimal precision is plenty for FX. Default Decimal
# precision is 28 already; setting it explicitly so no future caller
# silently changes the math.
getcontext().prec = 28


# Hub currency for triangulation. USD by convention.
_TRIANGULATION_HUB = "USD"


@dataclass
class ConversionResult:
    converted_amount: float
    rate_used: float
    path: str  # 'identity' | 'direct' | 'inverse' | 'triangulated' | 'none'
    from_currency: str
    to_currency: str
    as_of_date: str

    def to_dict(self) -> dict:
        return {
            "converted_amount": self.converted_amount,
            "rate_used": self.rate_used,
            "path": self.path,
            "from_currency": self.from_currency,
            "to_currency": self.to_currency,
            "as_of_date": self.as_of_date,
        }


def _normalize_date(value: Any) -> str:
    if value is None:
        return date.today().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text[:10] if text else date.today().isoformat()


def convert(
    db: Any,
    *,
    organization_id: str,
    amount: float,
    from_currency: str,
    to_currency: str,
    as_of_date: Optional[str] = None,
) -> Optional[ConversionResult]:
    """Convert ``amount`` from ``from_currency`` to ``to_currency`` using
    the lookup chain above. Returns None when no rate is available.
    """
    from_ccy = (from_currency or "").strip().upper()
    to_ccy = (to_currency or "").strip().upper()
    if not from_ccy or not to_ccy:
        return None
    as_of = _normalize_date(as_of_date)

    # 1. Identity
    if from_ccy == to_ccy:
        return ConversionResult(
            converted_amount=float(amount or 0),
            rate_used=1.0,
            path="identity",
            from_currency=from_ccy,
            to_currency=to_ccy,
            as_of_date=as_of,
        )

    # 2. Direct
    direct = db.find_fx_rate(organization_id, from_ccy, to_ccy, as_of)
    if direct:
        rate = Decimal(str(direct["rate"]))
        return _make_result(amount, rate, "direct", from_ccy, to_ccy, as_of)

    # 3. Inverse
    inverse = db.find_fx_rate(organization_id, to_ccy, from_ccy, as_of)
    if inverse:
        try:
            rate = Decimal(1) / Decimal(str(inverse["rate"]))
        except (ZeroDivisionError, ValueError):
            rate = None
        if rate is not None:
            return _make_result(amount, rate, "inverse", from_ccy, to_ccy, as_of)

    # 4. Triangulation through USD
    if from_ccy != _TRIANGULATION_HUB and to_ccy != _TRIANGULATION_HUB:
        leg_in_rate = _direct_or_inverse_rate(
            db, organization_id, from_ccy, _TRIANGULATION_HUB, as_of,
        )
        leg_out_rate = _direct_or_inverse_rate(
            db, organization_id, _TRIANGULATION_HUB, to_ccy, as_of,
        )
        if leg_in_rate is not None and leg_out_rate is not None:
            rate = leg_in_rate * leg_out_rate
            return _make_result(amount, rate, "triangulated", from_ccy, to_ccy, as_of)

    return None


def _make_result(
    amount: Any, rate: Decimal, path: str,
    from_ccy: str, to_ccy: str, as_of: str,
) -> ConversionResult:
    converted = (Decimal(str(amount or 0)) * rate).quantize(Decimal("0.01"))
    return ConversionResult(
        converted_amount=float(converted),
        rate_used=float(rate),
        path=path,
        from_currency=from_ccy,
        to_currency=to_ccy,
        as_of_date=as_of,
    )


def _direct_or_inverse_rate(
    db: Any, organization_id: str,
    from_ccy: str, to_ccy: str, as_of: str,
) -> Optional[Decimal]:
    """Resolve a single conversion leg via direct or inverse lookup."""
    direct = db.find_fx_rate(organization_id, from_ccy, to_ccy, as_of)
    if direct:
        return Decimal(str(direct["rate"]))
    inverse = db.find_fx_rate(organization_id, to_ccy, from_ccy, as_of)
    if inverse:
        try:
            return Decimal(1) / Decimal(str(inverse["rate"]))
        except (ZeroDivisionError, ValueError):
            return None
    return None


def get_functional_currency(db: Any, organization_id: str) -> str:
    """Org-level functional currency. Stored on
    organizations.settings_json["functional_currency"] with a USD
    default.
    """
    try:
        org = db.get_organization(organization_id) or {}
        raw_settings = org.get("settings_json") or org.get("settings") or {}
        if isinstance(raw_settings, str):
            try:
                raw_settings = json.loads(raw_settings)
            except (ValueError, TypeError):
                raw_settings = {}
        if isinstance(raw_settings, dict):
            value = raw_settings.get("functional_currency")
            if isinstance(value, str) and len(value.strip()) == 3:
                return value.strip().upper()
    except Exception as exc:
        logger.debug(
            "[workspace_fx] functional currency load failed for %s: %s",
            organization_id, exc,
        )
    return "USD"
