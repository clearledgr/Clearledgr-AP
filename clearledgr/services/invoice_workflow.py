"""
AP Workflow Service (PRD v1)

Handles:
- AP item creation from Gmail intake
- Validation and approval routing
- Slack approve/reject callbacks
- ERP posting and Gmail thread update
- Immutable audit trail
"""
from __future__ import annotations

import os
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from clearledgr.core.database import get_db
from clearledgr.services.ap_state import assert_valid_transition
from clearledgr.services.agent_runtime import get_agent_runtime
from clearledgr.services.browser_agent import get_browser_agent_service
from clearledgr.services.slack_api import SlackAPIClient, get_slack_client
from clearledgr.services.teams_api import TeamsAPIClient, get_teams_client
from clearledgr.services.policy_engine import evaluate_policy, normalize_ap_policy
from clearledgr.integrations.erp_router import Bill, post_bill, get_erp_connection
from clearledgr.services.gmail_api import GmailAPIClient
from clearledgr.workflows.ap.client import get_ap_temporal_client

logger = logging.getLogger(__name__)


@dataclass
class InvoiceData:
    gmail_id: str
    thread_id: str
    message_id: str
    subject: str
    sender: str
    vendor_name: str
    amount: Optional[float]
    currency: str = "USD"
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    confidence: float = 0.0
    organization_id: str = "default"
    user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


def compute_invoice_key(vendor: str, amount: Optional[float], invoice_number: Optional[str], due_date: Optional[str]) -> str:
    safe_vendor = (vendor or "").strip().lower()
    safe_number = (invoice_number or "").strip().lower()
    safe_amount = f"{amount:.2f}" if isinstance(amount, (int, float)) else "na"
    safe_due = (due_date or "").strip().lower()
    return "|".join([safe_vendor, safe_number, safe_amount, safe_due])


