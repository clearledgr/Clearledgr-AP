"""LearningStore — org-scoped Postgres persistence for the compounding-learning service.

Replaces the legacy per-process SQLite file (``state/learning.db``) the
CompoundingLearningService used to write. Every method is org-scoped: rows carry
``organization_id NOT NULL`` and it is part of the primary key, so two orgs that
generate the same ``pattern_id`` (e.g. ``cat_acme_6010``) keep separate rows and
one org's learned patterns never surface in another org's reasoning.

Tables created by migration v95: ``learning_patterns`` + ``learning_corrections``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _coerce_json(value: Any, default: Any) -> Any:
    """psycopg returns JSONB as parsed objects, but tolerate text too."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default
    return value


class LearningStore:
    """Mixin: org-scoped CRUD for compounding-learning patterns + corrections.

    Combined into SoldenDB via the four-site wiring in ``database.py``.
    """

    def list_learning_patterns(
        self,
        organization_id: str,
        *,
        min_confidence: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """All learned patterns for one org above a confidence floor."""
        self.initialize()
        sql = (
            "SELECT pattern_id, pattern_type, pattern_data, confidence, "
            "usage_count, success_count, last_used, created_from "
            "FROM learning_patterns "
            "WHERE organization_id = %s AND confidence > %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, float(min_confidence)))
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["pattern_data"] = _coerce_json(r.get("pattern_data"), {})
            r["created_from"] = _coerce_json(r.get("created_from"), [])
        return rows

    def save_learning_pattern(
        self,
        organization_id: str,
        pattern: Dict[str, Any],
    ) -> None:
        """Upsert one learned pattern, scoped to (organization_id, pattern_id)."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            "INSERT INTO learning_patterns "
            "(organization_id, pattern_id, pattern_type, pattern_data, confidence, "
            " usage_count, success_count, last_used, created_from, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s) "
            "ON CONFLICT (organization_id, pattern_id) DO UPDATE SET "
            "  pattern_type = EXCLUDED.pattern_type, "
            "  pattern_data = EXCLUDED.pattern_data, "
            "  confidence = EXCLUDED.confidence, "
            "  usage_count = EXCLUDED.usage_count, "
            "  success_count = EXCLUDED.success_count, "
            "  last_used = EXCLUDED.last_used, "
            "  created_from = EXCLUDED.created_from, "
            "  updated_at = EXCLUDED.updated_at"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                organization_id,
                pattern["pattern_id"],
                pattern["pattern_type"],
                json.dumps(pattern.get("pattern_data") or {}),
                float(pattern.get("confidence") or 0.0),
                int(pattern.get("usage_count") or 0),
                int(pattern.get("success_count") or 0),
                pattern.get("last_used"),
                json.dumps(pattern.get("created_from") or []),
                now,
                now,
            ))
            conn.commit()

    def save_learning_correction(
        self,
        organization_id: str,
        correction: Dict[str, Any],
    ) -> None:
        """Insert one correction row, scoped to (organization_id, correction_id)."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            "INSERT INTO learning_corrections "
            "(organization_id, correction_id, correction_type, original_value, "
            " corrected_value, user_email, context, created_at) "
            "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s) "
            "ON CONFLICT (organization_id, correction_id) DO NOTHING"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                organization_id,
                correction["correction_id"],
                correction["correction_type"],
                json.dumps(correction.get("original_value") or {}),
                json.dumps(correction.get("corrected_value") or {}),
                correction.get("user_email") or "system",
                json.dumps(correction.get("context") or {}),
                correction.get("created_at") or now,
            ))
            conn.commit()

    def learning_metrics(self, organization_id: str) -> Dict[str, Any]:
        """Aggregate learning metrics for one org (recomputed, not stored)."""
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS n FROM learning_corrections WHERE organization_id = %s",
                (organization_id,),
            )
            total_corrections = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                "SELECT COUNT(*) AS n FROM learning_patterns "
                "WHERE organization_id = %s AND confidence > 0.5",
                (organization_id,),
            )
            patterns_learned = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                "SELECT SUM(success_count) * 1.0 / NULLIF(SUM(usage_count), 0) AS acc "
                "FROM learning_patterns WHERE organization_id = %s AND usage_count > 0",
                (organization_id,),
            )
            row = cur.fetchone() or {}
            accuracy = float(row.get("acc") or 0.0)
        return {
            "total_corrections": total_corrections,
            "patterns_learned": patterns_learned,
            "accuracy": accuracy,
        }
