"""Finance agent runtime contracts (preview/execute) with skill registry dispatch.

This module defines a stable runtime seam so operator surfaces (Gmail, Slack,
future chat surfaces) call a consistent intent contract. Execution logic is
packaged as finance skills and dispatched by intent.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.ap_item_resolution import resolve_ap_item_reference
from clearledgr.core.database import get_db
from clearledgr.core.finance_contracts import (
    ActionExecution,
    AuditEvent,
    SkillRequest,
)
from clearledgr.services.finance_skills import (
    APFinanceSkill,
    FinanceSkill,
    VendorComplianceSkill,
    WorkflowHealthSkill,
)

logger = logging.getLogger(__name__)


class IntentNotSupportedError(ValueError):
    """Raised when an unknown finance agent intent is requested."""


class FinanceAgentRuntime:
    """Tenant-scoped finance agent runtime with intent-skill dispatch."""

    def __init__(
        self,
        *,
        organization_id: str,
        actor_id: str,
        actor_email: Optional[str] = None,
        db: Any = None,
    ) -> None:
        self.organization_id = str(organization_id or "default")
        self.actor_id = str(actor_id or "system")
        self.actor_email = str(actor_email or actor_id or "system")
        self.db = db or get_db()
        self._skills: Dict[str, FinanceSkill] = {}
        self._intent_skill_map: Dict[str, FinanceSkill] = {}
        self._register_default_skills()

    def _register_default_skills(self) -> None:
        self.register_skill(APFinanceSkill())
        self.register_skill(VendorComplianceSkill())
        self.register_skill(WorkflowHealthSkill())
        # Lazy import to avoid circular dependency
        from clearledgr.services.finance_skills.recon_skill import ReconciliationFinanceSkill
        self.register_skill(ReconciliationFinanceSkill())

    def register_skill(self, skill: FinanceSkill) -> None:
        """Register a skill and map all of its intents."""
        skill_id = str(skill.skill_id or "").strip().lower()
        if not skill_id:
            raise ValueError("missing_skill_id")
        self._skills[skill_id] = skill
        for raw_intent in skill.intents:
            intent = str(raw_intent or "").strip().lower()
            if not intent:
                continue
            self._intent_skill_map[intent] = skill

    @property
    def supported_intents(self) -> frozenset[str]:
        return frozenset(self._intent_skill_map.keys())

    def list_skills(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for skill_id, skill in sorted(self._skills.items()):
            manifest = skill.manifest.to_dict()
            rows.append(
                {
                    "skill_id": skill_id,
                    "intents": sorted(list(skill.intents)),
                    "manifest": manifest,
                    "readiness": self.skill_readiness_summary(skill_id),
                }
            )
        return rows

    def skill_readiness_summary(self, skill_id: str) -> Dict[str, Any]:
        token = str(skill_id or "").strip().lower()
        skill = self._skills.get(token)
        if skill is None:
            raise LookupError("skill_not_found")
        manifest = skill.manifest.to_dict()
        return {
            "status": "manifest_valid" if manifest.get("is_valid") else "manifest_incomplete",
            "missing_requirements": list(manifest.get("missing_requirements") or []),
            "has_runtime_metrics": token == "ap_v1",
        }

    @staticmethod
    def _parse_json_dict(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                value = json.loads(raw)
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}
        return {}

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _approval_sla_minutes() -> int:
        raw = os.getenv("AP_APPROVAL_SLA_MINUTES", "240")
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 240

    @staticmethod
    def _workflow_stuck_minutes() -> int:
        raw = os.getenv("AP_WORKFLOW_STUCK_MINUTES", "120")
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 120

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _parse_iso_utc(raw: Any) -> Optional[datetime]:
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _vendor_followup_sla_hours() -> int:
        try:
            hours = int(os.getenv("CLEARLEDGR_VENDOR_FOLLOWUP_SLA_HOURS", "24"))
        except (TypeError, ValueError):
            hours = 24
        return max(1, min(hours, 168))

    @staticmethod
    def _vendor_followup_max_attempts() -> int:
        try:
            attempts = int(os.getenv("CLEARLEDGR_VENDOR_FOLLOWUP_MAX_ATTEMPTS", "3"))
        except (TypeError, ValueError):
            attempts = 3
        return max(1, min(attempts, 10))

    @staticmethod
    def _item_reference(payload: Dict[str, Any]) -> str:
        return str(
            payload.get("ap_item_id")
            or payload.get("item_id")
            or payload.get("email_id")
            or ""
        ).strip()

    @staticmethod
    def _normalize_correlation_id(payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("correlation_id") or payload.get("run_id") or "").strip()

    def _ensure_supported(self, intent: str) -> str:
        normalized = str(intent or "").strip().lower()
        if normalized not in self._intent_skill_map:
            raise IntentNotSupportedError(f"unsupported_intent:{normalized or 'missing'}")
        return normalized

    def _skill_for_intent(self, intent: str) -> FinanceSkill:
        normalized = self._ensure_supported(intent)
        return self._intent_skill_map[normalized]

    def _build_skill_request(
        self,
        *,
        intent: str,
        payload: Dict[str, Any],
    ) -> SkillRequest:
        normalized_intent = self._ensure_supported(intent)
        skill = self._skill_for_intent(normalized_intent)
        reference = self._item_reference(payload)
        return SkillRequest.from_intent(
            org_id=self.organization_id,
            skill_id=skill.skill_id,
            task_type=normalized_intent,
            entity_id=reference,
            correlation_id=self._normalize_correlation_id(payload),
            payload=payload,
        )

    def _resolve_ap_item(self, reference: str) -> Dict[str, Any]:
        ref = str(reference or "").strip()
        if not ref:
            raise ValueError("missing_item_reference")

        item = resolve_ap_item_reference(
            self.db,
            self.organization_id,
            ref,
            allow_foreign_id=True,
        )

        if not item:
            raise LookupError("ap_item_not_found")
        if str(item.get("organization_id") or self.organization_id) != self.organization_id:
            raise PermissionError("organization_mismatch")
        return item

    def _correlation_id_for_item(self, item: Dict[str, Any]) -> Optional[str]:
        metadata = self._parse_json_dict(item.get("metadata"))
        correlation_id = str(item.get("correlation_id") or metadata.get("correlation_id") or "").strip()
        return correlation_id or None

    def _organization_settings(self) -> Dict[str, Any]:
        if not hasattr(self.db, "get_organization"):
            return {}
        try:
            organization = self.db.get_organization(self.organization_id) or {}
        except Exception:
            return {}
        raw_settings = (
            organization.get("settings_json")
            or organization.get("settings")
            or {}
        )
        return self._parse_json_dict(raw_settings)

    def _merge_item_metadata(self, item: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        metadata = self._parse_json_dict(item.get("metadata"))
        metadata.update(updates or {})
        item["metadata"] = metadata
        ap_item_id = str(item.get("id") or "").strip()
        if ap_item_id and hasattr(self.db, "update_ap_item"):
            try:
                self.db.update_ap_item(ap_item_id, metadata=metadata)
            except Exception:
                pass
        return metadata

    def _load_idempotent_response(self, idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        existing = self.db.get_ap_audit_event_by_key(key)
        if not existing:
            return None
        payload = existing.get("payload_json") if isinstance(existing, dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        response = payload.get("response")
        if isinstance(response, dict):
            replay = dict(response)
            replay.setdefault("audit_event_id", existing.get("id"))
            replay["idempotency_replayed"] = True
            return replay
        return {
            "intent": "unknown",
            "status": "idempotent_replay",
            "audit_event_id": existing.get("id"),
            "idempotency_replayed": True,
        }

    def _append_runtime_audit(
        self,
        *,
        ap_item_id: str,
        event_type: str,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        skill_id: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        metadata_payload = dict(metadata or {})
        response_payload = (
            metadata_payload.get("response")
            if isinstance(metadata_payload.get("response"), dict)
            else {}
        )
        resolved_skill_id = str(
            skill_id
            or metadata_payload.get("skill_id")
            or response_payload.get("skill_id")
            or "unknown"
        )
        resolved_evidence_refs = list(evidence_refs or [])
        if not resolved_evidence_refs:
            for key in ("email_id", "ap_item_id", "draft_id", "erp_reference", "audit_event_id"):
                token = str(response_payload.get(key) or "").strip()
                if token:
                    resolved_evidence_refs.append(token)
        canonical_event = AuditEvent(
            org_id=self.organization_id,
            skill_id=resolved_skill_id,
            entity_id=ap_item_id,
            action=event_type,
            actor="human" if self.actor_email else "system",
            outcome=reason,
            correlation_id=str(correlation_id or "").strip(),
            evidence_refs=resolved_evidence_refs,
        )
        metadata_payload.setdefault("canonical_audit_event", canonical_event.to_dict())
        return self.db.append_ap_audit_event(
            {
                "ap_item_id": ap_item_id,
                "event_type": event_type,
                "actor_type": "user",
                "actor_id": self.actor_email,
                "reason": reason,
                "metadata": metadata_payload,
                "organization_id": self.organization_id,
                "source": "finance_agent_runtime",
                "correlation_id": correlation_id,
                "idempotency_key": idempotency_key,
            }
        )

    def _evaluate_prepare_vendor_followup(
        self,
        ap_item: Dict[str, Any],
        *,
        force: bool,
    ) -> Dict[str, Any]:
        state = str(ap_item.get("state") or "").strip().lower()
        metadata = self._parse_json_dict(ap_item.get("metadata"))
        attempts = max(0, self._safe_int(metadata.get("followup_attempt_count"), 0))
        max_attempts = self._vendor_followup_max_attempts()
        sla_hours = self._vendor_followup_sla_hours()
        now = datetime.now(timezone.utc)

        last_sent_at = self._parse_iso_utc(metadata.get("followup_last_sent_at"))
        next_due_at = self._parse_iso_utc(metadata.get("followup_sla_due_at")) or (
            (last_sent_at + timedelta(hours=sla_hours)) if last_sent_at else None
        )

        reason_codes = []
        if state != "needs_info":
            reason_codes.append("state_not_needs_info")
        if attempts >= max_attempts and not force:
            reason_codes.append("followup_attempt_limit_reached")
        if next_due_at and now < next_due_at and not force:
            reason_codes.append("waiting_for_sla_window")

        return {
            "eligible": len(reason_codes) == 0,
            "state": state or None,
            "reason_codes": reason_codes,
            "force": force,
            "followup_attempt_count": attempts,
            "max_attempts": max_attempts,
            "followup_sla_due_at": next_due_at.isoformat() if next_due_at else None,
            "next_allowed_at": next_due_at.isoformat() if next_due_at else None,
        }

    def _list_ap_items(self, limit: int = 2000) -> List[Dict[str, Any]]:
        if not hasattr(self.db, "list_ap_items"):
            return []
        safe_limit = max(1, min(int(limit or 2000), 10000))
        try:
            rows = self.db.list_ap_items(self.organization_id, limit=safe_limit)
        except TypeError:
            rows = self.db.list_ap_items(self.organization_id)
            rows = rows[:safe_limit] if isinstance(rows, list) else []
        except Exception:
            rows = []
        return rows if isinstance(rows, list) else []

    def _list_ap_audit_events(self, ap_item_id: str) -> List[Dict[str, Any]]:
        if not ap_item_id or not hasattr(self.db, "list_ap_audit_events"):
            return []
        try:
            rows = self.db.list_ap_audit_events(ap_item_id)
        except Exception:
            rows = []
        return rows if isinstance(rows, list) else []

    def _collect_transition_integrity(self, *, max_items: int = 2000) -> Dict[str, Any]:
        items = self._list_ap_items(limit=max_items)
        if not items or not hasattr(self.db, "list_ap_audit_events"):
            return {
                "status": "not_verifiable",
                "legal_transition_correctness": None,
                "transition_attempt_count": 0,
                "rejected_transition_count": 0,
                "notes": "ap_audit_events_unavailable",
            }

        transition_attempt_count = 0
        rejected_transition_count = 0
        for item in items:
            ap_item_id = str((item or {}).get("id") or "").strip()
            if not ap_item_id:
                continue
            for event in self._list_ap_audit_events(ap_item_id):
                event_type = str((event or {}).get("event_type") or "").strip().lower()
                if event_type not in {"state_transition", "state_transition_rejected"}:
                    continue
                transition_attempt_count += 1
                reason = str(
                    (event or {}).get("decision_reason")
                    or (event or {}).get("reason")
                    or ""
                ).strip().lower()
                if event_type == "state_transition_rejected" or "illegal_transition" in reason:
                    rejected_transition_count += 1

        if transition_attempt_count == 0:
            return {
                "status": "not_verifiable",
                "legal_transition_correctness": None,
                "transition_attempt_count": 0,
                "rejected_transition_count": 0,
                "notes": "no_transition_events",
            }

        legal_transition_correctness = (
            transition_attempt_count - rejected_transition_count
        ) / max(1, transition_attempt_count)
        return {
            "status": "measured",
            "legal_transition_correctness": round(legal_transition_correctness, 4),
            "transition_attempt_count": int(transition_attempt_count),
            "rejected_transition_count": int(rejected_transition_count),
        }

    def _collect_idempotency_integrity(self, *, max_items: int = 2000) -> Dict[str, Any]:
        items = self._list_ap_items(limit=max_items)
        if not items or not hasattr(self.db, "list_ap_audit_events"):
            return {
                "status": "not_verifiable",
                "integrity_rate": None,
                "idempotent_event_count": 0,
                "duplicate_key_count": 0,
                "notes": "ap_audit_events_unavailable",
            }

        keys: List[str] = []
        for item in items:
            ap_item_id = str((item or {}).get("id") or "").strip()
            if not ap_item_id:
                continue
            for event in self._list_ap_audit_events(ap_item_id):
                key = str((event or {}).get("idempotency_key") or "").strip()
                if key:
                    keys.append(key)

        if not keys:
            return {
                "status": "not_verifiable",
                "integrity_rate": None,
                "idempotent_event_count": 0,
                "duplicate_key_count": 0,
                "notes": "no_idempotent_events",
            }

        unique_count = len(set(keys))
        duplicate_key_count = max(0, len(keys) - unique_count)
        integrity_rate = (len(keys) - duplicate_key_count) / max(1, len(keys))
        return {
            "status": "measured",
            "integrity_rate": round(integrity_rate, 4),
            "idempotent_event_count": int(len(keys)),
            "duplicate_key_count": int(duplicate_key_count),
        }

    def _collect_audit_coverage(self, *, max_items: int = 2000) -> Dict[str, Any]:
        items = self._list_ap_items(limit=max_items)
        if not items or not hasattr(self.db, "list_ap_audit_events"):
            return {
                "status": "not_verifiable",
                "coverage_rate": None,
                "items_with_audit": 0,
                "total_items": int(len(items)),
                "notes": "ap_audit_events_unavailable",
            }

        items_with_audit = 0
        for item in items:
            ap_item_id = str((item or {}).get("id") or "").strip()
            if not ap_item_id:
                continue
            if self._list_ap_audit_events(ap_item_id):
                items_with_audit += 1

        if not items:
            return {
                "status": "not_verifiable",
                "coverage_rate": None,
                "items_with_audit": 0,
                "total_items": 0,
                "notes": "no_ap_items",
            }

        coverage_rate = items_with_audit / max(1, len(items))
        return {
            "status": "measured",
            "coverage_rate": round(coverage_rate, 4),
            "items_with_audit": int(items_with_audit),
            "total_items": int(len(items)),
        }

    def _collect_operator_acceptance(self, ap_kpis: Dict[str, Any]) -> Dict[str, Any]:
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
            "rate": round(self._safe_float(rate), 4),
            "prompted_count": int(acceptance.get("prompted_count") or 0),
            "accepted_count": int(acceptance.get("accepted_count") or 0),
        }

    def _collect_connector_readiness(self) -> Dict[str, Any]:
        try:
            from clearledgr.services.erp_readiness import evaluate_erp_connector_readiness

            report = evaluate_erp_connector_readiness(
                self.organization_id,
                db=self.db,
                require_full_ga_scope=False,
            )
        except Exception:
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

    @staticmethod
    def _evaluate_gate(
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

    def skill_readiness(self, skill_id: str, *, window_hours: int = 168) -> Dict[str, Any]:
        token = str(skill_id or "").strip().lower()
        skill = self._skills.get(token)
        if skill is None:
            raise LookupError("skill_not_found")

        manifest = skill.manifest.to_dict()
        base: Dict[str, Any] = {
            "organization_id": self.organization_id,
            "skill_id": token,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_hours": int(max(1, min(int(window_hours or 168), 720))),
            "manifest": manifest,
            "manifest_status": "valid" if manifest.get("is_valid") else "invalid",
            "blocked_reasons": [],
        }
        if not manifest.get("is_valid"):
            base["blocked_reasons"].append("manifest_incomplete")

        # Delegate to skill's collect_runtime_metrics if available.
        # This allows non-AP skills to provide their own KPI collection.
        if hasattr(skill, 'collect_runtime_metrics'):
            skill_metrics = skill.collect_runtime_metrics(self, window_hours=window_hours)
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
        if hasattr(self.db, "get_ap_kpis"):
            try:
                ap_kpis = self.db.get_ap_kpis(
                    self.organization_id,
                    approval_sla_minutes=self._approval_sla_minutes(),
                )
            except Exception:
                ap_kpis = {}

        operational_metrics: Dict[str, Any] = {}
        if hasattr(self.db, "get_operational_metrics"):
            try:
                operational_metrics = self.db.get_operational_metrics(
                    self.organization_id,
                    approval_sla_minutes=self._approval_sla_minutes(),
                    workflow_stuck_minutes=self._workflow_stuck_minutes(),
                )
            except Exception:
                operational_metrics = {}

        transition = self._collect_transition_integrity()
        idempotency = self._collect_idempotency_integrity()
        audit_coverage = self._collect_audit_coverage()
        operator_acceptance = self._collect_operator_acceptance(ap_kpis)
        connector_readiness = self._collect_connector_readiness()

        gate_targets = ((skill.manifest.kpi_contract or {}).get("promotion_gates") or {})
        legal_target = gate_targets.get("legal_transition_correctness_min")
        idempotency_target = gate_targets.get("idempotency_integrity_min")
        audit_target = gate_targets.get("audit_coverage_min")
        operator_target = gate_targets.get("operator_acceptance_min")
        connector_target = gate_targets.get("enabled_connector_readiness_min")

        gates = [
            self._evaluate_gate(
                gate_key="legal_transition_correctness",
                target=self._safe_float(legal_target) if legal_target is not None else None,
                measured=transition.get("legal_transition_correctness"),
                metric_name="transition_integrity.legal_transition_correctness",
            ),
            self._evaluate_gate(
                gate_key="idempotency_integrity",
                target=self._safe_float(idempotency_target) if idempotency_target is not None else None,
                measured=idempotency.get("integrity_rate"),
                metric_name="idempotency_integrity.integrity_rate",
            ),
            self._evaluate_gate(
                gate_key="audit_coverage",
                target=self._safe_float(audit_target) if audit_target is not None else None,
                measured=audit_coverage.get("coverage_rate"),
                metric_name="audit_coverage.coverage_rate",
            ),
            self._evaluate_gate(
                gate_key="operator_acceptance",
                target=self._safe_float(operator_target) if operator_target is not None else None,
                measured=operator_acceptance.get("rate"),
                metric_name="operator_acceptance.rate",
            ),
            self._evaluate_gate(
                gate_key="enabled_connector_readiness",
                target=self._safe_float(connector_target) if connector_target is not None else None,
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

    def preview_intent(self, intent: str, input_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = input_payload if isinstance(input_payload, dict) else {}
        request = self._build_skill_request(intent=intent, payload=payload)
        return self.preview_skill_request(request)

    def preview_skill_request(self, request: SkillRequest) -> Dict[str, Any]:
        self._ensure_supported(request.task_type)
        skill = self._skill_for_intent(request.task_type)
        response = skill.preview_contract(self, request).to_dict()
        response.setdefault("intent", request.task_type)
        response.setdefault("skill_id", skill.skill_id)
        response.setdefault("org_id", request.org_id)
        return response

    async def execute_skill_request(
        self,
        request: SkillRequest,
        *,
        action: Optional[ActionExecution] = None,
    ) -> Dict[str, Any]:
        self._ensure_supported(request.task_type)
        resolved_action = action or ActionExecution(
            entity_id=request.entity_id,
            action=request.task_type,
            preview=False,
            reason=None,
            idempotency_key="",
        )
        replay = self._load_idempotent_response(resolved_action.idempotency_key)
        if replay:
            replay.setdefault("intent", request.task_type)
            replay.setdefault("recommended_next_action", replay.get("next_step") or request.task_type)
            replay.setdefault("legal_actions", replay.get("legal_actions") or [])
            replay.setdefault("blockers", replay.get("blockers") or [])
            replay.setdefault("confidence", float(replay.get("confidence") or 0.0))
            replay.setdefault("evidence_refs", replay.get("evidence_refs") or [])
            return replay

        skill = self._skill_for_intent(request.task_type)
        response = (await skill.execute_contract(self, request, resolved_action)).to_dict()
        response.setdefault("intent", request.task_type)
        response.setdefault("skill_id", skill.skill_id)
        response.setdefault("org_id", request.org_id)
        return response

    async def execute_intent(
        self,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = input_payload if isinstance(input_payload, dict) else {}
        request = self._build_skill_request(intent=intent, payload=payload)
        action = ActionExecution(
            entity_id=request.entity_id or self._item_reference(payload),
            action=request.task_type,
            preview=False,
            reason=str(payload.get("reason") or "").strip() or None,
            idempotency_key=(
                str(idempotency_key or "").strip()
                or str(payload.get("idempotency_key") or "").strip()
            ),
        )
        return await self.execute_skill_request(request, action=action)

    async def execute_ap_invoice_processing(
        self,
        invoice_payload: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        *,
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run AP invoice processing through the canonical planning engine path."""
        from clearledgr.services.invoice_workflow import InvoiceData

        invoice = invoice_payload if isinstance(invoice_payload, dict) else {}
        gmail_id = str(invoice.get("gmail_id") or invoice.get("thread_id") or "").strip()
        resolved_idempotency_key = str(idempotency_key or "").strip() or (
            f"invoice:{gmail_id}" if gmail_id else None
        )
        resolved_correlation_id = (
            str(correlation_id or "").strip()
            or str(invoice.get("correlation_id") or "").strip()
            or None
        )
        invoice_org = str(invoice.get("organization_id") or self.organization_id or "default").strip() or "default"
        attachment_list = attachments if isinstance(attachments, list) else []
        attachment_url = ""
        if attachment_list:
            first_attachment = attachment_list[0] if isinstance(attachment_list[0], dict) else {}
            attachment_url = str(
                first_attachment.get("url")
                or first_attachment.get("attachment_url")
                or ""
            ).strip()

        amount_raw = invoice.get("amount", 0.0)
        try:
            amount_value = float(amount_raw)
        except (TypeError, ValueError):
            amount_value = 0.0

        confidence_raw = invoice.get("confidence", 0.0)
        try:
            confidence_value = float(confidence_raw)
        except (TypeError, ValueError):
            confidence_value = 0.0

        invoice_data = InvoiceData(
            gmail_id=gmail_id or str(invoice.get("message_id") or "").strip() or f"invoice-{uuid.uuid4().hex[:10]}",
            subject=str(invoice.get("subject") or "").strip() or "Invoice",
            sender=str(invoice.get("sender") or "").strip() or "unknown@unknown.local",
            vendor_name=str(invoice.get("vendor_name") or invoice.get("vendor") or "").strip() or "Unknown vendor",
            amount=amount_value,
            currency=str(invoice.get("currency") or "USD").strip() or "USD",
            invoice_number=str(invoice.get("invoice_number") or "").strip() or None,
            due_date=str(invoice.get("due_date") or "").strip() or None,
            po_number=str(invoice.get("po_number") or "").strip() or None,
            confidence=confidence_value,
            attachment_url=attachment_url or None,
            organization_id=invoice_org,
            user_id=str(invoice.get("user_id") or self.actor_id or "").strip() or None,
            invoice_text=str(invoice.get("invoice_text") or "").strip() or None,
            correlation_id=resolved_correlation_id,
            field_confidences=invoice.get("field_confidences") if isinstance(invoice.get("field_confidences"), dict) else None,
        )

        # Route through AgentPlanningEngine (Claude tool-use planning loop).
        # Fail closed if planner is unavailable; never bypass policy gates with
        # a direct workflow fallback.
        try:
            from clearledgr.core.agent_runtime import get_planning_engine
            from clearledgr.core.skills.base import AgentTask

            planner = get_planning_engine()
            if "ap_invoice_processing" not in planner._skills:
                raise RuntimeError("APSkill not registered")

            task = AgentTask(
                task_type="ap_invoice_processing",
                organization_id=invoice_org,
                payload={"invoice": invoice_data.__dict__},
                idempotency_key=resolved_idempotency_key,
                correlation_id=resolved_correlation_id,
            )
            skill_result = await planner.run_task(task)

            response = dict(skill_result.outcome or {})
            response["execution_mode"] = "agent_planning_engine"
            response["task_run_id"] = skill_result.task_run_id
            response["step_count"] = skill_result.step_count
            response["agent_status"] = skill_result.status
            if skill_result.status == "failed":
                response.setdefault("status", "error")
                response.setdefault("reason", str(skill_result.error or "agent_planning_failed"))
            elif skill_result.status == "awaiting_human":
                response.setdefault("status", "pending_approval")
            elif skill_result.status == "max_steps_exceeded":
                response.setdefault("status", "error")
                response.setdefault("reason", "agent_max_steps_exceeded")
        except Exception as planner_exc:
            logger.error(
                "[FinanceAgentRuntime] planning engine unavailable; AP processing failed closed: %s",
                planner_exc,
            )
            response = {
                "status": "error",
                "reason": "planning_engine_unavailable",
                "detail": "AP planner unavailable; no workflow execution was performed.",
                "execution_mode": "agent_planning_engine",
                "agent_status": "failed",
            }

        if resolved_idempotency_key:
            response.setdefault("idempotency_key", resolved_idempotency_key)
        if resolved_correlation_id:
            response.setdefault("correlation_id", resolved_correlation_id)
        return response

    def ap_auto_approve_threshold(self) -> float:
        settings = self._organization_settings()
        threshold = self._safe_float(settings.get("auto_approve_threshold"), 0.95)
        return max(0.0, min(threshold, 1.0))

    def _build_finance_lead_summary_payload(
        self,
        ap_item: Dict[str, Any],
        *,
        audit_events: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        state = str(ap_item.get("state") or "received").strip().lower()
        next_action = str(ap_item.get("next_action") or "").strip().replace("_", " ")
        vendor = str(ap_item.get("vendor_name") or ap_item.get("vendor") or "Unknown vendor").strip()
        invoice_number = str(ap_item.get("invoice_number") or "N/A").strip()
        amount = ap_item.get("amount")
        currency = str(ap_item.get("currency") or "USD").strip().upper()
        due_date = str(ap_item.get("due_date") or "").strip()
        exception_code = str(ap_item.get("exception_code") or "").strip()
        exception_severity = str(ap_item.get("exception_severity") or "").strip()
        requires_field_review = bool(ap_item.get("requires_field_review"))
        confidence_blockers = (
            ap_item.get("confidence_blockers")
            if isinstance(ap_item.get("confidence_blockers"), list)
            else []
        )
        metadata = self._parse_json_dict(ap_item.get("metadata"))
        context_summary = str(metadata.get("context_summary") or "").strip()

        amount_text = (
            f"{currency} {float(amount):,.2f}"
            if isinstance(amount, (int, float))
            else f"{currency} amount unavailable"
        )
        lines: List[str] = [
            f"{vendor} · Invoice {invoice_number} · {amount_text}",
            f"Current state: {state.replace('_', ' ')}"
            + (f" · Next action: {next_action}" if next_action else ""),
        ]

        if exception_code:
            exception_line = f"Exception: {exception_code.replace('_', ' ')}"
            if exception_severity:
                exception_line += f" ({exception_severity})"
            lines.append(exception_line)
        if due_date:
            lines.append(f"Due date: {due_date}")
        if requires_field_review:
            fields: List[str] = []
            for entry in confidence_blockers[:4]:
                if isinstance(entry, str):
                    fields.append(entry)
                elif isinstance(entry, dict):
                    fields.append(str(entry.get("field") or entry.get("code") or "").strip())
            fields = [field for field in fields if field]
            lines.append(
                f"Field review blockers: {', '.join(fields)}"
                if fields
                else "Field review blockers require review before posting."
            )
        if bool(ap_item.get("budget_requires_decision")):
            budget_status = str(ap_item.get("budget_status") or "review").replace("_", " ")
            lines.append(f"Budget decision required ({budget_status}).")
        if context_summary:
            lines.append(f"Context: {context_summary[:180]}")

        recent: List[str] = []
        for event in (audit_events or [])[:4]:
            event_type = str(event.get("event_type") or event.get("eventType") or "").strip()
            if event_type:
                recent.append(event_type.replace("_", " "))
        if recent:
            lines.append(f"Recent activity: {' -> '.join(recent)}")

        deduped: List[str] = []
        seen: set[str] = set()
        for line in lines:
            text = str(line or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(text)

        return {
            "title": "Finance lead exception summary",
            "lines": deduped[:8],
            "state": state,
            "next_action": str(ap_item.get("next_action") or ""),
        }

    async def escalate_invoice_review(
        self,
        *,
        email_id: str,
        vendor: Optional[str] = None,
        amount: Optional[float] = None,
        currency: str = "USD",
        confidence: Optional[float] = None,
        mismatches: Optional[List[Dict[str, Any]]] = None,
        message: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> Dict[str, Any]:
        from clearledgr.workflows.gmail_activities import send_slack_notification_activity

        gmail_ref = str(email_id or "").strip()
        if not gmail_ref:
            raise ValueError("missing_email_id")

        try:
            ap_item = self._resolve_ap_item(gmail_ref)
        except Exception:
            ap_item = {}
        ap_item_id = str(ap_item.get("id") or gmail_ref).strip() or gmail_ref
        correlation_id = self._correlation_id_for_item(ap_item)

        mismatch_rows = mismatches if isinstance(mismatches, list) else []
        mismatch_text = "\n".join(
            [f"• {entry.get('message', str(entry))}" for entry in mismatch_rows[:5]]
        )
        amount_text = (
            f"{currency} {float(amount):,.2f}"
            if isinstance(amount, (int, float))
            else "Unknown"
        )
        escalation_message = str(message or "").strip() or (
            f"*Invoice Review Required*\n\n"
            f"*Vendor:* {vendor or 'Unknown'}\n"
            f"*Amount:* {amount_text}\n"
            f"*Confidence:* {confidence or 0}%\n\n"
            f"*Issues:*\n{mismatch_text or '• Manual review requested'}"
        )

        delivery = await send_slack_notification_activity(
            {
                "type": "escalation",
                "channel": str(channel or "#finance-escalations").strip() or "#finance-escalations",
                "email_id": gmail_ref,
                "ap_item_id": ap_item_id,
                "classification": {"type": "INVOICE"},
                "extraction": {
                    "vendor": vendor,
                    "amount": amount,
                    "currency": currency,
                },
                "confidence_result": {
                    "confidence_pct": confidence,
                    "mismatches": mismatch_rows,
                    "requires_review": True,
                },
                "organization_id": self.organization_id,
            }
        )

        audit_row = self._append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="invoice_escalated",
            reason="runtime_escalate_invoice_review",
            metadata={
                "email_id": gmail_ref,
                "vendor": vendor,
                "amount": amount,
                "currency": currency,
                "confidence": confidence,
                "mismatches": mismatch_rows,
                "channel": channel,
                "message": escalation_message[:500],
                "delivery": delivery,
            },
            correlation_id=correlation_id,
            skill_id="ap_v1",
        )

        return {
            "email_id": gmail_ref,
            "ap_item_id": ap_item_id,
            "status": "escalated",
            "channel": str(channel or "#finance-escalations").strip() or "#finance-escalations",
            "message": escalation_message,
            "delivery": delivery,
            "audit_event_id": (audit_row or {}).get("id"),
        }

    async def share_finance_summary(
        self,
        *,
        reference_id: str,
        target: str = "email_draft",
        preview_only: bool = False,
        recipient_email: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        from clearledgr.services.invoice_workflow import get_invoice_workflow
        from clearledgr.services.teams_notifications import (
            build_finance_summary_reply_activity,
            send_finance_summary_reply,
        )

        ap_item = self._resolve_ap_item(reference_id)
        ap_item_id = str(ap_item.get("id") or reference_id).strip() or str(reference_id)
        gmail_ref = str(ap_item.get("thread_id") or reference_id).strip() or str(reference_id)
        correlation_id = self._correlation_id_for_item(ap_item)
        resolved_target = str(target or "email_draft").strip().lower()
        if resolved_target not in {"email_draft", "slack_thread", "teams_reply"}:
            raise ValueError("unsupported_share_target")

        audit_events = []
        if hasattr(self.db, "list_ap_audit_events"):
            try:
                rows = self.db.list_ap_audit_events(ap_item_id)
                audit_events = rows if isinstance(rows, list) else []
            except Exception:
                audit_events = []
        summary = self._build_finance_lead_summary_payload(ap_item, audit_events=audit_events)

        resolved_recipient = (
            str(recipient_email or "").strip()
            or os.getenv("CLEARLEDGR_FINANCE_LEAD_EMAIL", "").strip()
            or os.getenv("FINANCE_LEAD_EMAIL", "").strip()
            or ""
        )
        operator_note = str(note or "").strip()
        vendor = str(ap_item.get("vendor_name") or ap_item.get("vendor") or "Unknown vendor").strip()
        invoice_number = str(ap_item.get("invoice_number") or "N/A").strip()
        subject = f"[Clearledgr] Exception summary: {vendor} · Invoice {invoice_number}"
        body_lines = [
            "Hi,",
            "",
            "Clearledgr prepared the following AP exception summary for review:",
            "",
            *[f"- {line}" for line in (summary.get("lines") or [])],
        ]
        if operator_note:
            body_lines.extend(["", "Operator note:", operator_note])
        body_lines.extend(["", "Sent from Clearledgr Gmail Agent Actions."])
        draft = {
            "to": resolved_recipient,
            "subject": subject,
            "body": "\n".join(body_lines),
        }

        if preview_only:
            preview_payload: Dict[str, Any]
            if resolved_target == "email_draft":
                preview_payload = {
                    "kind": "email_draft",
                    "draft": draft,
                    "recipient_email": resolved_recipient,
                }
            elif resolved_target == "slack_thread":
                slack_thread = (
                    self.db.get_slack_thread(gmail_ref)
                    if hasattr(self.db, "get_slack_thread")
                    else None
                )
                if not slack_thread:
                    raise ValueError("slack_thread_not_found")
                text_lines = [f"*{summary.get('title') or 'Finance exception summary'}*"]
                text_lines.extend([f"• {line}" for line in (summary.get("lines") or [])[:8]])
                if operator_note:
                    text_lines.extend(["", f"_Operator note:_ {operator_note}"])
                preview_payload = {
                    "kind": "slack_thread",
                    "channel_id": str(slack_thread.get("channel_id") or ""),
                    "thread_ts": str(slack_thread.get("thread_ts") or slack_thread.get("thread_id") or ""),
                    "text": "\n".join(text_lines),
                }
            else:
                metadata = self._parse_json_dict(ap_item.get("metadata"))
                teams_meta = metadata.get("teams") if isinstance(metadata.get("teams"), dict) else {}
                channel_id = str((teams_meta or {}).get("channel") or "").strip()
                reply_to_id = str((teams_meta or {}).get("message_id") or "").strip()
                if not channel_id:
                    raise ValueError("teams_channel_not_found")
                item_payload = {
                    "id": ap_item_id,
                    "vendor": vendor,
                    "amount": ap_item.get("amount") or 0,
                    "currency": ap_item.get("currency") or "USD",
                    "invoice_number": invoice_number,
                }
                preview_payload = {
                    "kind": "teams_reply",
                    "channel_id": channel_id,
                    "reply_to_id": reply_to_id or None,
                    "activity": build_finance_summary_reply_activity(
                        item_payload,
                        list(summary.get("lines") or []),
                        summary_title=str(summary.get("title") or "Finance exception summary"),
                        reply_to_id=reply_to_id or None,
                    ),
                }

            response = {
                "status": "preview",
                "target": resolved_target,
                "email_id": gmail_ref,
                "ap_item_id": ap_item_id,
                "summary": summary,
                "preview": preview_payload,
            }
            audit_row = self._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="finance_summary_share_previewed",
                reason=f"finance_summary_preview_{resolved_target}",
                metadata={
                    "target": resolved_target,
                    "summary_title": summary.get("title"),
                    "summary_lines": summary.get("lines"),
                    "preview_kind": preview_payload.get("kind"),
                    "recipient_email": resolved_recipient if resolved_target == "email_draft" else None,
                    "slack_channel_id": preview_payload.get("channel_id") if resolved_target == "slack_thread" else None,
                    "teams_channel_id": preview_payload.get("channel_id") if resolved_target == "teams_reply" else None,
                    "response": response,
                },
                correlation_id=correlation_id,
                skill_id="ap_v1",
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        if resolved_target == "email_draft":
            response = {
                "status": "prepared",
                "target": resolved_target,
                "email_id": gmail_ref,
                "ap_item_id": ap_item_id,
                "summary": summary,
                "draft": draft,
            }
            audit_row = self._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="finance_summary_share_prepared",
                reason="finance_summary_email_draft",
                metadata={
                    "target": resolved_target,
                    "recipient_email": resolved_recipient,
                    "summary_title": summary.get("title"),
                    "summary_lines": summary.get("lines"),
                    "response": response,
                },
                correlation_id=correlation_id,
                skill_id="ap_v1",
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        workflow = get_invoice_workflow(self.organization_id)
        delivery: Dict[str, Any]
        delivered = False
        if resolved_target == "slack_thread":
            slack_thread = (
                self.db.get_slack_thread(gmail_ref)
                if hasattr(self.db, "get_slack_thread")
                else None
            )
            if not slack_thread:
                raise ValueError("slack_thread_not_found")
            if not getattr(workflow, "slack_client", None):
                raise ValueError("slack_client_unavailable")
            text_lines = [f"*{summary.get('title') or 'Finance exception summary'}*"]
            text_lines.extend([f"• {line}" for line in (summary.get("lines") or [])[:8]])
            if operator_note:
                text_lines.extend(["", f"_Operator note:_ {operator_note}"])
            try:
                sent = await workflow.slack_client.send_message(
                    channel=str(slack_thread.get("channel_id") or ""),
                    thread_ts=str(slack_thread.get("thread_ts") or slack_thread.get("thread_id") or ""),
                    text="\n".join(text_lines),
                )
                delivery = {
                    "channel_id": sent.channel,
                    "thread_ts": sent.thread_ts or sent.ts,
                    "message_ts": sent.ts,
                    "status": "sent",
                }
                delivered = True
            except Exception as exc:
                delivery = {"status": "error", "reason": str(exc)}
        else:
            metadata = self._parse_json_dict(ap_item.get("metadata"))
            teams_meta = metadata.get("teams") if isinstance(metadata.get("teams"), dict) else {}
            channel_id = str((teams_meta or {}).get("channel") or "").strip()
            reply_to_id = str((teams_meta or {}).get("message_id") or "").strip()
            if not channel_id:
                raise ValueError("teams_channel_not_found")
            item_payload = {
                "id": ap_item_id,
                "vendor": vendor,
                "amount": ap_item.get("amount") or 0,
                "currency": ap_item.get("currency") or "USD",
                "invoice_number": invoice_number,
            }
            ok = await send_finance_summary_reply(
                item_payload,
                channel_id,
                list(summary.get("lines") or []),
                summary_title=str(summary.get("title") or "Finance exception summary"),
                reply_to_id=reply_to_id or None,
            )
            delivery = {
                "channel_id": channel_id,
                "reply_to_id": reply_to_id or None,
                "status": "sent" if ok else "error",
            }
            delivered = bool(ok)

        response = {
            "status": "shared" if delivered else "error",
            "target": resolved_target,
            "email_id": gmail_ref,
            "ap_item_id": ap_item_id,
            "summary": summary,
            "delivery": delivery,
        }
        audit_row = self._append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="finance_summary_shared" if delivered else "finance_summary_share_failed",
            reason=f"finance_summary_{resolved_target}",
            metadata={
                "target": resolved_target,
                "summary_title": summary.get("title"),
                "summary_lines": summary.get("lines"),
                "delivery": delivery,
                "response": response,
            },
            correlation_id=correlation_id,
            skill_id="ap_v1",
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response

    def record_field_correction(
        self,
        *,
        ap_item_id: str,
        field: str,
        original_value: Any = None,
        corrected_value: Any = None,
        feedback: Optional[str] = None,
        actor_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from clearledgr.services.audit_trail import get_audit_trail
        from clearledgr.services.correction_learning import CorrectionLearningService

        ap_item = self._resolve_ap_item(ap_item_id)
        resolved_ap_item_id = str(ap_item.get("id") or ap_item_id).strip() or str(ap_item_id)
        correlation_id = self._correlation_id_for_item(ap_item)
        resolved_actor = str(actor_id or self.actor_email or self.actor_id or "operator").strip() or "operator"

        learning_svc = CorrectionLearningService(self.organization_id)
        try:
            learning_result = learning_svc.record_correction(
                correction_type=field,
                original_value=original_value,
                corrected_value=corrected_value,
                context={
                    "ap_item_id": resolved_ap_item_id,
                    "field": field,
                    "vendor": ap_item.get("vendor_name"),
                },
                user_id=resolved_actor,
                invoice_id=ap_item.get("thread_id"),
                feedback=feedback,
            )
        except Exception as exc:
            logger.warning("correction_learning.record_correction failed: %s", exc)
            learning_result = {}

        audit_meta = {
            "field": field,
            "original_value": str(original_value) if original_value is not None else None,
            "corrected_value": str(corrected_value) if corrected_value is not None else None,
            "actor_id": resolved_actor,
            "feedback": feedback,
            "learning_result": learning_result,
        }
        try:
            audit_svc = get_audit_trail(self.organization_id)
            audit_svc.record_event(
                event_type="field_correction",
                invoice_id=ap_item.get("thread_id") or resolved_ap_item_id,
                actor_type="operator",
                actor_id=resolved_actor,
                metadata=audit_meta,
            )
        except Exception as exc:
            logger.warning("audit field_correction event failed: %s", exc)

        response = {
            "status": "recorded",
            "ap_item_id": resolved_ap_item_id,
            "field": field,
            "learning_result": learning_result,
        }
        audit_row = self._append_runtime_audit(
            ap_item_id=resolved_ap_item_id,
            event_type="field_correction",
            reason="runtime_record_field_correction",
            metadata={
                **audit_meta,
                "response": response,
            },
            correlation_id=correlation_id,
            skill_id="ap_v1",
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response

    async def resume_pending_agent_tasks(self) -> int:
        """Resume interrupted planning engine tasks and count pending retry jobs."""
        count = 0
        try:
            from clearledgr.core.agent_runtime import get_planning_engine

            count += await get_planning_engine().resume_pending_tasks()
        except Exception:
            pass
        try:
            jobs = self.db.list_agent_retry_jobs(self.organization_id, status="pending", limit=1000)
            count += len(jobs or [])
        except Exception:
            pass
        return count


_PLATFORM_RUNTIME_CACHE: Dict[str, FinanceAgentRuntime] = {}


def get_platform_finance_runtime(organization_id: str = "default") -> FinanceAgentRuntime:
    """Process-level singleton runtime used by startup/background AP flows."""
    org_id = str(organization_id or "default").strip() or "default"
    existing = _PLATFORM_RUNTIME_CACHE.get(org_id)
    if existing is not None:
        return existing

    runtime = FinanceAgentRuntime(
        organization_id=org_id,
        actor_id="system",
        actor_email="system@clearledgr.local",
        db=get_db(),
    )
    _PLATFORM_RUNTIME_CACHE[org_id] = runtime
    return runtime
