"""Tenant-level AP policy evaluation for Clearledgr AP v1."""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PolicyDecision:
    valid: bool
    issues: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


DEFAULT_AP_POLICY: Dict[str, Any] = {
    "version": 1,
    "validation": {
        "require_attachment": True,
        "amount_anomaly_threshold": 0.35,
        "po_match_required_over": None,
        "require_receipt": False,
        "po_amount_tolerance_pct": 0.05,
        "po_amount_tolerance_abs": 1.0,
        "budget_check_required_over": None,
        "require_budget_context": False,
        "block_on_budget_overrun": False,
    },
    "vendor_rules": {},
    "routing": {
        "exception_first": True,
    },
    "exception_severity": {
        "po_missing_reference": "high",
        "po_amount_mismatch": "high",
        "receipt_missing": "medium",
        "budget_overrun": "high",
        "missing_budget_context": "medium",
        "policy_validation_failed": "medium",
        "missing_fields": "medium",
    },
}


def _safe_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
        if parsed < 0:
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def _load_vendor_rules() -> Dict[str, Dict[str, Any]]:
    raw = os.getenv("AP_VENDOR_RULES_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for key, value in parsed.items():
        if not isinstance(value, dict):
            continue
        normalized[str(key).strip().lower()] = value
    return normalized


def _normalize_vendor_rules(raw_rules: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw_rules, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for key, value in raw_rules.items():
        if not isinstance(value, dict):
            continue
        normalized[str(key).strip().lower()] = value
    return normalized


def normalize_ap_policy(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    policy = copy.deepcopy(DEFAULT_AP_POLICY)
    if not isinstance(config, dict):
        return policy

    for key, value in config.items():
        if isinstance(policy.get(key), dict) and isinstance(value, dict):
            policy[key].update(value)
        else:
            policy[key] = value

    if not isinstance(policy.get("validation"), dict):
        policy["validation"] = copy.deepcopy(DEFAULT_AP_POLICY["validation"])
    if not isinstance(policy.get("routing"), dict):
        policy["routing"] = copy.deepcopy(DEFAULT_AP_POLICY["routing"])
    if not isinstance(policy.get("exception_severity"), dict):
        policy["exception_severity"] = copy.deepcopy(DEFAULT_AP_POLICY["exception_severity"])
    if not isinstance(policy.get("vendor_rules"), dict):
        policy["vendor_rules"] = {}
    return policy


def evaluate_policy(
    *,
    vendor_name: Optional[str],
    amount: Optional[float],
    invoice_number: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
    tenant_policy: Optional[Dict[str, Any]] = None,
) -> PolicyDecision:
    """
    Evaluate deterministic policy rules for AP validation.

    Rules:
    - Negative amounts are invalid.
    - Optional required-attachment policy.
    - Vendor specific required fields and anomaly thresholds.
    """
    metadata = metadata or {}
    issues: List[str] = []
    policy_meta: Dict[str, Any] = {}
    policy = normalize_ap_policy(tenant_policy)
    validation = policy.get("validation") if isinstance(policy.get("validation"), dict) else {}
    policy_meta["policy_version"] = policy.get("version")

    if amount is not None and amount < 0:
        issues.append("negative_amount")

    if tenant_policy is not None:
        require_attachment = bool(validation.get("require_attachment", True))
    else:
        require_attachment = str(os.getenv("AP_REQUIRE_ATTACHMENT", "true")).strip().lower() not in {"0", "false", "no", "off"}
    attachment_hashes = metadata.get("attachment_hashes") or []
    attachment_count = len(attachment_hashes) if isinstance(attachment_hashes, list) else 0
    if require_attachment and attachment_count == 0:
        issues.append("missing_attachment")
    policy_meta["attachment_count"] = attachment_count

    vendor_rules = _normalize_vendor_rules(policy.get("vendor_rules")) if tenant_policy is not None else _load_vendor_rules()
    vendor_key = (vendor_name or "").strip().lower()
    vendor_rule = vendor_rules.get(vendor_key) or {}
    if vendor_rule:
        policy_meta["vendor_rule"] = vendor_rule
        if vendor_rule.get("require_invoice_number", False) and not invoice_number:
            issues.append("missing_invoice_number")
        max_amount = vendor_rule.get("max_amount")
        if max_amount is not None and amount is not None:
            max_allowed = _safe_float(max_amount, default=-1)
            if max_allowed >= 0 and amount > max_allowed:
                issues.append("vendor_amount_limit_exceeded")

    if tenant_policy is not None:
        anomaly_threshold = _safe_float(validation.get("amount_anomaly_threshold"), default=0.35)
    else:
        anomaly_threshold = _safe_float(os.getenv("AP_AMOUNT_ANOMALY_THRESHOLD", "0.35"), default=0.35)
    if amount is not None and amount > 0:
        historical = metadata.get("historical_vendor_average")
        if isinstance(historical, (int, float)) and historical > 0:
            delta = abs(amount - float(historical)) / float(historical)
            policy_meta["historical_delta_ratio"] = round(delta, 4)
            if delta > anomaly_threshold:
                issues.append("amount_anomaly")

    return PolicyDecision(
        valid=len(issues) == 0,
        issues=issues,
        metadata=policy_meta,
    )
