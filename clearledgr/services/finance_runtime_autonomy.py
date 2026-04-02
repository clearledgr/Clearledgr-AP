"""Autonomy helpers extracted from FinanceAgentRuntime."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)

_AUTONOMY_ACTION_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "route_low_risk_for_approval": {
        "label": "route_for_approval",
        "min_recent_invoice_count": 2,
        "allowed_drift_risks": ("stable",),
        "require_zero_sample_recommended_count": True,
        "require_no_source_shift_fields": True,
        "min_shadow_scored_item_count": 2,
        "min_shadow_action_match_rate": 0.80,
        "min_shadow_critical_field_match_rate": 0.90,
        "require_zero_shadow_disagreements": False,
        "min_post_verification_attempt_count": 0,
        "min_post_verification_rate": 0.0,
        "require_zero_post_mismatches": False,
    },
    "auto_approve": {
        "label": "auto_approve",
        "min_recent_invoice_count": 4,
        "allowed_drift_risks": ("stable",),
        "require_zero_sample_recommended_count": True,
        "require_no_source_shift_fields": True,
        "min_shadow_scored_item_count": 4,
        "min_shadow_action_match_rate": 0.90,
        "min_shadow_critical_field_match_rate": 0.95,
        "require_zero_shadow_disagreements": True,
        "min_post_verification_attempt_count": 0,
        "min_post_verification_rate": 0.0,
        "require_zero_post_mismatches": False,
    },
    "post_to_erp": {
        "label": "post_to_erp",
        "min_recent_invoice_count": 4,
        "allowed_drift_risks": ("stable",),
        "require_zero_sample_recommended_count": True,
        "require_no_source_shift_fields": True,
        "min_shadow_scored_item_count": 4,
        "min_shadow_action_match_rate": 0.90,
        "min_shadow_critical_field_match_rate": 0.95,
        "require_zero_shadow_disagreements": True,
        "min_post_verification_attempt_count": 2,
        "min_post_verification_rate": 1.0,
        "require_zero_post_mismatches": True,
    },
}

_AUTONOMY_ACTION_ALIASES: Dict[str, tuple[str, ...]] = {
    "route_low_risk_for_approval": ("route_low_risk_for_approval",),
    "auto_approve": ("auto_approve",),
    "post_to_erp": ("post_to_erp",),
    "approve_invoice": ("auto_approve",),
    "retry_recoverable_failures": ("post_to_erp",),
    "auto_approve_post": ("auto_approve", "post_to_erp"),
}


def _load_org_autonomy_thresholds(organization_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Load per-org autonomy threshold overrides from settings_json.

    Returns the merged result of the global defaults + any org-level
    ``settings_json.autonomy_thresholds`` overrides.  Falls back to
    the global defaults if the org has no overrides or if anything fails.
    """
    if not organization_id:
        return _AUTONOMY_ACTION_THRESHOLDS

    try:
        from clearledgr.core.database import get_db

        db = get_db()
        org = db.get_organization(organization_id)
        if not org:
            return _AUTONOMY_ACTION_THRESHOLDS

        settings = org.get("settings_json") if isinstance(org, dict) else None
        if isinstance(settings, str):
            import json as _json
            settings = _json.loads(settings)
        if not isinstance(settings, dict):
            return _AUTONOMY_ACTION_THRESHOLDS

        overrides = settings.get("autonomy_thresholds")
        if not isinstance(overrides, dict) or not overrides:
            return _AUTONOMY_ACTION_THRESHOLDS

        # Deep-merge: for each action, start with global defaults and layer
        # org overrides on top so admins only need to specify the keys they
        # want to change.
        merged: Dict[str, Dict[str, Any]] = {}
        for action, defaults in _AUTONOMY_ACTION_THRESHOLDS.items():
            action_overrides = overrides.get(action)
            if isinstance(action_overrides, dict):
                merged[action] = {**defaults, **action_overrides}
            else:
                merged[action] = dict(defaults)
        return merged
    except Exception as exc:
        logger.debug("org autonomy thresholds load failed for %s: %s", organization_id, exc)
        return _AUTONOMY_ACTION_THRESHOLDS


