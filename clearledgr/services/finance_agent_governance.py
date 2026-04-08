"""Governance helpers for a stronger bounded finance agent.

This module centralizes:
- deliberation over belief + recall + quality proof
- doctrine enforcement from the persisted agent profile
- measurable autonomy proof snapshots
- self-recovery for recoverable AP failures
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from clearledgr.services.agent_memory import get_agent_memory_service
from clearledgr.services.finance_learning import get_finance_learning_service

logger = logging.getLogger(__name__)

_RISKY_ACTIONS = {
    "auto_approve",
    "auto_approve_post",
    "post_to_erp",
    "retry_recoverable_failures",
    "resume_workflow",
}

_BELIEF_ACTION_ALIGNMENT = {
    "human_field_review": {"field_review", "record_field_correction", "ap_invoice_processing"},
    "reprocess_after_correction": {"ap_invoice_processing", "retry_recoverable_failures", "resume_workflow"},
    "await_approval": {"route_low_risk_for_approval", "auto_approve", "auto_approve_post"},
    "await_vendor_info": {"request_vendor_info", "vendor_followup"},
    "operator_recovery": {"retry_recoverable_failures", "resume_workflow", "post_to_erp"},
    "monitor_completion": {"post_to_erp", "resume_workflow", "retry_recoverable_failures"},
}


def _normalize_action_token(action: Any) -> str:
    token = str(action or "").strip().lower()
    if token in {"approve_invoice", "route_for_approval"}:
        return "route_low_risk_for_approval"
    if token in {"retry", "retry_post", "resume"}:
        return "retry_recoverable_failures"
    return token or "route_low_risk_for_approval"


def _proof_gate_status(*, actual: Optional[float], minimum: float, sample_count: int, required_samples: int) -> str:
    if sample_count < required_samples:
        return "observe"
    if actual is None:
        return "observe"
    return "pass" if float(actual) >= float(minimum) else "fail"


def _autonomous_requested(runtime: Any, payload: Optional[Dict[str, Any]] = None) -> bool:
    data = payload if isinstance(payload, dict) else {}
    if hasattr(runtime, "is_autonomous_request"):
        try:
            return bool(runtime.is_autonomous_request(data))
        except Exception:
            pass
    execution_context = str(
        data.get("execution_context")
        or data.get("run_mode")
        or data.get("mode")
        or ""
    ).strip().lower()
    if execution_context in {"autonomous", "auto", "system", "background", "autopilot", "agent"}:
        return True
    if bool(data.get("autonomous")) or bool(data.get("autonomous_requested")):
        return True
    source_channel = str(data.get("source_channel") or data.get("source") or "").strip().lower()
    if source_channel in {"autopilot", "system", "agent_runtime", "background_worker"}:
        return True
    actor_id = str(getattr(runtime, "actor_id", "") or "").strip().lower()
    actor_email = str(getattr(runtime, "actor_email", "") or "").strip().lower()
    return actor_id in {"system", "agent_runtime"} or actor_email in {
        "system",
        "system@clearledgr.local",
    }


def build_agent_quality_snapshot(
    runtime: Any,
    *,
    requested_action: Any,
    autonomous_requested: bool = False,
    profile: Optional[Dict[str, Any]] = None,
    ap_item: Optional[Dict[str, Any]] = None,
    skill_id: str = "ap_v1",
    window_hours: int = 168,
) -> Dict[str, Any]:
    profile_data = dict(profile or {})
    item = dict(ap_item or {})
    vendor_name = runtime._normalize_vendor_name(item.get("vendor_name") or item.get("vendor"))
    normalized_action = _normalize_action_token(requested_action)
    learning = get_finance_learning_service(runtime.organization_id, db=getattr(runtime, "db", None))
    memory = get_agent_memory_service(runtime.organization_id, db=getattr(runtime, "db", None))

    try:
        readiness = runtime.skill_readiness(skill_id, window_hours=window_hours)
    except Exception as exc:
        logger.warning("quality snapshot readiness unavailable for org=%s: %s", runtime.organization_id, exc)
        readiness = {
            "status": "blocked",
            "blocked_reasons": ["skill_readiness_unavailable"],
            "gates": [],
            "metrics": {},
        }

    try:
        autonomy_policy = runtime.ap_autonomy_policy(
            vendor_name=vendor_name,
            action=normalized_action,
            autonomous_requested=bool(autonomous_requested),
            ap_item=item,
        )
    except Exception as exc:
        logger.warning("quality snapshot autonomy policy unavailable for org=%s: %s", runtime.organization_id, exc)
        autonomy_policy = {
            "mode": "manual",
            "autonomous_allowed": False,
            "reason_codes": ["autonomy_policy_unavailable"],
            "failing_gates": [],
            "vendor_shadow_scored_item_count": 0,
            "vendor_shadow_action_match_rate": 0.0,
            "vendor_shadow_critical_field_match_rate": 0.0,
            "vendor_post_verification_attempt_count": 0,
            "vendor_post_verification_rate": 0.0,
            "vendor_post_verification_mismatch_count": 0,
        }

    calibration = learning.get_outcome_calibration(
        vendor_name=vendor_name or None,
        action_key=normalized_action,
    )
    fallback_calibration = learning.get_outcome_calibration(
        vendor_name=None,
        action_key=normalized_action,
    )
    effective_calibration = calibration or fallback_calibration

    retry_jobs: List[Dict[str, Any]] = []
    if hasattr(runtime.db, "list_agent_retry_jobs") and item.get("id"):
        try:
            retry_jobs = runtime.db.list_agent_retry_jobs(
                runtime.organization_id,
                ap_item_id=str(item.get("id")),
                limit=10,
            ) or []
        except Exception:
            retry_jobs = []

    gate_statuses: Dict[str, str] = {
        str(gate.get("gate") or ""): str(gate.get("status") or "observe")
        for gate in (readiness.get("gates") or [])
        if isinstance(gate, dict) and str(gate.get("gate") or "").strip()
    }
    shadow_status = _proof_gate_status(
        actual=autonomy_policy.get("vendor_shadow_action_match_rate"),
        minimum=0.90,
        sample_count=int(autonomy_policy.get("vendor_shadow_scored_item_count") or 0),
        required_samples=4,
    )
    verification_status = _proof_gate_status(
        actual=autonomy_policy.get("vendor_post_verification_rate"),
        minimum=0.90,
        sample_count=int(autonomy_policy.get("vendor_post_verification_attempt_count") or 0),
        required_samples=2,
    )
    calibration_status = _proof_gate_status(
        actual=effective_calibration.get("success_rate"),
        minimum=0.80,
        sample_count=int(effective_calibration.get("sample_count") or 0),
        required_samples=3,
    )
    recovery_rate = effective_calibration.get("recovery_success_rate")
    recovery_status = "observe"
    if int(effective_calibration.get("sample_count") or 0) >= 1:
        if int(effective_calibration.get("metadata", {}).get("recovery_attempts_observed") or 0) > 0:
            recovery_status = "pass" if float(recovery_rate or 0.0) >= 0.5 else "fail"
    gate_statuses.update(
        {
            "shadow_decision_quality": shadow_status,
            "post_action_verification": verification_status,
            "outcome_calibration": calibration_status,
            "self_recovery": recovery_status,
        }
    )
    fail_count = sum(1 for status in gate_statuses.values() if status == "fail")
    observe_count = sum(1 for status in gate_statuses.values() if status == "observe")
    proof_status = "fail" if fail_count else ("observe" if observe_count else "pass")

    snapshot = {
        "requested_action": normalized_action,
        "autonomous_requested": bool(autonomous_requested),
        "vendor_name": vendor_name or None,
        "readiness_status": str(readiness.get("status") or "blocked"),
        "autonomy_mode": str(autonomy_policy.get("mode") or "manual"),
        "autonomous_allowed": bool(autonomy_policy.get("autonomous_allowed")),
        "calibration": effective_calibration,
        "gate_statuses": gate_statuses,
        "proof_status": proof_status,
        "proof_fail_count": fail_count,
        "proof_observe_count": observe_count,
        "retry_job_count": len(retry_jobs),
        "profile": {
            "doctrine_version": profile_data.get("doctrine_version"),
            "risk_posture": profile_data.get("risk_posture"),
            "autonomy_level": profile_data.get("autonomy_level"),
        },
    }
    scope = "ap_item" if item.get("id") else "organization"
    scope_id = str(item.get("id") or "").strip() or None
    try:
        memory.record_eval_snapshot(
            skill_id=skill_id,
            scope=scope,
            scope_id=scope_id,
            snapshot_type="quality_snapshot",
            payload=snapshot,
        )
    except Exception as exc:
        logger.debug("quality snapshot persistence skipped: %s", exc)
    return snapshot


def evaluate_doctrine(
    *,
    profile: Optional[Dict[str, Any]],
    requested_action: Any,
    quality_snapshot: Dict[str, Any],
    belief: Optional[Dict[str, Any]] = None,
    autonomous_requested: bool = False,
) -> Dict[str, Any]:
    profile_data = dict(profile or {})
    belief_data = dict(belief or {})
    normalized_action = _normalize_action_token(requested_action)
    forbidden_actions = {
        str(value or "").strip().lower()
        for value in (profile_data.get("forbidden_actions") or [])
        if str(value or "").strip()
    }
    promotion_gate = profile_data.get("promotion_gate_status")
    required_gates = [
        str(value or "").strip()
        for value in ((promotion_gate or {}).get("required_gates") or [])
        if str(value or "").strip()
    ]
    gate_statuses = quality_snapshot.get("gate_statuses") if isinstance(quality_snapshot.get("gate_statuses"), dict) else {}
    missing_required_gates = [
        gate for gate in required_gates
        if str(gate_statuses.get(gate) or "observe").strip().lower() != "pass"
    ]
    reason_codes: List[str] = []
    checks: List[Dict[str, Any]] = []

    forbidden_hit = normalized_action in forbidden_actions
    checks.append({"check": "forbidden_actions", "status": "fail" if forbidden_hit else "pass"})
    if forbidden_hit:
        reason_codes.append(f"forbidden_action:{normalized_action}")

    risky_action = normalized_action in _RISKY_ACTIONS
    promotion_block = bool(autonomous_requested and risky_action and missing_required_gates)
    checks.append(
        {
            "check": "promotion_gates",
            "status": "fail" if promotion_block else ("observe" if autonomous_requested and missing_required_gates else "pass"),
            "missing_gates": missing_required_gates,
        }
    )
    if promotion_block:
        reason_codes.extend([f"missing_gate:{gate}" for gate in missing_required_gates])

    autonomy_block = bool(autonomous_requested and risky_action and not quality_snapshot.get("autonomous_allowed"))
    checks.append({"check": "autonomy_policy", "status": "fail" if autonomy_block else "pass"})
    if autonomy_block:
        reason_codes.append("autonomy_not_earned")

    belief_next_action = str(
        (belief_data.get("next_action") or {}).get("type")
        or belief_data.get("next_action_type")
        or ""
    ).strip().lower()
    belief_block = bool(
        autonomous_requested
        and risky_action
        and belief_next_action in {"human_field_review", "await_vendor_info", "operator_recovery"}
    )
    checks.append({"check": "belief_alignment", "status": "fail" if belief_block else "pass"})
    if belief_block:
        reason_codes.append(f"belief_requires:{belief_next_action}")

    blocked = any(check.get("status") == "fail" for check in checks)
    detail = (
        "Doctrine allows execution."
        if not blocked
        else "Doctrine blocked execution until: " + ", ".join(reason_codes)
    )
    return {
        "blocked": blocked,
        "reason_codes": list(dict.fromkeys([code for code in reason_codes if code])),
        "checks": checks,
        "detail": detail,
    }


def build_deliberation(
    *,
    runtime: Any,
    request: Any,
    action: Any,
    ap_item: Optional[Dict[str, Any]],
    belief: Optional[Dict[str, Any]],
    recall: Optional[List[Dict[str, Any]]],
    profile: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    item = dict(ap_item or {})
    belief_data = dict(belief or {})
    recall_rows = list(recall or [])
    requested_action = _normalize_action_token(getattr(action, "action", None) or getattr(request, "task_type", None))
    payload = getattr(request, "payload", {}) if isinstance(getattr(request, "payload", {}), dict) else {}
    autonomous_requested = _autonomous_requested(runtime, payload)
    quality_snapshot = build_agent_quality_snapshot(
        runtime,
        requested_action=requested_action,
        autonomous_requested=autonomous_requested,
        profile=profile,
        ap_item=item,
        skill_id=str(getattr(request, "skill_id", "ap_v1") or "ap_v1"),
    )
    doctrine = evaluate_doctrine(
        profile=profile,
        requested_action=requested_action,
        quality_snapshot=quality_snapshot,
        belief=belief_data,
        autonomous_requested=autonomous_requested,
    )

    top_recall = recall_rows[0] if recall_rows else {}
    top_recall_score = float(top_recall.get("score") or 0.0)
    belief_next_action = str(
        (belief_data.get("next_action") or {}).get("type")
        or (belief_data.get("next_action") or {}).get("label")
        or ""
    ).strip().lower()
    aligned_actions = _BELIEF_ACTION_ALIGNMENT.get(belief_next_action, {requested_action})
    alignment_score = 1.0 if requested_action in aligned_actions else (0.55 if not belief_next_action else 0.2)
    calibration = quality_snapshot.get("calibration") if isinstance(quality_snapshot.get("calibration"), dict) else {}
    calibration_score = float(calibration.get("success_rate") or 0.6)
    proof_status = str(quality_snapshot.get("proof_status") or "observe").strip().lower()
    proof_score = {"pass": 1.0, "observe": 0.65, "fail": 0.25}.get(proof_status, 0.5)
    recall_score = min(1.0, top_recall_score / 6.0) if top_recall_score else 0.0
    deliberation_confidence = round(
        (alignment_score * 0.35) + (calibration_score * 0.25) + (proof_score * 0.25) + (recall_score * 0.15),
        4,
    )
    recommended_action = belief_next_action or requested_action
    should_pause = doctrine.get("blocked", False) or (
        autonomous_requested
        and
        requested_action in _RISKY_ACTIONS
        and belief_next_action in {"human_field_review", "await_vendor_info"}
        and requested_action not in aligned_actions
    )
    return {
        "requested_action": requested_action,
        "autonomous_requested": autonomous_requested,
        "recommended_action": recommended_action,
        "confidence": deliberation_confidence,
        "belief_next_action": belief_next_action or None,
        "top_recall_score": top_recall_score,
        "top_recall_match_reasons": list(top_recall.get("match_reasons") or []),
        "alignment_score": round(alignment_score, 4),
        "quality_snapshot": quality_snapshot,
        "doctrine": doctrine,
        "should_execute": not should_pause,
        "stop_reason": doctrine.get("detail") if should_pause else None,
    }


async def attempt_self_recovery(
    runtime: Any,
    *,
    request: Any,
    response: Optional[Dict[str, Any]],
    ap_item: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    item = dict(ap_item or {})
    data = dict(response or {})
    ap_item_id = str(item.get("id") or data.get("ap_item_id") or "").strip()
    current_state = str(item.get("state") or "").strip().lower()
    status = str(data.get("status") or "").strip().lower()
    reason = str(data.get("reason") or "").strip().lower()
    if not ap_item_id:
        return {"attempted": False}

    transient_failure = any(
        token in " ".join([status, reason, str(item.get("last_error") or "").lower()])
        for token in ("timeout", "temporar", "unavailable", "recoverable", "retry")
    )

    if current_state in {"failed_post", "ready_to_post"} or status in {"failed_post"} or transient_failure:
        try:
            from clearledgr.services.invoice_workflow import get_invoice_workflow

            workflow = get_invoice_workflow(runtime.organization_id)
            outcome = await workflow.resume_workflow(ap_item_id)
            return {
                "attempted": True,
                "strategy": "resume_workflow",
                "recovered": str(outcome.get("status") or "").strip().lower() in {"posted_to_erp", "already_posted"},
                "outcome": outcome,
            }
        except Exception as exc:
            logger.warning("self recovery resume_workflow failed for %s: %s", ap_item_id, exc)
            return {
                "attempted": True,
                "strategy": "resume_workflow",
                "recovered": False,
                "error": str(exc),
            }

    if reason in {"workflow_execution_failed", "planner_failed"}:
        payload = dict(getattr(request, "payload", {}) or {})
        invoice_payload = (
            payload.get("invoice_payload")
            if isinstance(payload.get("invoice_payload"), dict) else
            payload.get("invoice")
            if isinstance(payload.get("invoice"), dict) else
            payload
        )
        attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else None
        try:
            refreshed = runtime.refresh_invoice_record_from_extraction(
                invoice_payload=invoice_payload,
                attachments=attachments,
                correlation_id=getattr(request, "correlation_id", None),
                refresh_reason="self_recovery_refresh",
            )
            return {
                "attempted": True,
                "strategy": "extraction_refresh",
                "recovered": str(refreshed.get("status") or "").strip().lower() == "refreshed",
                "outcome": refreshed,
            }
        except Exception as exc:
            logger.warning("self recovery refresh failed for %s: %s", ap_item_id, exc)
            return {
                "attempted": True,
                "strategy": "extraction_refresh",
                "recovered": False,
                "error": str(exc),
            }

    return {"attempted": False}
