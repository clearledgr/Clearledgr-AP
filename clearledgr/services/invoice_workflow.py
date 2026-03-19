"""Internal AP workflow machinery used behind the finance runtime contract.

This module contains the implementation substrate for invoice lifecycle work
such as validation, approval routing, and ERP posting. User-facing API
surfaces should enter through ``FinanceAgentRuntime``; this workflow service is
an internal execution detail behind that contract boundary.
"""

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta

from clearledgr.core.ap_confidence import (
    DEFAULT_CRITICAL_FIELD_CONFIDENCE_THRESHOLD,
    evaluate_critical_field_confidence,
)
from clearledgr.core.ap_states import (
    APState,
    OverrideContext,
    classify_post_failure_recoverability,
)
from clearledgr.core.database import get_db
from clearledgr.services.slack_api import SlackAPIClient, get_slack_client
try:
    from clearledgr.services.teams_api import TeamsAPIClient
except Exception as e:  # pragma: no cover - optional integration in some local builds
    logging.getLogger(__name__).info("TeamsAPIClient not available: %s", e)
    TeamsAPIClient = None  # type: ignore[assignment]
from clearledgr.services.policy_compliance import get_policy_compliance
from clearledgr.services.budget_awareness import get_budget_awareness
from clearledgr.services.purchase_orders import get_purchase_order_service
from clearledgr.integrations.erp_router import (
    Bill, Vendor, get_or_create_vendor
)
from clearledgr.services.audit_trail import get_audit_trail
from clearledgr.services.erp_api_first import post_bill_api_first
from clearledgr.services.learning import get_learning_service
from clearledgr.services.approval_card_builder import (
    budget_status_rank,
    normalize_budget_checks,
    compute_budget_summary,
    humanize_reason_code,
    dedupe_reason_lines,
    build_approval_surface_copy,
    build_approval_blocks,
)
from clearledgr.services.invoice_models import InvoiceData  # noqa: F401 — re-export
from clearledgr.services.invoice_validation import InvoiceValidationMixin
from clearledgr.services.invoice_posting import InvoicePostingMixin

logger = logging.getLogger(__name__)


