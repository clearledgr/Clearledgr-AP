"""Architectural fraud controls for Clearledgr AP.

Per DESIGN_THESIS.md §8, fraud controls are *architectural*, not
configurational. They cannot be disabled by the AP Manager. Their numeric
parameters can be modified — but only by the CFO role, and every
modification is logged to the audit trail.

This module owns the configuration, defaults, loading, saving, and audit
emission for every Group A fraud-control primitive:

1. ``payment_ceiling``           — max invoice amount (in base currency)
                                    above which auto-approval is blocked
2. ``vendor_velocity_max_per_week`` — max invoices per vendor per 7 days
3. ``first_payment_dormancy_days``  — vendor silent for N days re-triggers
                                    first-payment hold
4. duplicate prevention          — always-on, no config (already enforced
                                    by invoice_validation._evaluate_…)
5. prompt injection rejection    — always-on, no config (enforced by
                                    prompt_guard.detect_injection)

The check CODE PATHS themselves are not disableable — only the numeric
thresholds are. An organization that wants to "disable" payment ceiling
enforcement must explicitly raise the ceiling to a high value via the
``/fraud-controls`` API as a CFO-role user, and that change is logged.

Usage:
    config = load_fraud_controls(org_id, db)
    if invoice.amount_base > config.payment_ceiling:
        add_reason("payment_ceiling_exceeded", ...)

    save_fraud_controls(org_id, new_config, modified_by=user.user_id, db=db)
    # → writes settings_json["fraud_controls"]
    # → appends ap_audit_events row with event_type="fraud_control_modified"
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults — these ship enabled on Day 1. Orgs that need different values
# raise/lower them via the /fraud-controls API.
# ---------------------------------------------------------------------------

DEFAULT_PAYMENT_CEILING = 10_000.0  # in org base currency (default USD)
DEFAULT_VENDOR_VELOCITY_MAX_PER_WEEK = 10
DEFAULT_FIRST_PAYMENT_DORMANCY_DAYS = 180


@dataclass(frozen=True)
class FraudControlConfig:
    """Immutable snapshot of an organization's fraud-control parameters.

    Frozen because callers should never mutate — always round-trip through
    ``save_fraud_controls`` so the audit trail captures every change.
    """

    payment_ceiling: float = DEFAULT_PAYMENT_CEILING
    vendor_velocity_max_per_week: int = DEFAULT_VENDOR_VELOCITY_MAX_PER_WEEK
    first_payment_dormancy_days: int = DEFAULT_FIRST_PAYMENT_DORMANCY_DAYS

    # The org's base currency — payment_ceiling is denominated in this.
    # Resolved from organization settings (locale.default_currency) at
    # load time; stored here so consumers don't need to re-resolve.
    base_currency: str = "USD"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for settings_json storage or API responses."""
        return asdict(self)

    @classmethod
    def from_dict(
        cls,
        data: Optional[Dict[str, Any]],
        *,
        base_currency: str = "USD",
    ) -> "FraudControlConfig":
        """Build a config from a settings_json sub-dict.

        Unknown keys are ignored. Missing keys fall back to defaults.
        Invalid values fall back to defaults with a warning log.
        """
        data = data or {}

        def _float(key: str, default: float) -> float:
            try:
                value = float(data.get(key, default))
                if value < 0:
                    logger.warning(
                        "[FraudControls] %s cannot be negative (got %s); using default %s",
                        key, value, default,
                    )
                    return default
                return value
            except (TypeError, ValueError):
                logger.warning(
                    "[FraudControls] %s is not numeric (got %r); using default %s",
                    key, data.get(key), default,
                )
                return default

        def _int(key: str, default: int) -> int:
            try:
                value = int(data.get(key, default))
                if value < 0:
                    logger.warning(
                        "[FraudControls] %s cannot be negative (got %s); using default %s",
                        key, value, default,
                    )
                    return default
                return value
            except (TypeError, ValueError):
                logger.warning(
                    "[FraudControls] %s is not an integer (got %r); using default %s",
                    key, data.get(key), default,
                )
                return default

        return cls(
            payment_ceiling=_float("payment_ceiling", DEFAULT_PAYMENT_CEILING),
            vendor_velocity_max_per_week=_int(
                "vendor_velocity_max_per_week", DEFAULT_VENDOR_VELOCITY_MAX_PER_WEEK
            ),
            first_payment_dormancy_days=_int(
                "first_payment_dormancy_days", DEFAULT_FIRST_PAYMENT_DORMANCY_DAYS
            ),
            base_currency=(str(data.get("base_currency") or base_currency or "USD")).upper(),
        )


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def _resolve_base_currency(org_settings: Dict[str, Any]) -> str:
    """Resolve the organization's base currency from its settings.

    Priority:
      1. settings_json["fraud_controls"]["base_currency"] (explicit override)
      2. settings_json["locale"]["default_currency"] (standard org locale)
      3. settings_json["default_currency"] (legacy flat shape)
      4. "USD" (fallback)
    """
    fraud_section = org_settings.get("fraud_controls") or {}
    if isinstance(fraud_section, dict) and fraud_section.get("base_currency"):
        return str(fraud_section["base_currency"]).upper()

    locale_section = org_settings.get("locale") or {}
    if isinstance(locale_section, dict) and locale_section.get("default_currency"):
        return str(locale_section["default_currency"]).upper()

    flat = org_settings.get("default_currency")
    if flat:
        return str(flat).upper()

    return "USD"