class InvoiceWorkflowService:
    def __init__(self, organization_id: str, slack_channel: Optional[str] = None):
        self.organization_id = organization_id or "default"
        self._slack_channel = slack_channel or os.getenv("SLACK_APPROVAL_CHANNEL", "#finance-approvals")
        self._teams_callback_url = os.getenv("TEAMS_ACTION_CALLBACK_URL", "").strip() or None
        self._approval_surface_default = os.getenv("AP_APPROVAL_SURFACE", "hybrid").strip().lower() or "hybrid"
        self._approval_policy_version = os.getenv("AP_APPROVAL_POLICY_VERSION", "2026.02")
        self._chat_only_threshold = float(os.getenv("AP_APPROVAL_THRESHOLD_CHAT_ONLY", "0") or 0)
        self._gmail_inline_allowed = str(os.getenv("AP_GMAIL_INLINE_APPROVAL_ALLOWED", "true")).strip().lower() not in {"0", "false", "no", "off"}
        self.db = get_db()
        self.agent_runtime = get_agent_runtime()
        self.browser_agent = get_browser_agent_service()
        self.temporal_client = get_ap_temporal_client()
        self._slack_client: Optional[SlackAPIClient] = None
        self._teams_client: Optional[TeamsAPIClient] = None

    @property
    def slack_client(self) -> SlackAPIClient:
        if self._slack_client is None:
            self._slack_client = get_slack_client()
        return self._slack_client

    @property
    def teams_client(self) -> TeamsAPIClient:
        if self._teams_client is None:
            self._teams_client = get_teams_client()
        return self._teams_client

    def _is_slack_available(self) -> bool:
        try:
            client = self.slack_client
            token = getattr(client, "bot_token", None)
            if token is None:
                return callable(getattr(client, "send_message", None))
            token = str(token).strip()
        except Exception:
            token = ""
        return bool(token)

    def _is_teams_available(self) -> bool:
        try:
            webhook = (self.teams_client.webhook_url or "").strip()
        except Exception:
            webhook = ""
        return bool(webhook)

    def _approval_channels(self) -> list[str]:
        raw = os.getenv("AP_APPROVAL_CHANNELS", "slack,teams")
        channels = [value.strip().lower() for value in raw.split(",") if value.strip()]
        if not channels:
            channels = ["slack"]
        return channels

    def _approval_channels_for_item(self, ap_item: Dict[str, Any]) -> list[str]:
        surface = str(ap_item.get("approval_surface") or self._approval_surface_default).strip().lower()
        amount = ap_item.get("amount")
        if surface == "gmail":
            return []
        if surface == "hybrid":
            if isinstance(amount, (int, float)) and amount < self._chat_only_threshold and self._gmail_inline_allowed:
                return []
            return self._approval_channels()
        if surface == "slack":
            return ["slack"]
        if surface == "teams":
            return ["teams"]
        return self._approval_channels()

    def _approval_required(self, amount: Optional[float]) -> bool:
        threshold = float(os.getenv("AP_APPROVAL_THRESHOLD", "0") or 0)
        if amount is None:
            return True
        return amount >= threshold

    @staticmethod
    def _parse_metadata(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _normalize_name(value: Optional[str]) -> str:
        return " ".join(str(value or "").strip().lower().split())

    @staticmethod
    def _amount_within_tolerance(left: Optional[float], right: Optional[float]) -> bool:
        if left is None or right is None:
            return True
        try:
            l = float(left)
            r = float(right)
        except (TypeError, ValueError):
            return True
        tolerance = max(1.0, max(abs(l), abs(r)) * 0.05)
        return abs(l - r) <= tolerance

    def _duplicate_conflicts(self, candidate: Dict[str, Any], invoice: InvoiceData) -> List[str]:
        conflicts: List[str] = []
        incoming_vendor = self._normalize_name(invoice.vendor_name)
        candidate_vendor = self._normalize_name(candidate.get("vendor_name"))
        if incoming_vendor and candidate_vendor and incoming_vendor != candidate_vendor:
            conflicts.append("vendor_mismatch")

        candidate_amount = candidate.get("amount")
        if not self._amount_within_tolerance(candidate_amount, invoice.amount):
            conflicts.append("amount_mismatch")

        incoming_invoice_number = self._normalize_name(invoice.invoice_number)
        candidate_invoice_number = self._normalize_name(candidate.get("invoice_number"))
        if incoming_invoice_number and candidate_invoice_number and incoming_invoice_number != candidate_invoice_number:
            conflicts.append("invoice_number_mismatch")

        return conflicts

    def _source_records_from_invoice(self, invoice: InvoiceData) -> List[Dict[str, Any]]:
        metadata = invoice.metadata if isinstance(invoice.metadata, dict) else {}
        records: List[Dict[str, Any]] = []
        if invoice.thread_id:
            records.append(
                {
                    "source_type": "gmail_thread",
                    "source_ref": str(invoice.thread_id),
                    "subject": invoice.subject,
                    "sender": invoice.sender,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {"gmail_id": invoice.gmail_id},
                }
            )
        if invoice.message_id:
            records.append(
                {
                    "source_type": "gmail_message",
                    "source_ref": str(invoice.message_id),
                    "subject": invoice.subject,
                    "sender": invoice.sender,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {"gmail_id": invoice.gmail_id},
                }
            )

        portal_refs: List[str] = []
        for key in ("invoice_portal_url", "vendor_portal_url", "portal_url"):
            value = str(metadata.get(key) or "").strip()
            if value:
                portal_refs.append(value)
        for portal_url in sorted(set(portal_refs)):
            records.append(
                {
                    "source_type": "portal",
                    "source_ref": portal_url,
                    "subject": invoice.subject,
                    "sender": invoice.sender,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {"discovered_from": "email_metadata"},
                }
            )

        connector_mappings = {
            "procurement": [
                "procurement_url",
                "procurement_ref",
                "po_url",
                "po_reference_url",
            ],
            "dms": [
                "dms_url",
                "document_repository_url",
                "document_url",
                "archive_url",
            ],
            "payment_portal": [
                "payment_portal_url",
                "payment_url",
                "remittance_portal_url",
            ],
        }
        for source_type, keys in connector_mappings.items():
            refs: List[str] = []
            for key in keys:
                value = metadata.get(key)
                if isinstance(value, list):
                    refs.extend([str(entry).strip() for entry in value if str(entry).strip()])
                else:
                    text = str(value or "").strip()
                    if text:
                        refs.append(text)
            for ref in sorted(set(refs)):
                records.append(
                    {
                        "source_type": source_type,
                        "source_ref": ref,
                        "subject": invoice.subject,
                        "sender": invoice.sender,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "metadata": {"discovered_from": "invoice_metadata"},
                    }
                )
        return records

    def _link_invoice_sources(self, ap_item: Dict[str, Any], invoice: InvoiceData) -> int:
        linked = 0
        for source in self._source_records_from_invoice(invoice):
            try:
                self.db.link_ap_item_source({"ap_item_id": ap_item["id"], **source})
                linked += 1
            except Exception:
                continue
        return linked

    def _external_refs(self, ap_item: Dict[str, Any], **extras: Any) -> Dict[str, Any]:
        refs = {
            "gmail_thread_id": ap_item.get("thread_id"),
            "gmail_message_id": ap_item.get("message_id"),
            "slack_message_ts": ap_item.get("slack_message_ts"),
            "erp_ref": ap_item.get("erp_reference"),
        }
        for key, value in extras.items():
            if value is not None:
                refs[key] = value
        return refs

    def _get_runtime_ids(self, ap_item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        metadata = self._parse_metadata(ap_item.get("metadata"))
        workflow_id = ap_item.get("workflow_id") or metadata.get("workflow_id")
        run_id = ap_item.get("run_id") or metadata.get("run_id")
        correlation_id = metadata.get("correlation_id")
        return workflow_id, run_id, correlation_id

    async def _signal_temporal(
        self,
        ap_item: Dict[str, Any],
        signal_name: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        workflow_id, run_id, correlation_id = self._get_runtime_ids(ap_item)
        if not workflow_id:
            return
        try:
            await self.temporal_client.signal(workflow_id, signal_name, payload or {})
        except Exception as exc:
            logger.warning(
                "Temporal signal failed (%s) for %s: %s",
                signal_name,
                ap_item.get("id"),
                exc,
            )
            self._append_audit(
                ap_item_id=ap_item["id"],
                event_type="workflow_signal_failed",
                from_state=ap_item.get("state"),
                to_state=ap_item.get("state"),
                actor_type="system",
                actor_id="temporal",
                reason=f"workflow_signal_failed:{signal_name}",
                metadata={"signal_name": signal_name, "error": str(exc)},
                idempotency_key=f"workflow_signal_failed:{ap_item['id']}:{signal_name}:{run_id or 'na'}",
                external_refs=self._external_refs(ap_item),
                source="workflow",
                workflow_id=workflow_id,
                run_id=run_id,
                correlation_id=correlation_id,
            )

    async def _ensure_temporal_run(
        self,
        ap_item: Dict[str, Any],
        command_name: str,
        payload: Optional[Dict[str, Any]] = None,
        actor_type: str = "system",
        actor_id: str = "workflow",
    ) -> Dict[str, Any]:
        envelope = await self.temporal_client.start_or_attach(
            organization_id=self.organization_id,
            ap_item_id=ap_item["id"],
            command_name=command_name,
            payload=payload or {},
            actor_type=actor_type,
            actor_id=actor_id,
            run_id=ap_item.get("run_id"),
        )
        metadata = self._parse_metadata(ap_item.get("metadata"))
        metadata = {
            **metadata,
            "workflow_id": envelope.workflow_id,
            "run_id": envelope.run_id,
            "correlation_id": envelope.correlation_id,
        }
        self.db.update_ap_item(
            ap_item["id"],
            workflow_id=envelope.workflow_id,
            run_id=envelope.run_id,
            metadata=metadata,
        )
        ap_item["workflow_id"] = envelope.workflow_id
        ap_item["run_id"] = envelope.run_id
        ap_item["metadata"] = metadata
        return ap_item

    def _find_rejected_match(
        self,
        invoice: InvoiceData,
        invoice_key_base: str,
        attachment_hashes: list[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if invoice.invoice_number:
            match = self.db.get_rejected_ap_item_by_vendor_invoice(
                self.organization_id, invoice.vendor_name, invoice.invoice_number
            )
            if match:
                return match, "vendor_invoice"
        existing = self.db.get_ap_item_by_invoice_key(self.organization_id, invoice_key_base)
        if existing and existing.get("state") == "rejected":
            return existing, "invoice_key"
        if attachment_hashes:
            rejected_items = self.db.list_ap_items(self.organization_id, state="rejected", limit=200)
            for item in rejected_items:
                metadata = self._parse_metadata(item.get("metadata"))
                hashes = set(metadata.get("attachment_hashes") or [])
                if hashes.intersection(set(attachment_hashes)):
                    return item, "attachment_hash"
        return None, None

    def _find_active_duplicate(
        self,
        invoice: InvoiceData,
        invoice_key_base: str,
        attachment_hashes: list[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str], List[str]]:
        existing = self.db.get_ap_item_by_invoice_key(self.organization_id, invoice_key_base)
        if existing and existing.get("state") != "rejected":
            conflicts = self._duplicate_conflicts(existing, invoice)
            if conflicts:
                return None, "invoice_key", conflicts
            return existing, "invoice_key", []
        resubmissions = self.db.list_ap_items_by_invoice_key_prefix(
            self.organization_id, f"{invoice_key_base}::resubmission::", limit=50
        )
        for item in resubmissions:
            if item.get("state") != "rejected":
                conflicts = self._duplicate_conflicts(item, invoice)
                if conflicts:
                    return None, "invoice_key_resubmission", conflicts
                return item, "invoice_key_resubmission", []
        if invoice.invoice_number:
            match = self.db.get_ap_item_by_vendor_invoice(
                self.organization_id, invoice.vendor_name, invoice.invoice_number
            )
            if match and match.get("state") != "rejected":
                conflicts = self._duplicate_conflicts(match, invoice)
                if conflicts:
                    return None, "invoice_number", conflicts
                return match, "invoice_number", []
        if attachment_hashes:
            active_items = self.db.list_ap_items(self.organization_id, limit=200)
            for item in active_items:
                if item.get("state") == "rejected":
                    continue
                metadata = self._parse_metadata(item.get("metadata"))
                hashes = set(metadata.get("attachment_hashes") or [])
                if hashes.intersection(set(attachment_hashes)):
                    conflicts = self._duplicate_conflicts(item, invoice)
                    if conflicts:
                        return None, "attachment_hash", conflicts
                    return item, "attachment_hash", []
        return None, None, []

    def _historical_vendor_average(self, vendor_name: Optional[str]) -> Optional[float]:
        vendor = (vendor_name or "").strip()
        if not vendor:
            return None
        items = self.db.list_ap_items(self.organization_id, limit=500)
        amounts = []
        for item in items:
            if (item.get("vendor_name") or "").strip().lower() != vendor.lower():
                continue
            amount = item.get("amount")
            if isinstance(amount, (int, float)) and amount > 0:
                amounts.append(float(amount))
        if not amounts:
            return None
        return sum(amounts) / len(amounts)

    @staticmethod
    def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _discount_signal(self, metadata: Dict[str, Any], currency: str) -> Dict[str, Any]:
        discount = self._as_dict(metadata.get("discount") or metadata.get("payment_discount"))
        available = bool(discount.get("available") or discount.get("eligible"))
        amount = self._safe_float(discount.get("amount"), 0.0) or 0.0
        deadline = discount.get("deadline") or discount.get("due_at")
        if amount > 0:
            available = True
        return {
            "available": bool(available),
            "deadline": deadline,
            "potential_savings": round(max(0.0, amount), 2),
            "currency": str(discount.get("currency") or currency or "USD"),
        }

    def _late_payment_risk(self, due_date: Any, requires_human_review: bool = False) -> Dict[str, Any]:
        due_dt = self._parse_datetime(due_date)
        if not due_dt:
            return {"level": "unknown", "days_to_due": None, "due_date": due_date}
        days_to_due = round((due_dt - datetime.now(timezone.utc)).total_seconds() / 86400.0, 2)
        level = "low"
        if days_to_due < 0:
            level = "high"
        elif days_to_due <= 2:
            level = "high" if requires_human_review else "medium"
        elif days_to_due <= 5:
            level = "medium"
        return {"level": level, "days_to_due": days_to_due, "due_date": due_dt.isoformat()}

    def _score_source_quality(self, source: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        source_type = str(source.get("source_type") or "").strip().lower()
        base_weights = {
            "erp": 0.95,
            "procurement": 0.9,
            "gmail_message": 0.9,
            "gmail_thread": 0.86,
            "payment_portal": 0.84,
            "portal": 0.82,
            "dms": 0.8,
            "slack": 0.72,
            "teams": 0.72,
        }
        score = float(base_weights.get(source_type, 0.7))
        if source.get("subject"):
            score += 0.04
        if source.get("sender"):
            score += 0.03

        detected_at = self._parse_datetime(source.get("detected_at"))
        age_seconds: Optional[int] = None
        if detected_at:
            age_seconds = max(0, int((now - detected_at).total_seconds()))
            if age_seconds <= 3600:
                score += 0.06
            elif age_seconds <= 86400:
                score += 0.03
            elif age_seconds > 7 * 86400:
                score -= 0.07
        else:
            score -= 0.05

        bounded = max(0.0, min(1.0, score))
        label = "high" if bounded >= 0.8 else "medium" if bounded >= 0.6 else "low"
        return {
            "source_type": source_type or "unknown",
            "source_ref": source.get("source_ref"),
            "score": round(bounded, 3),
            "label": label,
            "age_seconds": age_seconds,
            "detected_at": source.get("detected_at"),
        }

    def _summarize_source_quality(self, sources: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
        ranked = [self._score_source_quality(source, now) for source in sources]
        ranked.sort(key=lambda entry: (-float(entry.get("score") or 0.0), str(entry.get("source_type") or "")))
        average_score = (
            round(sum(float(entry.get("score") or 0.0) for entry in ranked) / len(ranked), 3)
            if ranked
            else 0.0
        )
        return {
            "avg_score": average_score,
            "items": ranked,
            "top_sources": ranked[:5],
            "high_quality_count": sum(1 for entry in ranked if entry.get("label") == "high"),
        }

    @staticmethod
    def _as_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _severity_rank(severity: str) -> int:
        normalized = str(severity or "").strip().lower()
        if normalized == "critical":
            return 4
        if normalized == "high":
            return 3
        if normalized == "medium":
            return 2
        if normalized == "low":
            return 1
        return 0

    @staticmethod
    def _priority_score(severity: str, state: str) -> float:
        base = float(InvoiceWorkflowService._severity_rank(severity) * 100)
        normalized_state = str(state or "").strip().lower()
        if normalized_state == "needs_info":
            base += 40.0
        elif normalized_state == "needs_approval":
            base += 30.0
        elif normalized_state == "failed_post":
            base += 45.0
        return base

    def _resolve_ap_policy(self) -> Dict[str, Any]:
        stored = self.db.get_ap_policy(self.organization_id, "ap_business_v1")
        if stored and stored.get("enabled"):
            config = stored.get("config_json") if isinstance(stored.get("config_json"), dict) else {}
            policy = normalize_ap_policy(config)
            policy["version"] = stored.get("version")
            policy["name"] = stored.get("policy_name") or "ap_business_v1"
            policy["source"] = "tenant"
            return policy
        policy = normalize_ap_policy({})
        policy["version"] = "env-default"
        policy["name"] = "ap_business_v1"
        policy["source"] = "env"
        return policy

    def _evaluate_po_match(
        self,
        invoice: InvoiceData,
        metadata: Dict[str, Any],
        policy: Dict[str, Any],
    ) -> Dict[str, Any]:
        validation = self._as_dict(policy.get("validation"))
        po_threshold = validation.get("po_match_required_over")
        require_po = False
        if po_threshold is not None and invoice.amount is not None:
            threshold = self._safe_float(po_threshold, 0.0) or 0.0
            require_po = float(invoice.amount) >= threshold
        elif self._as_bool(validation.get("po_match_required"), False):
            require_po = True
        if self._as_bool(metadata.get("require_po_check"), False):
            require_po = True

        po_context = {}
        for key in ("po_match", "po", "purchase_order"):
            po_context = self._as_dict(metadata.get(key))
            if po_context:
                break
        po_number = (
            po_context.get("po_number")
            or po_context.get("po_reference")
            or metadata.get("po_number")
            or metadata.get("po_reference")
        )
        expected_amount = self._safe_float(
            po_context.get("expected_amount")
            or po_context.get("po_amount")
            or po_context.get("amount")
        )
        require_receipt = self._as_bool(validation.get("require_receipt"), False)
        receipt_received = self._as_bool(
            po_context.get("receipt_received")
            if "receipt_received" in po_context
            else po_context.get("receipt_matched"),
            default=True,
        )

        status = "not_requested"
        amount_diff = None
        if require_po:
            if not po_number:
                status = "missing_po_reference"
            elif expected_amount is not None and invoice.amount is not None:
                tolerance_pct = self._safe_float(validation.get("po_amount_tolerance_pct"), 0.05) or 0.05
                tolerance_abs = self._safe_float(validation.get("po_amount_tolerance_abs"), 1.0) or 1.0
                tolerance = max(float(tolerance_abs), abs(float(expected_amount)) * float(tolerance_pct))
                amount_diff = abs(float(invoice.amount) - float(expected_amount))
                if amount_diff > tolerance:
                    status = "amount_mismatch"
                elif require_receipt and not receipt_received:
                    status = "receipt_missing"
                else:
                    status = "matched"
            elif require_receipt and not receipt_received:
                status = "receipt_missing"
            else:
                status = "matched"

        return {
            "required": bool(require_po),
            "status": status,
            "po_number": po_number,
            "expected_amount": expected_amount,
            "invoice_amount": invoice.amount,
            "amount_difference": amount_diff,
            "receipt_required": bool(require_receipt),
            "receipt_received": bool(receipt_received),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def _evaluate_budget_check(
        self,
        invoice: InvoiceData,
        metadata: Dict[str, Any],
        policy: Dict[str, Any],
    ) -> Dict[str, Any]:
        validation = self._as_dict(policy.get("validation"))
        budget_threshold = validation.get("budget_check_required_over")
        require_budget = False
        if budget_threshold is not None and invoice.amount is not None:
            threshold = self._safe_float(budget_threshold, 0.0) or 0.0
            require_budget = float(invoice.amount) >= threshold
        if self._as_bool(validation.get("require_budget_context"), False):
            require_budget = True
        if self._as_bool(metadata.get("require_budget_check"), False):
            require_budget = True

        budget = {}
        for key in ("budget", "budget_context", "spend_budget"):
            budget = self._as_dict(metadata.get(key))
            if budget:
                break
        remaining = self._safe_float(
            budget.get("remaining")
            or budget.get("remaining_budget")
            or budget.get("available")
            or budget.get("available_budget")
        )
        limit = self._safe_float(budget.get("limit") or budget.get("budget_limit"))
        spent = self._safe_float(budget.get("spent") or budget.get("used"))
        currency = budget.get("currency") or invoice.currency or "USD"

        status = "not_requested"
        overage = 0.0
        if require_budget:
            if remaining is None and limit is None:
                status = "missing_budget_context"
            else:
                effective_remaining = remaining
                if effective_remaining is None and limit is not None and spent is not None:
                    effective_remaining = float(limit) - float(spent)
                if invoice.amount is None:
                    status = "missing_invoice_amount"
                elif effective_remaining is None:
                    status = "missing_budget_context"
                elif float(invoice.amount) > float(effective_remaining):
                    overage = float(invoice.amount) - float(effective_remaining)
                    status = "over_budget"
                else:
                    status = "within_budget"

        return {
            "required": bool(require_budget),
            "status": status,
            "remaining": remaining,
            "limit": limit,
            "spent": spent,
            "invoice_amount": invoice.amount,
            "overage": round(overage, 2),
            "currency": currency,
            "period": budget.get("period") or budget.get("window"),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def _classify_exception(
        self,
        *,
        missing_fields: List[str],
        po_result: Dict[str, Any],
        budget_result: Dict[str, Any],
        policy_issues: List[str],
        policy: Dict[str, Any],
    ) -> Dict[str, Any]:
        status_to_code = {
            "missing_po_reference": "po_missing_reference",
            "amount_mismatch": "po_amount_mismatch",
            "receipt_missing": "receipt_missing",
            "over_budget": "budget_overrun",
            "missing_budget_context": "missing_budget_context",
        }
        code = ""
        status_code = status_to_code.get(str(po_result.get("status") or ""))
        if status_code:
            code = status_code
        if not code:
            status_code = status_to_code.get(str(budget_result.get("status") or ""))
            if status_code:
                code = status_code
        if not code and policy_issues:
            code = "policy_validation_failed"
        if not code and missing_fields:
            code = "missing_fields"

        severity_map = self._as_dict(policy.get("exception_severity"))
        severity = str(severity_map.get(code) or ("medium" if code else "low")).strip().lower()
        if severity not in {"critical", "high", "medium", "low"}:
            severity = "medium"

        return {
            "exception_code": code or None,
            "exception_severity": severity if code else None,
            "priority_score": self._priority_score(severity, "needs_info" if missing_fields else "needs_approval")
            if code
            else 0.0,
        }

    @staticmethod
    def _budget_summary(budget_result: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(budget_result, dict):
            return {}
        status = str(budget_result.get("status") or "")
        summary: Dict[str, Any] = {
            "status": status,
            "remaining": budget_result.get("remaining"),
            "overage": budget_result.get("overage"),
            "currency": budget_result.get("currency"),
            "required": bool(budget_result.get("required")),
        }
        return summary

    def _append_audit(
        self,
        ap_item_id: str,
        event_type: str,
        from_state: Optional[str],
        to_state: Optional[str],
        actor_type: str,
        actor_id: str,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        external_refs: Optional[Dict[str, Any]] = None,
        source: str = "workflow",
        workflow_id: Optional[str] = None,
        run_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        decision_reason: Optional[str] = None,
    ) -> None:
        payload = {"reason": reason}
        if metadata:
            payload.update(metadata)
        ap_item = self.db.get_ap_item(ap_item_id)
        if ap_item:
            item_workflow_id, item_run_id, item_correlation_id = self._get_runtime_ids(ap_item)
            workflow_id = workflow_id or item_workflow_id
            run_id = run_id or item_run_id
            correlation_id = correlation_id or item_correlation_id
        self.db.append_ap_audit_event({
            "ap_item_id": ap_item_id,
            "event_type": event_type,
            "from_state": from_state,
            "to_state": to_state,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "payload_json": payload,
            "external_refs": external_refs or {},
            "idempotency_key": idempotency_key,
            "source": source,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "correlation_id": correlation_id,
            "decision_reason": decision_reason,
            "organization_id": self.organization_id,
        })

    def _transition(
        self,
        ap_item: Dict[str, Any],
        to_state: str,
        actor_type: str,
        actor_id: str,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        current_state = ap_item.get("state")
        if current_state == to_state:
            return ap_item
        if idempotency_key and self.db.get_ap_audit_event_by_key(idempotency_key):
            return ap_item
        assert_valid_transition(current_state, to_state)
        self.db.update_ap_item(ap_item["id"], state=to_state)
        self._append_audit(
            ap_item_id=ap_item["id"],
            event_type="state_transition",
            from_state=current_state,
            to_state=to_state,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            metadata=metadata,
            idempotency_key=idempotency_key,
            external_refs=self._external_refs(ap_item),
        )
        ap_item["state"] = to_state
        return ap_item

    async def process_new_invoice(self, invoice: InvoiceData) -> Dict[str, Any]:
        invoice_key_base = compute_invoice_key(
            invoice.vendor_name, invoice.amount, invoice.invoice_number, invoice.due_date
        )
        metadata = invoice.metadata or {}
        if not isinstance(metadata, dict):
            metadata = {}
        run_id = str(metadata.get("run_id") or f"run_{invoice.message_id or uuid.uuid4().hex}")
        correlation_id = str(metadata.get("correlation_id") or f"corr_{uuid.uuid4().hex}")
        metadata = {**metadata, "run_id": run_id, "correlation_id": correlation_id}
        attachment_hashes = metadata.get("attachment_hashes") or []
        agent_result = await self.agent_runtime.analyze(
            {
                "organization_id": self.organization_id,
                "thread_id": invoice.thread_id,
                "message_id": invoice.message_id,
                "subject": invoice.subject,
                "sender": invoice.sender,
                "vendor_name": invoice.vendor_name,
                "amount": invoice.amount,
                "currency": invoice.currency,
                "invoice_number": invoice.invoice_number,
                "invoice_date": invoice.invoice_date,
                "due_date": invoice.due_date,
                "confidence": invoice.confidence,
                "metadata": metadata,
            }
        )
        metadata = {
            **metadata,
            "agent_trace": agent_result.trace,
            "agent_validation": agent_result.validation,
            "agent_posting_plan": agent_result.posting_plan,
            "agent_browser_commands": agent_result.browser_commands,
        }
        approval_surface = str(
            agent_result.approval_routing.get("surface")
            or metadata.get("approval_surface")
            or self._approval_surface_default
        ).strip().lower() or self._approval_surface_default

        duplicate, merge_reason_key, merge_conflicts = self._find_active_duplicate(
            invoice, invoice_key_base, attachment_hashes
        )
        if duplicate:
            duplicate_meta = self._parse_metadata(duplicate.get("metadata"))
            known_hashes = set(duplicate_meta.get("attachment_hashes") or [])
            known_hashes.update(set(attachment_hashes or []))
            duplicate_meta["attachment_hashes"] = sorted(known_hashes)
            duplicate_meta["merge_reason"] = merge_reason_key or "invoice_match"
            duplicate_meta["has_context_conflict"] = False
            duplicate_meta["last_merged_at"] = datetime.now(timezone.utc).isoformat()
            self.db.update_ap_item(duplicate["id"], metadata=duplicate_meta)
            duplicate["metadata"] = duplicate_meta

            linked_sources = self._link_invoice_sources(duplicate, invoice)
            self._append_audit(
                ap_item_id=duplicate["id"],
                event_type="duplicate_detected",
                from_state=duplicate.get("state"),
                to_state=duplicate.get("state"),
                actor_type="system",
                actor_id="duplicate_detector",
                reason="duplicate_invoice",
                metadata={
                    "invoice_key": invoice_key_base,
                    "merge_reason": merge_reason_key,
                    "linked_sources": linked_sources,
                },
                idempotency_key=f"dup:{duplicate['id']}:{invoice_key_base}:{invoice.message_id or invoice.gmail_id}",
                external_refs=self._external_refs(duplicate),
            )
            return {
                "status": "duplicate",
                "ap_item": duplicate,
                "merge_reason": merge_reason_key,
                "source_count": len(self.db.list_ap_item_sources(duplicate["id"])),
            }

        has_merge_conflict = bool(merge_reason_key and merge_conflicts)
        if has_merge_conflict:
            metadata = {
                **metadata,
                "has_context_conflict": True,
                "merge_reason": merge_reason_key,
                "merge_conflicts": merge_conflicts,
            }

        rejected_match, rejected_reason = self._find_rejected_match(
            invoice, invoice_key_base, attachment_hashes
        )
        invoice_key = invoice_key_base
        if rejected_match:
            invoice_key = f"{invoice_key_base}::resubmission::{rejected_match['id']}"
            metadata = {
                **metadata,
                "supersedes_ap_item_id": rejected_match.get("id"),
                "supersedes_invoice_key": rejected_match.get("invoice_key"),
                "resubmission_reason": rejected_reason,
            }

        ap_item = self.db.create_ap_item({
            "invoice_key": invoice_key,
            "thread_id": invoice.thread_id,
            "message_id": invoice.message_id,
            "subject": invoice.subject,
            "sender": invoice.sender,
            "vendor_name": invoice.vendor_name,
            "amount": invoice.amount,
            "currency": invoice.currency,
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "state": "received",
            "confidence": invoice.confidence,
            "approval_required": self._approval_required(invoice.amount),
            "approval_surface": approval_surface,
            "approval_policy_version": self._approval_policy_version,
            "run_id": run_id,
            "organization_id": self.organization_id,
            "metadata": metadata,
        })
        ap_item = await self._ensure_temporal_run(
            ap_item,
            "intake",
            payload={"initial_state": "received", "approval_surface": approval_surface},
            actor_type="agent",
            actor_id="intake_workflow",
        )
        linked_sources = self._link_invoice_sources(ap_item, invoice)
        self._append_audit(
            ap_item_id=ap_item["id"],
            event_type="item_created",
            from_state=None,
            to_state="received",
            actor_type="system",
            actor_id="workflow",
            reason="ap_item_created",
            metadata={
                "invoice_key": invoice_key,
                "run_id": run_id,
                "approval_surface": approval_surface,
                "approval_policy_version": self._approval_policy_version,
                "linked_sources": linked_sources,
            },
            idempotency_key=f"item_created:{ap_item['id']}",
            external_refs=self._external_refs(ap_item),
            source="intake",
            correlation_id=correlation_id,
        )
        if has_merge_conflict:
            self._append_audit(
                ap_item_id=ap_item["id"],
                event_type="merge_conflict_detected",
                from_state="received",
                to_state="received",
                actor_type="system",
                actor_id="duplicate_detector",
                reason="merge_conflict_detected",
                metadata={"merge_reason": merge_reason_key, "conflicts": merge_conflicts},
                idempotency_key=f"merge_conflict:{ap_item['id']}:{merge_reason_key}",
                external_refs=self._external_refs(ap_item),
            )
        self._append_audit(
            ap_item_id=ap_item["id"],
            event_type="agent_reasoned",
            from_state="received",
            to_state="received",
            actor_type="agent",
            actor_id="agent_runtime",
            reason="agent_reasoning_complete",
            metadata={
                "approval_surface": approval_surface,
                "trace": agent_result.trace,
                "validation": agent_result.validation,
                "approval_routing": agent_result.approval_routing,
                "posting_plan": agent_result.posting_plan,
                "browser_commands": agent_result.browser_commands,
            },
            idempotency_key=f"agent_reasoned:{ap_item['id']}:{run_id}",
            external_refs=self._external_refs(ap_item),
            source="agent",
            workflow_id=ap_item.get("workflow_id"),
            run_id=run_id,
            correlation_id=correlation_id,
        )
        if agent_result.browser_commands:
            try:
                session = self.browser_agent.create_session(
                    organization_id=self.organization_id,
                    ap_item_id=ap_item["id"],
                    created_by="agent_runtime",
                    metadata={"run_id": run_id, "correlation_id": correlation_id},
                )
                metadata = self._parse_metadata(ap_item.get("metadata"))
                metadata["agent_session_id"] = session.get("id")
                self.db.update_ap_item(ap_item["id"], metadata=metadata)
                ap_item["metadata"] = metadata

                ordered_commands = []
                for idx, command in enumerate(agent_result.browser_commands):
                    if not isinstance(command, dict):
                        continue
                    raw_sequence = command.get("sequence")
                    try:
                        sequence = int(raw_sequence)
                    except Exception:
                        sequence = idx + 1
                    ordered_commands.append((sequence, idx, command))

                for idx, (_, _, command) in enumerate(sorted(ordered_commands, key=lambda item: (item[0], item[1]))):
                    cmd_payload = {
                        **command,
                        "command_id": command.get("command_id") or f"agent_cmd_{idx+1}",
                        "correlation_id": command.get("correlation_id") or correlation_id,
                    }
                    self.browser_agent.enqueue_command(
                        session_id=session["id"],
                        command=cmd_payload,
                        actor_id="agent_runtime",
                        confirm=False,
                    )
            except Exception as exc:
                self._append_audit(
                    ap_item_id=ap_item["id"],
                    event_type="browser_session_failed",
                    from_state=ap_item.get("state"),
                    to_state=ap_item.get("state"),
                    actor_type="system",
                    actor_id="browser_agent",
                    reason="browser_session_init_failed",
                    metadata={"error": str(exc)},
                    idempotency_key=f"browser_session_failed:{ap_item['id']}:{run_id}",
                    external_refs=self._external_refs(ap_item),
                )
        if rejected_match:
            self._append_audit(
                ap_item_id=ap_item["id"],
                event_type="resubmission_created",
                from_state=None,
                to_state="received",
                actor_type="system",
                actor_id="resubmission",
                reason="resubmitted_after_rejection",
                metadata={
                    "supersedes_ap_item_id": rejected_match.get("id"),
                    "supersedes_invoice_key": rejected_match.get("invoice_key"),
                },
                idempotency_key=f"resubmission:{ap_item['id']}:{rejected_match.get('id')}",
                external_refs=self._external_refs(ap_item),
            )

        self._append_audit(
            ap_item_id=ap_item["id"],
            event_type="invoice_detected",
            from_state=None,
            to_state="received",
            actor_type="system",
            actor_id="gmail_intake",
            reason="email_received",
            metadata={"subject": invoice.subject, "sender": invoice.sender},
            idempotency_key=f"received:{ap_item['id']}",
            external_refs=self._external_refs(ap_item),
        )
        self._append_audit(
            ap_item_id=ap_item["id"],
            event_type="fields_extracted",
            from_state="received",
            to_state="received",
            actor_type="system",
            actor_id="extractor",
            reason="extraction_complete",
            metadata={"extraction": metadata.get("raw", {})},
            idempotency_key=f"extracted:{ap_item['id']}",
            external_refs=self._external_refs(ap_item),
        )

        ap_item = self._transition(
            ap_item,
            "validated",
            actor_type="system",
            actor_id="validator",
            reason="validated",
            idempotency_key=f"validated:{ap_item['id']}",
        )

        missing: List[str] = []
        if not invoice.vendor_name:
            missing.append("vendor_name")
        if invoice.amount is None:
            missing.append("amount")
        if not invoice.invoice_number:
            missing.append("invoice_number")

        ap_policy = self._resolve_ap_policy()
        validation_policy = self._as_dict(ap_policy.get("validation"))
        policy_metadata = dict(metadata)
        historical_vendor_average = self._historical_vendor_average(invoice.vendor_name)
        if historical_vendor_average is not None:
            policy_metadata["historical_vendor_average"] = historical_vendor_average

        po_result = self._evaluate_po_match(invoice, policy_metadata, ap_policy)
        budget_result = self._evaluate_budget_check(invoice, policy_metadata, ap_policy)
        budget_status = str(budget_result.get("status") or "")
        po_status = str(po_result.get("status") or "")
        block_on_budget_overrun = self._as_bool(validation_policy.get("block_on_budget_overrun"), False)

        blocking_control_issues: List[str] = []
        non_blocking_control_issues: List[str] = []
        po_status_to_code = {
            "missing_po_reference": "po_missing_reference",
            "amount_mismatch": "po_amount_mismatch",
            "receipt_missing": "receipt_missing",
        }
        po_code = po_status_to_code.get(po_status)
        if po_code:
            blocking_control_issues.append(po_code)

        if budget_status == "over_budget":
            if block_on_budget_overrun:
                blocking_control_issues.append("budget_overrun")
            else:
                non_blocking_control_issues.append("budget_overrun")
        elif budget_result.get("required") and budget_status in {"missing_budget_context", "missing_invoice_amount"}:
            blocking_control_issues.append("missing_budget_context")

        policy_decision = evaluate_policy(
            vendor_name=invoice.vendor_name,
            amount=invoice.amount,
            invoice_number=invoice.invoice_number,
            metadata=policy_metadata,
            tenant_policy=ap_policy if ap_policy.get("source") == "tenant" else None,
        )
        policy_issues = list(policy_decision.issues or [])
        if policy_issues:
            blocking_control_issues.extend(policy_issues)

        for issue in blocking_control_issues:
            if issue not in missing:
                missing.append(issue)

        exception_inputs = list(dict.fromkeys(missing + non_blocking_control_issues))
        exception_info = self._classify_exception(
            missing_fields=exception_inputs,
            po_result=po_result,
            budget_result=budget_result,
            policy_issues=policy_issues,
            policy=ap_policy,
        )
        discount_signal = self._discount_signal(policy_metadata, invoice.currency or "USD")
        late_payment_risk = self._late_payment_risk(
            invoice.due_date,
            requires_human_review=bool(exception_info.get("exception_code")),
        )
        risk_signals = {
            "po_issue": po_status not in {"matched", "not_requested"},
            "budget_issue": budget_status in {"over_budget", "missing_budget_context", "missing_invoice_amount"},
            "policy_issue_count": len(policy_issues),
            "requires_human_review": bool(exception_info.get("exception_code")),
            "amount_anomaly": "amount_anomaly" in policy_issues,
            "discount_opportunity": discount_signal,
            "late_payment_risk": late_payment_risk,
        }

        updated_metadata = self._parse_metadata(ap_item.get("metadata"))
        updated_metadata.update(
            {
                "policy": {
                    "name": ap_policy.get("name"),
                    "version": ap_policy.get("version"),
                },
                "po_match_result": po_result,
                "budget_check_result": budget_result,
                "budget_status": budget_status or "not_requested",
                "risk_signals": risk_signals,
            }
        )
        if exception_info.get("exception_code"):
            updated_metadata["exception_code"] = exception_info.get("exception_code")
            updated_metadata["exception_severity"] = exception_info.get("exception_severity")
            updated_metadata["priority_score"] = exception_info.get("priority_score")
        else:
            updated_metadata.pop("exception_code", None)
            updated_metadata.pop("exception_severity", None)
            updated_metadata.pop("priority_score", None)
        self.db.update_ap_item(ap_item["id"], metadata=updated_metadata)
        ap_item["metadata"] = updated_metadata
        ap_item["exception_code"] = updated_metadata.get("exception_code")
        ap_item["exception_severity"] = updated_metadata.get("exception_severity")
        ap_item["priority_score"] = updated_metadata.get("priority_score")

        self._append_audit(
            ap_item_id=ap_item["id"],
            event_type="controls_evaluated",
            from_state="validated",
            to_state="validated",
            actor_type="system",
            actor_id="validator",
            reason="controls_evaluated",
            metadata={
                "po_match_result": po_result,
                "budget_check_result": budget_result,
                "policy_issues": policy_issues,
                "blocking_issues": blocking_control_issues,
                "non_blocking_issues": non_blocking_control_issues,
            },
            idempotency_key=f"controls_evaluated:{ap_item['id']}",
            external_refs=self._external_refs(ap_item),
        )

        if missing:
            self._append_audit(
                ap_item_id=ap_item["id"],
                event_type="validation_failed",
                from_state="validated",
                to_state="needs_info",
                actor_type="system",
                actor_id="validator",
                reason="controls_validation_failed",
                metadata={
                    "missing": missing,
                    "policy": policy_decision.metadata,
                    "po_match_result": po_result,
                    "budget_check_result": budget_result,
                },
                idempotency_key=f"validation_failed:{ap_item['id']}",
                external_refs=self._external_refs(ap_item),
            )
            ap_item = self._transition(
                ap_item,
                "needs_info",
                actor_type="system",
                actor_id="validator",
                reason="controls_validation_failed",
                metadata={
                    "missing": missing,
                    "policy": policy_decision.metadata,
                    "po_match_result": po_result,
                    "budget_check_result": budget_result,
                },
                idempotency_key=f"needs_info:{ap_item['id']}",
            )
            await self._update_gmail_thread(ap_item, f"Needs info: {', '.join(missing)}", label_suffix="Needs Info")
            return {"status": "needs_info", "ap_item": ap_item, "missing_fields": missing}

        if non_blocking_control_issues:
            self._append_audit(
                ap_item_id=ap_item["id"],
                event_type="controls_warning",
                from_state="validated",
                to_state="validated",
                actor_type="system",
                actor_id="validator",
                reason="controls_warning",
                metadata={
                    "issues": non_blocking_control_issues,
                    "po_match_result": po_result,
                    "budget_check_result": budget_result,
                },
                idempotency_key=f"controls_warning:{ap_item['id']}",
                external_refs=self._external_refs(ap_item),
            )

        self._append_audit(
            ap_item_id=ap_item["id"],
            event_type="validation_passed",
            from_state="validated",
            to_state="needs_approval",
            actor_type="system",
            actor_id="validator",
            reason="validation_passed",
            metadata={},
            idempotency_key=f"validation_passed:{ap_item['id']}",
            external_refs=self._external_refs(ap_item),
        )

        ap_item = await self.request_approval(ap_item, reason="approval_required")
        return {"status": "needs_approval", "ap_item": ap_item}

    async def request_approval(self, ap_item: Dict[str, Any], reason: str = "approval_required") -> Dict[str, Any]:
        ap_item = await self._ensure_temporal_run(
            ap_item,
            "request_approval",
            payload={"reason": reason},
            actor_type="system",
            actor_id="approvals",
        )
        ap_item = self._transition(
            ap_item,
            "needs_approval",
            actor_type="system",
            actor_id="approvals",
            reason=reason,
            idempotency_key=f"needs_approval:{ap_item['id']}",
        )
        sent_count = await self._send_for_approval(ap_item)
        if sent_count == 0:
            self._append_audit(
                ap_item_id=ap_item["id"],
                event_type="approval_request_failed",
                from_state=ap_item.get("state"),
                to_state=ap_item.get("state"),
                actor_type="system",
                actor_id="approvals",
                reason="no_approval_channel_available",
                metadata={"channels": self._approval_channels()},
                idempotency_key=f"approval_request_failed:{ap_item['id']}",
                external_refs=self._external_refs(ap_item),
            )
            raise RuntimeError("No approval channel available for AP item")
        return ap_item

    async def _send_for_approval(self, ap_item: Dict[str, Any]) -> int:
        channel = self._slack_channel
        amount = ap_item.get("amount")
        currency = ap_item.get("currency", "USD")
        vendor = ap_item.get("vendor_name") or "Unknown vendor"
        invoice_number = ap_item.get("invoice_number") or "N/A"
        due_date = ap_item.get("due_date") or "N/A"
        ap_item_id = ap_item["id"]
        item_metadata = self._parse_metadata(ap_item.get("metadata"))
        run_id = item_metadata.get("run_id") or ap_item_id
        budget_summary = self._budget_summary(item_metadata.get("budget_check_result"))
        budget_status = str(budget_summary.get("status") or "")
        budget_line = ""
        if budget_status and budget_status != "not_requested":
            remaining = self._safe_float(budget_summary.get("remaining"), 0.0) or 0.0
            overage = self._safe_float(budget_summary.get("overage"), 0.0) or 0.0
            budget_currency = str(budget_summary.get("currency") or currency)
            if budget_status == "over_budget":
                budget_line = f"\nBudget: Over by {budget_currency} {overage:.2f}"
            elif budget_status == "within_budget":
                budget_line = f"\nBudget remaining: {budget_currency} {remaining:.2f}"
            else:
                budget_line = f"\nBudget status: {budget_status.replace('_', ' ')}"
        sent_count = 0

        channels = self._approval_channels_for_item(ap_item)
        if not channels:
            self._append_audit(
                ap_item_id=ap_item_id,
                event_type="approval_requested",
                from_state=ap_item.get("state"),
                to_state=ap_item.get("state"),
                actor_type="system",
                actor_id="approval_router",
                reason="approval_requested_gmail_inline",
                metadata={
                    "surface": "gmail",
                    "policy_version": ap_item.get("approval_policy_version") or self._approval_policy_version,
                },
                idempotency_key=f"approval_requested:inline:{ap_item_id}",
                external_refs=self._external_refs(ap_item),
            )
            return 1

        for approval_channel in channels:
            try:
                if approval_channel == "slack":
                    if not self._is_slack_available():
                        self._append_audit(
                            ap_item_id=ap_item_id,
                            event_type="approval_channel_unavailable",
                            from_state=ap_item.get("state"),
                            to_state=ap_item.get("state"),
                            actor_type="system",
                            actor_id="slack",
                            reason="slack_not_configured",
                            metadata={},
                            idempotency_key=f"approval_channel_unavailable:slack:{ap_item_id}",
                            external_refs=self._external_refs(ap_item),
                        )
                        continue
                    action_value = json.dumps({"ap_item_id": ap_item_id, "run_id": run_id})
                    blocks = [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"*{vendor}*  {currency} {amount or 'N/A'}\n"
                                    f"Invoice: `{invoice_number}`\nDue: {due_date}{budget_line}"
                                ),
                            },
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Approve"},
                                    "style": "primary",
                                    "action_id": "approve_ap",
                                    "value": action_value,
                                },
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Reject"},
                                    "style": "danger",
                                    "action_id": "reject_ap",
                                    "value": action_value,
                                },
                            ],
                        },
                    ]
                    message = await self.slack_client.send_message(
                        channel=channel,
                        text=f"Approval needed for {vendor} invoice",
                        blocks=blocks,
                    )
                    self.db.save_approval({
                        "ap_item_id": ap_item_id,
                        "channel_id": f"slack:{message.channel}",
                        "message_ts": message.ts,
                        "source_channel": f"slack:{message.channel}",
                        "source_message_ref": message.ts,
                        "status": "pending",
                        "organization_id": self.organization_id,
                    })
                    self.db.link_ap_item_source(
                        {
                            "ap_item_id": ap_item_id,
                            "source_type": "slack",
                            "source_ref": f"{message.channel}:{message.ts}",
                            "subject": f"Approval request for {vendor}",
                            "sender": "slack",
                            "metadata": {"channel": message.channel, "message_ts": message.ts},
                        }
                    )
                    self._append_audit(
                        ap_item_id=ap_item_id,
                        event_type="approval_requested",
                        from_state=ap_item.get("state"),
                        to_state=ap_item.get("state"),
                        actor_type="system",
                        actor_id="slack",
                        reason="approval_requested",
                        metadata={
                            "channel": message.channel,
                            "ts": message.ts,
                            "budget_status": budget_status,
                        },
                        idempotency_key=f"approval_requested:slack:{ap_item_id}:{message.ts}",
                        external_refs=self._external_refs(ap_item, slack_message_ts=message.ts, slack_channel=message.channel),
                    )
                    sent_count += 1
                    continue

                if approval_channel == "teams":
                    if not self._is_teams_available():
                        self._append_audit(
                            ap_item_id=ap_item_id,
                            event_type="approval_channel_unavailable",
                            from_state=ap_item.get("state"),
                            to_state=ap_item.get("state"),
                            actor_type="system",
                            actor_id="teams",
                            reason="teams_not_configured",
                            metadata={},
                            idempotency_key=f"approval_channel_unavailable:teams:{ap_item_id}",
                            external_refs=self._external_refs(ap_item),
                        )
                        continue
                    teams_message = await self.teams_client.send_approval_message(
                        text=f"Approval needed for {vendor} invoice",
                        ap_item_id=ap_item_id,
                        vendor=vendor,
                        amount=f"{currency} {amount or 'N/A'}",
                        invoice_number=invoice_number,
                        callback_url=self._teams_callback_url,
                        budget=budget_summary if budget_status and budget_status != "not_requested" else None,
                    )
                    self.db.save_approval({
                        "ap_item_id": ap_item_id,
                        "channel_id": f"teams:{teams_message.channel}",
                        "message_ts": teams_message.message_id,
                        "source_channel": f"teams:{teams_message.channel}",
                        "source_message_ref": teams_message.message_id,
                        "status": "pending",
                        "organization_id": self.organization_id,
                    })
                    self.db.link_ap_item_source(
                        {
                            "ap_item_id": ap_item_id,
                            "source_type": "teams",
                            "source_ref": f"{teams_message.channel}:{teams_message.message_id}",
                            "subject": f"Approval request for {vendor}",
                            "sender": "teams",
                            "metadata": {
                                "channel": teams_message.channel,
                                "message_id": teams_message.message_id,
                            },
                        }
                    )
                    self._append_audit(
                        ap_item_id=ap_item_id,
                        event_type="approval_requested",
                        from_state=ap_item.get("state"),
                        to_state=ap_item.get("state"),
                        actor_type="system",
                        actor_id="teams",
                        reason="approval_requested",
                        metadata={
                            "channel": teams_message.channel,
                            "message_id": teams_message.message_id,
                            "budget_status": budget_status,
                        },
                        idempotency_key=f"approval_requested:teams:{ap_item_id}:{teams_message.message_id}",
                        external_refs=self._external_refs(ap_item, teams_message_id=teams_message.message_id),
                    )
                    sent_count += 1
            except Exception as exc:
                self._append_audit(
                    ap_item_id=ap_item_id,
                    event_type="approval_request_failed",
                    from_state=ap_item.get("state"),
                    to_state=ap_item.get("state"),
                    actor_type="system",
                    actor_id=approval_channel,
                    reason=f"{approval_channel}_request_failed",
                    metadata={"error": str(exc)},
                    idempotency_key=f"approval_request_failed:{approval_channel}:{ap_item_id}",
                    external_refs=self._external_refs(ap_item),
                )
                logger.warning("Approval request failed on %s for %s: %s", approval_channel, ap_item_id, exc)
                continue

        return sent_count

    async def approve_ap_item(
        self,
        ap_item_id: str,
        approved_by: str,
        slack_channel: Optional[str] = None,
        slack_ts: Optional[str] = None,
        source_channel: Optional[str] = None,
        source_message_ref: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        ap_item = self.db.get_ap_item(ap_item_id)
        if not ap_item:
            return {"status": "not_found"}

        if ap_item.get("state") in {"closed", "posted_to_erp"} and ap_item.get("erp_reference"):
            return {
                "status": "idempotent",
                "ap_item": ap_item,
                "erp_reference": ap_item.get("erp_reference"),
                "erp_reference_id": ap_item.get("erp_reference"),
            }

        if ap_item.get("state") == "rejected":
            return {"status": "rejected_terminal", "ap_item": ap_item}

        if ap_item.get("state") not in {"needs_approval", "approved", "ready_to_post", "failed_post"}:
            return {"status": "invalid_state", "ap_item": ap_item}
        _, run_id, _ = self._get_runtime_ids(ap_item)
        run_id = run_id or ap_item_id

        source_channel = source_channel or slack_channel
        source_message_ref = source_message_ref or slack_ts
        idempotency_key = idempotency_key or f"approve:{ap_item_id}:{source_channel or 'na'}:{source_message_ref or 'na'}"
        if self.db.get_ap_audit_event_by_key(idempotency_key):
            return {"status": "idempotent", "ap_item": ap_item}

        self._append_audit(
            ap_item_id=ap_item_id,
            event_type="approval_received",
            from_state=ap_item.get("state"),
            to_state=ap_item.get("state"),
            actor_type="human",
            actor_id=approved_by,
            reason="approval_received",
            metadata={"channel": source_channel, "message_ref": source_message_ref},
            idempotency_key=f"approval_received:{ap_item_id}:{source_channel or 'na'}:{source_message_ref or 'na'}",
            external_refs=self._external_refs(
                ap_item,
                slack_message_ts=source_message_ref if source_channel and source_channel.startswith("slack") else None,
                slack_channel=source_channel if source_channel and source_channel.startswith("slack") else None,
                teams_message_id=source_message_ref if source_channel and source_channel.startswith("teams") else None,
            ),
        )

        if ap_item.get("state") == "needs_approval":
            ap_item = self._transition(
                ap_item,
                "approved",
                actor_type="human",
                actor_id=approved_by,
                reason="approved_in_channel",
                metadata={"channel": source_channel, "ts": source_message_ref},
                idempotency_key=idempotency_key,
            )
        await self._signal_temporal(
            ap_item,
            "approval_decision",
            {
                "action": "approve",
                "ap_item_id": ap_item_id,
                "run_id": run_id,
                "actor_id": approved_by,
                "source_channel": source_channel,
                "source_message_ref": source_message_ref,
            },
        )
        self.db.update_ap_item(
            ap_item_id,
            approved_by=approved_by,
            approved_at=datetime.now(timezone.utc).isoformat(),
        )
        self.db.update_approval_status(
            ap_item_id=ap_item_id,
            status="approved",
            approved_by=approved_by,
            approved_at=datetime.now(timezone.utc).isoformat(),
        )
        self._append_audit(
            ap_item_id=ap_item_id,
            event_type="approved",
            from_state="needs_approval",
            to_state="approved",
            actor_type="human",
            actor_id=approved_by,
            reason="approved_by_human",
            metadata={"channel": source_channel, "ts": source_message_ref},
            idempotency_key=f"approved_event:{ap_item_id}:{source_channel or 'na'}:{source_message_ref or 'na'}",
            external_refs=self._external_refs(
                ap_item,
                slack_message_ts=source_message_ref if source_channel and source_channel.startswith("slack") else None,
                slack_channel=source_channel if source_channel and source_channel.startswith("slack") else None,
                teams_message_id=source_message_ref if source_channel and source_channel.startswith("teams") else None,
            ),
        )

        result = await self._post_after_approval(ap_item, actor_type="system", actor_id="erp_connector")
        return result

    async def reject_ap_item(
        self,
        ap_item_id: str,
        rejected_by: str,
        reason: str,
        slack_channel: Optional[str] = None,
        slack_ts: Optional[str] = None,
        source_channel: Optional[str] = None,
        source_message_ref: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        ap_item = self.db.get_ap_item(ap_item_id)
        if not ap_item:
            return {"status": "not_found"}

        if ap_item.get("state") == "rejected":
            return {"status": "idempotent", "ap_item": ap_item}

        current_state = ap_item.get("state")
        if current_state not in {"needs_approval", "approved"}:
            return {"status": "invalid_state", "ap_item": ap_item}
        _, run_id, _ = self._get_runtime_ids(ap_item)
        run_id = run_id or ap_item_id

        post_attempted_at = ap_item.get("post_attempted_at")
        if current_state == "approved" and post_attempted_at:
            blocked_key = f"rejection_blocked_post_started:{ap_item_id}:{post_attempted_at}"
            self._append_audit(
                ap_item_id=ap_item_id,
                event_type="rejection_blocked_post_started",
                from_state=current_state,
                to_state=current_state,
                actor_type="human",
                actor_id=rejected_by,
                reason="rejection_blocked_post_started",
                metadata={
                    "requested_reason": reason,
                    "post_attempted_at": post_attempted_at,
                    "channel": source_channel,
                    "message_ref": source_message_ref,
                },
                idempotency_key=blocked_key,
                external_refs=self._external_refs(ap_item),
                decision_reason=reason,
            )
            return {"status": "conflict_post_started", "ap_item": ap_item}

        source_channel = source_channel or slack_channel
        source_message_ref = source_message_ref or slack_ts
        idempotency_key = idempotency_key or f"reject:{ap_item_id}:{source_channel or 'na'}:{source_message_ref or 'na'}"
        if self.db.get_ap_audit_event_by_key(idempotency_key):
            return {"status": "idempotent", "ap_item": ap_item}

        self._append_audit(
            ap_item_id=ap_item_id,
            event_type="approval_received",
            from_state=current_state,
            to_state=current_state,
            actor_type="human",
            actor_id=rejected_by,
            reason="rejection_received",
            metadata={"channel": source_channel, "message_ref": source_message_ref},
            idempotency_key=f"approval_received_reject:{ap_item_id}:{source_channel or 'na'}:{source_message_ref or 'na'}",
            external_refs=self._external_refs(
                ap_item,
                slack_message_ts=source_message_ref if source_channel and source_channel.startswith("slack") else None,
                slack_channel=source_channel if source_channel and source_channel.startswith("slack") else None,
                teams_message_id=source_message_ref if source_channel and source_channel.startswith("teams") else None,
            ),
        )

        ap_item = self._transition(
            ap_item,
            "rejected",
            actor_type="human",
            actor_id=rejected_by,
            reason=reason,
            metadata={"channel": source_channel, "ts": source_message_ref},
            idempotency_key=idempotency_key,
        )
        await self._signal_temporal(
            ap_item,
            "approval_decision",
            {
                "action": "reject",
                "ap_item_id": ap_item_id,
                "run_id": run_id,
                "actor_id": rejected_by,
                "reason": reason,
                "source_channel": source_channel,
                "source_message_ref": source_message_ref,
            },
        )
        self.db.update_ap_item(
            ap_item_id,
            rejected_by=rejected_by,
            rejected_at=datetime.now(timezone.utc).isoformat(),
            rejection_reason=reason,
        )
        self.db.update_approval_status(
            ap_item_id=ap_item_id,
            status="rejected",
            rejected_by=rejected_by,
            rejected_at=datetime.now(timezone.utc).isoformat(),
            rejection_reason=reason,
        )
        self._append_audit(
            ap_item_id=ap_item_id,
            event_type="rejected",
            from_state=current_state,
            to_state="rejected",
            actor_type="human",
            actor_id=rejected_by,
            reason=reason,
            metadata={"channel": source_channel, "ts": source_message_ref},
            idempotency_key=f"rejected_event:{ap_item_id}:{source_channel or 'na'}:{source_message_ref or 'na'}",
            external_refs=self._external_refs(
                ap_item,
                slack_message_ts=source_message_ref if source_channel and source_channel.startswith("slack") else None,
                slack_channel=source_channel if source_channel and source_channel.startswith("slack") else None,
                teams_message_id=source_message_ref if source_channel and source_channel.startswith("teams") else None,
            ),
            decision_reason=reason,
        )
        await self._update_gmail_thread(ap_item, f"Rejected: {reason}", label_suffix="Rejected")
        return {"status": "rejected", "ap_item": ap_item}

    async def _post_after_approval(
        self,
        ap_item: Dict[str, Any],
        actor_type: str,
        actor_id: str,
    ) -> Dict[str, Any]:
        if ap_item.get("state") == "rejected":
            return {"status": "rejected_terminal", "ap_item": ap_item}
        if ap_item.get("state") in {"closed", "posted_to_erp"} and ap_item.get("erp_reference"):
            return {
                "status": "posted",
                "ap_item": ap_item,
                "erp_reference": ap_item.get("erp_reference"),
                "erp_reference_id": ap_item.get("erp_reference"),
            }
        if ap_item.get("state") not in {"approved", "ready_to_post", "failed_post"}:
            return {"status": "invalid_state", "ap_item": ap_item}

        if ap_item.get("state") != "ready_to_post":
            ap_item = self._transition(
                ap_item,
                "ready_to_post",
                actor_type=actor_type,
                actor_id=actor_id,
                reason="ready_to_post",
                idempotency_key=f"ready_to_post:{ap_item['id']}",
            )
        post_attempted_at = datetime.now(timezone.utc).isoformat()
        self.db.update_ap_item(ap_item["id"], post_attempted_at=post_attempted_at)
        ap_item["post_attempted_at"] = post_attempted_at

        self._append_audit(
            ap_item_id=ap_item["id"],
            event_type="erp_post_attempted",
            from_state="ready_to_post",
            to_state="ready_to_post",
            actor_type="system",
            actor_id="erp",
            reason="erp_post_attempted",
            metadata={"post_attempted_at": post_attempted_at},
            idempotency_key=f"erp_post_attempted:{ap_item['id']}",
            external_refs=self._external_refs(ap_item),
        )

        result = await self._post_to_erp(ap_item)
        if result.get("status") != "success":
            ap_item = self._transition(
                ap_item,
                "failed_post",
                actor_type="system",
                actor_id="erp",
                reason=result.get("reason", "erp_post_failed"),
                metadata=result,
                idempotency_key=f"failed_post:{ap_item['id']}:{result.get('reason')}",
            )
            self.db.update_ap_item(ap_item["id"], last_error=result.get("reason"))
            self._append_audit(
                ap_item_id=ap_item["id"],
                event_type="erp_post_failed",
                from_state="ready_to_post",
                to_state="failed_post",
                actor_type="system",
                actor_id="erp",
                reason=result.get("reason", "erp_post_failed"),
                metadata=result,
                idempotency_key=f"erp_post_failed:{ap_item['id']}:{result.get('reason')}",
                external_refs=self._external_refs(ap_item),
            )
            await self._update_gmail_thread(ap_item, f"Failed post: {result.get('reason')}", label_suffix="Failed Post")
            await self._notify_post_failure(ap_item, result)
            return {"status": "failed_post", "ap_item": ap_item, "error": result.get("reason")}

        erp_reference = result.get("erp_reference_id") or result.get("erp_reference") or result.get("bill_id") or result.get("doc_num")
        self.db.update_ap_item(
            ap_item["id"],
            erp_reference=erp_reference,
            erp_posted_at=datetime.now(timezone.utc).isoformat(),
        )
        ap_item["erp_reference"] = erp_reference
        if erp_reference:
            self.db.link_ap_item_source(
                {
                    "ap_item_id": ap_item["id"],
                    "source_type": "erp",
                    "source_ref": str(erp_reference),
                    "subject": "ERP posting reference",
                    "sender": "erp",
                    "metadata": {"erp_reference": erp_reference},
                }
            )

        self._append_audit(
            ap_item_id=ap_item["id"],
            event_type="erp_post_succeeded",
            from_state="ready_to_post",
            to_state="posted_to_erp",
            actor_type="system",
            actor_id="erp",
            reason="erp_post_succeeded",
            metadata={"erp_reference_id": erp_reference},
            idempotency_key=f"erp_post_succeeded:{ap_item['id']}:{erp_reference}",
            external_refs=self._external_refs(ap_item, erp_ref=erp_reference),
        )

        ap_item = self._transition(
            ap_item,
            "posted_to_erp",
            actor_type="system",
            actor_id="erp",
            reason="posted_to_erp",
            metadata={"erp_reference": erp_reference},
            idempotency_key=f"posted_to_erp:{ap_item['id']}:{erp_reference}",
        )
        ap_item = self._transition(
            ap_item,
            "closed",
            actor_type="system",
            actor_id="workflow",
            reason="closed_after_post",
            metadata={"erp_reference": erp_reference},
            idempotency_key=f"closed:{ap_item['id']}:{erp_reference}",
        )

        await self._update_gmail_thread(ap_item, f"Posted to ERP: {erp_reference}", label_suffix="Posted")
        return {
            "status": "posted",
            "ap_item": ap_item,
            "erp_reference": erp_reference,
            "erp_reference_id": erp_reference,
        }

    async def retry_post(self, ap_item_id: str, actor_id: str = "system") -> Dict[str, Any]:
        ap_item = self.db.get_ap_item(ap_item_id)
        if not ap_item:
            return {"status": "not_found"}
        if ap_item.get("state") != "failed_post":
            return {"status": "invalid_state", "ap_item": ap_item}
        await self._signal_temporal(
            ap_item,
            "retry_post",
            {"ap_item_id": ap_item_id, "actor_id": actor_id},
        )
        return await self._post_after_approval(ap_item, actor_type="system", actor_id=actor_id)

    async def resubmit_ap_item(
        self,
        ap_item_id: str,
        actor_id: str,
        reason: str,
        corrected_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        original = self.db.get_ap_item(ap_item_id)
        if not original:
            return {"status": "not_found"}
        if original.get("state") != "rejected":
            return {"status": "invalid_state", "ap_item": original}

        corrected_fields = corrected_fields or {}
        metadata = self._parse_metadata(original.get("metadata"))
        metadata = {
            **metadata,
            "resubmitted_by": actor_id,
            "resubmission_reason": reason,
            "supersedes_ap_item_id": original.get("id"),
            "supersedes_invoice_key": original.get("invoice_key"),
            "corrected_fields": corrected_fields,
        }

        invoice = InvoiceData(
            gmail_id=corrected_fields.get("gmail_id") or original.get("message_id") or original.get("id"),
            thread_id=corrected_fields.get("thread_id") or original.get("thread_id") or "",
            message_id=corrected_fields.get("message_id") or original.get("message_id") or "",
            subject=corrected_fields.get("subject") or original.get("subject") or "",
            sender=corrected_fields.get("sender") or original.get("sender") or "",
            vendor_name=corrected_fields.get("vendor_name") or original.get("vendor_name") or "",
            amount=corrected_fields.get("amount", original.get("amount")),
            currency=corrected_fields.get("currency") or original.get("currency") or "USD",
            invoice_number=corrected_fields.get("invoice_number") or original.get("invoice_number"),
            invoice_date=corrected_fields.get("invoice_date") or original.get("invoice_date"),
            due_date=corrected_fields.get("due_date") or original.get("due_date"),
            confidence=float(corrected_fields.get("confidence") or original.get("confidence") or 0),
            organization_id=self.organization_id,
            user_id=corrected_fields.get("user_id") or original.get("user_id"),
            metadata=metadata,
        )
        result = await self.process_new_invoice(invoice)
        new_item = result.get("ap_item")
        if new_item:
            self._append_audit(
                ap_item_id=new_item["id"],
                event_type="resubmitted",
                from_state=None,
                to_state=new_item.get("state"),
                actor_type="human",
                actor_id=actor_id,
                reason=reason,
                metadata={"supersedes_ap_item_id": ap_item_id, "corrected_fields": corrected_fields},
                idempotency_key=f"resubmit:{ap_item_id}:{new_item['id']}",
                external_refs=self._external_refs(new_item),
                decision_reason=reason,
            )
        return result

    async def get_workflow_status(self, ap_item_id: str) -> Dict[str, Any]:
        item = self.db.get_ap_item(ap_item_id)
        if not item:
            return {"status": "not_found"}
        return await self.temporal_client.query_status(
            organization_id=str(item.get("organization_id") or self.organization_id),
            ap_item_id=ap_item_id,
        )

    def get_agent_trace(self, ap_item_id: str) -> Dict[str, Any]:
        item = self.db.get_ap_item(ap_item_id)
        if not item:
            return {"status": "not_found"}
        metadata = self._parse_metadata(item.get("metadata"))
        trace = metadata.get("agent_trace") or []
        validation = metadata.get("agent_validation") or {}
        posting_plan = metadata.get("agent_posting_plan") or {}
        browser_commands = metadata.get("agent_browser_commands") or []
        agent_session_id = metadata.get("agent_session_id")
        return {
            "status": "ok",
            "ap_item_id": ap_item_id,
            "trace": trace,
            "validation": validation,
            "posting_plan": posting_plan,
            "browser_commands": browser_commands,
            "agent_session_id": agent_session_id,
        }

    def get_ap_item_sources(self, ap_item_id: str) -> List[Dict[str, Any]]:
        item = self.db.get_ap_item(ap_item_id)
        if not item:
            return []
        sources = self.db.list_ap_item_sources(ap_item_id)
        # Backward compatibility: expose primary thread/message as sources when older rows lack link records.
        if item.get("thread_id") and not any(
            source.get("source_type") == "gmail_thread" and source.get("source_ref") == item.get("thread_id")
            for source in sources
        ):
            sources.insert(
                0,
                {
                    "id": f"legacy-thread-{ap_item_id}",
                    "ap_item_id": ap_item_id,
                    "source_type": "gmail_thread",
                    "source_ref": item.get("thread_id"),
                    "subject": item.get("subject"),
                    "sender": item.get("sender"),
                    "detected_at": item.get("created_at"),
                    "metadata": {"legacy": True},
                    "created_at": item.get("created_at"),
                },
            )
        if item.get("message_id") and not any(
            source.get("source_type") == "gmail_message" and source.get("source_ref") == item.get("message_id")
            for source in sources
        ):
            sources.insert(
                0,
                {
                    "id": f"legacy-message-{ap_item_id}",
                    "ap_item_id": ap_item_id,
                    "source_type": "gmail_message",
                    "source_ref": item.get("message_id"),
                    "subject": item.get("subject"),
                    "sender": item.get("sender"),
                    "detected_at": item.get("created_at"),
                    "metadata": {"legacy": True},
                    "created_at": item.get("created_at"),
                },
            )
        return sources

    def merge_ap_items(
        self,
        target_ap_item_id: str,
        source_ap_item_id: str,
        actor_id: str,
        reason: str = "manual_merge",
    ) -> Dict[str, Any]:
        if target_ap_item_id == source_ap_item_id:
            return {"status": "invalid_request", "reason": "same_ap_item"}

        target = self.db.get_ap_item(target_ap_item_id)
        source = self.db.get_ap_item(source_ap_item_id)
        if not target or not source:
            return {"status": "not_found"}
        if str(target.get("organization_id") or self.organization_id) != str(source.get("organization_id") or self.organization_id):
            return {"status": "invalid_request", "reason": "cross_org_merge_not_allowed"}

        moved_sources = 0
        for record in self.get_ap_item_sources(source_ap_item_id):
            source_type = str(record.get("source_type") or "").strip()
            source_ref = str(record.get("source_ref") or "").strip()
            if not source_type or not source_ref:
                continue
            moved = self.db.move_ap_item_source(
                from_ap_item_id=source_ap_item_id,
                to_ap_item_id=target_ap_item_id,
                source_type=source_type,
                source_ref=source_ref,
            )
            if moved:
                moved_sources += 1

        target_meta = self._parse_metadata(target.get("metadata"))
        merged_ids = set(target_meta.get("merged_ap_item_ids") or [])
        merged_ids.add(source_ap_item_id)
        target_meta["merged_ap_item_ids"] = sorted(str(entry) for entry in merged_ids)
        target_meta["merge_reason"] = "manual_merge"
        target_meta["has_context_conflict"] = False
        target_meta["last_merged_at"] = datetime.now(timezone.utc).isoformat()
        self.db.update_ap_item(target_ap_item_id, metadata=target_meta)

        source_meta = self._parse_metadata(source.get("metadata"))
        source_meta["merged_into_ap_item_id"] = target_ap_item_id
        source_meta["merge_reason"] = "manual_merge_source"
        source_meta["hidden_from_worklist"] = True
        source_meta["merged_by"] = actor_id
        source_meta["merged_at"] = datetime.now(timezone.utc).isoformat()
        self.db.update_ap_item(source_ap_item_id, state="closed", metadata=source_meta)

        self._append_audit(
            ap_item_id=target_ap_item_id,
            event_type="manual_merge_applied",
            from_state=target.get("state"),
            to_state=target.get("state"),
            actor_type="human",
            actor_id=actor_id,
            reason=reason,
            metadata={
                "source_ap_item_id": source_ap_item_id,
                "moved_sources": moved_sources,
            },
            idempotency_key=f"manual_merge:{target_ap_item_id}:{source_ap_item_id}",
            external_refs=self._external_refs(target),
        )
        self._append_audit(
            ap_item_id=source_ap_item_id,
            event_type="manual_merge_source_closed",
            from_state=source.get("state"),
            to_state="closed",
            actor_type="human",
            actor_id=actor_id,
            reason=reason,
            metadata={"target_ap_item_id": target_ap_item_id},
            idempotency_key=f"manual_merge_source_closed:{source_ap_item_id}:{target_ap_item_id}",
            external_refs=self._external_refs(source),
        )

        refreshed_target = self.db.get_ap_item(target_ap_item_id) or target
        refreshed_source = self.db.get_ap_item(source_ap_item_id) or source
        return {
            "status": "merged",
            "target_ap_item": refreshed_target,
            "source_ap_item": refreshed_source,
            "moved_sources": moved_sources,
        }

    def split_ap_item(
        self,
        ap_item_id: str,
        actor_id: str,
        source_refs: Optional[List[Dict[str, str]]] = None,
        reason: str = "manual_split",
    ) -> Dict[str, Any]:
        item = self.db.get_ap_item(ap_item_id)
        if not item:
            return {"status": "not_found"}

        all_sources = self.get_ap_item_sources(ap_item_id)
        if not all_sources:
            return {"status": "invalid_request", "reason": "no_sources_available"}

        normalized_refs = {
            (str((entry or {}).get("source_type") or "").strip(), str((entry or {}).get("source_ref") or "").strip())
            for entry in (source_refs or [])
            if str((entry or {}).get("source_type") or "").strip() and str((entry or {}).get("source_ref") or "").strip()
        }
        if normalized_refs:
            selected_sources = [
                source
                for source in all_sources
                if (str(source.get("source_type") or "").strip(), str(source.get("source_ref") or "").strip()) in normalized_refs
            ]
        else:
            # Default: split all non-primary linked sources.
            primary_thread = str(item.get("thread_id") or "").strip()
            primary_message = str(item.get("message_id") or "").strip()
            selected_sources = []
            for source in all_sources:
                source_type = str(source.get("source_type") or "").strip()
                source_ref = str(source.get("source_ref") or "").strip()
                if source_type == "gmail_thread" and source_ref == primary_thread:
                    continue
                if source_type == "gmail_message" and source_ref == primary_message:
                    continue
                selected_sources.append(source)

        if not selected_sources:
            return {"status": "invalid_request", "reason": "no_split_sources_selected"}

        metadata = self._parse_metadata(item.get("metadata"))
        split_metadata = {
            **metadata,
            "split_from_ap_item_id": ap_item_id,
            "split_reason": reason,
            "hidden_from_worklist": False,
            "has_context_conflict": False,
        }
        split_metadata.pop("merged_into_ap_item_id", None)
        split_metadata.pop("merged_by", None)
        split_metadata.pop("merged_at", None)

        split_key = f"{item.get('invoice_key') or ap_item_id}::split::{uuid.uuid4().hex[:8]}"
        split_item = self.db.create_ap_item(
            {
                "invoice_key": split_key,
                "thread_id": None,
                "message_id": None,
                "subject": item.get("subject"),
                "sender": item.get("sender"),
                "vendor_name": item.get("vendor_name"),
                "amount": item.get("amount"),
                "currency": item.get("currency") or "USD",
                "invoice_number": item.get("invoice_number"),
                "invoice_date": item.get("invoice_date"),
                "due_date": item.get("due_date"),
                "state": "needs_info",
                "confidence": item.get("confidence") or 0.0,
                "approval_required": bool(item.get("approval_required")),
                "approval_surface": item.get("approval_surface") or self._approval_surface_default,
                "approval_policy_version": item.get("approval_policy_version") or self._approval_policy_version,
                "organization_id": item.get("organization_id") or self.organization_id,
                "user_id": item.get("user_id"),
                "metadata": split_metadata,
            }
        )

        moved = 0
        for source in selected_sources:
            source_type = str(source.get("source_type") or "").strip()
            source_ref = str(source.get("source_ref") or "").strip()
            if not source_type or not source_ref:
                continue
            moved_source = self.db.move_ap_item_source(
                from_ap_item_id=ap_item_id,
                to_ap_item_id=split_item["id"],
                source_type=source_type,
                source_ref=source_ref,
            )
            if moved_source:
                moved += 1

        split_sources = self.get_ap_item_sources(split_item["id"])
        split_thread = next((s.get("source_ref") for s in split_sources if s.get("source_type") == "gmail_thread"), None)
        split_message = next((s.get("source_ref") for s in split_sources if s.get("source_type") == "gmail_message"), None)
        self.db.update_ap_item(split_item["id"], thread_id=split_thread, message_id=split_message)
        split_item = self.db.get_ap_item(split_item["id"]) or split_item

        remaining_sources = self.get_ap_item_sources(ap_item_id)
        remaining_thread = next((s.get("source_ref") for s in remaining_sources if s.get("source_type") == "gmail_thread"), item.get("thread_id"))
        remaining_message = next((s.get("source_ref") for s in remaining_sources if s.get("source_type") == "gmail_message"), item.get("message_id"))
        item_meta = self._parse_metadata(item.get("metadata"))
        item_meta["has_context_conflict"] = len(remaining_sources) > 1
        self.db.update_ap_item(ap_item_id, thread_id=remaining_thread, message_id=remaining_message, metadata=item_meta)

        self._append_audit(
            ap_item_id=ap_item_id,
            event_type="manual_split_applied",
            from_state=item.get("state"),
            to_state=item.get("state"),
            actor_type="human",
            actor_id=actor_id,
            reason=reason,
            metadata={
                "split_ap_item_id": split_item["id"],
                "moved_sources": moved,
            },
            idempotency_key=f"manual_split:{ap_item_id}:{split_item['id']}",
            external_refs=self._external_refs(item),
        )
        self._append_audit(
            ap_item_id=split_item["id"],
            event_type="manual_split_created",
            from_state=None,
            to_state=split_item.get("state"),
            actor_type="human",
            actor_id=actor_id,
            reason=reason,
            metadata={"source_ap_item_id": ap_item_id, "moved_sources": moved},
            idempotency_key=f"manual_split_created:{split_item['id']}",
            external_refs=self._external_refs(split_item),
        )

        return {
            "status": "split",
            "source_ap_item": self.db.get_ap_item(ap_item_id) or item,
            "new_ap_item": split_item,
            "moved_sources": moved,
        }

    async def get_ap_item_context(self, ap_item_id: str, refresh: bool = False) -> Dict[str, Any]:
        item = self.db.get_ap_item(ap_item_id)
        if not item:
            raise ValueError("ap_item_not_found")

        if not refresh:
            cached = self.db.get_ap_item_context_cache(ap_item_id)
            if cached and isinstance(cached.get("context_json"), dict):
                cached_context = dict(cached["context_json"])
                cached_at = cached.get("updated_at")
                generated_at = cached_context.get("generated_at")
                age_seconds: Optional[int] = None
                try:
                    generated_dt = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00")) if generated_at else None
                    if generated_dt:
                        age_seconds = max(
                            0,
                            int((datetime.now(timezone.utc) - generated_dt).total_seconds()),
                        )
                except Exception:
                    age_seconds = None
                cached_context["cached"] = True
                cached_context["cached_at"] = cached_at
                freshness = self._as_dict(cached_context.get("freshness"))
                freshness["cached_at"] = cached_at
                if age_seconds is not None:
                    freshness["age_seconds"] = age_seconds
                cached_context["freshness"] = freshness
                return cached_context

        sources = self.get_ap_item_sources(ap_item_id)
        metadata = self._parse_metadata(item.get("metadata"))
        warnings: List[str] = []
        generated_at = datetime.now(timezone.utc)
        source_quality = self._summarize_source_quality(sources, generated_at)

        email_sources = [
            source
            for source in sources
            if source.get("source_type") in {"gmail_thread", "gmail_message"}
        ]
        email_summary = {
            "source_count": len(email_sources),
            "sources": [
                {
                    "source_type": source.get("source_type"),
                    "source_ref": source.get("source_ref"),
                    "subject": source.get("subject"),
                    "sender": source.get("sender"),
                    "detected_at": source.get("detected_at"),
                }
                for source in email_sources
            ],
            "primary_subject": item.get("subject"),
            "primary_sender": item.get("sender"),
        }

        portal_sources = [source for source in sources if source.get("source_type") == "portal"]
        payment_portal_sources = [source for source in sources if source.get("source_type") == "payment_portal"]
        procurement_sources = [source for source in sources if source.get("source_type") == "procurement"]
        dms_sources = [source for source in sources if source.get("source_type") == "dms"]
        session_id = metadata.get("agent_session_id")
        browser_events = self.db.list_browser_action_events(session_id) if session_id else []
        web_summary = {
            "related_portals": [
                {
                    "url": source.get("source_ref"),
                    "subject": source.get("subject"),
                    "detected_at": source.get("detected_at"),
                }
                for source in portal_sources
            ],
            "payment_portals": [
                {
                    "url": source.get("source_ref"),
                    "subject": source.get("subject"),
                    "detected_at": source.get("detected_at"),
                }
                for source in payment_portal_sources
            ],
            "procurement": [
                {
                    "ref": source.get("source_ref"),
                    "subject": source.get("subject"),
                    "detected_at": source.get("detected_at"),
                }
                for source in procurement_sources
            ],
            "dms_documents": [
                {
                    "ref": source.get("source_ref"),
                    "subject": source.get("subject"),
                    "detected_at": source.get("detected_at"),
                }
                for source in dms_sources
            ],
            "browser_session_id": session_id,
            "browser_event_count": len(browser_events),
            "recent_browser_events": [
                {
                    "tool_name": event.get("tool_name"),
                    "status": event.get("status"),
                    "updated_at": event.get("updated_at"),
                    "policy_reason": event.get("policy_reason"),
                }
                for event in browser_events[-5:]
            ],
            "connector_coverage": {
                "portal": bool(portal_sources),
                "payment_portal": bool(payment_portal_sources),
                "procurement": bool(procurement_sources),
                "dms": bool(dms_sources),
                "browser_session": bool(session_id),
            },
        }

        approvals = self.db.list_approvals_by_item(ap_item_id)
        latest_approval = approvals[0] if approvals else None
        budget_summary = self._budget_summary(metadata.get("budget_check_result"))
        approval_summary: Dict[str, Any] = {
            "count": len(approvals),
            "latest": latest_approval,
            "slack": {"available": self._is_slack_available(), "thread_preview": []},
            "teams": {"available": self._is_teams_available(), "reference": None},
            "budget": budget_summary,
        }
        if not approval_summary["slack"]["available"]:
            warnings.append("slack_unavailable")
        if not approval_summary["teams"]["available"]:
            warnings.append("teams_unavailable")

        if latest_approval:
            source_channel = str(latest_approval.get("source_channel") or "")
            source_ref = str(
                latest_approval.get("source_message_ref")
                or latest_approval.get("message_ts")
                or ""
            )
            if source_channel.startswith("teams"):
                approval_summary["teams"]["reference"] = {
                    "source_channel": source_channel,
                    "source_ref": source_ref,
                }
            if source_channel.startswith("slack") and source_ref and self._is_slack_available():
                try:
                    channel_id = source_channel.split(":", 1)[1] if ":" in source_channel else source_channel
                    replies = await self.slack_client.get_thread_replies(channel_id, source_ref, limit=10)
                    approval_summary["slack"]["thread_preview"] = [
                        {
                            "text": str(reply.get("text") or "")[:240],
                            "user": reply.get("user"),
                            "ts": reply.get("ts"),
                        }
                        for reply in replies[:5]
                    ]
                except Exception as exc:
                    warnings.append(f"slack_preview_unavailable:{exc}")

        erp_connection = get_erp_connection(self.organization_id)
        erp_summary = {
            "state": item.get("state"),
            "posted": bool(item.get("erp_reference")),
            "erp_reference": item.get("erp_reference"),
            "erp_posted_at": item.get("erp_posted_at"),
            "connector_available": bool(erp_connection),
            "connector_type": erp_connection.get("erp_type") if isinstance(erp_connection, dict) else None,
        }
        if not erp_summary["connector_available"]:
            warnings.append("erp_unavailable")

        detected_values = []
        for source in sources:
            detected = source.get("detected_at")
            if isinstance(detected, str):
                try:
                    detected_values.append(datetime.fromisoformat(detected.replace("Z", "+00:00")))
                except Exception:
                    continue
        oldest_source = min(detected_values) if detected_values else None
        newest_source = max(detected_values) if detected_values else None
        source_age_seconds = (
            max(0, int((generated_at - newest_source).total_seconds()))
            if newest_source
            else None
        )
        stale_after_seconds = max(300, int(os.getenv("AP_CONTEXT_STALE_AFTER_SECONDS", "1800") or 1800))
        is_stale = bool(source_age_seconds is not None and source_age_seconds > stale_after_seconds)
        if is_stale:
            warnings.append("context_stale")

        risk_signals = metadata.get("risk_signals") or {}
        if not isinstance(risk_signals, dict):
            risk_signals = {}
        if "late_payment_risk" not in risk_signals:
            risk_signals["late_payment_risk"] = self._late_payment_risk(
                item.get("due_date"),
                requires_human_review=bool(risk_signals.get("requires_human_review")),
            )
        if "discount_opportunity" not in risk_signals:
            risk_signals["discount_opportunity"] = self._discount_signal(metadata, str(item.get("currency") or "USD"))

        freshness = {
            "generated_at": generated_at.isoformat(),
            "source_count": len(sources),
            "oldest_source_at": oldest_source.isoformat() if oldest_source else None,
            "newest_source_at": newest_source.isoformat() if newest_source else None,
            "age_seconds": source_age_seconds if source_age_seconds is not None else 0,
            "stale_after_seconds": stale_after_seconds,
            "is_stale": is_stale,
        }

        context = {
            "ap_item_id": ap_item_id,
            "generated_at": generated_at.isoformat(),
            "cached": False,
            "partial": bool(warnings),
            "warnings": warnings,
            "email": email_summary,
            "web": web_summary,
            "approvals": approval_summary,
            "erp": erp_summary,
            "po_match": metadata.get("po_match_result") or {},
            "budget": budget_summary,
            "risk_signals": risk_signals,
            "source_quality": source_quality,
            "freshness": freshness,
        }
        self.db.upsert_ap_item_context_cache(ap_item_id, context)
        return context

    async def _notify_post_failure(self, ap_item: Dict[str, Any], result: Dict[str, Any]) -> None:
        try:
            channel = self._slack_channel
            vendor = ap_item.get("vendor_name") or "Unknown vendor"
            amount = ap_item.get("amount") or 0
            currency = ap_item.get("currency") or "USD"
            reason = result.get("reason") or "erp_post_failed"
            await self.slack_client.send_message(
                channel=channel,
                text=f"ERP posting failed for {vendor} {currency} {amount}. Reason: {reason}",
            )
        except Exception as exc:
            logger.warning("Slack post failure notification failed: %s", exc)

    async def _post_to_erp(self, ap_item: Dict[str, Any]) -> Dict[str, Any]:
        erp_mode = os.getenv("ERP_MODE", "mock").lower()
        if erp_mode == "mock":
            ref = f"ERP-MOCK-{ap_item['id'][:8]}"
            return {"status": "success", "erp_reference": ref, "erp_reference_id": ref}

        connection = get_erp_connection(self.organization_id)
        if not connection:
            return {"status": "error", "reason": "erp_not_configured"}

        bill = Bill(
            vendor_name=ap_item.get("vendor_name") or "Unknown",
            amount=ap_item.get("amount") or 0,
            currency=ap_item.get("currency") or "USD",
            invoice_number=ap_item.get("invoice_number"),
            due_date=ap_item.get("due_date"),
            description=ap_item.get("subject") or "AP invoice",
        )
        result = await post_bill(self.organization_id, bill)
        if result.get("status") != "success":
            return {"status": "error", "reason": result.get("reason") or "erp_post_failed", "details": result}
        ref = (
            result.get("erp_reference_id")
            or result.get("erp_reference")
            or result.get("bill_id")
            or result.get("doc_num")
        )
        return {"status": "success", "erp_reference": ref, "erp_reference_id": ref, **result}

    async def _update_gmail_thread(self, ap_item: Dict[str, Any], status_text: str, label_suffix: str) -> None:
        user_id = ap_item.get("user_id")
        message_id = ap_item.get("message_id")
        thread_id = ap_item.get("thread_id")
        if not user_id or not message_id:
            return
        idempotency_key = f"gmail_thread_update:{ap_item['id']}:{label_suffix}"
        if self.db.get_ap_audit_event_by_key(idempotency_key):
            return
        try:
            client = GmailAPIClient(user_id)
            if not await client.ensure_authenticated():
                return
            label_name = f"Clearledgr/{label_suffix}"
            labels = await client.list_labels()
            label = next((l for l in labels if l.get("name") == label_name), None)
            if not label:
                label = await client.create_label(label_name)

            label_id = label.get("id")
            applied = False
            if label_id:
                message = await client.get_message(message_id, format="metadata")
                if label_id not in (message.labels or []):
                    await client.add_label(message_id, [label_id])
                    applied = True

            note_sent = False
            if status_text and thread_id:
                subject = ap_item.get("subject") or "Clearledgr update"
                await client.send_thread_note(thread_id, user_id, f"Re: {subject}", status_text)
                note_sent = True

            self._append_audit(
                ap_item_id=ap_item["id"],
                event_type="thread_updated",
                from_state=ap_item.get("state"),
                to_state=ap_item.get("state"),
                actor_type="system",
                actor_id="gmail",
                reason="gmail_thread_updated",
                metadata={
                    "label": label_name,
                    "label_applied": applied,
                    "note": status_text,
                    "note_sent": note_sent,
                },
                idempotency_key=idempotency_key,
                external_refs=self._external_refs(ap_item),
            )
        except Exception as exc:
            logger.warning("Gmail thread update failed: %s", exc)


def get_invoice_workflow(organization_id: str, slack_channel: Optional[str] = None) -> InvoiceWorkflowService:
    return InvoiceWorkflowService(organization_id=organization_id, slack_channel=slack_channel)
