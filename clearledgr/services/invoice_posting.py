"""
Invoice Posting Mixin

Extracted from InvoiceWorkflowService to separate posting/human-action logic
from the core workflow orchestration.

All methods use self.db, self.organization_id, self.slack_client, self.teams_client,
self.slack_channel, self._observer_registry, etc. — these are set in
InvoiceWorkflowService.__init__ and resolve via self at runtime (standard mixin pattern).
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta

from clearledgr.core.ap_states import (
    APState,
    OverrideContext,
    classify_post_failure_recoverability,
)
from clearledgr.integrations.erp_router import (
    Bill, Vendor, get_or_create_vendor,
)
from clearledgr.services.erp_api_first import post_bill_api_first
from clearledgr.services.finance_learning import get_finance_learning_service
from clearledgr.services.budget_awareness import get_budget_awareness
from clearledgr.services.invoice_models import InvoiceData

logger = logging.getLogger(__name__)

# Backward-compatible import alias for older tests/monkeypatch targets.
get_learning_service = get_finance_learning_service


class InvoicePostingMixin:
    """Mixin providing posting/human-action methods for InvoiceWorkflowService."""

    @staticmethod
    def _normalize_human_actor(
        actor_id: Optional[str],
        *,
        actor_display: Optional[str] = None,
        actor_email: Optional[str] = None,
        actor_platform_id: Optional[str] = None,
        actor_identity: Optional[Dict[str, Any]] = None,
        platform: Optional[str] = None,
    ) -> Dict[str, Any]:
        raw_identity = actor_identity if isinstance(actor_identity, dict) else {}
        normalized_email = str(raw_identity.get("email") or actor_email or "").strip() or None
        normalized_display = str(raw_identity.get("display_name") or actor_display or "").strip() or None
        normalized_platform_id = (
            str(raw_identity.get("platform_user_id") or actor_platform_id or "").strip() or None
        )
        normalized_platform = str(raw_identity.get("platform") or platform or "").strip().lower() or None
        raw_actor = str(actor_id or "").strip() or None

        canonical_actor = normalized_email or raw_actor or normalized_platform_id or "unknown_actor"
        if normalized_display and normalized_email and normalized_display.lower() != normalized_email.lower():
            label = f"{normalized_display} ({normalized_email})"
        elif normalized_display:
            label = normalized_display
        elif normalized_email:
            label = normalized_email
        elif normalized_platform_id:
            label = normalized_platform_id
        else:
            label = canonical_actor

        return {
            "canonical_actor": canonical_actor,
            "label": label,
            "identity": {
                "platform": normalized_platform,
                "platform_user_id": normalized_platform_id,
                "email": normalized_email,
                "display_name": normalized_display,
            },
        }

    async def approve_invoice(
        self,
        gmail_id: str,
        approved_by: str,
        slack_channel: Optional[str] = None,
        slack_ts: Optional[str] = None,
        source_channel: str = "slack",
        source_channel_id: Optional[str] = None,
        source_message_ref: Optional[str] = None,
        actor_display: Optional[str] = None,
        actor_email: Optional[str] = None,
        actor_platform_id: Optional[str] = None,
        actor_identity: Optional[Dict[str, Any]] = None,
        action_run_id: Optional[str] = None,
        decision_request_ts: Optional[str] = None,
        decision_idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
        allow_budget_override: bool = False,
        override_justification: Optional[str] = None,
        allow_confidence_override: bool = False,
        field_confidences: Optional[Dict[str, Any]] = None,
        allow_po_exception_override: bool = False,
        po_override_reason: Optional[str] = None,
        override_context: Optional["OverrideContext"] = None,  # structured override metadata
    ) -> Dict[str, Any]:
        """
        Approve an invoice and post to ERP.

        Called when user clicks Approve in Slack or Gmail extension.
        """
        # --- L7: lightweight input validation at service boundary ---
        if not str(gmail_id or "").strip():
            return {"status": "error", "reason": "missing_gmail_id"}
        if not str(approved_by or "").strip():
            return {"status": "error", "reason": "missing_approved_by"}

        # Get invoice data
        invoice_data = self.db.get_invoice_status(gmail_id)
        if not invoice_data:
            return {"status": "error", "reason": "Invoice not found"}

        resolved_source_channel = str(source_channel or "slack").strip().lower() or "slack"
        resolved_channel_id = source_channel_id or slack_channel
        resolved_message_ref = source_message_ref or slack_ts
        approver = self._normalize_human_actor(
            approved_by,
            actor_display=actor_display,
            actor_email=actor_email,
            actor_platform_id=actor_platform_id,
            actor_identity=actor_identity,
            platform=resolved_source_channel,
        )
        approved_by = approver["canonical_actor"]
        approved_by_label = approver["label"]
        actor_identity = approver["identity"]
        actor_display = actor_identity.get("display_name")
        actor_email = actor_identity.get("email")
        actor_platform_id = actor_identity.get("platform_user_id")
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=gmail_id,
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name"),
            invoice_number=invoice_data.get("invoice_number"),
        )

        # Wave 1 / D1 — segregation-of-duties gate. AICPA + IOFM
        # require requester != approver and processor != approver.
        # Default mode is enforced; tenants opt into ``warn`` for a
        # soft-fail signal or ``disabled`` for legacy behaviour. SOC
        # 2 controls expect this; current code path silently allowed
        # an AP Clerk to both edit and approve their own bills.
        if ap_item_id:
            from clearledgr.services.sod_check import check_sod

            approver_user_id_resolved: Optional[str] = None
            try:
                from clearledgr.core.auth import get_user_by_email
                if actor_email:
                    user_row = get_user_by_email(actor_email)
                    if user_row:
                        approver_user_id_resolved = str(getattr(user_row, "id", "") or "")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[approve_invoice] approver user_id lookup failed for %s: %s",
                    actor_email, exc,
                )

            sod_result = check_sod(
                self.db,
                ap_item_id=ap_item_id,
                approver_user_id=approver_user_id_resolved,
                approver_email=actor_email,
                organization_id=self.organization_id,
            )
            try:
                if not sod_result.allowed and sod_result.violation_reason:
                    sod_event_type = "sod_violation_blocked"
                elif sod_result.violation_reason:
                    sod_event_type = "sod_violation_warned"
                else:
                    sod_event_type = "sod_check_passed"
                self.db.append_audit_event({
                    "event_type": sod_event_type,
                    "actor_type": "user",
                    "actor_id": (
                        approver_user_id_resolved
                        or actor_email
                        or approved_by
                    ),
                    "organization_id": self.organization_id,
                    "box_id": ap_item_id,
                    "box_type": "ap_item",
                    "source": "approve_invoice",
                    "payload_json": sod_result.to_dict(),
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[approve_invoice] SOD audit emit failed: %s", exc,
                )

            if not sod_result.allowed:
                return {
                    "status": "error",
                    "invoice_id": gmail_id,
                    "ap_item_id": ap_item_id,
                    "reason": "sod_violation",
                    "violation_reason": sod_result.violation_reason,
                    "message": sod_result.message,
                    "sod": sod_result.to_dict(),
                }

        correlation_id = self._ensure_ap_item_correlation_id(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            preferred=correlation_id,
        )
        existing_decision_snapshot = self._approval_snapshot_by_decision_key(
            ap_item_id,
            decision_idempotency_key,
        )
        if existing_decision_snapshot:
            existing_status = str(existing_decision_snapshot.get("status") or "").strip().lower()
            existing_payload = self._approval_payload_dict(existing_decision_snapshot)
            if existing_status == "approved":
                return {
                    "status": "approved",
                    "invoice_id": gmail_id,
                    "approved_by": approved_by,
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                    "erp_result": existing_payload.get("erp_result") or {},
                    "reason": "duplicate_approval_action",
                }
            if existing_status == "failed":
                return {
                    "status": "error",
                    "invoice_id": gmail_id,
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                    "reason": "duplicate_approval_action",
                    "erp_result": existing_payload.get("erp_result") or {},
                }
            if existing_status == "processing":
                return {
                    "status": "duplicate_in_progress",
                    "invoice_id": gmail_id,
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                    "reason": "duplicate_approval_action_in_progress",
                }

        invoice_state = self._canonical_invoice_state(invoice_data)
        if invoice_state in {"posted_to_erp", "closed"}:
            return {"status": "error", "reason": "Invoice already posted"}
        if invoice_data.get("erp_bill_id") or invoice_data.get("erp_reference"):
            return {"status": "error", "reason": "Invoice already posted"}

        field_review_gate = self.evaluate_financial_action_field_review_gate(
            invoice_data,
            field_confidences_override=field_confidences,
        )
        confidence_gate = field_review_gate.get("confidence_gate") or {}
        confidence_blockers = field_review_gate.get("confidence_blockers") or []

        # Persist per-field confidences to the AP item row so accuracy trends
        # are queryable without re-parsing audit events.
        if ap_item_id:
            gate_field_confidences = confidence_gate.get("field_confidences") or {}
            if gate_field_confidences:
                try:
                    self.db.update_ap_item(
                        ap_item_id,
                        field_confidences=json.dumps(gate_field_confidences),
                        _actor_type="system",
                        _actor_id="confidence_gate",
                    )
                except Exception as _fc_err:
                    logger.warning("field_confidences persist failed: %s", _fc_err)

        if field_review_gate.get("blocked"):
            self._persist_financial_action_field_review_gate(ap_item_id, field_review_gate)
            return {
                "status": "blocked",
                "invoice_id": gmail_id,
                "reason": "field_review_required",
                "detail": field_review_gate.get("detail"),
                "requires_field_review": True,
                "confidence_gate": confidence_gate,
                "confidence_blockers": confidence_blockers,
                "source_conflicts": field_review_gate.get("source_conflicts") or [],
                "blocking_source_conflicts": field_review_gate.get("blocking_source_conflicts") or [],
                "blocked_fields": field_review_gate.get("blocked_fields") or [],
                "exception_code": field_review_gate.get("exception_code"),
                "options": [
                    "review_fields",
                    "reject",
                ],
            }

        budget_checks = self._load_budget_context_from_invoice_row(invoice_data)
        budget_summary = self._compute_budget_summary(budget_checks)

        # Hard block: budget exceeded cannot be overridden with justification alone
        if budget_summary.get("hard_block"):
            return {
                "status": "needs_budget_decision",
                "invoice_id": gmail_id,
                "reason": "budget_exceeded_hard_block",
                "budget": budget_summary,
                "options": [
                    "request_budget_adjustment",
                    "reject_over_budget",
                ],
            }
        if budget_summary.get("requires_decision") and not allow_budget_override:
            return {
                "status": "needs_budget_decision",
                "invoice_id": gmail_id,
                "reason": "budget_requires_decision",
                "budget": budget_summary,
                "options": [
                    "approve_override_with_justification",
                    "request_budget_adjustment",
                    "reject_over_budget",
                ],
            }
        if allow_budget_override and budget_summary.get("requires_decision"):
            if not str(override_justification or "").strip():
                return {
                    "status": "error",
                    "invoice_id": gmail_id,
                    "reason": "budget_override_requires_justification",
                }

        # PO exception blocking: check for unresolved high-severity PO exceptions
        po_block = self._check_po_exception_block(invoice_data)
        if po_block.get("blocked") and not allow_po_exception_override:
            return {
                "status": "needs_po_resolution",
                "invoice_id": gmail_id,
                "reason": "po_exceptions_require_resolution",
                "po_exceptions": po_block.get("exceptions", []),
                "options": [
                    "override_with_reason",
                    "resolve_exceptions",
                    "reject",
                ],
            }
        if allow_po_exception_override and po_block.get("blocked"):
            if not str(po_override_reason or "").strip():
                return {
                    "status": "error",
                    "invoice_id": gmail_id,
                    "reason": "po_override_requires_reason",
                }

        budget_override_used = bool(allow_budget_override and budget_summary.get("requires_decision"))
        po_override_used = bool(allow_po_exception_override and po_block.get("blocked"))
        decision_type = "approve_override" if (budget_override_used or po_override_used) else "approve"

        if decision_idempotency_key and not self._acquire_decision_action_lock(
            ap_item_id=ap_item_id,
            decision_idempotency_key=decision_idempotency_key,
            actor_id=approved_by,
            source_channel=resolved_source_channel,
            correlation_id=correlation_id,
            metadata={
                "gmail_id": gmail_id,
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "source_message_ref": resolved_message_ref,
            },
        ):
            existing_decision_snapshot = self._approval_snapshot_by_decision_key(ap_item_id, decision_idempotency_key)
            if existing_decision_snapshot:
                existing_status = str(existing_decision_snapshot.get("status") or "").strip().lower()
                existing_payload = self._approval_payload_dict(existing_decision_snapshot)
                if existing_status == "approved":
                    return {
                        "status": "approved",
                        "invoice_id": gmail_id,
                        "approved_by": approved_by,
                        "duplicate_action": True,
                        "decision_idempotency_key": decision_idempotency_key,
                        "erp_result": existing_payload.get("erp_result") or {},
                        "reason": "duplicate_approval_action",
                    }
                if existing_status == "failed":
                    return {
                        "status": "error",
                        "invoice_id": gmail_id,
                        "duplicate_action": True,
                        "decision_idempotency_key": decision_idempotency_key,
                        "reason": "duplicate_approval_action",
                        "erp_result": existing_payload.get("erp_result") or {},
                    }
            return {
                "status": "duplicate_in_progress",
                "invoice_id": gmail_id,
                "duplicate_action": True,
                "decision_idempotency_key": decision_idempotency_key,
                "reason": "duplicate_approval_action_in_progress",
            }

        approved_at = datetime.now(timezone.utc).isoformat()
        current_state = invoice_state
        if current_state == "received":
            self._transition_invoice_state(gmail_id, "validated", correlation_id=correlation_id)
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(gmail_id))
        if current_state == "validated":
            self._transition_invoice_state(gmail_id, "needs_approval", correlation_id=correlation_id)
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(gmail_id))
        if current_state in {"needs_approval", "approved"}:
            self._transition_invoice_state(
                gmail_id=gmail_id,
                target_state="approved",
                correlation_id=correlation_id,
                approved_by=approved_by,
                approved_at=approved_at,
            )
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(gmail_id))
        if current_state not in {"approved", "ready_to_post"}:
            return {"status": "error", "reason": f"invalid_state_for_post:{current_state or 'unknown'}"}
        self._transition_invoice_state(gmail_id, "ready_to_post", correlation_id=correlation_id)

        # Build invoice object for ERP
        _approve_meta = self._parse_metadata_dict((self.db.get_ap_item(ap_item_id) or {}).get("metadata")) if ap_item_id else {}
        invoice = InvoiceData(
            gmail_id=gmail_id,
            subject=invoice_data.get("email_subject", ""),
            sender="",
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name") or "Unknown",
            amount=invoice_data.get("amount", 0),
            currency=invoice_data.get("currency", "USD"),
            invoice_number=invoice_data.get("invoice_number"),
            due_date=invoice_data.get("due_date"),
            organization_id=self.organization_id,
            invoice_text=invoice_data.get("email_body", ""),  # For discount detection
            budget_impact=budget_checks,
            line_items=_approve_meta.get("line_items") if isinstance(_approve_meta.get("line_items"), list) else None,
        )
        if isinstance(field_confidences, dict) and field_confidences:
            self._update_ap_item_metadata(ap_item_id, {"field_confidences": field_confidences})

        # Compose the reason the human gave for approving. Multiple
        # gates can fire on one invoice (PO exception + budget); join
        # them so the audit event carries the full justification rather
        # than just whichever was passed first.
        _approve_reason_parts: List[str] = []
        if po_override_reason and str(po_override_reason).strip():
            _approve_reason_parts.append(f"po_override: {po_override_reason}")
        if override_justification and str(override_justification).strip():
            _approve_reason_parts.append(f"justification: {override_justification}")
        _approve_reason = " | ".join(_approve_reason_parts) if _approve_reason_parts else None

        _override_ctx_dict: Optional[Dict[str, Any]] = None
        if override_context is not None:
            try:
                from dataclasses import asdict, is_dataclass
                _override_ctx_dict = asdict(override_context) if is_dataclass(override_context) else dict(override_context)  # type: ignore[arg-type]
            except Exception:
                _override_ctx_dict = None

        self._maybe_record_ap_decision_override(
            ap_item_id, "approved", approved_by,
            correlation_id=correlation_id,
            human_reason=_approve_reason,
            override_context=_override_ctx_dict,
        )
        self._record_approval_snapshot(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            channel_id=resolved_channel_id,
            message_ts=resolved_message_ref,
            source_channel=resolved_source_channel,
            source_message_ref=gmail_id,
            status="processing",
            decision_idempotency_key=decision_idempotency_key,
            decision_payload={
                "decision": decision_type,
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "actor_display": actor_display,
                "actor_email": actor_email,
                "actor_platform_id": actor_platform_id,
                "actor_identity": actor_identity,
                "actor_label": approved_by_label,
                "source_channel": resolved_source_channel,
                "source_message_ref": resolved_message_ref,
            },
            approved_by=approved_by,
            approved_at=approved_at,
        )

        # Approval-chain gate — tripwire for future multi-step chains.
        #
        # Today, invoice_workflow.py creates single-step chains with
        # approval_type="any" so the first authorized click both
        # completes the step and releases posting. That's fine, and
        # the Slack/Teams precedence contract already enforces the
        # "only authorized approvers can click" rule upstream.
        #
        # The shape of ApprovalChain, ApprovalStep, and
        # db_check_parallel_chain_complete already supports N steps,
        # though. If a future feature creates a multi-step chain
        # (L1 + L2 + L3 sign-offs, say) and approve_invoice keeps
        # its current "mark step 0, mark whole chain approved, post"
        # behaviour, every additional approver gets silently bypassed
        # and a single L1 click posts the bill. That's a compliance
        # bug waiting to happen — SoC2 and most finance policy
        # frameworks mandate the full chain.
        #
        # Fix: find the step containing this actor, mark it approved,
        # then ask db_check_parallel_chain_complete. If ANY step is
        # still pending, skip the ERP post and return
        # awaiting_additional_approvers. Single-step chains collapse
        # to one-step-complete-therefore-proceed in zero extra DB
        # calls, so no behaviour change today.
        try:
            if ap_item_id and hasattr(self.db, "db_get_chain_by_invoice"):
                chain = self.db.db_get_chain_by_invoice(self.organization_id, gmail_id)
                if chain and str(chain.get("status") or "").lower() == "pending":
                    steps = chain.get("steps") or []
                    actor_lookup = (actor_email or approved_by or "").strip().lower()
                    matched_step_idx: Optional[int] = None
                    for idx, step in enumerate(steps):
                        if str(step.get("status") or "").lower() != "pending":
                            continue
                        approvers = step.get("approvers") or []
                        if isinstance(approvers, str):
                            try:
                                approvers = json.loads(approvers)
                            except Exception:
                                approvers = []
                        approvers_normalized = {
                            str(a).strip().lower() for a in approvers if str(a).strip()
                        }
                        if not approvers_normalized or actor_lookup in approvers_normalized:
                            matched_step_idx = idx
                            break
                    if matched_step_idx is not None:
                        step_complete_at = datetime.now(timezone.utc).isoformat()
                        self.db.db_update_chain_step(
                            chain["id"], matched_step_idx,
                            status="approved",
                            approved_by=approved_by,
                            approved_at=step_complete_at,
                        )
                        if hasattr(self.db, "db_check_parallel_chain_complete"):
                            status_check = self.db.db_check_parallel_chain_complete(chain["id"])
                            if not status_check.get("complete"):
                                # Still-pending step(s) on the same chain
                                # — this approver's job is done, but the
                                # chain isn't, so we stop short of the
                                # ERP post and let the remaining
                                # approver(s) finish the job.
                                return {
                                    "status": "awaiting_additional_approvers",
                                    "invoice_id": gmail_id,
                                    "approved_by": approved_by,
                                    "ap_item_id": ap_item_id,
                                    "pending_count": int(status_check.get("pending_count") or 0),
                                    "approved_count": int(status_check.get("approved_count") or 0),
                                    "total_steps": int(status_check.get("total_steps") or 0),
                                }
        except Exception as exc:
            # Chain-gate failure must never silently bypass itself into
            # a post. If we can't determine completion state, treat as
            # "unknown" and refuse the post — operator can retry after
            # checking the chain manually. The alternative (fall through
            # to posting) is a compliance incident class we'd rather
            # surface as a user-facing error than mask.
            logger.error(
                "[approve_invoice] approval-chain gate failed for %s: %s",
                ap_item_id, exc,
            )
            return {
                "status": "error",
                "invoice_id": gmail_id,
                "ap_item_id": ap_item_id,
                "reason": "approval_chain_gate_error",
                "detail": str(exc)[:200],
            }

        # Post to ERP
        if decision_idempotency_key:
            result = await self._post_to_erp(
                invoice,
                idempotency_key=decision_idempotency_key,
                correlation_id=correlation_id,
            )
        else:
            result = await self._post_to_erp(invoice, correlation_id=correlation_id)
        post_attempted_at = datetime.now(timezone.utc).isoformat()

        if result.get("status") == "success":
            erp_reference = (
                result.get("erp_reference")
                or result.get("bill_id")
                or result.get("reference_id")
                or result.get("doc_num")
            )
            # Wave 1 / A2 — auditor-traceable JE id. Each ERP adapter
            # surfaces ``erp_journal_entry_id`` on success (see
            # erp_quickbooks / erp_xero / erp_netsuite / erp_sap). For
            # QBO + NetSuite the JE id coincides with the bill id by
            # the ERP's data model; for SAP B1 + Xero it's a distinct
            # value retrieved from the create response or via a
            # follow-up GET. None on legacy paths is acceptable —
            # the column is optional and back-fill happens via the
            # erp_follow_on_reconciliation pass.
            erp_journal_entry_id = result.get("erp_journal_entry_id")
            self._transition_invoice_state(
                gmail_id=gmail_id,
                target_state="posted_to_erp",
                correlation_id=correlation_id,
                erp_reference=erp_reference,
                erp_journal_entry_id=erp_journal_entry_id,
                erp_posted_at=post_attempted_at,
                post_attempted_at=post_attempted_at,
                last_error=None,
            )

            # LEARNING: Record this approval to learn vendor->GL mappings
            try:
                learning = get_finance_learning_service(self.organization_id, db=self.db)
                learning.record_vendor_gl_approval(
                    vendor=invoice.vendor_name,
                    gl_code=result.get("gl_code", ""),
                    gl_description=result.get("gl_description", "Accounts Payable"),
                    amount=invoice.amount,
                    currency=invoice.currency,
                    was_auto_approved=False,
                    was_corrected=bool(
                        result.get("gl_code")
                        and (invoice.vendor_intelligence or {}).get("suggested_gl")
                        and result.get("gl_code") != (invoice.vendor_intelligence or {}).get("suggested_gl")
                    ),
                    metadata={"source": "invoice_posting.approve_invoice", "correlation_id": correlation_id},
                )
                logger.info(f"Recorded approval for learning: {invoice.vendor_name} → GL {result.get('gl_code')}")
            except Exception as e:
                logger.warning(f"Failed to record approval for learning: {e}")

            # BUDGET: Record spending against applicable budgets
            try:
                budget_service = get_budget_awareness(self.organization_id)
                for check in budget_checks:
                    budget_id = check.get("budget_id") or check.get("budget_name", "").lower().replace(" ", "_")
                    if budget_id:
                        budget_service.record_spending(budget_id, invoice.amount)
                        logger.info("Recorded budget spending: %s += %.2f", budget_id, invoice.amount)
            except Exception as e:
                logger.warning("Failed to record budget spending: %s", e)

            # Update Slack message
            if resolved_source_channel == "slack" and resolved_channel_id and resolved_message_ref:
                await self._update_slack_approved(
                    resolved_channel_id, resolved_message_ref, invoice, approved_by_label, result
                )
            self._record_approval_snapshot(
                ap_item_id=ap_item_id,
                gmail_id=gmail_id,
                channel_id=resolved_channel_id,
                message_ts=resolved_message_ref,
                source_channel=resolved_source_channel,
                source_message_ref=gmail_id,
                status="approved",
                decision_payload={
                    "decision": decision_type,
                    "override_justification": override_justification,
                    "confidence_override": False,
                    "confidence_gate": confidence_gate,
                    "po_override_reason": po_override_reason,
                    "po_exceptions_overridden": po_block.get("exceptions") if po_override_used else None,
                    "budget": budget_summary,
                    "budget_impact": budget_checks,
                    "erp_result": result,
                    "run_id": action_run_id,
                    "request_ts": decision_request_ts,
                    "actor_display": actor_display,
                    "actor_email": actor_email,
                    "actor_platform_id": actor_platform_id,
                    "actor_identity": actor_identity,
                    "actor_label": approved_by_label,
                    "decision_idempotency_key": decision_idempotency_key,
                },
                approved_by=approved_by,
                approved_at=approved_at,
                decision_idempotency_key=decision_idempotency_key,
            )
            self._record_vendor_decision_feedback(
                ap_item_id=ap_item_id,
                vendor_name=invoice.vendor_name,
                human_action="approve",
                actor_id=approved_by,
                source_channel=resolved_source_channel,
                correlation_id=correlation_id,
                reason=override_justification,
                action_outcome="posted_to_erp",
                final_state="posted_to_erp",
                was_approved=True,
                amount=invoice.amount,
                invoice_date=invoice.due_date,
            )

            # Complete approval chain if one exists.
            #
            # The per-step "approved" status was already written by the
            # chain gate above (before _post_to_erp), so all we need
            # to do here is roll the chain-level status to approved so
            # downstream consumers see a terminal chain. No hardcoded
            # step index: the gate writes the actor's matched step,
            # and db_check_parallel_chain_complete confirmed all steps
            # were approved before we got here.
            try:
                chain = self.db.db_get_chain_by_invoice(self.organization_id, gmail_id)
                if chain:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    self.db.db_update_chain_status(
                        chain["id"],
                        status="approved",
                        current_step=len(chain.get("steps") or []) or 0,
                        completed_at=now_iso,
                    )
            except Exception:
                pass  # Non-fatal — chain status is informational at this point

            # Create payment tracking record (informational — never executes payment)
            try:
                from clearledgr.core.database import get_db as _get_db_for_payment
                _pay_db = _get_db_for_payment()
                payment_record = _pay_db.create_payment({
                    "ap_item_id": ap_item_id or gmail_id,
                    "organization_id": self.organization_id,
                    "vendor_name": invoice.vendor_name,
                    "amount": invoice.amount,
                    "currency": invoice.currency,
                    "status": "ready_for_payment",
                    "due_date": getattr(invoice, "due_date", None),
                    "erp_reference": erp_reference,
                })
                # Store payment_id in AP item metadata for cross-reference
                if ap_item_id:
                    try:
                        _existing_meta = self._parse_metadata_dict(
                            (self.db.get_ap_item(ap_item_id) or {}).get("metadata")
                        )
                        _existing_meta["payment_id"] = payment_record["id"]
                        _existing_meta["payment_status"] = "ready_for_payment"
                        self.db.update_ap_item(
                            ap_item_id,
                            metadata=json.dumps(_existing_meta),
                            _actor_type="system",
                            _actor_id="payment_tracking",
                        )
                    except Exception as _meta_err:
                        logger.debug("payment metadata persist failed: %s", _meta_err)
                # Slack notification for payment readiness (fire-and-forget).
                # Don't block the approval flow on Slack — the user has
                # already been told the invoice is posted, and a slow
                # Slack API shouldn't hold the approve endpoint open.
                # But `ensure_future` without a task reference is a
                # silent-failure trap: if Slack raises, the exception
                # is only logged when the task is GC'd (if at all). Use
                # create_task + a done callback so any error lands in
                # our own logger where we can actually find it.
                try:
                    from clearledgr.services.slack_notifications import (
                        send_payment_ready_notification,
                    )
                    import asyncio
                    _task = asyncio.create_task(send_payment_ready_notification(
                        organization_id=self.organization_id,
                        ap_item_id=ap_item_id or gmail_id,
                        vendor_name=invoice.vendor_name,
                        amount=invoice.amount,
                        currency=invoice.currency,
                        due_date=getattr(invoice, "due_date", None),
                        erp_reference=erp_reference,
                    ))

                    def _log_slack_notify_result(t: "asyncio.Task") -> None:
                        exc = t.exception()
                        if exc is not None:
                            logger.warning(
                                "payment_ready slack notification failed: %s", exc
                            )

                    _task.add_done_callback(_log_slack_notify_result)
                except Exception as _slack_err:
                    logger.debug("payment ready slack notification skipped: %s", _slack_err)
            except Exception as pay_err:
                logger.warning("Failed to create payment tracking record: %s", pay_err)

            # M1: Transition posted_to_erp -> closed (terminal state).
            try:
                self._transition_invoice_state(
                    gmail_id=gmail_id,
                    target_state="closed",
                    correlation_id=correlation_id,
                )
            except Exception as close_exc:
                logger.warning("Failed to transition to closed: %s", close_exc)
        else:
            failure = self._erp_failure_details(result)
            failure_reason = failure["failure_reason"]
            exception_code = failure["exception_code"]
            recoverability = failure["recoverability"]
            self._transition_invoice_state(
                gmail_id=gmail_id,
                target_state="failed_post",
                correlation_id=correlation_id,
                post_attempted_at=post_attempted_at,
                last_error=failure_reason,
                exception_code=exception_code,
                exception_severity="error",
            )
            self._record_approval_snapshot(
                ap_item_id=ap_item_id,
                gmail_id=gmail_id,
                channel_id=resolved_channel_id,
                message_ts=resolved_message_ref,
                source_channel=resolved_source_channel,
                source_message_ref=gmail_id,
                status="failed",
                decision_payload={
                    "decision": decision_type,
                    "override_justification": override_justification,
                    "confidence_override": False,
                    "confidence_gate": confidence_gate,
                    "po_override_reason": po_override_reason,
                    "budget": budget_summary,
                    "budget_impact": budget_checks,
                    "erp_result": result,
                    "run_id": action_run_id,
                    "request_ts": decision_request_ts,
                    "actor_display": actor_display,
                    "actor_email": actor_email,
                    "actor_platform_id": actor_platform_id,
                    "actor_identity": actor_identity,
                    "actor_label": approved_by_label,
                    "decision_idempotency_key": decision_idempotency_key,
                },
                decision_idempotency_key=decision_idempotency_key,
            )
            self._record_vendor_decision_feedback(
                ap_item_id=ap_item_id,
                vendor_name=invoice.vendor_name,
                human_action="approve",
                actor_id=approved_by,
                source_channel=resolved_source_channel,
                correlation_id=correlation_id,
                reason=failure_reason,
                action_outcome="failed_post",
            )
            # Gap #5: Enqueue durable retry so the background loop can recover
            # items stuck in failed_post after a crash or transient ERP error.
            if ap_item_id and recoverability.get("recoverable"):
                self._enqueue_erp_post_retry(
                    ap_item_id=ap_item_id,
                    gmail_id=gmail_id,
                    correlation_id=correlation_id,
                )

        return {
            "status": "approved" if result.get("status") == "success" else "error",
            "invoice_id": gmail_id,
            "approved_by": approved_by,
            "approved_by_label": approved_by_label,
            "approver_identity": actor_identity,
            "decision_idempotency_key": decision_idempotency_key,
            "budget_override": budget_override_used,
            "confidence_override": False,
            "requires_field_review": bool(confidence_gate.get("requires_field_review")),
            "confidence_blockers": confidence_blockers,
            "override_justification": override_justification,
            "budget": budget_summary,
            "confidence_gate": confidence_gate,
            "erp_result": result,
        }

    async def reject_invoice(
        self,
        gmail_id: str,
        reason: str,
        rejected_by: str,
        slack_channel: Optional[str] = None,
        slack_ts: Optional[str] = None,
        source_channel: str = "slack",
        source_channel_id: Optional[str] = None,
        source_message_ref: Optional[str] = None,
        actor_display: Optional[str] = None,
        actor_email: Optional[str] = None,
        actor_platform_id: Optional[str] = None,
        actor_identity: Optional[Dict[str, Any]] = None,
        action_run_id: Optional[str] = None,
        decision_request_ts: Optional[str] = None,
        decision_idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reject an invoice with reason."""
        invoice_data = self.db.get_invoice_status(gmail_id)
        if not invoice_data:
            return {"status": "error", "reason": "Invoice not found"}
        rejected_at = datetime.now(timezone.utc).isoformat()
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=gmail_id,
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name"),
            invoice_number=invoice_data.get("invoice_number"),
        )
        correlation_id = self._ensure_ap_item_correlation_id(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            preferred=correlation_id,
        )
        resolved_source_channel = str(source_channel or "slack").strip().lower() or "slack"
        resolved_channel_id = source_channel_id or slack_channel
        resolved_message_ref = source_message_ref or slack_ts
        rejector = self._normalize_human_actor(
            rejected_by,
            actor_display=actor_display,
            actor_email=actor_email,
            actor_platform_id=actor_platform_id,
            actor_identity=actor_identity,
            platform=resolved_source_channel,
        )
        rejected_by = rejector["canonical_actor"]
        rejected_by_label = rejector["label"]
        actor_identity = rejector["identity"]
        actor_display = actor_identity.get("display_name")
        actor_email = actor_identity.get("email")
        actor_platform_id = actor_identity.get("platform_user_id")
        existing_decision_snapshot = self._approval_snapshot_by_decision_key(
            ap_item_id,
            decision_idempotency_key,
        )
        if existing_decision_snapshot:
            existing_status = str(existing_decision_snapshot.get("status") or "").strip().lower()
            if existing_status == "rejected":
                return {
                    "status": "rejected",
                    "invoice_id": gmail_id,
                    "rejected_by": rejected_by,
                    "reason": reason,
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                }
            if existing_status in {"processing", "pending_adjustment", "approved"}:
                return {
                    "status": "duplicate_in_progress",
                    "invoice_id": gmail_id,
                    "reason": "duplicate_reject_action_in_progress",
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                }
        if decision_idempotency_key and not self._acquire_decision_action_lock(
            ap_item_id=ap_item_id,
            decision_idempotency_key=decision_idempotency_key,
            actor_id=rejected_by,
            source_channel=resolved_source_channel,
            correlation_id=correlation_id,
            metadata={
                "gmail_id": gmail_id,
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "source_message_ref": resolved_message_ref,
                "action": "reject",
            },
        ):
            return {
                "status": "duplicate_in_progress",
                "invoice_id": gmail_id,
                "reason": "duplicate_reject_action_in_progress",
                "duplicate_action": True,
                "decision_idempotency_key": decision_idempotency_key,
            }

        # Update status
        self.db.update_invoice_status(
            gmail_id=gmail_id,
            status="rejected",
            rejection_reason=reason,
            rejected_by=rejected_by,
            rejected_at=rejected_at,
            _correlation_id=correlation_id,
            _source=resolved_source_channel,
            _workflow_id="approval_decision",
            _run_id=action_run_id,
            _decision_reason="reject",
        )

        # Update Slack thread status
        thread = self.db.get_slack_thread(gmail_id)
        if thread:
            self.db.update_slack_thread_status(
                gmail_id=gmail_id,
                channel_id=thread.get("channel_id"),
                thread_ts=thread.get("thread_ts"),
                thread_id=thread.get("thread_id") or thread.get("thread_ts"),
                status="rejected",
                rejection_reason=reason,
            )

        # Update Slack message
        if resolved_source_channel == "slack" and resolved_channel_id and resolved_message_ref:
            await self._update_slack_rejected(
                resolved_channel_id, resolved_message_ref, invoice_data, rejected_by_label, reason
            )
        self._maybe_record_ap_decision_override(
            ap_item_id, "rejected", rejected_by,
            correlation_id=correlation_id,
            human_reason=reason,
        )
        self._record_approval_snapshot(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            channel_id=resolved_channel_id,
            message_ts=resolved_message_ref,
            source_channel=resolved_source_channel,
            source_message_ref=gmail_id,
            status="rejected",
            decision_payload={
                "decision": "reject",
                "reason": reason,
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "actor_display": actor_display,
                "actor_email": actor_email,
                "actor_platform_id": actor_platform_id,
                "actor_identity": actor_identity,
                "actor_label": rejected_by_label,
                "decision_idempotency_key": decision_idempotency_key,
            },
            rejected_by=rejected_by,
            rejected_at=rejected_at,
            rejection_reason=reason,
            decision_idempotency_key=decision_idempotency_key,
        )
        self._record_vendor_decision_feedback(
            ap_item_id=ap_item_id,
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name"),
            human_action="reject",
            actor_id=rejected_by,
            source_channel=resolved_source_channel,
            correlation_id=correlation_id,
            reason=reason,
            action_outcome="rejected",
            final_state="rejected",
            was_approved=False,
            amount=invoice_data.get("amount"),
            invoice_date=invoice_data.get("due_date"),
        )

        # Gap 6: Update approval chain on rejection
        try:
            chain = self.db.db_get_chain_by_invoice(self.organization_id, gmail_id)
            if chain:
                now_iso = datetime.now(timezone.utc).isoformat()
                self.db.db_update_chain_step(
                    chain["id"], 0, status="rejected",
                    approved_by=rejected_by, approved_at=now_iso,
                    rejection_reason=reason,
                )
                self.db.db_update_chain_status(
                    chain["id"], status="rejected", current_step=0, completed_at=now_iso,
                )
        except Exception as exc:
            logger.error("Approval chain rejection update failed for %s: %s", ap_item_id, exc)

        logger.info(f"Invoice rejected: {gmail_id} by {rejected_by} - {reason}")

        return {
            "status": "rejected",
            "invoice_id": gmail_id,
            "rejected_by": rejected_by,
            "rejected_by_label": rejected_by_label,
            "approver_identity": actor_identity,
            "reason": reason,
            "decision_idempotency_key": decision_idempotency_key,
        }

    def _erp_failure_details(self, result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = result if isinstance(result, dict) else {}
        failure_reason = (
            str(payload.get("error_message") or "").strip()
            or str(payload.get("reason") or "").strip()
            or str(payload.get("status") or "").strip()
            or "erp_post_failed"
        )
        exception_code = str(payload.get("error_code") or "").strip().lower() or "erp_post_failed"
        recoverability = classify_post_failure_recoverability(
            last_error=failure_reason,
            exception_code=exception_code,
        )
        return {
            "failure_reason": failure_reason,
            "exception_code": exception_code,
            "recoverability": recoverability,
        }

    def _enqueue_erp_post_retry(
        self,
        *,
        ap_item_id: str,
        gmail_id: str,
        correlation_id: Optional[str] = None,
        max_retries: int = 3,
    ) -> None:
        """Create a durable retry job for ERP post recovery.

        Called after an item lands in ``failed_post`` so the background loop
        can attempt ``resume_workflow`` on the next tick.  Idempotent: a second
        call for the same ap_item_id is a no-op (same idempotency_key).
        """
        if not hasattr(self.db, "create_agent_retry_job"):
            return
        idem_key = f"erp_post_retry:{ap_item_id}"
        try:
            now = datetime.now(timezone.utc).isoformat()
            self.db.create_agent_retry_job(
                {
                    "organization_id": self.organization_id,
                    "ap_item_id": ap_item_id,
                    "gmail_id": gmail_id,
                    "job_type": "erp_post_retry",
                    "status": "pending",
                    "retry_count": 0,
                    "max_retries": max_retries,
                    "next_retry_at": now,
                    "idempotency_key": idem_key,
                    "correlation_id": correlation_id,
                }
            )
            logger.info(
                "Enqueued erp_post_retry job for ap_item_id=%s (corr=%s)",
                ap_item_id,
                correlation_id,
            )
        except Exception as exc:
            logger.warning("Failed to enqueue erp_post_retry for %s: %s", ap_item_id, exc)

    async def resume_workflow(self, ap_item_id: str) -> Dict[str, Any]:
        """Re-enter the ERP post step for an AP item stuck in a recoverable state.

        Safe to call multiple times — each step is idempotent:
        - ``ready_to_post``: re-runs ERP post directly.
        - ``failed_post``: transitions back to ``ready_to_post``, then re-runs.
        - Any other state: returns ``{"status": "not_resumable", ...}``.

        Uses a stable idempotency key ``resume:<ap_item_id>:erp_post`` so
        a duplicate network call never double-posts to the ERP.
        """
        if not hasattr(self.db, "get_ap_item"):
            return {"status": "error", "reason": "db_not_supported"}

        row = self.db.get_ap_item(ap_item_id)
        if not row:
            return {"status": "error", "reason": "ap_item_not_found", "ap_item_id": ap_item_id}

        current_state = self._canonical_invoice_state(row)
        gmail_id = str(row.get("thread_id") or "")
        correlation_id = self._get_ap_item_correlation_id(ap_item_id=ap_item_id)

        if current_state not in {"failed_post", "ready_to_post"}:
            return {
                "status": "not_resumable",
                "ap_item_id": ap_item_id,
                "current_state": current_state,
                "reason": "state_does_not_support_resume",
            }

        if not gmail_id:
            return {
                "status": "error",
                "ap_item_id": ap_item_id,
                "reason": "missing_gmail_id_on_ap_item",
            }

        field_review_gate = self.evaluate_financial_action_field_review_gate(row)
        if field_review_gate.get("blocked"):
            self._persist_financial_action_field_review_gate(ap_item_id, field_review_gate)
            return {
                "status": "blocked",
                "reason": "field_review_required",
                "ap_item_id": ap_item_id,
                "current_state": current_state,
                "detail": field_review_gate.get("detail"),
                "requires_field_review": True,
                "confidence_blockers": field_review_gate.get("confidence_blockers") or [],
                "source_conflicts": field_review_gate.get("source_conflicts") or [],
                "blocking_source_conflicts": field_review_gate.get("blocking_source_conflicts") or [],
                "blocked_fields": field_review_gate.get("blocked_fields") or [],
                "exception_code": field_review_gate.get("exception_code"),
            }

        # If in failed_post, step back to ready_to_post first (idempotent if already there)
        if current_state == "failed_post":
            self._transition_invoice_state(
                gmail_id,
                "ready_to_post",
                correlation_id=correlation_id,
                source="resume_workflow",
            )

        # Build InvoiceData from the persisted row
        invoice = InvoiceData(
            gmail_id=gmail_id,
            subject=str(row.get("subject") or ""),
            sender=str(row.get("sender") or ""),
            vendor_name=str(row.get("vendor_name") or "Unknown"),
            amount=float(row.get("amount") or 0),
            currency=str(row.get("currency") or "USD"),
            invoice_number=row.get("invoice_number"),
            due_date=row.get("due_date"),
            organization_id=self.organization_id,
            correlation_id=correlation_id,
        )

        # Stable idempotency key ensures the ERP never double-posts on resume
        idempotency_key = f"resume:{ap_item_id}:erp_post"
        result = await self._post_to_erp(
            invoice,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        post_attempted_at = datetime.now(timezone.utc).isoformat()

        if result.get("status") == "success":
            erp_reference = (
                result.get("erp_reference")
                or result.get("bill_id")
                or result.get("reference_id")
                or result.get("doc_num")
            )
            self._transition_invoice_state(
                gmail_id,
                "posted_to_erp",
                correlation_id=correlation_id,
                source="resume_workflow",
                erp_reference=erp_reference,
                erp_posted_at=post_attempted_at,
                post_attempted_at=post_attempted_at,
                last_error=None,
            )
            if ap_item_id:
                try:
                    self.db.append_audit_event(
                        {
                            "ap_item_id": ap_item_id,
                            "event_type": "erp_post_resumed",
                            "actor_type": "system",
                            "actor_id": "resume_workflow",
                            "reason": "workflow_crash_recovery",
                            "metadata": {
                                "erp_reference": erp_reference,
                                "idempotency_key": idempotency_key,
                                "recovered_from_state": current_state,
                            },
                            "organization_id": self.organization_id,
                            "correlation_id": correlation_id,
                            "source": "resume_workflow",
                        }
                    )
                except Exception as exc:
                    logger.error("Could not append erp_post_resumed audit event: %s", exc)
            logger.info(
                "resume_workflow: ap_item_id=%s recovered to posted_to_erp (ref=%s)",
                ap_item_id,
                erp_reference,
            )
            # M1: Transition posted_to_erp -> closed after successful recovery.
            try:
                self._transition_invoice_state(
                    gmail_id, "closed", correlation_id=correlation_id,
                )
            except Exception as close_exc:
                logger.warning("Failed to transition recovered item to closed: %s", close_exc)
            return {
                "status": "recovered",
                "ap_item_id": ap_item_id,
                "erp_reference": erp_reference,
                "erp_result": result,
            }

        # Post still failed — leave in failed_post with updated error
        failure = self._erp_failure_details(result)
        failure_reason = failure["failure_reason"]
        self._transition_invoice_state(
            gmail_id,
            "failed_post",
            correlation_id=correlation_id,
            source="resume_workflow",
            post_attempted_at=post_attempted_at,
            last_error=failure_reason,
            exception_code=failure["exception_code"],
        )
        logger.warning(
            "resume_workflow: ap_item_id=%s ERP post still failing: %s",
            ap_item_id,
            failure_reason,
        )
        return {
            "status": "still_failing",
            "ap_item_id": ap_item_id,
            "reason": failure_reason,
            "error_code": failure["exception_code"],
            "recoverability": failure["recoverability"],
            "erp_result": result,
        }

    async def request_budget_adjustment(
        self,
        gmail_id: str,
        requested_by: str,
        reason: Optional[str] = None,
        slack_channel: Optional[str] = None,
        slack_ts: Optional[str] = None,
        source_channel: str = "slack",
        source_channel_id: Optional[str] = None,
        source_message_ref: Optional[str] = None,
        actor_display: Optional[str] = None,
        actor_email: Optional[str] = None,
        actor_platform_id: Optional[str] = None,
        actor_identity: Optional[Dict[str, Any]] = None,
        action_run_id: Optional[str] = None,
        decision_request_ts: Optional[str] = None,
        decision_idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark invoice for budget adjustment before final approval."""
        invoice_data = self.db.get_invoice_status(gmail_id)
        if not invoice_data:
            return {"status": "error", "reason": "Invoice not found"}

        reason_text = str(reason or "budget_adjustment_requested").strip() or "budget_adjustment_requested"
        requested_at = datetime.now(timezone.utc).isoformat()
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=gmail_id,
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name"),
            invoice_number=invoice_data.get("invoice_number"),
        )
        correlation_id = self._ensure_ap_item_correlation_id(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            preferred=correlation_id,
        )
        resolved_source_channel = str(source_channel or "slack").strip().lower() or "slack"
        resolved_channel_id = source_channel_id or slack_channel
        resolved_message_ref = source_message_ref or slack_ts
        requester = self._normalize_human_actor(
            requested_by,
            actor_display=actor_display,
            actor_email=actor_email,
            actor_platform_id=actor_platform_id,
            actor_identity=actor_identity,
            platform=resolved_source_channel,
        )
        requested_by = requester["canonical_actor"]
        requested_by_label = requester["label"]
        actor_identity = requester["identity"]
        actor_display = actor_identity.get("display_name")
        actor_email = actor_identity.get("email")
        actor_platform_id = actor_identity.get("platform_user_id")
        existing_decision_snapshot = self._approval_snapshot_by_decision_key(
            ap_item_id,
            decision_idempotency_key,
        )
        if existing_decision_snapshot:
            existing_status = str(existing_decision_snapshot.get("status") or "").strip().lower()
            if existing_status == "pending_adjustment":
                return {
                    "status": "needs_info",
                    "invoice_id": gmail_id,
                    "requested_by": requested_by,
                    "reason": reason_text,
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                }
            if existing_status in {"processing", "approved", "rejected"}:
                return {
                    "status": "duplicate_in_progress",
                    "invoice_id": gmail_id,
                    "reason": "duplicate_request_info_action_in_progress",
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                }
        if decision_idempotency_key and not self._acquire_decision_action_lock(
            ap_item_id=ap_item_id,
            decision_idempotency_key=decision_idempotency_key,
            actor_id=requested_by,
            source_channel=resolved_source_channel,
            correlation_id=correlation_id,
            metadata={
                "gmail_id": gmail_id,
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "source_message_ref": resolved_message_ref,
                "action": "request_info",
            },
        ):
            return {
                "status": "duplicate_in_progress",
                "invoice_id": gmail_id,
                "reason": "duplicate_request_info_action_in_progress",
                "duplicate_action": True,
                "decision_idempotency_key": decision_idempotency_key,
            }

        self.db.update_invoice_status(
            gmail_id=gmail_id,
            status="needs_info",
            rejection_reason=reason_text,
            rejected_by=requested_by,
            rejected_at=requested_at,
            _correlation_id=correlation_id,
            _source=resolved_source_channel,
            _workflow_id="approval_decision",
            _run_id=action_run_id,
            _decision_reason="request_info",
        )

        if resolved_source_channel == "slack" and resolved_channel_id and resolved_message_ref:
            await self._update_slack_budget_adjustment_requested(
                resolved_channel_id,
                resolved_message_ref,
                invoice_data,
                requested_by=requested_by_label,
                reason=reason_text,
            )

        self._record_approval_snapshot(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            channel_id=resolved_channel_id,
            message_ts=resolved_message_ref,
            source_channel=resolved_source_channel,
            source_message_ref=gmail_id,
            status="pending_adjustment",
            decision_payload={
                "decision": "request_budget_adjustment",
                "reason": reason_text,
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "actor_display": actor_display,
                "actor_email": actor_email,
                "actor_platform_id": actor_platform_id,
                "actor_identity": actor_identity,
                "actor_label": requested_by_label,
                "decision_idempotency_key": decision_idempotency_key,
            },
            rejected_by=requested_by,
            rejected_at=requested_at,
            rejection_reason=reason_text,
            decision_idempotency_key=decision_idempotency_key,
        )
        self._record_vendor_decision_feedback(
            ap_item_id=ap_item_id,
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name"),
            human_action="request_info",
            actor_id=requested_by,
            source_channel=resolved_source_channel,
            correlation_id=correlation_id,
            reason=reason_text,
            action_outcome="needs_info",
        )

        ap_row = self.db.get_ap_item(ap_item_id) if ap_item_id and hasattr(self.db, "get_ap_item") else None
        ap_meta = self._parse_metadata_dict((ap_row or {}).get("metadata"))
        followup_question = str(ap_meta.get("needs_info_question") or reason_text).strip() or reason_text
        if followup_question:
            self._update_ap_item_metadata(ap_item_id, {"needs_info_question": followup_question})

        draft_id = await self._create_needs_info_vendor_draft(
            ap_item_id=ap_item_id,
            thread_id=gmail_id,
            to_email=str(invoice_data.get("sender") or ""),
            invoice_data={
                "subject": invoice_data.get("email_subject") or invoice_data.get("subject") or "",
                "vendor_name": invoice_data.get("vendor") or invoice_data.get("vendor_name") or "",
                "amount": invoice_data.get("amount") or 0.0,
                "invoice_number": invoice_data.get("invoice_number") or "",
            },
            question=followup_question,
            user_id=invoice_data.get("user_id"),
        )
        self._apply_needs_info_followup_metadata(
            ap_item_id=ap_item_id,
            draft_id=draft_id,
            question=followup_question,
            actor_type="user",
            actor_id=requested_by,
            source=resolved_source_channel,
            correlation_id=correlation_id,
        )

        return {
            "status": "needs_info",
            "invoice_id": gmail_id,
            "requested_by": requested_by,
            "requested_by_label": requested_by_label,
            "approver_identity": actor_identity,
            "reason": reason_text,
            "decision_idempotency_key": decision_idempotency_key,
        }

    async def _post_to_erp(
        self,
        invoice: InvoiceData,
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Post approved invoice to ERP as a Bill.

        Enforces state guard (PLAN.md S4.6-1): posting only from ``ready_to_post``.
        Enforces mandatory idempotency key (PLAN.md S7.3-1): generates one if
        the caller did not provide one.

        ERP-native short-circuit (Phase D of intake refactor): when
        ``invoice.erp_native`` is True the bill is already in the ERP —
        the customer's own AP team or an EDI gateway posted it before
        we ever saw it. Calling ``post_bill_to_*`` again would create
        a duplicate. We fabricate a successful PostResult shape with
        the existing ``erp_reference`` so the downstream state
        transition path runs unchanged but no actual ERP write
        happens.
        """
        if getattr(invoice, "erp_native", False):
            ap_item_id_native = self._lookup_ap_item_id(
                gmail_id=invoice.gmail_id,
                vendor_name=invoice.vendor_name,
                invoice_number=invoice.invoice_number,
            )
            existing_native = self.db.get_ap_item(ap_item_id_native) if ap_item_id_native else None
            erp_reference_native = (
                str((existing_native or {}).get("erp_reference") or "").strip()
                or str(getattr(invoice, "source_id", "") or "").strip()
            )
            erp_metadata = getattr(invoice, "erp_metadata", None) or {}
            return {
                "status": "success",
                "erp": str(getattr(invoice, "source_type", "") or "").replace("_native", "") or "erp_native",
                "erp_reference": erp_reference_native,
                "ap_item_id": ap_item_id_native,
                "invoice_id": invoice.gmail_id,
                "posted_by_erp_native": True,
                "skipped_post": True,
                "skip_reason": "erp_native_already_posted",
                "erp_metadata": erp_metadata,
            }

        # B2: State guard — only post from ready_to_post (PLAN.md S4.6)
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=invoice.gmail_id,
            vendor_name=invoice.vendor_name,
            invoice_number=invoice.invoice_number,
        )
        existing: Optional[Dict[str, Any]] = None
        if ap_item_id:
            existing = self.db.get_ap_item(ap_item_id)
        elif hasattr(self.db, "get_invoice_status"):
            existing = self.db.get_invoice_status(invoice.gmail_id)

        field_review_gate = self.evaluate_financial_action_field_review_gate(existing or {})
        if field_review_gate.get("blocked"):
            self._persist_financial_action_field_review_gate(ap_item_id, field_review_gate)
            return {
                "status": "blocked",
                "reason": "field_review_required",
                "invoice_id": invoice.gmail_id,
                "ap_item_id": ap_item_id,
                "detail": field_review_gate.get("detail"),
                "requires_field_review": True,
                "confidence_blockers": field_review_gate.get("confidence_blockers") or [],
                "source_conflicts": field_review_gate.get("source_conflicts") or [],
                "blocking_source_conflicts": field_review_gate.get("blocking_source_conflicts") or [],
                "blocked_fields": field_review_gate.get("blocked_fields") or [],
                "exception_code": field_review_gate.get("exception_code"),
            }

        if existing:
            current_state = self._canonical_invoice_state(existing) or ""
            if current_state not in ("ready_to_post",):
                logger.error(
                    "State guard: refusing ERP post for AP item %s in state '%s' (expected ready_to_post)",
                    ap_item_id, current_state,
                )
                return {
                    "status": "error",
                    "reason": "illegal_state_for_posting",
                    "current_state": current_state,
                    "expected_state": "ready_to_post",
                }

        # B3: Mandatory idempotency key — generate if not provided (PLAN.md S7.3)
        # B4: Use a stable key derived from the AP item so retries reuse the same
        # key and never duplicate-post to the ERP.
        if not idempotency_key:
            stable_seed = ap_item_id or invoice.gmail_id or invoice.invoice_number or ""
            idempotency_key = f"auto:{stable_seed}:erp_post"
            logger.warning("Generated stable idempotency_key=%s (caller did not provide one)", idempotency_key)

        # First, get or create vendor
        vendor = Vendor(
            name=invoice.vendor_name,
            currency=invoice.currency,
        )

        vendor_result = await get_or_create_vendor(self.organization_id, vendor)

        if vendor_result.get("status") == "error":
            logger.error(f"Failed to get/create vendor: {vendor_result}")
            return vendor_result

        vendor_id = vendor_result.get("vendor_id")

        # Create and post bill
        bill = Bill(
            vendor_id=vendor_id,
            vendor_name=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            invoice_number=invoice.invoice_number,
            invoice_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            due_date=invoice.due_date,
            description=f"Invoice from {invoice.vendor_name}",
            po_number=invoice.po_number,
            attachment_url=invoice.attachment_url,
            line_items=invoice.line_items,
            tax_amount=getattr(invoice, "tax_amount", None),
            tax_rate=getattr(invoice, "tax_rate", None),
            discount_amount=getattr(invoice, "discount_amount", None),
            discount_terms=getattr(invoice, "discount_terms", None),
            payment_terms=getattr(invoice, "payment_terms", None),
        )

        ap_item_id = self._lookup_ap_item_id(
            gmail_id=invoice.gmail_id,
            vendor_name=invoice.vendor_name,
            invoice_number=invoice.invoice_number,
        )

        # H3: Audit ERP post attempt before execution (PLAN.md S4.7)
        if ap_item_id:
            try:
                self.db.append_audit_event(
                    {
                        "ap_item_id": ap_item_id,
                        "event_type": "erp_post_attempted",
                        "actor_type": "system",
                        "actor_id": "invoice_workflow",
                        "metadata": {
                            "idempotency_key": idempotency_key,
                            "vendor": invoice.vendor_name,
                            "amount": invoice.amount,
                            "invoice_number": invoice.invoice_number,
                        },
                        "organization_id": self.organization_id,
                        "correlation_id": correlation_id or invoice.correlation_id,
                        "source": "invoice_workflow",
                    }
                )
            except Exception:
                pass  # Non-fatal

        # §3 Multi-entity: resolve entity_id from the AP item so the
        # correct entity-specific ERP connection is used.
        entity_id_for_post = None
        if existing and existing.get("entity_id"):
            entity_id_for_post = existing["entity_id"]

        result = await post_bill_api_first(
            organization_id=self.organization_id,
            bill=bill,
            actor_id="invoice_workflow",
            ap_item_id=ap_item_id,
            entity_id=entity_id_for_post,
            email_id=invoice.gmail_id,
            invoice_number=invoice.invoice_number,
            vendor_name=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            vendor_portal_url=invoice.attachment_url,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id or invoice.correlation_id,
        )

        # H3: Audit ERP post result (PLAN.md S4.7)
        if ap_item_id:
            post_event_type = "erp_post_succeeded" if result.get("status") == "success" else "erp_post_failed"
            try:
                self.db.append_audit_event(
                    {
                        "ap_item_id": ap_item_id,
                        "event_type": post_event_type,
                        "actor_type": "system",
                        "actor_id": "invoice_workflow",
                        "metadata": {
                            "idempotency_key": idempotency_key,
                            "erp_reference": result.get("erp_reference") or result.get("bill_id"),
                            "erp_type": result.get("erp") or result.get("erp_type"),
                            "status": result.get("status"),
                            "reason": result.get("reason"),
                        },
                        "organization_id": self.organization_id,
                        "correlation_id": correlation_id or invoice.correlation_id,
                        "source": "invoice_workflow",
                    }
                )
            except Exception:
                pass  # Non-fatal
            if hasattr(self.db, "update_ap_item_metadata_merge"):
                verification_status = "verified_success" if result.get("status") == "success" else "verified_failure"
                verification_reasons = []
                if result.get("status") == "success":
                    if not (result.get("erp_reference") or result.get("bill_id")):
                        verification_status = "verification_gap"
                        verification_reasons.append("missing_erp_reference")
                else:
                    if not (result.get("reason") or result.get("error")):
                        verification_reasons.append("missing_failure_reason")
                try:
                    self.db.update_ap_item_metadata_merge(
                        ap_item_id,
                        {
                            "post_action_verification": {
                                "verified_at": datetime.now(timezone.utc).isoformat(),
                                "status": verification_status,
                                "attempted": True,
                                "result_status": result.get("status"),
                                "erp_reference": result.get("erp_reference") or result.get("bill_id"),
                                "erp_type": result.get("erp") or result.get("erp_type"),
                                "reason_codes": verification_reasons,
                            }
                        },
                    )
                except Exception as exc:
                    logger.error("Post-action verification persistence failed: %s", exc)

        if result.get("status") == "success":
            result["vendor_id"] = vendor_id
            logger.info(f"Posted bill to ERP: {result.get('bill_id')}")

        return result

    async def _send_posted_notification(
        self,
        invoice: InvoiceData,
        erp_result: Dict[str, Any],
        reason: str = "high_confidence",
    ) -> None:
        """Send notification that invoice was auto-posted with reasoning."""
        _ = reason
        if invoice.reasoning_summary:
            reason_text = f"{invoice.reasoning_summary}"
        else:
            reason_text = f"Auto-approved (confidence: {invoice.confidence*100:.0f}%)"

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Invoice Auto-Posted*\n"
                            f"*{invoice.vendor_name}* - {invoice.currency} {invoice.amount:,.2f}\n"
                            f"Bill ID: `{erp_result.get('bill_id')}`"
                }
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": reason_text}
                ]
            }
        ]

        # Add reasoning factors if available
        if invoice.reasoning_factors:
            factor_lines = []
            for f in invoice.reasoning_factors[:3]:  # Top 3 factors
                score_value = int(f.get("score", 0) * 5)
                factor_lines.append(f"Score {score_value}/5 - {f.get('detail', '')}")

            if factor_lines:
                blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "\n".join(factor_lines)}
                    ]
                })

        await self.slack_client.send_message(
            channel=self.slack_channel,
            text=f"Invoice auto-posted: {invoice.vendor_name} ${invoice.amount:,.2f}",
            blocks=blocks,
        )

    async def _update_slack_approved(
        self,
        channel: str,
        ts: str,
        invoice: InvoiceData,
        approved_by: str,
        erp_result: Dict[str, Any],
    ) -> None:
        """Update Slack message to remove buttons and post threaded confirmation."""
        doc_number = erp_result.get("doc_num") or erp_result.get("document_number") or erp_result.get("erp_document")
        bill_id = erp_result.get("bill_id")
        erp_type = erp_result.get("erp_type", "ERP")
        gl_code = erp_result.get("gl_code") or (invoice.vendor_intelligence or {}).get("suggested_gl", "")

        # 1. Update original card — remove buttons, add "Approved" badge
        approved_blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*{invoice.vendor_name}* — {invoice.currency} {invoice.amount:,.2f}\n"
                f"Invoice #: {invoice.invoice_number or 'N/A'} | "
                f"Approved by {approved_by}"
            )}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "Posted to ERP"}]},
        ]
        try:
            await self.slack_client.update_message(channel, ts, "Invoice approved", approved_blocks)
        except Exception as e:
            logger.warning(f"Failed to update Slack card: {e}")

        # 2. Post threaded confirmation with details
        ref_parts = []
        if bill_id:
            ref_parts.append(f"Bill ID: `{bill_id}`")
        if doc_number:
            ref_parts.append(f"Doc #: `{doc_number}`")
        if gl_code:
            ref_parts.append(f"GL: `{gl_code}`")

        confirm_text = (
            f"Posted to {erp_type}\n"
            + (" | ".join(ref_parts) + "\n" if ref_parts else "")
            + f"Approved by {approved_by}"
        )
        try:
            await self.slack_client.send_message(
                channel=channel,
                text=confirm_text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": confirm_text}}],
                thread_ts=ts,
            )
        except Exception as e:
            logger.warning(f"Failed to post threaded confirmation: {e}")

    async def _update_slack_rejected(
        self,
        channel: str,
        ts: str,
        invoice_data: Dict[str, Any],
        rejected_by: str,
        reason: str,
    ) -> None:
        """Update Slack message to show rejected status."""
        blocks = [
            {
                "type": "section",
                "text": {
                "type": "mrkdwn",
                "text": f"*Invoice Rejected*\n"
                        f"*{invoice_data.get('vendor', 'Unknown')}* - {invoice_data.get('currency', 'USD')} {invoice_data.get('amount', 0):,.2f}\n"
                        f"Reason: {reason}"
            }
        },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Rejected by {rejected_by}"}
                ]
            }
        ]

        try:
            await self.slack_client.update_message(channel, ts, "Invoice rejected", blocks)
        except Exception as e:
            logger.warning(f"Failed to update Slack message: {e}")

    async def _update_slack_budget_adjustment_requested(
        self,
        channel: str,
        ts: str,
        invoice_data: Dict[str, Any],
        requested_by: str,
        reason: str,
    ) -> None:
        """Update Slack message when approver requests budget adjustment."""
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Budget Adjustment Requested*\n"
                        f"*{invoice_data.get('vendor', 'Unknown')}* - "
                        f"{invoice_data.get('currency', 'USD')} {invoice_data.get('amount', 0):,.2f}\n"
                        f"Reason: {reason}"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Requested by {requested_by}"},
                ],
            },
        ]
        try:
            await self.slack_client.update_message(channel, ts, "Budget adjustment requested", blocks)
        except Exception as e:
            logger.warning(f"Failed to update Slack message for budget adjustment: {e}")

    async def send_exception_alert(
        self,
        invoice: InvoiceData,
        exception_type: str,
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Send exception alert to Slack.

        Exception types:
        - duplicate: Potential duplicate invoice detected
        - amount_mismatch: Amount doesn't match PO
        - vendor_unknown: Vendor not in system
        - overdue: Invoice is past due date
        """
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Exception: {exception_type.replace('_', ' ').title()}"
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Vendor:*\n{invoice.vendor_name}"},
                    {"type": "mrkdwn", "text": f"*Amount:*\n{invoice.currency} {invoice.amount:,.2f}"},
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Details:*\n{details.get('message', 'No details available')}"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Review"},
                        "action_id": f"review_exception_{invoice.gmail_id}",
                        "value": invoice.gmail_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Dismiss"},
                        "action_id": f"dismiss_exception_{invoice.gmail_id}",
                        "value": invoice.gmail_id,
                    },
                ]
            }
        ]

        try:
            message = await self.slack_client.send_message(
                channel=self.slack_channel,
                text=f"Exception: {exception_type} - {invoice.vendor_name}",
                blocks=blocks,
            )

            return {
                "status": "sent",
                "channel": message.channel,
                "ts": message.ts,
            }
        except Exception as e:
            logger.error(f"Failed to send exception alert: {e}")
            return {"status": "error", "error": str(e)}