def _load_org_settings(org_id: str, db: Any) -> Dict[str, Any]:
    """Read the organization's settings dict (parsed from settings_json)."""
    try:
        org = db.get_organization(org_id)
    except Exception as exc:
        logger.warning("[FraudControls] Could not load org %s: %s", org_id, exc)
        return {}
    if not org:
        return {}
    settings = org.get("settings") or org.get("settings_json") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except json.JSONDecodeError:
            logger.warning(
                "[FraudControls] settings_json for org %s is not valid JSON", org_id
            )
            settings = {}
    return settings if isinstance(settings, dict) else {}


def load_fraud_controls(org_id: str, db: Any) -> FraudControlConfig:
    """Load the fraud-control config for an organization.

    Always returns a valid config — defaults are used for any missing or
    invalid fields. This is the canonical read path for every consumer
    (validation gate, cross-invoice analysis, API, etc).
    """
    settings = _load_org_settings(org_id, db)
    base_currency = _resolve_base_currency(settings)
    raw = settings.get("fraud_controls")
    return FraudControlConfig.from_dict(raw, base_currency=base_currency)


def save_fraud_controls(
    org_id: str,
    config: FraudControlConfig,
    *,
    modified_by: str,
    db: Any,
    correlation_id: Optional[str] = None,
) -> FraudControlConfig:
    """Persist a new fraud-control config + emit an audit event.

    ``modified_by`` should be the CFO/owner user_id performing the change.
    Role gating is enforced at the API boundary (``require_cfo_user``), not
    here — this function trusts the caller. The audit event is emitted
    unconditionally whether or not the values actually changed, so the
    timeline shows intent-to-modify as well as actual deltas.
    """
    previous = load_fraud_controls(org_id, db)

    settings = _load_org_settings(org_id, db)
    settings["fraud_controls"] = config.to_dict()

    try:
        db.update_organization(org_id, settings=settings)
    except Exception as exc:
        logger.error(
            "[FraudControls] Failed to persist fraud_controls for org %s: %s",
            org_id, exc,
        )
        raise

    # Audit event — reuse the existing ap_audit_events store with a distinct
    # entity_type so SOC/compliance queries can filter cleanly.
    diff = _diff_configs(previous, config)
    try:
        db.append_ap_audit_event(
            {
                "ap_item_id": "",  # not an invoice-scoped event
                "event_type": "fraud_control_modified",
                "actor_type": "user",
                "actor_id": modified_by,
                "reason": (
                    "Fraud control parameters updated"
                    if diff
                    else "Fraud control parameters re-saved (no value changes)"
                ),
                "metadata": {
                    "entity_type": "fraud_control",
                    "entity_id": org_id,
                    "previous": previous.to_dict(),
                    "current": config.to_dict(),
                    "diff": diff,
                    "correlation_id": correlation_id,
                    "modified_at": datetime.now(timezone.utc).isoformat(),
                },
                "organization_id": org_id,
                "source": "fraud_controls_api",
            }
        )
    except Exception as audit_exc:
        # Fail the whole save if the audit write fails — "no silent
        # modifications" is an architectural guarantee.
        logger.error(
            "[FraudControls] Audit event write failed for org %s: %s",
            org_id, audit_exc,
        )
        raise

    return config


