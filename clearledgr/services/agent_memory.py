"""Canonical agent memory for bounded AP autonomy.

This service gives the AP agent one persistent identity plus a unified place
to store cross-surface memory:
- agent profile / doctrine
- memory events
- current belief state per AP item
- episode summaries
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from clearledgr.core.database import ClearledgrDB, get_db

logger = logging.getLogger(__name__)

_agent_memory_services: Dict[Tuple[str, int], "AgentMemoryService"] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, default=str)


class AgentMemoryService:
    """Canonical memory/persistence layer for the AP runtime."""

    _DEFAULT_PROFILE: Dict[str, Any] = {
        "name": "Clearledgr AP Agent",
        "mission": "Own the AP lane from intake through approval routing and ERP completion.",
        "doctrine_version": "ap_v1",
        "risk_posture": "bounded_autonomy",
        "autonomy_level": "assisted",
        "explanation_style": "operator_brief",
        "goals": [
            "Keep the AP queue moving",
            "Escalate uncertainty instead of guessing",
            "Preserve auditability across every action",
        ],
        "forbidden_actions": [
            "release_funds",
            "bypass_approval_policy",
            "cross_tenant_boundaries",
        ],
        "promotion_gate_status": {
            "status": "bounded_autonomy_only",
            "required_gates": [
                "operator_acceptance",
                "legal_transition_correctness",
                "post_action_verification",
            ],
        },
    }

    def __init__(
        self,
        organization_id: str = "default",
        *,
        db: Optional[ClearledgrDB] = None,
    ) -> None:
        self.organization_id = str(organization_id or "default").strip() or "default"
        self.db = db or get_db()
        self.enabled = all(
            hasattr(self.db, attr)
            for attr in ("connect", "_prepare_sql")
        )
        if self.enabled:
            self._init_tables()

    @staticmethod
    def _row_to_dict(row: Any) -> Dict[str, Any]:
        if isinstance(row, dict):
            return dict(row)
        try:
            return dict(row)
        except Exception:
            return {}

    @staticmethod
    def _load_json(raw: Any, default: Any) -> Any:
        if raw is None:
            return default
        if isinstance(raw, (dict, list)):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                value = json.loads(raw)
                return value if isinstance(value, type(default)) else default
            except Exception:
                return default
        return default

    @staticmethod
    def _parse_item_metadata(ap_item: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        item = ap_item if isinstance(ap_item, dict) else {}
        raw = item.get("metadata")
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                value = json.loads(raw)
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}
        return {}

    @classmethod
    def _parse_task_payload(cls, task_run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        row = task_run if isinstance(task_run, dict) else {}
        return cls._load_json(row.get("input_payload"), {})

    @classmethod
    def _task_run_ap_item_id(cls, task_run: Optional[Dict[str, Any]]) -> Optional[str]:
        payload = cls._parse_task_payload(task_run)
        candidates = [
            payload.get("ap_item_id"),
            payload.get("entity_id"),
            (payload.get("ap_item") or {}).get("id") if isinstance(payload.get("ap_item"), dict) else None,
            (payload.get("invoice") or {}).get("ap_item_id") if isinstance(payload.get("invoice"), dict) else None,
        ]
        for candidate in candidates:
            resolved = str(candidate or "").strip()
            if resolved:
                return resolved
        return None

    def _init_tables(self) -> None:
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_profiles (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(organization_id, skill_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_memory_events (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    ap_item_id TEXT,
                    thread_id TEXT,
                    event_type TEXT NOT NULL,
                    channel TEXT,
                    actor_id TEXT,
                    correlation_id TEXT,
                    source TEXT,
                    summary TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_belief_states (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    ap_item_id TEXT NOT NULL,
                    thread_id TEXT,
                    current_state TEXT,
                    status TEXT,
                    belief_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    uncertainties_json TEXT NOT NULL,
                    next_action_json TEXT NOT NULL,
                    memory_summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(organization_id, skill_id, ap_item_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_episode_summaries (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    ap_item_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT,
                    outcome_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(organization_id, skill_id, ap_item_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_eval_snapshots (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    scope_id TEXT,
                    snapshot_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_patterns (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    pattern_key TEXT NOT NULL,
                    pattern_json TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    last_seen_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(organization_id, skill_id, pattern_type, pattern_key)
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_profiles_org_skill "
                "ON agent_profiles(organization_id, skill_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_memory_events_org_item_created "
                "ON agent_memory_events(organization_id, ap_item_id, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_memory_events_org_event "
                "ON agent_memory_events(organization_id, event_type, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_belief_states_org_item "
                "ON agent_belief_states(organization_id, ap_item_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_episode_summaries_org_item "
                "ON agent_episode_summaries(organization_id, ap_item_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_eval_snapshots_scope "
                "ON agent_eval_snapshots(organization_id, skill_id, scope, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_patterns_org_type "
                "ON agent_patterns(organization_id, skill_id, pattern_type, updated_at)"
            )
            conn.commit()

    def ensure_profile(
        self,
        *,
        skill_id: str = "ap_v1",
        profile_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {
                **self._DEFAULT_PROFILE,
                **dict(profile_overrides or {}),
                "organization_id": self.organization_id,
                "skill_id": str(skill_id or "ap_v1").strip() or "ap_v1",
            }

        resolved_skill_id = str(skill_id or "ap_v1").strip() or "ap_v1"
        existing_profile = self.get_profile(skill_id=resolved_skill_id)
        profile = {
            **self._DEFAULT_PROFILE,
            **dict(existing_profile or {}),
            **dict(profile_overrides or {}),
            "organization_id": self.organization_id,
            "skill_id": resolved_skill_id,
        }
        now = _now_iso()
        sql = (
            """
            INSERT INTO agent_profiles (
                id, organization_id, skill_id, profile_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, skill_id)
            DO UPDATE SET
                profile_json = EXCLUDED.profile_json,
                updated_at = EXCLUDED.updated_at
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    f"profile_{uuid.uuid4().hex}",
                    self.organization_id,
                    resolved_skill_id,
                    _json_dumps(profile),
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_profile(skill_id=resolved_skill_id) or profile

    def get_profile(self, *, skill_id: str = "ap_v1") -> Dict[str, Any]:
        if not self.enabled:
            return {}
        resolved_skill_id = str(skill_id or "ap_v1").strip() or "ap_v1"
        sql = (
            "SELECT profile_json FROM agent_profiles WHERE organization_id = %s AND skill_id = %s"
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (self.organization_id, resolved_skill_id))
            row = cur.fetchone()
        if not row:
            return {}
        raw = self._row_to_dict(row).get("profile_json")
        return self._load_json(raw, {})

    def default_profile(self, *, skill_id: str = "ap_v1") -> Dict[str, Any]:
        resolved_skill_id = str(skill_id or "ap_v1").strip() or "ap_v1"
        return {
            **self._DEFAULT_PROFILE,
            "organization_id": self.organization_id,
            "skill_id": resolved_skill_id,
        }

    def observe_event(
        self,
        *,
        skill_id: str = "ap_v1",
        ap_item_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        channel: Optional[str] = None,
        actor_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        source: str = "finance_agent_runtime",
        summary: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None

        resolved_skill_id = str(skill_id or "ap_v1").strip() or "ap_v1"
        self.ensure_profile(skill_id=resolved_skill_id)
        row = {
            "id": f"mem_{uuid.uuid4().hex}",
            "organization_id": self.organization_id,
            "skill_id": resolved_skill_id,
            "ap_item_id": str(ap_item_id or "").strip() or None,
            "thread_id": str(thread_id or "").strip() or None,
            "event_type": str(event_type or "").strip() or "unknown",
            "channel": str(channel or "").strip() or None,
            "actor_id": str(actor_id or "").strip() or None,
            "correlation_id": str(correlation_id or "").strip() or None,
            "source": str(source or "").strip() or "finance_agent_runtime",
            "summary": str(summary or "").strip() or None,
            "payload_json": dict(payload or {}),
            "created_at": _now_iso(),
        }
        sql = (
            """
            INSERT INTO agent_memory_events (
                id, organization_id, skill_id, ap_item_id, thread_id, event_type, channel,
                actor_id, correlation_id, source, summary, payload_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    row["id"],
                    row["organization_id"],
                    row["skill_id"],
                    row["ap_item_id"],
                    row["thread_id"],
                    row["event_type"],
                    row["channel"],
                    row["actor_id"],
                    row["correlation_id"],
                    row["source"],
                    row["summary"],
                    _json_dumps(row["payload_json"]),
                    row["created_at"],
                ),
            )
            conn.commit()
        return row

    def observe(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Compatibility alias for the canonical memory observe API."""
        return self.observe_event(**kwargs)

    def record_eval_snapshot(
        self,
        *,
        skill_id: str = "ap_v1",
        scope: str,
        snapshot_type: str,
        payload: Optional[Dict[str, Any]] = None,
        scope_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        row = {
            "id": f"eval_{uuid.uuid4().hex}",
            "organization_id": self.organization_id,
            "skill_id": str(skill_id or "ap_v1").strip() or "ap_v1",
            "scope": str(scope or "organization").strip() or "organization",
            "scope_id": str(scope_id or "").strip() or None,
            "snapshot_type": str(snapshot_type or "quality_snapshot").strip() or "quality_snapshot",
            "payload_json": dict(payload or {}),
            "created_at": _now_iso(),
        }
        sql = (
            """
            INSERT INTO agent_eval_snapshots (
                id, organization_id, skill_id, scope, scope_id, snapshot_type, payload_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    row["id"],
                    row["organization_id"],
                    row["skill_id"],
                    row["scope"],
                    row["scope_id"],
                    row["snapshot_type"],
                    _json_dumps(row["payload_json"]),
                    row["created_at"],
                ),
            )
            conn.commit()
        return {
            "scope": row["scope"],
            "scope_id": row["scope_id"],
            "snapshot_type": row["snapshot_type"],
            "payload": row["payload_json"],
            "created_at": row["created_at"],
        }

    def latest_eval_snapshot(
        self,
        *,
        skill_id: str = "ap_v1",
        scope: str,
        scope_id: Optional[str] = None,
        snapshot_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {}
        clauses = ["organization_id = %s", "skill_id = %s", "scope = %s"]
        params: List[Any] = [
            self.organization_id,
            str(skill_id or "ap_v1").strip() or "ap_v1",
            str(scope or "organization").strip() or "organization",
        ]
        if scope_id:
            clauses.append("scope_id = %s")
            params.append(str(scope_id).strip())
        if snapshot_type:
            clauses.append("snapshot_type = %s")
            params.append(str(snapshot_type).strip())
        sql = (
            f"""
            SELECT scope, scope_id, snapshot_type, payload_json, created_at
            FROM agent_eval_snapshots
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        if not row:
            return {}
        payload = self._row_to_dict(row)
        return {
            "scope": payload.get("scope"),
            "scope_id": payload.get("scope_id"),
            "snapshot_type": payload.get("snapshot_type"),
            "payload": self._load_json(payload.get("payload_json"), {}),
            "created_at": payload.get("created_at"),
        }

    def _derive_next_action(
        self,
        *,
        event_type: str,
        response: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        status = str(response.get("status") or metadata.get("processing_status") or "").strip().lower()
        reason = str(response.get("reason") or "").strip().lower()

        if event_type == "field_correction":
            return {
                "type": "reprocess_after_correction",
                "label": "Re-run AP evaluation with corrected fields",
                "owner": "agent",
            }
        if reason == "field_review_required" or response.get("requires_field_review") or metadata.get("requires_field_review"):
            return {
                "type": "human_field_review",
                "label": "Check the invoice details before Clearledgr continues",
                "owner": "operator",
            }
        if status in {"pending_approval", "awaiting_approval"}:
            return {
                "type": "await_approval",
                "label": "Wait for approval decision",
                "owner": "approver",
            }
        if status in {"needs_info"}:
            return {
                "type": "await_vendor_info",
                "label": "Wait for vendor response",
                "owner": "vendor",
            }
        if status in {"error", "failed"} or event_type.endswith("_failed"):
            return {
                "type": "operator_recovery",
                "label": "Investigate failure and resume safely",
                "owner": "operator",
            }
        if status in {"processed", "auto_approved", "posted", "posted_to_erp", "completed", "closed"}:
            return {
                "type": "monitor_completion",
                "label": "Monitor downstream completion and verification",
                "owner": "agent",
            }
        return {
            "type": "manual_review",
            "label": "Review current AP state",
            "owner": "operator",
        }

    def capture_runtime_state(
        self,
        *,
        skill_id: str = "ap_v1",
        ap_item: Optional[Dict[str, Any]] = None,
        ap_item_id: Optional[str] = None,
        event_type: str,
        reason: Optional[str] = None,
        response: Optional[Dict[str, Any]] = None,
        actor_id: Optional[str] = None,
        source: str = "finance_agent_runtime",
        correlation_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None

        item = dict(ap_item or {})
        metadata = self._parse_item_metadata(item)
        payload = dict(response or {})
        resolved_ap_item_id = (
            str(ap_item_id or item.get("id") or payload.get("ap_item_id") or "").strip()
        )
        if not resolved_ap_item_id:
            return None

        resolved_skill_id = str(skill_id or "ap_v1").strip() or "ap_v1"
        profile = self.ensure_profile(skill_id=resolved_skill_id)
        thread_id = str(
            item.get("thread_id")
            or payload.get("email_id")
            or payload.get("thread_id")
            or ""
        ).strip() or None
        status = str(
            payload.get("status")
            or metadata.get("processing_status")
            or item.get("state")
            or ""
        ).strip().lower() or None
        current_state = str(
            payload.get("ap_item_state")
            or item.get("state")
            or metadata.get("processing_status")
            or ""
        ).strip() or None
        autonomy_policy = payload.get("autonomy_policy")
        if not isinstance(autonomy_policy, dict):
            autonomy_policy = metadata.get("autonomy_policy") if isinstance(metadata.get("autonomy_policy"), dict) else {}
        uncertainties = {
            "confidence_blockers": payload.get("confidence_blockers")
            if isinstance(payload.get("confidence_blockers"), list)
            else metadata.get("confidence_blockers")
            if isinstance(metadata.get("confidence_blockers"), list)
            else [],
            "source_conflicts": payload.get("source_conflicts")
            if isinstance(payload.get("source_conflicts"), list)
            else metadata.get("source_conflicts")
            if isinstance(metadata.get("source_conflicts"), list)
            else [],
            "reason_codes": list(autonomy_policy.get("reason_codes") or []),
        }
        belief_state = {
            "document_type": str(
                item.get("document_type")
                or metadata.get("document_type")
                or metadata.get("email_type")
                or "invoice"
            ).strip()
            or "invoice",
            "vendor_name": str(item.get("vendor_name") or item.get("vendor") or "").strip() or None,
            "invoice_number": str(item.get("invoice_number") or "").strip() or None,
            "amount": item.get("amount"),
            "currency": str(item.get("currency") or payload.get("currency") or "USD").strip() or "USD",
            "status": status,
            "current_state": current_state,
            "reason": str(reason or payload.get("reason") or "").strip() or None,
            "requires_field_review": bool(
                payload.get("requires_field_review") or metadata.get("requires_field_review")
            ),
            "autonomy_mode": str(
                autonomy_policy.get("mode") or metadata.get("autonomy_mode") or ""
            ).strip()
            or None,
            "corrected_field": str(payload.get("field") or "").strip() or None,
        }
        evidence = {
            "thread_id": thread_id,
            "message_id": str(item.get("message_id") or "").strip() or None,
            "audit_event_id": str(payload.get("audit_event_id") or "").strip() or None,
            "correlation_id": str(
                correlation_id or payload.get("correlation_id") or metadata.get("correlation_id") or ""
            ).strip()
            or None,
            "field_confidences": metadata.get("field_confidences")
            if isinstance(metadata.get("field_confidences"), dict)
            else {},
            "source_channel": str(metadata.get("intake_source") or source).strip() or source,
        }
        next_action = self._derive_next_action(
            event_type=event_type,
            response=payload,
            metadata=metadata,
        )
        memory_summary = {
            "event_type": str(event_type or "").strip() or "unknown",
            "reason": str(reason or payload.get("reason") or "").strip() or None,
            "actor_id": str(actor_id or "").strip() or None,
            "source": str(source or "").strip() or "finance_agent_runtime",
            "profile": {
                "name": profile.get("name"),
                "mission": profile.get("mission"),
                "autonomy_level": profile.get("autonomy_level"),
            },
        }
        belief_row = self.upsert_belief_state(
            skill_id=resolved_skill_id,
            ap_item_id=resolved_ap_item_id,
            thread_id=thread_id,
            current_state=current_state,
            status=status,
            belief_state=belief_state,
            evidence=evidence,
            uncertainties=uncertainties,
            next_action=next_action,
            memory_summary=memory_summary,
        )
        episode_row = self.upsert_episode_summary(
            skill_id=resolved_skill_id,
            ap_item_id=resolved_ap_item_id,
            status=status or "unknown",
            summary=memory_summary["reason"] or memory_summary["event_type"],
            outcome={
                "event_type": memory_summary["event_type"],
                "reason": memory_summary["reason"],
                "status": status,
                "next_action": next_action,
            },
        )
        vendor_name = str(belief_state.get("vendor_name") or "").strip().lower()
        document_type = str(belief_state.get("document_type") or "invoice").strip().lower() or "invoice"
        next_action_type = str(next_action.get("type") or "manual_review").strip().lower() or "manual_review"
        if vendor_name:
            pattern_key = f"{vendor_name}|{document_type}|{next_action_type}"
            self.record_pattern(
                skill_id=resolved_skill_id,
                pattern_type="vendor_document_next_action",
                pattern_key=pattern_key,
                pattern={
                    "vendor_name": belief_state.get("vendor_name"),
                    "document_type": document_type,
                    "current_state": current_state,
                    "status": status,
                    "next_action": next_action,
                    "reason": belief_state.get("reason"),
                },
                confidence=0.85 if status in {"processed", "auto_approved", "posted", "posted_to_erp", "completed", "closed"} else 0.6,
            )
        return {
            "belief_state": belief_row,
            "episode_summary": episode_row,
        }

    def record_pattern(
        self,
        *,
        skill_id: str = "ap_v1",
        pattern_type: str,
        pattern_key: str,
        pattern: Optional[Dict[str, Any]] = None,
        confidence: float = 0.5,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        resolved_skill_id = str(skill_id or "ap_v1").strip() or "ap_v1"
        resolved_pattern_type = str(pattern_type or "").strip().lower()
        resolved_pattern_key = str(pattern_key or "").strip().lower()
        if not resolved_pattern_type or not resolved_pattern_key:
            return None
        now = _now_iso()
        sql = (
            """
            INSERT INTO agent_patterns (
                id, organization_id, skill_id, pattern_type, pattern_key, pattern_json,
                confidence, usage_count, last_seen_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, skill_id, pattern_type, pattern_key)
            DO UPDATE SET
                pattern_json = EXCLUDED.pattern_json,
                confidence = EXCLUDED.confidence,
                usage_count = agent_patterns.usage_count + 1,
                last_seen_at = EXCLUDED.last_seen_at,
                updated_at = EXCLUDED.updated_at
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    f"pattern_{uuid.uuid4().hex}",
                    self.organization_id,
                    resolved_skill_id,
                    resolved_pattern_type,
                    resolved_pattern_key,
                    _json_dumps(dict(pattern or {})),
                    max(0.0, min(float(confidence), 1.0)),
                    1,
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        matches = self.list_patterns(
            skill_id=resolved_skill_id,
            pattern_type=resolved_pattern_type,
            pattern_key_prefix=resolved_pattern_key,
            limit=1,
        )
        return matches[0] if matches else None

    def upsert_belief_state(
        self,
        *,
        skill_id: str = "ap_v1",
        ap_item_id: str,
        thread_id: Optional[str] = None,
        current_state: Optional[str] = None,
        status: Optional[str] = None,
        belief_state: Optional[Dict[str, Any]] = None,
        evidence: Optional[Dict[str, Any]] = None,
        uncertainties: Optional[Dict[str, Any]] = None,
        next_action: Optional[Dict[str, Any]] = None,
        memory_summary: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        resolved_ap_item_id = str(ap_item_id or "").strip()
        if not resolved_ap_item_id:
            return None
        resolved_skill_id = str(skill_id or "ap_v1").strip() or "ap_v1"
        now = _now_iso()
        sql = (
            """
            INSERT INTO agent_belief_states (
                id, organization_id, skill_id, ap_item_id, thread_id, current_state, status,
                belief_json, evidence_json, uncertainties_json, next_action_json,
                memory_summary_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, skill_id, ap_item_id)
            DO UPDATE SET
                thread_id = EXCLUDED.thread_id,
                current_state = EXCLUDED.current_state,
                status = EXCLUDED.status,
                belief_json = EXCLUDED.belief_json,
                evidence_json = EXCLUDED.evidence_json,
                uncertainties_json = EXCLUDED.uncertainties_json,
                next_action_json = EXCLUDED.next_action_json,
                memory_summary_json = EXCLUDED.memory_summary_json,
                updated_at = EXCLUDED.updated_at
            """
        )
        row = {
            "id": f"belief_{uuid.uuid4().hex}",
            "organization_id": self.organization_id,
            "skill_id": resolved_skill_id,
            "ap_item_id": resolved_ap_item_id,
            "thread_id": str(thread_id or "").strip() or None,
            "current_state": str(current_state or "").strip() or None,
            "status": str(status or "").strip() or None,
            "belief_json": dict(belief_state or {}),
            "evidence_json": dict(evidence or {}),
            "uncertainties_json": dict(uncertainties or {}),
            "next_action_json": dict(next_action or {}),
            "memory_summary_json": dict(memory_summary or {}),
            "created_at": now,
            "updated_at": now,
        }
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    row["id"],
                    row["organization_id"],
                    row["skill_id"],
                    row["ap_item_id"],
                    row["thread_id"],
                    row["current_state"],
                    row["status"],
                    _json_dumps(row["belief_json"]),
                    _json_dumps(row["evidence_json"]),
                    _json_dumps(row["uncertainties_json"]),
                    _json_dumps(row["next_action_json"]),
                    _json_dumps(row["memory_summary_json"]),
                    row["created_at"],
                    row["updated_at"],
                ),
            )
            conn.commit()
        return self.get_belief_state(ap_item_id=resolved_ap_item_id, skill_id=resolved_skill_id)

    def get_belief_state(self, *, ap_item_id: str, skill_id: str = "ap_v1") -> Dict[str, Any]:
        if not self.enabled:
            return {}
        sql = (
            """
            SELECT ap_item_id, thread_id, current_state, status, belief_json, evidence_json,
                   uncertainties_json, next_action_json, memory_summary_json, created_at, updated_at
            FROM agent_belief_states
            WHERE organization_id = %s AND skill_id = %s AND ap_item_id = %s
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    self.organization_id,
                    str(skill_id or "ap_v1").strip() or "ap_v1",
                    str(ap_item_id or "").strip(),
                ),
            )
            row = cur.fetchone()
        if not row:
            return {}
        payload = self._row_to_dict(row)
        return {
            "ap_item_id": payload.get("ap_item_id"),
            "thread_id": payload.get("thread_id"),
            "current_state": payload.get("current_state"),
            "status": payload.get("status"),
            "belief": self._load_json(payload.get("belief_json"), {}),
            "evidence": self._load_json(payload.get("evidence_json"), {}),
            "uncertainties": self._load_json(payload.get("uncertainties_json"), {}),
            "next_action": self._load_json(payload.get("next_action_json"), {}),
            "summary": self._load_json(payload.get("memory_summary_json"), {}),
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
        }

    def upsert_episode_summary(
        self,
        *,
        skill_id: str = "ap_v1",
        ap_item_id: str,
        status: str,
        summary: Optional[str] = None,
        outcome: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        resolved_ap_item_id = str(ap_item_id or "").strip()
        if not resolved_ap_item_id:
            return None
        resolved_skill_id = str(skill_id or "ap_v1").strip() or "ap_v1"
        now = _now_iso()
        sql = (
            """
            INSERT INTO agent_episode_summaries (
                id, organization_id, skill_id, ap_item_id, status, summary, outcome_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, skill_id, ap_item_id)
            DO UPDATE SET
                status = EXCLUDED.status,
                summary = EXCLUDED.summary,
                outcome_json = EXCLUDED.outcome_json,
                updated_at = EXCLUDED.updated_at
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    f"episode_{uuid.uuid4().hex}",
                    self.organization_id,
                    resolved_skill_id,
                    resolved_ap_item_id,
                    str(status or "unknown").strip() or "unknown",
                    str(summary or "").strip() or None,
                    _json_dumps(dict(outcome or {})),
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_episode_summary(ap_item_id=resolved_ap_item_id, skill_id=resolved_skill_id)

    def get_episode_summary(self, *, ap_item_id: str, skill_id: str = "ap_v1") -> Dict[str, Any]:
        if not self.enabled:
            return {}
        sql = (
            """
            SELECT ap_item_id, status, summary, outcome_json, created_at, updated_at
            FROM agent_episode_summaries
            WHERE organization_id = %s AND skill_id = %s AND ap_item_id = %s
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    self.organization_id,
                    str(skill_id or "ap_v1").strip() or "ap_v1",
                    str(ap_item_id or "").strip(),
                ),
            )
            row = cur.fetchone()
        if not row:
            return {}
        payload = self._row_to_dict(row)
        return {
            "ap_item_id": payload.get("ap_item_id"),
            "status": payload.get("status"),
            "summary": payload.get("summary"),
            "outcome": self._load_json(payload.get("outcome_json"), {}),
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
        }

    def list_patterns(
        self,
        *,
        skill_id: str = "ap_v1",
        pattern_type: Optional[str] = None,
        pattern_key_prefix: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        clauses = ["organization_id = %s", "skill_id = %s"]
        params: List[Any] = [self.organization_id, str(skill_id or "ap_v1").strip() or "ap_v1"]
        if pattern_type:
            clauses.append("pattern_type = %s")
            params.append(str(pattern_type).strip().lower())
        if pattern_key_prefix:
            clauses.append("pattern_key LIKE %s")
            params.append(f"{str(pattern_key_prefix).strip().lower()}%")
        sql = (
            f"""
            SELECT pattern_type, pattern_key, pattern_json, confidence, usage_count, last_seen_at, created_at, updated_at
            FROM agent_patterns
            WHERE {' AND '.join(clauses)}
            ORDER BY confidence DESC, usage_count DESC, updated_at DESC
            LIMIT %s
            """
        )
        params.append(max(1, int(limit or 10)))
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall() or []
        patterns: List[Dict[str, Any]] = []
        for row in rows:
            payload = self._row_to_dict(row)
            patterns.append(
                {
                    "pattern_type": payload.get("pattern_type"),
                    "pattern_key": payload.get("pattern_key"),
                    "pattern": self._load_json(payload.get("pattern_json"), {}),
                    "confidence": payload.get("confidence"),
                    "usage_count": payload.get("usage_count"),
                    "last_seen_at": payload.get("last_seen_at"),
                    "created_at": payload.get("created_at"),
                    "updated_at": payload.get("updated_at"),
                }
            )
        return patterns

    def build_belief_state(
        self,
        *,
        ap_item_id: str,
        skill_id: str = "ap_v1",
        ap_item: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved_ap_item_id = str(ap_item_id or "").strip()
        if not resolved_ap_item_id:
            return {}
        belief = self.get_belief_state(ap_item_id=resolved_ap_item_id, skill_id=skill_id)
        if belief:
            return belief

        item = dict(ap_item or {})
        if not item and hasattr(self.db, "get_ap_item"):
            try:
                item = self.db.get_ap_item(resolved_ap_item_id) or {}
            except Exception:
                item = {}
        if not item:
            return {}

        metadata = self._parse_item_metadata(item)
        reason = str(metadata.get("workflow_paused_reason") or metadata.get("exception_code") or "").strip() or None
        return self.upsert_belief_state(
            skill_id=skill_id,
            ap_item_id=resolved_ap_item_id,
            thread_id=str(item.get("thread_id") or "").strip() or None,
            current_state=str(item.get("state") or "").strip() or None,
            status=str(metadata.get("processing_status") or item.get("state") or "").strip() or None,
            belief_state={
                "document_type": str(metadata.get("document_type") or item.get("document_type") or "invoice").strip() or "invoice",
                "vendor_name": str(item.get("vendor_name") or item.get("vendor") or "").strip() or None,
                "invoice_number": str(item.get("invoice_number") or "").strip() or None,
                "amount": item.get("amount"),
                "currency": str(item.get("currency") or "USD").strip() or "USD",
                "status": str(metadata.get("processing_status") or item.get("state") or "").strip() or None,
                "current_state": str(item.get("state") or "").strip() or None,
                "reason": reason,
                "requires_field_review": bool(item.get("requires_field_review") or metadata.get("requires_field_review")),
                "autonomy_mode": str(metadata.get("autonomy_mode") or "").strip() or None,
                "corrected_field": None,
            },
            evidence={
                "thread_id": str(item.get("thread_id") or "").strip() or None,
                "message_id": str(item.get("message_id") or "").strip() or None,
                "audit_event_id": None,
                "correlation_id": str(metadata.get("correlation_id") or "").strip() or None,
                "field_confidences": metadata.get("field_confidences") if isinstance(metadata.get("field_confidences"), dict) else {},
                "source_channel": str(metadata.get("intake_source") or "ap_item_service").strip() or "ap_item_service",
            },
            uncertainties={
                "confidence_blockers": metadata.get("confidence_blockers") if isinstance(metadata.get("confidence_blockers"), list) else [],
                "source_conflicts": metadata.get("source_conflicts") if isinstance(metadata.get("source_conflicts"), list) else [],
                "reason_codes": list(metadata.get("reason_codes") or []),
            },
            next_action={
                "type": "manual_review",
                "label": "Review current AP state",
                "owner": "operator",
            },
            memory_summary={
                "event_type": "belief_built_from_item",
                "reason": reason,
                "actor_id": None,
                "source": "agent_memory",
                "profile": {
                    "name": self.default_profile(skill_id=skill_id).get("name"),
                    "mission": self.default_profile(skill_id=skill_id).get("mission"),
                    "autonomy_level": self.default_profile(skill_id=skill_id).get("autonomy_level"),
                },
            },
        ) or {}

    def summarize_episode(self, *, ap_item_id: str, skill_id: str = "ap_v1") -> Dict[str, Any]:
        summary = self.get_episode_summary(ap_item_id=ap_item_id, skill_id=skill_id)
        if summary:
            return summary
        belief = self.build_belief_state(ap_item_id=ap_item_id, skill_id=skill_id)
        if not belief:
            return {}
        return self.upsert_episode_summary(
            skill_id=skill_id,
            ap_item_id=str(ap_item_id or "").strip(),
            status=str(belief.get("status") or belief.get("current_state") or "unknown").strip() or "unknown",
            summary=str((belief.get("summary") or {}).get("reason") or "Belief state available").strip() or "Belief state available",
            outcome={
                "event_type": "episode_summarized_from_belief",
                "status": belief.get("status"),
                "next_action": belief.get("next_action"),
            },
        ) or {}

    def compact_memory(
        self,
        *,
        ap_item_id: str,
        skill_id: str = "ap_v1",
        keep_recent: int = 8,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"compacted": 0, "kept": 0}
        resolved_ap_item_id = str(ap_item_id or "").strip()
        if not resolved_ap_item_id:
            return {"compacted": 0, "kept": 0}
        sql = (
            """
            SELECT id, event_type, created_at
            FROM agent_memory_events
            WHERE organization_id = %s AND skill_id = %s AND ap_item_id = %s
            ORDER BY created_at ASC
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    self.organization_id,
                    str(skill_id or "ap_v1").strip() or "ap_v1",
                    resolved_ap_item_id,
                ),
            )
            rows = cur.fetchall() or []
        if len(rows) <= max(1, int(keep_recent or 8)):
            return {"compacted": 0, "kept": len(rows)}

        compact_rows = rows[: len(rows) - max(1, int(keep_recent or 8))]
        keep_rows = rows[len(compact_rows) :]
        counts: Dict[str, int] = {}
        for row in compact_rows:
            payload = self._row_to_dict(row)
            event_type = str(payload.get("event_type") or "unknown").strip() or "unknown"
            counts[event_type] = counts.get(event_type, 0) + 1

        summary = self.summarize_episode(ap_item_id=resolved_ap_item_id, skill_id=skill_id)
        outcome = summary.get("outcome") if isinstance(summary.get("outcome"), dict) else {}
        compacted_history = list(outcome.get("compacted_history") or [])
        compacted_history.append(
            {
                "compacted_at": _now_iso(),
                "event_count": len(compact_rows),
                "event_type_counts": counts,
                "oldest_created_at": self._row_to_dict(compact_rows[0]).get("created_at"),
                "newest_created_at": self._row_to_dict(compact_rows[-1]).get("created_at"),
            }
        )
        self.upsert_episode_summary(
            skill_id=skill_id,
            ap_item_id=resolved_ap_item_id,
            status=str(summary.get("status") or "compacted").strip() or "compacted",
            summary=str(summary.get("summary") or "Episode compacted").strip() or "Episode compacted",
            outcome={
                **outcome,
                "compacted_history": compacted_history[-10:],
            },
        )
        delete_sql = (
            """
            DELETE FROM agent_memory_events
            WHERE id = %s AND organization_id = %s AND skill_id = %s AND ap_item_id = %s
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            for row in compact_rows:
                payload = self._row_to_dict(row)
                cur.execute(
                    delete_sql,
                    (
                        payload.get("id"),
                        self.organization_id,
                        str(skill_id or "ap_v1").strip() or "ap_v1",
                        resolved_ap_item_id,
                    ),
                )
            conn.commit()
        return {
            "compacted": len(compact_rows),
            "kept": len(keep_rows),
            "event_type_counts": counts,
        }

    def recall_similar_cases(
        self,
        context: Optional[Dict[str, Any]] = None,
        *,
        skill_id: str = "ap_v1",
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        normalized_context = dict(context or {})
        target_vendor = str(normalized_context.get("vendor_name") or "").strip().lower()
        target_document_type = str(normalized_context.get("document_type") or "").strip().lower()
        target_state = str(normalized_context.get("current_state") or normalized_context.get("status") or "").strip().lower()
        target_next_action = str(normalized_context.get("next_action_type") or normalized_context.get("next_action") or "").strip().lower()

        sql = (
            """
            SELECT ap_item_id, current_state, status, belief_json, next_action_json, memory_summary_json, updated_at
            FROM agent_belief_states
            WHERE organization_id = %s AND skill_id = %s
            ORDER BY updated_at DESC
            LIMIT %s
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    self.organization_id,
                    str(skill_id or "ap_v1").strip() or "ap_v1",
                    max(5, int(limit or 5) * 8),
                ),
            )
            rows = cur.fetchall() or []

        matches: List[Dict[str, Any]] = []
        for row in rows:
            payload = self._row_to_dict(row)
            belief = self._load_json(payload.get("belief_json"), {})
            next_action = self._load_json(payload.get("next_action_json"), {})
            summary = self._load_json(payload.get("memory_summary_json"), {})
            score = 0.0
            match_reasons: List[str] = []
            belief_vendor = str(belief.get("vendor_name") or "").strip().lower()
            belief_document_type = str(belief.get("document_type") or "").strip().lower()
            belief_state = str(payload.get("current_state") or payload.get("status") or "").strip().lower()
            belief_next_action = str(next_action.get("type") or next_action.get("label") or "").strip().lower()
            if target_vendor and belief_vendor == target_vendor:
                score += 4.0
                match_reasons.append("vendor_exact")
            if target_document_type and belief_document_type == target_document_type:
                score += 2.0
                match_reasons.append("document_type")
            if target_state and belief_state == target_state:
                score += 2.0
                match_reasons.append("state")
            if target_next_action and belief_next_action == target_next_action:
                score += 1.5
                match_reasons.append("next_action")
            confidence_blockers = belief.get("requires_field_review")
            if bool(confidence_blockers) == bool(normalized_context.get("requires_field_review")) and normalized_context.get("requires_field_review") is not None:
                score += 0.5
                match_reasons.append("field_review_alignment")
            updated_at = str(payload.get("updated_at") or "").strip()
            if updated_at:
                try:
                    updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    age_hours = max(
                        0.0,
                        (datetime.now(timezone.utc) - updated_dt).total_seconds() / 3600.0,
                    )
                    recency_bonus = max(0.0, 1.0 - min(age_hours / 168.0, 1.0))
                    score += recency_bonus
                    if recency_bonus:
                        match_reasons.append("recency")
                except Exception:
                    pass
            if score <= 0:
                continue
            matches.append(
                {
                    "ap_item_id": payload.get("ap_item_id"),
                    "score": score,
                    "match_reasons": match_reasons,
                    "current_state": payload.get("current_state"),
                    "status": payload.get("status"),
                    "belief": belief,
                    "next_action": next_action,
                    "summary": summary,
                    "updated_at": payload.get("updated_at"),
                }
            )

        if target_vendor:
            pattern_prefix = f"{target_vendor}|"
            for pattern in self.list_patterns(
                skill_id=skill_id,
                pattern_type="vendor_document_next_action",
                pattern_key_prefix=pattern_prefix,
                limit=max(1, int(limit or 5)),
            ):
                matches.append(
                    {
                        "ap_item_id": None,
                        "score": 3,
                        "current_state": (pattern.get("pattern") or {}).get("current_state"),
                        "status": (pattern.get("pattern") or {}).get("status"),
                        "belief": {},
                        "next_action": (pattern.get("pattern") or {}).get("next_action") or {},
                        "summary": {
                            "reason": (pattern.get("pattern") or {}).get("reason"),
                            "pattern_type": pattern.get("pattern_type"),
                        },
                        "updated_at": pattern.get("updated_at"),
                    }
                )

        matches.sort(
            key=lambda entry: (
                -float(entry.get("score") or 0.0),
                str(entry.get("updated_at") or ""),
            )
        )
        return matches[: max(1, int(limit or 5))]

    def record_outcome(
        self,
        *,
        skill_id: str = "ap_v1",
        ap_item: Optional[Dict[str, Any]] = None,
        ap_item_id: Optional[str] = None,
        event_type: str,
        reason: Optional[str] = None,
        response: Optional[Dict[str, Any]] = None,
        actor_id: Optional[str] = None,
        source: str = "finance_agent_loop",
        correlation_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        item = dict(ap_item or {})
        resolved_ap_item_id = str(ap_item_id or item.get("id") or "").strip()
        if not resolved_ap_item_id:
            return None
        observed = self.observe_event(
            skill_id=skill_id,
            ap_item_id=resolved_ap_item_id,
            thread_id=str(item.get("thread_id") or "").strip() or None,
            event_type=event_type,
            payload=dict(response or {}),
            actor_id=actor_id,
            correlation_id=correlation_id,
            source=source,
            summary=str(reason or event_type or "").strip() or None,
            channel=source,
        )
        snapshot = self.capture_runtime_state(
            skill_id=skill_id,
            ap_item=item,
            ap_item_id=resolved_ap_item_id,
            event_type=event_type,
            reason=reason,
            response=response,
            actor_id=actor_id,
            source=source,
            correlation_id=correlation_id,
        )
        compaction = self.compact_memory(
            ap_item_id=resolved_ap_item_id,
            skill_id=skill_id,
            keep_recent=12,
        )
        return {
            "event": observed,
            "snapshot": snapshot,
            "compaction": compaction,
        }

    def build_surface(self, *, ap_item_id: str, skill_id: str = "ap_v1") -> Dict[str, Any]:
        resolved_ap_item_id = str(ap_item_id or "").strip()
        if not resolved_ap_item_id:
            return {
                "profile": self.default_profile(skill_id=skill_id),
                "belief": {},
                "current_state": None,
                "status": None,
                "evidence": {},
                "uncertainties": {},
                "next_action": {},
                "summary": {},
                "episode": {},
                "working_memory": {},
                "episodic_memory": {
                    "episode": {},
                    "recent_events": [],
                    "retry_jobs": [],
                    "task_runs": [],
                },
                "semantic_memory": {},
                "identity_memory": self.default_profile(skill_id=skill_id),
            }

        profile = self.get_profile(skill_id=skill_id) or self.default_profile(skill_id=skill_id)
        belief = self.get_belief_state(ap_item_id=resolved_ap_item_id, skill_id=skill_id)
        episode = self.get_episode_summary(ap_item_id=resolved_ap_item_id, skill_id=skill_id)
        working_memory = {
            "belief": belief.get("belief") if isinstance(belief.get("belief"), dict) else {},
            "current_state": belief.get("current_state"),
            "status": belief.get("status"),
            "evidence": belief.get("evidence") if isinstance(belief.get("evidence"), dict) else {},
            "uncertainties": belief.get("uncertainties") if isinstance(belief.get("uncertainties"), dict) else {},
            "next_action": belief.get("next_action") if isinstance(belief.get("next_action"), dict) else {},
            "summary": belief.get("summary") if isinstance(belief.get("summary"), dict) else {},
        }

        recent_events = self.list_memory_events(ap_item_id=resolved_ap_item_id)
        recent_events = recent_events[-8:] if len(recent_events) > 8 else recent_events

        retry_jobs: List[Dict[str, Any]] = []
        if hasattr(self.db, "list_agent_retry_jobs"):
            try:
                jobs = self.db.list_agent_retry_jobs(self.organization_id, ap_item_id=resolved_ap_item_id, limit=20) or []
                retry_jobs = [
                    {
                        "id": row.get("id"),
                        "job_type": row.get("job_type"),
                        "status": row.get("status"),
                        "retry_count": row.get("retry_count"),
                        "last_error": row.get("last_error"),
                        "next_retry_at": row.get("next_retry_at"),
                        "created_at": row.get("created_at"),
                    }
                    for row in jobs
                ][:5]
            except Exception:
                retry_jobs = []

        task_runs: List[Dict[str, Any]] = []
        if hasattr(self.db, "list_task_runs"):
            try:
                runs = self.db.list_task_runs(self.organization_id, limit=50) or []
                task_runs = [
                    {
                        "id": row.get("id"),
                        "task_type": row.get("task_type"),
                        "status": row.get("status"),
                        "current_step": row.get("current_step"),
                        "correlation_id": row.get("correlation_id"),
                        "last_error": row.get("last_error"),
                        "created_at": row.get("created_at"),
                        "updated_at": row.get("updated_at"),
                        "completed_at": row.get("completed_at"),
                    }
                    for row in runs
                    if self._task_run_ap_item_id(row) == resolved_ap_item_id
                ][:5]
            except Exception:
                task_runs = []

        episodic_memory = {
            "episode": episode if isinstance(episode, dict) else {},
            "recent_events": recent_events,
            "retry_jobs": retry_jobs,
            "task_runs": task_runs,
        }

        semantic_memory: Dict[str, Any] = {
            "patterns": [],
            "vendor_profile": {},
            "vendor_feedback_summary": {},
            "recent_vendor_history": [],
        }
        vendor_name = str((working_memory.get("belief") or {}).get("vendor_name") or "").strip()
        if vendor_name:
            try:
                semantic_memory["patterns"] = self.list_patterns(
                    skill_id=skill_id,
                    pattern_type="vendor_document_next_action",
                    pattern_key_prefix=f"{vendor_name.lower()}|",
                    limit=5,
                )
            except Exception:
                semantic_memory["patterns"] = []
            if hasattr(self.db, "get_vendor_profile"):
                try:
                    semantic_memory["vendor_profile"] = (
                        self.db.get_vendor_profile(self.organization_id, vendor_name) or {}
                    )
                except Exception:
                    semantic_memory["vendor_profile"] = {}
            if hasattr(self.db, "get_vendor_decision_feedback_summary"):
                try:
                    semantic_memory["vendor_feedback_summary"] = (
                        self.db.get_vendor_decision_feedback_summary(self.organization_id, vendor_name) or {}
                    )
                except Exception:
                    semantic_memory["vendor_feedback_summary"] = {}
            if hasattr(self.db, "get_vendor_invoice_history"):
                try:
                    semantic_memory["recent_vendor_history"] = (
                        self.db.get_vendor_invoice_history(self.organization_id, vendor_name, limit=5) or []
                    )
                except Exception:
                    semantic_memory["recent_vendor_history"] = []

        return {
            "profile": {
                "name": profile.get("name"),
                "mission": profile.get("mission"),
                "doctrine_version": profile.get("doctrine_version"),
                "risk_posture": profile.get("risk_posture"),
                "autonomy_level": profile.get("autonomy_level"),
                "explanation_style": profile.get("explanation_style"),
            },
            "belief": belief.get("belief") if isinstance(belief.get("belief"), dict) else {},
            "current_state": belief.get("current_state"),
            "status": belief.get("status"),
            "evidence": belief.get("evidence") if isinstance(belief.get("evidence"), dict) else {},
            "uncertainties": belief.get("uncertainties") if isinstance(belief.get("uncertainties"), dict) else {},
            "next_action": belief.get("next_action") if isinstance(belief.get("next_action"), dict) else {},
            "summary": belief.get("summary") if isinstance(belief.get("summary"), dict) else {},
            "episode": episode if isinstance(episode, dict) else {},
            "working_memory": working_memory,
            "episodic_memory": episodic_memory,
            "semantic_memory": semantic_memory,
            "identity_memory": dict(profile),
            "latest_eval": self.latest_eval_snapshot(
                skill_id=skill_id,
                scope="ap_item",
                scope_id=resolved_ap_item_id,
            ),
        }

    def list_memory_events(self, *, ap_item_id: str) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        sql = (
            """
            SELECT event_type, summary, payload_json, created_at
            FROM agent_memory_events
            WHERE organization_id = %s AND ap_item_id = %s
            ORDER BY created_at ASC
            """
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (self.organization_id, str(ap_item_id or "").strip()))
            rows = cur.fetchall() or []
        results: List[Dict[str, Any]] = []
        for row in rows:
            payload = self._row_to_dict(row)
            results.append(
                {
                    "event_type": payload.get("event_type"),
                    "summary": payload.get("summary"),
                    "payload": self._load_json(payload.get("payload_json"), {}),
                    "created_at": payload.get("created_at"),
                }
            )
        return results


def get_agent_memory_service(
    organization_id: str = "default",
    *,
    db: Optional[ClearledgrDB] = None,
) -> AgentMemoryService:
    if db is None:
        db = get_db()
    key = (str(organization_id or "default").strip() or "default", id(db))
    service = _agent_memory_services.get(key)
    if service is None:
        service = AgentMemoryService(key[0], db=db)
        _agent_memory_services[key] = service
    return service
