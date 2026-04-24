"""Learning calibration snapshots for tenant-level AP decision quality."""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.database import ClearledgrDB, get_db
from clearledgr.core.utils import safe_float
from clearledgr.services.correction_learning import CorrectionLearningService

logger = logging.getLogger(__name__)

# Module-level calibration history for rollback visibility
_calibration_history: List[Dict[str, Any]] = []

# Hard bounds for auto-applied thresholds
_THRESHOLD_FLOOR = 0.70
_THRESHOLD_CEILING = 0.99


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LearningCalibrationService:
    """Compute and persist calibration snapshots from real operator outcomes."""

    def __init__(self, organization_id: str = "default", *, db: Optional[ClearledgrDB] = None) -> None:
        self.organization_id = str(organization_id or "default").strip() or "default"
        self.db = db or get_db()
        self._init_table()

    def _init_table(self) -> None:
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_calibration_snapshots (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    calibration_version TEXT NOT NULL,
                    window_days INTEGER NOT NULL,
                    min_feedback INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_learning_calibration_snapshots_org_created
                ON learning_calibration_snapshots(organization_id, created_at)
                """
            )
            conn.commit()

    def _load_feedback_rows(self, *, window_days: int, limit: int) -> List[Dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days)))).isoformat()
        sql = self.db._prepare_sql(
            "SELECT vendor_name, human_decision, agent_recommendation, decision_override, reason, created_at "
            "FROM vendor_decision_feedback "
            "WHERE organization_id = ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT ?"
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (self.organization_id, cutoff, int(max(1, limit))))
            rows = [dict(row) for row in cur.fetchall()]
        return rows

    @staticmethod
    def _empty_snapshot(*, organization_id: str, window_days: int, min_feedback: int) -> Dict[str, Any]:
        return {
            "organization_id": organization_id,
            "generated_at": _now_iso(),
            "window_days": int(window_days),
            "min_feedback": int(min_feedback),
            "status": "insufficient_signal",
            "summary": {
                "total_feedback": 0,
                "vendor_count": 0,
                "disagreement_count": 0,
                "disagreement_rate": 0.0,
                "override_count": 0,
                "override_rate": 0.0,
                "approve_count": 0,
                "reject_count": 0,
                "request_info_count": 0,
            },
            "top_vendor_calibration_gaps": [],
            "recommendations": [
                "Collect more operator outcomes before applying calibration changes.",
            ],
            "correction_learning": {"status": "not_available"},
            "applied_changes": [],
        }

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return str(value or "").strip().lower()

    def recompute_snapshot(
        self,
        *,
        window_days: int = 180,
        min_feedback: int = 20,
        limit: int = 5000,
        auto_apply: bool = False,
    ) -> Dict[str, Any]:
        rows = self._load_feedback_rows(window_days=window_days, limit=limit)
        if not rows:
            snapshot = self._empty_snapshot(
                organization_id=self.organization_id,
                window_days=window_days,
                min_feedback=min_feedback,
            )
            return self._persist_snapshot(snapshot, window_days=window_days, min_feedback=min_feedback)

        vendor_buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        approve_count = 0
        reject_count = 0
        request_info_count = 0
        override_count = 0
        disagreement_count = 0
        reason_counts: Dict[str, int] = defaultdict(int)

        for row in rows:
            vendor = str(row.get("vendor_name") or "unknown").strip() or "unknown"
            vendor_buckets[vendor].append(row)
            human = self._normalize_text(row.get("human_decision"))
            agent = self._normalize_text(row.get("agent_recommendation"))
            if human == "approve":
                approve_count += 1
            elif human == "reject":
                reject_count += 1
            elif human == "request_info":
                request_info_count += 1
            if bool(row.get("decision_override")):
                override_count += 1
            if agent and human and agent != human:
                disagreement_count += 1
            reason = str(row.get("reason") or "").strip()
            if reason:
                reason_counts[reason] += 1

        total_feedback = len(rows)
        override_rate = round(override_count / max(1, total_feedback), 4)
        disagreement_rate = round(disagreement_count / max(1, total_feedback), 4)

        vendor_gaps: List[Dict[str, Any]] = []
        for vendor_name, bucket in vendor_buckets.items():
            vendor_total = len(bucket)
            vendor_override = sum(1 for row in bucket if bool(row.get("decision_override")))
            vendor_disagreements = 0
            recent_reasons: List[str] = []
            for row in bucket:
                human = self._normalize_text(row.get("human_decision"))
                agent = self._normalize_text(row.get("agent_recommendation"))
                if human and agent and human != agent:
                    vendor_disagreements += 1
                reason = str(row.get("reason") or "").strip()
                if reason and reason not in recent_reasons and len(recent_reasons) < 3:
                    recent_reasons.append(reason)

            vendor_gaps.append(
                {
                    "vendor_name": vendor_name,
                    "total_feedback": vendor_total,
                    "override_rate": round(vendor_override / max(1, vendor_total), 4),
                    "disagreement_rate": round(vendor_disagreements / max(1, vendor_total), 4),
                    "latest_feedback_at": bucket[0].get("created_at"),
                    "recent_reasons": recent_reasons,
                }
            )

        vendor_gaps.sort(
            key=lambda row: (
                -safe_float(row.get("disagreement_rate")),
                -int(row.get("total_feedback") or 0),
                str(row.get("vendor_name") or ""),
            )
        )
        top_vendor_gaps = vendor_gaps[:10]

        correction_learning: Dict[str, Any]
        try:
            correction_learning = CorrectionLearningService(
                organization_id=self.organization_id
            ).get_learning_stats()
            correction_learning["status"] = "available"
        except Exception:
            correction_learning = {"status": "not_available"}

        recommendations: List[str] = []
        if total_feedback < int(max(1, min_feedback)):
            status = "insufficient_signal"
            recommendations.append(
                "Collect more approve/reject/request-info outcomes before applying automatic calibration updates."
            )
        elif disagreement_rate >= 0.3:
            status = "recalibration_needed"
            recommendations.append(
                "High disagreement rate detected; tighten agent confidence thresholds and require stronger evidence on low-confidence fields."
            )
        elif override_rate >= 0.25:
            status = "monitor"
            recommendations.append(
                "Override rate is elevated; review top vendor blockers and tune policy thresholds per vendor."
            )
        else:
            status = "stable"
            recommendations.append(
                "Calibration is stable; continue monitoring vendor-level disagreement trends."
            )

        if top_vendor_gaps:
            recommendations.append(
                f"Prioritize vendor calibration review for: {', '.join(row['vendor_name'] for row in top_vendor_gaps[:3])}."
            )
        if reason_counts:
            top_reason = sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
            recommendations.append(
                f"Most frequent operator reason signal: '{top_reason}'. Validate decision policies against this pattern."
            )

        snapshot = {
            "organization_id": self.organization_id,
            "generated_at": _now_iso(),
            "window_days": int(window_days),
            "min_feedback": int(min_feedback),
            "status": status,
            "summary": {
                "total_feedback": total_feedback,
                "vendor_count": len(vendor_buckets),
                "disagreement_count": disagreement_count,
                "disagreement_rate": disagreement_rate,
                "override_count": override_count,
                "override_rate": override_rate,
                "approve_count": approve_count,
                "reject_count": reject_count,
                "request_info_count": request_info_count,
            },
            "top_vendor_calibration_gaps": top_vendor_gaps,
            "recommendations": recommendations,
            "correction_learning": correction_learning,
        }

        # Auto-apply threshold adjustments when enabled
        applied_changes: List[Dict[str, Any]] = []
        if auto_apply and status in ("recalibration_needed", "stable"):
            try:
                _db = self.db or get_db()
                _org_row = _db.get_organization(self.organization_id) or {}
                _raw_settings = _org_row.get("settings_json") or _org_row.get("settings") or {}
                if isinstance(_raw_settings, str):
                    _raw_settings = json.loads(_raw_settings)
                if not isinstance(_raw_settings, dict):
                    _raw_settings = {}
                _cfg_dict = _raw_settings.get("org_config") or {}
                if not isinstance(_cfg_dict, dict):
                    _cfg_dict = {}

                current_threshold = safe_float(
                    _cfg_dict.get("auto_approve_confidence_threshold", 0.95), 0.95
                )
                new_threshold = current_threshold

                if status == "recalibration_needed":
                    # Tighten: human disagreement is high, lower the auto-approve bar
                    new_threshold = round(current_threshold - 0.02, 4)
                elif status == "stable" and current_threshold < 0.95 and override_rate < 0.1:
                    # Relax: stable performance, gradually return toward default
                    new_threshold = round(current_threshold + 0.01, 4)

                # Enforce hard bounds: never below floor or above ceiling
                new_threshold = max(_THRESHOLD_FLOOR, min(_THRESHOLD_CEILING, new_threshold))

                if new_threshold != current_threshold:
                    logger.info(
                        "[LearningCalibration] auto_apply org=%s: threshold %.4f -> %.4f (reason=%s)",
                        self.organization_id,
                        current_threshold,
                        new_threshold,
                        status,
                    )
                    # Store previous value for rollback visibility
                    _calibration_history.append({
                        "organization_id": self.organization_id,
                        "field": "auto_approve_confidence_threshold",
                        "old_value": current_threshold,
                        "new_value": new_threshold,
                        "reason": status,
                        "applied_at": _now_iso(),
                    })
                    _cfg_dict["auto_approve_confidence_threshold"] = new_threshold
                    _raw_settings["org_config"] = _cfg_dict
                    _db.update_organization(self.organization_id, settings=_raw_settings)
                    applied_changes.append({
                        "field": "auto_approve_confidence_threshold",
                        "old_value": current_threshold,
                        "new_value": new_threshold,
                        "reason": status,
                        "applied_at": _now_iso(),
                    })
            except Exception as _exc:
                logger.warning(
                    "[LearningCalibration] auto_apply failed (non-fatal): %s", _exc
                )

        snapshot["applied_changes"] = applied_changes
        return self._persist_snapshot(snapshot, window_days=window_days, min_feedback=min_feedback)

    def _persist_snapshot(
        self,
        snapshot: Dict[str, Any],
        *,
        window_days: int,
        min_feedback: int,
    ) -> Dict[str, Any]:
        created_at = _now_iso()
        calibration_version = f"calib-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        record = {
            "id": str(uuid.uuid4()),
            "organization_id": self.organization_id,
            "calibration_version": calibration_version,
            "window_days": int(window_days),
            "min_feedback": int(min_feedback),
            "snapshot_json": json.dumps(snapshot),
            "created_at": created_at,
        }
        sql = self.db._prepare_sql(
            "INSERT INTO learning_calibration_snapshots "
            "(id, organization_id, calibration_version, window_days, min_feedback, snapshot_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    record["id"],
                    record["organization_id"],
                    record["calibration_version"],
                    record["window_days"],
                    record["min_feedback"],
                    record["snapshot_json"],
                    record["created_at"],
                ),
            )
            conn.commit()
        return {
            **snapshot,
            "calibration_version": calibration_version,
            "snapshot_id": record["id"],
            "stored_at": created_at,
        }

    def get_latest_snapshot(self) -> Dict[str, Any]:
        sql = self.db._prepare_sql(
            "SELECT id, calibration_version, snapshot_json, created_at, window_days, min_feedback "
            "FROM learning_calibration_snapshots "
            "WHERE organization_id = ? "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (self.organization_id,))
            row = cur.fetchone()
            payload = dict(row) if row else None

        if not payload:
            return self._empty_snapshot(
                organization_id=self.organization_id,
                window_days=180,
                min_feedback=20,
            )

        raw_snapshot = payload.get("snapshot_json")
        try:
            snapshot = json.loads(raw_snapshot) if isinstance(raw_snapshot, str) else {}
        except Exception:
            snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot["snapshot_id"] = payload.get("id")
        snapshot["calibration_version"] = payload.get("calibration_version")
        snapshot["stored_at"] = payload.get("created_at")
        snapshot.setdefault("window_days", int(payload.get("window_days") or 180))
        snapshot.setdefault("min_feedback", int(payload.get("min_feedback") or 20))
        snapshot.setdefault("organization_id", self.organization_id)
        return snapshot


def get_learning_calibration_service(
    organization_id: str = "default",
    *,
    db: Optional[ClearledgrDB] = None,
) -> LearningCalibrationService:
    return LearningCalibrationService(organization_id=organization_id, db=db)

