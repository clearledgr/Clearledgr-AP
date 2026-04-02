"""Shared AP extraction confidence gating helpers.

Implements launch-critical server-side checks for low-confidence critical fields.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence


DEFAULT_CRITICAL_FIELD_CONFIDENCE_THRESHOLD = 0.95
DEFAULT_CRITICAL_FIELDS = ("vendor", "amount", "invoice_number", "due_date")

_FIELD_ALIASES = {
    "vendor": ("vendor", "vendor_name"),
    "amount": ("amount", "total", "invoice_total"),
    "invoice_number": ("invoice_number", "invoiceNo", "invoice_num", "number"),
    "due_date": ("due_date", "dueDate", "payment_due_date"),
}

_SENDER_DOMAIN_GROUPS = {
    "known_billing_platforms": (
        "google.com",
        "stripe.com",
        "paypal.com",
        "square.com",
        "squareup.com",
    ),
}


_CONFIDENCE_PROFILE_CLASSES = (
    {
        "id": "known_billing_attachment_invoice",
        "document_types": ("invoice",),
        "primary_sources": ("attachment",),
        "requires_attachment": True,
        "sender_domain_groups": ("known_billing_platforms",),
        "threshold_overrides": {
            "vendor": 0.90,
            "invoice_number": 0.90,
            "due_date": 0.88,
        },
    },
    {
        "id": "generic_attachment_invoice",
        "document_types": ("invoice",),
        "primary_sources": ("attachment",),
        "requires_attachment": True,
        "threshold_overrides": {
            "vendor": 0.93,
            "invoice_number": 0.93,
            "due_date": 0.91,
        },
    },
    {
        "id": "known_billing_email_invoice",
        "document_types": ("invoice",),
        "primary_sources": ("email",),
        "sender_domain_groups": ("known_billing_platforms",),
        "threshold_overrides": {
            "vendor": 0.94,
            "invoice_number": 0.93,
            "due_date": 0.91,
        },
    },
    {
        "id": "generic_email_invoice",
        "document_types": ("invoice",),
        "primary_sources": ("email",),
    },
    {
        "id": "non_invoice_finance_document",
        "document_types": ("credit_note", "refund", "payment", "receipt", "statement"),
        "critical_fields": ("vendor", "amount"),
        "threshold_overrides": {
            "vendor": 0.92,
            "amount": 0.93,
        },
    },
)


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
    return min(1.0, max(0.0, num))


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


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _extract_sender_domain(sender: Any) -> str:
    raw = _normalize_text(sender)
    if not raw:
        return ""
    if "<" in raw and ">" in raw:
        inner = raw.split("<", 1)[1].split(">", 1)[0].strip()
    else:
        inner = raw
    if "@" not in inner:
        return ""
    return inner.rsplit("@", 1)[-1].strip().lower()


def _matches_sender_domain_groups(sender_domain: str, groups: Sequence[str]) -> bool:
    if not sender_domain:
        return False
    for group in groups:
        domains = _SENDER_DOMAIN_GROUPS.get(str(group or "").strip(), ())
        for domain in domains:
            if sender_domain == domain or sender_domain.endswith(f".{domain}"):
                return True
    return False


def _matches_document_type(document_type: str, allowed_document_types: Sequence[str]) -> bool:
    if not allowed_document_types:
        return True
    return document_type in {str(value or "").strip().lower() for value in allowed_document_types}


def _matches_primary_source(
    primary_source: str,
    *,
    has_attachment: bool,
    allowed_primary_sources: Sequence[str],
) -> bool:
    if not allowed_primary_sources:
        return True
    normalized_sources = {str(value or "").strip().lower() for value in allowed_primary_sources}
    if "attachment" in normalized_sources:
        if primary_source == "attachment" or (not primary_source and has_attachment):
            return True
    if "email" in normalized_sources:
        if primary_source == "email" or (not primary_source and not has_attachment):
            return True
    return primary_source in normalized_sources


def _resolve_confidence_profile(
    vendor_name: Any,
    sender: Any,
    *,
    document_type: Any = None,
    primary_source: Any = None,
    has_attachment: Any = None,
    sender_domain: Any = None,
) -> Optional[Dict[str, Any]]:
    resolved_sender_domain = _normalize_text(sender_domain) or _extract_sender_domain(sender)
    normalized_document_type = _normalize_text(document_type)
    normalized_primary_source = _normalize_text(primary_source)
    normalized_document_type = normalized_document_type or "invoice"
    attachment_present = bool(has_attachment)

    for profile in _CONFIDENCE_PROFILE_CLASSES:
        if not _matches_document_type(
            normalized_document_type,
            profile.get("document_types") or (),
        ):
            continue
        if not _matches_primary_source(
            normalized_primary_source,
            has_attachment=attachment_present,
            allowed_primary_sources=profile.get("primary_sources") or (),
        ):
            continue
        if profile.get("requires_attachment") and not attachment_present:
            continue
        if profile.get("sender_domain_groups") and not _matches_sender_domain_groups(
            resolved_sender_domain,
            profile.get("sender_domain_groups") or (),
        ):
            continue
        return profile
    return None


def evaluate_critical_field_confidence(
    *,
    overall_confidence: Any,
    field_values: Optional[Dict[str, Any]] = None,
    field_confidences: Any = None,
    threshold: float = DEFAULT_CRITICAL_FIELD_CONFIDENCE_THRESHOLD,
    critical_fields: Optional[Iterable[str]] = None,
    vendor_name: Any = None,
    sender: Any = None,
    document_type: Any = None,
    primary_source: Any = None,
    has_attachment: Any = None,
    sender_domain: Any = None,
    learned_threshold_overrides: Optional[Dict[str, Any]] = None,
    learned_profile_id: Any = None,
    learned_signal_count: Any = None,
) -> Dict[str, Any]:
    """Evaluate whether critical extracted fields require manual review."""
    threshold_norm = normalize_confidence_value(threshold)
    if threshold_norm is None:
        threshold_norm = DEFAULT_CRITICAL_FIELD_CONFIDENCE_THRESHOLD

    values = dict(field_values or {})
    explicit_field_conf = extract_field_confidences(field_confidences)
    overall_norm = normalize_confidence_value(overall_confidence)
    profile = _resolve_confidence_profile(
        vendor_name or values.get("vendor"),
        sender,
        document_type=document_type,
        primary_source=primary_source,
        has_attachment=has_attachment,
        sender_domain=sender_domain,
    )
    threshold_overrides = (
        profile.get("threshold_overrides")
        if isinstance(profile, dict) and isinstance(profile.get("threshold_overrides"), dict)
        else {}
    )
    learned_overrides = (
        learned_threshold_overrides
        if isinstance(learned_threshold_overrides, dict)
        else {}
    )
    merged_threshold_overrides: Dict[str, float] = {}
    for field in set(threshold_overrides.keys()) | set(learned_overrides.keys()):
        static_value = normalize_confidence_value(threshold_overrides.get(field))
        learned_value = normalize_confidence_value(learned_overrides.get(field))
        if static_value is None:
            if learned_value is not None:
                merged_threshold_overrides[field] = learned_value
            continue
        if learned_value is None:
            merged_threshold_overrides[field] = static_value
            continue
        # Learned calibration currently comes from correction history, so it only
        # tightens thresholds when there is repeated evidence of extraction errors.
        merged_threshold_overrides[field] = min(1.0, max(0.0, max(static_value, learned_value)))

    blockers: List[Dict[str, Any]] = []
    evaluated_fields: List[str] = []
    effective_confidences: Dict[str, float] = {}
    effective_thresholds: Dict[str, float] = {}
    profile_critical_fields = (
        profile.get("critical_fields")
        if isinstance(profile, dict)
        else None
    )
    effective_critical_fields = list(profile_critical_fields or critical_fields or DEFAULT_CRITICAL_FIELDS)

    for field in effective_critical_fields:
        raw_value = _field_value(field, values)
        if not _has_value(raw_value):
            continue
        evaluated_fields.append(field)

        confidence = explicit_field_conf.get(field)
        if confidence is None:
            confidence = overall_norm if overall_norm is not None else 0.0

        effective_confidences[field] = confidence
        field_threshold = normalize_confidence_value(merged_threshold_overrides.get(field))
        if field_threshold is None:
            field_threshold = threshold_norm
        effective_thresholds[field] = field_threshold
        if confidence < field_threshold:
            blockers.append(
                {
                    "field": field,
                    "confidence": round(confidence, 4),
                    "confidence_pct": round(confidence * 100),
                    "threshold": round(field_threshold, 4),
                    "threshold_pct": round(field_threshold * 100),
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
        "field_thresholds": effective_thresholds,
        "confidence_blockers": blockers,
        "requires_field_review": bool(blockers),
        "profile_id": profile.get("id") if isinstance(profile, dict) else None,
        "learned_profile_id": str(learned_profile_id or "").strip() or None,
        "learned_signal_count": int(learned_signal_count or 0),
    }
