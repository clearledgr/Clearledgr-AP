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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from enum import Enum

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

_correction_learning_services: Dict[str, "CorrectionLearningService"] = {}


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
    _REFRESH_INTERVAL: int = 300   # full refresh interval in seconds (5 minutes)

    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()

        # In-memory cache backed by DB
        self._corrections: List[Correction] = []
        self._learned_rules: Dict[str, LearningRule] = {}
        self._vendor_preferences: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._rules_loaded_at: float = 0.0  # monotonic timestamp of last DB load
        self._last_refresh: float = time.time()
        self._extraction_calibration_cache: Dict[tuple, tuple[float, Dict[str, Any]]] = {}
        self._review_snapshot_cache: Dict[tuple, tuple[float, Dict[str, Any]]] = {}

        self._init_tables()
        self._load_rules()
        self._load_vendor_preferences()
    
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
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS agent_review_outcomes (
                        id TEXT PRIMARY KEY,
                        organization_id TEXT NOT NULL,
                        ap_item_id TEXT,
                        field_name TEXT NOT NULL,
                        outcome_type TEXT NOT NULL,
                        outcome_tags_json TEXT,
                        selected_source TEXT,
                        user_id TEXT,
                        vendor_name TEXT,
                        sender TEXT,
                        sender_domain TEXT,
                        subject TEXT,
                        document_type TEXT,
                        layout_key TEXT,
                        confidence_profile_id TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS vendor_layout_review_stats (
                        id TEXT PRIMARY KEY,
                        organization_id TEXT NOT NULL,
                        vendor_name TEXT NOT NULL,
                        sender_domain TEXT,
                        layout_key TEXT NOT NULL,
                        document_type TEXT,
                        field_name TEXT NOT NULL,
                        confidence_profile_id TEXT,
                        review_count INTEGER NOT NULL DEFAULT 0,
                        corrected_count INTEGER NOT NULL DEFAULT 0,
                        confirmed_count INTEGER NOT NULL DEFAULT 0,
                        email_selected_count INTEGER NOT NULL DEFAULT 0,
                        attachment_selected_count INTEGER NOT NULL DEFAULT 0,
                        manual_selected_count INTEGER NOT NULL DEFAULT 0,
                        last_reviewed_at TEXT,
                        last_outcome_type TEXT,
                        last_ap_item_id TEXT,
                        UNIQUE(organization_id, vendor_name, layout_key, field_name)
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
                cur.execute(self.db._prepare_sql(
                    "SELECT * FROM agent_learned_rules WHERE organization_id = ?"),
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

    def _load_vendor_preferences(self):
        """Load vendor preferences from the learned_rules DB (rule_type='vendor_pref')."""
        import json as _json
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(self.db._prepare_sql(
                    "SELECT id, action FROM agent_learned_rules "
                    "WHERE organization_id = ? AND rule_type = 'vendor_pref'"),
                    (self.organization_id,),
                )
                for row in cur.fetchall():
                    r = dict(row)
                    rule_id = r.get("id") or ""
                    # rule_id format: vendor_pref_{vendor_key}
                    vendor_key = rule_id.replace("vendor_pref_", "", 1)
                    if not vendor_key:
                        continue
                    try:
                        prefs = _json.loads(r["action"]) if r.get("action") else {}
                    except Exception:
                        prefs = {}
                    if isinstance(prefs, dict) and prefs:
                        self._vendor_preferences[vendor_key] = prefs
            if self._vendor_preferences:
                logger.info(
                    "Loaded %d vendor preferences for %s",
                    len(self._vendor_preferences),
                    self.organization_id,
                )
        except Exception as e:
            logger.error("Could not load vendor preferences: %s", e)

    def _persist_vendor_preference(self, vendor: str):
        """Persist a single vendor's preferences to the learned_rules DB."""
        import json as _json
        prefs = self._vendor_preferences.get(vendor)
        if not prefs:
            return
        rule_id = f"vendor_pref_{vendor.lower().replace(' ', '_')}"
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(self.db._prepare_sql(
                    """INSERT INTO agent_learned_rules
                    (id, organization_id, rule_type, condition, action,
                     confidence, learned_from, created_at, last_applied, success_rate)
                    VALUES (?, ?, 'vendor_pref', ?, ?, 0.0, 0, ?, NULL, 1.0)
                    ON CONFLICT (id) DO UPDATE SET
                        organization_id = EXCLUDED.organization_id,
                        rule_type = EXCLUDED.rule_type,
                        condition = EXCLUDED.condition,
                        action = EXCLUDED.action,
                        confidence = EXCLUDED.confidence,
                        learned_from = EXCLUDED.learned_from,
                        created_at = EXCLUDED.created_at,
                        last_applied = EXCLUDED.last_applied,
                        success_rate = EXCLUDED.success_rate"""),
                    (
                        rule_id,
                        self.organization_id,
                        _json.dumps({"vendor": vendor}),
                        _json.dumps(prefs),
                        now,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error("Could not persist vendor preference for %s: %s", vendor, e)

    def _persist_correction(self, correction: Correction) -> bool:
        """Write a correction to the DB. Returns False on failure."""
        import json as _json
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(self.db._prepare_sql(
                    """INSERT OR IGNORE INTO agent_corrections
                    (id, organization_id, correction_type, original_value,
                     corrected_value, context, user_id, invoice_id, vendor,
                     feedback, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""),
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
            return True
        except Exception as e:
            logger.error("Could not persist correction: %s", e)
            return False

    def _persist_rule(self, rule: LearningRule) -> bool:
        """Upsert a learned rule to the DB. Returns False on failure."""
        import json as _json
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(self.db._prepare_sql(
                    """INSERT INTO agent_learned_rules
                    (id, organization_id, rule_type, condition, action,
                     confidence, learned_from, created_at, last_applied, success_rate)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        organization_id = EXCLUDED.organization_id,
                        rule_type = EXCLUDED.rule_type,
                        condition = EXCLUDED.condition,
                        action = EXCLUDED.action,
                        confidence = EXCLUDED.confidence,
                        learned_from = EXCLUDED.learned_from,
                        created_at = EXCLUDED.created_at,
                        last_applied = EXCLUDED.last_applied,
                        success_rate = EXCLUDED.success_rate"""),
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
            return True
        except Exception as e:
            logger.error("Could not persist rule: %s", e)
            return False

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
            "payment_confirmation": "payment",
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
            "event_id": f"cevt_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
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

    def _persist_normalized_correction_event(self, normalized: Dict[str, Any]):
        """Persist a normalized correction event. Returns event_id on success, False on failure."""
        event_id = str(normalized.get("event_id") or f"cevt_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}")
        _COLS = ("id, organization_id, ap_item_id, invoice_id, field_name, correction_type, "
                 "original_value, corrected_value, selected_source, source_channel, event_source, "
                 "user_id, vendor_name, sender, sender_domain, subject, document_type, layout_key, "
                 "attachment_names_json, expected_fields_json, input_payload_json, created_at")
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                sql = self.db._prepare_sql(
                    f"INSERT INTO agent_correction_events ({_COLS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    + ", ".join(
                        f"{c} = EXCLUDED.{c}"
                        for c in _COLS.replace(" ", "").split(",")
                        if c != "id"
                    )
                )
                cur.execute(
                    sql,
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
            return event_id
        except Exception as exc:
            logger.error("Could not persist normalized correction event: %s", exc)
            return False

    def _update_vendor_layout_error_stats(self, normalized: Dict[str, Any]) -> Optional[str]:
        vendor_name = self._normalize_vendor_name(normalized.get("vendor_name"))
        layout_key = str(normalized.get("layout_key") or "").strip()
        field_name = self._normalize_field_name(normalized.get("field_name"))
        if not vendor_name or not layout_key or not field_name:
            return None
        stat_id = f"vles_{self.organization_id}_{vendor_name}_{layout_key}_{field_name}"
        stat_id = re.sub(r"[^a-zA-Z0-9_:-]+", "_", stat_id)[:180]
        now = str(normalized.get("created_at") or datetime.now(timezone.utc).isoformat())
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(self.db._prepare_sql(
                    """
                    INSERT INTO vendor_layout_error_stats
                    (id, organization_id, vendor_name, sender_domain, layout_key, document_type,
                     field_name, correction_count, first_corrected_at, last_corrected_at, last_ap_item_id,
                     last_original_value, last_corrected_value)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(organization_id, vendor_name, layout_key, field_name)
                    DO UPDATE SET
                        correction_count = vendor_layout_error_stats.correction_count + 1,
                        last_corrected_at = excluded.last_corrected_at,
                        last_ap_item_id = excluded.last_ap_item_id,
                        last_original_value = excluded.last_original_value,
                        last_corrected_value = excluded.last_corrected_value,
                        sender_domain = excluded.sender_domain,
                        document_type = excluded.document_type
                    """),
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
        now = str(normalized.get("created_at") or datetime.now(timezone.utc).isoformat())
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(self.db._prepare_sql(
                    """
                    SELECT correction_fields_json FROM reviewed_extraction_cases
                    WHERE organization_id = ? AND ap_item_id = ?
                    LIMIT 1
                    """),
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
                cur.execute(self.db._prepare_sql(
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
                    """),
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
                cur.execute(self.db._prepare_sql(
                    """
                    SELECT *
                    FROM reviewed_extraction_cases
                    WHERE organization_id = ?
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ?
                    """),
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
                cur.execute(self.db._prepare_sql(
                    """
                    SELECT *
                    FROM vendor_layout_error_stats
                    WHERE organization_id = ?
                    ORDER BY correction_count DESC, last_corrected_at DESC
                    LIMIT ?
                    """),
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

    def _persist_review_outcome_event(self, normalized: Dict[str, Any]) -> str:
        event_id = str(normalized.get("event_id") or f"review_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}")
        _COLS = ("id, organization_id, ap_item_id, field_name, outcome_type, outcome_tags_json, "
                 "selected_source, user_id, vendor_name, sender, sender_domain, subject, "
                 "document_type, layout_key, confidence_profile_id, created_at")
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                sql = self.db._prepare_sql(
                    f"INSERT INTO agent_review_outcomes ({_COLS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    + ", ".join(
                        f"{c} = EXCLUDED.{c}"
                        for c in _COLS.replace(" ", "").split(",")
                        if c != "id"
                    )
                )
                cur.execute(
                    sql,
                    (
                        event_id,
                        self.organization_id,
                        normalized.get("ap_item_id"),
                        normalized.get("field_name"),
                        normalized.get("outcome_type"),
                        json.dumps(normalized.get("outcome_tags") or []),
                        normalized.get("selected_source"),
                        normalized.get("user_id"),
                        normalized.get("vendor_name"),
                        normalized.get("sender"),
                        normalized.get("sender_domain"),
                        normalized.get("subject"),
                        normalized.get("document_type"),
                        normalized.get("layout_key"),
                        normalized.get("confidence_profile_id"),
                        normalized.get("created_at"),
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.error("Could not persist review outcome event: %s", exc)
        return event_id

    def _update_vendor_layout_review_stats(self, normalized: Dict[str, Any]) -> Optional[str]:
        vendor_name = self._normalize_vendor_name(normalized.get("vendor_name"))
        layout_key = str(normalized.get("layout_key") or "").strip()
        field_name = self._normalize_field_name(normalized.get("field_name"))
        if not vendor_name or not layout_key or not field_name:
            return None

        stat_id = f"vlrs_{self.organization_id}_{vendor_name}_{layout_key}_{field_name}"
        stat_id = re.sub(r"[^a-zA-Z0-9_:-]+", "_", stat_id)[:180]
        selected_source = str(normalized.get("selected_source") or "").strip().lower()
        outcome_type = str(normalized.get("outcome_type") or "").strip().lower()
        corrected_increment = 1 if outcome_type == "corrected" else 0
        confirmed_increment = 1 if outcome_type == "confirmed_correct" else 0
        email_increment = 1 if selected_source == "email" else 0
        attachment_increment = 1 if selected_source == "attachment" else 0
        manual_increment = 1 if selected_source == "manual" else 0
        now = str(normalized.get("created_at") or datetime.now(timezone.utc).isoformat())

        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(self.db._prepare_sql(
                    """
                    INSERT INTO vendor_layout_review_stats
                    (id, organization_id, vendor_name, sender_domain, layout_key, document_type, field_name,
                     confidence_profile_id, review_count, corrected_count, confirmed_count,
                     email_selected_count, attachment_selected_count, manual_selected_count,
                     last_reviewed_at, last_outcome_type, last_ap_item_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(organization_id, vendor_name, layout_key, field_name)
                    DO UPDATE SET
                        sender_domain = excluded.sender_domain,
                        document_type = excluded.document_type,
                        confidence_profile_id = excluded.confidence_profile_id,
                        review_count = vendor_layout_review_stats.review_count + 1,
                        corrected_count = vendor_layout_review_stats.corrected_count + excluded.corrected_count,
                        confirmed_count = vendor_layout_review_stats.confirmed_count + excluded.confirmed_count,
                        email_selected_count = vendor_layout_review_stats.email_selected_count + excluded.email_selected_count,
                        attachment_selected_count = vendor_layout_review_stats.attachment_selected_count + excluded.attachment_selected_count,
                        manual_selected_count = vendor_layout_review_stats.manual_selected_count + excluded.manual_selected_count,
                        last_reviewed_at = excluded.last_reviewed_at,
                        last_outcome_type = excluded.last_outcome_type,
                        last_ap_item_id = excluded.last_ap_item_id
                    """),
                    (
                        stat_id,
                        self.organization_id,
                        vendor_name,
                        normalized.get("sender_domain"),
                        layout_key,
                        normalized.get("document_type"),
                        field_name,
                        normalized.get("confidence_profile_id"),
                        corrected_increment,
                        confirmed_increment,
                        email_increment,
                        attachment_increment,
                        manual_increment,
                        now,
                        outcome_type or None,
                        normalized.get("ap_item_id"),
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.error("Could not update vendor/layout review stats: %s", exc)
            return None

        self._review_snapshot_cache.clear()
        self._extraction_calibration_cache.clear()
        return stat_id

    def record_review_outcome(
        self,
        *,
        field_name: str,
        outcome_type: str,
        context: Dict[str, Any],
        user_id: str,
        selected_source: Optional[str] = None,
        outcome_tags: Optional[List[str]] = None,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_context = context if isinstance(context, dict) else {}
        normalized_field = self._normalize_field_name(field_name)
        normalized_outcome = str(outcome_type or "").strip().lower()
        if normalized_outcome not in {"confirmed_correct", "corrected"}:
            raise ValueError("unsupported_review_outcome_type")

        sender = str(normalized_context.get("sender") or normalized_context.get("sender_email") or "").strip()
        normalized = {
            "event_id": f"review_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            "ap_item_id": str(normalized_context.get("ap_item_id") or "").strip() or None,
            "field_name": normalized_field,
            "outcome_type": normalized_outcome,
            "outcome_tags": sorted(
                {
                    normalized_outcome,
                    *(str(value or "").strip().lower() for value in (outcome_tags or []) if str(value or "").strip()),
                }
            ),
            "selected_source": str(selected_source or normalized_context.get("selected_source") or "").strip().lower() or None,
            "user_id": str(user_id or "").strip() or None,
            "vendor_name": self._normalize_vendor_name(normalized_context.get("vendor") or normalized_context.get("vendor_name")) or None,
            "sender": sender or None,
            "sender_domain": self._sender_domain(normalized_context.get("sender_domain") or sender) or None,
            "subject": str(normalized_context.get("subject") or "").strip() or None,
            "document_type": self._normalize_document_type(
                normalized_context.get("document_type") or normalized_context.get("email_type")
            ) or None,
            "layout_key": str(normalized_context.get("layout_key") or self._derive_layout_key(normalized_context)).strip() or None,
            "confidence_profile_id": str(normalized_context.get("confidence_profile_id") or "").strip() or None,
            "created_at": str(created_at or datetime.now(timezone.utc).isoformat()),
        }
        event_id = self._persist_review_outcome_event(normalized)
        stat_id = self._update_vendor_layout_review_stats(normalized)
        return {
            "review_outcome_event_id": event_id,
            "review_stat_id": stat_id,
        }

    def get_extraction_review_calibration_snapshot(
        self,
        *,
        vendor_name: Any,
        sender_domain: Any = None,
        document_type: Any = None,
        confidence_profile_id: Any = None,
    ) -> Dict[str, Any]:
        normalized_vendor = self._normalize_vendor_name(vendor_name)
        normalized_sender_domain = self._sender_domain(sender_domain)
        normalized_document_type = self._normalize_document_type(document_type)
        normalized_profile = str(confidence_profile_id or "").strip() or None
        if not normalized_vendor:
            return {"status": "no_vendor", "summary": {"total_reviews": 0}, "fields": {}}

        cache_key = (
            normalized_vendor,
            normalized_sender_domain,
            normalized_document_type,
            normalized_profile,
        )
        cached = self._review_snapshot_cache.get(cache_key)
        now_mono = time.monotonic()
        if cached and (now_mono - cached[0]) <= self._RULES_TTL_SECONDS:
            return dict(cached[1])

        params: List[Any] = [self.organization_id, normalized_vendor]
        sql = (
            "SELECT field_name, SUM(review_count) AS review_count, SUM(corrected_count) AS corrected_count, "
            "SUM(confirmed_count) AS confirmed_count, SUM(email_selected_count) AS email_selected_count, "
            "SUM(attachment_selected_count) AS attachment_selected_count, SUM(manual_selected_count) AS manual_selected_count "
            "FROM vendor_layout_review_stats WHERE organization_id = %s AND vendor_name = %s "
        )
        if normalized_document_type:
            sql += "AND document_type = %s "
            params.append(normalized_document_type)
        if normalized_sender_domain:
            sql += "AND (sender_domain = %s OR sender_domain = '' OR sender_domain IS NULL) "
            params.append(normalized_sender_domain)
        if normalized_profile:
            sql += "AND (confidence_profile_id = %s OR confidence_profile_id IS NULL) "
            params.append(normalized_profile)
        sql += "GROUP BY field_name"

        rows: List[Dict[str, Any]] = []
        try:
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(self.db._prepare_sql(sql), tuple(params))
                rows = [dict(row) for row in cur.fetchall()]
        except Exception as exc:
            logger.error("Could not load review calibration snapshot: %s", exc)
            snapshot = {"status": "error", "summary": {"total_reviews": 0}, "fields": {}}
            self._review_snapshot_cache[cache_key] = (now_mono, snapshot)
            return dict(snapshot)

        field_stats: Dict[str, Any] = {}
        total_reviews = 0
        for row in rows:
            field_name = self._normalize_field_name(row.get("field_name"))
            review_count = int(row.get("review_count") or 0)
            corrected_count = int(row.get("corrected_count") or 0)
            confirmed_count = int(row.get("confirmed_count") or 0)
            email_count = int(row.get("email_selected_count") or 0)
            attachment_count = int(row.get("attachment_selected_count") or 0)
            manual_count = int(row.get("manual_selected_count") or 0)
            total_reviews += review_count
            field_stats[field_name] = {
                "review_count": review_count,
                "corrected_count": corrected_count,
                "confirmed_count": confirmed_count,
                "correction_rate": round(corrected_count / review_count, 4) if review_count else 0.0,
                "confirmation_rate": round(confirmed_count / review_count, 4) if review_count else 0.0,
                "source_win_rates": {
                    "email": round(email_count / review_count, 4) if review_count else 0.0,
                    "attachment": round(attachment_count / review_count, 4) if review_count else 0.0,
                    "manual": round(manual_count / review_count, 4) if review_count else 0.0,
                },
            }

        snapshot = {
            "status": "available" if field_stats else "no_signal",
            "summary": {"total_reviews": total_reviews, "field_count": len(field_stats)},
            "fields": field_stats,
        }
        self._review_snapshot_cache[cache_key] = (now_mono, snapshot)
        return dict(snapshot)

    def get_recent_corrections(self, vendor_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent field corrections for a vendor to feed into extraction prompt."""
        try:
            normalized = str(vendor_name or "").strip().lower()
            if not normalized:
                return []
            results = []
            for rule in self._rules:
                if rule.rule_type == "vendor_alias" and str(rule.condition.get("raw_vendor", "")).strip().lower() == normalized:
                    results.append({
                        "field": "vendor",
                        "original": rule.condition.get("raw_vendor", ""),
                        "corrected": rule.action.get("normalized_vendor", ""),
                    })
                elif rule.rule_type == "field_correction":
                    rule_vendor = str(rule.condition.get("vendor_name", "")).strip().lower()
                    if rule_vendor == normalized:
                        results.append({
                            "field": rule.condition.get("field", ""),
                            "original": rule.condition.get("original_value", ""),
                            "corrected": rule.action.get("corrected_value", ""),
                        })
            return results[:limit]
        except Exception:
            return []

    def get_extraction_confidence_adjustments(
        self,
        *,
        vendor_name: Any,
        sender_domain: Any = None,
        document_type: Any = None,
        min_corrections: int = 3,
    ) -> Dict[str, Any]:
        """Return tighten-only threshold overrides from reviewed correction history.

        This uses production correction history conservatively: repeated operator
        corrections for a field only ever *increase* the required confidence for
        that field. It does not relax thresholds, because correction history does
        not tell us when a reviewed field was actually a false positive.
        """
        normalized_vendor = self._normalize_vendor_name(vendor_name)
        normalized_sender_domain = self._sender_domain(sender_domain)
        normalized_document_type = self._normalize_document_type(document_type)
        if not normalized_vendor:
            return {"profile_id": None, "threshold_overrides": {}, "signal_count": 0}

        cache_key = (
            normalized_vendor,
            normalized_sender_domain,
            normalized_document_type,
            int(max(1, min_corrections)),
        )
        cached = self._extraction_calibration_cache.get(cache_key)
        now_mono = time.monotonic()
        if cached and (now_mono - cached[0]) <= self._RULES_TTL_SECONDS:
            return dict(cached[1])

        review_snapshot = self.get_extraction_review_calibration_snapshot(
            vendor_name=normalized_vendor,
            sender_domain=normalized_sender_domain,
            document_type=normalized_document_type,
        )
        threshold_overrides: Dict[str, float] = {}
        signal_count = 0
        for field_name, stats in (review_snapshot.get("fields") or {}).items():
            review_count = int((stats or {}).get("review_count") or 0)
            corrected_count = int((stats or {}).get("corrected_count") or 0)
            correction_rate = float((stats or {}).get("correction_rate") or 0.0)
            if corrected_count < int(max(1, min_corrections)):
                continue
            signal_count += corrected_count
            if corrected_count >= 8 and correction_rate >= 0.85:
                threshold_overrides[field_name] = 0.98
            elif corrected_count >= 5 and correction_rate >= 0.75:
                threshold_overrides[field_name] = 0.97
            elif review_count >= 5 and correction_rate >= 0.6:
                threshold_overrides[field_name] = 0.96

        if not threshold_overrides:
            params: List[Any] = [self.organization_id, normalized_vendor]
            sql = (
                "SELECT field_name, SUM(correction_count) AS correction_count "
                "FROM vendor_layout_error_stats "
                "WHERE organization_id = %s AND vendor_name = %s "
            )
            if normalized_document_type:
                sql += "AND document_type = %s "
                params.append(normalized_document_type)
            if normalized_sender_domain:
                sql += "AND (sender_domain = %s OR sender_domain = '' OR sender_domain IS NULL) "
                params.append(normalized_sender_domain)
            sql += "GROUP BY field_name"

            try:
                with self.db.connect() as conn:
                    cur = conn.cursor()
                    cur.execute(self.db._prepare_sql(sql), tuple(params))
                    rows = [dict(row) for row in cur.fetchall()]
            except Exception as exc:
                logger.error("Could not load extraction calibration stats: %s", exc)
                rows = []

            for row in rows:
                field_name = self._normalize_field_name(row.get("field_name"))
                try:
                    correction_count = int(row.get("correction_count") or 0)
                except (TypeError, ValueError):
                    correction_count = 0
                if correction_count < int(max(1, min_corrections)):
                    continue
                signal_count += correction_count
                if correction_count >= 8:
                    threshold_overrides[field_name] = 0.98
                elif correction_count >= 5:
                    threshold_overrides[field_name] = 0.97
                else:
                    threshold_overrides[field_name] = 0.96

        result = {
            "profile_id": "learned_review_history_tightening" if threshold_overrides else None,
            "threshold_overrides": threshold_overrides,
            "signal_count": signal_count,
            "review_snapshot": review_snapshot,
        }
        self._extraction_calibration_cache[cache_key] = (now_mono, result)
        return dict(result)

    def export_reviewed_extraction_cases(self, output_path: Path | str) -> Dict[str, Any]:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        cases = self.list_reviewed_extraction_cases()
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
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
            correction_id=f"corr_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            correction_type=correction_type,
            original_value=original_value,
            corrected_value=corrected_value,
            context=context,
            user_id=user_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
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
        """Learn GL code preferences from correction.

        Requires at least 2 corrections before creating a rule to avoid
        one wrong GL correction producing a high-confidence auto-apply rule.
        Confidence ramps from 0.4 (at 2 corrections) toward 1.0 (at 5+).
        """
        vendor = correction.vendor
        if not vendor:
            return {"rules_created": 0}

        # Create or update vendor GL preference
        rule_id = f"gl_{vendor.lower().replace(' ', '_')}"

        if rule_id in self._learned_rules:
            rule = self._learned_rules[rule_id]
            rule.learned_from += 1
            rule.action = {"gl_code": correction.corrected_value}
            # Ramp confidence: cap at 0.7 initially, approach 0.99 at 5+ corrections
            rule.confidence = min(0.99, rule.learned_from / 5.0)
            self._persist_rule(rule)
            return {"rules_updated": 1}
        else:
            # Track the correction count but don't create a rule until 2+ corrections
            prefs = self._vendor_preferences.get(vendor) or {}
            gl_correction_count = int(prefs.get("gl_correction_count") or 0) + 1
            if vendor not in self._vendor_preferences:
                self._vendor_preferences[vendor] = {}
            self._vendor_preferences[vendor]["gl_correction_count"] = gl_correction_count
            self._vendor_preferences[vendor]["gl_last_corrected_value"] = correction.corrected_value
            self._persist_vendor_preference(vendor)

            if gl_correction_count < 2:
                logger.info(
                    "GL correction recorded but rule not created yet (need 2+, have %d) for vendor %s",
                    gl_correction_count, vendor,
                )
                return {"rules_created": 0}

            rule = LearningRule(
                rule_id=rule_id,
                rule_type="gl_code",
                condition={"vendor": vendor},
                action={"gl_code": correction.corrected_value},
                confidence=min(0.7, gl_correction_count / 5.0),
                learned_from=gl_correction_count,
                created_at=datetime.now(timezone.utc).isoformat(),
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
            created_at=datetime.now(timezone.utc).isoformat(),
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
        self._persist_vendor_preference(vendor)

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
            created_at=datetime.now(timezone.utc).isoformat(),
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
            self._persist_vendor_preference(vendor)

            return {"preferences_updated": ["approval_threshold"]}

        elif original_decision == "auto_approved" and user_decision == "rejected":
            # User is more strict - raise the threshold
            if vendor not in self._vendor_preferences:
                self._vendor_preferences[vendor] = {}

            self._vendor_preferences[vendor]["approval_bias"] = "strict"
            self._vendor_preferences[vendor]["auto_approve_threshold_adj"] = 0.1
            self._persist_vendor_preference(vendor)

            return {"preferences_updated": ["approval_threshold"]}
        
        return {"rules_created": 0}
    
    def _refresh_if_stale(self):
        """Reload rules and vendor preferences from DB if the refresh interval has elapsed."""
        now = time.time()
        if now - self._last_refresh > self._REFRESH_INTERVAL:
            self._load_rules()
            self._load_vendor_preferences()
            self._last_refresh = now

    def suggest(
        self,
        suggestion_type: str,
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Get a suggestion based on learned rules.

        Refreshes the in-memory caches (rules + vendor preferences) from DB
        if older than _REFRESH_INTERVAL (default 5 min), so corrections
        written by one process are visible to others without a restart.

        Returns None if no learned rule applies.
        """
        self._refresh_if_stale()

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

            # Apply time-based decay to confidence
            effective_confidence = rule.confidence
            try:
                created = datetime.fromisoformat(rule.created_at)
                age_days = (datetime.now(timezone.utc) - created).days
                decay = max(0.3, 1.0 - (age_days / 365))
                effective_confidence = rule.confidence * decay
            except (ValueError, TypeError):
                pass  # If created_at is unparseable, skip decay

            # Update last applied
            rule.last_applied = datetime.now(timezone.utc).isoformat()

            return {
                "value": rule.action.get("gl_code"),
                "confidence": effective_confidence,
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
    
    # §7.9: 50-signal minimum per category before a pattern is actionable
    MIN_SIGNALS_FOR_SYSTEMIC_PATTERN = 50

    def get_learning_stats(self) -> Dict[str, Any]:
        """Get statistics about what the agent has learned."""
        corrections_by_category = self._count_corrections_by_category()
        actionable_categories = {
            cat: count for cat, count in corrections_by_category.items()
            if count >= self.MIN_SIGNALS_FOR_SYSTEMIC_PATTERN
        }
        return {
            "total_corrections": len(self._corrections),
            "learned_rules": len(self._learned_rules),
            "vendor_preferences": len(self._vendor_preferences),
            "rules_by_type": self._count_rules_by_type(),
            "corrections_by_category": corrections_by_category,
            "actionable_categories": actionable_categories,
            "min_signals_for_pattern": self.MIN_SIGNALS_FOR_SYSTEMIC_PATTERN,
            "recent_corrections": len([
                c for c in self._corrections
                if (datetime.now(timezone.utc) - datetime.fromisoformat(c.timestamp)).days <= 7
            ]),
        }

    def _count_corrections_by_category(self) -> Dict[str, int]:
        """§7.9: Count corrections by category (field + document type)."""
        counts: Dict[str, int] = defaultdict(int)
        for c in self._corrections:
            category = f"{c.field_name}:{c.document_type or 'unknown'}"
            counts[category] += 1
        return dict(counts)

    def get_closed_loop_validation(self, improvement_date: str, window_days: int = 28) -> Dict[str, Any]:
        """§7.9 Closed-loop validation: track override rate change post-improvement.

        "After each model improvement, the Backoffice tracks whether the
        override rate for the targeted category decreases in the four weeks
        following deployment."
        """
        try:
            improvement_dt = datetime.fromisoformat(improvement_date.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return {"error": "invalid_improvement_date"}

        pre_window = [
            c for c in self._corrections
            if _parse_ts(c.timestamp) and improvement_dt - timedelta(days=window_days) <= _parse_ts(c.timestamp) < improvement_dt
        ]
        post_window = [
            c for c in self._corrections
            if _parse_ts(c.timestamp) and improvement_dt <= _parse_ts(c.timestamp) <= improvement_dt + timedelta(days=window_days)
        ]

        pre_count = len(pre_window)
        post_count = len(post_window)
        improvement_pct = ((pre_count - post_count) / pre_count * 100) if pre_count > 0 else 0.0

        return {
            "improvement_date": improvement_date,
            "window_days": window_days,
            "pre_improvement_corrections": pre_count,
            "post_improvement_corrections": post_count,
            "improvement_pct": round(improvement_pct, 1),
            "improved": post_count < pre_count,
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


def get_correction_learning_service(organization_id: str = "default") -> CorrectionLearningService:
    """Get a cached correction learning service instance."""
    normalized_org = str(organization_id or "default").strip() or "default"
    service = _correction_learning_services.get(normalized_org)
    current_db = get_db()
    if (
        service is None
        or service.__class__ is not CorrectionLearningService
        or getattr(service, "db", None) is not current_db
    ):
        service = CorrectionLearningService(organization_id=normalized_org)
        _correction_learning_services[normalized_org] = service
    return service
