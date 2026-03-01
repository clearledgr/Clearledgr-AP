"""Canonical contracts for the Finance AI Agent runtime.

These contracts keep one shared execution shape across skill domains.
AP v1 is the first production skill, but future skills must reuse
the same request/response/action/audit schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SkillCapabilityManifest:
    """Skill package contract required for every finance skill implementation."""

    skill_id: str
    version: str
    state_machine: Dict[str, Any] = field(default_factory=dict)
    action_catalog: List[Dict[str, Any]] = field(default_factory=list)
    policy_pack: Dict[str, Any] = field(default_factory=dict)
    evidence_schema: Dict[str, Any] = field(default_factory=dict)
    adapter_bindings: Dict[str, List[str]] = field(default_factory=dict)
    kpi_contract: Dict[str, Any] = field(default_factory=dict)

    def missing_requirements(self) -> List[str]:
        missing: List[str] = []
        if not str(self.skill_id or "").strip():
            missing.append("skill_id")
        if not str(self.version or "").strip():
            missing.append("version")
        if not isinstance(self.state_machine, dict) or not self.state_machine:
            missing.append("state_machine")
        if not isinstance(self.action_catalog, list) or not self.action_catalog:
            missing.append("action_catalog")
        if not isinstance(self.policy_pack, dict) or not self.policy_pack:
            missing.append("policy_pack")
        if not isinstance(self.evidence_schema, dict) or not self.evidence_schema:
            missing.append("evidence_schema")
        if not isinstance(self.adapter_bindings, dict) or not self.adapter_bindings:
            missing.append("adapter_bindings")
        if not isinstance(self.kpi_contract, dict) or not self.kpi_contract:
            missing.append("kpi_contract")
        return missing

    def is_valid(self) -> bool:
        return len(self.missing_requirements()) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "state_machine": dict(self.state_machine or {}),
            "action_catalog": list(self.action_catalog or []),
            "policy_pack": dict(self.policy_pack or {}),
            "evidence_schema": dict(self.evidence_schema or {}),
            "adapter_bindings": dict(self.adapter_bindings or {}),
            "kpi_contract": dict(self.kpi_contract or {}),
            "missing_requirements": self.missing_requirements(),
            "is_valid": self.is_valid(),
        }


@dataclass(frozen=True)
class SkillRequest:
    """Canonical unit-of-work request dispatched to a skill."""

    org_id: str
    skill_id: str
    task_type: str
    entity_id: str
    correlation_id: str
    payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_intent(
        cls,
        *,
        org_id: str,
        task_type: str,
        skill_id: str,
        entity_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> "SkillRequest":
        return cls(
            org_id=str(org_id or "default"),
            skill_id=str(skill_id or "unknown"),
            task_type=str(task_type or "").strip().lower(),
            entity_id=str(entity_id or "").strip(),
            correlation_id=str(correlation_id or "").strip(),
            payload=dict(payload or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "org_id": self.org_id,
            "skill_id": self.skill_id,
            "task_type": self.task_type,
            "entity_id": self.entity_id,
            "correlation_id": self.correlation_id,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class ActionExecution:
    """Canonical action envelope for preview/run semantics."""

    entity_id: str
    action: str
    preview: bool
    idempotency_key: str
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "action": self.action,
            "preview": bool(self.preview),
            "reason": self.reason,
            "idempotency_key": self.idempotency_key,
        }


@dataclass
class SkillResponse:
    """Canonical skill response contract.

    ``details`` is preserved to avoid breaking existing AP response payloads.
    ``to_dict`` flattens details with canonical keys overlaid.
    """

    status: str
    recommended_next_action: str
    legal_actions: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence_refs: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy(
        cls,
        payload: Dict[str, Any],
        *,
        fallback_status: str = "failed",
        default_recommended_action: str = "",
    ) -> "SkillResponse":
        data = dict(payload or {})
        precheck = data.get("policy_precheck") if isinstance(data.get("policy_precheck"), dict) else {}
        reason_codes = precheck.get("reason_codes")
        blockers = list(reason_codes or data.get("blockers") or [])
        legal_actions = list(data.get("legal_actions") or [])
        if not legal_actions and str(data.get("status") or "") not in {"blocked", "failed", "error"}:
            next_step = str(data.get("next_step") or "").strip()
            intent = str(data.get("intent") or "").strip()
            if next_step:
                legal_actions.append(next_step)
            elif intent:
                legal_actions.append(intent)

        evidence_refs: List[str] = []
        for key in ("email_id", "ap_item_id", "draft_id", "erp_reference", "audit_event_id"):
            token = str(data.get(key) or "").strip()
            if token:
                evidence_refs.append(token)

        return cls(
            status=str(data.get("status") or fallback_status),
            recommended_next_action=str(
                data.get("recommended_next_action")
                or data.get("next_step")
                or default_recommended_action
                or ""
            ),
            legal_actions=legal_actions,
            blockers=blockers,
            confidence=_safe_float(data.get("confidence"), 0.0),
            evidence_refs=evidence_refs,
            details=data,
        )

    def to_dict(self) -> Dict[str, Any]:
        data = dict(self.details or {})
        data["status"] = self.status
        data["recommended_next_action"] = self.recommended_next_action
        data["legal_actions"] = list(self.legal_actions)
        data["blockers"] = list(self.blockers)
        data["confidence"] = float(self.confidence)
        data["evidence_refs"] = list(self.evidence_refs)
        return data


@dataclass(frozen=True)
class AuditEvent:
    """Canonical audit event schema emitted by finance skills."""

    org_id: str
    skill_id: str
    entity_id: str
    action: str
    actor: str
    outcome: str
    correlation_id: str = ""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=_utcnow_iso)
    evidence_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "org_id": self.org_id,
            "skill_id": self.skill_id,
            "entity_id": self.entity_id,
            "action": self.action,
            "actor": self.actor,
            "outcome": self.outcome,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "evidence_refs": list(self.evidence_refs),
        }
