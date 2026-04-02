"""Readiness helpers extracted from FinanceAgentRuntime."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


def collect_operator_acceptance(runtime: Any, ap_kpis: Dict[str, Any]) -> Dict[str, Any]:
    telemetry = (ap_kpis or {}).get("agentic_telemetry")
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    acceptance = telemetry.get("agent_suggestion_acceptance")
    acceptance = acceptance if isinstance(acceptance, dict) else {}
    rate = acceptance.get("rate")
    if rate is None:
        return {
            "status": "not_verifiable",
            "rate": None,
            "prompted_count": 0,
            "accepted_count": 0,
        }
    return {
        "status": "measured",
        "rate": round(runtime._safe_float(rate), 4),
        "prompted_count": int(acceptance.get("prompted_count") or 0),
        "accepted_count": int(acceptance.get("accepted_count") or 0),
    }


def collect_connector_readiness(runtime: Any) -> Dict[str, Any]:
    try:
        from clearledgr.services.erp_readiness import evaluate_erp_connector_readiness

        report = evaluate_erp_connector_readiness(
            runtime.organization_id,
            db=runtime.db,
            require_full_ga_scope=False,
        )
    except Exception as exc:
        logger.warning(
            "Connector readiness unavailable for org=%s: %s",
            runtime.organization_id,
            exc,
        )
        return {
            "status": "not_verifiable",
            "enabled_readiness_rate": None,
            "enabled_connectors_total": 0,
            "enabled_connectors_ready": 0,
            "notes": "connector_readiness_unavailable",
        }

    summary = report.get("summary") if isinstance(report, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    return {
        "status": str(summary.get("status") or "not_verifiable"),
        "enabled_readiness_rate": summary.get("enabled_readiness_rate"),
        "enabled_connectors_total": int(summary.get("enabled_connectors_total") or 0),
        "enabled_connectors_ready": int(summary.get("enabled_connectors_ready") or 0),
        "configured_connectors": list(summary.get("configured_connectors") or []),
        "blocked_reasons": list(summary.get("blocked_reasons") or []),
        "report": report,
    }


def evaluate_gate(
    *,
    gate_key: str,
    target: Optional[float],
    measured: Optional[float],
    metric_name: str,
) -> Dict[str, Any]:
    if target is None:
        return {
            "gate": gate_key,
            "metric": metric_name,
            "status": "not_configured",
            "target": None,
            "actual": measured,
        }
    if measured is None:
        return {
            "gate": gate_key,
            "metric": metric_name,
            "status": "not_verifiable",
            "target": float(target),
            "actual": None,
        }
    status = "pass" if measured >= target else "fail"
    return {
        "gate": gate_key,
        "metric": metric_name,
        "status": status,
        "target": float(target),
        "actual": round(float(measured), 4),
    }


def build_skill_readiness(runtime: Any, skill_id: str, *, window_hours: int = 168) -> Dict[str, Any]:
    token = str(skill_id or "").strip().lower()
    skill = runtime._skills.get(token)
    if skill is None:
        raise LookupError("skill_not_found")

    manifest = skill.manifest.to_dict()
    base: Dict[str, Any] = {
        "organization_id": runtime.organization_id,
        "skill_id": token,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": int(max(1, min(int(window_hours or 168), 720))),
        "manifest": manifest,
        "manifest_status": "valid" if manifest.get("is_valid") else "invalid",
        "blocked_reasons": [],
    }
    if not manifest.get("is_valid"):
        base["blocked_reasons"].append("manifest_incomplete")

    if hasattr(skill, "collect_runtime_metrics"):
        skill_metrics = skill.collect_runtime_metrics(runtime, window_hours=window_hours)
        if skill_metrics is not None:
            base.update(skill_metrics)
            if "status" not in base:
                base["status"] = "ready" if not base.get("blocked_reasons") else "blocked"
            return base

    if token != "ap_v1":
        base["status"] = "manifest_only"
        base["gates"] = []
        base["blocked_reasons"].append("runtime_metrics_not_defined_for_skill")
        return base

    ap_kpis: Dict[str, Any] = {}
    if hasattr(runtime.db, "get_ap_kpis"):
        try:
            ap_kpis = runtime.db.get_ap_kpis(
                runtime.organization_id,
                approval_sla_minutes=runtime._approval_sla_minutes(),
            )
        except Exception as exc:
            logger.warning("AP KPI snapshot unavailable for org=%s: %s", runtime.organization_id, exc)
            ap_kpis = {}

    operational_metrics: Dict[str, Any] = {}
    if hasattr(runtime.db, "get_operational_metrics"):
        try:
            operational_metrics = runtime.db.get_operational_metrics(
                runtime.organization_id,
                approval_sla_minutes=runtime._approval_sla_minutes(),
                workflow_stuck_minutes=runtime._workflow_stuck_minutes(),
            )
        except Exception as exc:
            logger.warning("Operational metrics unavailable for org=%s: %s", runtime.organization_id, exc)
            operational_metrics = {}

    transition = runtime._collect_transition_integrity()
    idempotency = runtime._collect_idempotency_integrity()
    audit_coverage = runtime._collect_audit_coverage()
    operator_acceptance = collect_operator_acceptance(runtime, ap_kpis)
    connector_readiness = collect_connector_readiness(runtime)

    gate_targets = ((skill.manifest.kpi_contract or {}).get("promotion_gates") or {})
    legal_target = gate_targets.get("legal_transition_correctness_min")
    idempotency_target = gate_targets.get("idempotency_integrity_min")
    audit_target = gate_targets.get("audit_coverage_min")
    operator_target = gate_targets.get("operator_acceptance_min")
    connector_target = gate_targets.get("enabled_connector_readiness_min")

    gates = [
        evaluate_gate(
            gate_key="legal_transition_correctness",
            target=runtime._safe_float(legal_target) if legal_target is not None else None,
            measured=transition.get("legal_transition_correctness"),
            metric_name="transition_integrity.legal_transition_correctness",
        ),
        evaluate_gate(
            gate_key="idempotency_integrity",
            target=runtime._safe_float(idempotency_target) if idempotency_target is not None else None,
            measured=idempotency.get("integrity_rate"),
            metric_name="idempotency_integrity.integrity_rate",
        ),
        evaluate_gate(
            gate_key="audit_coverage",
            target=runtime._safe_float(audit_target) if audit_target is not None else None,
            measured=audit_coverage.get("coverage_rate"),
            metric_name="audit_coverage.coverage_rate",
        ),
        evaluate_gate(
            gate_key="operator_acceptance",
            target=runtime._safe_float(operator_target) if operator_target is not None else None,
            measured=operator_acceptance.get("rate"),
            metric_name="operator_acceptance.rate",
        ),
        evaluate_gate(
            gate_key="enabled_connector_readiness",
            target=runtime._safe_float(connector_target) if connector_target is not None else None,
            measured=connector_readiness.get("enabled_readiness_rate"),
            metric_name="connector_readiness.enabled_readiness_rate",
        ),
    ]

    gate_failures = [
        gate["gate"]
        for gate in gates
        if gate.get("status") in {"fail", "not_verifiable", "not_configured"}
    ]
    base["blocked_reasons"].extend(gate_failures)
    base["gates"] = gates
    base["metrics"] = {
        "transition_integrity": transition,
        "idempotency_integrity": idempotency,
        "audit_coverage": audit_coverage,
        "operator_acceptance": operator_acceptance,
        "connector_readiness": connector_readiness,
        "ap_kpis": ap_kpis,
        "operational_metrics": operational_metrics,
    }
    base["status"] = "ready" if not base["blocked_reasons"] else "blocked"
    return base


def ap_kpis_snapshot(runtime: Any) -> Dict[str, Any]:
    if not hasattr(runtime.db, "get_ap_kpis"):
        return {}
    try:
        return runtime.db.get_ap_kpis(
            runtime.organization_id,
            approval_sla_minutes=runtime._approval_sla_minutes(),
        ) or {}
    except Exception as exc:
        logger.warning("AP KPI snapshot unavailable for org=%s: %s", runtime.organization_id, exc)
        return {}


def readiness_gate_failures(readiness: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    for gate in readiness.get("gates") or []:
        if not isinstance(gate, dict):
            continue
        status = str(gate.get("status") or "").strip().lower()
        gate_key = str(gate.get("gate") or "").strip()
        if gate_key and status in {"fail", "not_verifiable", "not_configured"}:
            failures.append(gate_key)
    return failures
