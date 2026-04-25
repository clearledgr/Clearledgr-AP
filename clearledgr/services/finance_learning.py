"""Canonical learning facade for the AP agent.

This keeps the current learning systems in place but gives the runtime a
single write path for:
- manual field corrections
- runtime invoice outcomes
- future decision-feedback hooks
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from clearledgr.core.database import ClearledgrDB
from clearledgr.core.utils import safe_float
from clearledgr.services.compounding_learning import (
    get_learning_service as get_compounding_learning_service,
)
from clearledgr.services.correction_learning import get_correction_learning_service
from clearledgr.services.learning import get_learning_service as get_pattern_learning_service

logger = logging.getLogger(__name__)

_finance_learning_services: Dict[Tuple[str, int], "FinanceLearningService"] = {}


class FinanceLearningService:
    """Single facade over the repo's current learning subsystems."""

    def __init__(
        self,
        organization_id: str = "default",
        *,
        db: Optional[ClearledgrDB] = None,
    ) -> None:
        self.organization_id = str(organization_id or "default").strip() or "default"
        self.db = db
        self.enabled = bool(
            self.db is not None
            and all(hasattr(self.db, attr) for attr in ("connect", "_prepare_sql"))
        )
        self._correction_learning = None
        self._pattern_learning = None
        self._compounding_learning = None
        if self.enabled:
            self._init_tables()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _init_tables(self) -> None:
        if not self.enabled:
            return
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS finance_learning_events (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    ap_item_id TEXT,
                    event_type TEXT NOT NULL,
                    actor_id TEXT,
                    vendor_name TEXT,
                    action_status TEXT,
                    learning_summary TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS finance_learning_calibration (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    vendor_name TEXT,
                    action_key TEXT NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    shadow_match_count INTEGER NOT NULL DEFAULT 0,
                    verification_success_count INTEGER NOT NULL DEFAULT 0,
                    recovery_attempt_count INTEGER NOT NULL DEFAULT 0,
                    recovery_success_count INTEGER NOT NULL DEFAULT 0,
                    confidence_delta_total REAL NOT NULL DEFAULT 0.0,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(organization_id, vendor_name, action_key)
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_finance_learning_events_org_type "
                "ON finance_learning_events(organization_id, event_type, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_finance_learning_events_org_item "
                "ON finance_learning_events(organization_id, ap_item_id, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_finance_learning_calibration_org_action "
                "ON finance_learning_calibration(organization_id, action_key, updated_at)"
            )
            conn.commit()

    @property
    def correction_learning(self):
        if self._correction_learning is None:
            self._correction_learning = get_correction_learning_service(self.organization_id)
        return self._correction_learning

    @property
    def pattern_learning(self):
        if self._pattern_learning is None:
            self._pattern_learning = get_pattern_learning_service(self.organization_id)
        return self._pattern_learning

    @property
    def compounding_learning(self):
        if self._compounding_learning is None:
            try:
                self._compounding_learning = get_compounding_learning_service()
            except Exception as exc:
                logger.warning("Could not load compounding learning service: %s", exc)
                self._compounding_learning = None
        return self._compounding_learning

    @staticmethod
    def _resolved_actual_action(response: Dict[str, Any]) -> str:
        status = str(response.get("status") or "").strip().lower()
        if status in {"pending_approval", "awaiting_approval"}:
            return "route_for_approval"
        if status in {"needs_info"}:
            return "vendor_followup"
        if status in {"blocked"}:
            return "field_review"
        if status in {"auto_approved", "posted_to_erp", "closed"}:
            return "auto_approve_post"
        if status in {"processed", "completed"}:
            return "processed"
        if status in {"failed", "error"}:
            return "failed"
        return status or "unknown"

    @staticmethod
    def _normalize_payload(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    def _persist_learning_event(
        self,
        *,
        event_type: str,
        ap_item_id: Optional[str],
        actor_id: Optional[str],
        vendor_name: Optional[str],
        action_status: Optional[str],
        learning_summary: Optional[str],
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        row = {
            "id": f"learning_{uuid.uuid4().hex}",
            "organization_id": self.organization_id,
            "ap_item_id": str(ap_item_id or "").strip() or None,
            "event_type": str(event_type or "").strip() or "unknown",
            "actor_id": str(actor_id or "").strip() or None,
            "vendor_name": str(vendor_name or "").strip() or None,
            "action_status": str(action_status or "").strip() or None,
            "learning_summary": str(learning_summary or "").strip() or None,
            "payload_json": dict(payload or {}),
            "created_at": self._now_iso(),
        }
        if not self.enabled:
            return row
        sql = (
            """
            INSERT INTO finance_learning_events (
                id, organization_id, ap_item_id, event_type, actor_id, vendor_name,
                action_status, learning_summary, payload_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    row["id"],
                    row["organization_id"],
                    row["ap_item_id"],
                    row["event_type"],
                    row["actor_id"],
                    row["vendor_name"],
                    row["action_status"],
                    row["learning_summary"],
                    json.dumps(row["payload_json"], sort_keys=True, default=str),
                    row["created_at"],
                ),
            )
            conn.commit()
        return row

    def list_learning_events(self, *, ap_item_id: Optional[str] = None, limit: int = 20) -> list[Dict[str, Any]]:
        if not self.enabled:
            return []
        clauses = ["organization_id = %s"]
        params: list[Any] = [self.organization_id]
        if ap_item_id:
            clauses.append("ap_item_id = %s")
            params.append(str(ap_item_id).strip())
        sql = (
            f"""
            SELECT ap_item_id, event_type, actor_id, vendor_name, action_status, learning_summary, payload_json, created_at
            FROM finance_learning_events
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT %s
            """
        )
        params.append(max(1, int(limit or 20)))
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall() or []
        events: list[Dict[str, Any]] = []
        for row in rows:
            payload = dict(row) if not isinstance(row, dict) else row
            events.append(
                {
                    "ap_item_id": payload.get("ap_item_id"),
                    "event_type": payload.get("event_type"),
                    "actor_id": payload.get("actor_id"),
                    "vendor_name": payload.get("vendor_name"),
                    "action_status": payload.get("action_status"),
                    "learning_summary": payload.get("learning_summary"),
                    "payload": self._normalize_payload(payload.get("payload_json")),
                    "created_at": payload.get("created_at"),
                }
            )
        return events

    def record_outcome_calibration(
        self,
        *,
        action_key: str,
        vendor_name: Optional[str] = None,
        was_success: bool = False,
        matched_shadow: bool = False,
        verification_succeeded: bool = False,
        recovery_attempted: bool = False,
        recovery_succeeded: bool = False,
        confidence_delta: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        row = {
            "organization_id": self.organization_id,
            "vendor_name": str(vendor_name or "").strip() or None,
            "action_key": str(action_key or "unknown").strip().lower() or "unknown",
            "metadata": dict(metadata or {}),
        }
        if not self.enabled:
            return {
                "vendor_name": row["vendor_name"],
                "action_key": row["action_key"],
                "sample_count": 1,
                "success_rate": 1.0 if was_success else 0.0,
                "shadow_match_rate": 1.0 if matched_shadow else 0.0,
                "verification_rate": 1.0 if verification_succeeded else 0.0,
                "recovery_success_rate": 1.0 if recovery_succeeded else 0.0,
                "avg_confidence_delta": float(confidence_delta or 0.0),
            }
        now = self._now_iso()
        sql = (
            """
            INSERT INTO finance_learning_calibration (
                id, organization_id, vendor_name, action_key, sample_count, success_count,
                shadow_match_count, verification_success_count, recovery_attempt_count,
                recovery_success_count, confidence_delta_total, metadata_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, vendor_name, action_key)
            DO UPDATE SET
                sample_count = finance_learning_calibration.sample_count + EXCLUDED.sample_count,
                success_count = finance_learning_calibration.success_count + EXCLUDED.success_count,
                shadow_match_count = finance_learning_calibration.shadow_match_count + EXCLUDED.shadow_match_count,
                verification_success_count = finance_learning_calibration.verification_success_count + EXCLUDED.verification_success_count,
                recovery_attempt_count = finance_learning_calibration.recovery_attempt_count + EXCLUDED.recovery_attempt_count,
                recovery_success_count = finance_learning_calibration.recovery_success_count + EXCLUDED.recovery_success_count,
                confidence_delta_total = finance_learning_calibration.confidence_delta_total + EXCLUDED.confidence_delta_total,
                metadata_json = EXCLUDED.metadata_json,
                updated_at = EXCLUDED.updated_at
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    f"cal_{uuid.uuid4().hex}",
                    self.organization_id,
                    row["vendor_name"],
                    row["action_key"],
                    1,
                    1 if was_success else 0,
                    1 if matched_shadow else 0,
                    1 if verification_succeeded else 0,
                    1 if recovery_attempted else 0,
                    1 if recovery_succeeded else 0,
                    float(confidence_delta or 0.0),
                    json.dumps(row["metadata"], sort_keys=True, default=str),
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_outcome_calibration(
            vendor_name=row["vendor_name"],
            action_key=row["action_key"],
        )

    def get_outcome_calibration(
        self,
        *,
        vendor_name: Optional[str] = None,
        action_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {}
        clauses = ["organization_id = %s"]
        params: List[Any] = [self.organization_id]
        if vendor_name is None:
            clauses.append("vendor_name IS NULL")
        else:
            clauses.append("vendor_name = %s")
            params.append(str(vendor_name).strip())
        if action_key:
            clauses.append("action_key = %s")
            params.append(str(action_key).strip().lower())
        sql = (
            f"""
            SELECT vendor_name, action_key, sample_count, success_count, shadow_match_count,
                   verification_success_count, recovery_attempt_count, recovery_success_count,
                   confidence_delta_total, metadata_json, updated_at
            FROM finance_learning_calibration
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        if not row:
            return {}
        payload = dict(row) if not isinstance(row, dict) else row
        sample_count = max(1, int(payload.get("sample_count") or 0))
        recovery_attempt_count = int(payload.get("recovery_attempt_count") or 0)
        return {
            "vendor_name": payload.get("vendor_name"),
            "action_key": payload.get("action_key"),
            "sample_count": sample_count,
            "success_rate": round(int(payload.get("success_count") or 0) / sample_count, 4),
            "shadow_match_rate": round(int(payload.get("shadow_match_count") or 0) / sample_count, 4),
            "verification_rate": round(int(payload.get("verification_success_count") or 0) / sample_count, 4),
            "recovery_success_rate": round(
                (int(payload.get("recovery_success_count") or 0) / recovery_attempt_count)
                if recovery_attempt_count > 0 else 0.0,
                4,
            ),
            "avg_confidence_delta": round(float(payload.get("confidence_delta_total") or 0.0) / sample_count, 4),
            "metadata": self._normalize_payload(payload.get("metadata_json")),
            "updated_at": payload.get("updated_at"),
        }

    def get_extraction_confidence_adjustments(
        self,
        *,
        vendor_name: str,
        sender_domain: Optional[str] = None,
        document_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return self.correction_learning.get_extraction_confidence_adjustments(
                vendor_name=vendor_name,
                sender_domain=sender_domain,
                document_type=document_type,
            ) or {}
        except Exception as exc:
            logger.warning("finance_learning.get_extraction_confidence_adjustments failed: %s", exc)
            return {}

    def suggest_corrections(
        self,
        *,
        vendor_name: str,
        amount: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        try:
            suggestions = self.correction_learning.suggest(vendor_name, amount)
            return suggestions if isinstance(suggestions, list) else []
        except Exception as exc:
            logger.warning("finance_learning.suggest_corrections failed: %s", exc)
            return []

    def suggest_field_correction(
        self,
        field: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            suggestion = self.correction_learning.suggest(field, dict(context or {}))
            return suggestion if isinstance(suggestion, dict) else None
        except Exception as exc:
            logger.warning("finance_learning.suggest_field_correction failed: %s", exc)
            return None

    def list_recent_corrections(
        self,
        vendor_name: str,
        *,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        try:
            corrections = self.correction_learning.get_recent_corrections(vendor_name, limit=limit)
            return corrections if isinstance(corrections, list) else []
        except Exception as exc:
            logger.warning("finance_learning.list_recent_corrections failed: %s", exc)
            return []

    def suggest_gl_code(
        self,
        vendor: str,
        amount: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            suggestion = self.pattern_learning.suggest_gl_code(vendor=vendor, amount=amount)
            return suggestion if isinstance(suggestion, dict) else None
        except Exception as exc:
            logger.warning("finance_learning.suggest_gl_code failed: %s", exc)
            return None

    def get_vendor_pattern(self, vendor: str) -> Optional[Dict[str, Any]]:
        if not vendor:
            return None
        try:
            pattern = self.pattern_learning.get_vendor_pattern(vendor)
            return pattern if isinstance(pattern, dict) else None
        except Exception as exc:
            logger.warning("finance_learning.get_vendor_pattern failed: %s", exc)
            return None

    def get_categorization_hint(
        self,
        vendor: str,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if self.compounding_learning is None:
            return None
        try:
            hint = self.compounding_learning.get_categorization_hint(vendor, description or "")
            return hint if isinstance(hint, dict) else None
        except Exception as exc:
            logger.warning("finance_learning.get_categorization_hint failed: %s", exc)
            return None

    def get_patterns_for_matching(
        self,
        text: str,
        *,
        min_confidence: float = 0.5,
    ) -> List[Any]:
        if self.compounding_learning is None:
            return []
        try:
            patterns = self.compounding_learning.get_patterns_for_matching(
                text,
                min_confidence=min_confidence,
            )
            return list(patterns or [])
        except Exception as exc:
            logger.warning("finance_learning.get_patterns_for_matching failed: %s", exc)
            return []

    def record_pattern_feedback(self, pattern_id: str, was_correct: bool) -> bool:
        resolved_pattern_id = str(pattern_id or "").strip()
        if not resolved_pattern_id or self.compounding_learning is None:
            return False
        try:
            self.compounding_learning.record_pattern_usage(resolved_pattern_id, bool(was_correct))
            return True
        except Exception as exc:
            logger.warning("finance_learning.record_pattern_feedback failed for %s: %s", resolved_pattern_id, exc)
            return False

    def record_vendor_gl_approval(
        self,
        *,
        vendor: str,
        gl_code: str,
        gl_description: str,
        amount: float,
        currency: str,
        was_auto_approved: bool,
        was_corrected: bool,
        ap_item_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        adapter_results: Dict[str, Any] = {}
        resolved_vendor = str(vendor or "").strip()
        resolved_gl_code = str(gl_code or "").strip()
        if not resolved_vendor or not resolved_gl_code:
            return adapter_results
        try:
            self.pattern_learning.record_approval(
                vendor=resolved_vendor,
                gl_code=resolved_gl_code,
                gl_description=str(gl_description or "Accounts Payable").strip() or "Accounts Payable",
                amount=safe_float(amount, 0.0),
                currency=str(currency or "USD").strip() or "USD",
                was_auto_approved=bool(was_auto_approved),
                was_corrected=bool(was_corrected),
            )
            adapter_results["pattern_learning"] = "vendor_gl_approval"
        except Exception as exc:
            logger.warning("finance_learning.record_vendor_gl_approval failed: %s", exc)
        self._persist_learning_event(
            event_type="vendor_gl_approval",
            ap_item_id=ap_item_id,
            actor_id=actor_id,
            vendor_name=resolved_vendor,
            action_status="auto_approved" if was_auto_approved else "approved",
            learning_summary="vendor gl approval recorded",
            payload={
                "vendor": resolved_vendor,
                "gl_code": resolved_gl_code,
                "gl_description": str(gl_description or "Accounts Payable").strip() or "Accounts Payable",
                "amount": safe_float(amount, 0.0),
                "currency": str(currency or "USD").strip() or "USD",
                "was_auto_approved": bool(was_auto_approved),
                "was_corrected": bool(was_corrected),
                "metadata": dict(metadata or {}),
            },
        )
        return adapter_results

    def record_manual_field_correction(
        self,
        *,
        field: str,
        original_value: Any,
        corrected_value: Any,
        context: Dict[str, Any],
        actor_id: str,
        invoice_id: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> Dict[str, Any]:
        correction_result: Dict[str, Any] = {}
        review_result: Dict[str, Any] = {}
        correction_svc = self.correction_learning
        review_outcome = context.get("review_outcome") if isinstance(context, dict) else {}
        if not isinstance(review_outcome, dict):
            review_outcome = {}
        selected_source = str(
            review_outcome.get("selected_source")
            or context.get("selected_source")
            or "manual"
        ).strip().lower() or "manual"
        outcome_type = str(review_outcome.get("outcome_type") or "corrected").strip().lower() or "corrected"
        outcome_tags = review_outcome.get("outcome_tags")
        if not isinstance(outcome_tags, list):
            outcome_tags = ["corrected", "manual_entry"]
        created_at = str(
            review_outcome.get("created_at")
            or context.get("resolved_at")
            or ""
        ).strip() or None

        correction_result = correction_svc.record_correction(
            correction_type=field,
            original_value=original_value,
            corrected_value=corrected_value,
            context=dict(context or {}),
            user_id=str(actor_id or "").strip() or "operator",
            invoice_id=invoice_id,
            feedback=feedback,
        )
        if hasattr(correction_svc, "record_review_outcome"):
            review_result = correction_svc.record_review_outcome(
                field_name=field,
                outcome_type=outcome_type,
                context=dict(context or {}),
                user_id=str(actor_id or "").strip() or "operator",
                selected_source=selected_source,
                outcome_tags=outcome_tags,
                created_at=created_at,
            )
        self.record_outcome_calibration(
            action_key=f"field_correction:{field}",
            vendor_name=str((context or {}).get("vendor_name") or (context or {}).get("vendor") or "").strip() or None,
            was_success=True,
            matched_shadow=True,
            verification_succeeded=outcome_type in {"confirmed", "corrected", "accepted"},
            confidence_delta=0.0,
            metadata={
                "field": field,
                "outcome_type": outcome_type,
                "selected_source": selected_source,
            },
        )
        return {
            "correction_learning": correction_result,
            "review_outcome": review_result,
        }

    def record_decision_feedback(
        self,
        *,
        decision: Optional[Dict[str, Any]] = None,
        was_correct: bool,
        correction: Optional[Dict[str, Any]] = None,
        actor_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_decision = dict(decision or {})
        normalized_context = dict(context or {})
        extraction = normalized_decision.get("extraction") if isinstance(normalized_decision.get("extraction"), dict) else {}
        vendor_name = str(
            normalized_context.get("vendor_name")
            or extraction.get("vendor")
            or normalized_context.get("vendor")
            or ""
        ).strip()
        pattern_ids = normalized_context.get("pattern_ids")
        if not isinstance(pattern_ids, list):
            pattern_ids = []
        for pattern_id in [str(value).strip() for value in pattern_ids if str(value).strip()]:
            self.record_pattern_feedback(pattern_id, bool(was_correct))

        correction_result = {}
        if correction and self.compounding_learning is not None:
            try:
                correction_result = self.compounding_learning.record_correction(
                    correction_type="categorization",
                    original_value={
                        "decision": normalized_decision.get("decision"),
                        "confidence": normalized_decision.get("confidence"),
                        "vendor": vendor_name,
                    },
                    corrected_value=dict(correction),
                    user_email=str(actor_id or "system").strip() or "system",
                    context={
                        "vendor_name": vendor_name,
                        **normalized_context,
                    },
                    organization_id=self.organization_id,
                ).to_dict()
            except Exception as exc:
                logger.warning("decision correction feedback failed: %s", exc)

        event = self._persist_learning_event(
            event_type="decision_feedback",
            ap_item_id=normalized_context.get("ap_item_id"),
            actor_id=actor_id,
            vendor_name=vendor_name,
            action_status="correct" if was_correct else "incorrect",
            learning_summary="Decision feedback recorded",
            payload={
                "decision": normalized_decision,
                "was_correct": bool(was_correct),
                "correction": dict(correction or {}),
                "context": normalized_context,
                "correction_result": correction_result,
            },
        )
        return {
            "event": event,
            "correction_result": correction_result,
        }

    def record_action_outcome(
        self,
        *,
        event_type: str,
        ap_item: Optional[Dict[str, Any]],
        response: Optional[Dict[str, Any]],
        actor_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        item = dict(ap_item or {})
        result = dict(response or {})
        extra = dict(metadata or {})
        event_type_token = str(event_type or "").strip().lower() or "unknown"
        status = str(
            result.get("status")
            or result.get("action_status")
            or extra.get("status")
            or ""
        ).strip().lower() or None
        vendor_name = str(item.get("vendor_name") or item.get("vendor") or "").strip()
        ap_item_id = str(item.get("id") or result.get("ap_item_id") or "").strip() or None
        learning_summary = event_type_token.replace("_", " ")
        adapter_results: Dict[str, Any] = {}

        result_payload = result.get("result") if isinstance(result.get("result"), dict) else {}
        gl_code = str(
            result.get("gl_code")
            or result_payload.get("gl_code")
            or ""
        ).strip()
        gl_description = str(
            result.get("gl_description")
            or result_payload.get("gl_description")
            or "Accounts Payable"
        ).strip() or "Accounts Payable"
        amount = safe_float(item.get("amount") or result.get("amount"), 0.0)
        currency = str(item.get("currency") or result.get("currency") or "USD").strip() or "USD"

        if event_type_token in {"invoice_approved", "erp_post_completed", "route_low_risk_for_approval"} and vendor_name and gl_code:
            adapter_results.update(
                self.record_vendor_gl_approval(
                    vendor=vendor_name,
                    gl_code=gl_code,
                    gl_description=gl_description,
                    amount=amount,
                    currency=currency,
                    was_auto_approved=event_type_token == "erp_post_completed" and bool(result.get("autonomous_requested")),
                    was_corrected=False,
                    ap_item_id=ap_item_id,
                    actor_id=actor_id,
                    metadata={"event_type": event_type_token, "response_status": status},
                )
            )

        if event_type_token in {"entity_route_resolved", "approval_reassigned"} and self.compounding_learning is not None:
            try:
                correction = self.compounding_learning.record_correction(
                    correction_type="routing",
                    original_value={
                        "event_type": event_type_token,
                        "entity_code": item.get("entity_code"),
                    },
                    corrected_value={
                        "entity_selection": extra.get("entity_selection") or result.get("entity_selection"),
                        "assignee": result_payload.get("assignee") or extra.get("assignee"),
                    },
                    user_email=str(actor_id or "system").strip() or "system",
                    context={
                        "ap_item_id": ap_item_id,
                        "vendor_name": vendor_name,
                        "organization_id": self.organization_id,
                    },
                    organization_id=self.organization_id,
                )
                adapter_results["routing_learning"] = correction.to_dict()
            except Exception as exc:
                logger.warning("routing learning outcome failed: %s", exc)

        override_used = any(
            bool(result_payload.get(key))
            for key in ("budget_override", "confidence_override", "po_override")
        ) or bool(result_payload.get("override_justification") or extra.get("override_justification"))
        if override_used:
            learning_summary = "human override recorded"

        event = self._persist_learning_event(
            event_type=event_type_token,
            ap_item_id=ap_item_id,
            actor_id=actor_id,
            vendor_name=vendor_name or None,
            action_status=status,
            learning_summary=learning_summary,
            payload={
                "response": result,
                "metadata": extra,
                "adapter_results": adapter_results,
            },
        )
        self.record_outcome_calibration(
            action_key=event_type_token,
            vendor_name=vendor_name or None,
            was_success=status not in {"error", "failed", "blocked"},
            matched_shadow=bool(extra.get("matched_shadow")),
            verification_succeeded=bool(extra.get("verification_succeeded")),
            recovery_attempted=bool(extra.get("recovery_attempted")),
            recovery_succeeded=bool(extra.get("recovery_succeeded")),
            confidence_delta=safe_float(extra.get("confidence_delta"), 0.0),
            metadata={
                "status": status,
                "event_type": event_type_token,
                "response_reason": result.get("reason"),
            },
        )
        return {
            "event": event,
            "adapter_results": adapter_results,
        }

    def record_runtime_outcome(
        self,
        *,
        ap_item: Optional[Dict[str, Any]],
        response: Optional[Dict[str, Any]],
        shadow_decision: Optional[Dict[str, Any]] = None,
        actor_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        item = ap_item if isinstance(ap_item, dict) else {}
        result = response if isinstance(response, dict) else {}
        metadata = item.get("metadata")
        if isinstance(metadata, str) and metadata.strip():
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        metadata = metadata if isinstance(metadata, dict) else {}
        learning_result: Dict[str, Any] = {
            "recorded": [],
            "shadow_feedback": {},
        }

        vendor_name = str(item.get("vendor_name") or item.get("vendor") or "").strip()
        amount = safe_float(item.get("amount") or result.get("amount"), 0.0)
        currency = str(item.get("currency") or result.get("currency") or "USD").strip() or "USD"
        erp_result = result.get("erp_result") if isinstance(result.get("erp_result"), dict) else {}
        gl_code = str(
            result.get("gl_code")
            or erp_result.get("gl_code")
            or metadata.get("gl_code")
            or ""
        ).strip()
        gl_description = str(
            result.get("gl_description")
            or erp_result.get("gl_description")
            or "Accounts Payable"
        ).strip() or "Accounts Payable"
        actual_action = self._resolved_actual_action(result)
        proposed_action = ""
        if isinstance(shadow_decision, dict):
            proposed_action = str(shadow_decision.get("proposed_action") or "").strip()
        learning_result["shadow_feedback"] = {
            "proposed_action": proposed_action or None,
            "actual_action": actual_action,
            "matched": bool(proposed_action and proposed_action == actual_action),
        }

        if vendor_name and gl_code and actual_action in {"auto_approve_post", "processed"}:
            adapter_results = self.record_vendor_gl_approval(
                vendor=vendor_name,
                gl_code=gl_code,
                gl_description=gl_description,
                amount=amount,
                currency=currency,
                was_auto_approved=(actual_action == "auto_approve_post"),
                was_corrected=False,
                ap_item_id=item.get("id") or result.get("ap_item_id"),
                actor_id=actor_id,
                metadata={"actual_action": actual_action},
            )
            if adapter_results.get("pattern_learning"):
                learning_result["recorded"].append("vendor_gl_approval")

        pattern_ids = []
        if isinstance(shadow_decision, dict):
            raw_pattern_ids = shadow_decision.get("pattern_ids")
            if isinstance(raw_pattern_ids, list):
                pattern_ids = [str(value).strip() for value in raw_pattern_ids if str(value).strip()]
        if pattern_ids and self.compounding_learning is not None:
            was_correct = bool(proposed_action and proposed_action == actual_action)
            for pattern_id in pattern_ids:
                try:
                    if not self.record_pattern_feedback(pattern_id, was_correct):
                        continue
                    learning_result["recorded"].append(f"pattern_feedback:{pattern_id}")
                except Exception as exc:
                    logger.warning("pattern usage feedback failed for %s: %s", pattern_id, exc)

        if actor_id:
            learning_result["actor_id"] = str(actor_id).strip() or None
        verification_succeeded = bool(
            result.get("post_verified")
            or erp_result.get("verified")
            or result.get("verification_succeeded")
        )
        confidence_delta = 0.0
        if proposed_action:
            confidence_delta = 1.0 if proposed_action == actual_action else -1.0
        self.record_outcome_calibration(
            action_key=actual_action,
            vendor_name=vendor_name or None,
            was_success=actual_action not in {"failed", "unknown"},
            matched_shadow=bool(proposed_action and proposed_action == actual_action),
            verification_succeeded=verification_succeeded,
            recovery_attempted=bool(result.get("recovery_attempted")),
            recovery_succeeded=bool(result.get("recovery_succeeded")),
            confidence_delta=confidence_delta,
            metadata={
                "status": result.get("status"),
                "shadow_proposed_action": proposed_action or None,
                "actual_action": actual_action,
            },
        )
        learning_result["event"] = self._persist_learning_event(
            event_type="runtime_outcome",
            ap_item_id=item.get("id") or result.get("ap_item_id"),
            actor_id=actor_id,
            vendor_name=vendor_name or None,
            action_status=result.get("status"),
            learning_summary=actual_action,
            payload={
                "response": result,
                "shadow_decision": dict(shadow_decision or {}),
                "learning_result": {
                    "recorded": list(learning_result.get("recorded") or []),
                    "shadow_feedback": dict(learning_result.get("shadow_feedback") or {}),
                },
            },
        )
        return learning_result


def get_finance_learning_service(
    organization_id: str = "default",
    *,
    db: Optional[ClearledgrDB] = None,
) -> FinanceLearningService:
    key = (str(organization_id or "default").strip() or "default", id(db) if db is not None else 0)
    service = _finance_learning_services.get(key)
    if service is None:
        service = FinanceLearningService(key[0], db=db)
        _finance_learning_services[key] = service
    return service