def extraction_drift_payload(ap_kpis: Dict[str, Any]) -> Dict[str, Any]:
    telemetry = (ap_kpis or {}).get("agentic_telemetry")
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    drift = telemetry.get("extraction_drift")
    return drift if isinstance(drift, dict) else {}


def shadow_decision_payload(ap_kpis: Dict[str, Any]) -> Dict[str, Any]:
    telemetry = (ap_kpis or {}).get("agentic_telemetry")
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    shadow = telemetry.get("shadow_decision_scoring")
    return shadow if isinstance(shadow, dict) else {}


def post_action_verification_payload(ap_kpis: Dict[str, Any]) -> Dict[str, Any]:
    telemetry = (ap_kpis or {}).get("agentic_telemetry")
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    verification = telemetry.get("post_action_verification")
    return verification if isinstance(verification, dict) else {}


def vendor_shadow_scorecard(
    runtime: Any,
    vendor_name: Any,
    *,
    ap_kpis: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    vendor = runtime._normalize_vendor_name(vendor_name)
    if not vendor:
        return None
    vendor_token = vendor.casefold()
    shadow = shadow_decision_payload(ap_kpis or {})
    for row in shadow.get("vendor_scorecards") or []:
        if not isinstance(row, dict):
            continue
        candidate = runtime._normalize_vendor_name(row.get("vendor_name"))
        if candidate and candidate.casefold() == vendor_token:
            return row
    return None


def vendor_post_verification_scorecard(
    runtime: Any,
    vendor_name: Any,
    *,
    ap_kpis: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    vendor = runtime._normalize_vendor_name(vendor_name)
    if not vendor:
        return None
    vendor_token = vendor.casefold()
    verification = post_action_verification_payload(ap_kpis or {})
    for row in verification.get("vendor_scorecards") or []:
        if not isinstance(row, dict):
            continue
        candidate = runtime._normalize_vendor_name(row.get("vendor_name"))
        if candidate and candidate.casefold() == vendor_token:
            return row
    return None


def autonomy_action_thresholds(
    organization_id: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    source = (
        _load_org_autonomy_thresholds(organization_id)
        if organization_id
        else _AUTONOMY_ACTION_THRESHOLDS
    )
    return {
        action: {
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in thresholds.items()
        }
        for action, thresholds in source.items()
    }


def dedupe_reason_codes(codes: List[str]) -> List[str]:
    return list(dict.fromkeys([str(code).strip() for code in codes if str(code).strip()]))


def item_finance_effect_policy(runtime: Any, ap_item: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    item = ap_item if isinstance(ap_item, dict) else {}
    metadata = runtime._parse_json_dict(item.get("metadata"))
    document_type = str(
        item.get("document_type")
        or item.get("email_type")
        or metadata.get("document_type")
        or metadata.get("email_type")
        or "invoice"
    ).strip().lower() or "invoice"
    if document_type != "invoice":
        return {
            "reason_codes": [],
            "detail_lines": [],
            "summary": {},
        }

    summary = item.get("finance_effect_summary") if isinstance(item.get("finance_effect_summary"), dict) else {}
    if not summary:
        summary = metadata.get("finance_effect_summary") if isinstance(metadata.get("finance_effect_summary"), dict) else {}
    blockers = item.get("finance_effect_blockers") if isinstance(item.get("finance_effect_blockers"), list) else []
    if not blockers:
        blockers = metadata.get("finance_effect_blockers") if isinstance(metadata.get("finance_effect_blockers"), list) else []
    requires_review = bool(
        item.get("finance_effect_review_required")
        or metadata.get("finance_effect_review_required")
    )

    reason_codes: List[str] = []
    detail_lines: List[str] = []
    if requires_review:
        reason_codes.append("linked_finance_effect_review_required")
    for blocker in blockers:
        if isinstance(blocker, dict):
            code = str(blocker.get("code") or "").strip()
            detail = str(blocker.get("detail") or "").strip()
        else:
            code = str(blocker or "").strip()
            detail = ""
        if code:
            reason_codes.append(code)
        if detail:
            detail_lines.append(detail)
    return {
        "reason_codes": dedupe_reason_codes(reason_codes),
        "detail_lines": list(dict.fromkeys([line for line in detail_lines if line])),
        "summary": summary or {},
    }


def autonomy_requested_action_dependencies(action: Any) -> tuple[str, ...]:
    token = str(action or "").strip().lower()
    return _AUTONOMY_ACTION_ALIASES.get(token, (token or "route_low_risk_for_approval",))


def evaluate_action_autonomy_policy(
    runtime: Any,
    *,
    action: str,
    vendor: str,
    readiness: Dict[str, Any],
    failing_gates: List[str],
    scorecard: Optional[Dict[str, Any]],
    shadow_scorecard: Optional[Dict[str, Any]],
    verification_scorecard: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    org_id = getattr(runtime, "organization_id", None)
    org_thresholds = _load_org_autonomy_thresholds(org_id) if org_id else _AUTONOMY_ACTION_THRESHOLDS
    thresholds = org_thresholds.get(action) or _AUTONOMY_ACTION_THRESHOLDS[action]
    action_label = str(thresholds.get("label") or action)
    reason_codes: List[str] = []
    status = str(readiness.get("status") or "").strip().lower()
    if status != "ready" or failing_gates:
        reason_codes.append("ap_skill_not_ready")
        reason_codes.extend([f"gate:{gate}" for gate in failing_gates])
    elif not vendor:
        reason_codes.append("vendor_missing")
    elif not scorecard:
        reason_codes.append("vendor_unscored")
    else:
        drift_risk = str(scorecard.get("drift_risk") or "stable").strip().lower() or "stable"
        recent_invoice_count = runtime._safe_int(scorecard.get("recent_invoice_count"))
        sample_recommended_count = runtime._safe_int(scorecard.get("sample_recommended_count"))
        source_shift_fields = [
            str(value).strip()
            for value in (scorecard.get("source_shift_fields") or [])
            if str(value).strip()
        ]
        verification_attempted_count = runtime._safe_int((verification_scorecard or {}).get("attempted_count"))
        verification_rate = runtime._safe_float((verification_scorecard or {}).get("verification_rate"))
        verification_mismatch_count = runtime._safe_int((verification_scorecard or {}).get("mismatch_count"))

        if verification_attempted_count >= 2 and (
            verification_rate < 0.90 or verification_mismatch_count > 0
        ):
            reason_codes.append("vendor_post_verification_low")

        allowed_drift_risks = {
            str(value).strip().lower()
            for value in (thresholds.get("allowed_drift_risks") or ())
            if str(value).strip()
        }
        if allowed_drift_risks and drift_risk not in allowed_drift_risks:
            reason_codes.append(f"vendor_drift_{drift_risk}")
        if recent_invoice_count < runtime._safe_int(thresholds.get("min_recent_invoice_count")):
            reason_codes.append("vendor_observation_mode")
        if bool(thresholds.get("require_zero_sample_recommended_count")) and sample_recommended_count > 0:
            reason_codes.append("vendor_sample_review_required")
        if bool(thresholds.get("require_no_source_shift_fields")) and source_shift_fields:
            reason_codes.append("vendor_source_shift_detected")

        min_shadow_scored = runtime._safe_int(thresholds.get("min_shadow_scored_item_count"))
        if min_shadow_scored > 0:
            shadow_scored_count = runtime._safe_int((shadow_scorecard or {}).get("scored_item_count"))
            if not shadow_scorecard or shadow_scored_count < min_shadow_scored:
                reason_codes.append("vendor_shadow_observation_mode")
            else:
                shadow_action_match_rate = runtime._safe_float((shadow_scorecard or {}).get("action_match_rate"))
                shadow_critical_field_match_rate = runtime._safe_float((shadow_scorecard or {}).get("critical_field_match_rate"))
                shadow_disagreement_count = runtime._safe_int((shadow_scorecard or {}).get("disagreement_count"))
                if shadow_action_match_rate < runtime._safe_float(thresholds.get("min_shadow_action_match_rate")):
                    reason_codes.append("vendor_shadow_action_match_low")
                if shadow_critical_field_match_rate < runtime._safe_float(
                    thresholds.get("min_shadow_critical_field_match_rate")
                ):
                    reason_codes.append("vendor_shadow_critical_field_match_low")
                if bool(thresholds.get("require_zero_shadow_disagreements")) and shadow_disagreement_count > 0:
                    reason_codes.append("vendor_shadow_disagreement_present")

        min_verification_attempts = runtime._safe_int(thresholds.get("min_post_verification_attempt_count"))
        if min_verification_attempts > 0:
            attempted_count = runtime._safe_int((verification_scorecard or {}).get("attempted_count"))
            if not verification_scorecard or attempted_count < min_verification_attempts:
                reason_codes.append("vendor_post_verification_observation_mode")
            else:
                verification_rate = runtime._safe_float((verification_scorecard or {}).get("verification_rate"))
                mismatch_count = runtime._safe_int((verification_scorecard or {}).get("mismatch_count"))
                if verification_rate < runtime._safe_float(thresholds.get("min_post_verification_rate")):
                    reason_codes.append("vendor_post_verification_low")
                if bool(thresholds.get("require_zero_post_mismatches")) and mismatch_count > 0:
                    reason_codes.append("vendor_post_verification_mismatch_present")

    blocked_reason_codes = dedupe_reason_codes(reason_codes)
    allowed = len(blocked_reason_codes) == 0
    detail = (
        f"{action_label} is earned for this vendor."
        if allowed
        else f"{action_label} is blocked until: {', '.join(blocked_reason_codes)}"
    )
    return {
        "action": action,
        "label": action_label,
        "autonomous_allowed": allowed,
        "requires_human_trigger": not allowed,
        "blocked_reason_codes": blocked_reason_codes,
        "detail": detail,
        "thresholds": {
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in thresholds.items()
        },
    }


def vendor_drift_scorecard(
    runtime: Any,
    vendor_name: Any,
    *,
    ap_kpis: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    vendor = runtime._normalize_vendor_name(vendor_name)
    if not vendor:
        return None
    vendor_token = vendor.casefold()
    drift = extraction_drift_payload(ap_kpis or {})
    for row in drift.get("vendor_scorecards") or []:
        if not isinstance(row, dict):
            continue
        candidate = runtime._normalize_vendor_name(row.get("vendor_name"))
        if candidate and candidate.casefold() == vendor_token:
            return row
    return None


def evaluate_ap_vendor_autonomy(
    runtime: Any,
    *,
    vendor_name: Any,
    readiness: Dict[str, Any],
    ap_kpis: Dict[str, Any],
) -> Dict[str, Any]:
    vendor = runtime._normalize_vendor_name(vendor_name)
    scorecard = vendor_drift_scorecard(runtime, vendor, ap_kpis=ap_kpis)
    shadow_scorecard = vendor_shadow_scorecard(runtime, vendor, ap_kpis=ap_kpis)
    verification_scorecard = vendor_post_verification_scorecard(runtime, vendor, ap_kpis=ap_kpis)
    drift = extraction_drift_payload(ap_kpis)
    failing_gates = runtime._readiness_gate_failures(readiness)

    action_policies: Dict[str, Dict[str, Any]] = {}
    for action in _AUTONOMY_ACTION_THRESHOLDS:
        action_policies[action] = evaluate_action_autonomy_policy(
            runtime,
            action=action,
            vendor=vendor,
            readiness=readiness,
            failing_gates=failing_gates,
            scorecard=scorecard,
            shadow_scorecard=shadow_scorecard,
            verification_scorecard=verification_scorecard,
        )

    hard_manual_reasons: List[str] = []
    if str(readiness.get("status") or "").strip().lower() != "ready" or failing_gates:
        hard_manual_reasons.append("ap_skill_not_ready")
        hard_manual_reasons.extend([f"gate:{gate}" for gate in failing_gates])
    drift_risk = str((scorecard or {}).get("drift_risk") or "unknown").strip().lower() or "unknown"
    if drift_risk == "high":
        hard_manual_reasons.append("vendor_drift_high")
    shadow_scored_count = runtime._safe_int((shadow_scorecard or {}).get("scored_item_count"))
    shadow_action_match_rate = runtime._safe_float((shadow_scorecard or {}).get("action_match_rate"))
    shadow_critical_field_match_rate = runtime._safe_float((shadow_scorecard or {}).get("critical_field_match_rate"))
    if shadow_scorecard and shadow_scored_count >= 2 and (
        shadow_action_match_rate < 0.75 or shadow_critical_field_match_rate < 0.85
    ):
        hard_manual_reasons.append("vendor_shadow_quality_low")
    verification_attempted_count = runtime._safe_int((verification_scorecard or {}).get("attempted_count"))
    verification_rate = runtime._safe_float((verification_scorecard or {}).get("verification_rate"))
    verification_mismatch_count = runtime._safe_int((verification_scorecard or {}).get("mismatch_count"))
    if verification_scorecard and verification_attempted_count >= 2 and (
        verification_rate < 0.90 or verification_mismatch_count > 0
    ):
        hard_manual_reasons.append("vendor_post_verification_low")

    if hard_manual_reasons:
        mode = "manual"
    elif (
        action_policies["route_low_risk_for_approval"]["autonomous_allowed"]
        and action_policies["auto_approve"]["autonomous_allowed"]
        and action_policies["post_to_erp"]["autonomous_allowed"]
    ):
        mode = "auto"
    else:
        mode = "assisted"

    earned_actions = [
        action
        for action, policy in action_policies.items()
        if bool(policy.get("autonomous_allowed"))
    ]
    blocked_actions = {
        action: list(policy.get("blocked_reason_codes") or [])
        for action, policy in action_policies.items()
        if not bool(policy.get("autonomous_allowed"))
    }
    if mode == "manual":
        detail = "Vendor is held in manual mode because autonomy health is below the hard minimum."
    elif mode == "auto":
        detail = "Vendor has earned autonomous routing, approval, and ERP post."
    elif action_policies["route_low_risk_for_approval"]["autonomous_allowed"]:
        detail = "Vendor is in assisted mode: autonomous routing is earned, but approval or posting still requires trust promotion."
    else:
        detail = "Vendor is in assisted mode: human-triggered review remains required until routing trust is earned."

    return {
        "mode": mode,
        "detail": detail,
        "vendor_name": vendor or None,
        "action_policies": action_policies,
        "earned_actions": earned_actions,
        "blocked_actions": blocked_actions,
        "reason_codes": dedupe_reason_codes(
            list(hard_manual_reasons)
            or blocked_actions.get("route_low_risk_for_approval", [])
            or blocked_actions.get("auto_approve", [])
            or blocked_actions.get("post_to_erp", [])
        ),
        "hard_manual_reasons": dedupe_reason_codes(hard_manual_reasons),
        "failing_gates": failing_gates,
        "scorecard": scorecard or {},
        "shadow_scorecard": shadow_scorecard or {},
        "verification_scorecard": verification_scorecard or {},
        "vendors_at_risk": int((drift.get("summary") or {}).get("vendors_at_risk") or 0),
        "high_risk_vendors": int((drift.get("summary") or {}).get("high_risk_vendors") or 0),
    }


def build_shadow_decision_proposal(
    runtime: Any,
    *,
    invoice: Dict[str, Any],
    vendor_name: Optional[str],
    amount: float,
    confidence: float,
    requires_field_review: bool,
    autonomy_policy: Dict[str, Any],
    auto_post_threshold: float,
) -> Dict[str, Any]:
    metadata = runtime._parse_json_dict(invoice.get("metadata"))
    document_type = str(
        invoice.get("document_type")
        or invoice.get("email_type")
        or metadata.get("document_type")
        or metadata.get("email_type")
        or "invoice"
    ).strip().lower() or "invoice"
    proposed_action = "route_for_approval"
    reason_codes: List[str] = []

    if document_type != "invoice":
        proposed_action = "non_invoice_finance_doc"
        reason_codes.append(f"document_type:{document_type}")
    elif requires_field_review:
        proposed_action = "field_review"
        reason_codes.append("field_review_required")
    elif autonomy_policy.get("autonomous_allowed") and amount >= 0 and confidence >= auto_post_threshold:
        proposed_action = "auto_approve_post"
        reason_codes.append("meets_auto_post_threshold")
    else:
        proposed_action = "route_for_approval"
        if confidence < auto_post_threshold:
            reason_codes.append("below_auto_post_threshold")
        if not autonomy_policy.get("autonomous_allowed"):
            reason_codes.append(f"autonomy_mode:{autonomy_policy.get('mode') or 'assisted'}")

    return {
        "version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": "finance_agent_runtime",
        "proposed_action": proposed_action,
        "reason_codes": list(dict.fromkeys([code for code in reason_codes if code])),
        "confidence": round(float(confidence or 0.0), 4),
        "auto_post_threshold": round(float(auto_post_threshold or 0.0), 4),
        "autonomy_mode": str(autonomy_policy.get("mode") or "manual"),
        "autonomous_allowed": bool(autonomy_policy.get("autonomous_allowed")),
        "proposed_fields": {
            "vendor": vendor_name or None,
            "amount": round(float(amount or 0.0), 2),
            "currency": str(invoice.get("currency") or "USD").strip() or "USD",
            "invoice_number": str(invoice.get("invoice_number") or "").strip() or None,
            "document_type": document_type,
            "due_date": str(invoice.get("due_date") or "").strip() or None,
        },
    }


def is_autonomous_request(runtime: Any, payload: Optional[Dict[str, Any]] = None) -> bool:
    data = payload if isinstance(payload, dict) else {}
    execution_context = str(
        data.get("execution_context")
        or data.get("run_mode")
        or data.get("mode")
        or ""
    ).strip().lower()
    if execution_context in {"autonomous", "auto", "system", "background", "autopilot", "agent"}:
        return True
    if runtime._as_bool(data.get("autonomous")) or runtime._as_bool(data.get("autonomous_requested")):
        return True
    source_channel = str(data.get("source_channel") or data.get("source") or "").strip().lower()
    if source_channel in {"autopilot", "system", "agent_runtime", "background_worker"}:
        return True
    actor_id = str(runtime.actor_id or "").strip().lower()
    actor_email = str(runtime.actor_email or "").strip().lower()
    return actor_id in {"system", "agent_runtime"} or actor_email in {
        "system",
        "system@clearledgr.local",
    }


def ap_autonomy_policy(
    runtime: Any,
    *,
    vendor_name: Any = None,
    action: str = "route_low_risk_for_approval",
    autonomous_requested: bool = False,
    window_hours: int = 168,
    ap_item: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        readiness = runtime.skill_readiness("ap_v1", window_hours=window_hours)
    except Exception as exc:
        logger.warning(
            "AP autonomy policy fell back to blocked readiness for org=%s: %s",
            runtime.organization_id,
            exc,
        )
        readiness = {
            "status": "blocked",
            "blocked_reasons": ["skill_readiness_unavailable"],
            "gates": [],
            "metrics": {},
        }

    metrics = readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {}
    ap_kpis = metrics.get("ap_kpis") if isinstance(metrics.get("ap_kpis"), dict) else runtime._ap_kpis_snapshot()
    vendor = runtime._normalize_vendor_name(vendor_name)
    evaluation = evaluate_ap_vendor_autonomy(
        runtime,
        vendor_name=vendor,
        readiness=readiness,
        ap_kpis=ap_kpis,
    )
    requested_action = str(action or "").strip().lower() or "route_low_risk_for_approval"
    dependencies = autonomy_requested_action_dependencies(requested_action)
    dependency_policies = [
        evaluation["action_policies"].get(dependency, {})
        for dependency in dependencies
    ]
    vendor_allowed = all(bool(policy.get("autonomous_allowed")) for policy in dependency_policies)
    reason_codes = dedupe_reason_codes(
        [
            code
            for policy in dependency_policies
            for code in (policy.get("blocked_reason_codes") or [])
        ]
    )
    effect_policy = item_finance_effect_policy(runtime, ap_item)
    item_reason_codes = list(effect_policy.get("reason_codes") or [])
    autonomous_allowed = bool(vendor_allowed and not item_reason_codes)
    if autonomous_allowed:
        detail = (
            dependency_policies[0].get("detail")
            if len(dependencies) == 1
            else "Autonomous approval and ERP post are both earned for this vendor."
        )
    elif item_reason_codes:
        reason_codes = dedupe_reason_codes([*reason_codes, *item_reason_codes])
        detail_lines = list(effect_policy.get("detail_lines") or [])
        detail = (
            "Autonomous action is blocked until linked finance effects are reviewed. "
            + " ".join(detail_lines)
        ).strip()
    elif len(dependencies) > 1:
        detail = "Autonomous approval/post is blocked until: " + ", ".join(reason_codes)
    else:
        detail = dependency_policies[0].get("detail") or evaluation.get("detail")

    scorecard = evaluation.get("scorecard") or {}
    shadow_scorecard = evaluation.get("shadow_scorecard") or {}
    verification_scorecard = evaluation.get("verification_scorecard") or {}

    return {
        "mode": evaluation.get("mode"),
        "action": requested_action,
        "action_dependencies": list(dependencies),
        "autonomous_requested": bool(autonomous_requested),
        "autonomous_allowed": bool(autonomous_allowed),
        "requires_human_trigger": not bool(autonomous_allowed),
        "vendor_name": vendor or None,
        "reason_codes": reason_codes,
        "detail": detail,
        "ap_skill_status": str(readiness.get("status") or "blocked"),
        "failing_gates": list(evaluation.get("failing_gates") or []),
        "earned_actions": list(evaluation.get("earned_actions") or []),
        "blocked_actions": dict(evaluation.get("blocked_actions") or {}),
        "action_policies": dict(evaluation.get("action_policies") or {}),
        "action_thresholds": autonomy_action_thresholds(getattr(runtime, "organization_id", None)),
        "item_reason_codes": item_reason_codes,
        "finance_effect_summary": effect_policy.get("summary") or {},
        "vendor_drift_risk": (
            str(scorecard.get("drift_risk") or "").strip().lower() or "unknown"
        ),
        "vendor_recent_invoice_count": int(scorecard.get("recent_invoice_count") or 0),
        "vendor_sample_recommended_count": int(scorecard.get("sample_recommended_count") or 0),
        "vendor_source_shift_fields": list(scorecard.get("source_shift_fields") or []),
        "vendor_shadow_scored_item_count": int(shadow_scorecard.get("scored_item_count") or 0),
        "vendor_shadow_action_match_rate": round(runtime._safe_float(shadow_scorecard.get("action_match_rate")), 4),
        "vendor_shadow_critical_field_match_rate": round(runtime._safe_float(shadow_scorecard.get("critical_field_match_rate")), 4),
        "vendor_shadow_disagreement_count": int(shadow_scorecard.get("disagreement_count") or 0),
        "vendor_post_verification_rate": round(runtime._safe_float(verification_scorecard.get("verification_rate")), 4),
        "vendor_post_verification_attempt_count": int(verification_scorecard.get("attempted_count") or 0),
        "vendor_post_verification_mismatch_count": int(verification_scorecard.get("mismatch_count") or 0),
        "vendors_at_risk": int(evaluation.get("vendors_at_risk") or 0),
        "high_risk_vendors": int(evaluation.get("high_risk_vendors") or 0),
    }


def ap_autonomy_summary(runtime: Any, *, window_hours: int = 168) -> Dict[str, Any]:
    try:
        readiness = runtime.skill_readiness("ap_v1", window_hours=window_hours)
    except Exception as exc:
        logger.warning(
            "AP autonomy summary fell back to blocked readiness for org=%s: %s",
            runtime.organization_id,
            exc,
        )
        readiness = {
            "status": "blocked",
            "blocked_reasons": ["skill_readiness_unavailable"],
            "gates": [],
            "metrics": {},
        }
    metrics = readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {}
    ap_kpis = metrics.get("ap_kpis") if isinstance(metrics.get("ap_kpis"), dict) else runtime._ap_kpis_snapshot()
    drift = extraction_drift_payload(ap_kpis)
    shadow = shadow_decision_payload(ap_kpis)
    verification = post_action_verification_payload(ap_kpis)
    summary = drift.get("summary") if isinstance(drift.get("summary"), dict) else {}
    shadow_summary = shadow.get("summary") if isinstance(shadow.get("summary"), dict) else {}
    verification_summary = verification.get("summary") if isinstance(verification.get("summary"), dict) else {}
    failing_gates = runtime._readiness_gate_failures(readiness)
    vendor_names: List[str] = []
    for payload in (
        drift.get("vendor_scorecards") or [],
        shadow.get("vendor_scorecards") or [],
        verification.get("vendor_scorecards") or [],
    ):
        for row in payload:
            if not isinstance(row, dict):
                continue
            vendor = runtime._normalize_vendor_name(row.get("vendor_name"))
            if vendor and vendor not in vendor_names:
                vendor_names.append(vendor)

    vendor_promotion_status: List[Dict[str, Any]] = []
    for vendor in vendor_names:
        evaluation = evaluate_ap_vendor_autonomy(
            runtime,
            vendor_name=vendor,
            readiness=readiness,
            ap_kpis=ap_kpis,
        )
        scorecard = evaluation.get("scorecard") or {}
        shadow_scorecard = evaluation.get("shadow_scorecard") or {}
        verification_scorecard = evaluation.get("verification_scorecard") or {}
        vendor_promotion_status.append(
            {
                "vendor_name": vendor,
                "mode": evaluation.get("mode"),
                "detail": evaluation.get("detail"),
                "earned_actions": list(evaluation.get("earned_actions") or []),
                "blocked_actions": dict(evaluation.get("blocked_actions") or {}),
                "action_policies": dict(evaluation.get("action_policies") or {}),
                "reason_codes": list(evaluation.get("reason_codes") or []),
                "drift_risk": str(scorecard.get("drift_risk") or "unknown"),
                "recent_invoice_count": int(scorecard.get("recent_invoice_count") or 0),
                "sample_recommended_count": int(scorecard.get("sample_recommended_count") or 0),
                "source_shift_fields": list(scorecard.get("source_shift_fields") or []),
                "shadow_scored_item_count": int(shadow_scorecard.get("scored_item_count") or 0),
                "shadow_action_match_rate": round(runtime._safe_float(shadow_scorecard.get("action_match_rate")), 4),
                "shadow_critical_field_match_rate": round(runtime._safe_float(shadow_scorecard.get("critical_field_match_rate")), 4),
                "shadow_disagreement_count": int(shadow_scorecard.get("disagreement_count") or 0),
                "post_verification_attempt_count": int(verification_scorecard.get("attempted_count") or 0),
                "post_verification_rate": round(runtime._safe_float(verification_scorecard.get("verification_rate")), 4),
                "post_verification_mismatch_count": int(verification_scorecard.get("mismatch_count") or 0),
            }
        )
    vendor_promotion_status.sort(
        key=lambda row: (
            {"manual": 0, "assisted": 1, "auto": 2}.get(str(row.get("mode") or "manual"), 9),
            {"high": 0, "medium": 1, "stable": 2}.get(str(row.get("drift_risk") or "stable").lower(), 9),
            -int(row.get("sample_recommended_count") or 0),
            -int(row.get("shadow_disagreement_count") or 0),
            -int(row.get("recent_invoice_count") or 0),
            str(row.get("vendor_name") or ""),
        )
    )

    if str(readiness.get("status") or "").strip().lower() != "ready" or failing_gates:
        default_mode = "manual"
    elif any(str(row.get("mode")) == "auto" for row in vendor_promotion_status):
        default_mode = "auto"
    else:
        default_mode = "assisted"
    return {
        "mode": default_mode,
        "readiness_status": str(readiness.get("status") or "blocked"),
        "failing_gates": failing_gates,
        "vendors_monitored": int(summary.get("vendors_monitored") or 0),
        "vendors_at_risk": int(summary.get("vendors_at_risk") or 0),
        "high_risk_vendors": int(summary.get("high_risk_vendors") or 0),
        "recent_open_blocked_items": int(summary.get("recent_open_blocked_items") or 0),
        "shadow_scored_items": int(shadow_summary.get("scored_item_count") or 0),
        "shadow_disagreement_count": int(shadow_summary.get("disagreement_count") or 0),
        "shadow_action_match_rate": round(runtime._safe_float(shadow_summary.get("action_match_rate")), 4),
        "post_verification_rate": round(runtime._safe_float(verification_summary.get("verification_rate")), 4),
        "post_verification_mismatch_count": int(verification_summary.get("mismatch_count") or 0),
        "action_thresholds": autonomy_action_thresholds(getattr(runtime, "organization_id", None)),
        "vendor_promotion_status": vendor_promotion_status[:20],
        "vendors_manual": sum(1 for row in vendor_promotion_status if str(row.get("mode")) == "manual"),
        "vendors_assisted": sum(1 for row in vendor_promotion_status if str(row.get("mode")) == "assisted"),
        "vendors_auto": sum(1 for row in vendor_promotion_status if str(row.get("mode")) == "auto"),
        "detail": (
            "Autonomy is held in manual mode until readiness gates pass."
            if default_mode == "manual"
            else "Vendor-level autonomy is earned per action: routing first, then approval and ERP post."
        ),
    }
