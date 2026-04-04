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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from clearledgr.core.ap_item_resolution import resolve_ap_item_reference
from clearledgr.core.database import get_db
from clearledgr.core.finance_contracts import (
    ActionExecution,
    AuditEvent,
    SkillRequest,
)
from clearledgr.services.policy_compliance import get_approval_automation_policy
from clearledgr.services.finance_runtime_invoice_processing import (
    execute_ap_invoice_processing as execute_runtime_invoice_processing,
)
from clearledgr.services.finance_runtime_actions import (
    build_finance_lead_summary_payload as runtime_build_finance_lead_summary_payload,
    escalate_invoice_review as runtime_escalate_invoice_review,
    record_field_correction as runtime_record_field_correction,
    share_finance_summary as runtime_share_finance_summary,
)
from clearledgr.services.finance_runtime_autonomy import (
    ap_autonomy_policy as runtime_ap_autonomy_policy,
    ap_autonomy_summary as runtime_ap_autonomy_summary,
    autonomy_action_thresholds as runtime_autonomy_action_thresholds,
    autonomy_requested_action_dependencies as runtime_autonomy_requested_action_dependencies,
    build_shadow_decision_proposal as runtime_build_shadow_decision_proposal,
    dedupe_reason_codes as runtime_dedupe_reason_codes,
    evaluate_action_autonomy_policy as runtime_evaluate_action_autonomy_policy,
    evaluate_ap_vendor_autonomy as runtime_evaluate_ap_vendor_autonomy,
    extraction_drift_payload as runtime_extraction_drift_payload,
    is_autonomous_request as runtime_is_autonomous_request,
    item_finance_effect_policy as runtime_item_finance_effect_policy,
    post_action_verification_payload as runtime_post_action_verification_payload,
    shadow_decision_payload as runtime_shadow_decision_payload,
    vendor_drift_scorecard as runtime_vendor_drift_scorecard,
    vendor_post_verification_scorecard as runtime_vendor_post_verification_scorecard,
    vendor_shadow_scorecard as runtime_vendor_shadow_scorecard,
)
from clearledgr.services.finance_runtime_readiness import (
    ap_kpis_snapshot,
    build_skill_readiness,
    collect_connector_readiness,
    collect_operator_acceptance,
    evaluate_gate,
    readiness_gate_failures,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from clearledgr.services.finance_skills import FinanceSkill

_GENERIC_VENDOR_ALIASES = {
    "google",
    "stripe",
    "paypal",
    "square",
    "google workspace",
}


class IntentNotSupportedError(ValueError):
    """Raised when an unknown finance agent intent is requested."""


@dataclass
class APActionContext:
    reference: str
    ap_item: Dict[str, Any]
    ap_item_id: str
    email_id: str
    metadata: Dict[str, Any]
    correlation_id: Optional[str]


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
        from clearledgr.services.finance_skills import (
            APFinanceSkill,
            VendorComplianceSkill,
            WorkflowHealthSkill,
        )

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
    def parse_json_dict(raw: Any) -> Dict[str, Any]:
        return FinanceAgentRuntime._parse_json_dict(raw)

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def safe_int(value: Any, default: int = 0) -> int:
        return FinanceAgentRuntime._safe_int(value, default)

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_vendor_name(value: Any) -> str:
        vendor = str(value or "").strip()
        if vendor.lower() in {"unknown", "unknown vendor", "n/a", "na", "none"}:
            return ""
        return vendor

    @staticmethod
    def _sender_domain(value: Any) -> str:
        sender = str(value or "").strip().lower()
        if "@" not in sender:
            return ""
        return sender.rsplit("@", 1)[-1]

    @classmethod
    def _vendor_from_sender(cls, sender: Any) -> str:
        raw = str(sender or "").strip()
        if not raw:
            return ""
        import re

        name_match = re.match(r"^([^<]+)", raw)
        if name_match:
            candidate = cls._normalize_vendor_name(name_match.group(1))
            if candidate:
                return candidate
        if "@" in raw:
            domain = raw.split("@", 1)[1].split(".", 1)[0]
            return cls._normalize_vendor_name(domain.title())
        return cls._normalize_vendor_name(raw)

    @classmethod
    def _resolved_vendor_name(cls, vendor: Any, sender: Any) -> str:
        normalized_vendor = cls._normalize_vendor_name(vendor)
        sender_vendor = cls._vendor_from_sender(sender)
        if sender_vendor and normalized_vendor and normalized_vendor.lower() in _GENERIC_VENDOR_ALIASES:
            return sender_vendor
        return normalized_vendor or sender_vendor

    def _approval_sla_minutes(self) -> int:
        try:
            reminder_hours = int(
                get_approval_automation_policy(organization_id=self.organization_id).get("reminder_hours") or 4
            )
        except (TypeError, ValueError):
            reminder_hours = 4
        return max(60, min(reminder_hours * 60, 10080))

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
    def coerce_bool(value: Any) -> bool:
        return FinanceAgentRuntime._as_bool(value)

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
    def vendor_followup_sla_hours() -> int:
        return FinanceAgentRuntime._vendor_followup_sla_hours()

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
    def _item_reference_candidates(payload: Optional[Dict[str, Any]]) -> List[str]:
        if not isinstance(payload, dict):
            return []
        candidates: List[str] = []
        for key in ("ap_item_id", "item_id", "email_id", "thread_id", "message_id"):
            token = str(payload.get(key) or "").strip()
            if token and token not in candidates:
                candidates.append(token)
        return candidates

    @staticmethod
    def _normalize_correlation_id(payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("correlation_id") or payload.get("run_id") or "").strip()

    @staticmethod
    def _invoice_thread_id(invoice: Dict[str, Any]) -> str:
        if not isinstance(invoice, dict):
            return ""
        return str(
            invoice.get("thread_id")
            or invoice.get("gmail_thread_id")
            or invoice.get("gmail_id")
            or invoice.get("email_id")
            or ""
        ).strip()

    @staticmethod
    def _invoice_message_id(invoice: Dict[str, Any]) -> str:
        if not isinstance(invoice, dict):
            return ""
        return str(
            invoice.get("message_id")
            or invoice.get("gmail_message_id")
            or invoice.get("gmail_id")
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

    def _build_skill_request(
        self,
        *,
        intent: str,
        payload: Dict[str, Any],
    ) -> SkillRequest:
        normalized_intent = self._ensure_supported(intent)
        skill = self._skill_for_intent(normalized_intent)
        reference = self._item_reference(payload)
        try:
            _resolved_reference, resolved_item = self._resolve_ap_item_from_payload(payload)
            canonical_reference = str((resolved_item or {}).get("id") or "").strip()
            if canonical_reference:
                reference = canonical_reference
        except ValueError:
            pass
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

    def _resolve_ap_item_from_payload(
        self,
        payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, Dict[str, Any]]:
        candidates = self._item_reference_candidates(payload)
        if not candidates:
            raise ValueError("missing_item_reference")

        last_error: Optional[Exception] = None
        for reference in candidates:
            try:
                return reference, self._resolve_ap_item(reference)
            except (ValueError, LookupError, PermissionError) as exc:
                last_error = exc
                continue

        if isinstance(last_error, PermissionError):
            raise last_error
        if isinstance(last_error, LookupError):
            raise last_error
        raise ValueError("missing_item_reference")

    def resolve_ap_item_from_payload(
        self,
        payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, Dict[str, Any]]:
        return self._resolve_ap_item_from_payload(payload)

    def _correlation_id_for_item(self, item: Dict[str, Any]) -> Optional[str]:
        metadata = self._parse_json_dict(item.get("metadata"))
        correlation_id = str(item.get("correlation_id") or metadata.get("correlation_id") or "").strip()
        return correlation_id or None

    def correlation_id_for_item(self, item: Dict[str, Any]) -> Optional[str]:
        return self._correlation_id_for_item(item)

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

    def organization_settings(self) -> Dict[str, Any]:
        return self._organization_settings()

    @staticmethod
    def _initial_state_for_document(invoice: Dict[str, Any]) -> str:
        """Determine initial AP state based on document classification.

        Subscription notifications and receipts go straight to 'closed' —
        they are records of charges already made, not payables.
        """
        doc_type = str(
            invoice.get("document_type")
            or invoice.get("classification", {}).get("type", "")
            if isinstance(invoice.get("classification"), dict)
            else invoice.get("document_type", "")
        ).strip().lower()
        if doc_type in ("subscription_notification", "subscription", "receipt"):
            return "closed"
        workflow = str(invoice.get("workflow") or "").strip().lower()
        if workflow == "auto_record":
            return "closed"
        suggested = str(invoice.get("suggested_state") or "").strip().lower()
        if suggested == "closed":
            return "closed"
        return "received"

    def _seed_ap_item_for_invoice_processing(
        self,
        invoice: Dict[str, Any],
        *,
        correlation_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(invoice, dict) or not hasattr(self.db, "create_ap_item"):
            return None

        organization_id = (
            str(invoice.get("organization_id") or self.organization_id or "default").strip()
            or "default"
        )
        thread_id = self._invoice_thread_id(invoice)
        message_id = self._invoice_message_id(invoice)
        invoice_number = str(invoice.get("invoice_number") or "").strip() or None
        subject = str(invoice.get("subject") or "").strip() or "Invoice"
        sender = str(invoice.get("sender") or "").strip() or "unknown@unknown.local"
        vendor_name = self._resolved_vendor_name(invoice.get("vendor_name") or invoice.get("vendor"), sender)
        currency = str(invoice.get("currency") or "USD").strip() or "USD"
        due_date = str(invoice.get("due_date") or "").strip() or None
        attachment_url = str(invoice.get("attachment_url") or "").strip() or None
        attachment_count = max(0, self._safe_int(invoice.get("attachment_count"), 0))
        raw_attachment_names = invoice.get("attachment_names")
        attachment_names = (
            [str(value).strip() for value in raw_attachment_names if str(value or "").strip()]
            if isinstance(raw_attachment_names, list)
            else []
        )
        has_attachment = bool(invoice.get("has_attachment")) or attachment_count > 0 or bool(attachment_url) or bool(attachment_names)
        user_id = str(invoice.get("user_id") or self.actor_id or "").strip() or None

        try:
            amount = float(invoice.get("amount", 0.0) or 0.0)
        except (TypeError, ValueError):
            amount = 0.0
        try:
            confidence = float(invoice.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        existing = None
        if thread_id and hasattr(self.db, "get_ap_item_by_thread"):
            try:
                existing = self.db.get_ap_item_by_thread(organization_id, thread_id)
            except Exception:
                existing = None
        if not existing and message_id and hasattr(self.db, "get_ap_item_by_message_id"):
            try:
                existing = self.db.get_ap_item_by_message_id(organization_id, message_id)
            except Exception:
                existing = None

        metadata_updates = {
            "correlation_id": str(correlation_id or "").strip() or None,
            "intake_source": invoice.get("intake_source") or "gmail_autopilot",
            "document_type": invoice.get("document_type") or invoice.get("email_type") or "invoice",
            "email_type": invoice.get("email_type") or "invoice",
            "source_snippet": str(invoice.get("snippet") or "").strip() or None,
            "source_body_excerpt": str(invoice.get("body") or invoice.get("body_excerpt") or "").strip()[:4000] or None,
            "source_sender_domain": self._sender_domain(sender) or None,
            "has_attachment": has_attachment,
            "attachment_count": attachment_count,
        }
        if isinstance(invoice.get("field_confidences"), dict) and invoice.get("field_confidences"):
            metadata_updates["field_confidences"] = invoice.get("field_confidences")
        if isinstance(invoice.get("field_provenance"), dict) and invoice.get("field_provenance"):
            metadata_updates["field_provenance"] = invoice.get("field_provenance")
        if isinstance(invoice.get("field_evidence"), dict) and invoice.get("field_evidence"):
            metadata_updates["field_evidence"] = invoice.get("field_evidence")
        if isinstance(invoice.get("shadow_decision"), dict) and invoice.get("shadow_decision"):
            metadata_updates["shadow_decision"] = invoice.get("shadow_decision")
        if isinstance(invoice.get("source_conflicts"), list) and invoice.get("source_conflicts"):
            metadata_updates["source_conflicts"] = invoice.get("source_conflicts")
        if isinstance(invoice.get("conflict_actions"), list) and invoice.get("conflict_actions"):
            metadata_updates["conflict_actions"] = invoice.get("conflict_actions")
        if isinstance(invoice.get("confidence_gate"), dict) and invoice.get("confidence_gate"):
            metadata_updates["confidence_gate"] = invoice.get("confidence_gate")
        if isinstance(invoice.get("confidence_blockers"), list) and invoice.get("confidence_blockers"):
            metadata_updates["confidence_blockers"] = invoice.get("confidence_blockers")
        if isinstance(invoice.get("raw_parser"), dict) and invoice.get("raw_parser"):
            metadata_updates["raw_parser"] = invoice.get("raw_parser")
        if isinstance(invoice.get("attachment_manifest"), list) and invoice.get("attachment_manifest"):
            metadata_updates["attachment_manifest"] = invoice.get("attachment_manifest")
        for key in (
            "extraction_method",
            "extraction_model",
            "reasoning_summary",
            "payment_processor",
            "invoice_date",
            "primary_source",
            "exception_code",
            "exception_severity",
        ):
            value = invoice.get(key)
            if value:
                metadata_updates[key] = value
        if invoice.get("requires_extraction_review") is not None:
            metadata_updates["requires_extraction_review"] = bool(invoice.get("requires_extraction_review"))
        if invoice.get("requires_field_review") is not None:
            metadata_updates["requires_field_review"] = bool(invoice.get("requires_field_review"))
        if invoice.get("zero_amount_confirmed_by_attachment") is not None:
            metadata_updates["zero_amount_confirmed_by_attachment"] = bool(
                invoice.get("zero_amount_confirmed_by_attachment")
            )
        if attachment_names:
            metadata_updates["attachment_names"] = attachment_names
        if attachment_url:
            metadata_updates["attachment_url"] = attachment_url
        metadata_updates = {key: value for key, value in metadata_updates.items() if value}

        item = None
        if existing:
            updates: Dict[str, Any] = {}
            existing_metadata = self._parse_json_dict(existing.get("metadata"))
            merged_metadata = {**existing_metadata, **metadata_updates}
            if merged_metadata != existing_metadata:
                updates["metadata"] = merged_metadata
            if thread_id and str(existing.get("thread_id") or "").strip() != thread_id:
                updates["thread_id"] = thread_id
            if message_id and not str(existing.get("message_id") or "").strip():
                updates["message_id"] = message_id
            if subject and not str(existing.get("subject") or "").strip():
                updates["subject"] = subject
            if sender and not str(existing.get("sender") or "").strip():
                updates["sender"] = sender
            if vendor_name and not self._normalize_vendor_name(existing.get("vendor_name") or existing.get("vendor")):
                updates["vendor_name"] = vendor_name
            if invoice_number and not str(existing.get("invoice_number") or "").strip():
                updates["invoice_number"] = invoice_number
            if due_date and not str(existing.get("due_date") or "").strip():
                updates["due_date"] = due_date
            if attachment_url and not str(existing.get("attachment_url") or "").strip():
                updates["attachment_url"] = attachment_url
            if self._safe_float(existing.get("amount"), 0.0) <= 0.0 and amount > 0.0:
                updates["amount"] = amount
            if not str(existing.get("currency") or "").strip() and currency:
                updates["currency"] = currency
            if confidence > self._safe_float(existing.get("confidence"), 0.0):
                updates["confidence"] = confidence
            if isinstance(invoice.get("field_confidences"), dict) and invoice.get("field_confidences"):
                updates["field_confidences"] = invoice.get("field_confidences")
            if invoice.get("exception_code"):
                updates["exception_code"] = invoice.get("exception_code")
            if invoice.get("exception_severity"):
                updates["exception_severity"] = invoice.get("exception_severity")
            if updates and hasattr(self.db, "update_ap_item"):
                try:
                    self.db.update_ap_item(str(existing.get("id") or "").strip(), **updates)
                except Exception as exc:
                    logger.error("Failed to persist extraction updates for ap_item %s: %s", ap_item_id, exc)
            if hasattr(self.db, "get_ap_item"):
                try:
                    item = self.db.get_ap_item(str(existing.get("id") or "").strip())
                except Exception:
                    item = None
            if not item:
                item = {**existing, **updates}
                if "metadata" not in item:
                    item["metadata"] = merged_metadata
        else:
            invoice_key = None
            if invoice_number and vendor_name:
                invoice_key = f"{vendor_name}::{invoice_number}"
            elif thread_id:
                invoice_key = f"gmail-thread::{thread_id}"
            elif message_id:
                invoice_key = f"gmail-message::{message_id}"

            payload = {
                "invoice_key": invoice_key,
                "thread_id": thread_id or message_id,
                "message_id": message_id or None,
                "subject": subject,
                "sender": sender,
                "vendor_name": vendor_name or "Unknown vendor",
                "amount": amount,
                "currency": currency,
                "invoice_number": invoice_number,
                "due_date": due_date,
                "attachment_url": attachment_url,
                "state": self._initial_state_for_document(invoice),
                "confidence": confidence,
                "field_confidences": invoice.get("field_confidences") if isinstance(invoice.get("field_confidences"), dict) else None,
                "exception_code": invoice.get("exception_code"),
                "exception_severity": invoice.get("exception_severity"),
                "organization_id": organization_id,
                "user_id": user_id,
                "metadata": metadata_updates,
            }
            try:
                item = self.db.create_ap_item(payload)
            except Exception as exc:
                logger.warning("[FinanceAgentRuntime] failed to seed AP item for invoice: %s", exc)
                item = None

        if item and hasattr(self.db, "link_ap_item_source"):
            ap_item_id = str(item.get("id") or "").strip()
            if thread_id:
                try:
                    self.db.link_ap_item_source(
                        {
                            "ap_item_id": ap_item_id,
                            "source_type": "gmail_thread",
                            "source_ref": thread_id,
                            "subject": subject,
                            "sender": sender,
                            "metadata": {
                                "linked_by": "finance_agent_runtime",
                                "has_attachment": has_attachment,
                                "attachment_count": attachment_count,
                                "attachment_names": attachment_names,
                                "attachment_url": attachment_url,
                                "snippet": str(invoice.get("snippet") or "").strip() or None,
                                "body_excerpt": str(invoice.get("body") or invoice.get("body_excerpt") or "").strip()[:4000] or None,
                                "sender_domain": self._sender_domain(sender) or None,
                            },
                        }
                    )
                except Exception as exc:
                    logger.debug("Source link (thread) failed: %s", exc)
            if message_id:
                try:
                    self.db.link_ap_item_source(
                        {
                            "ap_item_id": ap_item_id,
                            "source_type": "gmail_message",
                            "source_ref": message_id,
                            "subject": subject,
                            "sender": sender,
                            "metadata": {
                                "linked_by": "finance_agent_runtime",
                                "has_attachment": has_attachment,
                                "attachment_count": attachment_count,
                                "attachment_names": attachment_names,
                                "attachment_url": attachment_url,
                                "snippet": str(invoice.get("snippet") or "").strip() or None,
                                "body_excerpt": str(invoice.get("body") or invoice.get("body_excerpt") or "").strip()[:4000] or None,
                                "sender_domain": self._sender_domain(sender) or None,
                            },
                        }
                    )
                except Exception as exc:
                    logger.debug("Source link (message) failed: %s", exc)

        return item

    def seed_ap_item_for_invoice_processing(
        self,
        invoice: Dict[str, Any],
        *,
        correlation_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self._seed_ap_item_for_invoice_processing(
            invoice,
            correlation_id=correlation_id,
        )

    def _merge_item_metadata(self, item: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        metadata = self._parse_json_dict(item.get("metadata"))
        metadata.update(updates or {})
        item["metadata"] = metadata
        ap_item_id = str(item.get("id") or "").strip()
        if ap_item_id and hasattr(self.db, "update_ap_item"):
            try:
                self.db.update_ap_item(ap_item_id, metadata=metadata)
            except Exception as exc:
                logger.error("Metadata merge persistence failed for %s: %s", ap_item_id, exc)
        return metadata

    def merge_item_metadata(self, item: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        return self._merge_item_metadata(item, updates)

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

    def append_runtime_audit(
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
        return self._append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type=event_type,
            reason=reason,
            metadata=metadata,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            skill_id=skill_id,
            evidence_refs=evidence_refs,
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

    def evaluate_prepare_vendor_followup(
        self,
        ap_item: Dict[str, Any],
        *,
        force: bool,
    ) -> Dict[str, Any]:
        return self._evaluate_prepare_vendor_followup(ap_item, force=force)

    def create_ap_action_context(
        self,
        payload: Optional[Dict[str, Any]] = None,
    ) -> APActionContext:
        reference, ap_item = self.resolve_ap_item_from_payload(payload)
        email_id = str(
            ap_item.get("thread_id")
            or ap_item.get("message_id")
            or (payload or {}).get("email_id")
            or reference
        )
        ap_item_id = str(ap_item.get("id") or reference)
        metadata = self.parse_json_dict(ap_item.get("metadata"))
        return APActionContext(
            reference=reference,
            ap_item=ap_item,
            ap_item_id=ap_item_id,
            email_id=email_id,
            metadata=metadata,
            correlation_id=self.correlation_id_for_item(ap_item),
        )

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
        return collect_operator_acceptance(self, ap_kpis)

    def _collect_connector_readiness(self) -> Dict[str, Any]:
        return collect_connector_readiness(self)

    @staticmethod
    def _evaluate_gate(
        *,
        gate_key: str,
        target: Optional[float],
        measured: Optional[float],
        metric_name: str,
    ) -> Dict[str, Any]:
        return evaluate_gate(
            gate_key=gate_key,
            target=target,
            measured=measured,
            metric_name=metric_name,
        )

    def skill_readiness(self, skill_id: str, *, window_hours: int = 168) -> Dict[str, Any]:
        return build_skill_readiness(self, skill_id, window_hours=window_hours)

    def _ap_kpis_snapshot(self) -> Dict[str, Any]:
        return ap_kpis_snapshot(self)

    @staticmethod
    def _readiness_gate_failures(readiness: Dict[str, Any]) -> List[str]:
        return readiness_gate_failures(readiness)

    @staticmethod
    def _extraction_drift_payload(ap_kpis: Dict[str, Any]) -> Dict[str, Any]:
        return runtime_extraction_drift_payload(ap_kpis)

    @staticmethod
    def _shadow_decision_payload(ap_kpis: Dict[str, Any]) -> Dict[str, Any]:
        return runtime_shadow_decision_payload(ap_kpis)

    @staticmethod
    def _post_action_verification_payload(ap_kpis: Dict[str, Any]) -> Dict[str, Any]:
        return runtime_post_action_verification_payload(ap_kpis)

    def _vendor_shadow_scorecard(
        self,
        vendor_name: Any,
        *,
        ap_kpis: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return runtime_vendor_shadow_scorecard(self, vendor_name, ap_kpis=ap_kpis)

    def _vendor_post_verification_scorecard(
        self,
        vendor_name: Any,
        *,
        ap_kpis: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return runtime_vendor_post_verification_scorecard(self, vendor_name, ap_kpis=ap_kpis)

    @staticmethod
    def _autonomy_action_thresholds() -> Dict[str, Dict[str, Any]]:
        return runtime_autonomy_action_thresholds()

    @staticmethod
    def _dedupe_reason_codes(codes: List[str]) -> List[str]:
        return runtime_dedupe_reason_codes(codes)

    def _item_finance_effect_policy(self, ap_item: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return runtime_item_finance_effect_policy(self, ap_item)

    def _autonomy_requested_action_dependencies(self, action: Any) -> tuple[str, ...]:
        return runtime_autonomy_requested_action_dependencies(action)

    def _evaluate_action_autonomy_policy(
        self,
        *,
        action: str,
        vendor: str,
        readiness: Dict[str, Any],
        failing_gates: List[str],
        scorecard: Optional[Dict[str, Any]],
        shadow_scorecard: Optional[Dict[str, Any]],
        verification_scorecard: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return runtime_evaluate_action_autonomy_policy(
            self,
            action=action,
            vendor=vendor,
            readiness=readiness,
            failing_gates=failing_gates,
            scorecard=scorecard,
            shadow_scorecard=shadow_scorecard,
            verification_scorecard=verification_scorecard,
        )

    def _evaluate_ap_vendor_autonomy(
        self,
        *,
        vendor_name: Any,
        readiness: Dict[str, Any],
        ap_kpis: Dict[str, Any],
    ) -> Dict[str, Any]:
        return runtime_evaluate_ap_vendor_autonomy(
            self,
            vendor_name=vendor_name,
            readiness=readiness,
            ap_kpis=ap_kpis,
        )

    def _build_shadow_decision_proposal(
        self,
        *,
        invoice: Dict[str, Any],
        vendor_name: Optional[str],
        amount: float,
        confidence: float,
        requires_field_review: bool,
        autonomy_policy: Dict[str, Any],
        auto_post_threshold: float,
    ) -> Dict[str, Any]:
        return runtime_build_shadow_decision_proposal(
            self,
            invoice=invoice,
            vendor_name=vendor_name,
            amount=amount,
            confidence=confidence,
            requires_field_review=requires_field_review,
            autonomy_policy=autonomy_policy,
            auto_post_threshold=auto_post_threshold,
        )

    def _vendor_drift_scorecard(
        self,
        vendor_name: Any,
        *,
        ap_kpis: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return runtime_vendor_drift_scorecard(self, vendor_name, ap_kpis=ap_kpis)

    def is_autonomous_request(self, payload: Optional[Dict[str, Any]] = None) -> bool:
        return runtime_is_autonomous_request(self, payload)

    def ap_autonomy_policy(
        self,
        *,
        vendor_name: Any = None,
        action: str = "route_low_risk_for_approval",
        autonomous_requested: bool = False,
        window_hours: int = 168,
        ap_item: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return runtime_ap_autonomy_policy(
            self,
            vendor_name=vendor_name,
            action=action,
            autonomous_requested=autonomous_requested,
            window_hours=window_hours,
            ap_item=ap_item,
        )

    def ap_autonomy_summary(self, *, window_hours: int = 168) -> Dict[str, Any]:
        return runtime_ap_autonomy_summary(self, window_hours=window_hours)

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

    def refresh_invoice_record_from_extraction(
        self,
        invoice_payload: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        *,
        correlation_id: Optional[str] = None,
        refresh_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Refresh canonical AP record fields from extraction without planner execution.

        Used by replay/backfill and repair flows that need deterministic field
        refresh but must not depend on planning skill registration.
        """
        invoice = invoice_payload if isinstance(invoice_payload, dict) else {}
        gmail_thread_id = self._invoice_thread_id(invoice)
        gmail_message_id = self._invoice_message_id(invoice)
        resolved_correlation_id = (
            str(correlation_id or "").strip()
            or str(invoice.get("correlation_id") or "").strip()
            or None
        )
        invoice_org = str(invoice.get("organization_id") or self.organization_id or "default").strip() or "default"
        attachment_list = attachments if isinstance(attachments, list) else []
        attachment_url = ""
        attachment_names: List[str] = []
        source_conflicts = invoice.get("source_conflicts") if isinstance(invoice.get("source_conflicts"), list) else []
        blocking_conflicts = [
            conflict for conflict in source_conflicts
            if isinstance(conflict, dict) and bool(conflict.get("blocking"))
        ]
        confidence_blockers = invoice.get("confidence_blockers") if isinstance(invoice.get("confidence_blockers"), list) else []
        if not confidence_blockers:
            gate = invoice.get("confidence_gate") if isinstance(invoice.get("confidence_gate"), dict) else {}
            confidence_blockers = gate.get("confidence_blockers") if isinstance(gate.get("confidence_blockers"), list) else []
        requires_field_review = bool(
            invoice.get("requires_field_review")
            or invoice.get("requires_extraction_review")
            or confidence_blockers
            or blocking_conflicts
        )
        vendor_name = self._resolved_vendor_name(
            invoice.get("vendor_name") or invoice.get("vendor"),
            invoice.get("sender"),
        )
        confidence_value = self._safe_float(invoice.get("confidence"))
        amount_value = self._safe_float(invoice.get("amount"))
        if attachment_list:
            first_attachment = attachment_list[0] if isinstance(attachment_list[0], dict) else {}
            attachment_url = str(
                first_attachment.get("url")
                or first_attachment.get("attachment_url")
                or ""
            ).strip()
            for attachment in attachment_list:
                if not isinstance(attachment, dict):
                    continue
                name = str(attachment.get("filename") or attachment.get("name") or "").strip()
                if name:
                    attachment_names.append(name)

        seeded_item = self._seed_ap_item_for_invoice_processing(
            {
                **invoice,
                "organization_id": invoice_org,
                "thread_id": gmail_thread_id or invoice.get("thread_id"),
                "message_id": gmail_message_id or invoice.get("message_id"),
                "attachment_url": attachment_url or invoice.get("attachment_url"),
                "attachment_count": len(attachment_list),
                "attachment_names": attachment_names,
                "has_attachment": bool(attachment_list),
                "requires_field_review": requires_field_review,
            },
            correlation_id=resolved_correlation_id,
        )
        autonomy_threshold = self.ap_auto_approve_threshold()
        autonomy_policy = self.ap_autonomy_policy(
            vendor_name=vendor_name,
            action="auto_approve_post",
            autonomous_requested=True,
            ap_item=seeded_item,
        )
        shadow_decision = self._build_shadow_decision_proposal(
            invoice=invoice,
            vendor_name=vendor_name,
            amount=amount_value,
            confidence=confidence_value,
            requires_field_review=requires_field_review,
            autonomy_policy=autonomy_policy,
            auto_post_threshold=autonomy_threshold,
        )

        if not seeded_item:
            return {
                "status": "error",
                "reason": "ap_item_seed_failed",
                "execution_mode": "extraction_refresh",
            }

        existing_metadata = self._parse_json_dict(seeded_item.get("metadata"))
        stale_runtime_failure = (
            str(
                existing_metadata.get("exception_code")
                or seeded_item.get("exception_code")
                or ""
            ).strip().lower() == "planner_failed"
            or str(existing_metadata.get("processing_status") or "").strip().lower() == "planner_failed"
            or str(
                existing_metadata.get("exception_code")
                or seeded_item.get("exception_code")
                or ""
            ).strip().lower() == "workflow_execution_failed"
            or str(existing_metadata.get("processing_status") or "").strip().lower() == "workflow_execution_failed"
            or "apskill not registered" in str(
                existing_metadata.get("planner_error")
                or seeded_item.get("last_error")
                or ""
            ).strip().lower()
            or bool(str(existing_metadata.get("workflow_error") or "").strip())
        )
        refresh_metadata = {
            "processing_status": "extraction_refreshed",
            "refresh_reason": str(refresh_reason or "replay_backfill").strip() or "replay_backfill",
            "extraction_refreshed_at": datetime.now(timezone.utc).isoformat(),
            "shadow_decision": shadow_decision,
            "autonomy_policy": autonomy_policy,
            "autonomy_mode": autonomy_policy.get("mode"),
        }
        if stale_runtime_failure:
            refresh_metadata.update(
                {
                    "exception_code": None,
                    "exception_severity": None,
                    "planner_error": None,
                    "workflow_error": None,
                }
            )
        ap_item_id = str(seeded_item.get("id") or "").strip()
        if stale_runtime_failure and ap_item_id and hasattr(self.db, "update_ap_item"):
            try:
                self.db.update_ap_item(
                    ap_item_id,
                    exception_code=None,
                    exception_severity=None,
                    last_error=None,
                )
            except Exception as exc:
                logger.debug("Stale exception clear failed: %s", exc)
        if ap_item_id and hasattr(self.db, "update_ap_item_metadata_merge"):
            try:
                self.db.update_ap_item_metadata_merge(ap_item_id, refresh_metadata)
            except Exception as exc:
                logger.debug("Metadata merge (refresh) failed: %s", exc)
        if ap_item_id and hasattr(self.db, "get_ap_item"):
            try:
                seeded_item = self.db.get_ap_item(ap_item_id) or seeded_item
            except Exception as exc:
                logger.debug("Item reload failed: %s", exc)

        return {
            "status": "refreshed",
            "execution_mode": "extraction_refresh",
            "ap_item_id": seeded_item.get("id"),
            "email_id": gmail_thread_id or gmail_message_id or seeded_item.get("thread_id"),
            "correlation_id": resolved_correlation_id,
        }

    async def execute_ap_invoice_processing(
        self,
        invoice_payload: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        *,
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await execute_runtime_invoice_processing(
            self,
            invoice_payload=invoice_payload,
            attachments=attachments,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

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
        return runtime_build_finance_lead_summary_payload(
            self,
            ap_item,
            audit_events=audit_events,
        )

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
        return await runtime_escalate_invoice_review(
            self,
            email_id=email_id,
            vendor=vendor,
            amount=amount,
            currency=currency,
            confidence=confidence,
            mismatches=mismatches,
            message=message,
            channel=channel,
        )

    async def share_finance_summary(
        self,
        *,
        reference_id: str,
        target: str = "email_draft",
        preview_only: bool = False,
        recipient_email: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await runtime_share_finance_summary(
            self,
            reference_id=reference_id,
            target=target,
            preview_only=preview_only,
            recipient_email=recipient_email,
            note=note,
        )

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
        return runtime_record_field_correction(
            self,
            ap_item_id=ap_item_id,
            field=field,
            original_value=original_value,
            corrected_value=corrected_value,
            feedback=feedback,
            actor_id=actor_id,
        )

    async def resume_pending_agent_tasks(self) -> Dict[str, int]:
        """Resume due retry jobs for this tenant through the canonical workflow path."""
        from clearledgr.services.agent_retry_jobs import drain_agent_retry_jobs

        return await drain_agent_retry_jobs(
            organization_id=self.organization_id,
            limit=25,
            worker_id_prefix="finance_agent_runtime_resume",
        )


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
