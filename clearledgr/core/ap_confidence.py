"""Shared AP extraction confidence gating helpers.

Implements launch-critical server-side checks for low-confidence critical fields.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


DEFAULT_CRITICAL_FIELD_CONFIDENCE_THRESHOLD = 0.95
DEFAULT_CRITICAL_FIELDS = ("vendor", "amount", "invoice_number", "due_date")

_FIELD_ALIASES = {
    "vendor": ("vendor", "vendor_name"),
    "amount": ("amount", "total", "invoice_total"),
    "invoice_number": ("invoice_number", "invoiceNo", "invoice_num", "number"),
    "due_date": ("due_date", "dueDate", "payment_due_date"),
}


def normalize_confidence_value(value: Any) -> Optional[float]:
    """Normalize confidence values to ``0.0-1.0``.

    Accepts either ratios (``0.95``) or percentages (``95``).
    """
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num < 0:
        return None
    if num > 1.0:
        if num <= 100.0:
            num = num / 100.0
        else:
            return None
    if num < 0:
        return 0.0
    if num > 1.0:
        return 1.0
    return num


def coerce_confidence_map(raw: Any) -> Dict[str, float]:
    """Normalize a field-confidence mapping to canonical keys + ``0.0-1.0`` values."""
    if not isinstance(raw, dict):
        return {}
    values: Dict[str, float] = {}
    for canonical_field, aliases in _FIELD_ALIASES.items():
        for key in aliases:
            if key not in raw:
                continue
            normalized = normalize_confidence_value(raw.get(key))
            if normalized is not None:
                values[canonical_field] = normalized
                break
    return values


def extract_field_confidences(raw: Any) -> Dict[str, float]:
    """Extract field confidences from common payload shapes."""
    if isinstance(raw, dict):
        for key in ("field_confidences", "fieldConfidences", "confidence_by_field", "confidenceByField"):
            nested = raw.get(key)
            normalized = coerce_confidence_map(nested)
            if normalized:
                return normalized
        return coerce_confidence_map(raw)
    return {}


def _field_value(field: str, values: Dict[str, Any]) -> Any:
    aliases = _FIELD_ALIASES.get(field, (field,))
    for key in aliases:
        if key in values:
            return values.get(key)
    return None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def evaluate_critical_field_confidence(
    *,
    overall_confidence: Any,
    field_values: Optional[Dict[str, Any]] = None,
    field_confidences: Any = None,
    threshold: float = DEFAULT_CRITICAL_FIELD_CONFIDENCE_THRESHOLD,
    critical_fields: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Evaluate whether critical extracted fields require manual review."""
    threshold_norm = normalize_confidence_value(threshold)
    if threshold_norm is None:
        threshold_norm = DEFAULT_CRITICAL_FIELD_CONFIDENCE_THRESHOLD

    values = dict(field_values or {})
    explicit_field_conf = extract_field_confidences(field_confidences)
    overall_norm = normalize_confidence_value(overall_confidence)

    blockers: List[Dict[str, Any]] = []
    evaluated_fields: List[str] = []
    effective_confidences: Dict[str, float] = {}

    for field in list(critical_fields or DEFAULT_CRITICAL_FIELDS):
        raw_value = _field_value(field, values)
        if not _has_value(raw_value):
            continue
        evaluated_fields.append(field)

        confidence = explicit_field_conf.get(field)
        if confidence is None:
            confidence = overall_norm if overall_norm is not None else 0.0

        effective_confidences[field] = confidence
        if confidence < threshold_norm:
            blockers.append(
                {
                    "field": field,
                    "confidence": round(confidence, 4),
                    "confidence_pct": round(confidence * 100),
                    "threshold": round(threshold_norm, 4),
                    "threshold_pct": round(threshold_norm * 100),
                    "severity": "high",
                    "reason": "critical_field_low_confidence",
                }
            )

    return {
        "threshold": round(threshold_norm, 4),
        "threshold_pct": round(threshold_norm * 100),
        "overall_confidence": overall_norm,
        "evaluated_fields": evaluated_fields,
        "field_confidences": effective_confidences,
        "confidence_blockers": blockers,
        "requires_field_review": bool(blockers),
    }

