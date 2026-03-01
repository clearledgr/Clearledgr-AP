"""
Agent Orchestrator

Central coordinator that wires all intelligence services into the invoice flow.
Sits between event sources (Gmail webhooks, Slack actions, Gmail extension) and
InvoiceWorkflowService, adding reasoning, reflection, learning, and proactive
behavior.

Architecture:
    Gmail webhook → AgentOrchestrator.process_invoice() → FinanceAgentRuntime
    Slack approve → AgentOrchestrator.on_approval() → InvoiceWorkflowService
    Extension approve → AgentOrchestrator.on_approval() → InvoiceWorkflowService

Runtime contract:
    AP execution uses a single canonical agentic runtime path. Planner/runtime
    failures fail closed and return a typed failure result; they do not branch
    into legacy execution fallback.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """
    Coordinates intelligence services around the invoice workflow.

    Does NOT replace InvoiceWorkflowService — wraps it. The workflow handles
    execution (DB writes, Slack messages, ERP posting). The orchestrator
    handles intelligence (reasoning, reflection, learning, follow-up).
    """

    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self._workflow = None
        self._reasoning = None
        self._reflection = None
        self._priority = None
        self._correction_learning = None
        self._conversational = None
        self._followup = None
        self._insights = None
        self._retry_worker_task: Optional[asyncio.Task] = None
        self._retry_worker_stopping = False
        self._runtime_contract_warning_logged = False

    # ------------------------------------------------------------------
    # Runtime mode / durability truth-in-claims
    # ------------------------------------------------------------------

    @staticmethod
    def _env_flag(name: str, default: bool) -> bool:
        raw = str(os.getenv(name, "true" if default else "false")).strip().lower()
        return raw not in {"0", "false", "no", "off"}

    @staticmethod
    def _is_production_env() -> bool:
        return str(os.getenv("ENV", "dev")).strip().lower() in {"prod", "production"}

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(str(os.getenv(name, str(default))).strip())
        except (TypeError, ValueError):
            return int(default)

    def _retry_backoff_schedule_seconds(self) -> List[int]:
        raw = str(os.getenv("AP_AGENT_RETRY_BACKOFF_SECONDS", "5,15,45")).strip()
        schedule: List[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                schedule.append(max(0, int(part)))
            except ValueError:
                continue
        return schedule or [5, 15, 45]

    def _retry_poll_interval_seconds(self) -> int:
        return max(1, self._env_int("AP_AGENT_RETRY_POLL_SECONDS", 5))

    def _post_process_backoff_schedule_seconds(self) -> List[int]:
        raw = str(os.getenv("AP_AGENT_POST_PROCESS_BACKOFF_SECONDS", "5,15,45")).strip()
        schedule: List[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                schedule.append(max(0, int(part)))
            except ValueError:
                continue
        return schedule or [5, 15, 45]

    def _post_process_max_attempts(self) -> int:
        return max(1, self._env_int("AP_AGENT_POST_PROCESS_MAX_ATTEMPTS", 3))

    def post_process_runtime_status(self) -> Dict[str, Any]:
        enabled = self._env_flag("AP_AGENT_POST_PROCESS_DURABLE_ENABLED", True)
        return {
            "enabled": bool(enabled),
            "durable": True,
            "mode": "durable_db_post_process_queue",
            "backoff_seconds": self._post_process_backoff_schedule_seconds(),
            "max_attempts": self._post_process_max_attempts(),
            "allow_non_durable": False,
            "worker_running": bool(self._retry_worker_task and not self._retry_worker_task.done()),
            "reason": None if enabled else "post_process_disabled_by_config",
        }

    def autonomous_retry_runtime_status(self) -> Dict[str, Any]:
        enabled_by_config = self._env_flag("AP_AGENT_AUTONOMOUS_RETRY_ENABLED", True)
        allow_non_durable_default = not self._is_production_env()
        allow_non_durable = self._env_flag(
            "AP_AGENT_NON_DURABLE_RETRY_ALLOWED",
            allow_non_durable_default,
        )
        durable = True
        enabled = bool(enabled_by_config)
        if not enabled_by_config:
            reason = "autonomous_retry_disabled_by_config"
        else:
            reason = None

        return {
            "enabled": enabled,
            "durable": durable,
            "mode": "durable_db_retry_queue",
            "post_process_mode": "enqueue_durable_post_process_job",
            "allow_non_durable": allow_non_durable,
            "backoff_seconds": self._retry_backoff_schedule_seconds(),
            "poll_interval_seconds": self._retry_poll_interval_seconds(),
            "worker_running": bool(self._retry_worker_task and not self._retry_worker_task.done()),
            "reason": reason,
        }

    def _planning_loop_requested(self) -> bool:
        # Agentic planner is the canonical default path.
        return self._env_flag("AGENT_PLANNING_LOOP", True)

    def _planning_loop_enabled(self) -> bool:
        # Single-runtime doctrine: agentic loop is always effective.
        _ = self._planning_loop_requested()
        return True

    def _legacy_fallback_requested(self) -> bool:
        return self._env_flag("AGENT_LEGACY_FALLBACK_ON_ERROR", False)

    def _runtime_execution_contract(self) -> Dict[str, Any]:
        requested_planning = self._planning_loop_requested()
        effective_planning = self._planning_loop_enabled()
        requested_fallback = self._legacy_fallback_requested()
        effective_fallback = self._legacy_fallback_on_planner_error()
        prod_mode = self._is_production_env()

        forced_agentic = bool(not requested_planning and effective_planning)
        forced_fallback_off = bool(requested_fallback and not effective_fallback)
        warnings: List[str] = []
        if forced_agentic:
            warnings.append(
                "planning_loop_forced_on_in_production"
                if prod_mode
                else "planning_loop_opt_out_ignored"
            )
        if forced_fallback_off:
            warnings.append(
                "legacy_fallback_forced_off_in_production"
                if prod_mode
                else "legacy_fallback_opt_in_ignored"
            )

        return {
            "mode": "agentic_runtime",
            "production_env": prod_mode,
            "production_contract_enforced": prod_mode,
            "planning_loop_requested": requested_planning,
            "planning_loop_enabled": effective_planning,
            "legacy_fallback_requested": requested_fallback,
            "legacy_fallback_on_error": effective_fallback,
            "warnings": warnings,
        }

    def _log_runtime_contract_warnings_once(self, contract: Dict[str, Any]) -> None:
        if self._runtime_contract_warning_logged:
            return
        warnings = contract.get("warnings") or []
        if not warnings:
            return
        logger.warning(
            "[AgentOrchestrator] runtime contract enforcement for org=%s: %s",
            self.organization_id,
            ",".join(str(w) for w in warnings),
        )
        self._runtime_contract_warning_logged = True

    def runtime_status(self) -> Dict[str, Any]:
        execution_contract = self._runtime_execution_contract()
        return {
            "autonomous_retry": self.autonomous_retry_runtime_status(),
            "post_process": self.post_process_runtime_status(),
            "legacy_fallback_on_planner_error": execution_contract["legacy_fallback_on_error"],
            "execution_contract": execution_contract,
        }

    def _legacy_fallback_on_planner_error(self) -> bool:
        # Single-runtime doctrine: legacy fallback is hard-disabled.
        return False

    # ------------------------------------------------------------------
    # Lazy service loaders
    # ------------------------------------------------------------------

    @property
    def workflow(self) -> InvoiceWorkflowService:
        if self._workflow is None:
            self._workflow = InvoiceWorkflowService(organization_id=self.organization_id)
        return self._workflow

    @property
    def reasoning(self):
        if self._reasoning is None:
            from clearledgr.services.agent_reasoning import AgentReasoningService
            self._reasoning = AgentReasoningService(organization_id=self.organization_id)
        return self._reasoning

    @property
    def reflection(self):
        if self._reflection is None:
            from clearledgr.services.agent_reflection import AgentReflection
            self._reflection = AgentReflection()
        return self._reflection

    @property
    def priority_service(self):
        if self._priority is None:
            from clearledgr.services.priority_detection import get_priority_detection
            self._priority = get_priority_detection(self.organization_id)
        return self._priority

    @property
    def correction_learning(self):
        if self._correction_learning is None:
            from clearledgr.services.correction_learning import CorrectionLearningService
            self._correction_learning = CorrectionLearningService(organization_id=self.organization_id)
        return self._correction_learning

    @property
    def conversational(self):
        if self._conversational is None:
            from clearledgr.services.conversational_agent import ConversationalAgent
            self._conversational = ConversationalAgent(organization_id=self.organization_id)
        return self._conversational

    @property
    def followup(self):
        if self._followup is None:
            from clearledgr.services.auto_followup import AutoFollowUpService
            self._followup = AutoFollowUpService()
        return self._followup

    @property
    def insights(self):
        if self._insights is None:
            from clearledgr.services.proactive_insights import get_proactive_insights
            self._insights = get_proactive_insights(self.organization_id)
        return self._insights

    # ------------------------------------------------------------------
    # Main entry point: process a new invoice
    # ------------------------------------------------------------------

    async def process_invoice(
        self,
        invoice: InvoiceData,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Process a new invoice through the agent pipeline.

        Agentic planning runs through FinanceAgentRuntime as the only runtime path.

        1. Reason (chain-of-thought LLM analysis)
        2. Reflect (self-validate and correct)
        3. Prioritize (6-factor scoring)
        4. Learn (apply correction learning suggestions)
        5. Route (set confidence to control workflow decision)
        6. Delegate to InvoiceWorkflowService
        7. Post-process (follow-up, insights, clarifying questions)
        """
        execution_contract = self._runtime_execution_contract()
        self._log_runtime_contract_warnings_once(execution_contract)

        try:
            from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

            runtime = FinanceAgentRuntime(
                organization_id=self.organization_id,
                actor_id=str(getattr(invoice, "user_id", None) or "system"),
                actor_email=str(getattr(invoice, "sender", None) or "system@clearledgr.local"),
            )
            runtime_result = await runtime.execute_ap_invoice_processing(
                invoice_payload=invoice.__dict__,
                attachments=attachments or [],
                idempotency_key=f"invoice:{invoice.gmail_id}",
                correlation_id=getattr(invoice, "correlation_id", None),
            )
            if not isinstance(runtime_result, dict):
                logger.warning(
                    "[AgentOrchestrator] runtime returned non-dict result type=%s",
                    type(runtime_result).__name__,
                )
                return {
                    "status": "failed",
                    "reason": "agent_runtime_invalid_response",
                    "error": "runtime_result_must_be_object",
                    "execution_path": "agentic_runtime",
                    "gmail_id": invoice.gmail_id,
                    "runtime_contract": execution_contract,
                }
            runtime_result.setdefault("execution_path", "agentic_runtime")
            runtime_result.setdefault("runtime_contract", execution_contract)
            return runtime_result
        except Exception as exc:
            logger.warning(
                "[AgentOrchestrator] planning loop failed; returning typed failure: %s",
                exc,
            )
            return {
                "status": "failed",
                "reason": "agent_runtime_failed",
                "error": str(exc),
                "execution_path": "agentic_runtime",
                "gmail_id": invoice.gmail_id,
                "runtime_contract": execution_contract,
            }

    async def _process_invoice_legacy(
        self,
        invoice: InvoiceData,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Legacy hardcoded 8-step orchestration pipeline (unchanged)."""
        original_confidence = invoice.confidence

        # --- Step 1: Agent Reasoning ---
        agent_decision = None
        try:
            agent_decision = self.reasoning.reason_about_invoice(
                text=invoice.invoice_text or f"{invoice.subject}\n{invoice.sender}",
                attachments=attachments,
                context={
                    "vendor": invoice.vendor_name,
                    "amount": invoice.amount,
                    "currency": invoice.currency,
                    "organization_id": self.organization_id,
                },
            )
            logger.info(
                f"Agent reasoning: decision={agent_decision.decision} "
                f"confidence={agent_decision.confidence:.2f} "
                f"vendor={invoice.vendor_name}"
            )
        except Exception as e:
            logger.warning(f"Agent reasoning failed (falling back to heuristic): {e}")

        # --- Step 2: Self-Reflection ---
        reflection_result = None
        extraction = agent_decision.extraction if agent_decision else {}
        if agent_decision and extraction:
            try:
                reflection_result = self.reflection.reflect_on_extraction(
                    extraction=extraction,
                    original_text=invoice.invoice_text or invoice.subject,
                )
                if reflection_result.corrections_made:
                    logger.info(
                        f"Self-reflection corrected {len(reflection_result.corrections_made)} field(s) "
                        f"(confidence adj: {reflection_result.confidence_adjustment:+.2f})"
                    )
            except Exception as e:
                logger.warning(f"Self-reflection failed: {e}")

        # --- Step 3: Priority Detection ---
        try:
            priority_assessment = self.priority_service.assess({
                "vendor": invoice.vendor_name,
                "amount": invoice.amount,
                "due_date": invoice.due_date,
                "currency": invoice.currency,
                "invoice_number": invoice.invoice_number,
            })
            invoice.priority = priority_assessment.to_dict()
        except Exception as e:
            logger.warning(f"Priority detection failed: {e}")

        # --- Step 4: Correction Learning Suggestions ---
        learned_gl = None
        try:
            suggestion = self.correction_learning.suggest(
                "gl_code",
                {"vendor": invoice.vendor_name, "amount": invoice.amount},
            )
            if suggestion and suggestion.get("confidence", 0) > 0.5:
                learned_gl = suggestion
                logger.info(
                    f"Learned GL suggestion: {suggestion.get('value')} "
                    f"(confidence={suggestion.get('confidence'):.2f}, "
                    f"from {suggestion.get('learned_from', 0)} corrections)"
                )
        except Exception as e:
            logger.debug(f"Correction learning suggestion failed: {e}")

        # --- Step 5: Populate InvoiceData ---
        if agent_decision:
            # Merge agent extraction into invoice (fill gaps, don't overwrite user data)
            merged = agent_decision.extraction
            if reflection_result and reflection_result.final_extraction:
                merged = reflection_result.final_extraction

            if not invoice.vendor_name or invoice.vendor_name == "Unknown":
                invoice.vendor_name = merged.get("vendor", invoice.vendor_name)
            if not invoice.amount and merged.get("total_amount"):
                invoice.amount = float(merged.get("total_amount", 0))
            if not invoice.invoice_number and merged.get("invoice_number"):
                invoice.invoice_number = merged.get("invoice_number")
            if not invoice.due_date and merged.get("due_date"):
                invoice.due_date = merged.get("due_date")

            # Set reasoning fields (InvoiceData already has these fields)
            invoice.reasoning_summary = agent_decision.summary
            invoice.reasoning_factors = [
                {"factor": f.factor, "score": f.score, "detail": f.detail}
                for f in agent_decision.factors
            ]
            invoice.reasoning_risks = agent_decision.risks

            # Merge learned GL into vendor intelligence
            if learned_gl:
                vi = invoice.vendor_intelligence or {}
                vi["suggested_gl"] = learned_gl.get("value")
                vi["gl_confidence"] = learned_gl.get("confidence")
                vi["gl_learned_from"] = learned_gl.get("learned_from", 0)
                invoice.vendor_intelligence = vi

        # --- Step 6: Route via confidence (with earned autonomy) ---
        if agent_decision:
            agent_confidence = agent_decision.confidence
            if reflection_result:
                agent_confidence += reflection_result.confidence_adjustment
                agent_confidence = max(0.0, min(1.0, agent_confidence))

            # Boost confidence if we have learned patterns
            if learned_gl and learned_gl.get("confidence", 0) > 0.7:
                agent_confidence = min(1.0, agent_confidence + 0.05)

            # Earned autonomy: adjust threshold per vendor track record
            vendor_boost = self._earned_autonomy_boost(invoice.vendor_name)
            if vendor_boost > 0:
                agent_confidence = min(1.0, agent_confidence + vendor_boost)
                logger.info(
                    f"Earned autonomy boost +{vendor_boost:.2f} for {invoice.vendor_name}"
                )

            invoice.confidence = agent_confidence

            decision = agent_decision.decision
            if decision == "auto_approve" and agent_confidence < 0.95:
                invoice.confidence = 0.96  # Ensure workflow auto-approves
            elif decision == "reject":
                # Flag for rejection — workflow will create the record,
                # then we reject in post-processing
                invoice.confidence = 0.0
        else:
            # Fallback: keep original heuristic confidence
            invoice.confidence = original_confidence

        # --- Step 7: Delegate to workflow ---
        result = await self.workflow.process_new_invoice(invoice)

        # --- Step 8: Post-process (durable queue only) ---
        queued_post_process = self._enqueue_post_process_job(invoice, agent_decision, result)
        if queued_post_process:
            self._ensure_retry_worker()
        else:
            post_process_status = self.post_process_runtime_status()
            reason = str(post_process_status.get("reason") or "post_process_enqueue_unavailable")
            self._record_post_process_gated_event(
                invoice,
                result,
                post_process_status,
                reason=reason,
            )

        return result

    # ------------------------------------------------------------------
    # Approval / Rejection handlers (feedback loop)
    # ------------------------------------------------------------------

    async def on_approval(
        self,
        gmail_id: str,
        approved_by: str,
        source_channel: str = "slack",
        allow_budget_override: bool = False,
        allow_confidence_override: bool = False,
        override_justification: Optional[str] = None,
        field_confidences: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Handle invoice approval. Wraps workflow.approve_invoice() and
        records learning feedback.
        """
        result = await self.workflow.approve_invoice(
            gmail_id=gmail_id,
            approved_by=approved_by,
            source_channel=source_channel,
            allow_budget_override=allow_budget_override,
            allow_confidence_override=allow_confidence_override,
            override_justification=override_justification,
            field_confidences=field_confidences,
            **kwargs,
        )

        # Record feedback for learning
        try:
            from clearledgr.core.database import get_db
            db = get_db()
            ap_item = db.get_ap_item_by_thread(self.organization_id, gmail_id)
            if not ap_item:
                ap_item = db.get_ap_item_by_message_id(self.organization_id, gmail_id)

            if ap_item:
                vendor = ap_item.get("vendor_name", "")
                amount = ap_item.get("amount", 0)
                self.correction_learning.record_correction(
                    correction_type="approval",
                    original_value="pending",
                    corrected_value="approved",
                    context={
                        "vendor": vendor,
                        "amount": amount,
                        "source_channel": source_channel,
                        "override": allow_budget_override,
                        "confidence_override": allow_confidence_override,
                    },
                    user_id=approved_by,
                    invoice_id=gmail_id,
                )
        except Exception as e:
            logger.debug(f"Learning feedback on approval failed: {e}")

        return result

    async def on_rejection(
        self,
        gmail_id: str,
        rejected_by: str,
        reason: str = "",
        source_channel: str = "slack",
    ) -> Dict[str, Any]:
        """
        Handle invoice rejection. Wraps workflow.reject_invoice() and
        records learning feedback so the agent learns from mistakes.
        """
        result = await self.workflow.reject_invoice(
            gmail_id=gmail_id,
            rejected_by=rejected_by,
            reason=reason,
            source_channel=source_channel,
        )

        # Record rejection as correction feedback
        try:
            from clearledgr.core.database import get_db
            db = get_db()
            ap_item = db.get_ap_item_by_thread(self.organization_id, gmail_id)
            if not ap_item:
                ap_item = db.get_ap_item_by_message_id(self.organization_id, gmail_id)

            if ap_item:
                vendor = ap_item.get("vendor_name", "")
                self.correction_learning.record_correction(
                    correction_type="approval",
                    original_value="pending",
                    corrected_value="rejected",
                    context={
                        "vendor": vendor,
                        "amount": ap_item.get("amount", 0),
                        "reason": reason,
                        "source_channel": source_channel,
                    },
                    user_id=rejected_by,
                    invoice_id=gmail_id,
                    feedback=reason,
                )
        except Exception as e:
            logger.debug(f"Learning feedback on rejection failed: {e}")

        return result

    async def on_correction(
        self,
        gmail_id: str,
        correction_type: str,
        original_value: Any,
        corrected_value: Any,
        user_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Handle a user correction (GL code change, vendor fix, etc.).
        Records the correction for learning.
        """
        ctx = context or {}
        return self.correction_learning.record_correction(
            correction_type=correction_type,
            original_value=original_value,
            corrected_value=corrected_value,
            context=ctx,
            user_id=user_id,
            invoice_id=gmail_id,
        )

    # ------------------------------------------------------------------
    # Post-processing (durable queue worker execution)
    # ------------------------------------------------------------------

    async def _run_post_process_steps(
        self,
        *,
        invoice: InvoiceData,
        agent_decision,
        workflow_result: Dict[str, Any],
    ) -> None:
        """
        Post-processing tasks after workflow execution:
        - Autonomous ERP retry on failure
        - Auto follow-up for missing info
        - Clarifying questions for uncertain decisions
        - Proactive insights
        """
        retry_status = self.autonomous_retry_runtime_status()
        if retry_status.get("enabled"):
            try:
                if self._should_retry_erp_post(workflow_result):
                    self._enqueue_erp_retry_job(invoice, workflow_result)
                    self._ensure_retry_worker()
            except Exception as e:
                logger.debug(f"ERP retry failed: {e}")
        elif self._should_retry_erp_post(workflow_result):
            self._record_retry_gated_event(invoice, workflow_result, retry_status)
            logger.info(
                "Autonomous ERP retry skipped for %s (%s)",
                invoice.gmail_id,
                retry_status.get("reason") or "retry_disabled",
            )

        try:
            await self._check_follow_up(invoice)
        except Exception as e:
            logger.debug(f"Follow-up check failed: {e}")

        try:
            await self._check_clarifying_questions(invoice, agent_decision)
        except Exception as e:
            logger.debug(f"Clarifying question check failed: {e}")

        try:
            await self._check_insights(invoice)
        except Exception as e:
            logger.debug(f"Insights check failed: {e}")

    @staticmethod
    def _serialize_agent_decision(agent_decision: Any) -> Dict[str, Any]:
        if not agent_decision:
            return {}

        factors: List[Dict[str, Any]] = []
        raw_factors = getattr(agent_decision, "factors", None) or []
        for raw in raw_factors:
            if isinstance(raw, dict):
                factors.append(
                    {
                        "factor": raw.get("factor"),
                        "score": raw.get("score"),
                        "detail": raw.get("detail"),
                    }
                )
                continue
            factors.append(
                {
                    "factor": getattr(raw, "factor", None),
                    "score": getattr(raw, "score", None),
                    "detail": getattr(raw, "detail", None),
                }
            )

        return {
            "decision": getattr(agent_decision, "decision", None),
            "confidence": getattr(agent_decision, "confidence", None),
            "extraction": getattr(agent_decision, "extraction", None) or {},
            "risks": getattr(agent_decision, "risks", None) or [],
            "summary": getattr(agent_decision, "summary", None),
            "factors": factors,
        }

    @staticmethod
    def _deserialize_agent_decision(snapshot: Dict[str, Any]) -> Optional[Any]:
        if not isinstance(snapshot, dict) or not snapshot:
            return None

        factors = []
        for raw in snapshot.get("factors") or []:
            if not isinstance(raw, dict):
                continue
            factors.append(
                SimpleNamespace(
                    factor=raw.get("factor"),
                    score=raw.get("score"),
                    detail=raw.get("detail"),
                )
            )
        return SimpleNamespace(
            decision=snapshot.get("decision"),
            confidence=float(snapshot.get("confidence") or 0.0),
            extraction=snapshot.get("extraction") if isinstance(snapshot.get("extraction"), dict) else {},
            risks=list(snapshot.get("risks") or []),
            summary=snapshot.get("summary"),
            factors=factors,
        )

    async def _check_follow_up(self, invoice: InvoiceData):
        """Check if follow-up is needed for missing info."""
        missing = self.followup.detect_missing_info({
            "vendor": invoice.vendor_name,
            "amount": invoice.amount,
            "invoice_number": invoice.invoice_number,
            "due_date": invoice.due_date,
            "po_number": invoice.po_number,
            "sender": invoice.sender,
        })
        if missing and missing.missing_info:
            logger.info(
                f"Auto follow-up needed for {invoice.gmail_id}: "
                f"missing {[m.value for m in missing.missing_info]}"
            )
            # The follow-up draft is available for the user in the Slack thread

    async def _check_clarifying_questions(self, invoice: InvoiceData, agent_decision):
        """Post clarifying questions to Slack if the agent is uncertain."""
        if not agent_decision:
            return

        risks = agent_decision.risks or []
        missing_fields = []
        if not invoice.vendor_name or invoice.vendor_name == "Unknown":
            missing_fields.append("vendor")
        if not invoice.amount:
            missing_fields.append("amount")
        if not invoice.invoice_number:
            missing_fields.append("invoice_number")

        if self.conversational.should_ask_question(
            confidence=agent_decision.confidence,
            risks=risks,
            missing_fields=missing_fields,
        ):
            questions = self.conversational.generate_questions(
                invoice_id=invoice.gmail_id,
                extraction=agent_decision.extraction,
                reasoning_factors=[
                    {"factor": f.factor, "score": f.score, "detail": f.detail}
                    for f in agent_decision.factors
                ],
                risks=risks,
            )
            if questions:
                logger.info(
                    f"Posting {len(questions)} clarifying question(s) for {invoice.gmail_id}"
                )
                for q in questions[:2]:  # Max 2 questions per invoice
                    try:
                        await self.conversational.slack_client.send_message(
                            channel=self.workflow.slack_channel,
                            text=f"Question about {invoice.vendor_name} invoice:",
                            blocks=[q.to_slack_blocks()],
                        )
                    except Exception as e:
                        logger.debug(f"Failed to post clarifying question: {e}")

    async def _check_insights(self, invoice: InvoiceData):
        """Generate proactive insights after processing an invoice."""
        insights_list = self.insights.analyze_after_invoice({
            "vendor": invoice.vendor_name,
            "amount": invoice.amount,
            "currency": invoice.currency,
            "organization_id": self.organization_id,
        })
        if insights_list:
            for insight in insights_list[:1]:  # Max 1 insight per invoice
                logger.info(f"Proactive insight: {insight.title}")

    # ------------------------------------------------------------------
    # Earned Autonomy
    # ------------------------------------------------------------------

    def _earned_autonomy_boost(self, vendor_name: str) -> float:
        """
        Per-vendor confidence boost based on approval track record.

        Vendors that have been approved correctly many times earn trust —
        the agent becomes more autonomous for known-good vendors.

        Returns a confidence boost (0.0 to 0.15).
        """
        if not vendor_name:
            return 0.0

        try:
            from clearledgr.core.database import get_db
            db = get_db()
            conn = db.connect()
            cursor = conn.cursor()

            # Count approvals and rejections for this vendor
            cursor.execute(
                """SELECT corrected_value, COUNT(*) as cnt
                   FROM agent_corrections
                   WHERE organization_id = ? AND vendor = ? AND correction_type = 'approval'
                   GROUP BY corrected_value""",
                (self.organization_id, vendor_name),
            )
            rows = cursor.fetchall()
            conn.close()

            approvals = 0
            rejections = 0
            for row in rows:
                if row[0] == "approved":
                    approvals = row[1]
                elif row[0] == "rejected":
                    rejections = row[1]

            total = approvals + rejections
            if total < 3:
                return 0.0  # Not enough history

            approval_rate = approvals / total
            if approval_rate < 0.8:
                return 0.0  # Too many rejections — no trust boost

            # Scale: 3 approvals → +0.03, 10 → +0.08, 50+ → +0.15
            boost = min(0.15, approvals * 0.008)
            # Penalize if any rejections
            boost *= approval_rate

            return round(boost, 3)
        except Exception as e:
            logger.debug(f"Earned autonomy check failed for {vendor_name}: {e}")
            return 0.0

    # ------------------------------------------------------------------
    # Autonomous ERP retry
    # ------------------------------------------------------------------

    @staticmethod
    def _should_retry_erp_post(workflow_result: Dict[str, Any]) -> bool:
        erp_result = workflow_result.get("erp_result")
        if isinstance(erp_result, dict):
            erp_status = erp_result.get("status") or workflow_result.get("erp_status") or workflow_result.get("status")
            if erp_status in ("success", "posted"):
                return False
            return True
        erp_status = workflow_result.get("erp_status") or workflow_result.get("status")
        if erp_status in ("success", "posted"):
            return False
        return workflow_result.get("status") in ("approved", "erp_failed", "posted_partial")

    def _retry_worker_id(self) -> str:
        return f"agent_orchestrator:{self.organization_id}"

    def _serialize_invoice_for_retry(self, invoice: InvoiceData) -> Dict[str, Any]:
        payload = dict(vars(invoice))
        # Ensure org is always present for restart-safe reconstruction.
        payload["organization_id"] = payload.get("organization_id") or self.organization_id
        return payload

    def _invoice_from_retry_snapshot(self, payload: Dict[str, Any]) -> InvoiceData:
        return InvoiceData(
            gmail_id=str(payload.get("gmail_id") or ""),
            subject=str(payload.get("subject") or ""),
            sender=str(payload.get("sender") or ""),
            vendor_name=str(payload.get("vendor_name") or "Unknown"),
            amount=float(payload.get("amount") or 0),
            currency=str(payload.get("currency") or "USD"),
            invoice_number=payload.get("invoice_number"),
            due_date=payload.get("due_date"),
            po_number=payload.get("po_number"),
            confidence=float(payload.get("confidence") or 0.0),
            attachment_url=payload.get("attachment_url"),
            organization_id=str(payload.get("organization_id") or self.organization_id),
            user_id=payload.get("user_id"),
            invoice_text=payload.get("invoice_text"),
            reasoning_summary=payload.get("reasoning_summary"),
            reasoning_factors=payload.get("reasoning_factors"),
            reasoning_risks=payload.get("reasoning_risks"),
            vendor_intelligence=payload.get("vendor_intelligence"),
            policy_compliance=payload.get("policy_compliance"),
            priority=payload.get("priority"),
            budget_impact=payload.get("budget_impact"),
            po_match_result=payload.get("po_match_result"),
            budget_check_result=payload.get("budget_check_result"),
            potential_duplicates=int(payload.get("potential_duplicates") or 0),
            insights=payload.get("insights"),
            field_confidences=payload.get("field_confidences"),
            correlation_id=payload.get("correlation_id"),
        )

    def _lookup_ap_item_for_invoice(self, invoice: InvoiceData) -> Optional[Dict[str, Any]]:
        db = self.workflow.db
        ap_item = db.get_ap_item_by_thread(self.organization_id, invoice.gmail_id)
        if not ap_item:
            ap_item = db.get_ap_item_by_message_id(self.organization_id, invoice.gmail_id)
        return ap_item

    def start_durable_workers(self) -> None:
        """Start background durable workers for retry/post-process queues."""
        self._ensure_retry_worker()

    async def stop_durable_workers(self) -> None:
        """Stop background durable workers gracefully."""
        self._retry_worker_stopping = True
        worker = self._retry_worker_task
        if not worker:
            return
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        finally:
            self._retry_worker_task = None

    def _ensure_retry_worker(self) -> None:
        if not (
            self.post_process_runtime_status().get("enabled")
            or self.autonomous_retry_runtime_status().get("enabled")
        ):
            return
        if self._retry_worker_task and not self._retry_worker_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._retry_worker_stopping = False
        self._retry_worker_task = loop.create_task(self._retry_worker_loop())

    async def _retry_worker_loop(self) -> None:
        poll_seconds = self._retry_poll_interval_seconds()
        try:
            while not self._retry_worker_stopping:
                post_process_status = self.post_process_runtime_status()
                if post_process_status.get("enabled"):
                    try:
                        await self.process_due_post_process_jobs(limit=10)
                    except Exception as exc:  # pragma: no cover - safety net for background task
                        logger.debug("Durable post-process worker iteration failed: %s", exc)

                retry_status = self.autonomous_retry_runtime_status()
                if retry_status.get("enabled"):
                    try:
                        await self.process_due_retry_jobs(limit=10)
                    except Exception as exc:  # pragma: no cover - safety net for background task
                        logger.debug("Durable retry worker iteration failed: %s", exc)
                await asyncio.sleep(poll_seconds)
        except asyncio.CancelledError:  # pragma: no cover
            raise

    def _first_retry_eta(self) -> str:
        schedule = self._retry_backoff_schedule_seconds()
        delay = schedule[0] if schedule else 0
        return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()

    def _next_retry_eta_after_attempt(self, attempt_number: int) -> str:
        schedule = self._retry_backoff_schedule_seconds()
        idx = min(max(int(attempt_number), 0), len(schedule) - 1)
        delay = schedule[idx]
        return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()

    def _first_post_process_eta(self) -> str:
        schedule = self._post_process_backoff_schedule_seconds()
        delay = schedule[0] if schedule else 0
        return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()

    def _next_post_process_eta_after_attempt(self, attempt_number: int) -> str:
        schedule = self._post_process_backoff_schedule_seconds()
        idx = min(max(int(attempt_number), 0), len(schedule) - 1)
        delay = schedule[idx]
        return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()

    def _enqueue_post_process_job(
        self,
        invoice: InvoiceData,
        agent_decision: Any,
        workflow_result: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        status = self.post_process_runtime_status()
        if not status.get("enabled"):
            return None

        db = self.workflow.db
        ap_item = self._lookup_ap_item_for_invoice(invoice)
        if not ap_item:
            return None

        ap_item_id = str(ap_item.get("id") or "")
        correlation_id = (
            invoice.correlation_id
            or self.workflow._get_ap_item_correlation_id(ap_item_id=ap_item_id, gmail_id=invoice.gmail_id)
        )
        if correlation_id:
            invoice.correlation_id = correlation_id

        source_hint = str(ap_item.get("updated_at") or ap_item.get("created_at") or "")
        idempotency_key = (
            f"agent_post_process_job:{ap_item_id}:{source_hint}:{correlation_id or invoice.gmail_id}"
        )
        existing = db.get_agent_retry_job_by_key(idempotency_key)
        if existing and str(existing.get("status") or "") in {"pending", "running", "completed"}:
            return existing

        job = db.create_agent_retry_job(
            {
                "organization_id": self.organization_id,
                "ap_item_id": ap_item_id,
                "gmail_id": invoice.gmail_id,
                "job_type": "post_process",
                "status": "pending",
                "retry_count": 0,
                "max_retries": self._post_process_max_attempts(),
                "next_retry_at": self._first_post_process_eta(),
                "idempotency_key": idempotency_key,
                "correlation_id": correlation_id,
                "payload": {
                    "invoice": self._serialize_invoice_for_retry(invoice),
                    "workflow_result": dict(workflow_result or {}),
                    "agent_decision": self._serialize_agent_decision(agent_decision),
                },
            }
        )
        try:
            db.append_ap_audit_event(
                {
                    "ap_item_id": ap_item_id,
                    "event_type": "agent_post_process_enqueued",
                    "actor_type": "system",
                    "actor_id": "agent_orchestrator",
                    "reason": "post_process_enqueued",
                    "organization_id": self.organization_id,
                    "source": "agent_orchestrator",
                    "correlation_id": correlation_id,
                    "workflow_id": "agent_post_process",
                    "run_id": job.get("id"),
                    "metadata": {
                        "gmail_id": invoice.gmail_id,
                        "next_retry_at": job.get("next_retry_at"),
                        "max_retries": job.get("max_retries"),
                        "post_process_runtime": status,
                    },
                    "idempotency_key": f"agent_post_process_enqueued:{job.get('id')}",
                }
            )
        except Exception as exc:
            logger.debug("Could not record durable post-process enqueue audit event: %s", exc)
        return job

    def _enqueue_erp_retry_job(
        self,
        invoice: InvoiceData,
        workflow_result: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not self._should_retry_erp_post(workflow_result):
            return None

        db = self.workflow.db
        ap_item = self._lookup_ap_item_for_invoice(invoice)
        if not ap_item:
            return None

        ap_item_id = str(ap_item.get("id") or "")
        current_state = str(ap_item.get("state") or "")
        if current_state not in {"failed_post", "ready_to_post", "approved"}:
            return None

        existing = db.get_active_agent_retry_job(
            self.organization_id,
            ap_item_id,
            job_type="erp_post_retry",
        )
        if existing:
            return existing

        correlation_id = (
            invoice.correlation_id
            or self.workflow._get_ap_item_correlation_id(ap_item_id=ap_item_id, gmail_id=invoice.gmail_id)
        )
        if correlation_id:
            invoice.correlation_id = correlation_id

        source_attempt = str(ap_item.get("post_attempted_at") or ap_item.get("updated_at") or "")
        idempotency_key = f"agent_erp_retry_job:{ap_item_id}:{source_attempt}:{correlation_id or invoice.gmail_id}"
        erp_post_idempotency_key = f"agent_erp_retry_post:{ap_item_id}:{correlation_id or 'default'}"

        job = db.create_agent_retry_job(
            {
                "organization_id": self.organization_id,
                "ap_item_id": ap_item_id,
                "gmail_id": invoice.gmail_id,
                "job_type": "erp_post_retry",
                "status": "pending",
                "retry_count": 0,
                "max_retries": max(1, self._env_int("AP_AGENT_AUTONOMOUS_RETRY_MAX_ATTEMPTS", 3)),
                "next_retry_at": self._first_retry_eta(),
                "idempotency_key": idempotency_key,
                "correlation_id": correlation_id,
                "payload": {
                    "invoice": self._serialize_invoice_for_retry(invoice),
                    "workflow_result": dict(workflow_result or {}),
                    "erp_post_idempotency_key": erp_post_idempotency_key,
                    "retry_kind": "autonomous_erp_post",
                },
            }
        )

        try:
            db.append_ap_audit_event(
                {
                    "ap_item_id": ap_item_id,
                    "event_type": "agent_retry_scheduled",
                    "actor_type": "system",
                    "actor_id": "agent_orchestrator",
                    "reason": "erp_post_retry_enqueued",
                    "organization_id": self.organization_id,
                    "source": "agent_orchestrator",
                    "correlation_id": correlation_id,
                    "workflow_id": "agent_retry_erp_post",
                    "run_id": job.get("id"),
                    "metadata": {
                        "gmail_id": invoice.gmail_id,
                        "next_retry_at": job.get("next_retry_at"),
                        "max_retries": job.get("max_retries"),
                        "retry_runtime": self.autonomous_retry_runtime_status(),
                    },
                    "idempotency_key": f"agent_retry_scheduled:{job.get('id')}",
                }
            )
        except Exception as exc:
            logger.debug("Could not record durable retry schedule audit event: %s", exc)
        return job

    async def process_due_retry_jobs(self, limit: int = 10) -> Dict[str, Any]:
        """Process due durable retry jobs for this organization.

        Safe to call after restart; jobs are persisted in the DB.
        """
        summary = {
            "organization_id": self.organization_id,
            "fetched": 0,
            "claimed": 0,
            "processed": 0,
            "succeeded": 0,
            "rescheduled": 0,
            "dead_letter": 0,
            "skipped": 0,
            "errors": 0,
        }
        jobs = self.workflow.db.list_due_agent_retry_jobs(
            organization_id=self.organization_id,
            job_type="erp_post_retry",
            limit=max(1, int(limit or 10)),
        )
        summary["fetched"] = len(jobs)
        for candidate in jobs:
            job_id = str(candidate.get("id") or "")
            if not job_id:
                continue
            claimed = self.workflow.db.claim_agent_retry_job(job_id, worker_id=self._retry_worker_id())
            if not claimed:
                continue
            summary["claimed"] += 1
            outcome = await self._process_erp_retry_job(claimed)
            summary["processed"] += 1
            bucket = str(outcome.get("result") or "errors")
            if bucket in summary:
                summary[bucket] += 1
            else:
                summary["errors"] += 1
        return summary

    async def process_due_post_process_jobs(self, limit: int = 10) -> Dict[str, Any]:
        """Process due durable post-process jobs for this organization."""
        summary = {
            "organization_id": self.organization_id,
            "fetched": 0,
            "claimed": 0,
            "processed": 0,
            "succeeded": 0,
            "rescheduled": 0,
            "dead_letter": 0,
            "skipped": 0,
            "errors": 0,
        }
        jobs = self.workflow.db.list_due_agent_retry_jobs(
            organization_id=self.organization_id,
            job_type="post_process",
            limit=max(1, int(limit or 10)),
        )
        summary["fetched"] = len(jobs)
        for candidate in jobs:
            job_id = str(candidate.get("id") or "")
            if not job_id:
                continue
            claimed = self.workflow.db.claim_agent_retry_job(job_id, worker_id=self._retry_worker_id())
            if not claimed:
                continue
            summary["claimed"] += 1
            outcome = await self._process_post_process_job(claimed)
            summary["processed"] += 1
            bucket = str(outcome.get("result") or "errors")
            if bucket in summary:
                summary[bucket] += 1
            else:
                summary["errors"] += 1
        return summary

    async def _process_post_process_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        db = self.workflow.db
        job_id = str(job.get("id") or "")
        ap_item_id = str(job.get("ap_item_id") or "")
        correlation_id = job.get("correlation_id")
        payload = job.get("payload") or job.get("payload_json") or {}
        payload = payload if isinstance(payload, dict) else {}

        if not ap_item_id:
            db.complete_agent_retry_job(
                job_id,
                status="dead_letter",
                last_error="post_process_missing_ap_item_id",
                result={"error": "post_process_missing_ap_item_id"},
            )
            return {"result": "dead_letter"}

        ap_item = db.get_ap_item(ap_item_id)
        if not ap_item:
            db.complete_agent_retry_job(
                job_id,
                status="dead_letter",
                last_error="post_process_ap_item_not_found",
                result={"error": "post_process_ap_item_not_found"},
            )
            return {"result": "dead_letter"}

        invoice_payload = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else {}
        workflow_result = payload.get("workflow_result") if isinstance(payload.get("workflow_result"), dict) else {}
        decision_payload = payload.get("agent_decision") if isinstance(payload.get("agent_decision"), dict) else {}

        invoice = self._invoice_from_retry_snapshot(invoice_payload)
        if not invoice.gmail_id:
            invoice.gmail_id = str(ap_item.get("thread_id") or job.get("gmail_id") or "")
        if not invoice.gmail_id:
            db.complete_agent_retry_job(
                job_id,
                status="dead_letter",
                last_error="post_process_missing_gmail_id",
                result={"error": "post_process_missing_gmail_id"},
            )
            return {"result": "dead_letter"}
        if correlation_id:
            invoice.correlation_id = str(correlation_id)
        elif not invoice.correlation_id:
            invoice.correlation_id = self.workflow._get_ap_item_correlation_id(
                ap_item_id=ap_item_id,
                gmail_id=invoice.gmail_id,
            )

        agent_decision = self._deserialize_agent_decision(decision_payload)
        attempt_number = int(job.get("retry_count") or 1)
        max_retries = max(1, int(job.get("max_retries") or self._post_process_max_attempts()))

        try:
            await self._run_post_process_steps(
                invoice=invoice,
                agent_decision=agent_decision,
                workflow_result=workflow_result,
            )
            db.complete_agent_retry_job(
                job_id,
                status="completed",
                result={
                    "status": "completed",
                    "attempt": attempt_number,
                },
            )
            try:
                db.append_ap_audit_event(
                    {
                        "ap_item_id": ap_item_id,
                        "event_type": "agent_post_process_completed",
                        "actor_type": "system",
                        "actor_id": "agent_orchestrator",
                        "reason": "post_process_completed",
                        "organization_id": self.organization_id,
                        "source": "agent_orchestrator",
                        "correlation_id": invoice.correlation_id,
                        "workflow_id": "agent_post_process",
                        "run_id": job_id,
                        "metadata": {
                            "attempt": attempt_number,
                            "max_retries": max_retries,
                        },
                        "idempotency_key": f"agent_post_process_completed:{job_id}:{attempt_number}",
                    }
                )
            except Exception as exc:
                logger.debug("Could not record post-process completion audit event: %s", exc)
            return {"result": "succeeded"}
        except Exception as exc:
            error_message = str(exc) or "post_process_failed"
            if attempt_number >= max_retries:
                db.complete_agent_retry_job(
                    job_id,
                    status="dead_letter",
                    last_error=error_message,
                    result={
                        "status": "dead_letter",
                        "attempt": attempt_number,
                        "max_retries": max_retries,
                        "error": error_message,
                    },
                )
                event_type = "agent_post_process_dead_letter"
                summary_key = "dead_letter"
            else:
                next_retry_at = self._next_post_process_eta_after_attempt(attempt_number)
                db.reschedule_agent_retry_job(
                    job_id,
                    next_retry_at=next_retry_at,
                    last_error=error_message,
                    result={
                        "status": "retry_scheduled",
                        "attempt": attempt_number,
                        "max_retries": max_retries,
                        "error": error_message,
                        "next_retry_at": next_retry_at,
                    },
                    status="pending",
                )
                event_type = "agent_post_process_rescheduled"
                summary_key = "rescheduled"

            try:
                metadata: Dict[str, Any] = {
                    "attempt": attempt_number,
                    "max_retries": max_retries,
                    "error": error_message,
                }
                if summary_key == "rescheduled":
                    metadata["next_retry_at"] = self.workflow.db.get_agent_retry_job(job_id).get("next_retry_at")
                db.append_ap_audit_event(
                    {
                        "ap_item_id": ap_item_id,
                        "event_type": event_type,
                        "actor_type": "system",
                        "actor_id": "agent_orchestrator",
                        "reason": "post_process_failed",
                        "organization_id": self.organization_id,
                        "source": "agent_orchestrator",
                        "correlation_id": invoice.correlation_id,
                        "workflow_id": "agent_post_process",
                        "run_id": job_id,
                        "metadata": metadata,
                        "idempotency_key": f"{event_type}:{job_id}:{attempt_number}",
                    }
                )
            except Exception as audit_exc:
                logger.debug("Could not record post-process failure audit event: %s", audit_exc)
            return {"result": summary_key}

    async def _process_erp_retry_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        db = self.workflow.db
        job_id = str(job.get("id") or "")
        ap_item_id = str(job.get("ap_item_id") or "")
        correlation_id = job.get("correlation_id")
        payload = job.get("payload") or job.get("payload_json") or {}
        if not isinstance(payload, dict):
            payload = {}
        invoice_payload = payload.get("invoice") or {}
        invoice = self._invoice_from_retry_snapshot(invoice_payload if isinstance(invoice_payload, dict) else {})
        if not invoice.gmail_id:
            invoice.gmail_id = str(job.get("gmail_id") or "")
        if not invoice.gmail_id:
            db.complete_agent_retry_job(
                job_id,
                status="dead_letter",
                last_error="retry_job_missing_gmail_id",
                result={"error": "retry_job_missing_gmail_id"},
            )
            return {"result": "dead_letter"}

        ap_item = db.get_ap_item(ap_item_id) if ap_item_id else None
        if not ap_item:
            db.complete_agent_retry_job(
                job_id,
                status="dead_letter",
                last_error="retry_ap_item_not_found",
                result={"error": "retry_ap_item_not_found"},
            )
            return {"result": "dead_letter"}

        current_state = str(ap_item.get("state") or "")
        if current_state in {"posted_to_erp", "closed"}:
            db.complete_agent_retry_job(
                job_id,
                status="completed",
                result={
                    "status": "noop_already_posted",
                    "ap_item_state": current_state,
                    "erp_reference": ap_item.get("erp_reference"),
                },
            )
            return {"result": "skipped"}
        if current_state not in {"failed_post", "ready_to_post", "approved"}:
            db.complete_agent_retry_job(
                job_id,
                status="skipped",
                last_error=f"invalid_retry_state:{current_state or 'unknown'}",
                result={
                    "status": "skipped_invalid_state",
                    "ap_item_state": current_state,
                },
            )
            return {"result": "skipped"}

        attempt_number = int(job.get("retry_count") or 1)
        max_retries = max(1, int(job.get("max_retries") or 3))
        workflow_id = "agent_retry_erp_post"
        run_id = job_id
        if current_state in {"failed_post", "approved"}:
            self.workflow._transition_invoice_state(
                invoice.gmail_id,
                "ready_to_post",
                correlation_id=correlation_id,
                source="agent_orchestrator",
                workflow_id=workflow_id,
                run_id=run_id,
                decision_reason="autonomous_retry_attempt",
            )

        try:
            result = await self.workflow._post_to_erp(
                invoice,
                idempotency_key=str(payload.get("erp_post_idempotency_key") or f"agent_erp_retry_post:{ap_item_id}"),
                correlation_id=str(correlation_id) if correlation_id else None,
            )
        except Exception as exc:
            result = {
                "status": "error",
                "error_code": "agent_retry_exception",
                "error_message": str(exc),
            }

        post_attempted_at = datetime.now(timezone.utc).isoformat()
        if result.get("status") == "success":
            erp_reference = (
                result.get("erp_reference")
                or result.get("bill_id")
                or result.get("reference_id")
                or result.get("doc_num")
            )
            self.workflow._transition_invoice_state(
                invoice.gmail_id,
                "posted_to_erp",
                correlation_id=correlation_id,
                source="agent_orchestrator",
                workflow_id=workflow_id,
                run_id=run_id,
                decision_reason="autonomous_retry_succeeded",
                erp_reference=erp_reference,
                erp_posted_at=post_attempted_at,
                post_attempted_at=post_attempted_at,
                last_error=None,
            )
            db.complete_agent_retry_job(
                job_id,
                status="completed",
                result={
                    "status": "success",
                    "attempt": attempt_number,
                    "erp_reference": erp_reference,
                    "erp_result": result,
                },
            )
            try:
                db.append_ap_audit_event(
                    {
                        "ap_item_id": ap_item_id,
                        "event_type": "agent_retry_succeeded",
                        "actor_type": "system",
                        "actor_id": "agent_orchestrator",
                        "reason": "erp_post_retry_succeeded",
                        "organization_id": self.organization_id,
                        "source": "agent_orchestrator",
                        "correlation_id": correlation_id,
                        "workflow_id": workflow_id,
                        "run_id": run_id,
                        "metadata": {
                            "attempt": attempt_number,
                            "max_retries": max_retries,
                            "erp_reference": erp_reference,
                        },
                        "idempotency_key": f"agent_retry_success:{job_id}:{attempt_number}",
                    }
                )
            except Exception as exc:
                logger.debug("Could not record retry success audit event: %s", exc)
            return {"result": "succeeded", "erp_result": result}

        failure_reason = (
            str(result.get("error_message") or "")
            or str(result.get("reason") or "")
            or str(result.get("status") or "")
            or "erp_post_failed"
        )
        self.workflow._transition_invoice_state(
            invoice.gmail_id,
            "failed_post",
            correlation_id=correlation_id,
            source="agent_orchestrator",
            workflow_id=workflow_id,
            run_id=run_id,
            decision_reason="autonomous_retry_failed",
            post_attempted_at=post_attempted_at,
            last_error=failure_reason,
        )

        terminal = attempt_number >= max_retries
        if terminal:
            db.complete_agent_retry_job(
                job_id,
                status="dead_letter",
                last_error=failure_reason,
                result={
                    "status": "dead_letter",
                    "attempt": attempt_number,
                    "max_retries": max_retries,
                    "erp_result": result,
                },
            )
            event_type = "agent_retry_dead_letter"
            summary_key = "dead_letter"
        else:
            next_retry_at = self._next_retry_eta_after_attempt(attempt_number)
            db.reschedule_agent_retry_job(
                job_id,
                next_retry_at=next_retry_at,
                last_error=failure_reason,
                result={
                    "status": "retry_scheduled",
                    "attempt": attempt_number,
                    "max_retries": max_retries,
                    "erp_result": result,
                    "next_retry_at": next_retry_at,
                },
                status="pending",
            )
            event_type = "agent_retry_rescheduled"
            summary_key = "rescheduled"

        try:
            event_metadata: Dict[str, Any] = {
                "attempt": attempt_number,
                "max_retries": max_retries,
                "failure_reason": failure_reason,
            }
            if not terminal:
                event_metadata["next_retry_at"] = self.workflow.db.get_agent_retry_job(job_id).get("next_retry_at")
            db.append_ap_audit_event(
                {
                    "ap_item_id": ap_item_id,
                    "event_type": event_type,
                    "actor_type": "system",
                    "actor_id": "agent_orchestrator",
                    "reason": "erp_post_retry_failed",
                    "organization_id": self.organization_id,
                    "source": "agent_orchestrator",
                    "correlation_id": correlation_id,
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "metadata": event_metadata,
                    "idempotency_key": f"{event_type}:{job_id}:{attempt_number}",
                }
            )
        except Exception as exc:
            logger.debug("Could not record retry failure audit event: %s", exc)

        return {"result": summary_key, "erp_result": result}

    def _record_retry_gated_event(
        self,
        invoice: InvoiceData,
        workflow_result: Dict[str, Any],
        retry_status: Dict[str, Any],
    ) -> None:
        try:
            db = self.workflow.db
            ap_item = db.get_ap_item_by_thread(self.organization_id, invoice.gmail_id)
            if not ap_item:
                ap_item = db.get_ap_item_by_message_id(self.organization_id, invoice.gmail_id)
            if not ap_item:
                return
            reason = str(retry_status.get("reason") or "autonomous_retry_disabled")
            db.append_ap_audit_event(
                {
                    "ap_item_id": ap_item["id"],
                    "event_type": "agent_retry_skipped_non_durable",
                    "actor_type": "system",
                    "actor_id": "agent_orchestrator",
                    "reason": reason,
                    "organization_id": self.organization_id,
                    "source": "agent_orchestrator",
                    "metadata": {
                        "gmail_id": invoice.gmail_id,
                        "workflow_result_status": workflow_result.get("status"),
                        "erp_status": workflow_result.get("erp_status"),
                        "retry_runtime": retry_status,
                    },
                    "idempotency_key": f"agent_retry_skipped:{ap_item['id']}:{reason}:{workflow_result.get('status')}:{workflow_result.get('erp_status')}",
                }
            )
        except Exception as exc:
            logger.debug("Could not record retry gated audit event: %s", exc)

    def _record_post_process_gated_event(
        self,
        invoice: InvoiceData,
        workflow_result: Dict[str, Any],
        post_process_status: Dict[str, Any],
        *,
        reason: Optional[str] = None,
    ) -> None:
        try:
            db = self.workflow.db
            ap_item = db.get_ap_item_by_thread(self.organization_id, invoice.gmail_id)
            if not ap_item:
                ap_item = db.get_ap_item_by_message_id(self.organization_id, invoice.gmail_id)
            if not ap_item:
                return
            event_reason = str(reason or post_process_status.get("reason") or "post_process_queue_unavailable")
            db.append_ap_audit_event(
                {
                    "ap_item_id": ap_item["id"],
                    "event_type": "agent_post_process_skipped",
                    "actor_type": "system",
                    "actor_id": "agent_orchestrator",
                    "reason": event_reason,
                    "organization_id": self.organization_id,
                    "source": "agent_orchestrator",
                    "metadata": {
                        "gmail_id": invoice.gmail_id,
                        "workflow_result_status": workflow_result.get("status"),
                        "erp_status": workflow_result.get("erp_status"),
                        "post_process_runtime": post_process_status,
                    },
                    "idempotency_key": (
                        f"agent_post_process_skipped:{ap_item['id']}:{event_reason}:"
                        f"{workflow_result.get('status')}:{workflow_result.get('erp_status')}"
                    ),
                }
            )
        except Exception as exc:
            logger.debug("Could not record post-process gated audit event: %s", exc)


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------

_orchestrator_cache: Dict[str, AgentOrchestrator] = {}


def get_orchestrator(organization_id: str = "default") -> AgentOrchestrator:
    """Get or create an AgentOrchestrator for the given org."""
    if organization_id not in _orchestrator_cache:
        _orchestrator_cache[organization_id] = AgentOrchestrator(organization_id)
    return _orchestrator_cache[organization_id]
