"""
Correction Learning Service

When users correct the agent's decisions, learn from those corrections
to improve future accuracy.

Learns from:
- GL code corrections
- Vendor name corrections
- Amount corrections
- Classification corrections
- Approval/rejection overrides

Architecture: Part of the MEMORY LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
from enum import Enum

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


class CorrectionType(str, Enum):
    """Canonical correction categories used across services."""

    GL_CODE = "gl_code"
    VENDOR = "vendor"
    AMOUNT = "amount"
    CURRENCY = "currency"
    INVOICE_NUMBER = "invoice_number"
    DOCUMENT_TYPE = "document_type"
    DUE_DATE = "due_date"
    CLASSIFICATION = "classification"
    APPROVAL = "approval"


@dataclass
class Correction:
    """A user correction to agent output."""
    correction_id: str
    correction_type: str  # "gl_code", "vendor", "amount", "classification", "approval"
    original_value: Any
    corrected_value: Any
    context: Dict[str, Any]
    user_id: str
    timestamp: str
    invoice_id: Optional[str] = None
    vendor: Optional[str] = None
    feedback: Optional[str] = None  # User's explanation


@dataclass
class LearningRule:
    """A rule learned from corrections."""
    rule_id: str
    rule_type: str
    condition: Dict[str, Any]
    action: Dict[str, Any]
    confidence: float
    learned_from: int  # Number of corrections
    created_at: str
    last_applied: Optional[str] = None
    success_rate: float = 1.0


class CorrectionLearningService:
    """
    Learns from user corrections to improve future decisions.
    
    Usage:
        service = CorrectionLearningService("org_123")
        
        # Record a correction
        service.record_correction(
            correction_type="gl_code",
            original_value="6100",
            corrected_value="6150",
            context={"vendor": "Stripe", "category": "software"},
            user_id="user@acme.com"
        )
        
        # Ask if agent should suggest learned value
        suggestion = service.suggest("gl_code", {"vendor": "Stripe"})
        if suggestion:
            print(f"Suggested GL: {suggestion['value']} (learned from {suggestion['learned_from']} corrections)")
    """
    
    _RULES_TTL_SECONDS: int = 300  # refresh in-memory cache every 5 minutes

    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()

        # In-memory cache backed by DB
        self._corrections: List[Correction] = []
        self._learned_rules: Dict[str, LearningRule] = {}
        self._vendor_preferences: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._rules_loaded_at: float = 0.0  # monotonic timestamp of last DB load

        self._init_tables()
        self._load_rules()
    
    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    def _init_tables(self):
        """Create tables for persisting corrections and learned rules."""
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS agent_corrections (
                        id TEXT PRIMARY KEY,
                        organization_id TEXT NOT NULL,
                        correction_type TEXT NOT NULL,
                        original_value TEXT,
                        corrected_value TEXT,
                        context TEXT,
                        user_id TEXT,
                        invoice_id TEXT,
                        vendor TEXT,
                        feedback TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS agent_learned_rules (
                        id TEXT PRIMARY KEY,
                        organization_id TEXT NOT NULL,
                        rule_type TEXT NOT NULL,
                        condition TEXT,
                        action TEXT,
                        confidence REAL,
                        learned_from INTEGER,
                        created_at TEXT NOT NULL,
                        last_applied TEXT,
                        success_rate REAL DEFAULT 1.0
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS agent_correction_events (
                        id TEXT PRIMARY KEY,
                        organization_id TEXT NOT NULL,
                        ap_item_id TEXT,
                        invoice_id TEXT,
                        field_name TEXT NOT NULL,
                        correction_type TEXT NOT NULL,
                        original_value TEXT,
                        corrected_value TEXT,
                        selected_source TEXT,
                        source_channel TEXT,
                        event_source TEXT,
                        user_id TEXT,
                        vendor_name TEXT,
                        sender TEXT,
                        sender_domain TEXT,
                        subject TEXT,
                        document_type TEXT,
                        layout_key TEXT,
                        attachment_names_json TEXT,
                        expected_fields_json TEXT,
                        input_payload_json TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS vendor_layout_error_stats (
                        id TEXT PRIMARY KEY,
                        organization_id TEXT NOT NULL,
                        vendor_name TEXT NOT NULL,
                        sender_domain TEXT,
                        layout_key TEXT NOT NULL,
                        document_type TEXT,
                        field_name TEXT NOT NULL,
                        correction_count INTEGER NOT NULL DEFAULT 0,
                        first_corrected_at TEXT,
                        last_corrected_at TEXT,
                        last_ap_item_id TEXT,
                        last_original_value TEXT,
                        last_corrected_value TEXT,
                        UNIQUE(organization_id, vendor_name, layout_key, field_name)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS reviewed_extraction_cases (
                        id TEXT PRIMARY KEY,
                        organization_id TEXT NOT NULL,
                        ap_item_id TEXT NOT NULL,
                        vendor_name TEXT,
                        sender_domain TEXT,
                        layout_key TEXT,
                        document_type TEXT,
                        correction_fields_json TEXT,
                        input_payload_json TEXT NOT NULL,
                        expected_fields_json TEXT NOT NULL,
                        source_event_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(organization_id, ap_item_id)
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not init correction tables: {e}")

    def _load_rules(self):
        """Load learned rules from DB into memory cache."""
        import json as _json
        import time as _time
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT * FROM agent_learned_rules WHERE organization_id = ?",
                    (self.organization_id,),
                )
                fresh: Dict[str, LearningRule] = {}
                for row in cur.fetchall():
                    r = dict(row)
                    rule = LearningRule(
                        rule_id=r["id"],
                        rule_type=r["rule_type"],
                        condition=_json.loads(r["condition"]) if r.get("condition") else {},
                        action=_json.loads(r["action"]) if r.get("action") else {},
                        confidence=r.get("confidence", 0.5),
                        learned_from=r.get("learned_from", 1),
                        created_at=r.get("created_at", ""),
                        last_applied=r.get("last_applied"),
                        success_rate=r.get("success_rate", 1.0),
                    )
                    fresh[rule.rule_id] = rule
                self._learned_rules = fresh
                self._rules_loaded_at = _time.monotonic()
            if self._learned_rules:
                logger.info(
                    f"Loaded {len(self._learned_rules)} learned rules for {self.organization_id}"
                )
        except Exception as e:
            logger.error("Could not load learned rules: %s", e)

    def _persist_correction(self, correction: Correction):
        """Write a correction to the DB."""
        import json as _json
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """INSERT OR IGNORE INTO agent_corrections
                    (id, organization_id, correction_type, original_value,
                     corrected_value, context, user_id, invoice_id, vendor,
                     feedback, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        correction.correction_id,
                        self.organization_id,
                        correction.correction_type,
                        str(correction.original_value),
                        str(correction.corrected_value),
                        _json.dumps(correction.context),
                        correction.user_id,
                        correction.invoice_id,
                        correction.vendor,
                        correction.feedback,
                        correction.timestamp,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error("Could not persist correction: %s", e)

    def _persist_rule(self, rule: LearningRule):
        """Upsert a learned rule to the DB."""
        import json as _json
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """INSERT OR REPLACE INTO agent_learned_rules
                    (id, organization_id, rule_type, condition, action,
                     confidence, learned_from, created_at, last_applied, success_rate)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rule.rule_id,
                        self.organization_id,
                        rule.rule_type,
                        _json.dumps(rule.condition),
                        _json.dumps(rule.action),
                        rule.confidence,
                        rule.learned_from,
                        rule.created_at,
                        rule.last_applied,
                        rule.success_rate,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error("Could not persist rule: %s", e)

    @staticmethod
    def _normalize_field_name(raw: Any) -> str:
        token = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "vendor_name": "vendor",
            "primary_amount": "amount",
            "total_amount": "amount",
            "primary_invoice": "invoice_number",
            "email_type": "document_type",
            "classification": "document_type",
        }
        return aliases.get(token, token or "unknown")

    @staticmethod
    def _normalize_document_type(raw: Any) -> str:
        token = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "credit_memo": "credit_note",
            "bank_statement": "statement",
        }
        return aliases.get(token, token or "invoice")

    @staticmethod
    def _normalize_vendor_name(raw: Any) -> str:
        return " ".join(str(raw or "").strip().split())

    @staticmethod
    def _sender_domain(raw: Any) -> str:
        sender = str(raw or "").strip().lower()
        if "@" not in sender:
            return ""
        return sender.rsplit("@", 1)[-1]

    @staticmethod
    def _normalize_attachment_names(raw: Any) -> List[str]:
        if not isinstance(raw, list):
            return []
        names: List[str] = []
        for value in raw:
            token = str(value or "").strip()
            if token and token not in names:
                names.append(token)
        return names[:10]

    @staticmethod
    def _subject_pattern(raw: Any) -> str:
        subject = str(raw or "").strip().lower()
        if not subject:
            return ""
        subject = re.sub(r"\d+", "#", subject)
        subject = re.sub(r"[^a-z0-9# ]+", " ", subject)
        return " ".join(subject.split())[:120]

    def _derive_layout_key(self, context: Dict[str, Any]) -> str:
        sender_domain = self._sender_domain(context.get("sender") or context.get("sender_email"))
        document_type = self._normalize_document_type(context.get("document_type") or context.get("email_type"))
        attachment_names = self._normalize_attachment_names(context.get("attachment_names") or [])
        attachment_basis = "|".join(
            re.sub(r"\d+", "#", Path(name).stem.lower())[:24]
            for name in attachment_names[:3]
        )
        subject_basis = self._subject_pattern(context.get("subject"))
        basis = attachment_basis or subject_basis or "generic"
        return "::".join(part for part in (sender_domain or "unknown", document_type, basis) if part)

    def _normalize_input_payload(self, context: Dict[str, Any]) -> Dict[str, Any]:
        attachment_names = self._normalize_attachment_names(context.get("attachment_names") or [])
        attachments = []
        for name in attachment_names:
            attachments.append({"filename": name})
        body = str(
            context.get("body")
            or context.get("body_excerpt")
            or context.get("snippet")
            or ""
        )
        return {
            "subject": str(context.get("subject") or "").strip(),
            "body": body,
            "sender": str(context.get("sender") or context.get("sender_email") or "").strip(),
            "attachments": attachments,
        }

    def _normalize_expected_fields(
        self,
        *,
        correction_type: str,
        corrected_value: Any,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        expected = context.get("expected_fields")
        expected_fields = dict(expected) if isinstance(expected, dict) else {}
        corrected_map = {
            "vendor": "vendor",
            "amount": "primary_amount",
            "currency": "currency",
            "invoice_number": "primary_invoice",
            "document_type": "email_type",
            "due_date": "due_date",
        }
        corrected_key = corrected_map.get(correction_type)
        if corrected_key:
            expected_fields[corrected_key] = corrected_value
        vendor_name = self._normalize_vendor_name(
            expected_fields.get("vendor") or context.get("vendor")
        )
        if vendor_name:
            expected_fields["vendor"] = vendor_name
        document_type = self._normalize_document_type(
            expected_fields.get("email_type")
            or expected_fields.get("document_type")
            or context.get("document_type")
        )
        if document_type:
            expected_fields["email_type"] = document_type
        return expected_fields

    def _normalize_correction_event(
        self,
        *,
        correction: Correction,
    ) -> Dict[str, Any]:
        context = correction.context if isinstance(correction.context, dict) else {}
        field_name = self._normalize_field_name(correction.correction_type)
        vendor_name = self._normalize_vendor_name(context.get("vendor") or correction.vendor)
        sender = str(context.get("sender") or context.get("sender_email") or "").strip()
        sender_domain = self._sender_domain(sender)
        document_type = self._normalize_document_type(
            context.get("document_type") or context.get("email_type")
        )
        attachment_names = self._normalize_attachment_names(context.get("attachment_names") or [])
        expected_fields = self._normalize_expected_fields(
            correction_type=field_name,
            corrected_value=correction.corrected_value,
            context=context,
        )
        input_payload = self._normalize_input_payload(context)
        return {
            "event_id": f"cevt_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            "ap_item_id": str(context.get("ap_item_id") or "").strip() or None,
            "invoice_id": correction.invoice_id,
            "field_name": field_name,
            "correction_type": field_name,
            "original_value": correction.original_value,
            "corrected_value": correction.corrected_value,
            "selected_source": str(context.get("selected_source") or context.get("source") or "").strip() or None,
            "source_channel": str(context.get("source_channel") or "gmail").strip() or "gmail",
            "event_source": str(context.get("event_source") or "operator_review").strip() or "operator_review",
            "user_id": correction.user_id,
            "vendor_name": vendor_name or None,
            "sender": sender or None,
            "sender_domain": sender_domain or None,
            "subject": str(context.get("subject") or "").strip() or None,
            "document_type": document_type or None,
            "layout_key": str(context.get("layout_key") or self._derive_layout_key(context)).strip(),
            "attachment_names": attachment_names,
            "expected_fields": expected_fields,
            "input_payload": input_payload,
            "created_at": correction.timestamp,
        }

    def _persist_normalized_correction_event(self, normalized: Dict[str, Any]) -> str:
        event_id = str(normalized.get("event_id") or f"cevt_{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT OR REPLACE INTO agent_correction_events
                    (id, organization_id, ap_item_id, invoice_id, field_name, correction_type,
                     original_value, corrected_value, selected_source, source_channel, event_source,
                     user_id, vendor_name, sender, sender_domain, subject, document_type, layout_key,
                     attachment_names_json, expected_fields_json, input_payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        self.organization_id,
                        normalized.get("ap_item_id"),
                        normalized.get("invoice_id"),
                        normalized.get("field_name"),
                        normalized.get("correction_type"),
                        json.dumps(normalized.get("original_value")),
                        json.dumps(normalized.get("corrected_value")),
                        normalized.get("selected_source"),
                        normalized.get("source_channel"),
                        normalized.get("event_source"),
                        normalized.get("user_id"),
                        normalized.get("vendor_name"),
                        normalized.get("sender"),
                        normalized.get("sender_domain"),
                        normalized.get("subject"),
                        normalized.get("document_type"),
                        normalized.get("layout_key"),
                        json.dumps(normalized.get("attachment_names") or []),
                        json.dumps(normalized.get("expected_fields") or {}),
                        json.dumps(normalized.get("input_payload") or {}),
                        normalized.get("created_at"),
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.error("Could not persist normalized correction event: %s", exc)
        return event_id

    def _update_vendor_layout_error_stats(self, normalized: Dict[str, Any]) -> Optional[str]:
        vendor_name = self._normalize_vendor_name(normalized.get("vendor_name"))
        layout_key = str(normalized.get("layout_key") or "").strip()
        field_name = self._normalize_field_name(normalized.get("field_name"))
        if not vendor_name or not layout_key or not field_name:
            return None
        stat_id = f"vles_{self.organization_id}_{vendor_name}_{layout_key}_{field_name}"
        stat_id = re.sub(r"[^a-zA-Z0-9_:-]+", "_", stat_id)[:180]
        now = str(normalized.get("created_at") or datetime.now().isoformat())
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO vendor_layout_error_stats
                    (id, organization_id, vendor_name, sender_domain, layout_key, document_type,
                     field_name, correction_count, first_corrected_at, last_corrected_at, last_ap_item_id,
                     last_original_value, last_corrected_value)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(organization_id, vendor_name, layout_key, field_name)
                    DO UPDATE SET
                        correction_count = correction_count + 1,
                        last_corrected_at = excluded.last_corrected_at,
                        last_ap_item_id = excluded.last_ap_item_id,
                        last_original_value = excluded.last_original_value,
                        last_corrected_value = excluded.last_corrected_value,
                        sender_domain = excluded.sender_domain,
                        document_type = excluded.document_type
                    """,
                    (
                        stat_id,
                        self.organization_id,
                        vendor_name,
                        normalized.get("sender_domain"),
                        layout_key,
                        normalized.get("document_type"),
                        field_name,
                        now,
                        now,
                        normalized.get("ap_item_id"),
                        json.dumps(normalized.get("original_value")),
                        json.dumps(normalized.get("corrected_value")),
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.error("Could not update vendor/layout error stats: %s", exc)
            return None
        return stat_id

    def _upsert_reviewed_extraction_case(
        self,
        normalized: Dict[str, Any],
        *,
        source_event_id: str,
    ) -> Optional[str]:
        ap_item_id = str(normalized.get("ap_item_id") or "").strip()
        input_payload = normalized.get("input_payload") if isinstance(normalized.get("input_payload"), dict) else {}
        expected_fields = normalized.get("expected_fields") if isinstance(normalized.get("expected_fields"), dict) else {}
        if not ap_item_id or not input_payload or not expected_fields:
            return None
        sender = str(input_payload.get("sender") or "").strip()
        subject = str(input_payload.get("subject") or "").strip()
        if not sender or not subject:
            return None

        case_id = f"reviewed_{ap_item_id}"
        now = str(normalized.get("created_at") or datetime.now().isoformat())
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT correction_fields_json FROM reviewed_extraction_cases
                    WHERE organization_id = ? AND ap_item_id = ?
                    LIMIT 1
                    """,
                    (self.organization_id, ap_item_id),
                )
                row = cur.fetchone()
                existing_fields: List[str] = []
                if row and row[0]:
                    try:
                        existing_fields = json.loads(row[0]) or []
                    except Exception:
                        existing_fields = []
                correction_fields = sorted(
                    {
                        *(str(value or "").strip() for value in existing_fields),
                        str(normalized.get("field_name") or "").strip(),
                    }
                )
                cur.execute(
                    """
                    INSERT INTO reviewed_extraction_cases
                    (id, organization_id, ap_item_id, vendor_name, sender_domain, layout_key, document_type,
                     correction_fields_json, input_payload_json, expected_fields_json, source_event_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(organization_id, ap_item_id)
                    DO UPDATE SET
                        vendor_name = excluded.vendor_name,
                        sender_domain = excluded.sender_domain,
                        layout_key = excluded.layout_key,
                        document_type = excluded.document_type,
                        correction_fields_json = excluded.correction_fields_json,
                        input_payload_json = excluded.input_payload_json,
                        expected_fields_json = excluded.expected_fields_json,
                        source_event_id = excluded.source_event_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        case_id,
                        self.organization_id,
                        ap_item_id,
                        normalized.get("vendor_name"),
                        normalized.get("sender_domain"),
                        normalized.get("layout_key"),
                        normalized.get("document_type"),
                        json.dumps(correction_fields),
                        json.dumps(input_payload),
                        json.dumps(expected_fields),
                        source_event_id,
                        now,
                        now,
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.error("Could not upsert reviewed extraction case: %s", exc)
            return None
        return case_id

    def list_reviewed_extraction_cases(self, limit: int = 500) -> List[Dict[str, Any]]:
        cases: List[Dict[str, Any]] = []
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT *
                    FROM reviewed_extraction_cases
                    WHERE organization_id = ?
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ?
                    """,
                    (self.organization_id, max(1, int(limit))),
                )
                rows = cur.fetchall()
        except Exception as exc:
            logger.error("Could not load reviewed extraction cases: %s", exc)
            return cases

        for row in rows:
            record = dict(row)
            input_payload = json.loads(record.get("input_payload_json") or "{}")
            expected_fields = json.loads(record.get("expected_fields_json") or "{}")
            correction_fields = json.loads(record.get("correction_fields_json") or "[]")
            case_id = str(record.get("id") or "").strip()
            cases.append(
                {
                    "id": case_id,
                    "input": input_payload,
                    "expected": expected_fields,
                    "metadata": {
                        "source": "reviewed_production_case",
                        "organization_id": self.organization_id,
                        "ap_item_id": record.get("ap_item_id"),
                        "vendor_name": record.get("vendor_name"),
                        "sender_domain": record.get("sender_domain"),
                        "layout_key": record.get("layout_key"),
                        "document_type": record.get("document_type"),
                        "correction_fields": correction_fields,
                        "reviewed_at": record.get("updated_at") or record.get("created_at"),
                    },
                }
            )
        return cases

    def list_vendor_layout_error_stats(self, limit: int = 500) -> List[Dict[str, Any]]:
        stats: List[Dict[str, Any]] = []
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT *
                    FROM vendor_layout_error_stats
                    WHERE organization_id = ?
                    ORDER BY correction_count DESC, last_corrected_at DESC
                    LIMIT ?
                    """,
                    (self.organization_id, max(1, int(limit))),
                )
                rows = cur.fetchall()
        except Exception as exc:
            logger.error("Could not load vendor/layout error stats: %s", exc)
            return stats

        for row in rows:
            record = dict(row)
            for field in ("last_original_value", "last_corrected_value"):
                try:
                    record[field] = json.loads(record.get(field) or "null")
                except Exception:
                    record[field] = record.get(field)
            stats.append(record)
        return stats

    def export_reviewed_extraction_cases(self, output_path: Path | str) -> Dict[str, Any]:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        cases = self.list_reviewed_extraction_cases()
        payload = {
            "generated_at": datetime.now().isoformat(),
            "organization_id": self.organization_id,
            "cases": cases,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return {
            "path": str(path),
            "case_count": len(cases),
        }

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def record_correction(
        self,
        correction_type: str,
        original_value: Any,
        corrected_value: Any,
        context: Dict[str, Any],
        user_id: str,
        invoice_id: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record a user correction and learn from it.
        
        Returns info about what was learned.
        """
        correction = Correction(
            correction_id=f"corr_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            correction_type=correction_type,
            original_value=original_value,
            corrected_value=corrected_value,
            context=context,
            user_id=user_id,
            timestamp=datetime.now().isoformat(),
            invoice_id=invoice_id,
            vendor=context.get("vendor"),
            feedback=feedback,
        )
        
        self._corrections.append(correction)
        self._persist_correction(correction)
        normalized_event = self._normalize_correction_event(correction=correction)
        normalized_event_id = self._persist_normalized_correction_event(normalized_event)
        stat_id = self._update_vendor_layout_error_stats(normalized_event)
        reviewed_case_id = self._upsert_reviewed_extraction_case(
            normalized_event,
            source_event_id=normalized_event_id,
        )
        export_result = None
        export_path = str(os.getenv("CLEARLEDGR_REVIEWED_EXTRACTION_EXPORT_PATH") or "").strip()
        if export_path:
            try:
                export_result = self.export_reviewed_extraction_cases(export_path)
            except Exception as exc:
                logger.error("Could not auto-export reviewed extraction cases: %s", exc)

        # Learn from the correction
        learned = self._learn_from_correction(correction)
        
        logger.info(
            f"Recorded correction: {correction_type} "
            f"{original_value} -> {corrected_value} "
            f"(vendor: {context.get('vendor', 'N/A')})"
        )
        
        return {
            "correction_id": correction.correction_id,
            "normalized_event_id": normalized_event_id,
            "vendor_layout_stat_id": stat_id,
            "reviewed_case_id": reviewed_case_id,
            "reviewed_case_export": export_result,
            "learned": learned,
            "message": self._generate_learning_message(correction, learned),
        }
    
    def _learn_from_correction(self, correction: Correction) -> Dict[str, Any]:
        """Extract learning from a correction."""
        learned = {
            "rules_created": 0,
            "rules_updated": 0,
            "preferences_updated": [],
        }
        
        if correction.correction_type == "gl_code":
            learned.update(self._learn_gl_code(correction))
        
        elif correction.correction_type == "vendor":
            learned.update(self._learn_vendor_name(correction))
        
        elif correction.correction_type == "amount":
            learned.update(self._learn_amount_pattern(correction))
        
        elif correction.correction_type == "classification":
            learned.update(self._learn_classification(correction))
        
        elif correction.correction_type == "approval":
            learned.update(self._learn_approval_preference(correction))
        
        return learned
    
    def _learn_gl_code(self, correction: Correction) -> Dict[str, Any]:
        """Learn GL code preferences from correction."""
        vendor = correction.vendor
        if not vendor:
            return {"rules_created": 0}
        
        # Create or update vendor GL preference
        rule_id = f"gl_{vendor.lower().replace(' ', '_')}"
        
        if rule_id in self._learned_rules:
            rule = self._learned_rules[rule_id]
            rule.learned_from += 1
            rule.action = {"gl_code": correction.corrected_value}
            rule.confidence = min(0.99, rule.confidence + 0.1)
            self._persist_rule(rule)
            return {"rules_updated": 1}
        else:
            rule = LearningRule(
                rule_id=rule_id,
                rule_type="gl_code",
                condition={"vendor": vendor},
                action={"gl_code": correction.corrected_value},
                confidence=0.7,
                learned_from=1,
                created_at=datetime.now().isoformat(),
            )
            self._learned_rules[rule_id] = rule
            self._persist_rule(rule)
            return {"rules_created": 1}
    
    def _learn_vendor_name(self, correction: Correction) -> Dict[str, Any]:
        """Learn vendor name normalization."""
        original = str(correction.original_value).lower()
        corrected = str(correction.corrected_value)
        
        # Store alias mapping
        rule_id = f"vendor_alias_{original.replace(' ', '_')}"

        rule = LearningRule(
            rule_id=rule_id,
            rule_type="vendor_alias",
            condition={"raw_vendor": original},
            action={"normalized_vendor": corrected},
            confidence=0.9,
            learned_from=1,
            created_at=datetime.now().isoformat(),
        )
        self._learned_rules[rule_id] = rule
        self._persist_rule(rule)

        return {"rules_created": 1, "preferences_updated": ["vendor_aliases"]}
    
    def _learn_amount_pattern(self, correction: Correction) -> Dict[str, Any]:
        """Learn amount expectations."""
        vendor = correction.vendor
        if not vendor:
            return {"rules_created": 0}
        
        # Update vendor expected amount range
        corrected_amount = float(correction.corrected_value) if correction.corrected_value else 0
        
        if vendor not in self._vendor_preferences:
            self._vendor_preferences[vendor] = {}
        
        prefs = self._vendor_preferences[vendor]
        if "expected_amounts" not in prefs:
            prefs["expected_amounts"] = []
        
        prefs["expected_amounts"].append(corrected_amount)
        
        # Keep last 10 amounts
        prefs["expected_amounts"] = prefs["expected_amounts"][-10:]
        
        return {"preferences_updated": ["amount_expectations"]}
    
    def _learn_classification(self, correction: Correction) -> Dict[str, Any]:
        """Learn document classification patterns."""
        # Learn that certain patterns should be classified differently
        context = correction.context
        
        rule_id = f"classify_{context.get('sender', 'unknown')[:20]}"

        rule = LearningRule(
            rule_id=rule_id,
            rule_type="classification",
            condition={
                "sender_contains": context.get("sender", ""),
                "subject_pattern": context.get("subject_pattern", ""),
            },
            action={"classification": correction.corrected_value},
            confidence=0.8,
            learned_from=1,
            created_at=datetime.now().isoformat(),
        )
        self._learned_rules[rule_id] = rule
        self._persist_rule(rule)

        return {"rules_created": 1}
    
    def _learn_approval_preference(self, correction: Correction) -> Dict[str, Any]:
        """Learn approval preferences (e.g., always auto-approve this vendor)."""
        vendor = correction.vendor
        if not vendor:
            return {"rules_created": 0}
        
        # If user approved something agent wanted to flag, learn to be less strict
        # If user rejected something agent auto-approved, learn to be more careful
        
        original_decision = correction.original_value  # e.g., "flag_for_review"
        user_decision = correction.corrected_value  # e.g., "approved"
        
        if original_decision == "flag_for_review" and user_decision == "approved":
            # User is more permissive - lower the threshold for this vendor
            if vendor not in self._vendor_preferences:
                self._vendor_preferences[vendor] = {}
            
            self._vendor_preferences[vendor]["approval_bias"] = "permissive"
            self._vendor_preferences[vendor]["auto_approve_threshold_adj"] = -0.1
            
            return {"preferences_updated": ["approval_threshold"]}
        
        elif original_decision == "auto_approved" and user_decision == "rejected":
            # User is more strict - raise the threshold
            if vendor not in self._vendor_preferences:
                self._vendor_preferences[vendor] = {}
            
            self._vendor_preferences[vendor]["approval_bias"] = "strict"
            self._vendor_preferences[vendor]["auto_approve_threshold_adj"] = 0.1
            
            return {"preferences_updated": ["approval_threshold"]}
        
        return {"rules_created": 0}
    
    def suggest(
        self,
        suggestion_type: str,
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Get a suggestion based on learned rules.

        Refreshes the in-memory rule cache from DB if it is older than
        _RULES_TTL_SECONDS (default 5 min), so corrections written by one
        process are visible to others without a restart.

        Returns None if no learned rule applies.
        """
        import time as _time
        if _time.monotonic() - self._rules_loaded_at > self._RULES_TTL_SECONDS:
            self._load_rules()

        if suggestion_type == "gl_code":
            return self._suggest_gl_code(context)
        
        elif suggestion_type == "vendor":
            return self._suggest_vendor_name(context)
        
        elif suggestion_type == "approval_threshold":
            return self._suggest_approval_threshold(context)
        
        return None
    
    def _suggest_gl_code(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Suggest GL code based on learned patterns."""
        vendor = context.get("vendor", "")
        if not vendor:
            return None
        
        rule_id = f"gl_{vendor.lower().replace(' ', '_')}"
        
        if rule_id in self._learned_rules:
            rule = self._learned_rules[rule_id]
            
            # Update last applied
            rule.last_applied = datetime.now().isoformat()
            
            return {
                "value": rule.action.get("gl_code"),
                "confidence": rule.confidence,
                "learned_from": rule.learned_from,
                "message": f"Learned from {rule.learned_from} previous correction(s)",
            }
        
        return None
    
    def _suggest_vendor_name(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Suggest normalized vendor name."""
        raw_vendor = context.get("raw_vendor", "").lower()
        if not raw_vendor:
            return None
        
        rule_id = f"vendor_alias_{raw_vendor.replace(' ', '_')}"
        
        if rule_id in self._learned_rules:
            rule = self._learned_rules[rule_id]
            return {
                "value": rule.action.get("normalized_vendor"),
                "confidence": rule.confidence,
                "learned_from": rule.learned_from,
            }
        
        return None
    
    def _suggest_approval_threshold(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Suggest approval threshold adjustment for vendor."""
        vendor = context.get("vendor", "")
        if not vendor or vendor not in self._vendor_preferences:
            return None
        
        prefs = self._vendor_preferences[vendor]
        
        if "auto_approve_threshold_adj" in prefs:
            return {
                "adjustment": prefs["auto_approve_threshold_adj"],
                "bias": prefs.get("approval_bias", "neutral"),
                "message": f"Adjusted based on previous corrections",
            }
        
        return None
    
    def _generate_learning_message(
        self,
        correction: Correction,
        learned: Dict[str, Any],
    ) -> str:
        """Generate a human-readable message about what was learned."""
        messages = []
        
        if learned.get("rules_created", 0) > 0:
            if correction.correction_type == "gl_code":
                messages.append(
                    f"Got it! I'll use GL {correction.corrected_value} for "
                    f"{correction.vendor} from now on."
                )
            elif correction.correction_type == "vendor":
                messages.append(
                    f"Learned: '{correction.original_value}' = '{correction.corrected_value}'"
                )
            else:
                messages.append(f"Created {learned['rules_created']} new rule(s)")
        
        if learned.get("rules_updated", 0) > 0:
            messages.append(f"Updated existing rule (now more confident)")
        
        if learned.get("preferences_updated"):
            prefs = ", ".join(learned["preferences_updated"])
            messages.append(f"Updated preferences: {prefs}")
        
        return " ".join(messages) if messages else "Correction recorded."
    
    def get_learning_stats(self) -> Dict[str, Any]:
        """Get statistics about what the agent has learned."""
        return {
            "total_corrections": len(self._corrections),
            "learned_rules": len(self._learned_rules),
            "vendor_preferences": len(self._vendor_preferences),
            "rules_by_type": self._count_rules_by_type(),
            "recent_corrections": len([
                c for c in self._corrections
                if (datetime.now() - datetime.fromisoformat(c.timestamp)).days <= 7
            ]),
        }
    
    def _count_rules_by_type(self) -> Dict[str, int]:
        """Count learned rules by type."""
        counts = defaultdict(int)
        for rule in self._learned_rules.values():
            counts[rule.rule_type] += 1
        return dict(counts)
    
    def ask_about_correction(
        self,
        correction_type: str,
        original_value: Any,
        corrected_value: Any,
        vendor: Optional[str] = None,
    ) -> str:
        """
        Generate a question to ask the user about applying a correction broadly.
        
        Called after a correction to see if user wants to apply it to all similar cases.
        """
        if correction_type == "gl_code" and vendor:
            return (
                f"Should I use GL {corrected_value} for all future "
                f"invoices from {vendor}?"
            )
        
        elif correction_type == "vendor":
            return (
                f"Should I always recognize '{original_value}' as '{corrected_value}'?"
            )
        
        elif correction_type == "approval" and vendor:
            if corrected_value == "approved":
                return (
                    f"Should I auto-approve similar invoices from {vendor} in the future?"
                )
            else:
                return (
                    f"Should I always flag {vendor} invoices for manual review?"
                )
        
        return ""


# Convenience function
def get_correction_learning(organization_id: str = "default") -> CorrectionLearningService:
    """Get a correction learning service instance."""
    return CorrectionLearningService(organization_id=organization_id)
