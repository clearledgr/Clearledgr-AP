"""Single control loop owner for bounded finance-agent execution."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from clearledgr.core.finance_contracts import ActionExecution, SkillRequest
from clearledgr.services.agent_memory import get_agent_memory_service
from clearledgr.services.finance_agent_governance import (
    attempt_self_recovery,
    build_deliberation,
)
from clearledgr.services.finance_learning import get_finance_learning_service

logger = logging.getLogger(__name__)


class FinanceAgentLoopService:
    """Owns observe -> recall -> deliberate -> act -> verify -> learn."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.memory = get_agent_memory_service(runtime.organization_id, db=getattr(runtime, "db", None))
        self.learning = get_finance_learning_service(runtime.organization_id, db=getattr(runtime, "db", None))

    def _resolve_ap_item(self, request: SkillRequest) -> Dict[str, Any]:
        entity_id = str(request.entity_id or "").strip()
        if not entity_id:
            return {}
        try:
            item = self.runtime._resolve_ap_item(entity_id)
            return item if isinstance(item, dict) else {}
        except Exception:
            return {}

    def observe(self, request: SkillRequest, action: ActionExecution) -> Dict[str, Any]:
        profile = self.memory.ensure_profile(skill_id=request.skill_id)
        ap_item = self._resolve_ap_item(request)
        ap_item_id = str(ap_item.get("id") or request.entity_id or "").strip() or None
        metadata = self.runtime.parse_json_dict(ap_item.get("metadata")) if ap_item else {}
        belief = {}
        recall = []
        if ap_item_id:
            belief = self.memory.build_belief_state(ap_item_id=ap_item_id, skill_id=request.skill_id, ap_item=ap_item)
            recall = self.memory.recall_similar_cases(
                {
                    "vendor_name": ap_item.get("vendor_name") or ap_item.get("vendor"),
                    "document_type": metadata.get("document_type") or ap_item.get("document_type"),
                    "current_state": ap_item.get("state"),
                    "status": metadata.get("processing_status") or ap_item.get("state"),
                    "next_action": action.action,
                },
                skill_id=request.skill_id,
                limit=5,
            )
            self.memory.observe_event(
                skill_id=request.skill_id,
                ap_item_id=ap_item_id,
                thread_id=str(ap_item.get("thread_id") or "").strip() or None,
                event_type="loop_started",
                payload={
                    "intent": request.task_type,
                    "action": action.to_dict(),
                    "recall_count": len(recall),
                    "profile": {
                        "doctrine_version": profile.get("doctrine_version"),
                        "risk_posture": profile.get("risk_posture"),
                        "autonomy_level": profile.get("autonomy_level"),
                    },
                },
                channel="finance_agent_loop",
                actor_id=self.runtime.actor_email or self.runtime.actor_id,
                correlation_id=request.correlation_id,
                source="finance_agent_loop",
                summary=f"loop_started:{request.task_type}",
            )
        preview = self.runtime.preview_skill_request(request)
        deliberation = build_deliberation(
            runtime=self.runtime,
            request=request,
            action=action,
            ap_item=ap_item,
            belief=belief,
            recall=recall,
            profile=profile,
        )
        return {
            "ap_item": ap_item,
            "ap_item_id": ap_item_id,
            "belief": belief,
            "recall": recall,
            "profile": profile,
            "preview": preview,
            "deliberation": deliberation,
        }

    async def run_skill_request(
        self,
        request: SkillRequest,
        action: ActionExecution,
        executor: Callable[[], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        observed = self.observe(request, action)
        deliberation = observed.get("deliberation") if isinstance(observed.get("deliberation"), dict) else {}
        if deliberation and not deliberation.get("should_execute", True):
            blocked_response = {
                "status": "blocked",
                "reason": "doctrine_enforced_block",
                "detail": deliberation.get("stop_reason") or "Execution blocked by doctrine enforcement.",
                "intent": request.task_type,
                "deliberation": deliberation,
                "agent_loop": {
                    "owner": "finance_agent_loop",
                    "observed": bool(observed.get("ap_item_id") or observed.get("ap_item")),
                    "recall_count": len(observed.get("recall") or []),
                    "belief_available": bool(observed.get("belief")),
                    "preview_status": str((observed.get("preview") or {}).get("status") or "").strip() or None,
                    "profile": {
                        "doctrine_version": (observed.get("profile") or {}).get("doctrine_version"),
                        "risk_posture": (observed.get("profile") or {}).get("risk_posture"),
                        "autonomy_level": (observed.get("profile") or {}).get("autonomy_level"),
                    },
                },
            }
            ap_item_id = observed.get("ap_item_id")
            if ap_item_id:
                self.memory.record_outcome(
                    skill_id=request.skill_id,
                    ap_item=observed.get("ap_item"),
                    ap_item_id=ap_item_id,
                    event_type="loop_blocked_by_doctrine",
                    reason=str(blocked_response.get("detail") or "doctrine_enforced_block"),
                    response=blocked_response,
                    actor_id=self.runtime.actor_email or self.runtime.actor_id,
                    source="finance_agent_loop",
                    correlation_id=request.correlation_id,
                )
                self.learning.record_action_outcome(
                    event_type="loop_blocked_by_doctrine",
                    ap_item=observed.get("ap_item"),
                    response=blocked_response,
                    actor_id=self.runtime.actor_email or self.runtime.actor_id,
                    metadata={
                        "preview": observed.get("preview"),
                        "matched_shadow": False,
                    },
                )
            return blocked_response
        try:
            response = await executor()
        except Exception as exc:
            ap_item_id = observed.get("ap_item_id")
            if ap_item_id:
                self.memory.record_outcome(
                    skill_id=request.skill_id,
                    ap_item=observed.get("ap_item"),
                    ap_item_id=ap_item_id,
                    event_type="loop_failed",
                    reason=str(exc),
                    response={
                        "status": "error",
                        "reason": str(exc),
                        "intent": request.task_type,
                    },
                    actor_id=self.runtime.actor_email or self.runtime.actor_id,
                    source="finance_agent_loop",
                    correlation_id=request.correlation_id,
                )
                self.learning.record_action_outcome(
                    event_type="loop_failed",
                    ap_item=observed.get("ap_item"),
                    response={
                        "status": "error",
                        "reason": str(exc),
                        "intent": request.task_type,
                    },
                    actor_id=self.runtime.actor_email or self.runtime.actor_id,
                    metadata={"preview": observed.get("preview")},
                )
            raise

        ap_item_id = str(
            response.get("ap_item_id")
            or observed.get("ap_item_id")
            or request.entity_id
            or ""
        ).strip() or None
        verified_item = observed.get("ap_item") or {}
        if ap_item_id and hasattr(self.runtime.db, "get_ap_item"):
            try:
                verified_item = self.runtime.db.get_ap_item(ap_item_id) or verified_item
            except Exception:
                verified_item = observed.get("ap_item") or {}

        self_recovery = await attempt_self_recovery(
            self.runtime,
            request=request,
            response=response,
            ap_item=verified_item,
        )
        if self_recovery.get("attempted"):
            response["self_recovery"] = self_recovery
            response["recovery_attempted"] = True
            response["recovery_succeeded"] = bool(self_recovery.get("recovered"))
            if self_recovery.get("recovered") and isinstance(self_recovery.get("outcome"), dict):
                recovered_outcome = dict(self_recovery.get("outcome") or {})
                response.setdefault("original_response", dict(response))
                response.update(recovered_outcome)
                response["self_recovery"] = self_recovery

        if ap_item_id:
            self.memory.record_outcome(
                skill_id=request.skill_id,
                ap_item=verified_item,
                ap_item_id=ap_item_id,
                event_type="loop_completed",
                reason=str(response.get("status") or request.task_type).strip() or request.task_type,
                response=response,
                actor_id=self.runtime.actor_email or self.runtime.actor_id,
                source="finance_agent_loop",
                correlation_id=request.correlation_id,
            )
            self.learning.record_action_outcome(
                event_type=f"loop_{request.task_type}",
                ap_item=verified_item,
                response=response,
                actor_id=self.runtime.actor_email or self.runtime.actor_id,
                metadata={
                    "preview": observed.get("preview"),
                    "recall_count": len(observed.get("recall") or []),
                    "matched_shadow": bool(
                        (response.get("shadow_decision") or {}).get("proposed_action")
                        and (response.get("shadow_decision") or {}).get("proposed_action")
                        == str(response.get("status") or "").strip()
                    ),
                    "verification_succeeded": bool(
                        response.get("post_verified") or response.get("verification_succeeded")
                    ),
                    "recovery_attempted": bool(response.get("recovery_attempted")),
                    "recovery_succeeded": bool(response.get("recovery_succeeded")),
                    "confidence_delta": (
                        1.0 if bool(response.get("recovery_succeeded")) else 0.0
                    ),
                },
            )

        response.setdefault(
            "agent_loop",
            {
                "observed": bool(ap_item_id or observed.get("ap_item")),
                "recall_count": len(observed.get("recall") or []),
                "preview_status": str((observed.get("preview") or {}).get("status") or "").strip() or None,
                "belief_available": bool(observed.get("belief")),
                "profile": {
                    "doctrine_version": (observed.get("profile") or {}).get("doctrine_version"),
                    "risk_posture": (observed.get("profile") or {}).get("risk_posture"),
                    "autonomy_level": (observed.get("profile") or {}).get("autonomy_level"),
                },
                "deliberation_confidence": deliberation.get("confidence"),
                "recommended_action": deliberation.get("recommended_action"),
                "owner": "finance_agent_loop",
            },
        )
        response.setdefault("deliberation", deliberation)
        return response