def _diff_configs(
    before: FraudControlConfig, after: FraudControlConfig
) -> List[Dict[str, Any]]:
    """Return a list of field-level deltas between two configs."""
    before_dict = before.to_dict()
    after_dict = after.to_dict()
    deltas: List[Dict[str, Any]] = []
    for key in sorted(set(before_dict.keys()) | set(after_dict.keys())):
        if before_dict.get(key) != after_dict.get(key):
            deltas.append(
                {
                    "field": key,
                    "before": before_dict.get(key),
                    "after": after_dict.get(key),
                }
            )
    return deltas


# ---------------------------------------------------------------------------
# Invoice-amount normalization — converts an invoice's amount into the
# organization's base currency so it can be compared to payment_ceiling.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CeilingCheckResult:
    """Result of converting an invoice amount into base currency for ceiling comparison."""

    exceeds_ceiling: bool
    invoice_amount: float
    invoice_currency: str
    base_currency: str
    converted_amount: Optional[float]  # None iff conversion failed
    rate: Optional[float]
    fx_unavailable: bool
    ceiling: float


def evaluate_payment_ceiling(
    invoice_amount: float,
    invoice_currency: str,
    config: FraudControlConfig,
) -> CeilingCheckResult:
    """Check whether an invoice amount exceeds the payment ceiling.

    Converts to base currency via ``fx_conversion.convert``. On FX
    failure (unavailable pair, network error) returns
    ``fx_unavailable=True`` and ``exceeds_ceiling=True`` — fail-closed
    per DESIGN_THESIS.md §8 "better to hold than to overpay."
    """
    from clearledgr.services.fx_conversion import convert

    invoice_currency = (invoice_currency or config.base_currency).upper()
    base_currency = (config.base_currency or "USD").upper()

    if invoice_currency == base_currency:
        converted = float(invoice_amount or 0)
        return CeilingCheckResult(
            exceeds_ceiling=converted > config.payment_ceiling,
            invoice_amount=float(invoice_amount or 0),
            invoice_currency=invoice_currency,
            base_currency=base_currency,
            converted_amount=converted,
            rate=1.0,
            fx_unavailable=False,
            ceiling=config.payment_ceiling,
        )

    try:
        fx = convert(float(invoice_amount or 0), invoice_currency, base_currency)
    except Exception as exc:
        logger.warning(
            "[FraudControls] FX conversion raised for %s→%s: %s",
            invoice_currency, base_currency, exc,
        )
        fx = {"converted_amount": None, "rate": None, "source": "error"}

    converted = fx.get("converted_amount")
    rate = fx.get("rate")

    if converted is None:
        # Fail closed: cannot verify, must block.
        return CeilingCheckResult(
            exceeds_ceiling=True,
            invoice_amount=float(invoice_amount or 0),
            invoice_currency=invoice_currency,
            base_currency=base_currency,
            converted_amount=None,
            rate=None,
            fx_unavailable=True,
            ceiling=config.payment_ceiling,
        )

    return CeilingCheckResult(
        exceeds_ceiling=float(converted) > config.payment_ceiling,
        invoice_amount=float(invoice_amount or 0),
        invoice_currency=invoice_currency,
        base_currency=base_currency,
        converted_amount=float(converted),
        rate=float(rate) if rate is not None else None,
        fx_unavailable=False,
        ceiling=config.payment_ceiling,
    )