class InvoiceWorkflowService(InvoiceValidationMixin, InvoicePostingMixin):
    """
    Internal implementation for AP workflow execution.
    
    Usage:
        service = InvoiceWorkflowService(organization_id="acme")
        
        # When invoice detected in Gmail
        result = await service.process_new_invoice(invoice_data)
        
        # When approved in Slack
        result = await service.approve_invoice(gmail_id, approved_by="user@acme.com")
        
        # When rejected in Slack
        result = await service.reject_invoice(gmail_id, reason="Duplicate", rejected_by="user@acme.com")
    """
    
    def __init__(
        self,
        organization_id: str,
        slack_channel: Optional[str] = None,
        auto_approve_threshold: float = 0.95,
    ):
        self.organization_id = organization_id
        self._slack_channel = slack_channel
        self._auto_approve_threshold = auto_approve_threshold
        self.db = get_db()
        self._slack_client: Optional[SlackAPIClient] = None
        self._teams_client: Optional[Any] = None
        self._settings_loaded = False
        self._settings: Optional[Dict] = None

        from clearledgr.services.state_observers import (
            AuditTrailObserver,
            NotificationObserver,
            StateObserverRegistry,
            VendorFeedbackObserver,
        )
        self._observer_registry = StateObserverRegistry()
        self._observer_registry.register(AuditTrailObserver(self.db))
        self._observer_registry.register(VendorFeedbackObserver(self.db))
        self._observer_registry.register(NotificationObserver(self.db))

    def _load_settings(self):
        """Load organization settings if not already loaded."""
        if self._settings_loaded:
            return
        
        try:
            org = self.db.get_organization(self.organization_id)
            if org:
                settings = org.get("settings", {})
                if isinstance(settings, str):
                    import json
                    settings = json.loads(settings) if settings else {}
                self._settings = settings
        except Exception as e:
            logger.warning("Failed to load org settings for %s: %s", self.organization_id, e)
            self._settings = {}
        
        self._settings_loaded = True
    
    @property
    def slack_channel(self) -> str:
        """Get Slack channel, using settings if available."""
        if self._slack_channel:
            return self._slack_channel
        
        self._load_settings()
        if self._settings:
            channels = self._settings.get("slack_channels", {})
            return channels.get("invoices", "#finance-approvals")
        env_channel = (
            os.getenv("SLACK_APPROVAL_CHANNEL")
            or os.getenv("SLACK_DEFAULT_CHANNEL")
            or ""
        ).strip()
        return env_channel or "#finance-approvals"
    
    @property
    def auto_approve_threshold(self) -> float:
        """Get auto-approve threshold from settings."""
        self._load_settings()
        if self._settings:
            return self._settings.get("auto_approve_threshold", self._auto_approve_threshold)
        return self._auto_approve_threshold
    
    def get_approval_channel_for_amount(self, amount: float) -> str:
        """Get appropriate Slack channel based on amount thresholds."""
        self._load_settings()
        
        if not self._settings:
            return self.slack_channel
        
        thresholds = self._settings.get("approval_thresholds", [])
        
        for threshold in thresholds:
            min_amt = threshold.get("min_amount", 0)
            max_amt = threshold.get("max_amount")
            
            if amount >= min_amt and (max_amt is None or amount < max_amt):
                return threshold.get("approver_channel", self.slack_channel)
        
        return self.slack_channel
    
    @property
    def slack_client(self) -> SlackAPIClient:
        """Lazy-load Slack client."""
        if self._slack_client is None:
            self._slack_client = get_slack_client(organization_id=self.organization_id)
        return self._slack_client

    @property
    def teams_client(self) -> Optional[Any]:
        """Lazy-load Teams client."""
        if TeamsAPIClient is None:
            return None
        if self._teams_client is None:
            self._teams_client = TeamsAPIClient.from_env(self.organization_id)
        return self._teams_client

    async def _get_ap_decision(
        self,
        invoice: InvoiceData,
        validation_gate: Dict[str, Any],
    ):
        """Assemble vendor context and call APDecisionService. Never raises.

        Returns an APDecision object.  If the API key is absent or Claude fails,
        the service's built-in fallback reproduces the existing rule-based routing
        so the workflow is never blocked.
        """
        from clearledgr.services.ap_decision import APDecisionService

        decision_feedback: Dict[str, Any] = {}
        try:
            vendor_profile = (
                self.db.get_vendor_profile(self.organization_id, invoice.vendor_name)
                if hasattr(self.db, "get_vendor_profile") else None
            )
            vendor_history = (
                self.db.get_vendor_invoice_history(self.organization_id, invoice.vendor_name, limit=6)
                if hasattr(self.db, "get_vendor_invoice_history") else []
            )
            decision_feedback = (
                self.db.get_vendor_decision_feedback_summary(
                    self.organization_id,
                    invoice.vendor_name,
                    window_days=180,
                )
                if hasattr(self.db, "get_vendor_decision_feedback_summary")
                else {}
            )

            # Best-effort correction suggestions
            suggestions: Dict[str, Any] = {}
            try:
                from clearledgr.services.correction_learning import CorrectionLearningService
                svc = CorrectionLearningService(self.organization_id)
                gl_sug = svc.suggest("gl_code", {"vendor": invoice.vendor_name})
                if gl_sug:
                    suggestions["gl_code"] = gl_sug
            except Exception:
                pass

            org_config: Dict[str, Any] = {}
            try:
                _org_row = self.db.get_organization(self.organization_id) or {}
                _raw_settings = _org_row.get("settings_json") or _org_row.get("settings") or {}
                if isinstance(_raw_settings, str):
                    _raw_settings = json.loads(_raw_settings)
                if isinstance(_raw_settings, dict):
                    _cfg = _raw_settings.get("org_config") or {}
                    if isinstance(_cfg, dict):
                        org_config = _cfg
            except Exception:
                pass

            # ---- Cross-invoice duplicate/anomaly analysis ----
            cross_analysis_dict: Optional[Dict[str, Any]] = None
            try:
                from clearledgr.services.cross_invoice_analysis import CrossInvoiceAnalyzer
                analyzer = CrossInvoiceAnalyzer(self.organization_id)
                cross_result = analyzer.analyze(
                    vendor=invoice.vendor_name,
                    amount=invoice.amount,
                    invoice_number=getattr(invoice, "invoice_number", None),
                    invoice_date=getattr(invoice, "due_date", None),
                    currency=getattr(invoice, "currency", "USD"),
                    gmail_id=invoice.gmail_id,
                )
                cross_analysis_dict = cross_result.to_dict() if cross_result else None
            except Exception as exc:
                logger.debug("[APDecision] Cross-invoice analysis skipped (non-fatal): %s", exc)

            # ---- Volume anomaly detection ----
            anomaly_signals: Dict[str, Any] = {}
            try:
                from clearledgr.services.agent_anomaly_detection import detect_volume_anomalies
                historical_amounts = [
                    h.get("amount") for h in (vendor_history or [])
                    if h.get("amount") is not None
                ]
                if historical_amounts and invoice.amount is not None:
                    vol_result = detect_volume_anomalies(invoice.amount, historical_amounts)
                    if vol_result and vol_result.get("is_anomaly"):
                        anomaly_signals["volume"] = vol_result
            except Exception as exc:
                logger.debug("[APDecision] Volume anomaly detection skipped (non-fatal): %s", exc)

            # ---- Vendor risk score ----
            vendor_risk: Optional[Dict[str, Any]] = None
            try:
                from clearledgr.services.ap_decision import compute_vendor_risk_score
                vendor_risk = compute_vendor_risk_score(
                    vendor_profile=vendor_profile,
                    cross_invoice_analysis=cross_analysis_dict,
                    anomaly_signals=anomaly_signals,
                    decision_feedback=decision_feedback,
                )
            except Exception as exc:
                logger.debug("[APDecision] Risk score computation skipped (non-fatal): %s", exc)

            # Enrich invoice with risk signals for downstream UX
            if vendor_risk and vendor_risk.get("flags"):
                existing_risks = getattr(invoice, "reasoning_risks", None) or []
                invoice.reasoning_risks = existing_risks + vendor_risk["flags"]

            decision_svc = APDecisionService()
            decision = await decision_svc.decide(
                invoice,
                vendor_profile=vendor_profile,
                vendor_history=vendor_history,
                decision_feedback=decision_feedback,
                correction_suggestions=suggestions,
                validation_gate=validation_gate,
                org_config=org_config,
                cross_invoice_analysis=cross_analysis_dict,
                anomaly_signals=anomaly_signals,
                vendor_risk_score=vendor_risk,
            )
            logger.info(
                "[APDecision] %s → %s (confidence=%.2f fallback=%s risk=%s): %s",
                invoice.vendor_name, decision.recommendation,
                decision.confidence, decision.fallback,
                (vendor_risk or {}).get("level", "n/a"),
                decision.reasoning[:120],
            )
            return decision
        except Exception as exc:
            logger.warning("[APDecision] Unexpected error, using conservative fallback: %s", exc)
            from clearledgr.services.ap_decision import APDecisionService
            return APDecisionService()._fallback_decision(
                invoice,
                validation_gate,
                decision_feedback=decision_feedback,
            )

    async def process_new_invoice(self, invoice: InvoiceData, ap_decision=None) -> Dict[str, Any]:
        """
        Process a newly detected invoice email.
        
        Flow:
        1. Save invoice to database with 'received' status
        2. If confidence >= threshold, auto-approve and post
        3. Otherwise, send to Slack for approval
        
        Returns:
            Dict with status, invoice_id, and action taken
        """
        existing = self.db.get_invoice_status(invoice.gmail_id)
        if existing:
            if existing.get("status") == "posted":
                return {
                    "status": "already_posted",
                    "invoice_id": invoice.gmail_id,
                    "erp_bill_id": existing.get("erp_bill_id"),
                }
            if existing.get("status") == "pending_approval" and existing.get("slack_thread_id"):
                thread = self.db.get_slack_thread(invoice.gmail_id)
                return {
                    "status": "pending_approval",
                    "invoice_id": invoice.gmail_id,
                    "slack_channel": thread.get("channel_id") if thread else None,
                    "slack_ts": thread.get("thread_ts") if thread else None,
                    "existing": True,
                }

        # Save invoice to database (canonical AP state: received)
        invoice_id = self.db.save_invoice_status(
            gmail_id=invoice.gmail_id,
            status="received",
            email_subject=invoice.subject,
            vendor=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            invoice_number=invoice.invoice_number,
            due_date=invoice.due_date,
            confidence=invoice.confidence,
            organization_id=self.organization_id,
            user_id=invoice.user_id,
        )
        
        logger.info(f"New invoice detected: {invoice.vendor_name} ${invoice.amount} (confidence: {invoice.confidence})")
        correlation_id = self._ensure_ap_item_correlation_id(
            ap_item_id=invoice_id,
            gmail_id=invoice.gmail_id,
            preferred=invoice.correlation_id,
        )
        invoice.correlation_id = correlation_id

        # Deterministic controls always run before confidence-based routing.
        validation_gate = await self._evaluate_deterministic_validation(invoice)
        confidence_gate = validation_gate.get("confidence_gate") if isinstance(validation_gate, dict) else None
        self._update_ap_item_metadata(
            invoice_id,
            {
                "validation_gate": validation_gate,
                "confidence_gate": confidence_gate or {},
                "requires_field_review": bool(
                    isinstance(confidence_gate, dict) and confidence_gate.get("requires_field_review")
                ),
                "confidence_blockers": (
                    confidence_gate.get("confidence_blockers") if isinstance(confidence_gate, dict) else []
                ) or [],
                "field_confidences": invoice.field_confidences or {},
                "correlation_id": correlation_id,
                "erp_preflight": invoice.erp_preflight or {},
            },
        )

        # Validation/extraction completed: advance AP item to canonical `validated`
        # before routing to human approval or auto-posting.
        self._transition_invoice_state(
            invoice.gmail_id,
            "validated",
            correlation_id=correlation_id,
            workflow_id="invoice_entry",
        )

        # --- AP reasoning layer: Claude decides with vendor context ---
        # If a pre-computed decision was provided (e.g. from the agent planning loop),
        # skip the internal Claude call to avoid a double Sonnet invocation.
        if ap_decision is None:
            ap_decision = await self._get_ap_decision(invoice, validation_gate)

        # Populate InvoiceData reasoning fields (surfaced in Slack cards, Gmail sidebar)
        invoice.reasoning_summary = ap_decision.reasoning
        invoice.reasoning_risks = ap_decision.risk_flags
        invoice.vendor_intelligence = {
            **(invoice.vendor_intelligence or {}),
            "vendor_context": ap_decision.vendor_context_used,
            "ap_decision": ap_decision.recommendation,
            "decision_feedback": {
                "count": ap_decision.vendor_context_used.get("feedback_count", 0),
                "override_rate": ap_decision.vendor_context_used.get("feedback_override_rate", 0.0),
                "strictness_bias": ap_decision.vendor_context_used.get("feedback_strictness_bias", "neutral"),
            },
        }

        # Persist Claude's reasoning into ap_item metadata so the Gmail sidebar
        # card can show it proactively (without requiring the "Why?" button click).
        # Use invoice_id directly — it was returned by save_invoice_status() above,
        # so we know the row exists. _lookup_ap_item_id would silently return None here.
        self._update_ap_item_metadata(
            invoice_id,
            {
                "ap_decision_reasoning": ap_decision.reasoning[:1024],  # cap length
                "ap_decision_recommendation": ap_decision.recommendation,
                "ap_decision_risk_flags": ap_decision.risk_flags,
                "ap_decision_model": ap_decision.model,
                "vendor_intelligence": invoice.vendor_intelligence,
            },
        )

        # Audit: Log the AP agent decision
        try:
            trail = get_audit_trail(self.organization_id)
            trail.log_decision(
                invoice_id=invoice.gmail_id,
                decision=ap_decision.recommendation,
                reasoning=ap_decision.reasoning,
                confidence=ap_decision.confidence,
                factors=[{"risk_flags": ap_decision.risk_flags, "model": ap_decision.model}],
            )
        except Exception as audit_exc:
            logger.debug("Audit trail log_decision failed (non-fatal): %s", audit_exc)

        # Deterministic gate is a hard guardrail that overrides Claude.
        # If it fires, route to human — but use Claude's reasoning as context.
        if not validation_gate.get("passed", True):
            self._record_validation_gate_failure(
                invoice,
                validation_gate,
                correlation_id=correlation_id,
            )
            logger.info(
                "Routing invoice %s to approval due to deterministic controls: %s",
                invoice.gmail_id,
                ", ".join(validation_gate.get("reason_codes") or []),
            )
            result = await self._send_for_approval(
                invoice,
                extra_context={
                    "validation_gate": validation_gate,
                    "ap_decision": ap_decision.recommendation,
                    "ap_reasoning": ap_decision.reasoning,
                    "erp_preflight": validation_gate.get("erp_preflight") if isinstance(validation_gate, dict) else None,
                },
            )
            if isinstance(result, dict):
                result.setdefault("validation_gate", validation_gate)
                result.setdefault("reason_codes", validation_gate.get("reason_codes") or [])
            return result

        # Claude says needs_info: transition to needs_info state with the exact question.
        if ap_decision.recommendation == "needs_info" and ap_decision.info_needed:
            logger.info(
                "AP decision needs_info for %s: %s",
                invoice.gmail_id, ap_decision.info_needed[:80],
            )
            self._transition_invoice_state(
                invoice.gmail_id, "needs_info",
                correlation_id=correlation_id,
                decision_reason="ap_decision_needs_info",
            )
            ap_item_id = self._lookup_ap_item_id(invoice.gmail_id)
            self._update_ap_item_metadata(
                ap_item_id,
                {
                    "needs_info_question": ap_decision.info_needed,
                    "ap_decision_reasoning": ap_decision.reasoning,
                    "ap_decision_risk_flags": ap_decision.risk_flags,
                },
            )
            draft_id = await self._create_needs_info_vendor_draft(
                ap_item_id=ap_item_id,
                thread_id=invoice.gmail_id,
                to_email=invoice.sender,
                invoice_data={
                    "subject": invoice.subject,
                    "vendor_name": invoice.vendor_name,
                    "amount": invoice.amount,
                    "invoice_number": invoice.invoice_number,
                },
                question=ap_decision.info_needed,
                user_id=invoice.user_id,
            )
            self._apply_needs_info_followup_metadata(
                ap_item_id=ap_item_id,
                draft_id=draft_id,
                question=ap_decision.info_needed,
                actor_type="system",
                actor_id="ap_agent",
                source="invoice_workflow",
                correlation_id=correlation_id,
            )

            return {
                "status": "needs_info",
                "invoice_id": invoice.gmail_id,
                "reason": ap_decision.reasoning,
                "info_needed": ap_decision.info_needed,
                "risk_flags": ap_decision.risk_flags,
                "ap_decision": "needs_info",
            }

        # LEARNING: Check if we have a learned GL code for this vendor
        suggested_gl = None
        try:
            learning = get_learning_service(self.organization_id)
            suggestion = learning.suggest_gl_code(
                vendor=invoice.vendor_name,
                amount=invoice.amount,
            )
            if suggestion and suggestion.get("confidence", 0) > 0.5:
                suggested_gl = suggestion
                logger.info(f"Learning suggested GL {suggestion.get('gl_code')} for {invoice.vendor_name} (confidence: {suggestion.get('confidence'):.2f})")
                
                # Boost confidence if we've seen this vendor before
                if suggestion.get("confidence", 0) > 0.8:
                    invoice.confidence = min(0.99, invoice.confidence + 0.1)
        except Exception as e:
            logger.warning(f"Failed to get GL suggestion from learning: {e}")
        
        # Route based on Claude's recommendation (gate already passed above).
        if ap_decision.recommendation == "approve":
            logger.info(
                "AP decision approve for %s (confidence=%.2f fallback=%s)",
                invoice.gmail_id, ap_decision.confidence, ap_decision.fallback,
            )
            return await self._auto_approve_and_post(
                invoice, reason=f"ap_decision_approve"
            )

        if ap_decision.recommendation == "reject":
            logger.info(
                "AP decision reject for %s: %s",
                invoice.gmail_id, ap_decision.reasoning[:80],
            )
            return await self._send_for_approval(
                invoice,
                extra_context={
                    "ap_decision": "reject",
                    "ap_reasoning": ap_decision.reasoning,
                    "risk_flags": ap_decision.risk_flags,
                    "erp_preflight": validation_gate.get("erp_preflight") if isinstance(validation_gate, dict) else None,
                },
            )

        # escalate or unrecognised recommendation → send for human approval
        return await self._send_for_approval(
            invoice,
            extra_context={
                "ap_decision": ap_decision.recommendation,
                "ap_reasoning": ap_decision.reasoning,
                "risk_flags": ap_decision.risk_flags,
                "erp_preflight": validation_gate.get("erp_preflight") if isinstance(validation_gate, dict) else None,
            },
        )
    
    async def _auto_approve_and_post(
        self, 
        invoice: InvoiceData, 
        reason: str = "high_confidence",
    ) -> Dict[str, Any]:
        """Auto-approve invoice and post to ERP."""
        existing = self.db.get_invoice_status(invoice.gmail_id)
        existing_state = self._canonical_invoice_state(existing)
        if existing_state in {"posted_to_erp", "closed"}:
            return {
                "status": "already_posted",
                "invoice_id": invoice.gmail_id,
                "erp_bill_id": (existing or {}).get("erp_bill_id") or (existing or {}).get("erp_reference"),
            }
        if existing and (existing.get("erp_reference") or existing.get("erp_bill_id")):
            return {
                "status": "already_posted",
                "invoice_id": invoice.gmail_id,
                "erp_bill_id": existing.get("erp_bill_id") or existing.get("erp_reference"),
            }

        ap_item_id = self._lookup_ap_item_id(
            gmail_id=invoice.gmail_id,
            vendor_name=invoice.vendor_name,
            invoice_number=invoice.invoice_number,
        )
        correlation_id = self._ensure_ap_item_correlation_id(
            ap_item_id=ap_item_id,
            gmail_id=invoice.gmail_id,
            preferred=invoice.correlation_id,
        )
        invoice.correlation_id = correlation_id

        # Canonical AP path for auto-approval:
        # validated -> needs_approval -> approved -> ready_to_post
        approved_by = f"clearledgr-auto:{reason}"
        approved_at = datetime.now(timezone.utc).isoformat()
        current_state = existing_state or self._canonical_invoice_state(self.db.get_invoice_status(invoice.gmail_id))

        if current_state == "received":
            self._transition_invoice_state(invoice.gmail_id, "validated", correlation_id=correlation_id)
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(invoice.gmail_id))
        if current_state == "validated":
            self._transition_invoice_state(invoice.gmail_id, "needs_approval", correlation_id=correlation_id)
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(invoice.gmail_id))
        if current_state in {"needs_approval", "approved"}:
            self._transition_invoice_state(
                gmail_id=invoice.gmail_id,
                target_state="approved",
                correlation_id=correlation_id,
                approved_by=approved_by,
                approved_at=approved_at,
            )
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(invoice.gmail_id))
        if current_state in {"approved", "ready_to_post"}:
            self._transition_invoice_state(invoice.gmail_id, "ready_to_post", correlation_id=correlation_id)
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(invoice.gmail_id))
        if current_state not in {"ready_to_post"}:
            return {
                "status": "error",
                "invoice_id": invoice.gmail_id,
                "reason": f"invalid_state_for_auto_post:{current_state or 'unknown'}",
            }
        
        # Post to ERP
        result = await self._post_to_erp(invoice, correlation_id=correlation_id)
        post_attempted_at = datetime.now(timezone.utc).isoformat()
        
        if result.get("status") == "success":
            erp_reference = (
                result.get("erp_reference")
                or result.get("bill_id")
                or result.get("reference_id")
                or result.get("doc_num")
            )

            # Post-posting verification: confirm bill actually persisted in ERP
            post_verified = True  # default to trust if verification unavailable
            try:
                from clearledgr.integrations.erp_router import verify_bill_posted
                verification = await verify_bill_posted(
                    organization_id=self.organization_id,
                    invoice_number=invoice.invoice_number,
                    expected_amount=invoice.amount,
                )
                post_verified = verification.get("verified", True)
                if not post_verified:
                    logger.warning(
                        "Post-posting verification failed for %s: %s",
                        invoice.invoice_number,
                        verification.get("reason"),
                    )
            except Exception as ver_exc:
                logger.warning("Post-posting verification error (non-fatal): %s", ver_exc)

            self._transition_invoice_state(
                gmail_id=invoice.gmail_id,
                target_state="posted_to_erp",
                correlation_id=correlation_id,
                erp_reference=erp_reference,
                erp_posted_at=post_attempted_at,
                post_attempted_at=post_attempted_at,
                last_error=None,
            )

            # Store verification result in metadata
            if not post_verified:
                ap_id = self._lookup_ap_item_id(
                    gmail_id=invoice.gmail_id,
                    vendor_name=invoice.vendor_name,
                    invoice_number=invoice.invoice_number,
                )
                if ap_id:
                    self._update_ap_item_metadata(ap_id, {"post_verified": False})
            
            # Audit: Log auto-approval + ERP posting
            try:
                trail = get_audit_trail(self.organization_id)
                trail.log_approval(
                    invoice_id=invoice.gmail_id,
                    approved_by=f"clearledgr-auto:{reason}",
                    comment=f"Auto-approved and posted to ERP (ref: {erp_reference})",
                )
            except Exception as audit_exc:
                logger.debug("Audit trail log_approval failed (non-fatal): %s", audit_exc)

            # LEARNING: Record auto-approval to learn vendor→GL mappings
            try:
                learning = get_learning_service(self.organization_id)
                learning.record_approval(
                    vendor=invoice.vendor_name,
                    gl_code=result.get("gl_code", ""),
                    gl_description=result.get("gl_description", "Accounts Payable"),
                    amount=invoice.amount,
                    currency=invoice.currency,
                    was_auto_approved=True,
                    was_corrected=False,
                )
                logger.info(f"Recorded auto-approval for learning: {invoice.vendor_name}")
            except Exception as e:
                logger.warning(f"Failed to record auto-approval for learning: {e}")

            # VENDOR INTELLIGENCE: Update vendor profile from this outcome
            try:
                ap_item_id = self._lookup_ap_item_id(invoice.gmail_id)
                agent_rec = (invoice.vendor_intelligence or {}).get("ap_decision")
                if hasattr(self.db, "update_vendor_profile_from_outcome") and ap_item_id:
                    self.db.update_vendor_profile_from_outcome(
                        self.organization_id,
                        invoice.vendor_name,
                        ap_item_id=ap_item_id,
                        final_state="posted_to_erp",
                        was_approved=True,
                        approval_override=False,
                        agent_recommendation=str(agent_rec or "approve"),
                        human_decision=None,
                        amount=invoice.amount,
                        invoice_date=invoice.due_date,
                    )
            except Exception as exc:
                logger.error("[VendorStore] Failed to update vendor profile after auto-post: %s", exc)
            
            # Notify in Slack (informational, not approval)
            try:
                await self._send_posted_notification(invoice, result, reason)
            except Exception as e:
                logger.warning(f"Failed to send Slack notification: {e}")

            # M1: Transition posted_to_erp → closed (terminal state).
            # All post-processing (learning, vendor profile, notifications) is
            # complete — the AP item lifecycle is finished.
            try:
                self._transition_invoice_state(
                    gmail_id=invoice.gmail_id,
                    target_state="closed",
                    correlation_id=correlation_id,
                )
            except Exception as close_exc:
                logger.warning("Failed to transition to closed: %s", close_exc)
        else:
            failure_reason = (
                str(result.get("error_message") or "")
                or str(result.get("reason") or "")
                or str(result.get("status") or "")
                or "erp_post_failed"
            )
            self._transition_invoice_state(
                gmail_id=invoice.gmail_id,
                target_state="failed_post",
                correlation_id=correlation_id,
                post_attempted_at=post_attempted_at,
                last_error=failure_reason,
            )
        
        return {
            "status": "auto_approved" if result.get("status") == "success" else "error",
            "invoice_id": invoice.gmail_id,
            "reason": reason,
            "erp_result": result,
        }
    
    async def _send_for_approval(
        self, 
        invoice: InvoiceData,
        extra_context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Send invoice to Slack for approval."""
        budget_checks = self._get_invoice_budget_checks(invoice)
        budget_summary = self._compute_budget_summary(budget_checks)
        context_payload = dict(extra_context or {})
        if "budget" not in context_payload:
            context_payload["budget"] = budget_summary
        if "budget_impact" not in context_payload:
            context_payload["budget_impact"] = budget_checks
        context_payload["approval_context"] = self._build_approval_context(
            invoice=invoice,
            context_payload=context_payload,
        )
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=invoice.gmail_id,
            vendor_name=invoice.vendor_name,
            invoice_number=invoice.invoice_number,
        )

        existing_thread = self.db.get_slack_thread(invoice.gmail_id)
        if existing_thread:
            # Ensure status is pending, but avoid duplicate Slack messages
            self._transition_invoice_state(
                gmail_id=invoice.gmail_id,
                target_state="needs_approval",
                slack_thread_id=existing_thread.get("thread_id") or existing_thread.get("thread_ts"),
            )
            self._record_approval_snapshot(
                ap_item_id=ap_item_id,
                gmail_id=invoice.gmail_id,
                channel_id=existing_thread.get("channel_id"),
                message_ts=existing_thread.get("thread_ts"),
                source_channel="slack",
                source_message_ref=invoice.gmail_id,
                status="pending",
                decision_payload={
                    "budget": budget_summary,
                    "budget_impact": budget_checks,
                    "validation_gate": context_payload.get("validation_gate"),
                    "approval_context": context_payload.get("approval_context"),
                },
            )
            teams_status = self._send_teams_budget_card(invoice, budget_summary, context_payload)
            if isinstance(teams_status, dict):
                teams_state = str(teams_status.get("status") or "unknown")
                self._update_ap_item_metadata(
                    ap_item_id,
                    {
                        "teams": {
                            "state": teams_state,
                            "channel": teams_status.get("channel_id"),
                            "message_id": teams_status.get("message_id"),
                            "reason": teams_status.get("reason"),
                        }
                    },
                )
                if teams_state == "sent":
                    self._record_approval_snapshot(
                        ap_item_id=ap_item_id,
                        gmail_id=invoice.gmail_id,
                        channel_id=str(teams_status.get("channel_id") or "teams"),
                        message_ts=str(teams_status.get("message_id") or invoice.gmail_id),
                        source_channel="teams",
                        source_message_ref=invoice.gmail_id,
                        status="pending",
                        decision_payload={
                            "budget": budget_summary,
                            "budget_impact": budget_checks,
                            "validation_gate": context_payload.get("validation_gate"),
                            "approval_context": context_payload.get("approval_context"),
                        },
                    )
            return {
                "status": "pending_approval",
                "invoice_id": invoice.gmail_id,
                "slack_channel": existing_thread.get("channel_id"),
                "slack_ts": existing_thread.get("thread_ts"),
                "existing": True,
                "budget": budget_summary,
                "teams": teams_status,
            }

        # Update status to pending
        self._transition_invoice_state(
            gmail_id=invoice.gmail_id,
            target_state="needs_approval",
        )

        # Create approval chain record for audit and multi-step tracking
        chain_id = None
        try:
            from types import SimpleNamespace
            chain_id = f"chain-{uuid.uuid4().hex[:12]}"
            chain = SimpleNamespace(
                chain_id=chain_id,
                organization_id=self.organization_id,
                invoice_id=invoice.gmail_id,
                vendor_name=invoice.vendor_name,
                amount=invoice.amount,
                gl_code=None,
                department=None,
                status="pending",
                current_step=0,
                requester_id="ap_agent",
                requester_name="Clearledgr AP Agent",
                created_at=datetime.now(timezone.utc),
                completed_at=None,
                steps=[SimpleNamespace(
                    step_id=f"step-{uuid.uuid4().hex[:12]}",
                    level="L1",
                    approvers=[],
                    approval_type="any",
                    status="pending",
                    approved_by=None,
                    approved_at=None,
                    rejection_reason=None,
                    comments="",
                )],
            )
            self.db.db_create_approval_chain(chain)
            self._update_ap_item_metadata(ap_item_id, {"approval_chain_id": chain_id})
        except Exception as chain_exc:
            logger.debug("Approval chain creation failed (non-fatal): %s", chain_exc)
            chain_id = None

        # Build approval message
        blocks = self._build_approval_blocks(invoice, context_payload)
        
        # Get appropriate channel based on amount
        approval_channel = self.get_approval_channel_for_amount(invoice.amount)
        
        try:
            # Send to Slack
            message = await self.slack_client.send_message(
                channel=approval_channel,
                text=f"Invoice approval needed: {invoice.vendor_name} - ${invoice.amount:,.2f}",
                blocks=blocks,
            )
            
            # Save Slack thread reference
            thread_id = self.db.save_slack_thread(
                invoice_id=invoice.gmail_id,
                channel_id=message.channel,
                thread_ts=message.ts,
                gmail_id=invoice.gmail_id,
                organization_id=self.organization_id,
            )
            
            # Update invoice with thread reference
            self._transition_invoice_state(
                gmail_id=invoice.gmail_id,
                target_state="needs_approval",
                slack_thread_id=thread_id,
            )
            self._record_approval_snapshot(
                ap_item_id=ap_item_id,
                gmail_id=invoice.gmail_id,
                channel_id=message.channel,
                message_ts=message.ts,
                source_channel="slack",
                source_message_ref=invoice.gmail_id,
                status="pending",
                decision_payload={
                    "budget": budget_summary,
                    "budget_impact": budget_checks,
                    "validation_gate": context_payload.get("validation_gate"),
                    "approval_context": context_payload.get("approval_context"),
                },
            )
            teams_status = self._send_teams_budget_card(invoice, budget_summary, context_payload)
            if isinstance(teams_status, dict):
                teams_state = str(teams_status.get("status") or "unknown")
                self._update_ap_item_metadata(
                    ap_item_id,
                    {
                        "teams": {
                            "state": teams_state,
                            "channel": teams_status.get("channel_id"),
                            "message_id": teams_status.get("message_id"),
                            "reason": teams_status.get("reason"),
                        }
                    },
                )
                if teams_state == "sent":
                    self._record_approval_snapshot(
                        ap_item_id=ap_item_id,
                        gmail_id=invoice.gmail_id,
                        channel_id=str(teams_status.get("channel_id") or "teams"),
                        message_ts=str(teams_status.get("message_id") or invoice.gmail_id),
                        source_channel="teams",
                        source_message_ref=invoice.gmail_id,
                        status="pending",
                        decision_payload={
                            "budget": budget_summary,
                            "budget_impact": budget_checks,
                            "validation_gate": context_payload.get("validation_gate"),
                            "approval_context": context_payload.get("approval_context"),
                        },
                    )
            
            logger.info(f"Sent approval request to Slack: {message.ts}")

            # Audit: Log approval request to audit trail
            try:
                trail = get_audit_trail(self.organization_id)
                from clearledgr.services.audit_trail import AuditEventType
                trail.log(
                    invoice_id=invoice.gmail_id,
                    event_type=AuditEventType.APPROVAL_REQUESTED,
                    summary=f"Sent for approval: {invoice.vendor_name} ${invoice.amount:,.2f}",
                    details={"channel": message.channel, "ap_decision": (extra_context or {}).get("ap_decision")},
                )
            except Exception as audit_exc:
                logger.debug("Audit trail approval_requested failed (non-fatal): %s", audit_exc)

            # H4: Audit approval request dispatch (PLAN.md §4.7)
            if ap_item_id:
                channels_notified = ["slack"]
                if isinstance(teams_status, dict) and teams_status.get("status") == "sent":
                    channels_notified.append("teams")
                try:
                    self.db.append_ap_audit_event(
                        {
                            "ap_item_id": ap_item_id,
                            "event_type": "approval_requested",
                            "actor_type": "system",
                            "actor_id": "invoice_workflow",
                            "reason": f"Approval request sent to {', '.join(channels_notified)}",
                            "metadata": {
                                "channels": channels_notified,
                                "slack_channel": message.channel,
                                "slack_ts": message.ts,
                                "vendor": invoice.vendor_name,
                                "amount": invoice.amount,
                            },
                            "organization_id": self.organization_id,
                            "source": "invoice_workflow",
                        }
                    )
                except Exception:
                    pass  # Non-fatal

            return {
                "status": "pending_approval",
                "invoice_id": invoice.gmail_id,
                "slack_channel": message.channel,
                "slack_ts": message.ts,
                "budget": budget_summary,
                "teams": teams_status,
            }
            
        except Exception as e:
            logger.error(f"Failed to send Slack approval: {e}")
            return {
                "status": "error",
                "invoice_id": invoice.gmail_id,
                "error": str(e),
            }

    def _send_teams_budget_card(
        self,
        invoice: InvoiceData,
        budget_summary: Dict[str, Any],
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Best-effort Teams delivery for approval/budget decisions."""
        client = self.teams_client
        if client is None:
            return {"status": "skipped", "reason": "teams_client_unavailable"}
        try:
            approval_copy = self._build_approval_surface_copy(
                invoice=invoice,
                extra_context=extra_context or {"budget": budget_summary},
                budget_summary=budget_summary,
            )
            result = client.send_invoice_budget_card(
                email_id=invoice.gmail_id,
                organization_id=self.organization_id,
                vendor=invoice.vendor_name,
                amount=invoice.amount,
                currency=invoice.currency,
                invoice_number=invoice.invoice_number,
                budget=budget_summary,
                decision_reason_summary=approval_copy.get("why_summary"),
                next_step_lines=(
                    ([f"Recommended now: {approval_copy.get('recommended_action_text')}"] if approval_copy.get("recommended_action_text") else [])
                    + (approval_copy.get("what_happens_next") or [])
                ),
                requested_by_text=approval_copy.get("requested_by_text"),
                source_of_truth_text=approval_copy.get("source_of_truth_text"),
                source_url=approval_copy.get("gmail_url"),
            )
            if isinstance(result, dict):
                return result
            return {"status": "error", "reason": "invalid_teams_response"}
        except Exception as exc:
            logger.warning("Failed to send Teams approval card: %s", exc)
            return {"status": "error", "reason": str(exc)}

    def _build_approval_context(
        self,
        invoice: InvoiceData,
        context_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build compact cross-system context for approval surfaces."""
        summary: Dict[str, Any] = {
            "vendor_name": invoice.vendor_name,
            "vendor_spend_to_date": 0.0,
            "vendor_open_invoices": 0,
            "connected_systems": [],
            "source_count": 0,
        }
        try:
            if hasattr(self.db, "list_ap_items"):
                items = self.db.list_ap_items(self.organization_id, limit=5000)
                vendor_key = str(invoice.vendor_name or "").strip().lower()
                if vendor_key:
                    vendor_items = [
                        item
                        for item in items
                        if str(item.get("vendor_name") or "").strip().lower() == vendor_key
                    ]
                    summary["vendor_spend_to_date"] = round(
                        sum(float(item.get("amount") or 0) for item in vendor_items),
                        2,
                    )
                    summary["vendor_open_invoices"] = sum(
                        1
                        for item in vendor_items
                        if str(item.get("state") or "").strip().lower()
                        in {
                            "received",
                            "validated",
                            "needs_info",
                            "needs_approval",
                            "pending_approval",
                            "approved",
                            "ready_to_post",
                        }
                    )
        except Exception as e:
            # Approval flow must not fail due to optional context derivation.
            logger.warning("Optional context derivation failed: %s", e)

        multi_system = context_payload.get("multi_system")
        if isinstance(multi_system, dict):
            connected = multi_system.get("connected_systems")
            if isinstance(connected, list):
                summary["connected_systems"] = [str(system) for system in connected if str(system).strip()]

        email_context = context_payload.get("email")
        if isinstance(email_context, dict):
            try:
                summary["source_count"] = int(email_context.get("source_count") or 0)
            except (TypeError, ValueError):
                summary["source_count"] = 0
        return summary

    @staticmethod
    def _humanize_reason_code(code: Any) -> str:
        return humanize_reason_code(code)

    @staticmethod
    def _dedupe_reason_lines(lines: List[str], limit: int = 3) -> List[str]:
        return dedupe_reason_lines(lines, limit)

    def _build_approval_surface_copy(
        self,
        invoice: InvoiceData,
        extra_context: Optional[Dict[str, Any]] = None,
        budget_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return build_approval_surface_copy(invoice, extra_context, budget_summary)
    
    def _build_approval_blocks(
        self,
        invoice: InvoiceData,
        extra_context: Optional[Dict] = None,
    ) -> list:
        return build_approval_blocks(invoice, extra_context)
    
def get_invoice_workflow(
    organization_id: str,
    slack_channel: Optional[str] = None,
) -> InvoiceWorkflowService:
    """Get the internal workflow service used by runtime-owned AP actions."""
    return InvoiceWorkflowService(
        organization_id=organization_id,
        slack_channel=slack_channel,
    )
