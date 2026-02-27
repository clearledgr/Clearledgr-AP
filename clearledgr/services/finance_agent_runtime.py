"""Finance agent runtime contracts (preview/execute) with skill registry dispatch.

This module defines a stable runtime seam so operator surfaces (Gmail, Slack,
future chat surfaces) call a consistent intent contract. Execution logic is
packaged as finance skills and dispatched by intent.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.database import get_db
from clearledgr.services.finance_skills import APFinanceSkill, FinanceSkill, WorkflowHealthSkill


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
        self.register_skill(WorkflowHealthSkill())

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
            payload.get("email_id")
            or payload.get("ap_item_id")
            or payload.get("item_id")
            or ""
        ).strip()

    def _ensure_supported(self, intent: str) -> str:
        normalized = str(intent or "").strip().lower()
        if normalized not in self._intent_skill_map:
            raise IntentNotSupportedError(f"unsupported_intent:{normalized or 'missing'}")
        return normalized

    def _skill_for_intent(self, intent: str) -> FinanceSkill:
        normalized = self._ensure_supported(intent)
        return self._intent_skill_map[normalized]

    def _resolve_ap_item(self, reference: str) -> Dict[str, Any]:
        ref = str(reference or "").strip()
        if not ref:
            raise ValueError("missing_item_reference")

        item: Optional[Dict[str, Any]] = None
        if hasattr(self.db, "get_ap_item"):
            item = self.db.get_ap_item(ref)
            if item and str(item.get("organization_id") or self.organization_id) != self.organization_id:
                item = None
        if not item and hasattr(self.db, "get_ap_item_by_thread"):
            item = self.db.get_ap_item_by_thread(self.organization_id, ref)
        if not item and hasattr(self.db, "get_ap_item_by_message_id"):
            item = self.db.get_ap_item_by_message_id(self.organization_id, ref)

        if not item:
            raise LookupError("ap_item_not_found")
        if str(item.get("organization_id") or self.organization_id) != self.organization_id:
            raise PermissionError("organization_mismatch")
        return item

    def _correlation_id_for_item(self, item: Dict[str, Any]) -> Optional[str]:
        metadata = self._parse_json_dict(item.get("metadata"))
        correlation_id = str(item.get("correlation_id") or metadata.get("correlation_id") or "").strip()
        return correlation_id or None

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
        if not key or not hasattr(self.db, "get_ap_audit_event_by_key"):
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
    ) -> Optional[Dict[str, Any]]:
        if not hasattr(self.db, "append_ap_audit_event"):
            return None
        return self.db.append_ap_audit_event(
            {
                "ap_item_id": ap_item_id,
                "event_type": event_type,
                "actor_type": "user",
                "actor_id": self.actor_email,
                "reason": reason,
                "metadata": metadata or {},
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

    def preview_intent(self, intent: str, input_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        normalized_intent = self._ensure_supported(intent)
        payload = input_payload if isinstance(input_payload, dict) else {}
        skill = self._skill_for_intent(normalized_intent)
        result = skill.preview(self, normalized_intent, payload)
        if isinstance(result, dict):
            result.setdefault("intent", normalized_intent)
            result.setdefault("skill_id", skill.skill_id)
        return result

    async def execute_intent(
        self,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_intent = self._ensure_supported(intent)
        payload = input_payload if isinstance(input_payload, dict) else {}
        replay = self._load_idempotent_response(idempotency_key)
        if replay:
            replay.setdefault("intent", normalized_intent)
            return replay

        skill = self._skill_for_intent(normalized_intent)
        result = await skill.execute(
            self,
            normalized_intent,
            payload,
            idempotency_key=idempotency_key,
        )
        if isinstance(result, dict):
            result.setdefault("intent", normalized_intent)
            result.setdefault("skill_id", skill.skill_id)
        return result

    async def execute_ap_invoice_processing(
        self,
        invoice_payload: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        *,
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run AP invoice processing via the internal planning engine.

        This keeps a single public runtime surface (FinanceAgentRuntime) while
        reusing the planner engine as an internal execution primitive.
        """
        from clearledgr.core.agent_runtime import get_planning_engine
        from clearledgr.core.skills.ap_skill import APSkill
        from clearledgr.core.skills.base import AgentTask

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

        planner = get_planning_engine()
        planner.register_skill(APSkill(organization_id=self.organization_id))

        task = AgentTask(
            task_type="ap_invoice_processing",
            organization_id=self.organization_id,
            payload={
                "invoice": invoice,
                "attachments": attachments or [],
            },
            idempotency_key=resolved_idempotency_key,
            correlation_id=resolved_correlation_id,
        )
        result = await planner.run_task(task)
        response = dict(result.outcome or {})
        response.setdefault("status", result.status)
        response.setdefault("task_run_id", result.task_run_id)
        return response

    async def resume_pending_agent_tasks(self) -> int:
        """Resume interrupted planner tasks for this runtime's organization."""
        from clearledgr.core.agent_runtime import get_planning_engine
        from clearledgr.core.skills.ap_skill import APSkill

        planner = get_planning_engine()
        planner.register_skill(APSkill(organization_id=self.organization_id))
        return await planner.resume_pending_tasks()


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
